# F1-02 — Bridge OIDC (diseño)

Keycloak (realm `ordo`) es el OP en F0–F2 (ADR-003). `ordo-iam` valida sus tokens
detrás de una interfaz OIDC genérica: en F3 se cambia el emisor sin tocar consumidores.

## Componentes

- `OIDCVerifier(issuer, audience, jwks_source)` — verifica JWS RS256/ES256 contra JWKS
  (fetch httpx + caché TTL 300s, refetch ante kid desconocido, inyectable en tests).
  Claims obligatorios: `iss`, `aud`, `exp`, `iat`, `sub`. Rechaza: `alg=none`, algoritmos
  simétricos (confusión de clave), firma inválida, token vencido, iss/aud incorrectos.
- `IdentityBridge` — resuelve claims → `iam_user`:
  1. `idp_sub` conocido → usuario (si activo).
  2. Desconocido + `email` de usuario existente **del mismo tenant** (claim `tenant`) y
     sin `idp_sub` previo → vincula (primer login).
  3. Sin match → `IAM_UNKNOWN_IDENTITY` (401). **Nunca auto-crea usuarios.**
  4. Usuario suspendido → `IAM_PRINCIPAL_SUSPENDED` (403).
- `GET /iam/v1/me` — bearer token → `{principal_id, tenant, type, display_name, email}`.

## Claims esperados del token Keycloak

`sub`, `email`, `email_verified`, `tenant` (user attribute mapeado; requerido),
`preferred_username`. Sin claim `tenant` → `IAM_TOKEN_INVALID`.

## Errores nuevos

`IAM_TOKEN_INVALID` (401), `IAM_TOKEN_EXPIRED` (401, retryable tras refresh),
`IAM_UNKNOWN_IDENTITY` (401), `IAM_PRINCIPAL_SUSPENDED` (403).

## Tests (primero)

Unit (JWKS propio, sin red): firma válida pasa; vencido; iss malo; aud mala; `alg=none`;
HS256 firmado con la clave pública (confusión); kid desconocido; firma adulterada; sin sub;
sin tenant. Integración (postgres): vinculación primer login; sub conocido; email en otro
tenant NO vincula; email ya vinculado a otro sub NO re-vincula; suspendido rechazado.

## Config (env)

`IAM_DATABASE_URL`, `OIDC_ISSUER` (http://127.0.0.1:8080/realms/ordo en dev),
`OIDC_AUDIENCE` (`ordo-api`), `OIDC_JWKS_URL` (derivado del issuer si no se define).
