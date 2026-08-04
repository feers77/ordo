# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es/) + Conventional Commits (changelog automático vía commitizen desde F2).

## [Unreleased]

### Added

- **F2.6** Chatter como canal agente-humano: mensajes con author_kind obligatorio (user,
  agent o system, para que quien lee un hilo distinga persona de agente sin inferirlo),
  seguidores, actividades con estado derivado de la fecha, y tracking automatico de
  cambios con valor anterior y nuevo. Adjuntos con deduplicacion por sha256: dos archivos
  identicos comparten objeto y borrar uno no borra los bytes del otro; tamano, checksum
  y mimetype se derivan del contenido, nunca se confian del cliente.
- **E2E del kernel**: ciclo completo de un documento (schema semantico, secuencia legal,
  dry-run, creacion, chatter, bloqueo optimista, adjuntos, outbox y jobs) contra Postgres
  real, con tenant aislado por test. 19 tests nuevos.
- **F2.5** Servicios transversales del kernel: secuencias con modo no_gap que bloquea la
  fila (documentos legales sin huecos, verificado con 5 sesiones concurrentes), cola de jobs
  en Postgres con FOR UPDATE SKIP LOCKED (dos workers nunca toman el mismo job), reintentos
  con backoff exponencial y DLQ, cron con lock de fila que avanza next_call antes de
  ejecutar, y outbox transaccional con relay idempotente (message id = id del outbox, para
  que el broker deduplique tras un crash).
- **Schema semantico** generado desde el registry (`GET /meta/v1/schema`, formato llm o
  full) con convenciones para agentes: dinero como string decimal, dry_run e Idempotency-Key
  en escrituras, paginacion por cursor. 25 tests nuevos.
- **F2.4** ORM de escritura y API generica: RecordSet batch-first (create/read/write/
  unlink/search) con validaciones (required, readonly, selection, tipos, Monetary rechaza
  float), bloqueo optimista que devuelve el estado actual del registro en el 409,
  paginacion por cursor (nunca offset), dry-run universal que hace rollback siempre,
  idempotencia con respuesta cacheada 24h y deteccion de reuso, y transacciones
  multi-operacion atomicas o con reporte parcial por indice. Endpoints /api/v1/{model},
  batch y tx. 47 tests nuevos.
- **Seguridad**: el binding de tenant se re-aplica en cada transaccion nueva de la sesion;
  antes un commit a mitad de request dejaba las consultas siguientes sin filtro.
- **F2.3** Campos calculados: decorador @depends, grafo de dependencias con orden
  topologico y deteccion de ciclos al construir el registry (falla el boot, no en runtime),
  recomputacion siempre en lote (N+1 imposible por diseno), campos related resueltos como
  compute con dependencia en cada segmento de la ruta, cache por transaccion con
  invalidacion en cascada. Filtrar por calculado no almacenado se rechaza con
  DOMAIN_FIELD_NOT_STORED. 20 tests nuevos.
- **F2.2** Compilador de dominios a SQL (ADR-006): sintaxis Odoo, operadores de
  comparacion y logicos con notacion polaca, joins por rutas punteadas (max 4 saltos),
  record rules global AND / rol OR, active_test. Garantias: cero interpolacion (todo
  parametro vinculado), identificadores validados contra el registry, limites de tamano.
  52 tests: 7 de inyeccion, 4 property-based (Hypothesis) y 11 que ejecutan el SQL
  generado contra Postgres real.
- **F2.1** Kernel `ordo-core`: sistema de campos (Monetary solo Decimal), registry con
  grafo topologico de modulos, herencia por extension (_inherit) y delegacion (_inherits),
  agent_hint/examples obligatorios en campos de negocio, metadatos en ir_model/ir_model_field
  y Environment con schema-per-tenant + RLS.
- **Seguridad**: rol `ordo_app` sin SUPERUSER ni BYPASSRLS. Conectarse con el rol dueno
  dejaba RLS inerte (segunda barrera de ADR-002 no se aplicaba). Environment fuerza
  `SET LOCAL ROLE` en cada transaccion. 15 tests nuevos de aislamiento y registry.
- **F1.6** Aprobaciones HITL: iam_approval_request con operacion serializada y sellada por
  hash (se ejecuta exactamente lo aprobado), creacion idempotente por Idempotency-Key,
  approve/reject solo por el dueno, consumo unico, expiracion 24h; cada transicion auditada.
  Suite e2e contra Keycloak real: login OIDC, vinculacion, agente, grant, token exchange
  con act, PDP allow/deny/monto, HITL completo y verificacion de la cadena de auditoria.
  Nuevo job e2e en CI. 11 tests nuevos.
- **F1.5** PDP tres capas (cap primero, RBAC del usuario efectivo, record rules global
  AND / rol OR) con denegacion por defecto, limites monetarios en Decimal y acumulados
  diarios en micros enteros (Redis, fail-closed), POST /iam/v1/authorize, y auditoria
  append-only con cadena de hash por tenant (deteccion de tamper y borrado). 24 tests.
- **F1.3/F1.4** Token exchange RFC 8693: POST /iam/v1/token (agente autenticado por secret
  intercambia token del owner por JWT propio con act, tenant, cap merged, jti, exp 900s),
  registro de agentes POST /iam/v1/agents (secret una sola vez), grants por owner,
  JWKS propio en /iam/v1/jwks. Merge de caps: union modelos/deny/requires_approval,
  limites al minimo, record_domain AND. 19 tests nuevos. (ADR-004)
- **F1.2** Bridge OIDC: verificador JWT genérico (JWKS con caché y refetch ante rotación,
  solo RS256/ES256, rechaza alg=none y confusión de clave), bridge `idp_sub`→`iam_user`
  con vinculación en primer login verificado y sin auto-creación, endpoint `GET /iam/v1/me`.
  Realm Keycloak con claim `tenant` y audiencia `ordo-api`. 12 tests de seguridad unit +
  12 integración nuevos. (ADR-003)
- **F1.1** Modelo de datos de principals en `ordo-iam`: `iam_principal`, `iam_user`,
  `iam_service_client`, `iam_agent`, `iam_capability_grant`. Migración Alembic 0001.
  Invariantes: owner activo y mismo tenant, email único por tenant (case-insensitive),
  denegación por defecto (sin grants vigentes = sin capacidades), suspensión en cascada
  owner→agentes. Códigos de error `IAM_*` en `docs/api/errors.md`. (ADR-003, ADR-004, ADR-011)
- **F0** Bootstrap completo: provisioning Ansible, stack compose, ordo-runtime,
  7 esqueletos de servicio, CI/CD, suite agéntica, backups pgBackRest con restore probado,
  runbook, ADRs 001–010.
