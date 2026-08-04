## Qué hace este PR

<!-- Una unidad lógica. Si supera ~600 líneas de diff útil, dividir. -->

## Checklist (Definition of Done — CLAUDE.md §3)

- [ ] Tests unitarios/integración pasando y cobertura ≥ 85 % de la lógica nueva
- [ ] Sin secretos, sin código copiado de Odoo
- [ ] Endpoints documentados en OpenAPI (baseline regenerado si aplica: `uv run python tools/export_openapi.py`)
- [ ] Errores con código estable
- [ ] `dry_run` + idempotencia si escribe; evento outbox si cambia estado de negocio
- [ ] CHANGELOG.md actualizado

## ADR relacionado

<!-- ADR-NNN o "no aplica" -->
