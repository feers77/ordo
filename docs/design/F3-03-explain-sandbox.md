# F3-03 — Explicación, catálogo de acciones y sandbox (diseño)

Tres piezas que responden a las tres preguntas que un agente se hace antes
de actuar: *¿de dónde salió este valor?*, *¿qué puedo hacer?* y *¿qué pasa
si me equivoco?*.

## 1. `GET /api/v1/{model}/{id}/explain`

Devuelve, para un registro concreto:

- `fields`: por cada campo almacenado o calculado, su valor y su
  **procedencia** (`origin`): `stored` (lo escribió alguien), `computed`
  (con `compute` y las rutas de `@depends` que lo disparan), `related`
  (con la ruta), `default` (valor por defecto declarado, el registro nunca
  lo escribió). Incluye `agent_hint` para que la explicación se lea sin
  consultar el schema aparte.
- `actions`: las acciones del modelo separadas en `available` y `blocked`,
  cada una con `requires_approval`. "Bloqueada" se determina ejecutando la
  acción en `dry_run` y viendo si falla con un código estable; el resultado
  incluye ese código y su `hint`, que es exactamente lo que el agente
  necesita para decidir su siguiente paso.
- `history`: los últimos cambios registrados por el chatter (campo, valor
  anterior, nuevo, autor, fecha), si el modelo tiene tracking.

El explain **no escribe nada**: las simulaciones corren en savepoint y se
revierten, igual que cualquier `dry_run`.

## 2. `GET /meta/v1/actions`

Catálogo global: todas las acciones registradas (modelo, nombre, resumen,
`requires_approval`, parámetros) y todos los reportes. Es el índice que
hoy hay que descubrir modelo por modelo. Filtro opcional `?models=a,b`.

## 3. Sandbox: `POST /api/v1/sandbox` y `DELETE /api/v1/sandbox/{tenant}`

Clona el schema del tenant actual —estructura y datos— en un tenant
efímero `<tenant>_sb_<hex>` y devuelve su nombre y su vencimiento. El
agente ensaya ahí lo destructivo apuntando `X-Ordo-Tenant` al sandbox;
borrarlo es un `DROP SCHEMA CASCADE` y no toca nada real.

**Privilegios**: clonar es DDL y el rol de la aplicación no tiene DDL a
propósito (AGENTS §7). El sandbox usa una conexión aparte declarada en
`ORDO_ADMIN_DATABASE_URL`; sin esa variable el endpoint responde 503
`SANDBOX_UNAVAILABLE` en vez de degradar los privilegios del rol normal.
Un sandbox no puede clonar otro sandbox, y solo se borran schemas cuyo
nombre lleva el sufijo `_sb_`: un bug de este código no puede borrar
producción.

Los sandboxes caducan (`SANDBOX_TTL_HOURS`, default 24) y el worker de
`ordo-events` los recoge; el registro vive en la tabla `ir_sandbox` del
schema público.

## Qué NO entra

- Búsqueda semántica (pgvector + embeddings) y NL→dominio (modelo de
  lenguaje): ambas exigen dependencias y ADR propio.
- Copiar el estado de IAM al sandbox: el tenant efímero se opera con el
  mismo token; las ACL del tenant original aplican por rol, no por schema.
