"""Punto de venta: la caja, sus medios de cobro y el turno.

El turno es la unidad de responsabilidad: se abre con un fondo declarado, se
cierra contando el efectivo, y la diferencia entre lo contado y lo esperado se
asienta. Ese asiento es todo lo que el cierre contabiliza; cada ticket lleva su
propio asiento, porque cada boleta es un documento legal con folio (ADR-019).
"""

from ordo_core.fields import (
    Boolean,
    Char,
    Date,
    Datetime,
    Many2one,
    Monetary,
    Selection,
    Text,
)
from ordo_core.model import Model

SESSION_STATES = [
    ("draft", "Por abrir"),
    ("opened", "Abierto"),
    ("closing", "En cierre"),
    ("closed", "Cerrado"),
    ("cancelled", "Cancelado"),
]

PAYMENT_METHOD_TYPES = [
    ("cash", "Efectivo"),
    ("card", "Tarjeta"),
    ("transfer", "Transferencia"),
    ("voucher", "Vale o canje"),
]


class PosConfig(Model):
    _name = "pos.config"
    _description = "Caja de un punto de venta"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre de la caja, tal como la ve el cajero al abrir su turno",
        examples=["Caja 1", "Caja Providencia"],
    )
    warehouse_id = Many2one(
        "stock.warehouse",
        agent_hint="Almacén al que pertenece la tienda donde está esta caja",
        examples=["2"],
    )
    location_id = Many2one(
        "stock.location",
        required=True,
        agent_hint=(
            "Ubicación interna desde la que sale la mercadería vendida en esta "
            "caja; es la sala de ventas, no la bodega central"
        ),
        examples=["5"],
    )
    journal_id = Many2one(
        "account.journal",
        required=True,
        agent_hint="Diario de ventas donde se asientan los tickets de esta caja",
        examples=["1"],
    )
    cash_journal_id = Many2one(
        "account.journal",
        required=True,
        agent_hint="Diario contra el que se asienta la diferencia de arqueo del turno",
        examples=["4"],
    )
    cash_account_id = Many2one(
        "account.account",
        required=True,
        agent_hint="Cuenta de caja: el efectivo que físicamente está en el cajón",
        examples=["2"],
    )
    difference_account_id = Many2one(
        "account.account",
        required=True,
        agent_hint=(
            "Cuenta donde aterrizan faltantes y sobrantes de caja. Es la que se "
            "mira cuando hay que explicar por qué falta plata"
        ),
        examples=["14"],
    )
    document_type_code = Char(
        agent_hint=(
            "Tipo de documento electrónico por defecto del país; en Chile 39 es "
            "la boleta y 33 la factura"
        ),
        examples=["39"],
    )
    anonymous_partner_id = Many2one(
        "res.partner",
        agent_hint=(
            "Contacto genérico con el que se emiten las boletas sin cliente "
            "identificado, que en retail son casi todas"
        ),
        examples=["3"],
    )
    price_includes_tax = Boolean(
        default=True,
        agent_hint=(
            "Verdadero si los precios de venta ya traen el impuesto dentro, como "
            "exige el retail chileno; cambia qué impuestos se resuelven, no el total"
        ),
        examples=["true"],
    )
    currency_id = Many2one(
        "res.currency",
        required=True,
        agent_hint="Moneda en que cobra esta caja",
        examples=["1"],
    )
    active = Boolean(default=True, agent_hint="Caja operativa", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía dueña de la caja", examples=["1"]
    )


class PosPaymentMethod(Model):
    _name = "pos.payment.method"
    _description = "Medio de cobro de un punto de venta"

    name = Char(
        required=True,
        agent_hint="Nombre del medio de cobro tal como lo ofrece el cajero",
        examples=["Efectivo", "Tarjeta de débito"],
    )
    code = Char(
        required=True,
        index=True,
        agent_hint="Código estable del medio de cobro, para integraciones",
        examples=["EFECTIVO", "TARJETA"],
    )
    method_type = Selection(
        PAYMENT_METHOD_TYPES,
        required=True,
        agent_hint=(
            "El efectivo se arquea al cierre del turno; los demás se liquidan "
            "contra un tercero y no entran en el conteo del cajón"
        ),
        examples=["cash", "card"],
    )
    config_id = Many2one(
        "pos.config",
        required=True,
        index=True,
        agent_hint="Caja que ofrece este medio de cobro",
        examples=["1"],
    )
    settlement_account_id = Many2one(
        "account.account",
        required=True,
        agent_hint=(
            "Cuenta donde queda el cobro hasta que el dinero llega: caja para el "
            "efectivo, deudores por tarjetas para el plástico"
        ),
        examples=["2"],
    )
    opens_drawer = Boolean(
        default=False,
        agent_hint="Si el cobro abre el cajón físico; es un dato para el terminal, no contable",
        examples=["true"],
    )
    active = Boolean(default=True, agent_hint="Medio de cobro disponible", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del medio de cobro", examples=["1"]
    )


class PosSession(Model):
    _name = "pos.session"
    _description = "Turno de caja"
    _order = "id desc"

    name = Char(
        index=True,
        agent_hint="Número del turno; se asigna al abrirlo, no al crearlo",
        examples=["POS/00001"],
    )
    config_id = Many2one(
        "pos.config",
        required=True,
        index=True,
        agent_hint="Caja a la que pertenece el turno",
        examples=["1"],
    )
    state = Selection(
        SESSION_STATES,
        default="draft",
        agent_hint=(
            "Estado del turno. Usa action_open, action_close_register y "
            "action_close; nunca escribas este campo directamente"
        ),
        examples=["opened", "closed"],
    )
    opened_at = Datetime(
        agent_hint="Momento UTC en que se abrió el turno",
        examples=["2026-08-05T13:00:00Z"],
    )
    closed_at = Datetime(
        agent_hint="Momento UTC en que se cerró el turno",
        examples=["2026-08-05T21:30:00Z"],
    )
    opening_cash = Monetary(
        agent_hint="Fondo de caja declarado al abrir; es la base del arqueo",
        examples=["50000.00"],
    )
    counted_cash = Monetary(
        agent_hint="Efectivo físicamente contado al cerrar el turno",
        examples=["213500.00"],
    )
    expected_cash = Monetary(
        agent_hint=(
            "Efectivo que debería haber según fondo, cobros en efectivo, vueltos "
            "y retiros. Lo calcula el sistema al cerrar"
        ),
        examples=["214000.00"],
    )
    difference = Monetary(
        agent_hint=(
            "Contado menos esperado. Negativo es faltante y se asienta como "
            "pérdida; positivo es sobrante"
        ),
        examples=["-500.00"],
    )
    withdrawals = Monetary(
        default=None,
        agent_hint=(
            "Efectivo retirado del cajón durante el turno, por ejemplo para "
            "depositarlo; baja el efectivo esperado"
        ),
        examples=["100000.00"],
    )
    move_id = Many2one(
        "account.move",
        agent_hint="Asiento de la diferencia de arqueo, si la hubo; sin diferencia no hay asiento",
        examples=["48"],
    )
    note = Text(
        agent_hint="Explicación de la diferencia de caja, si la hubo",
        examples=["Faltan $500; se dio vuelto de más en el último ticket"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del turno", examples=["1"]
    )


ORDER_STATES = [
    ("draft", "En curso"),
    ("paid", "Cobrado"),
    ("cancelled", "Cancelado"),
]


class PosOrder(Model):
    _name = "pos.order"
    _description = "Ticket de punto de venta"
    _order = "id desc"

    name = Char(
        index=True,
        agent_hint="Número del ticket; se asigna al validarlo, no al crearlo",
        examples=["T/00001"],
    )
    session_id = Many2one(
        "pos.session",
        required=True,
        index=True,
        agent_hint="Turno en el que se vendió; un ticket no existe fuera de un turno abierto",
        examples=["1"],
    )
    terminal_ref = Char(
        index=True,
        agent_hint=(
            "Referencia local del terminal. Evita registrar dos veces el mismo "
            "ticket cuando el terminal reintenta tras un corte"
        ),
        examples=["CAJA1-20260805-0042"],
    )
    partner_id = Many2one(
        "res.partner",
        index=True,
        agent_hint=(
            "Cliente identificado, si lo hay. Vacío es consumidor final, que en "
            "retail son casi todos los tickets"
        ),
        examples=["4"],
    )
    state = Selection(
        ORDER_STATES,
        default="draft",
        agent_hint=(
            "Estado del ticket. Usa action_validate y action_cancel; nunca "
            "escribas este campo directamente"
        ),
        examples=["draft", "paid"],
    )
    date_order = Date(
        required=True,
        agent_hint="Fecha del ticket; también es la fecha contable de su asiento",
        examples=["2026-08-05"],
    )
    currency_id = Many2one(
        "res.currency",
        required=True,
        agent_hint="Moneda del ticket",
        examples=["1"],
    )
    amount_untaxed = Monetary(
        agent_hint="Neto sin impuestos; lo fija el sistema al validar",
        examples=["20000.00"],
    )
    amount_tax = Monetary(
        agent_hint="Impuestos del ticket; los fija el sistema al validar",
        examples=["3800.00"],
    )
    amount_total = Monetary(
        agent_hint="Total cobrado al cliente; lo fija el sistema al validar",
        examples=["23800.00"],
    )
    change = Monetary(
        agent_hint=(
            "Vuelto entregado en efectivo. Solo puede salir del efectivo "
            "recibido, nunca de un cobro con tarjeta"
        ),
        examples=["1200.00"],
    )
    move_id = Many2one(
        "account.move",
        agent_hint="Asiento del ticket; cada boleta lleva el suyo, no se agregan por turno",
        examples=["51"],
    )
    picking_id = Many2one(
        "stock.picking",
        agent_hint=(
            "Movimiento de stock del ticket: uno por ticket y no agregado al "
            "cierre, para que la bodega no mienta durante todo el turno"
        ),
        examples=["23"],
    )
    refund_of_id = Many2one(
        "pos.order",
        index=True,
        agent_hint=(
            "Ticket original que esta devolución revierte. El original no cambia "
            "de estado: la devolución es un documento nuevo"
        ),
        examples=["12"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del ticket", examples=["1"]
    )


class PosOrderLine(Model):
    _name = "pos.order.line"
    _description = "Línea de ticket"

    order_id = Many2one(
        "pos.order",
        required=True,
        index=True,
        agent_hint="Ticket al que pertenece la línea",
        examples=["12"],
    )
    name = Char(
        required=True,
        agent_hint="Descripción tal como se imprime en el ticket",
        examples=["Polera Oversize M / Rojo"],
    )
    product_id = Many2one(
        "product.product",
        required=True,
        index=True,
        agent_hint=("Producto vendido; si es almacenable, el ticket descuenta stock al validar"),
        examples=["31"],
    )
    quantity = Char(
        required=True,
        agent_hint="Cantidad como string decimal, nunca float",
        examples=["2"],
    )
    price_unit = Monetary(
        required=True,
        agent_hint=(
            "Precio unitario cobrado, con o sin impuesto según price_includes_tax de la caja"
        ),
        examples=["11900.00"],
    )
    discount_percent = Char(
        agent_hint="Descuento porcentual de la línea, como string decimal",
        examples=["10"],
    )
    tax_codes = Char(
        agent_hint="Códigos de impuesto separados por coma, resueltos contra account.tax",
        examples=["IVA19I"],
    )
    income_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de ingreso propia de la línea; si falta, la del producto o el diario",
        examples=["4"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la línea", examples=["1"]
    )


class PosPayment(Model):
    _name = "pos.payment"
    _description = "Cobro de un ticket"

    order_id = Many2one(
        "pos.order",
        required=True,
        index=True,
        agent_hint="Ticket que se está cobrando",
        examples=["12"],
    )
    method_id = Many2one(
        "pos.payment.method",
        required=True,
        index=True,
        agent_hint="Medio con el que se cobró; su cuenta define dónde queda el dinero",
        examples=["1"],
    )
    amount = Monetary(
        required=True,
        agent_hint=(
            "Importe entregado con este medio. La suma de los cobros debe cubrir "
            "el total del ticket; lo que sobre es vuelto y solo sale del efectivo"
        ),
        examples=["10000.00"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del cobro", examples=["1"]
    )
