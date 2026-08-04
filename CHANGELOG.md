# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es/) + Conventional Commits (changelog automático vía commitizen desde F2).

## [Unreleased]

### Added

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
