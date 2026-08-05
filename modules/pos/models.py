"""Punto de venta: la caja, sus medios de cobro y el turno.

El turno es la unidad de responsabilidad: se abre con un fondo declarado, se
cierra contando el efectivo, y la diferencia entre lo contado y lo esperado se
asienta. Ese asiento es todo lo que el cierre contabiliza; cada ticket lleva su
propio asiento, porque cada boleta es un documento legal con folio (ADR-019).
"""

from ordo_core.fields import Boolean, Char, Datetime, Many2one, Monetary, Selection, Text
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
