"""Combinatoria de variantes: invariantes, no ejemplos.

La matriz talla x color es donde se pierden combinaciones, se duplican SKUs o
se genera un catálogo de 40.000 productos por un cero de más. Todo eso se prueba
sin base de datos.
"""

from math import prod

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from modules.product.variants import (
    MAX_VARIANTS,
    VariantError,
    combination_count,
    combinations,
    compose_label,
    compose_sku,
    parse_value_ids,
)

# Ejes pequeños: lo que importa es la forma de la matriz, no su tamaño, y el
# tope se prueba aparte con un conteo, sin materializar nada.
axis = st.lists(st.integers(min_value=1, max_value=999), min_size=1, max_size=6, unique=True)
axes = st.lists(axis, min_size=1, max_size=3)


class TestCombinations:
    @given(axes)
    @settings(max_examples=300, deadline=None)
    def test_size_is_the_product_of_the_axes(self, given_axes: list[list[int]]) -> None:
        assert len(combinations(given_axes)) == prod(len(a) for a in given_axes)

    @given(axes)
    @settings(max_examples=300, deadline=None)
    def test_no_combination_repeats(self, given_axes: list[list[int]]) -> None:
        """Una combinación duplicada es una variante duplicada en el catálogo."""
        result = combinations(given_axes)
        assert len(set(result)) == len(result)

    @given(axes)
    @settings(max_examples=200, deadline=None)
    def test_every_value_of_every_axis_appears(self, given_axes: list[list[int]]) -> None:
        """Perder un valor es perder una talla entera del catálogo, en silencio."""
        result = combinations(given_axes)
        for position, values in enumerate(given_axes):
            assert {combo[position] for combo in result} == set(values)

    @given(axes)
    @settings(max_examples=200, deadline=None)
    def test_axis_order_is_preserved(self, given_axes: list[list[int]]) -> None:
        """El orden define el label y el SKU: si baila, la misma variante se ve
        distinta entre dos generaciones."""
        for combo in combinations(given_axes):
            assert len(combo) == len(given_axes)
            for position, values in enumerate(given_axes):
                assert combo[position] in values

    def test_no_axes_is_no_matrix(self) -> None:
        """Un modelo sin atributos no tiene una variante anónima: no tiene ninguna."""
        assert combinations([]) == []

    def test_an_empty_axis_generates_nothing_without_raising(self) -> None:
        """Matriz declarada a medias: el servicio lo distingue y lo explica; la
        función pura no decide políticas."""
        assert combinations([[1, 2], []]) == []

    def test_the_limit_is_checked_before_materialising(self) -> None:
        big = [list(range(1, 11)) for _ in range(6)]  # 10^6
        assert combination_count(big) == 1_000_000
        with pytest.raises(VariantError) as excinfo:
            combinations(big)
        assert excinfo.value.code == "PRODUCT_VARIANT_LIMIT"

    def test_the_limit_itself_is_allowed(self) -> None:
        exact = [list(range(1, 26)), list(range(100, 120))]  # 25 x 20 = 500
        assert combination_count(exact) == MAX_VARIANTS
        assert len(combinations(exact)) == MAX_VARIANTS


class TestParseValueIds:
    @given(st.lists(st.integers(min_value=1, max_value=9999), min_size=1, max_size=8))
    @settings(max_examples=200, deadline=None)
    def test_roundtrip_preserves_order_without_duplicates(self, ids: list[int]) -> None:
        expected: list[int] = []
        for value in ids:
            if value not in expected:
                expected.append(value)
        assert parse_value_ids(",".join(str(value) for value in ids)) == expected

    def test_blank_pieces_are_ignored(self) -> None:
        assert parse_value_ids(" 3 , ,4,") == [3, 4]

    def test_empty_axis_is_empty_not_an_error(self) -> None:
        assert parse_value_ids("") == []

    def test_garbage_is_rejected_with_a_stable_code(self) -> None:
        with pytest.raises(VariantError) as excinfo:
            parse_value_ids("3,rojo")
        assert excinfo.value.code == "PRODUCT_ATTRIBUTE_VALUE_UNKNOWN"


class TestComposition:
    def test_label_reads_like_a_person_wrote_it(self) -> None:
        assert compose_label(["M", "Rojo"]) == "M / Rojo"

    def test_label_skips_blanks_instead_of_leaving_separators(self) -> None:
        assert compose_label(["M", "", "  "]) == "M"

    def test_sku_joins_prefix_and_codes(self) -> None:
        assert compose_sku("POL-OVR", ["M", "ROJ"]) == "POL-OVR-M-ROJ"

    def test_sku_omits_missing_codes_instead_of_leaving_a_hole(self) -> None:
        """ "POL--ROJ" es el tipo de dato que después nadie sabe si es un error."""
        assert compose_sku("POL", ["", "ROJ"]) == "POL-ROJ"

    def test_sku_without_prefix_is_just_the_codes(self) -> None:
        assert compose_sku("", ["M", "ROJ"]) == "M-ROJ"

    def test_a_prefix_typed_with_a_trailing_dash_does_not_double_it(self) -> None:
        assert compose_sku("POL-", ["M"]) == "POL-M"
        assert compose_sku("-POL-", ["M"]) == "POL-M"

    def test_an_interior_dash_typed_by_a_person_is_respected(self) -> None:
        """La función responde por las uniones que hace ella. Reescribir el
        prefijo que alguien tecleó sería corregirle el dato a sus espaldas."""
        assert compose_sku("POL-OVR", ["M"]) == "POL-OVR-M"

    @given(
        st.text(alphabet="ABC", min_size=0, max_size=6),
        st.lists(st.text(alphabet="XYZ", min_size=0, max_size=3), max_size=3),
    )
    @settings(max_examples=200, deadline=None)
    def test_the_join_never_introduces_an_empty_segment(
        self, prefix: str, codes: list[str]
    ) -> None:
        sku = compose_sku(prefix, codes)
        kept = [piece.strip() for piece in [prefix, *codes] if piece.strip()]
        assert sku == "-".join(kept)
        assert "" not in (sku.split("-") if sku else [])
