# ADR-003 — Estrategia IAM: Keycloak ahora, propio después

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Un OpenID Provider correcto toma meses; lo necesitamos desde F1. Pero el modelo de agentes (delegación, capabilities) excede lo que da cualquier IdP comercial.

## Opciones consideradas

1. **Keycloak como OP + capa de autorización propia** — desbloquea ya; dos piezas hasta F3.
2. **OP propio desde el día 1** — control total; 3 meses de riesgo criptográfico/protocolo.
3. **IdP SaaS (Auth0/Cognito)** — rápido; lock-in, sin token exchange flexible, costo por usuario.

## Decisión

F0–F2: Keycloak 26 como OP detrás de interfaz OIDC estándar. `ordo-iam` nace como Authorization Layer (capability tokens, delegación, políticas, PDP) delegando autenticación a Keycloak. F3+: `ordo-iam` absorbe la autenticación (Authlib). Todo el sistema habla solo OIDC estándar; el reemplazo es transparente. **Prohibido** escribir un OP desde cero antes de F3 (CLAUDE.md §2.2).

## Consecuencias

- Positivas: F1 arranca ya; riesgo criptográfico diferido y acotado.
- Negativas: operar Keycloak (RAM, upgrades) mientras tanto; doble emisor temporal.
- Invalidaría: que Keycloak soporte nativamente capability tokens con intersección de permisos (improbable).
