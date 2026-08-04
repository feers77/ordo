# ADR-006 — Lenguaje de dominios de sintaxis prefija

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Necesitamos un lenguaje de filtrado para API, record rules y automatizaciones. Los LLMs ya conocen esta sintaxis de dominios por su corpus de entrenamiento, y las migraciones desde otros ERP la usan.

## Opciones consideradas

1. **Sintaxis de dominios prefija (notación polaca + tuplas) con compilador propio** — conocimiento previo de agentes y migradores; sintaxis peculiar.
2. **Lenguaje propio (p.ej. estilo OData/JSON-API)** — más estándar web; cero ventaja de corpus, doble traducción en migraciones.
3. **SQL restringido** — expresivo; superficie de inyección y acoplamiento al storage.

## Decisión

Sintaxis prefija con tuplas: `[("state","=","sale"), "|", (...), (...)]`, rutas punteadas, `active_test`. Compilador propio (escrito desde cero, sin mirar código de terceros — AGENTS.md §2.1) hacia SQLAlchemy Core con parámetros vinculados, que aplica record rules y filtro de tenant. Este archivo es zona de revisión humana obligatoria + property-based testing (Hypothesis) + tests de inyección.

## Consecuencias

- Positivas: agentes y clientes existentes funcionan sin aprender sintaxis nueva.
- Negativas: cargamos con las rarezas del formato (notación polaca); compilador es el componente de mayor riesgo del kernel.
- Invalidaría: divergencia semántica irreconciliable con operadores nuevos de otros dialectos.
