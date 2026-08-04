"""Máquina de estados del documento electrónico (F4-03).

La tabla de transiciones es la especificación: si una transición no está
aquí, no existe. Mantenerla pura permite probarla exhaustivamente sin base
de datos.
"""

from __future__ import annotations

from ordo_core.errors import KernelError


class EdiError(KernelError):
    """Error de facturación electrónica con código estable."""


# estado_actual -> {acción: estado_siguiente}
TRANSITIONS: dict[str, dict[str, str]] = {
    "draft": {
        "generate": "generated",
        "cancel": "cancelled",
    },
    "generated": {
        "sign": "signed",
        "cancel": "cancelled",
    },
    "signed": {
        "send": "sent",
        "contingency": "contingency",
    },
    "sent": {
        "accept": "accepted",
        "reject": "rejected",
        "contingency": "contingency",
    },
    "contingency": {
        "send": "sent",
    },
    "rejected": {
        # Corregir el origen y regenerar: el folio quemado no se recicla,
        # el documento nuevo toma un folio nuevo.
        "generate": "generated",
        "cancel": "cancelled",
    },
    "accepted": {
        # Solo si el país soporta anulación directa (SIFEN). En Chile la
        # corrección de un DTE aceptado es una nota de crédito.
        "cancel": "cancelled",
    },
    "cancelled": {},
}

VALID_STATES = frozenset(TRANSITIONS)


def next_state(current: str, action: str) -> str:
    """Devuelve el estado destino o falla con código estable."""
    if current not in VALID_STATES:
        raise EdiError("EDI_UNKNOWN_STATE", f"Estado desconocido: {current!r}")
    target = TRANSITIONS[current].get(action)
    if target is None:
        allowed = ", ".join(sorted(TRANSITIONS[current])) or "ninguna"
        raise EdiError(
            "EDI_INVALID_TRANSITION",
            f"La acción '{action}' no es válida desde el estado '{current}'",
            hint=f"Acciones posibles desde '{current}': {allowed}.",
        )
    return target
