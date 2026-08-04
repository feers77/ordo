"""Modelos contables: cuentas, diarios, períodos, asientos y partidas.

La contabilidad es donde un error no se nota hasta el cierre, cuando ya es
caro. Por eso los invariantes de `services.py` no acompañan al código: son
la especificación, y están probados con property-based testing.
"""

from ordo_core.fields import (
    Boolean,
    Char,
    Date,
    Many2one,
    Monetary,
    Selection,
    Text,
)
from ordo_core.model import Model

ACCOUNT_TYPES = [
    ("asset", "Activo"),
    ("liability", "Pasivo"),
    ("equity", "Patrimonio"),
    ("income", "Ingreso"),
    ("expense", "Gasto"),
]

JOURNAL_TYPES = [
    ("sale", "Ventas"),
    ("purchase", "Compras"),
    ("cash", "Efectivo"),
    ("bank", "Banco"),
    ("general", "General"),
]


class Account(Model):
    _name = "account.account"
    _description = "Cuenta contable"

    code = Char(
        required=True,
        index=True,
        agent_hint="Código de la cuenta en el plan contable, único por compañía",
        examples=["1101", "2101001"],
    )
    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre descriptivo de la cuenta",
        examples=["Caja", "IVA débito fiscal"],
    )
    account_type = Selection(
        ACCOUNT_TYPES,
        required=True,
        agent_hint=(
            "Naturaleza de la cuenta. Determina su signo natural y en qué estado "
            "financiero aparece: activo y gasto aumentan al debe; pasivo, patrimonio "
            "e ingreso aumentan al haber"
        ),
        examples=["asset", "income"],
    )
    reconcile = Boolean(
        default=False,
        agent_hint=(
            "Verdadero si sus partidas se concilian entre sí, como cuentas por "
            "cobrar o pagar. Una cuenta de gasto normalmente no se concilia"
        ),
        examples=["true", "false"],
    )
    currency_id = Many2one(
        "res.currency",
        agent_hint="Moneda si la cuenta opera en una distinta a la de la compañía",
        examples=["2"],
    )
    company_id = Many2one(
        "res.company",
        required=True,
        agent_hint="Compañía dueña de la cuenta; los planes contables no se comparten",
        examples=["1"],
    )
    active = Boolean(default=True, agent_hint="Cuenta disponible para usar", examples=["true"])


class Journal(Model):
    _name = "account.journal"
    _description = "Diario contable"

    code = Char(
        required=True,
        index=True,
        agent_hint="Código corto del diario",
        examples=["VTA", "CMP", "BCO"],
    )
    name = Char(required=True, agent_hint="Nombre del diario", examples=["Ventas"])
    journal_type = Selection(
        JOURNAL_TYPES,
        required=True,
        agent_hint="Tipo de operación que registra el diario",
        examples=["sale", "bank"],
    )
    sequence_code = Char(
        required=True,
        agent_hint=(
            "Código de la secuencia que numera sus asientos. Para diarios con "
            "efecto fiscal debe ser una secuencia sin huecos"
        ),
        examples=["account.move.sale"],
    )
    default_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta que se propone por defecto en las partidas del diario",
        examples=["5"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del diario", examples=["1"]
    )
    active = Boolean(default=True, agent_hint="Diario disponible", examples=["true"])


class Period(Model):
    _name = "account.period"
    _description = "Período contable"

    name = Char(
        required=True,
        agent_hint="Nombre del período",
        examples=["2026-08", "Ejercicio 2026"],
    )
    date_from = Date(
        required=True,
        index=True,
        agent_hint="Primer día que cubre el período",
        examples=["2026-08-01"],
    )
    date_to = Date(
        required=True,
        index=True,
        agent_hint="Último día que cubre el período",
        examples=["2026-08-31"],
    )
    state = Selection(
        [("open", "Abierto"), ("closed", "Cerrado")],
        default="open",
        agent_hint=(
            "Un período cerrado no admite asientos nuevos. Reabrirlo es una acción "
            "excepcional que queda registrada en la auditoría"
        ),
        examples=["open"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del período", examples=["1"]
    )


class Move(Model):
    _name = "account.move"
    _description = "Asiento contable"

    name = Char(
        index=True,
        agent_hint=(
            "Número legal del asiento. Se asigna al contabilizar, no al crear el "
            "borrador, para que un borrador descartado no consuma numeración"
        ),
        examples=["VTA/2026/00001"],
    )
    journal_id = Many2one(
        "account.journal",
        required=True,
        agent_hint="Diario en que se registra el asiento",
        examples=["1"],
    )
    date = Date(
        required=True,
        index=True,
        agent_hint=(
            "Fecha contable del asiento. Debe caer en un período abierto; determina "
            "el ejercicio al que pertenece"
        ),
        examples=["2026-08-04"],
    )
    ref = Char(
        agent_hint="Referencia del documento de origen",
        examples=["Factura 12345"],
    )
    state = Selection(
        [("draft", "Borrador"), ("posted", "Contabilizado"), ("cancel", "Anulado")],
        default="draft",
        agent_hint=(
            "Un asiento contabilizado no se modifica ni se borra: para corregirlo se "
            "emite un asiento de reversión. Usa action_post y action_reverse, nunca "
            "escribas este campo directamente"
        ),
        examples=["draft", "posted"],
    )
    partner_id = Many2one(
        "res.partner",
        agent_hint="Tercero del documento, si corresponde",
        examples=["7"],
    )
    currency_id = Many2one(
        "res.currency",
        required=True,
        agent_hint="Moneda del asiento; todas sus partidas comparten esta moneda",
        examples=["1"],
    )
    amount_total = Monetary(
        agent_hint="Suma del debe del asiento, que por partida doble iguala al haber",
        examples=["119000.00"],
    )
    reversed_entry_id = Many2one(
        "account.move",
        agent_hint="Asiento que este revierte, si es una reversión",
        examples=["42"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del asiento", examples=["1"]
    )
    narration = Text(agent_hint="Glosa o explicación del asiento", examples=["Venta al contado"])


class MoveLine(Model):
    _name = "account.move.line"
    _description = "Partida contable"

    move_id = Many2one(
        "account.move",
        required=True,
        index=True,
        agent_hint="Asiento al que pertenece la partida",
        examples=["1"],
    )
    account_id = Many2one(
        "account.account",
        required=True,
        index=True,
        agent_hint="Cuenta contable afectada por la partida",
        examples=["3"],
    )
    name = Char(
        agent_hint="Detalle de la partida",
        examples=["Venta mercadería"],
    )
    debit = Monetary(
        agent_hint=(
            "Importe al debe. En una misma partida el debe o el haber es cero: "
            "nunca los dos a la vez, y ninguno puede ser negativo"
        ),
        examples=["119000.00", "0.00"],
    )
    credit = Monetary(
        agent_hint="Importe al haber, con las mismas reglas que el debe",
        examples=["0.00", "100000.00"],
    )
    partner_id = Many2one(
        "res.partner",
        agent_hint="Tercero de la partida, necesario en cuentas por cobrar o pagar",
        examples=["7"],
    )
    date_maturity = Date(
        agent_hint="Fecha de vencimiento en partidas de cobro o pago",
        examples=["2026-09-03"],
    )
    reconciled = Boolean(
        default=False,
        agent_hint="Verdadero cuando la partida quedó conciliada contra otras",
        examples=["false"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la partida", examples=["1"]
    )
