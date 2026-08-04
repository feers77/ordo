# F1-06 — Aprobaciones human-in-the-loop (diseño)

Objeto de primera clase `iam.approval_request` (PLAN §2.6). La operación
pendiente se guarda **serializada y sellada por hash**: se ejecuta exactamente
lo aprobado, ni un byte distinto.

## Tabla (migración 0004)

`iam_approval_request`: `id`, `tenant`, `agent_id`, `requested_by` (usuario efectivo),
`operation JSONB` (model, operation, amount, payload), `operation_hash` (sha256 canónico),
`idempotency_key`, `status` ∈ {pending, approved, rejected, expired, consumed},
`approver_id`, `resolved_at`, `expires_at` (default +24h), `reason`.
Índice único `(tenant, idempotency_key)` — reintentar con la misma clave no duplica.

## Flujo

```
POST /iam/v1/authorize  (decision.requires_approval)
  ▶ el llamador crea POST /iam/v1/approvals  → 201 {approval_id, status:pending, expires_at}
       (idempotente por Idempotency-Key: misma clave ⇒ misma solicitud)
  ▶ aprobador (owner del agente): POST /iam/v1/approvals/{id}/approve | /reject
  ▶ agente: GET /iam/v1/approvals/{id}  → estado
  ▶ agente reintenta con misma Idempotency-Key:
       POST /iam/v1/approvals/{id}/consume {operation}
       - status ≠ approved ⇒ IAM_APPROVAL_PENDING / _REJECTED / _EXPIRED
       - hash(operation) ≠ operation_hash ⇒ IAM_APPROVAL_MISMATCH
       - ok ⇒ status=consumed (una sola vez; segundo intento ⇒ IAM_APPROVAL_CONSUMED)
```

Auditoría: cada creación/resolución/consumo se registra en `iam_audit_log`
con `approval_id` en el payload.

## Errores nuevos

`IAM_APPROVAL_NOT_FOUND` (404), `IAM_APPROVAL_PENDING` (409, retryable),
`IAM_APPROVAL_REJECTED` (403), `IAM_APPROVAL_EXPIRED` (410),
`IAM_APPROVAL_CONSUMED` (409), `IAM_APPROVAL_MISMATCH` (409),
`IAM_NOT_APPROVER` (403).

## Tests (primero)

Crear (pending + expires_at); idempotencia por clave; solo el owner aprueba;
aprobar → consumir ok; consumir sin aprobar; rechazado; vencido; doble consumo;
operación alterada (mismatch); aprobador ajeno; auditoría de cada transición.
