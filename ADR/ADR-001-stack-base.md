# ADR-001 — Lenguaje y stack base

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Necesitamos un stack para un ERP API-first operado por agentes, con cobertura funcional amplia y localizaciones fiscales complejas. El kernel debe generar SQL desde un lenguaje de dominios y servir OpenAPI introspectable.

## Opciones consideradas

1. **Python 3.12 + FastAPI + SQLAlchemy 2.0 + Postgres 17** — semántica cercana a la de los ERP libres de referencia, ecosistema fiscal maduro, OpenAPI nativo; menor rendimiento bruto.
2. **Go/Rust** — rendimiento superior; portar lógica contable/fiscal mucho más lento, sin ventaja para generación dinámica de schemas.
3. **Node/TypeScript** — buen tooling API; ORM sin equivalente a SQLAlchemy Core para compilar dominios, ecosistema contable débil.

## Decisión

Python 3.12, FastAPI + Uvicorn, SQLAlchemy 2.0 async (Core para el compilador de dominios), Pydantic v2, PostgreSQL 17, Redis 7, NATS JetStream, MinIO, Alembic, OpenTelemetry. Hot paths se extraen a Go/Rust **solo** si las mediciones de SLO (§4.3 del plan) lo exigen, nunca antes de F2.

## Consecuencias

- Positivas: velocidad de desarrollo, facilidad para reimplementar comportamiento de ERP maduros, generación dinámica de schemas.
- Negativas: presupuesto de rendimiento más ajustado; SLO en CI obligatorio desde F2.
- Invalidaría: incumplimiento sistemático de SLO no resoluble con optimización local.
