"""Puente entre documentos comerciales y el asiento contable.

Ventas y compras comparten esta pieza: resolver impuestos por código,
calcular totales con el motor de F4-02 y construir partidas que cuadran por
construcción. El asiento resultante pasa igual por `validate_lines`: si esta
lógica tuviera un error, el invariante de partida doble lo detiene.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.account.services import AccountingError
from modules.account.taxes import Tax, TaxResult, compute_document

ZERO = Decimal("0")


@dataclass(frozen=True)
class ResolvedLine:
    """Línea comercial con sus impuestos ya resueltos a objetos del motor."""

    name: str
    quantity: Decimal
    price_unit: Decimal
    discount_percent: Decimal
    taxes: list[Tax]
    account_id: int | None


async def resolve_taxes(
    env: Environment,
    codes: list[str],
    *,
    company_id: int,
    side: str,  # sale | purchase
    error_prefix: str,
) -> dict[str, tuple[dict[str, Any], Tax]]:
    """Convierte códigos en pares (registro account.tax, impuesto del motor)."""
    if not codes:
        return {}
    records = RecordSet(env, "account.tax")
    result = await records.search(
        [("company_id", "=", company_id), ("code", "in", sorted(set(codes)))],
        fields=[
            "id",
            "code",
            "name",
            "rate",
            "tax_type",
            "price_include",
            "is_withholding",
            "applies_to",
            "account_id",
        ],
        limit=len(set(codes)),
    )
    by_code: dict[str, tuple[dict[str, Any], Tax]] = {}
    for row in result["rows"]:
        by_code[row["code"]] = (
            row,
            Tax(
                code=row["code"],
                name=row["name"],
                amount=Decimal(row["rate"]),
                tax_type=row["tax_type"],
                price_include=row["price_include"],
                is_withholding=row["is_withholding"],
            ),
        )
    for code in codes:
        if code not in by_code:
            raise AccountingError(
                f"{error_prefix}_TAX_UNKNOWN",
                f"El impuesto '{code}' no existe para esta compañía",
                hint="Créalo en account.tax o corrige el código de la línea.",
            )
        row, _ = by_code[code]
        if row["applies_to"] not in (side, "both"):
            raise AccountingError(
                f"{error_prefix}_TAX_UNKNOWN",
                f"El impuesto '{code}' no aplica a documentos de tipo {side}",
                hint="Usa un impuesto con applies_to compatible.",
            )
    return by_code


def compute_totals(lines: list[ResolvedLine], *, decimals: int) -> TaxResult:
    return compute_document(
        [
            {
                "price_unit": line.price_unit,
                "quantity": line.quantity,
                "discount_percent": line.discount_percent,
                "taxes": line.taxes,
            }
            for line in lines
        ],
        decimals=decimals,
    )


async def settings_for(env: Environment, company_id: int) -> dict[str, Any]:
    settings = RecordSet(env, "account.settings")
    result = await settings.search(
        [("company_id", "=", company_id)],
        fields=["id", "receivable_account_id", "payable_account_id"],
        limit=1,
    )
    if not result["rows"]:
        raise AccountingError(
            "ACCOUNT_SETTINGS_MISSING",
            "La compañía no tiene configuración contable (account.settings)",
            hint="Crea la fila con las cuentas por cobrar y por pagar.",
        )
    return result["rows"][0]


def build_invoice_lines(
    *,
    kind: str,  # customer | vendor
    resolved_lines: list[ResolvedLine],
    totals: TaxResult,
    taxes_by_code: dict[str, tuple[dict[str, Any], Tax]],
    counterpart_account_id: int,
    partner_id: int,
    fallback_account_id: int | None,
    error_prefix: str,
    decimals: int,
) -> list[dict[str, Any]]:
    """Arma las partidas del asiento de una factura de cliente o proveedor.

    Cliente: por cobrar al debe; ingreso e impuestos al haber. Proveedor:
    espejo exacto. Las retenciones van del lado contrario a su impuesto y
    reducen la contrapartida.
    """
    base_side, counter_side = ("credit", "debit") if kind == "customer" else ("debit", "credit")

    entries: list[dict[str, Any]] = []
    for line in resolved_lines:
        account_id = line.account_id or fallback_account_id
        if account_id is None:
            raise AccountingError(
                f"{error_prefix}_NO_ACCOUNT",
                f"La línea '{line.name}' no tiene cuenta y el diario no define una por defecto",
                hint="Fija la cuenta en la línea o default_account_id del diario.",
            )
        single = compute_document(
            [
                {
                    "price_unit": line.price_unit,
                    "quantity": line.quantity,
                    "discount_percent": line.discount_percent,
                    "taxes": line.taxes,
                }
            ],
            decimals=decimals,
        )
        if single.base != ZERO:
            # Una línea cuya base redondea a cero no genera partida: una
            # partida 0/0 no es contable y el invariante la rechazaría.
            entries.append({"account_id": account_id, "name": line.name, base_side: single.base})

    withheld = ZERO
    for tax_line in totals.taxes:
        if tax_line.amount == ZERO:
            continue
        row, _ = taxes_by_code[tax_line.code]
        if row["account_id"] is None:
            raise AccountingError(
                "ACCOUNT_TAX_NO_ACCOUNT",
                f"El impuesto '{tax_line.code}' no tiene cuenta contable asignada",
                hint="Fija account_id en el registro account.tax.",
            )
        if tax_line.is_withholding:
            withheld += tax_line.amount
            entries.append(
                {
                    "account_id": row["account_id"],
                    "name": tax_line.name,
                    counter_side: tax_line.amount,
                }
            )
        else:
            entries.append(
                {
                    "account_id": row["account_id"],
                    "name": tax_line.name,
                    base_side: tax_line.amount,
                }
            )

    counterpart = totals.total_included - withheld
    if not entries or counterpart == ZERO:
        raise AccountingError(
            f"{error_prefix}_ZERO_TOTAL",
            "El documento redondea a cero: no hay nada que asentar",
            hint="Revisa importes y descuentos de las líneas.",
        )
    entries.insert(
        0,
        {
            "account_id": counterpart_account_id,
            "name": "Por cobrar" if kind == "customer" else "Por pagar",
            "partner_id": partner_id,
            counter_side: counterpart,
        },
    )
    return entries
