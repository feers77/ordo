## Qué hace este PR

<!-- Una unidad lógica. Si supera ~600 líneas de diff útil, dividir. -->

## Checklist (Definition of Done — AGENTS.md §3)

- [ ] Tests unitarios/integración pasando y cobertura ≥ 85 % de la lógica nueva
- [ ] Sin secretos, sin código copiado de otro ERP copyleft
- [ ] Endpoints documentados en OpenAPI (baseline regenerado si aplica: `uv run python tools/export_openapi.py`)
- [ ] Errores con código estable
- [ ] `dry_run` + idempotencia si escribe; evento outbox si cambia estado de negocio
- [ ] CHANGELOG.md actualizado

## ADR relacionado

<!-- ADR-NNN o "no aplica" -->
