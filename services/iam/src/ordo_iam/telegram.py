"""Telegram como canal HITL: firma de callbacks, webhook y cliente (F1-07).

Tres cosas sostienen la seguridad de este canal:

1. El webhook sólo atiende peticiones que traen el secreto acordado con
   Telegram en `X-Telegram-Bot-Api-Secret-Token`. Sin secreto configurado, el
   endpoint no procesa nada (falla cerrado).
2. El `callback_data` de los botones va firmado con HMAC-SHA256 por el
   servidor: un botón fabricado a mano no verifica.
3. Un callback firmado sólo vale si llega desde un chat verificado por su
   dueño, y la resolución la sigue haciendo `ApprovalService.resolve`, que
   exige que el aprobador sea el dueño del agente. Firma válida desde otro
   usuario ⇒ rechazo.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.approvals import ApprovalService
from ordo_iam.channels import LINK_CODE_ALPHABET, LINK_CODE_LENGTH, ChannelService
from ordo_iam.errors import (
    CallbackInvalidError,
    ChannelNotVerifiedError,
    LinkCodeInvalidError,
    TelegramDeliveryError,
    TelegramNotConfiguredError,
    WebhookUnauthorizedError,
)
from ordo_iam.models import Agent, ApprovalRequest, ChannelType, Principal
from ordo_iam.notifications import (
    Button,
    NotificationSender,
    OutboundMessage,
    enqueue_message,
)

WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 — nombre de cabecera
TELEGRAM_API_BASE = "https://api.telegram.org"
#: Telegram trunca el callback_data a 64 bytes; por eso uuid en hex y firma corta.
TELEGRAM_CALLBACK_MAX_BYTES = 64
CALLBACK_PREFIX = "a1"
CALLBACK_SIGNATURE_CHARS = 20  # 80 bits de HMAC truncado
ACTION_APPROVE = "a"
ACTION_REJECT = "r"
#: Etiqueta de dominio: la clave de firma se deriva, nunca es el secreto crudo.
CALLBACK_KEY_INFO = b"ordo/telegram/callback/v1"


# -- configuración -----------------------------------------------------------


def webhook_secret() -> str:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        raise TelegramNotConfiguredError(
            "TELEGRAM_WEBHOOK_SECRET no está configurado.",
            hint="Define el secreto del webhook antes de habilitar el canal Telegram.",
        )
    return secret


def bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise TelegramNotConfiguredError(
            "TELEGRAM_BOT_TOKEN no está configurado.",
            hint="Define el token del bot antes de habilitar el canal Telegram.",
        )
    return token


def _callback_key() -> bytes:
    """Clave de firma derivada del secreto: un secreto, un uso."""
    return hmac.new(webhook_secret().encode(), CALLBACK_KEY_INFO, hashlib.sha256).digest()


# -- firma de callbacks ------------------------------------------------------


def _signature(body: str) -> str:
    digest = hmac.new(_callback_key(), body.encode(), hashlib.sha256).hexdigest()
    return digest[:CALLBACK_SIGNATURE_CHARS]


def sign_callback(approval_id: uuid.UUID, *, approve: bool) -> str:
    body = f"{CALLBACK_PREFIX}:{approval_id.hex}:{ACTION_APPROVE if approve else ACTION_REJECT}"
    data = f"{body}:{_signature(body)}"
    assert len(data.encode()) <= TELEGRAM_CALLBACK_MAX_BYTES
    return data


def verify_callback(data: str) -> tuple[uuid.UUID, bool]:
    """Devuelve (approval_id, approve) o falla: nada sin firma válida."""
    _callback_key()  # falla cerrado si no hay secreto configurado
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != CALLBACK_PREFIX:
        raise CallbackInvalidError("Callback de Telegram inválido.")
    _, approval_hex, action, signature = parts
    if action not in (ACTION_APPROVE, ACTION_REJECT):
        raise CallbackInvalidError("Callback de Telegram inválido.")
    body = f"{CALLBACK_PREFIX}:{approval_hex}:{action}"
    if not hmac.compare_digest(signature, _signature(body)):
        raise CallbackInvalidError("Callback de Telegram inválido.")
    try:
        approval_id = uuid.UUID(hex=approval_hex)
    except ValueError as exc:
        raise CallbackInvalidError("Callback de Telegram inválido.") from exc
    return approval_id, action == ACTION_APPROVE


def verify_webhook_secret(header_value: str | None) -> None:
    """Sin cabecera correcta no se procesa el update: se rechaza antes de leerlo."""
    if not header_value or not hmac.compare_digest(header_value, webhook_secret()):
        raise WebhookUnauthorizedError(
            "Webhook de Telegram no autorizado.",
            hint="Configura el mismo secret_token en setWebhook y en TELEGRAM_WEBHOOK_SECRET.",
        )


# -- códigos de vinculación --------------------------------------------------


def normalize_link_code(raw: str) -> str:
    """Acepta lo que teclea un humano: '/start abcd-efgh', minúsculas, espacios."""
    tokens = [token for token in raw.split() if not token.startswith("/")]
    candidate = "".join(c for c in "".join(tokens).upper() if c in LINK_CODE_ALPHABET)
    return candidate if len(candidate) == LINK_CODE_LENGTH else ""


# -- render del aviso de aprobación ------------------------------------------


def _amount_line(operation: dict[str, Any]) -> str | None:
    amount = operation.get("amount")
    if not isinstance(amount, dict):
        return None
    try:
        value = Decimal(str(amount.get("value", "")))
    except (InvalidOperation, ValueError):
        return None
    return f"Monto: {amount.get('currency', '')} {value}".strip()


def approval_summary(request: ApprovalRequest, *, agent_name: str) -> str:
    operation = request.operation if isinstance(request.operation, dict) else {}
    model = str(operation.get("model", "?"))
    action = str(operation.get("operation", "?"))
    lines = [
        "ORDO — aprobación pendiente",
        "",
        f"Agente: {agent_name}",
        f"Operación: {model}.{action}",
    ]
    amount = _amount_line(operation)
    if amount:
        lines.append(amount)
    lines.extend(
        [
            f"Solicitud: {request.id}",
            f"Vence: {request.expires_at:%Y-%m-%d %H:%M %Z}",
        ]
    )
    return "\n".join(lines)


async def build_approval_messages(
    session: AsyncSession, approval_id: uuid.UUID
) -> list[OutboundMessage]:
    """Mensaje con botones para cada canal verificado del aprobador legítimo."""
    request = await session.get(ApprovalRequest, approval_id)
    if request is None:
        return []
    agent = await session.get(Agent, request.agent_id)
    if agent is None:
        return []
    channels = await ChannelService(session).verified_channels(
        principal_id=agent.owner_user_id, channel_type=ChannelType.telegram
    )
    if not channels:
        return []
    principal = await session.get(Principal, agent.principal_id)
    text = approval_summary(request, agent_name=principal.display_name if principal else "agente")
    buttons = (
        Button(label="✅ Aprobar", callback_data=sign_callback(request.id, approve=True)),
        Button(label="⛔ Rechazar", callback_data=sign_callback(request.id, approve=False)),
    )
    return [
        OutboundMessage(address=channel.address, text=text, buttons=buttons) for channel in channels
    ]


# -- webhook -----------------------------------------------------------------


def _sub_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Sub-objeto del update, o vacío: el cuerpo lo escribe un tercero."""
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


class TelegramWebhook:
    """Traduce updates de Telegram a operaciones de dominio. No envía nada."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def handle(self, update: dict[str, Any]) -> dict[str, Any]:
        if isinstance(update.get("callback_query"), dict):
            return await self._resolve(update["callback_query"])
        if isinstance(update.get("message"), dict):
            return await self._link(update["message"])
        return {"ok": True, "action": "ignored"}

    async def _link(self, message: dict[str, Any]) -> dict[str, Any]:
        chat = _sub_dict(message, "chat")
        sender = _sub_dict(message, "from")
        chat_id = chat.get("id")
        if chat.get("type") != "private" or chat_id != sender.get("id"):
            # Vincular desde un grupo ataría el canal a una audiencia entera.
            raise LinkCodeInvalidError(
                "La vinculación sólo se hace en el chat privado con el bot.",
                hint="Escribe el código en una conversación directa con el bot.",
            )
        code = normalize_link_code(str(message.get("text") or ""))
        channel = await ChannelService(self.session).redeem_link_code(
            code=code, channel_type=ChannelType.telegram, address=str(chat_id)
        )
        await enqueue_message(
            self.session,
            address=channel.address,
            body="Canal verificado. Aquí recibirás las aprobaciones pendientes de ORDO.",
        )
        await self.session.commit()
        return {"ok": True, "action": "linked", "channel_id": str(channel.id)}

    async def _resolve(self, callback: dict[str, Any]) -> dict[str, Any]:
        approval_id, approve = verify_callback(str(callback.get("data") or ""))
        # Manda quien apretó el botón (`from`), no el chat donde se muestra.
        address = str(_sub_dict(callback, "from").get("id") or "")
        channel = await ChannelService(self.session).verified_principal(
            channel_type=ChannelType.telegram, address=address
        )
        if channel is None:
            raise ChannelNotVerifiedError(
                "Ese chat no está verificado para resolver aprobaciones.",
                hint="Vincula el canal desde POST /iam/v1/channels/telegram/link.",
            )
        # resolve() exige que el aprobador sea el dueño del agente: una firma
        # válida en manos de otro usuario no alcanza.
        request = await ApprovalService(self.session).resolve(
            approval_id,
            approver_id=channel.principal_id,
            approve=approve,
            reason="telegram",
        )
        action = "approved" if approve else "rejected"
        await enqueue_message(
            self.session,
            address=channel.address,
            body=f"Solicitud {request.id} {'aprobada' if approve else 'rechazada'}.",
        )
        await self.session.commit()
        return {"ok": True, "action": action, "approval_id": str(request.id)}


# -- cliente HTTP ------------------------------------------------------------


class TelegramSender:
    """Implementación real de `NotificationSender` contra la API de Telegram."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = TELEGRAM_API_BASE,
        timeout: float = 10.0,
    ) -> None:
        self._token = token
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _payload(self, message: OutboundMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": message.address, "text": message.text}
        if message.buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": button.label, "callback_data": button.callback_data}
                        for button in message.buttons
                    ]
                ]
            }
        return payload

    async def send(self, message: OutboundMessage) -> None:
        # La URL lleva el token: nunca se registra ni se incluye en errores.
        url = f"{self._base_url}/bot{self._token}/sendMessage"
        if self._client is not None:
            await self._post(self._client, url, message)
            return
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            await self._post(client, url, message)

    async def _post(self, client: httpx.AsyncClient, url: str, message: OutboundMessage) -> None:
        try:
            response = await client.post(url, json=self._payload(message))
        except httpx.HTTPError as exc:
            raise TelegramDeliveryError(
                f"No se pudo entregar el aviso de Telegram: {type(exc).__name__}."
            ) from exc
        if response.status_code >= 400:
            raise TelegramDeliveryError(
                f"Telegram respondió {response.status_code} al enviar el aviso."
            )


def api_base() -> str:
    """Dónde vive la API de Telegram.

    Configurable a propósito. Sin esto **no hay forma de probar el canal de
    punta a punta sin salir a Internet**: `TelegramSender` ya aceptaba la URL
    por constructor, pero el worker la fijaba dura y solo los tests unitarios
    podían inyectarla. Un emisor apuntado a un receptor local recibe exactamente
    el mismo mensaje que recibiría Telegram —el mismo texto, los mismos botones
    y la misma firma— y eso es justo lo que hace falta para verificar el flujo
    completo antes de que exista un bot de verdad.
    """
    return os.environ.get("TELEGRAM_API_BASE", TELEGRAM_API_BASE).rstrip("/")


def sender_from_env() -> NotificationSender:
    """Sender real para el worker; los tests inyectan el de memoria."""
    return TelegramSender(bot_token(), base_url=api_base())
