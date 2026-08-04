# ADR-004 — Diseño de capability tokens

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Un agente debe operar con permisos delegados y acotados (modelos, montos, ventana temporal), verificables sin consultar la base en cada request.

## Opciones consideradas

1. **Claim `cap` en el access token (JWT)** — verificable offline, autocontenido; tokens más grandes.
2. **Permisos solo en base de datos** — tokens chicos; una consulta extra por request y sin verificación offline.
3. **Macaroons/Biscuit** — atenuación elegante; ecosistema inmaduro, curva para auditores.

## Decisión

JWT con claim `cap` estructurado: `models` (operaciones por modelo), `limits` (montos por operación/día, tasa de escritura, `record_domain`), `requires_approval`, `deny`. Claim `act` (RFC 8693) conserva la cadena de delegación. Regla de intersección estricta: `permisos_efectivos = permisos_usuario ∩ cap_agente ∩ record_rules` — `cap` **nunca amplía** lo que el delegante tiene. El PDP evalúa `cap` antes de tocar el ORM; denegación por defecto. Límites acumulados (monto/día) usan Redis con ventana deslizante — único punto con estado.

## Consecuencias

- Positivas: autorización de agente auditable y verificable offline; revocación vía `jti` + exp cortos.
- Negativas: tokens grandes (~2-4 KB); rotación de límites requiere reemisión.
- Invalidaría: necesidad de atenuación dinámica en cascada (revisar Biscuit).
