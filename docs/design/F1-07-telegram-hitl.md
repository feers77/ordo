# F1-07 — Telegram como canal HITL (diseño)

Primer canal humano para el flujo de aprobaciones de F1-06. El aprobador
recibe la solicitud donde ya está y la resuelve con dos botones; el estado
resultante es idéntico al de `/approvals/{id}/approve`.

## Tablas (migración 0005)

`iam_notification_channel`: `id`, `tenant`, `principal_id`, `channel_type`
(`telegram`), `address` (chat_id), `verified_at`, `active`. Índice único
parcial `(channel_type, address) WHERE active`: una dirección activa pertenece
a un solo principal.

`iam_channel_link_code`: `id`, `tenant`, `principal_id`, `channel_type`,
`code_hash` (sha256, único), `expires_at`, `used_at`. Sólo el hash: leer la
tabla no permite vincular un chat ajeno.

`ir_job`: misma forma que la tabla del kernel (ADR-007). La base de IAM es
independiente y `ordo-iam` no depende de `ordo-core`.

## Flujo

```
POST /iam/v1/channels/telegram/link   (autenticado)  → 201 {code, expires_at}
  ▶ el usuario envía el código al bot en chat privado
  ▶ webhook: canje atómico (UPDATE ... WHERE used_at IS NULL AND expires_at > now)
       ⇒ canal verificado. Un solo uso, 10 minutos.
POST /iam/v1/approvals (F1-06)
  ▶ encola iam.notify_approval en la misma transacción (el request no sale a la red)
  ▶ worker: mensaje con resumen + botones; callback_data = a1:<uuid hex>:<a|r>:<hmac20>
POST /iam/v1/telegram/webhook
  ▶ X-Telegram-Bot-Api-Secret-Token válido, si no 403 y no se lee el cuerpo
  ▶ HMAC del callback válido (clave derivada del secreto, no el secreto crudo)
  ▶ chat verificado y activo ⇒ ApprovalService.resolve(approver=dueño del canal)
       resolve() sigue exigiendo que el aprobador sea el dueño del agente:
       una firma válida en manos de otro usuario no aprueba nada.
```

`NotificationSender.send` es la única salida a la red: en tests se inyecta la
implementación en memoria. Configuración: `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_WEBHOOK_SECRET` (nunca en git).

## Errores nuevos

`IAM_LINK_CODE_INVALID` (400, mismo código para inexistente/vencido/usado),
`IAM_CHANNEL_ALREADY_LINKED` (409), `IAM_CHANNEL_NOT_VERIFIED` (403),
`IAM_CALLBACK_INVALID` (403), `IAM_WEBHOOK_UNAUTHORIZED` (403),
`IAM_TELEGRAM_NOT_CONFIGURED` (503), `IAM_TELEGRAM_DELIVERY_FAILED` (502).

## Fuera de alcance

Desvinculación por API, edición del mensaje tras resolver, `answerCallbackQuery`,
recordatorios antes de expirar y canales que no sean Telegram.
