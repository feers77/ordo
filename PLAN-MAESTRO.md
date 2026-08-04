# ORDO ERP — Plan Maestro

> ERP/CRM completo, **API-first, sin frontend**, diseñado para ser operado por agentes de IA.
> Paridad funcional con Odoo Community + reimplementación propia de las funciones Enterprise + framework de localizaciones.
>
> `ORDO` es un nombre placeholder. Cámbialo antes de la Fase 0 (afecta namespaces de paquetes, prefijos de tablas y `iss` de los tokens).

---

## 0. Decisiones estratégicas (léelas antes de escribir una línea de código)

### 0.1 Qué estamos construyendo realmente

No es "Odoo sin frontend". Odoo sin frontend es una API CRUD sobre un ORM. Lo que diferencia este producto es que **el consumidor primario es un agente de IA, no un humano con pantalla**. Eso cambia requisitos de fondo:

| Requisito | ERP tradicional | ERP agéntico |
|---|---|---|
| Descubrimiento | Documentación + capacitación | Introspección en runtime: el agente pregunta al sistema qué puede hacer |
| Errores | Mensaje para humano | Código de error estable + causa estructurada + acción sugerida |
| Reintentos | Usuario reintenta | Idempotencia obligatoria en toda escritura |
| Validación | Formulario con onchange | `dry-run` / `prepare` sobre cualquier operación |
| Permisos | Rol del usuario | Capacidad delegada con límites (monto máx., modelos, ventana temporal) |
| Auditoría | Quién | Quién + qué agente + con qué prompt/trace + bajo qué delegación |
| Operaciones masivas | Import CSV | Batch transaccional nativo con reporte parcial |
| Errores del actor | Poco frecuentes | Frecuentes y creativos → el sistema debe ser hostil a estados inválidos |

**Regla de oro del proyecto:** si un agente puede dejar la contabilidad en un estado inconsistente, el bug es del sistema, no del agente.

### 0.2 Stack recomendado

| Capa | Elección | Por qué |
|---|---|---|
| Lenguaje | Python 3.12 | Semántica más cercana a Odoo (facilita portar lógica de localizaciones), ecosistema contable/fiscal maduro, y es donde Claude Code produce mejor código de dominio |
| Framework HTTP | FastAPI + Uvicorn (workers) | ASGI, OpenAPI 3.1 nativo, Pydantic v2 |
| ORM base | SQLAlchemy 2.0 async (Core + ORM) | Necesitamos el Core para generar SQL desde nuestro lenguaje de dominios |
| Validación/serialización | Pydantic v2 | Generación dinámica de schemas desde el registry |
| DB | PostgreSQL 17 | `pgvector`, `pg_trgm`, `pg_partman`, RLS, `SKIP LOCKED`, JSONB, `GENERATED` columns |
| Cache / locks / rate limit | Redis 7 | |
| Bus de eventos | NATS JetStream | Outbox → relay. Webhooks y suscripciones de agentes |
| Jobs | Cola en Postgres (`SKIP LOCKED`) + workers propios | Transaccionalidad con el negocio; Celery/ARQ no dan eso |
| Object storage | MinIO (S3-compatible) | Adjuntos, PDFs, XML de facturación electrónica |
| Búsqueda | Postgres FTS + pgvector (fase 1) → Meilisearch/OpenSearch si hace falta | No sobre-ingenierizar |
| Migraciones | Alembic + migraciones de datos versionadas por módulo | |
| Observabilidad | OpenTelemetry → Prometheus + Grafana + Loki + Tempo | Trazas correlacionadas con `trace_id` del agente |
| Renderizado PDF | Typst o WeasyPrint (servicio aparte) | Reportes legales |
| Contenedores | Docker + Compose (dev/prod single-node) → k8s cuando duela | |

**Alternativa si la latencia se vuelve crítica:** extraer los hot paths (motor de dominios→SQL, cálculo de impuestos, disponibilidad de stock) a un servicio en Go o Rust. **No lo hagas en Fase 1.** Mide primero.

### 0.3 Multi-tenancy

Modelo por defecto: **schema-per-tenant** en Postgres, con enrutamiento en el gateway.

- Tenants pequeños/medianos: N schemas por cluster, pool compartido.
- Tenants grandes: base de datos dedicada (misma abstracción, distinto DSN).
- Defensa en profundidad: RLS de Postgres además del filtro de aplicación.
- **Multi-company dentro del tenant** es una dimensión distinta (como Odoo): `company_id` + `allowed_company_ids` en el contexto.

Ambas capas deben ser transparentes para el código de dominio: se resuelven en el middleware que construye el `Environment`.

### 0.4 Aviso legal (importante, no lo saltes)

- Odoo Community es **LGPLv3**. Puedes estudiar su comportamiento y reimplementarlo, pero **no copiar código, ni planes contables, ni plantillas de impuestos, ni traducciones** de sus repositorios sin cumplir la LGPL (y sin arrastrar esa licencia a tu producto).
- Los datos de localización (planes de cuentas, tasas de IVA, formatos de reportes legales) deben obtenerse de **fuentes primarias**: normativa del SII, AEAT, IRS, SUNAT, etc. Documenta la fuente de cada pack.
- "Odoo" es marca registrada. Puedes decir "compatible con", no "Odoo".
- Define tu propia licencia antes de la Fase 0 (recomendación: AGPLv3 con excepción comercial, o BSL con conversión a Apache 2.0 a los 4 años).

---

## 1. Arquitectura de servicios

```
                    ┌──────────────────────────────────────────┐
   Agentes IA ──────▶            ordo-gateway                   │
   Apps / ETL       │  TLS · routing por tenant · rate limit     │
   MCP clients      │  idempotencia · quotas · trazas OTel       │
                    └───────┬──────────────────────┬────────────┘
                            │                      │
              ┌─────────────▼──────────┐   ┌───────▼─────────────┐
              │      ordo-iam          │   │      ordo-api       │
              │ OIDC/OAuth2.1 · SCIM   │   │  kernel ORM +       │
              │ agentes · delegación   │◀──│  módulos de negocio │
              │ capability tokens      │   │  PDP embebido       │
              └───────────┬────────────┘   └───────┬─────────────┘
                          │                        │
                    ┌─────▼──────┐        ┌────────▼────────┐
                    │ Postgres   │        │  Postgres       │
                    │ (identity) │        │  (tenants)      │
                    └────────────┘        └────────┬────────┘
                                                   │ outbox
              ┌────────────────┬───────────────────┼──────────────┐
        ┌─────▼─────┐   ┌──────▼──────┐   ┌────────▼────┐  ┌──────▼──────┐
        │ ordo-jobs │   │ ordo-events │   │ ordo-render │  │ ordo-mcp    │
        │ cron/cola │   │ NATS+webhook│   │ PDF/XML     │  │ MCP server  │
        └───────────┘   └─────────────┘   └─────────────┘  └─────────────┘
```

Todos los servicios son *stateless* salvo Postgres, Redis, NATS y MinIO.

---

## 2. `ordo-iam` — Identidad y autorización centralizada

Es el primer entregable con valor real. Debe estar listo antes que el kernel del ERP.

### 2.1 Estrategia build vs. buy

Construir un OP (OpenID Provider) desde cero y que sea *correcto* toma meses. Estrategia recomendada:

- **Fase 0–1: Keycloak** desplegado como OP, detrás de la interfaz OIDC estándar. Desbloquea todo de inmediato.
- **En paralelo:** `ordo-iam` propio, que arranca como *Authorization Layer* (emisión de capability tokens, delegación, políticas, cuotas) delegando la autenticación en Keycloak vía OIDC.
- **Fase 3+:** `ordo-iam` absorbe también la autenticación (Authlib para el plumbing OAuth). Como todo el sistema habla OIDC estándar, el reemplazo es transparente.

Documenta esto como un ADR. Si Claude Code intenta escribir un OP desde cero en la semana 1, detenlo.

### 2.2 Tipos de principal (esto es lo que Keycloak no te da)

```
Principal
├── User          humano; MFA, sesión, consentimiento
├── ServiceClient integración máquina-máquina clásica (client_credentials)
└── Agent         ★ ciudadano de primera clase
    ├── owner_user_id        a nombre de quién actúa por defecto
    ├── model / version      claude-opus-5, gpt-x, etc. (para auditoría)
    ├── capability_grants[]  qué puede hacer, con qué límites
    ├── budget               llamadas/día, tokens de escritura, monto acumulado
    └── autonomy_level       observador | propone | ejecuta | ejecuta+aprueba
```

### 2.3 Flujos soportados

| Flujo | Uso |
|---|---|
| Authorization Code + PKCE | Usuarios humanos en apps cliente |
| Client Credentials | Integraciones backend |
| Device Code | CLI, dispositivos |
| **Token Exchange (RFC 8693)** | ★ Un agente obtiene un token que actúa *en nombre de* un usuario; claim `act` conserva la cadena de delegación |
| Refresh tokens rotativos | Con detección de reuso |
| DPoP (RFC 9449) | Tokens ligados a clave; evita robo de bearer tokens |
| mTLS (opcional) | Clientes de alta seguridad |
| SCIM 2.0 | Aprovisionamiento desde IdP corporativo |
| SAML / OIDC federation | Login corporativo (Entra ID, Google Workspace, Okta) |

### 2.4 Capability tokens — el corazón del diseño

Un access token de agente lleva restricciones **verificables sin consultar la base**:

```jsonc
{
  "iss": "https://iam.ordo.example",
  "sub": "agent:8f2c...",              // el agente
  "act": { "sub": "user:1042" },       // actúa en nombre de
  "tenant": "acme",
  "companies": [1, 3],
  "scope": "erp.read erp.write",
  "cap": {
    "models": {
      "sale.order":      ["read", "create", "write"],
      "account.move":    ["read"],
      "res.partner":     ["read", "create"]
    },
    "limits": {
      "max_amount_per_op":  { "CLP": 5000000 },
      "max_amount_per_day": { "CLP": 20000000 },
      "max_writes_per_min": 120,
      "record_domain": [["company_id","in",[1,3]]]
    },
    "requires_approval": ["account.move.action_post", "res.partner.unlink"],
    "deny": ["res.users.write", "ir.model.*"]
  },
  "exp": 1767225600,
  "jti": "..."
}
```

Reglas:
- El PDP evalúa `cap` **antes** de tocar el ORM. Denegación por defecto.
- `cap` nunca puede ampliar lo que el usuario delegante tiene. Intersección estricta: `permisos_efectivos = permisos_usuario ∩ cap_agente ∩ record_rules`.
- Los límites monetarios acumulados sí requieren estado (Redis, ventana deslizante).

### 2.5 Motor de políticas (PDP)

Tres capas que se componen:

1. **RBAC** — grupos y permisos por modelo/operación (`ir.model.access` equivalente).
2. **ABAC / record rules** — dominios asociados a grupos, evaluados a nivel de fila. Semántica Odoo: reglas globales en `AND`, reglas de grupo en `OR`.
3. **Capabilities** — restricciones del token, evaluadas primero.

Implementación: **PDP embebido como librería** en `ordo-api` (sin salto de red), con caché de políticas invalidado por evento. Expón además `POST /iam/v1/authorize` para consultas externas (útil para que un agente pregunte "¿puedo hacer esto?" antes de intentarlo).

### 2.6 Human-in-the-loop

Objeto de primera clase: `iam.approval_request`.

```
Agente intenta operación restringida
  → 202 Accepted + { approval_id, expires_at, status: "pending" }
  → notificación al aprobador (email/Slack/webhook)
  → aprobador resuelve
  → agente hace polling o recibe webhook
  → reintenta con el mismo Idempotency-Key → se ejecuta
```

La operación pendiente se guarda **serializada y firmada**: se ejecuta exactamente lo aprobado, ni un byte distinto.

### 2.7 Auditoría

Cada evento de auth y cada escritura de negocio registra: `principal_id`, `act_chain[]`, `agent_model`, `trace_id`, `idempotency_key`, `ip`, `token_jti`, `policy_decision`, `approval_id`. Append-only, con encadenamiento por hash (`prev_hash`) para detectar manipulación. Retención y export configurables.

---

## 3. `ordo-core` — El kernel tipo Odoo

Esta es la pieza que hace posible todo lo demás. **No empieces por CRM.**

### 3.1 Registry de modelos

- Modelos declarados en Python (declarativo), registrados en un `Registry` en boot.
- Metadatos persistidos en `ir_model`, `ir_model_field`, `ir_model_constraint` → introspectables y extensibles en runtime.
- **Herencia:**
  - *Extensión* (`_inherit`): un módulo agrega campos/métodos a un modelo existente. Merge del registry en boot, respetando el grafo de dependencias.
  - *Delegación* (`_inherits`): p.ej. `product.product` delega en `product.template`.
- **Campos dinámicos (Studio):** se almacenan en columna `JSONB x_custom` con índices `GIN`/expresión. Opción de "materializar" a columna real vía migración controlada. Evita DDL con locks en caliente.

### 3.2 Sistema de campos

Tipos: `Char, Text, Html, Integer, Float, Monetary, Boolean, Date, Datetime, Binary, Selection, Json, Many2one, One2many, Many2many, Reference`.

Atributos clave: `required, readonly, index, default, compute, inverse, search, related, store, depends, groups, company_dependent, translate, tracking, agent_hint, examples`.

- **Campos calculados** con grafo de dependencias (`@depends('line_ids.price_total')`), recomputación en lote, invalidación de caché.
- **`agent_hint` y `examples`** son nuevos y obligatorios en campos de negocio: alimentan el schema semántico que consumen los agentes.

### 3.3 Lenguaje de dominios

Mantén compatibilidad sintáctica con Odoo (facilita migraciones y el conocimiento previo de los LLMs):

```python
[('state','=','sale'), '|', ('partner_id.country_id.code','=','CL'),
                            ('amount_total','>',1000000)]
```

El compilador traduce a SQL con joins sobre rutas punteadas, aplica record rules y `active_test`. **Este componente concentra el mayor riesgo de bugs de seguridad y de rendimiento: tests exhaustivos + property-based testing.**

### 3.4 Environment y transacciones

```python
env = Environment(tenant, user, agent, companies, lang, tz, context, session)
```
- Unit of Work por request, con savepoints por operación de un batch.
- Caché de registros por transacción; invalidación explícita.
- Bloqueo optimista por `write_date` + `version`; conflicto → `409 CONCURRENT_MODIFICATION` con el estado actual del registro (para que el agente reconcilie).

### 3.5 Servicios transversales del kernel

| Servicio | Notas |
|---|---|
| Secuencias | Por compañía/período; **modo "sin huecos"** obligatorio para documentos legales (lock a nivel de fila, no en caché) |
| Multi-moneda | Tasas con vigencia, redondeo por moneda, ganancia/pérdida cambiaria |
| i18n | Traducciones de campos `translate=True` + catálogo de mensajes; idiomas por tenant |
| Chatter (`mail.thread`) | Mensajes, seguidores, actividades. Es el canal natural agente↔humano: úsalo |
| Adjuntos | MinIO + deduplicación por hash + checksum |
| Cron | Tareas programadas por tenant, con lock distribuido |
| Cola de jobs | Tabla + `SKIP LOCKED`, reintentos con backoff, DLQ, prioridad |
| Outbox de eventos | Escritura transaccional → relay a NATS → webhooks |
| Automated actions | Reglas declarativas: trigger (create/write/unlink/cron/estado) → condición (dominio) → acción (Python sandbox / webhook / crear registro) |
| Motor de reportes | Query → dataset JSON → renderer (PDF/XLSX/XML). Los reportes son endpoints, no vistas |
| Data import/export | Con mapeo, validación previa (`dry-run`) y reporte de errores por fila |

### 3.6 Capa de agente (lo que nadie más tiene)

Endpoints y comportamientos que se implementan **una vez en el kernel** y aplican a todos los módulos:

| Capacidad | Endpoint / mecanismo |
|---|---|
| Schema semántico | `GET /meta/v1/schema?models=sale.order&format=llm` → modelos, campos, relaciones, invariantes, ejemplos, en formato compacto |
| Catálogo de acciones | `GET /meta/v1/actions` → métodos invocables, sus parámetros, precondiciones y efectos |
| Dry-run universal | `POST /api/v1/{model}/{op}?dry_run=true` → devuelve el resultado simulado, valores calculados y validaciones que fallarían, sin escribir |
| Idempotencia | Header `Idempotency-Key` obligatorio en escrituras; respuesta cacheada 24 h |
| Transacción multi-operación | `POST /api/v1/tx` con lista de operaciones y `atomic: true\|false` |
| Errores estructurados | `{ code, message, field, model, record_id, hint, retryable, docs_url }` con códigos estables versionados |
| Explicación | `GET /api/v1/{model}/{id}/explain` → cómo se calculó cada campo, qué reglas aplicaron |
| Búsqueda semántica | `POST /api/v1/search` sobre embeddings de registros (pgvector) |
| NL → dominio | `POST /meta/v1/translate-query` → convierte lenguaje natural a dominio válido y lo devuelve *sin ejecutar* |
| Sandbox | Tenant efímero clonado para que el agente ensaye operaciones destructivas |
| Suscripciones | `POST /events/v1/subscriptions` → webhook o stream SSE/NATS filtrado por dominio |
| Servidor MCP | `ordo-mcp` expone modelos y acciones como tools MCP, con el schema semántico como descripción |

---

## 4. API pública

### 4.1 Superficie

```
/api/v1/{model}                      GET (search_read) · POST (create)
/api/v1/{model}/{id}                 GET · PATCH · DELETE
/api/v1/{model}/batch                POST  (create/write/unlink masivo)
/api/v1/{model}/{id}/{method}        POST  (métodos de negocio: action_confirm, etc.)
/api/v1/{model}/aggregate            POST  (read_group: agrupaciones y métricas)
/api/v1/tx                           POST  (transacción multi-operación)
/api/v1/rpc                          POST  (RPC genérico compatible Odoo)
/meta/v1/...                         schema, acciones, traducción de queries
/events/v1/...                       suscripciones, replay
/reports/v1/{report}                 generación de reportes
/iam/v1/...                          identidad, tokens, aprobaciones, políticas
/mcp                                 servidor MCP
```

Además: **shim JSON-RPC/XML-RPC compatible con Odoo** (`/jsonrpc`, `/xmlrpc/2/object`) para que herramientas existentes se conecten sin cambios. Es un diferenciador comercial fuerte para migraciones.

### 4.2 Convenciones no negociables

- Versionado en la ruta; *deprecation* anunciada con 2 versiones de anticipación y header `Sunset`.
- Paginación por cursor (no `offset`) para colecciones grandes.
- `fields=` para proyección; `expand=` para incluir relacionados en una llamada (los agentes pagan por round-trip).
- ETag + `If-Match` en escrituras.
- Todo timestamp en UTC ISO-8601; el tenant define tz de presentación.
- Montos siempre como `{ "amount": "1234.56", "currency": "CLP" }` — string decimal, nunca float.
- Rate limit por principal, no por IP. Headers `RateLimit-*` (RFC 9331).

### 4.3 Objetivos de rendimiento (SLO)

| Operación | p50 | p95 |
|---|---|---|
| Lectura simple por id | 8 ms | 30 ms |
| `search_read` 80 registros, 15 campos | 25 ms | 100 ms |
| `aggregate` sobre 1 M de filas | 150 ms | 600 ms |
| Confirmar orden de venta (con stock e impuestos) | 60 ms | 250 ms |
| Contabilizar asiento | 40 ms | 150 ms |

Estos números van en un test de carga en CI desde la Fase 2. Si se degradan, el build falla.

---

## 5. Mapa de módulos

### 5.1 Fundacionales
`base` · `iam-bridge` · `mail` (chatter) · `attachments` · `sequences` · `currency` · `uom` · `partner` · `product` · `settings` · `automation` · `reports` · `studio-api`

### 5.2 Paridad Odoo Community

| Área | Módulos |
|---|---|
| Ventas | CRM, Sales, Pricelists, Coupons/Promociones, Sales Teams, Commissions |
| Compras | Purchase, RFQ, Vendor pricelists, Purchase Agreements |
| Inventario | Warehouse, Multi-location, Lotes/series, Rutas, Reglas de reabastecimiento, Trazabilidad, Inventarios cíclicos, Barcode API, Dropshipping, Consignación |
| Contabilidad | Plan de cuentas, Diarios, Asientos, Impuestos, Posiciones fiscales, Conciliación bancaria, Pagos, Términos de pago, Multi-moneda, Cierre de período, Analítica |
| Facturación | Facturas cliente/proveedor, Notas de crédito/débito, Anticipos, Facturación recurrente |
| Manufactura | BoM (multinivel, variantes, phantom), Órdenes de producción, Órdenes de trabajo, Centros de trabajo, Subcontratación, Desechos, Desensamblaje |
| Proyectos | Proyectos, Tareas, Etapas, Timesheets, Rentabilidad |
| RRHH | Empleados, Departamentos, Contratos, Ausencias, Gastos, Reclutamiento, Asistencia |
| Punto de venta | Sesiones, Órdenes, Cierre de caja, Métodos de pago (API; el terminal es del cliente) |
| Otros | Mantenimiento, Flota, Encuestas, eLearning, Eventos, Marketing por email, Suscripciones a listas |

### 5.3 Reimplementación de funciones Enterprise (diseño propio)

| Función Odoo Enterprise | Nuestro equivalente | Nota de diseño |
|---|---|---|
| Studio | `studio-api` | CRUD de modelos/campos/reglas vía API. Para un agente esto es *superpoder*: puede extender el ERP solo |
| Reportes contables | `account-reports` | Motor declarativo de reportes (definición en YAML) + engine de expresiones |
| Conciliación bancaria asistida | `bank-reco` | Matching por reglas + scoring; el agente resuelve los casos dudosos |
| Sincronización bancaria | `bank-sync` | Conectores: Plaid, Salt Edge, Belvo (LatAm), PSD2/Open Banking |
| Documents / OCR | `documents` | Ingesta, clasificación, extracción con modelo de visión, workspaces, flujos |
| Sign | `sign` | Firma electrónica; integrar con proveedores locales de firma avanzada/cualificada |
| Approvals | Ya en `ordo-iam` | Se unifica con el HITL de agentes — mejor que Odoo |
| Planning | `planning` | Turnos, disponibilidad, asignación con optimizador |
| Field Service | `fsm` | Órdenes de servicio, rutas, geolocalización, partes en terreno |
| Helpdesk | `helpdesk` | Tickets, SLA, escalamiento, base de conocimiento |
| Quality | `quality` | Puntos de control, alertas, no conformidades |
| MRP II / MPS | `mrp-advanced` | Planificación maestra, capacidad finita, PLM, ECO |
| Subscriptions | `subscription` | Ciclos, prorrateo, MRR/ARR, churn, dunning |
| Rental | `rental` | Disponibilidad temporal, penalizaciones |
| Marketing Automation | `marketing-automation` | Campañas como grafo de estados |
| Payroll | `payroll` | Motor de reglas salariales por país (ver §6) |
| Appraisal / Recruitment | `hr-talent` | |
| Consolidación | `consolidation` | Multi-entidad, eliminaciones, conversión de moneda |
| Audit trail | Ya en el kernel | Encadenado por hash desde el día 1 |
| IoT | `iot-bridge` | MQTT/HTTP; el edge lo pone el cliente |
| Knowledge | `knowledge` | Artículos + embeddings → RAG nativo para los agentes |

### 5.4 Módulos que solo existen aquí

- `agent-registry` — catálogo de agentes, capacidades, presupuestos, métricas de desempeño.
- `agent-memory` — memoria estructurada y episódica por agente y por tenant.
- `simulation` — clonar tenant, ejecutar plan, comparar estado resultante (útil para "¿qué pasa si subo precios 8%?").
- `intent-log` — registra intención declarada del agente vs. efecto real. Base de la explicabilidad y de las auditorías.
- `policy-guardrails` — reglas de negocio duras que ningún agente puede violar aunque tenga permisos (p. ej. "nunca vender bajo costo", "nunca eliminar asientos contabilizados").

---

## 6. Framework de localizaciones

Odoo tiene ~90 localizaciones. Replicarlas a mano es inviable; hay que construir un **framework declarativo** y luego llenar packs.

### 6.1 Anatomía de un pack de país

```
localizations/cl/
├── manifest.yaml            # metadatos, versión, fuentes normativas
├── coa.yaml                 # plan de cuentas (+ variantes: pyme, corporativo)
├── taxes.yaml               # impuestos, grupos, vigencias, reglas de redondeo
├── fiscal_positions.yaml    # mapeos por tipo de contribuyente / región
├── document_types.yaml      # tipos de documento legales (DTE 33, 34, 39, 61...)
├── sequences.yaml           # numeración legal, folios, correlatividad
├── partner_validation.yaml  # RUT: formato, dígito verificador, unicidad
├── reports/                 # F29, libros de compra/venta, balance tributario
├── einvoicing/              # conector SII: firma, CAF, envío, acuse, XSD
├── payroll/                 # reglas salariales (AFP, Fonasa/Isapre, gratificación)
└── tests/                   # casos dorados con valores esperados
```

### 6.2 Piezas del framework

1. **Motor de impuestos** — impuestos compuestos, retenciones, base imponible modificada, precio con/sin impuesto, redondeo por línea vs. por documento, exenciones, impuestos por región (US sales tax, GST/HST canadiense, IVA UE con OSS/reverse charge).
2. **Motor de reportes legales** — definición declarativa: líneas, expresiones sobre saldos/impuestos, jerarquía, formatos de export (PDF, XLSX, XML, TXT posicional).
3. **Framework de facturación electrónica** — máquina de estados común (`borrador → firmado → enviado → aceptado/rechazado → anulado`), gestión de certificados, firma XAdES/CAdES, reintentos, acuses, contingencia. Cada país implementa el adaptador.
4. **Motor de reglas de nómina** — DSL declarativa: reglas encadenadas con secuencia, categorías, bases, topes, tramos; el país aporta las tablas.
5. **Numeración legal** — inalterabilidad, correlatividad sin huecos, encadenamiento por hash (requisito en FR, PT, IT, ES-Verifactu, CL).

### 6.3 Olas de despliegue

| Ola | Países | Motivo |
|---|---|---|
| 1 | **Chile** | Mercado inicial; e-invoicing complejo → valida el framework |
| 2 | México, Colombia, Perú, Argentina, Brasil | E-invoicing obligatorio; misma familia normativa |
| 3 | España, Portugal, Italia, Francia, Alemania | SII/Verifactu, SAF-T, SdI, Factur-X, GoBD |
| 4 | USA, Canadá | Sales tax por jurisdicción (integrar Avalara/TaxJar antes que construir) |
| 5 | UK, NL, BE, PL, RO, resto UE | Making Tax Digital, JPK, e-Factura |
| 6 | India, Australia, Nueva Zelanda, Emiratos, Arabia Saudita | GST, IRN/e-way bill, ZATCA |
| 7 | Resto | Guiado por demanda comercial |

Cada pack debe traer **tests dorados**: facturas reales anonimizadas con los importes de impuestos exactos esperados.

---

## 7. Roadmap

| Fase | Duración est. | Entregable verificable |
|---|---|---|
| **F0 — Bootstrap** | 1–2 sem | Servidor provisionado, repo, CI, Compose levantando Postgres/Redis/NATS/MinIO/Keycloak, healthchecks verdes, ADRs 001–010 escritos |
| **F1 — IAM** | 3–4 sem | Login OIDC, agentes registrables, token exchange con `act`, capability tokens, PDP con RBAC+ABAC, aprobaciones HITL, auditoría encadenada. **Suite de tests de seguridad pasando** |
| **F2 — Kernel** | 6–8 sem | Registry, campos calculados, dominios→SQL, record rules, herencia, secuencias, chatter, jobs, cron, outbox, adjuntos, i18n. API genérica CRUD + batch + tx + dry-run + idempotencia. OpenAPI y schema semántico generados |
| **F3 — Capa agéntica** | 3 sem | Servidor MCP, errores estructurados, `explain`, búsqueda semántica, NL→dominio, suscripciones, sandbox |
| **F4 — Contabilidad** | 6–8 sem | Plan de cuentas, diarios, asientos, motor de impuestos, conciliación, pagos, cierre, analítica, reportes financieros base. Invariantes probadas con property-based testing |
| **F5 — Ventas y compras** | 4–6 sem | CRM, Sales, Purchase, listas de precios, facturación desde documentos |
| **F6 — Inventario** | 5–7 sem | Multi-almacén, movimientos, lotes/series, rutas, reabastecimiento, valorización (FIFO/promedio/estándar) con asientos automáticos |
| **F7 — Localización Chile** | 4–6 sem | Pack CL completo con DTE certificado en ambiente de pruebas del SII |
| **F8 — Manufactura, proyectos, RRHH** | 8–10 sem | MRP, BoM, timesheets, gastos, ausencias |
| **F9 — Enterprise-equivalentes** | 10–12 sem | Studio API, Documents, Sign, Planning, Subscriptions, Helpdesk, FSM, Quality |
| **F10 — Olas de localización** | continuo | 2–3 países por ola, en paralelo con lo demás |
| **F11 — Endurecimiento** | continuo | Carga, caos, pentest, DR, SOC 2 readiness |

Estimaciones para un equipo Claude Code trabajando en paralelo con revisión humana. **No comprimas F1 ni F2**: todo lo demás se apoya en ellas.

---

## 8. Estructura del repositorio

```
ordo/
├── ADR/                      decisiones de arquitectura numeradas
├── docs/
│   ├── api/                  OpenAPI generado + guías
│   ├── agent/                cómo un agente usa el sistema
│   └── localization/         fuentes normativas por país
├── services/
│   ├── gateway/
│   ├── iam/
│   ├── api/
│   ├── jobs/
│   ├── events/
│   ├── render/
│   └── mcp/
├── packages/
│   ├── ordo-core/            kernel: registry, ORM, dominios, env, PDP
│   ├── ordo-schema/          schema semántico y generación OpenAPI
│   └── ordo-testing/         fixtures, factories, aserciones de dominio
├── modules/
│   ├── base/  mail/  product/  partner/ ...
│   ├── account/  sale/  purchase/  stock/  mrp/ ...
│   └── agent_registry/  simulation/  policy_guardrails/ ...
├── localizations/
│   └── cl/  mx/  es/ ...
├── infra/
│   ├── compose/  ansible/  terraform/  systemd/
│   └── observability/
├── tests/
│   ├── unit/  integration/  contract/  load/  security/  golden/
└── tools/                    scripts de scaffolding, generadores, linters
```

---

## 9. Estrategia de pruebas

| Tipo | Qué cubre | Umbral |
|---|---|---|
| Unitarias | Lógica pura de dominio | Cobertura ≥ 85 % en `packages/` y `modules/*/services` |
| Integración | Con Postgres real (testcontainers) | Todo endpoint público |
| Contrato | OpenAPI ↔ implementación; no romper compatibilidad | Bloqueante en CI |
| Property-based (Hypothesis) | Invariantes: débitos = créditos, stock nunca negativo sin permiso, secuencias sin huecos, impuestos conmutativos | Bloqueante |
| Golden | Localizaciones: documentos reales → importes esperados | Bloqueante por pack |
| Seguridad | Escalada de privilegios, bypass de record rules, inyección en dominios, IDOR entre tenants | Bloqueante |
| Carga (k6) | SLO de §4.3 | Bloqueante ante regresión > 20 % |
| Caos | Caída de DB/Redis/NATS a mitad de transacción | Semanal |
| **Agénticas** | Un agente real intenta 200 tareas de negocio; se mide tasa de éxito y estados inválidos generados | Semanal, es el KPI del producto |

La última es la más importante y la que nadie hace. Constrúyela temprano.

---

## 10. Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Alcance infinito (Odoo son ~20 años-persona) | Priorizar por lo que un agente realmente necesita; publicar API estable temprano; no perseguir paridad de UI que no existe |
| El compilador de dominios filtra datos entre tenants | Tests de seguridad obligatorios; RLS como segunda barrera; revisión humana de todo cambio en ese archivo |
| Contabilidad incorrecta | Property-based testing + revisión de un contador por localización; nunca aceptar un pack sin tests dorados |
| Construir un OP OIDC desde cero se come 3 meses | Keycloak primero, migración posterior detrás de interfaz estándar |
| Rendimiento del ORM genérico | SLO en CI desde F2; presupuesto de queries por endpoint (detección de N+1 automática) |
| Deuda de Claude Code sin revisión | Todo PR pasa por revisión humana en `packages/ordo-core`, `modules/account` y `services/iam`; el resto por muestreo |
| Contaminación de licencia por copiar de Odoo | Prohibición explícita en `CLAUDE.md`; revisión de similitud antes de cada release |

---

## 11. Primeros 10 ADRs a escribir en la Fase 0

1. Lenguaje y stack base
2. Modelo de multi-tenancy
3. Estrategia IAM: Keycloak ahora, propio después
4. Diseño de capability tokens
5. Campos dinámicos: JSONB vs. DDL
6. Compatibilidad del lenguaje de dominios con Odoo
7. Cola de jobs en Postgres vs. broker externo
8. Bus de eventos y patrón outbox
9. Versionado y política de compatibilidad de la API
10. Licencia del producto y política anti-contaminación de código
