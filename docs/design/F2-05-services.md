
# F2-05 — Secuencias, jobs, cron y outbox (diseño)

Servicios transversales del kernel (PLAN §3.5). Todos por tenant.

## Secuencias (`ir_sequence`)

- `prefix`, `padding`, `next_number`, `implementation` ∈ {standard, no_gap}.
- **`no_gap` es obligatorio para documentos legales**: toma `SELECT ... FOR UPDATE`
  sobre la fila de la secuencia, así que dos transacciones concurrentes se serializan
  y no hay huecos. Nunca se cachean rangos.
- `standard` permite huecos (más concurrencia) para documentos internos.
- Formato: `{prefix}{numero:0{padding}d}`; opcionalmente por período (año/mes).

## Cola de jobs (`ir_job`, ADR-007)

Encolar es parte de la transacción de negocio: si el commit falla, el job no existe.

- Columnas: `id, tenant, name, payload, state, priority, run_at, attempts,
  max_attempts, last_error, locked_by, locked_at, create_date`.
- `state` ∈ {pending, running, done, failed, dead}.
- Toma: `SELECT ... WHERE state='pending' AND run_at <= now() ORDER BY priority,
  run_at FOR UPDATE SKIP LOCKED LIMIT n` — dos workers nunca toman el mismo job.
- Reintentos con backoff exponencial (`2^attempts` minutos, tope 1h). Agotados
  los intentos ⇒ `dead` (DLQ), nunca se pierde ni se reintenta infinito.

## Cron (`ir_cron`)

Tarea con `interval_seconds` y `next_call`. El worker toma la fila con
`FOR UPDATE SKIP LOCKED` y avanza `next_call` **antes** de ejecutar: si el proceso
muere a mitad, la tarea no se repite en bucle. Lock distribuido = la fila misma.

## Outbox (`ir_outbox`, ADR-008)

- Se escribe en la **misma transacción** que el cambio de negocio (sin dual-write).
- Relay: lee pendientes por lote, publica en NATS con `Nats-Msg-Id = id del outbox`
  (deduplicación en el broker), marca `published_at`.
- Semántica al menos una vez; los consumidores deduplican por `event_id`.
- El relay es idempotente: si muere tras publicar y antes de marcar, el mensaje
  se republica con el mismo id y JetStream lo descarta.

## Tests (primero)

Secuencias: formato con padding; incremento; `no_gap` sin huecos bajo concurrencia
real (dos sesiones simultáneas); `standard` funciona.
Jobs: encolar y tomar; `SKIP LOCKED` impide doble toma; reintento con backoff;
agotados ⇒ dead; job encolado en transacción abortada no existe.
Cron: due se ejecuta, no-due no; `next_call` avanza antes de ejecutar.
Outbox: se escribe con el negocio; rollback no deja evento; relay marca publicados;
relay dos veces no republica lo ya marcado.
