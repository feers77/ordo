# ADR-008 — Bus de eventos y patrón outbox

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Agentes y sistemas externos se suscriben a cambios de negocio (webhooks, SSE, NATS). Publicar directo al broker desde la transacción crea dual-write: evento sin commit o commit sin evento.

## Opciones consideradas

1. **Outbox transaccional → relay → JetStream** — entrega garantizada al menos una vez; latencia del relay.
2. **Publicación directa a NATS en el request** — simple; eventos fantasma o perdidos ante fallos.
3. **CDC (Debezium sobre WAL)** — sin código de aplicación; pieza operativa pesada (Kafka Connect) para esta escala.

## Decisión

Tabla `outbox` escrita en la misma transacción del cambio de negocio. Relay (`ordo-events`) lee por lotes, publica a NATS JetStream con `Nats-Msg-Id` = id del outbox (deduplicación), marca publicado. Consumidores: webhooks con reintentos+backoff y firma HMAC, streams SSE/NATS filtrados por dominio. Semántica: **al menos una vez**; los consumidores deduplican por `event_id`. Replay disponible desde JetStream.

## Consecuencias

- Positivas: nunca evento sin commit ni commit sin evento; replay barato.
- Negativas: latencia extra (relay poll/notify); consumidores deben ser idempotentes.
- Invalidaría: necesidad de orden global estricto multi-stream (JetStream ordena por stream).
