"""Modelos de webhooks: suscripciones y bitácora de entregas.

La suscripción dice a quién y qué se le manda; la entrega es la prueba de
que se mandó. Esa bitácora también es el watermark: un evento se entrega
una vez por suscripción, aunque el worker se caiga a mitad de lote.
"""

from ordo_core.fields import (
    Char,
    Datetime,
    Integer,
    Many2one,
    Selection,
    Text,
)
from ordo_core.model import Model

SUBSCRIPTION_STATES = [
    ("active", "Activa"),
    ("suspended", "Suspendida"),
]

DELIVERY_STATUSES = [
    ("pending", "Pendiente"),
    ("delivered", "Entregada"),
    ("failed", "Fallida"),
    ("skipped", "Omitida"),
]


class Subscription(Model):
    _name = "webhook.subscription"
    _description = "Suscripción a eventos del outbox"

    name = Char(
        required=True,
        agent_hint="Nombre de la suscripción, para reconocerla en la lista",
        examples=["ERP → CRM", "Alertas de bodega"],
    )
    url = Char(
        required=True,
        index=True,
        agent_hint="Endpoint HTTP que recibe el POST firmado de cada evento",
        examples=["https://crm.example.com/hooks/ordo"],
    )
    event_pattern = Char(
        required=True,
        agent_hint=(
            "Patrón fnmatch sobre el event_type: '*' recibe todo, 'sale.order.*' solo los de ventas"
        ),
        examples=["*", "sale.order.*", "account.move.posted"],
    )
    secret = Char(
        required=True,
        agent_hint=(
            "Lo genera el sistema al crear y firma con HMAC cada entrega "
            "(cabecera X-Ordo-Signature). Se muestra completo una sola vez: "
            "crea la suscripción con el servicio de webhooks, no a mano, y "
            "usa action_rotate_secret si lo perdiste"
        ),
        examples=["9f2c…"],
    )
    state = Selection(
        SUBSCRIPTION_STATES,
        default="active",
        agent_hint=(
            "Diez fallos consecutivos la suspenden y deja de recibir; usa "
            "action_suspend y action_resume, nunca escribas esto directo"
        ),
        examples=["active", "suspended"],
    )
    failure_count = Integer(
        default=0,
        agent_hint="Fallos consecutivos; una entrega exitosa lo vuelve a cero",
        examples=["0", "3"],
    )
    last_delivery_at = Datetime(
        agent_hint="Momento de la última entrega exitosa (UTC)",
        examples=["2026-08-04T14:32:10+00:00"],
    )
    company_id = Many2one(
        "res.company",
        agent_hint="Compañía cuyos eventos se envían; vacío = eventos de todo el tenant",
        examples=["1"],
    )


class Delivery(Model):
    _name = "webhook.delivery"
    _description = "Intento de entrega de un evento a una suscripción"

    subscription_id = Many2one(
        "webhook.subscription",
        required=True,
        index=True,
        agent_hint="Suscripción a la que se entregó (o se intentó entregar)",
        examples=["1"],
    )
    event_id = Integer(
        required=True,
        index=True,
        agent_hint="Id del evento en ir_outbox; marca hasta dónde leyó la suscripción",
        examples=["1042"],
    )
    event_type = Char(
        required=True,
        agent_hint="Tipo del evento entregado, copiado del outbox",
        examples=["sale.order.confirmed"],
    )
    status = Selection(
        DELIVERY_STATUSES,
        default="pending",
        agent_hint=(
            "Entregada = respondió 2xx; fallida = no; omitida = vista pero "
            "fuera del patrón, solo avanza el watermark"
        ),
        examples=["delivered", "failed", "skipped"],
    )
    attempts = Integer(
        default=0,
        agent_hint="Intentos hechos; el reintento para a los 5",
        examples=["1", "4"],
    )
    response_status = Integer(
        agent_hint="Código HTTP que devolvió el receptor; 599 si ni siquiera respondió",
        examples=["200", "500", "599"],
    )
    error = Text(
        agent_hint="Detalle del fallo, tal como lo reportó el transporte",
        examples=["ConnectTimeout"],
    )
    delivered_at = Datetime(
        agent_hint="Momento en que el receptor aceptó el evento (UTC)",
        examples=["2026-08-04T14:32:10+00:00"],
    )
    company_id = Many2one(
        "res.company",
        agent_hint="Compañía de la suscripción, si la tiene",
        examples=["1"],
    )
