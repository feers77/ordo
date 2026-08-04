# Catálogo de códigos de error (contrato público)

Los `code` se agregan, nunca se renombran ni eliminan (AGENTS.md §5).
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
| `IAM_TOKEN_INVALID` | 401 | no | Token malformado, firma/iss/aud/alg inválidos o claim faltante |
| `IAM_TOKEN_EXPIRED` | 401 | sí | Token vencido; renovar y reintentar |
| `IAM_UNKNOWN_IDENTITY` | 401 | no | Identidad no registrada en el tenant (sin auto-creación) |
| `IAM_PRINCIPAL_SUSPENDED` | 403 | no | Principal suspendido |
| `IAM_AGENT_AUTH_FAILED` | 401 | no | client_id/client_secret de agente inválidos |
| `IAM_AGENT_SUSPENDED` | 403 | no | Agente suspendido |
| `IAM_DELEGATION_NOT_ALLOWED` | 403 | no | El subject no es el owner del agente |
| `IAM_NO_CAPABILITIES` | 403 | no | Agente sin grants vigentes |
| `IAM_UNSUPPORTED_GRANT` | 400 | no | grant_type no soportado en /iam/v1/token |
| `IAM_NOT_AGENT_OWNER` | 403 | no | Solo el dueño puede otorgar capacidades |

## PDP (razones de decisión en /iam/v1/authorize)

| Razón | Significado |
|---|---|
| `OK` | Permitido |
| `CAP_DENIED` | Coincide patrón deny del capability token |
| `CAP_NOT_GRANTED` | Modelo/operación no otorgados en el cap |
| `CAP_AMOUNT_EXCEEDED` | Supera max_amount_per_op |
| `CAP_DAILY_LIMIT` | Supera max_amount_per_day acumulado |
| `CAP_LIMIT_BACKEND_DOWN` | Contador de límites caído (fail-closed) |
| `RBAC_DENIED` | Usuario efectivo sin ACL para la operación |

## Aprobaciones HITL

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `IAM_APPROVAL_NOT_FOUND` | 404 | no | Solicitud inexistente o de otro agente |
| `IAM_APPROVAL_PENDING` | 409 | sí | Aún sin resolver; reintentar con la misma Idempotency-Key |
| `IAM_APPROVAL_REJECTED` | 403 | no | El aprobador rechazó la operación |
| `IAM_APPROVAL_EXPIRED` | 410 | no | Venció la ventana de aprobación |
| `IAM_APPROVAL_CONSUMED` | 409 | no | Ya se ejecutó (una aprobación ejecuta una sola vez) |
| `IAM_APPROVAL_MISMATCH` | 409 | no | La operación no coincide byte a byte con lo aprobado |
| `IAM_NOT_APPROVER` | 403 | no | Solo el dueño del agente puede resolver |
| `IAM_IDEMPOTENCY_KEY_REQUIRED` | 400 | no | Falta header Idempotency-Key |
