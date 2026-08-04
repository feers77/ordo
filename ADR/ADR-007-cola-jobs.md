# ADR-007 — Cola de jobs en Postgres

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Los jobs (envío de DTE, recomputaciones, imports) deben encolarse **en la misma transacción** que el cambio de negocio que los origina: si el commit falla, el job no debe existir.

## Opciones consideradas

1. **Tabla en Postgres + `SKIP LOCKED` + workers propios** — transaccionalidad exacta; throughput limitado por la DB.
2. **Celery/ARQ sobre Redis** — ecosistema maduro; encolado no transaccional (dual-write) y semántica at-most/at-least confusa.
3. **NATS JetStream como cola de trabajo** — ya está en el stack; mismo problema de dual-write con la transacción de negocio.

## Decisión

Cola en Postgres: tabla de jobs por tenant, `FOR UPDATE SKIP LOCKED`, reintentos con backoff exponencial, DLQ, prioridades, cron con lock distribuido. Workers propios en `ordo-jobs`. NATS queda **solo** para eventos (ADR-008), no para trabajo transaccional.

## Consecuencias

- Positivas: job y negocio commitean o fallan juntos; cero infraestructura extra; introspección SQL de la cola.
- Negativas: techo de throughput (~miles de jobs/s); vacuum/bloat a vigilar.
- Invalidaría: throughput sostenido que degrade la DB de negocio (particionar o mover a broker con outbox).
