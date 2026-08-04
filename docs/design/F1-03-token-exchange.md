# F1-03 — Token exchange RFC 8693 + capability tokens (diseño)

`ordo-iam` emite access tokens de agente (ADR-004). No es un OP completo:
la autenticación humana sigue en Keycloak (ADR-003). Firma propia RS256,
JWKS público en `GET /iam/v1/jwks`.

## Flujo

```
Agente (agent_id + secret) ──POST /iam/v1/token──▶ ordo-iam
  grant_type=urn:ietf:params:oauth:grant-type:token-exchange
  subject_token = access token Keycloak del usuario delegante
  → verifica subject (OIDCVerifier), resuelve iam_user (bridge)
  → autentica agente (hash del secret), agente activo
  → subject DEBE ser el owner del agente (F1; delegación a terceros = fase posterior)
  → cap = merge(grants vigentes); sin grants ⇒ IAM_NO_CAPABILITIES
  → JWT: iss=IAM_ISSUER, sub="agent:<id>", act={sub:"user:<id>"}, tenant,
         scope derivado, cap, exp=900s, jti
```

## Merge de caps (grants aditivos → un claim)

- `models`: unión de operaciones por modelo.
- `deny` y `requires_approval`: unión (deny siempre gana en el PDP).
- `limits`: el más restrictivo — mínimo por moneda/valor numérico.
- `record_domain`: concatenación (AND, semántica de dominios).
- El claim **nunca amplía** permisos del usuario: la intersección con RBAC/record
  rules del owner la aplica el PDP en cada evaluación (F1-05).

## Credenciales de agente

`iam_agent.secret_hash` (sha256 salteado; el secret es aleatorio de 32 bytes,
no una contraseña humana). Se entrega **una sola vez** al registrar:
`POST /iam/v1/agents` (bearer de usuario; el caller queda como owner).

## Errores nuevos

`IAM_AGENT_AUTH_FAILED` (401), `IAM_AGENT_SUSPENDED` (403),
`IAM_DELEGATION_NOT_ALLOWED` (403), `IAM_NO_CAPABILITIES` (403),
`IAM_UNSUPPORTED_GRANT` (400).

## Tests (primero)

Unit: merge de caps (unión modelos, mínimo límites, unión deny, dominio AND,
vacío ⇒ None). Integración: exchange feliz verifica firma/claims contra JWKS
propio; secret malo; agente suspendido; subject ≠ owner; sin grants; grant_type
malo; subject_token emitido por ordo-iam (re-exchange) rechazado.
