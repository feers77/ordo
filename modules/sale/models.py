"""Órdenes de venta: el documento comercial que termina en un asiento.

Los totales se calculan al confirmar y quedan fijos: si el pedido cambia,
vuelve a borrador, se corrige y se reconfirma. Una orden facturada es
historia contable y no se toca.
"""

from ordo_core.fields import (
    Char,
    Date,
    Many2one,
    Monetary,
    Selection,
    Text,
)
from ordo_core.model import Model

SALE_STATES = [
    ("draft", "Borrador"),
    ("confirmed", "Confirmada"),
    ("invoiced", "Facturada"),
    ("credited", "Con nota de crédito"),
    ("cancelled", "Cancelada"),
]


class SaleOrder(Model):
    _name = "sale.order"
    _description = "Orden de venta"

    name = Char(
        index=True,
        agent_hint="Número de la orden; se asigna al confirmar, no al crear",
        examples=["SO/00001"],
    )
    partner_id = Many2one(
        "res.partner",
        required=True,
        index=True,
        agent_hint="Cliente de la orden",
        examples=["7"],
    )
    date_order = Date(
        required=True,
        index=True,
        agent_hint="Fecha del pedido; también fecha contable de la factura",
        examples=["2026-08-04"],
    )
    currency_id = Many2one(
        "res.currency",
        required=True,
        agent_hint="Moneda de la orden",
        examples=["1"],
    )
    journal_id = Many2one(
        "account.journal",
        required=True,
        agent_hint="Diario de ventas donde se asentará la factura",
        examples=["1"],
    )
    state = Selection(
        SALE_STATES,
        default="draft",
        agent_hint=(
            "Estado de la orden. Usa action_confirm, action_invoice y "
            "action_cancel; nunca escribas este campo directo"
        ),
        examples=["draft", "invoiced"],
    )
    amount_untaxed = Monetary(
        agent_hint="Total sin impuestos, fijado al confirmar",
        examples=["100000.00"],
    )
    amount_tax = Monetary(
        agent_hint="Impuestos agregados (sin contar retenciones), fijado al confirmar",
        examples=["19000.00"],
    )
    amount_total = Monetary(
        agent_hint="Total con impuestos, fijado al confirmar",
        examples=["119000.00"],
    )
    invoice_move_id = Many2one(
        "account.move",
        agent_hint="Asiento de la factura generada, si ya se facturó",
        examples=["17"],
    )
    credit_note_move_id = Many2one(
        "account.move",
        agent_hint="Asiento de la nota de crédito que revirtió la factura, si existe",
        examples=["19"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía vendedora", examples=["1"]
    )
    note = Text(agent_hint="Observaciones del pedido", examples=["Entrega en obra"])


class SaleOrderLine(Model):
    _name = "sale.order.line"
    _description = "Línea de orden de venta"

    order_id = Many2one(
        "sale.order",
        required=True,
        index=True,
        agent_hint="Orden a la que pertenece la línea",
        examples=["1"],
    )
    name = Char(
        required=True,
        agent_hint="Descripción de lo vendido",
        examples=["Licencia anual plan Pro"],
    )
    product_id = Many2one(
        "product.product",
        index=True,
        agent_hint=(
            "Producto vendido, si existe en el catálogo. Con producto almacenable "
            "la orden puede entregarse con action_deliver y descuenta stock"
        ),
        examples=["3"],
    )
    quantity = Char(
        required=True,
        agent_hint="Cantidad como string decimal, nunca float",
        examples=["1", "2.5"],
    )
    price_unit = Monetary(
        required=True,
        agent_hint="Precio unitario en la moneda de la orden",
        examples=["100000.00"],
    )
    discount_percent = Char(
        agent_hint="Descuento porcentual como string decimal",
        examples=["0", "10"],
    )
    tax_codes = Char(
        agent_hint=(
            "Códigos de impuesto separados por coma, resueltos contra account.tax de la compañía"
        ),
        examples=["IVA19", "IVA19,RET_HON"],
    )
    income_account_id = Many2one(
        "account.account",
        agent_hint=(
            "Cuenta de ingreso de la línea; si falta se usa default_account_id "
            "del diario de la orden"
        ),
        examples=["4"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la línea", examples=["1"]
    )
