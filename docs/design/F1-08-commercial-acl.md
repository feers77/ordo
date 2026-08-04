# F1-08 — Roles y ACL de los módulos comerciales (diseño)

El PDP (F1-05) siempre negó por defecto, pero ningún modelo de negocio tenía
grants: un tenant recién creado era inoperable con rol y todopoderoso sin
PDP delante. Esta pieza declara la política por defecto y la hace cargable.

## Declaración

Cada módulo trae `security.yaml`: roles → modelo → permisos
(`read/write/create/unlink`). Los fragmentos se combinan por rol:
`ventas` junta lo que le dan `base` (partners), `sale` (sus órdenes),
`account` (leer impuestos y asientos) y `einvoicing` (leer documentos).

Roles por defecto: `ventas`, `compras`, `contabilidad`, `tesoreria`,
`facturacion`, `auditor` (solo lectura transversal).

## Invariantes probadas

- **Cobertura consciente**: todo modelo del registry aparece en al menos un
  `security.yaml`; agregar un modelo sin decidir quién lo toca rompe un test.
- **Sin unlink de historia**: nadie —ni contabilidad— puede borrar
  `account.move`, `account.move.line` ni `edi.document`.
- **Acciones = write**: el PDP mapea operaciones no-CRUD a `write` del
  modelo; `requires_approval` del cap agrega el HITL por encima.

## Carga

`ordo_core.security.load_security_specs()` parsea y valida;
`tools/seed_iam_roles.py <tenant>` upserta roles y ACLs en la base IAM
(idempotente). La membresía —qué persona tiene qué rol— es del tenant y
nunca se toca desde el seed.

## Qué NO entra aquí

- Record rules por defecto (dominios por rol): cuando exista un caso real
  (p. ej. vendedor solo ve sus órdenes), no antes.
- Enforcement en ordo-api/ordo-mcp: sigue siendo del gateway+PDP; estos
  servicios no reimplementan autorización.
