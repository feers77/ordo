# ORDO ERP

ERP/CRM **API-first, sin frontend**, diseñado para ser operado por agentes de IA.

- Paridad funcional con Odoo Community (comportamiento reimplementado desde cero; ver política de licencias en `CLAUDE.md` §2).
- Equivalentes propios de las funciones Enterprise.
- Framework declarativo de localizaciones fiscales (primera ola: Chile).

## Documentos clave

| Doc | Qué contiene |
|---|---|
| [`PLAN-MAESTRO.md`](PLAN-MAESTRO.md) | Arquitectura, roadmap, decisiones estratégicas |
| [`FASE-0-BOOTSTRAP.md`](FASE-0-BOOTSTRAP.md) | Fase actual: bootstrap de infraestructura |
| [`CLAUDE.md`](CLAUDE.md) | Reglas de trabajo vinculantes (humanos y agentes) |
| [`ADR/`](ADR/) | Decisiones de arquitectura |

## Estado

**Fase 0 — Bootstrap.** Sin código de negocio todavía.

## Desarrollo

```bash
uv sync          # dependencias
make check       # lint + types + tests
make up          # stack local (docker compose)
```

## Licencia

Pendiente de definición formal en ADR-010 (Fase 0).
