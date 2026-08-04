# F3-02 — Webhooks: el outbox llega a quien se suscribió (diseño)

Un agente que hace polling es un agente que llega tarde. El outbox ya
registra cada evento de negocio en la misma transacción que lo produce
(ADR-008); esta pieza lo entrega por HTTP firmado a quien se suscribió.

## Modelos (módulo `webhook`, depende de `base`)

- `webhook.subscription` — `name`, `url`, `event_pattern` (fnmatch sobre el
  `event_type`, ej. `sale.order.*` o `*`), `secret` (lo genera el sistema al
  crear, HMAC de cada entrega), `state` ∈ {active, suspended},
  `failure_count`, `last_delivery_at`. Diez fallos consecutivos suspenden la
  suscripción (`WEBHOOK_SUSPENDED` en el chatter del futuro); un éxito
  resetea el contador.
- `webhook.delivery` — bitácora por intento: `subscription_id`, `event_id`
  (id del outbox), `event_type`, `status` ∈ {pending, delivered, failed},
  `attempts`, `response_status`, `error`, `delivered_at`. Sirve de
  watermark: un evento se entrega una vez por suscripción, aunque el worker
  se caiga a mitad de lote.

## Entrega

`WebhookService.dispatch_pending(env, transport, limit)`:

1. Lee eventos del outbox con `id >` que el último entregado por
   suscripción (join contra `webhook.delivery`).
2. Para cada (evento, suscripción activa cuyo patrón calce): crea la
   entrega `pending`, hace `POST url` con el cuerpo JSON
   `{event_id, event_type, subject, payload, emitted_for: tenant}` y las
   cabeceras `X-Ordo-Event`, `X-Ordo-Delivery` y `X-Ordo-Signature:
   sha256=<hmac-sha256(cuerpo, secret)>`.
3. 2xx → `delivered`; otra cosa → `failed` con el detalle, `attempts += 1`
   y `failure_count += 1` en la suscripción (a 10, `suspended`).
4. `retry_failed(env, transport)` reintenta las fallidas con menos de 5
   intentos; la re-entrega lleva el MISMO `X-Ordo-Delivery`, para que el
   receptor deduplique.

El transporte es un Protocol (`async send(url, body, headers) -> int`);
HTTP real con httpx en el worker, stub en tests. Firmar con HMAC-SHA256 es
deliberado: el receptor verifica origen e integridad sin PKI.

**El secreto se guarda en la base**, como hacen GitHub y Stripe con los
suyos: es un secreto de integridad por suscripción, no material de firma
legal (AGENTS §7 aplica a certificados). Se genera server-side y se muestra
completo solo al crear.

## Worker (`ordo-events`)

El servicio deja de ser esqueleto: un loop (mismo patrón del worker IAM)
que cada N segundos descubre los schemas `t_*`, arma un `Environment` por
tenant y corre `dispatch_pending` + `retry_failed`. `ORDO_EVENTS_INTERVAL`
(default 5s), fail-soft por tenant: un tenant roto no frena a los demás.

## Acciones y seguridad

`webhook.subscription`: `action_suspend`, `action_resume` (resetea
contador). ACL: rol nuevo `integraciones` (rwc de suscripciones, r de
entregas); `auditor` lee todo; nadie borra entregas.

## Qué NO entra

- Entrega por NATS al broker (el relay existente sigue disponible).
- Reordenamiento o garantía de orden entre suscripciones distintas.
- Transformaciones/filtros por payload: el patrón es sobre `event_type`.
