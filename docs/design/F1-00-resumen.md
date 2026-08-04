# Fase 1 — IAM: resumen de entrega

Estado: implementada, con CI verde. Pendiente de revisión humana y merge
(`services/iam/**` exige revisión obligatoria, AGENTS.md §7).

## Entregables vs. §2 del PLAN-MAESTRO

| Requisito del plan | Dónde | Verificación |
|---|---|---|
| Principals: User, ServiceClient, Agent | `models.py`, `repository.py` | 14 tests de invariantes |
| Login OIDC (Keycloak como OP) | `oidc.py`, `bridge.py` | 12 tests de seguridad + e2e real |
| Agentes registrables | `POST /iam/v1/agents` | e2e + integración |
| Token exchange RFC 8693 con `act` | `POST /iam/v1/token` | 10 tests + e2e |
| Capability tokens (claim `cap`) | `captokens.py`, `tokens.py` | 7 tests de merge + verificación de firma |
| PDP con RBAC + ABAC | `pdp.py`, `POST /iam/v1/authorize` | 14 unit + 10 integración |
| Aprobaciones HITL | `approvals.py`, `/iam/v1/approvals/*` | 9 tests + e2e |
| Auditoría encadenada | `audit.py` | tamper y borrado detectados |

## Decisiones de seguridad tomadas

- **Denegación por defecto** en todas las capas: sin grants no hay token de agente;
  sin ACL no hay operación; `cap` nunca amplía los permisos del usuario delegante.
- **Sin auto-creación de identidades**: el primer login solo vincula un usuario
  pre-aprovisionado del mismo tenant con email verificado.
- **Dinero en `Decimal`** de punta a punta; acumulados diarios en micros enteros.
  Contador caído ⇒ fail-closed (`CAP_LIMIT_BACKEND_DOWN`).
- **Aprobación sellada por hash**: se ejecuta exactamente la operación aprobada,
  una sola vez.
- Verificación JWT restringida a RS256/ES256; `alg=none` y confusión de clave
  simétrica cubiertas por tests.

## Límites conocidos (fuera del alcance de F1)

- Delegación solo agente→su dueño; delegación a terceros con cadena `act`
  multinivel queda para una fase posterior.
- Gestión de roles/ACL/record rules por repositorio; API admin llega con
  `studio-api` (F9) o antes si el kernel lo requiere.
- DPoP, mTLS, SCIM y federación SAML: pendientes (§2.3 del plan) — no bloquean F2.
- Las record rules se devuelven como dominios; su compilación a SQL es del kernel (F2).
