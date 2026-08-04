"""La tabla de transiciones es la especificación: se prueba completa."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from modules.einvoicing.statemachine import TRANSITIONS, EdiError, next_state

ACTIONS = sorted({action for moves in TRANSITIONS.values() for action in moves})


class TestDeclaredTransitions:
    def test_happy_path_to_accepted(self) -> None:
        state = "draft"
        for action in ("generate", "sign", "send", "accept"):
            state = next_state(state, action)
        assert state == "accepted"

    def test_rejection_allows_regeneration(self) -> None:
        assert next_state("sent", "reject") == "rejected"
        assert next_state("rejected", "generate") == "generated"

    def test_contingency_resends(self) -> None:
        assert next_state("signed", "contingency") == "contingency"
        assert next_state("contingency", "send") == "sent"

    def test_cancelled_is_terminal(self) -> None:
        assert TRANSITIONS["cancelled"] == {}

    def test_every_declared_transition_lands_on_a_known_state(self) -> None:
        for moves in TRANSITIONS.values():
            for target in moves.values():
                assert target in TRANSITIONS


class TestInvalidTransitions:
    @given(
        current=st.sampled_from(sorted(TRANSITIONS)),
        action=st.sampled_from(ACTIONS),
    )
    def test_undeclared_transitions_always_fail(self, current: str, action: str) -> None:
        """Cualquier par (estado, acción) fuera de la tabla es error estable."""
        if action in TRANSITIONS[current]:
            assert next_state(current, action) in TRANSITIONS
        else:
            with pytest.raises(EdiError) as excinfo:
                next_state(current, action)
            assert excinfo.value.code == "EDI_INVALID_TRANSITION"

    def test_unknown_state_is_its_own_error(self) -> None:
        with pytest.raises(EdiError) as excinfo:
            next_state("limbo", "send")
        assert excinfo.value.code == "EDI_UNKNOWN_STATE"

    def test_sending_an_unsigned_document_is_impossible(self) -> None:
        """De generated no se salta a sent: firmar no es opcional."""
        with pytest.raises(EdiError):
            next_state("generated", "send")

    def test_accepted_documents_never_regenerate(self) -> None:
        with pytest.raises(EdiError):
            next_state("accepted", "generate")
