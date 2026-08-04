# ADR-005 — Campos dinámicos: JSONB vs. DDL

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

`studio-api` permite a clientes (y agentes) agregar campos en runtime. DDL en caliente toma locks y fragmenta el schema multi-tenant.

## Opciones consideradas

1. **Columna JSONB `x_custom` + índices de expresión** — sin DDL, sin locks; tipado más débil.
2. **`ALTER TABLE` por campo** — tipado nativo; locks, migraciones por tenant, catálogo explosivo.
3. **Tabla EAV** — flexible; queries ilegibles y rendimiento pobre (el error clásico).

## Decisión

Campos dinámicos en JSONB `x_custom` por tabla, con validación de tipos en el registry (Pydantic) e índices GIN/expresión creados según uso. Materialización opcional a columna real vía migración controlada y explícita cuando un campo se vuelve crítico (volumen de queries o necesidad de FK). EAV prohibido.

## Consecuencias

- Positivas: extensión instantánea y segura por agentes; cero locks.
- Negativas: constraint checking más débil hasta materializar; dominios sobre `x_custom` requieren soporte del compilador.
- Invalidaría: mayoría de campos custom terminando materializados (señal de que el modelo base quedó corto).
