# F2-01 — Registry de modelos, sistema de campos y Environment (diseño)

Kernel en `packages/ordo-core`. Sin dependencias de servicios: es librería.

## Declaración de modelos

```python
class SaleOrder(Model):
    _name = "sale.order"
    _description = "Orden de venta"
    _table = "sale_order"          # derivado de _name si se omite

    name = Char(required=True, agent_hint="Número del documento", examples=["SO0001"])
    partner_id = Many2one("res.partner", required=True, agent_hint="Cliente")
    state = Selection([("draft","Borrador"),("sale","Confirmada")], default="draft",
                      agent_hint="Estado del ciclo de vida")
```

- `agent_hint` y `examples` **obligatorios** en campos de negocio (CLAUDE.md §4);
  el registry falla al construirse si faltan. Campos técnicos del kernel
  (`id`, `create_uid`, `write_date`, `version`, `company_id`, `x_custom`) exentos.
- Tipos F2.1: `Char, Text, Html, Integer, Float, Monetary, Boolean, Date, Datetime,
  Binary, Selection, Json, Many2one, One2many, Many2many`.
  (`Reference` y campos calculados llegan en F2.3.)
- Atributos: `required, readonly, index, default, store, related, groups,
  company_dependent, translate, tracking, agent_hint, examples`.

## Herencia

- **Extensión** `_inherit = "sale.order"`: agrega campos/métodos al modelo existente.
  El merge respeta el orden topológico de módulos; el último gana en conflicto de
  atributos, pero **no puede** cambiar el tipo de un campo existente (error explícito).
- **Delegación** `_inherits = {"product.template": "product_tmpl_id"}`: los campos del
  padre se exponen como si fueran propios; escritura delegada al registro padre.
- Ciclos en el grafo de módulos ⇒ `KernelError("REGISTRY_DEPENDENCY_CYCLE")`.

## Registry

`Registry.build(modules)` → resuelve dependencias, aplica merges, valida y congela.
Introspección: `registry["sale.order"].fields`, `.inherits`, `.table`.
Metadatos persistidos por tenant en `ir_model` / `ir_model_field` (para `studio-api`
y el schema semántico); la persistencia es idempotente (upsert por nombre).

## Environment y multi-tenancy

```python
env = Environment(session, tenant="acme", user_id=..., agent_id=None,
                  companies=[1,3], lang="es_CL", tz="America/Santiago", context={})
```
- Schema-per-tenant (ADR-002): el `Environment` fija `SET LOCAL search_path` al schema
  del tenant en cada transacción y `SET LOCAL ordo.tenant` para las políticas RLS.
- El código de dominio **nunca** escribe el nombre del schema ni filtra por tenant:
  eso vive aquí. Cualquier query que no pase por `Environment` se rechaza en revisión
  (CLAUDE.md §7).
- `env.companies` alimenta las record rules de multi-company (dimensión distinta).

## Tests (primero)

Registro y congelado; campo sin `agent_hint` falla; `_inherit` agrega campo;
`_inherit` no puede cambiar tipo; `_inherits` expone campos del padre; ciclo detectado;
orden topológico correcto; `ir_model` reflejado e idempotente; `Environment` fija
search_path y variable de tenant; dos tenants no se ven (RLS + schema).
