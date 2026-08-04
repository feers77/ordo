# F1-01 — Modelo de datos de principals (diseño)

Base dedicada `ordo_iam` (arquitectura §1: Postgres identity separado del de tenants).
Todas las tablas: `id UUID`, `create_date/write_date timestamptz UTC`, `version int` (bloqueo optimista).

## Tablas

- `iam_principal` — supertipo. `type` ∈ {user, service_client, agent}; `tenant` (slug);
  `display_name`; `status` ∈ {active, suspended, deleted}. Unicidad e integridad por subtipo.
- `iam_user` — `principal_id` PK/FK; `email` (único por tenant, case-insensitive);
  `idp_sub` (sub de Keycloak, único global, nullable hasta primer login); `mfa_enrolled`.
- `iam_service_client` — `principal_id` PK/FK; `client_id` único global; `allowed_scopes text[]`.
- `iam_agent` — `principal_id` PK/FK; `owner_user_id` FK→iam_user (a nombre de quién actúa);
  `model`, `model_version`; `autonomy_level` ∈ {observer, propose, execute, execute_approve}
  (default observer); `budget JSONB` (llamadas/día, escrituras, monto acumulado).
- `iam_capability_grant` — `agent_id` FK; `granted_by` FK→iam_user; `cap JSONB` (estructura
  ADR-004); `valid_from/valid_until timestamptz`; `revoked_at` nullable.

## Invariantes (tests primero)

1. Agent exige owner user **activo y del mismo tenant**.
2. Email único por tenant; `idp_sub` único global.
3. Sin grants vigentes ⇒ el agente no tiene capacidades (denegación por defecto).
4. Grant revocado o vencido nunca aparece en capacidades efectivas.
5. Suspender/borrar al owner suspende sus agentes (no quedan huérfanos activos).
6. `autonomy_level` default = observer.
7. Timestamps siempre tz-aware UTC.

## Errores (códigos estables)

`IAM_OWNER_NOT_FOUND`, `IAM_OWNER_INACTIVE`, `IAM_TENANT_MISMATCH`,
`IAM_EMAIL_TAKEN`, `IAM_CLIENT_ID_TAKEN`, `IAM_GRANT_NOT_FOUND`.

## Fuera de alcance en este PR

Endpoints HTTP, bridge OIDC, emisión de tokens, PDP (siguientes pasos F1).
