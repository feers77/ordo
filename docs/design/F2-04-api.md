# F2-04 — ORM de escritura y API genérica (diseño)

Dos capas: `RecordSet` (kernel, sin HTTP) y los endpoints de `ordo-api`.

## RecordSet (kernel)

```python
rs = RecordSet(env, "sale.order")
ids   = await rs.create([{...}, {...}])          # lote siempre
rows  = await rs.read(ids, fields=["name"])
await rs.write(ids, {"state": "sale"}, expected_version=3)
await rs.unlink(ids)
rows  = await rs.search(domain, fields=[...], limit=80, cursor=...)
```

- Toda operación es **por lote**: la API de un registro es un lote de uno.
- `create`/`write` completan `create_uid/create_date/write_uid/write_date` y
  suben `version`.
- **Bloqueo optimista**: `write` con `expected_version` distinto de la fila ⇒
  `CONCURRENT_MODIFICATION` (409) incluyendo el estado actual del registro para que
  el agente reconcilie (PLAN §3.4).
- Validaciones antes de tocar la base: campos desconocidos, `required` faltante,
  `readonly` que se intenta escribir, valor fuera de una `Selection`, tipo incorrecto,
  Monetary que llega como float.
- Toda lectura pasa por el compilador de dominios (record rules y tenant incluidos).

## Paginación por cursor

`search` devuelve `{rows, next_cursor}`. El cursor es opaco (base64 de `id`), nunca
un offset: colecciones grandes no se degradan (§4.2).

## Endpoints

```
GET    /api/v1/{model}                 search_read (domain, fields, limit, cursor, order)
POST   /api/v1/{model}                 create
GET    /api/v1/{model}/{id}            read
PATCH  /api/v1/{model}/{id}            write
DELETE /api/v1/{model}/{id}            unlink
POST   /api/v1/{model}/batch           create/write/unlink masivo
POST   /api/v1/tx                      transacción multi-operación (atomic true|false)
```

## Dry-run universal

`?dry_run=true` en cualquier escritura: ejecuta dentro de un savepoint, arma la
respuesta que devolvería la operación real más `validations[]` con lo que fallaría,
y **hace rollback siempre**. Nunca escribe, ni siquiera parcialmente.

## Idempotencia

`Idempotency-Key` obligatorio en toda escritura (AGENTS.md §6). La respuesta se
guarda en `ir_idempotency` (clave, hash del request, respuesta, expiración 24h):
- misma clave + mismo request ⇒ se devuelve la respuesta guardada (no se re-ejecuta);
- misma clave + request distinto ⇒ `IDEMPOTENCY_KEY_REUSED` (409).

## Transacción multi-operación

`POST /api/v1/tx` con `{"atomic": true, "operations": [...]}`:
- `atomic: true` ⇒ todo o nada;
- `atomic: false` ⇒ savepoint por operación y reporte parcial por índice.

## Tests (primero)

CRUD por lote; version sube; bloqueo optimista devuelve estado actual; validaciones
(required, readonly, selection, tipo, float en Monetary, campo desconocido);
paginación por cursor estable; dry-run no escribe ni con éxito ni con error;
idempotencia devuelve la misma respuesta y detecta reuso con payload distinto;
tx atómica revierte todo ante fallo; tx no atómica reporta por índice.
