"""Canales de notificación y vinculación verificada del aprobador (F1-07).

Cualquiera puede escribirle a un bot: el `chat_id` por sí solo no prueba nada.
El vínculo chat ↔ principal se establece siempre desde dentro del sistema, con
un código de un solo uso y vida corta que emite un endpoint autenticado y que
el usuario entrega por el canal. Sin canje verificado no se recibe ni se
resuelve nada.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.audit import append_audit
from ordo_iam.errors import ChannelAlreadyLinkedError, LinkCodeInvalidError
from ordo_iam.models import ChannelLinkCode, ChannelType, NotificationChannel

LINK_CODE_TTL = timedelta(minutes=10)
LINK_CODE_LENGTH = 10
#: Sin caracteres ambiguos (I, O, 0, 1): el código se dicta y se teclea a mano.
LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_link_code() -> str:
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


def hash_link_code(code: str) -> str:
    """Sólo se guarda el hash: leer la tabla no permite vincular un chat ajeno."""
    return hashlib.sha256(code.encode()).hexdigest()


class ChannelService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue_link_code(
        self,
        *,
        tenant: str,
        principal_id: uuid.UUID,
        channel_type: ChannelType,
    ) -> tuple[str, ChannelLinkCode]:
        """Emite un código nuevo; el valor en claro se devuelve una sola vez."""
        code = generate_link_code()
        row = ChannelLinkCode(
            tenant=tenant,
            principal_id=principal_id,
            channel_type=channel_type,
            code_hash=hash_link_code(code),
            expires_at=datetime.now(UTC) + LINK_CODE_TTL,
        )
        self.session.add(row)
        await self.session.commit()
        await append_audit(
            self.session,
            tenant=tenant,
            event_type="channel_link_code_issued",
            payload={"channel_type": channel_type.value, "link_code_id": str(row.id)},
            principal_id=principal_id,
        )
        return code, row

    async def redeem_link_code(
        self,
        *,
        code: str,
        channel_type: ChannelType,
        address: str,
    ) -> NotificationChannel:
        """Canjea el código y deja el canal verificado. Un solo uso, atómico."""
        if not code:
            raise LinkCodeInvalidError(
                "Código de vinculación inválido, vencido o ya usado.",
                hint="Pide uno nuevo en POST /iam/v1/channels/telegram/link.",
            )
        now = datetime.now(UTC)
        claimed = (
            await self.session.execute(
                update(ChannelLinkCode)
                .where(
                    ChannelLinkCode.code_hash == hash_link_code(code),
                    ChannelLinkCode.channel_type == channel_type,
                    ChannelLinkCode.used_at.is_(None),
                    ChannelLinkCode.expires_at > now,
                )
                .values(used_at=now)
                .returning(
                    ChannelLinkCode.id,
                    ChannelLinkCode.tenant,
                    ChannelLinkCode.principal_id,
                )
            )
        ).first()
        if claimed is None:
            await self.session.rollback()
            raise LinkCodeInvalidError(
                "Código de vinculación inválido, vencido o ya usado.",
                hint="Pide uno nuevo en POST /iam/v1/channels/telegram/link.",
            )

        existing = await self.session.scalar(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == channel_type,
                NotificationChannel.address == address,
                NotificationChannel.active.is_(True),
            )
        )
        if existing is not None and existing.principal_id != claimed.principal_id:
            # El código queda quemado igualmente: fallar cerrado y sin reintento.
            await self.session.commit()
            raise ChannelAlreadyLinkedError(
                "Esa dirección ya está vinculada a otro principal.",
                hint="Desvincula el canal desde la cuenta que lo tiene antes de reasignarlo.",
            )

        if existing is not None:
            existing.verified_at = now
            channel = existing
        else:
            channel = NotificationChannel(
                tenant=claimed.tenant,
                principal_id=claimed.principal_id,
                channel_type=channel_type,
                address=address,
                verified_at=now,
                active=True,
            )
            self.session.add(channel)
        await self.session.commit()
        await append_audit(
            self.session,
            tenant=claimed.tenant,
            event_type="channel_verified",
            payload={
                "channel_type": channel_type.value,
                "channel_id": str(channel.id),
                "link_code_id": str(claimed.id),
            },
            principal_id=claimed.principal_id,
        )
        return channel

    async def verified_channels(
        self, *, principal_id: uuid.UUID, channel_type: ChannelType
    ) -> list[NotificationChannel]:
        rows = await self.session.scalars(
            select(NotificationChannel).where(
                NotificationChannel.principal_id == principal_id,
                NotificationChannel.channel_type == channel_type,
                NotificationChannel.active.is_(True),
                NotificationChannel.verified_at.is_not(None),
            )
        )
        return list(rows)

    async def verified_principal(
        self, *, channel_type: ChannelType, address: str
    ) -> NotificationChannel | None:
        """Canal verificado y activo dueño de la dirección, o None."""
        channel: NotificationChannel | None = await self.session.scalar(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == channel_type,
                NotificationChannel.address == address,
                NotificationChannel.active.is_(True),
                NotificationChannel.verified_at.is_not(None),
            )
        )
        return channel
