"""Tests del framework de localización y los validadores de identificador.

Los algoritmos de dígito verificador se prueban con casos conocidos y con
property-based: para todo número, el dígito calculado debe validar.
"""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from ordo_core.localization import (
    LocalizationError,
    discover_packs,
    load_pack,
)
from ordo_core.taxid import (
    TaxIdError,
    format_rut,
    ruc_check_digit,
    rut_check_digit,
    validate,
    validate_ruc,
    validate_rut,
)

PACKS_ROOT = Path(__file__).resolve().parents[3] / "localizations"


class TestChileanRut:
    @pytest.mark.parametrize(
        ("number", "expected"),
        # Valores calculados a mano con el algoritmo publicado (módulo 11,
        # factores cíclicos 2..7 de derecha a izquierda). No son RUT de
        # contribuyentes reales; el pack advierte que falta validación con
        # casos verificados externamente.
        [(76123456, "0"), (12345678, "5"), (11111111, "1"), (6, "K"), (1, "9")],
    )
    def test_check_digit_matches_known_cases(self, number: int, expected: str) -> None:
        assert rut_check_digit(number) == expected

    def test_accepts_formatted_and_plain(self) -> None:
        assert validate_rut("76.123.456-0") == "76123456-0"
        assert validate_rut("761234560") == "76123456-0"
        assert validate_rut("76123456-0") == "76123456-0"

    def test_wrong_check_digit_is_rejected(self) -> None:
        with pytest.raises(TaxIdError) as exc:
            validate_rut("76.123.456-8")
        assert exc.value.code == "TAXID_INVALID_CHECK_DIGIT"
        assert "0" in exc.value.message  # dice cuál era el correcto

    def test_k_check_digit(self) -> None:
        """El dígito K es un caso especial del módulo 11 que suele romperse."""
        number = next(n for n in range(1000, 2000) if rut_check_digit(n) == "K")
        assert validate_rut(f"{number}-K") == f"{number}-K"

    def test_malformed_is_rejected(self) -> None:
        for bad in ("", "abc", "76.123.456", "76123456-Z", "1234567890-1"):
            with pytest.raises(TaxIdError):
                validate_rut(bad)

    def test_formatting_adds_separators(self) -> None:
        assert format_rut("761234560") == "76.123.456-0"

    @settings(max_examples=300, deadline=None)
    @given(number=st.integers(min_value=1, max_value=99_999_999))
    def test_generated_digit_always_validates(self, number: int) -> None:
        """Para cualquier número, su dígito calculado hace válido el RUT."""
        digit = rut_check_digit(number)
        assert validate_rut(f"{number}-{digit}") == f"{number}-{digit}"

    @settings(max_examples=200, deadline=None)
    @given(number=st.integers(min_value=1, max_value=99_999_999))
    def test_wrong_digit_never_validates(self, number: int) -> None:
        correct = rut_check_digit(number)
        for candidate in "0123456789K":
            if candidate == correct:
                continue
            with pytest.raises(TaxIdError):
                validate_rut(f"{number}-{candidate}")


class TestParaguayanRuc:
    def test_accepts_valid_ruc(self) -> None:
        digit = ruc_check_digit(80012345)
        assert validate_ruc(f"80012345-{digit}") == f"80012345-{digit}"

    def test_wrong_check_digit_is_rejected(self) -> None:
        digit = ruc_check_digit(80012345)
        wrong = "0" if digit != "0" else "1"
        with pytest.raises(TaxIdError) as exc:
            validate_ruc(f"80012345-{wrong}")
        assert exc.value.code == "TAXID_INVALID_CHECK_DIGIT"

    def test_malformed_is_rejected(self) -> None:
        # "80012345" sin guion se interpreta como 8001234-5, que es válido o no
        # según su dígito: la ambigüedad se resuelve tomando el último como DV.
        for bad in ("", "abc", "80012345-A", "800123456789-1"):
            with pytest.raises(TaxIdError):
                validate_ruc(bad)

    @settings(max_examples=300, deadline=None)
    @given(number=st.integers(min_value=1, max_value=99_999_999))
    def test_generated_digit_always_validates(self, number: int) -> None:
        digit = ruc_check_digit(number)
        assert validate_ruc(f"{number}-{digit}") == f"{number}-{digit}"


class TestValidatorDispatch:
    def test_dispatches_by_country(self) -> None:
        assert validate("cl", "76.123.456-0") == "76123456-0"
        assert validate("CL", "76.123.456-0") == "76123456-0"

    def test_unknown_country_passes_through(self) -> None:
        """Sin validador declarado no se inventa uno: se acepta tal cual."""
        assert validate("uy", " 123456-7 ") == "123456-7"


class TestPackLoading:
    def test_chilean_pack_loads(self) -> None:
        pack = load_pack(PACKS_ROOT / "cl")
        assert pack.country == "cl"
        assert pack.currency == "CLP"
        assert pack.sources  # obligatorias

    def test_paraguayan_pack_loads(self) -> None:
        pack = load_pack(PACKS_ROOT / "py")
        assert pack.country == "py"
        assert pack.currency == "PYG"

    def test_both_packs_are_discovered(self) -> None:
        packs = discover_packs(PACKS_ROOT)
        assert {"cl", "py"} <= set(packs)

    def test_packs_declare_review_state(self) -> None:
        """Un pack sin revisar debe decirlo: se usa para declarar impuestos."""
        for country in ("cl", "py"):
            pack = load_pack(PACKS_ROOT / country)
            assert pack.review_state in {"draft", "reviewed", "certified"}
            if pack.needs_professional_review:
                assert "PENDIENTE" in pack.notes

    def test_every_tax_cites_its_norm(self) -> None:
        """Una tasa sin norma citada no se puede verificar ni defender."""
        for country in ("cl", "py"):
            pack = load_pack(PACKS_ROOT / country)
            for tax in pack.taxes:
                assert tax.legal_reference, f"{country}: {tax.code} sin referencia legal"

    def test_document_types_cite_their_format(self) -> None:
        for country in ("cl", "py"):
            pack = load_pack(PACKS_ROOT / country)
            for doc in pack.document_types:
                assert doc.legal_reference, f"{country}: {doc.code} sin referencia"

    def test_chilean_taxes_include_general_rate(self) -> None:
        pack = load_pack(PACKS_ROOT / "cl")
        rates = {t.code: t.rate for t in pack.taxes}
        assert rates["IVA19"] == "19"

    def test_paraguayan_taxes_include_both_rates(self) -> None:
        pack = load_pack(PACKS_ROOT / "py")
        rates = {t.code: t.rate for t in pack.taxes}
        assert rates["IVA10"] == "10"
        assert rates["IVA5"] == "5"

    def test_pack_without_sources_is_rejected(self, tmp_path: Path) -> None:
        """Sin fuentes no se carga: un dato fiscal sin norma es indefendible."""
        (tmp_path / "manifest.yaml").write_text(
            "country: xx\nname: Test\nversion: 1.0.0\ncurrency: USD\nsources: []\n"
        )
        with pytest.raises(LocalizationError) as exc:
            load_pack(tmp_path)
        assert exc.value.code == "LOCALIZATION_NO_SOURCES"

    def test_invalid_country_code_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.yaml").write_text(
            'country: CHILE\nname: T\nversion: 1.0.0\ncurrency: CLP\nsources: ["x"]\n'
        )
        with pytest.raises(LocalizationError) as exc:
            load_pack(tmp_path)
        assert exc.value.code == "LOCALIZATION_INVALID"
