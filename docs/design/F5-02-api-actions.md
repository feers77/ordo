# F5-02 — Acciones de negocio por la API (diseño)

Las transiciones de estado son métodos explícitos (AGENTS.md §4), pero hasta
ahora vivían solo como servicios de Python: un agente no podía confirmar una
orden por HTTP. Esta pieza las vuelve operaciones de primera clase con el
mismo contrato que cualquier escritura.

## Registro

`ordo_core.actions` define el decorador:

```python
@action("sale.order", "action_confirm",
        summary="Confirma la orden: fija totales y asigna número",
        requires_approval=False, params={})
async def confirm(env, record_id, params) -> dict: ...
```

Cada módulo declara las suyas en `actions.py`, que el `ModuleLoader` importa
junto a `models.py`. `requires_approval` es metadato para el PDP: el `cap`
de un agente ya soporta listas como `["account.move.action_post"]`, y ahora
la API declara qué operaciones caen en esa categoría en vez de dejarlo a la
memoria de quien redacta el grant.

## Endpoints

- `GET /api/v1/{model}/actions` — descubrimiento: nombre, resumen,
  `requires_approval` y parámetros de cada acción.
- `POST /api/v1/{model}/{id}/actions/{action}` — ejecución. Cuerpo
  `{"params": {...}}`. `Idempotency-Key` obligatorio; `?dry_run=true`
  ejecuta de verdad dentro de un savepoint y lo revierte todo — incluida la
  secuencia sin huecos: una confirmación simulada no quema número legal.

Cada ejecución real emite un evento `{model}.{action}` al outbox **en la
misma transacción**: si el commit falla, el evento nunca existió (ADR-008).

## Acciones expuestas

| Modelo | Acción | Aprobación |
|---|---|---|
| `account.move` | `action_post`, `action_reverse` | sí |
| `account.move` | `action_cancel` | no |
| `sale.order` | `action_confirm`, `action_cancel` | no |
| `sale.order` | `action_invoice`, `action_einvoice` | sí |
| `purchase.order` | `action_confirm`, `action_cancel` | no |
| `purchase.order` | `action_bill` | sí |
| `edi.document` | `action_contingency` | no |
| `edi.document` | `action_cancel` | sí |

`action_einvoice` usa el puente `modules/einvoicing/bridge.py`: valida que
la orden esté confirmada, que emisor y receptor tengan identificador
tributario, resuelve los impuestos y produce el `InvoiceData` neutro que el
adaptador del país convierte en XML con folio asignado.

Firmar, enviar y consultar acuse **no** se exponen todavía: dependen de la
clave en el vault y del transporte contra el ambiente de certificación.

## Carga de módulos en el servicio

`ordo-api` deja de arrancar con registry vacío: `ORDO_MODULES_PATH`
(por defecto `modules/`) se carga una vez por proceso con el `ModuleLoader`.

## Errores

`ACTION_UNKNOWN` (404, lista las acciones disponibles en el hint),
`EDI_SOURCE_NOT_FOUND`, `EDI_SOURCE_NOT_READY`, `EDI_MISSING_TAX_ID`,
`EDI_MISSING_COUNTRY`, `EDI_DOCUMENT_TYPE_REQUIRED`.
