# ADR-006 — Lenguaje de dominios compatible con Odoo

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Necesitamos un lenguaje de filtrado para API, record rules y automatizaciones. Los LLMs ya conocen la sintaxis de dominios de Odoo por su corpus de entrenamiento; las migraciones desde Odoo la usan.

## Opciones consideradas

1. **Sintaxis de dominios Odoo (prefijo polaco + tuplas) con compilador propio** — conocimiento previo de agentes y migradores; sintaxis peculiar.
2. **Lenguaje propio (p.ej. estilo OData/JSON-API)** — más estándar web; cero ventaja de corpus, doble traducción en migraciones.
3. **SQL restringido** — expresivo; superficie de inyección y acoplamiento al storage.

## Decisión

Compatibilidad **sintáctica** con dominios Odoo: `[("state","=","sale"), "|", (...), (...)]`, rutas punteadas, `active_test`. Compilador propio (escrito desde cero, sin mirar código Odoo — CLAUDE.md §2.1) hacia SQLAlchemy Core con parámetros vinculados, que aplica record rules y filtro de tenant. Este archivo es zona de revisión humana obligatoria + property-based testing (Hypothesis) + tests de inyección.

## Consecuencias

- Positivas: agentes y herramientas Odoo funcionan sin aprender sintaxis nueva.
- Negativas: cargamos con las rarezas del formato (notación polaca); compilador es el componente de mayor riesgo del kernel.
- Invalidaría: divergencia semántica irreconciliable con operadores Odoo nuevos.
