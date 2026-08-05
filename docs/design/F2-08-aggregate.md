# F2-08 — Agregaciones (`read_group`) (diseño)

Un agente que quiere "las ventas de agosto por cliente" hoy tiene que
traerse las órdenes y sumarlas en su lado: caro, lento y propenso a que el
total dependa de cuántas páginas alcanzó a leer. El plan declara
`POST /api/v1/{model}/aggregate` en la superficie pública; esta pieza lo
implementa.

## Kernel

```python
await RecordSet(env, "sale.order").read_group(
    domain=[("date_order", ">=", "2026-08-01")],
    group_by=["partner_id", "state"],
    aggregates=["count", "sum:amount_total", "avg:amount_total"],
    order="sum:amount_total desc",
    limit=50,
)
```

Devuelve `{"groups": [{"partner_id": 7, "state": "invoiced", "count": 12,
"sum:amount_total": "1428000.00", ...}], "total_groups": n}`.

- El dominio se compila con el compilador existente (parámetros vinculados,
  record rules y `active_test` incluidos): agregar **no** puede saltarse el
  filtro de tenant ni las reglas de registro.
- `group_by` admite campos almacenados del modelo, no rutas punteadas: un
  join implícito en una agregación cambia los totales de formas que nadie
  espera. Un campo desconocido es `FIELD_UNKNOWN`.
- `aggregates`: `count`, `sum:<campo>`, `avg:<campo>`, `min:<campo>`,
  `max:<campo>`, sobre campos numéricos o monetarios (`sum` de un texto es
  `AGGREGATE_INVALID_FIELD`). Los importes vuelven como **string decimal**,
  como todo el dinero de la API.
- Sin `group_by` devuelve un único grupo con los totales de todo el dominio.
- `limit` tope 500; el orden acepta un agregado (`sum:amount_total desc`) o
  un campo del `group_by`.

## API y MCP

`POST /api/v1/{model}/aggregate` con `{domain, group_by, aggregates, order,
limit}`. Es de solo lectura: el enforcement lo evalúa como `read` del
modelo. La tool MCP `ordo_aggregate` expone lo mismo.

## Qué NO entra

- `having` (filtrar por el agregado) y ventanas temporales automáticas
  (`date:month`): cuando haya un caso real que lo pida.
- Agregar sobre relaciones punteadas.
