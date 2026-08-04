# Catálogo de códigos de error (contrato público)

Los `code` se agregan, nunca se renombran ni eliminan (CLAUDE.md §5).
Formato de payload: ver `packages/ordo-runtime/src/ordo_runtime/errors.py`.

## Runtime (todos los servicios)

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `INTERNAL_ERROR` | 500 | sí | Error no manejado; reintentar con mismo Idempotency-Key |
| `REQUEST_TIMEOUT` | 504 | sí | La operación excedió el tiempo máximo |

## IAM

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `IAM_PRINCIPAL_NOT_FOUND` | 404 | no | Principal inexistente |
| `IAM_OWNER_NOT_FOUND` | 404 | no | owner_user_id no existe al crear agente |
| `IAM_OWNER_INACTIVE` | 409 | no | El dueño del agente no está activo |
| `IAM_TENANT_MISMATCH` | 409 | no | Agente y dueño de tenants distintos |
| `IAM_EMAIL_TAKEN` | 409 | no | Email ya registrado en el tenant |
| `IAM_CLIENT_ID_TAKEN` | 409 | no | client_id ya registrado (único global) |
| `IAM_GRANT_NOT_FOUND` | 404 | no | Capability grant inexistente |
