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

**AGPLv3** ([`LICENSE`](LICENSE)). Puedes usar ORDO gratis, también comercialmente,
y modificarlo. Si lo modificas y lo despliegas —incluso si solo lo ofreces por red,
como SaaS— debes publicar el código de tus cambios bajo la misma licencia.

La razón está en [`ADR/ADR-010-licencia.md`](ADR/ADR-010-licencia.md): queremos que
las mejoras vuelvan al proyecto, no que alguien cierre el producto y lo revenda.

Para contribuir, ver [`CONTRIBUTING.md`](CONTRIBUTING.md): DCO en cada commit
(`git commit -s`) y el [CLA](CLA.md) una sola vez, en tu primer PR. El CLA **no cede
copyright**; otorga una licencia amplia al proyecto.

> ORDO no está afiliado a Odoo S.A. "Odoo" es marca registrada de su titular; ORDO
> reimplementa comportamiento observable, sin copiar su código.
