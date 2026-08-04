# F1-05 — PDP (RBAC + ABAC + capabilities) y auditoría encadenada (diseño)

Tres capas compuestas (PLAN §2.5), denegación por defecto. El PDP vive como módulo
de `ordo-iam` en F1; F2 lo extrae a librería embebida en `ordo-api`.

## Datos (migración 0003)

- `iam_role` — rol por tenant (`tenant`, `name` únicos juntos).
- `iam_role_member` — principal∈rol.
- `iam_acl` — por rol y modelo: `perm_read/write/create/unlink` (equivalente `ir.model.access`).
- `iam_record_rule` — dominio JSONB por modelo+ops; `role_id NULL` ⇒ global.
  Semántica clásica: globales en AND, de rol en OR.
- `iam_audit_log` — append-only con cadena de hash por tenant:
  `hash = sha256(prev_hash + json_canónico(evento))`.

## Evaluación

```
evaluate(session, req) → Decision{allowed, requires_approval, reason, record_domain}
req: tenant, model, operation, amount?, cap? (agente), user_id (efectivo)
1. cap (si agente):  deny glob ─▶ DENY CAP_DENIED
                     op ∉ cap.models[model] ─▶ DENY CAP_NOT_GRANTED
                     amount > max_amount_per_op[cur] ─▶ DENY CAP_AMOUNT_EXCEEDED
                     acumulado día + amount > max_amount_per_day[cur] ─▶ DENY CAP_DAILY_LIMIT
                     match requires_approval ─▶ requires_approval=True
2. RBAC (usuario efectivo = owner delegante): sin ACL que permita op ─▶ DENY RBAC_DENIED
3. Record rules: {global_and: [...], role_or: [...]} devueltas para que el kernel
   las compile (F2). El PDP no toca datos de negocio.
Intersección efectiva: agente pasa 1 Y 2 (cap nunca amplía al usuario).
```

- Montos: `Decimal` vía string; acumulados diarios en Redis como **enteros en micros**
  (`INCRBY`, TTL 25h) — nunca float (AGENTS.md §2.3). Contador inyectable
  (`UsageCounter`): Redis en producción, memoria en tests. Redis caído ⇒ deniega
  operaciones con límite diario (fail-closed).
- Patrones `deny`/`requires_approval`: glob estilo `fnmatch` sobre `model.op`
  o `model.método` (`ir.model.*`, `account.move.action_post`).

## /iam/v1/authorize

POST `{model, operation, amount?{currency,value}}` con bearer:
- token de agente (emisor ordo-iam): cap del claim, usuario efectivo = `act.sub`.
- token de usuario (Keycloak): solo RBAC+reglas.
Respuesta: `{allowed, requires_approval, reason, record_domain}`. Cada decisión
se registra en `iam_audit_log`.

## Gestión de roles/ACL/reglas

En F1 solo por repositorio (seed de tests y e2e). API admin llega con F2
(studio) tras resolver bootstrap de admin por tenant.

## Tests (primero)

Unit: deny gana; op no otorgada; monto por operación; acumulado diario cruza límite;
requires_approval; glob; RBAC permite/deniega; intersección usuario∩cap; reglas
global AND + rol OR; contador caído ⇒ fail-closed. Integración: /authorize con token
agente y usuario; cadena de auditoría detecta manipulación.
