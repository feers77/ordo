# ADR-011 — Dependencias del servicio IAM

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

F1 implementa `ordo-iam` (principals, bridge OIDC, token exchange, capability tokens, PDP). Requiere acceso a Postgres, migraciones y criptografía JOSE. AGENTS.md §2.7 exige ADR para dependencias nuevas.

## Opciones consideradas

1. **SQLAlchemy 2.0 + asyncpg + Alembic + joserfc + httpx** — ya sancionados por ADR-001 (ORM/migraciones); joserfc es la librería JOSE moderna del autor de Authlib (tipada, RFC 7515-7523, mantiene camino a Authlib en F3); httpx para hablar con Keycloak.
2. **PyJWT** — ubicua pero API limitada para JWKS/EdDSA y sin camino natural a Authlib.
3. **python-jose** — sin mantenimiento activo. Descartada.

## Decisión

`sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic`, `joserfc`, `httpx`, `python-multipart` (forms RFC 8693) y `redis` (contadores de límites diarios) como dependencias de `ordo-iam`. Ninguna otra librería de crypto/auth sin nuevo ADR.

**Actualización 2026-08-04:** `ordo-iam` depende además de `ordo-core` (paquete interno del workspace, sin dependencias externas nuevas) para usar la única implementación de la cola de jobs. La copia local `ordo_iam/jobs.py` reimplementaba el contrato de `ordo_core.services.jobs` y las dos podían divergir; era deuda registrada desde F1.7.

## Consecuencias

- Positivas: stack alineado con ADR-001/003; migración a Authlib (F3) sin cambiar formato de tokens.
- Negativas: joserfc menos conocida que PyJWT por los LLMs.
- Invalidaría: abandono de mantenimiento de joserfc.
