"""Órdenes de compra: espejo de ventas, con el proveedor al otro lado.

`vendor_ref` guarda el número de la factura del proveedor: el documento
legal lo emite él, nosotros solo lo registramos y asentamos.
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

PURCHASE_STATES = [
    ("draft", "Borrador"),
    ("confirmed", "Confirmada"),
    ("billed", "Facturada"),
    ("credited", "Con nota de crédito"),
    ("cancelled", "Cancelada"),
]


class PurchaseOrder(Model):
    _name = "purchase.order"
    _description = "Orden de compra"

    name = Char(
        index=True,
        agent_hint="Número de la orden; se asigna al confirmar, no al crear",
        examples=["PO/00001"],
    )
    partner_id = Many2one(
        "res.partner",
        required=True,
        index=True,
        agent_hint="Proveedor de la orden",
        examples=["9"],
    )
    vendor_ref = Char(
        agent_hint="Número de la factura del proveedor, cuando llega",
        examples=["F-4581"],
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
        agent_hint="Diario de compras donde se asentará la factura",
        examples=["2"],
    )
    state = Selection(
        PURCHASE_STATES,
        default="draft",
        agent_hint=(
            "Estado de la orden. Usa action_confirm, action_bill y "
            "action_cancel; nunca escribas este campo directo"
        ),
        examples=["draft", "billed"],
    )
    amount_untaxed = Monetary(
        agent_hint="Total sin impuestos, fijado al confirmar",
        examples=["50000.00"],
    )
    amount_tax = Monetary(
        agent_hint="Impuestos agregados (sin contar retenciones), fijado al confirmar",
        examples=["9500.00"],
    )
    amount_total = Monetary(
        agent_hint="Total con impuestos, fijado al confirmar",
        examples=["59500.00"],
    )
    bill_move_id = Many2one(
        "account.move",
        agent_hint="Asiento de la factura de proveedor, si ya se registró",
        examples=["23"],
    )
    credit_note_move_id = Many2one(
        "account.move",
        agent_hint="Asiento de la nota de crédito del proveedor, si se registró",
        examples=["25"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía compradora", examples=["1"]
    )
    note = Text(agent_hint="Observaciones del pedido", examples=["Urgente"])


class PurchaseOrderLine(Model):
    _name = "purchase.order.line"
    _description = "Línea de orden de compra"

    order_id = Many2one(
        "purchase.order",
        required=True,
        index=True,
        agent_hint="Orden a la que pertenece la línea",
        examples=["1"],
    )
    name = Char(
        required=True,
        agent_hint="Descripción de lo comprado",
        examples=["Hosting dedicado agosto"],
    )
    product_id = Many2one(
        "product.product",
        index=True,
        agent_hint=(
            "Producto comprado, si existe en el catálogo. Con producto almacenable "
            "la orden puede recibirse con action_receive y suma stock al costo"
        ),
        examples=["3"],
    )
    quantity = Char(
        required=True,
        agent_hint="Cantidad como string decimal, nunca float",
        examples=["1", "12"],
    )
    price_unit = Monetary(
        required=True,
        agent_hint="Costo unitario en la moneda de la orden",
        examples=["50000.00"],
    )
    discount_percent = Char(
        agent_hint="Descuento porcentual como string decimal",
        examples=["0"],
    )
    tax_codes = Char(
        agent_hint=(
            "Códigos de impuesto separados por coma, resueltos contra account.tax de la compañía"
        ),
        examples=["IVA19"],
    )
    expense_account_id = Many2one(
        "account.account",
        agent_hint=(
            "Cuenta de gasto de la línea; si falta se usa default_account_id del diario de la orden"
        ),
        examples=["5"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la línea", examples=["1"]
    )
