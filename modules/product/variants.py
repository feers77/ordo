"""Combinatoria de variantes: funciones puras, sin base de datos.

Está aparte del servicio a propósito. La matriz talla x color es donde se
duplican SKUs, se pierden combinaciones o se genera un catálogo de 40.000
productos por un cero de más, y todo eso se puede probar con property-based
testing sin levantar Postgres.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import product as cartesian

from ordo_core.errors import KernelError

# Tope por operación. No es una limitación del modelo —una tienda puede tener
# más variantes— sino un cortafuegos: 6 ejes de 10 valores son un millón de
# productos, y casi siempre significa que alguien se equivocó al declarar la
# matriz, no que quiera un millón de poleras.
MAX_VARIANTS = 500


class VariantError(KernelError):
    """Error del catálogo de variantes con código estable."""


def parse_value_ids(raw: str) -> list[int]:
    """Lee el eje guardado como "3,4,5". Preserva el orden y quita duplicados."""
    seen: list[int] = []
    for chunk in raw.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        try:
            value = int(piece)
        except ValueError as exc:
            raise VariantError(
                "PRODUCT_ATTRIBUTE_VALUE_UNKNOWN",
                f"El eje de la matriz contiene '{piece}', que no es un id de valor",
                hint="value_ids es una lista de ids separados por coma, como '3,4,5'.",
            ) from exc
        if value not in seen:
            seen.append(value)
    return seen


def combination_count(axes: Sequence[Sequence[int]]) -> int:
    """Cuántas variantes saldrían, sin materializarlas.

    Se cuenta antes de construir: comprobar el tope después de generar un
    millón de tuplas es comprobarlo tarde.
    """
    if not axes:
        return 0
    total = 1
    for axis in axes:
        total *= len(axis)
    return total


def combinations(axes: Sequence[Sequence[int]]) -> list[tuple[int, ...]]:
    """Producto cartesiano de los ejes, en el orden en que vienen.

    Sin ejes no hay matriz (devuelve vacío, no la tupla vacía: un modelo sin
    atributos no tiene una variante anónima, no tiene ninguna). Un eje vacío
    tampoco genera nada, y eso no es un error: es una matriz declarada a
    medias, que el servicio distingue de una bien declarada.
    """
    if not axes or any(len(axis) == 0 for axis in axes):
        return []
    count = combination_count(axes)
    if count > MAX_VARIANTS:
        raise VariantError(
            "PRODUCT_VARIANT_LIMIT",
            f"La matriz genera {count} variantes y el tope por operación es {MAX_VARIANTS}",
            hint=(
                "Revisa los ejes declarados: casi siempre sobra un atributo. "
                "Si de verdad necesitas más, divide el modelo en varios."
            ),
        )
    return [tuple(combo) for combo in cartesian(*axes)]


def compose_label(names: Sequence[str], *, separator: str = " / ") -> str:
    """La combinación tal como la lee una persona: "M / Rojo"."""
    return separator.join(name.strip() for name in names if name and name.strip())


def compose_sku(prefix: str, codes: Sequence[str]) -> str:
    """SKU de la variante: prefijo del modelo más los códigos de sus valores.

    Un valor sin código corto se omite en vez de meter un guion suelto: un SKU
    con "POL--ROJ" es el tipo de cosa que después nadie sabe si es un error de
    datos o una convención. Por lo mismo se limpian los guiones sobrantes de
    los extremos de cada pieza: un prefijo tecleado como "POL-" no debe
    producir un separador doble. Los guiones interiores sí se respetan, porque
    "POL-OVR" es un prefijo legítimo: la función responde por las uniones que
    hace ella, no por cómo alguien tecleó su prefijo.
    """
    parts = []
    for piece in [prefix, *codes]:
        clean = (piece or "").strip().strip("-").strip()
        if clean:
            parts.append(clean)
    return "-".join(parts)
