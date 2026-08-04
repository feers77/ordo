# ADR-002 — Modelo de multi-tenancy

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Tenants de tamaños muy dispares; el aislamiento de datos entre tenants es el riesgo de seguridad número 1 del producto. El código de dominio no debe conocer el mecanismo.

## Opciones consideradas

1. **Schema-per-tenant + RLS** — aislamiento fuerte, backup/restore por tenant, migraciones por schema; más objetos en el catálogo.
2. **Fila con `tenant_id` compartido** — simple y elástico; un bug de filtro filtra datos entre tenants.
3. **DB dedicada por tenant** — aislamiento máximo; costo operativo inviable para tenants chicos.

## Decisión

Schema-per-tenant en Postgres con enrutamiento en el gateway, **más RLS como segunda barrera** (defensa en profundidad). Tenants grandes: base de datos dedicada con la misma abstracción (distinto DSN). Multi-company es una dimensión aparte dentro del tenant (`company_id` + `allowed_company_ids` en el `Environment`). Ambas capas se resuelven en el middleware; el código de dominio nunca escribe el nombre del schema.

## Nota de implementación (F2.1)

RLS **no aplica a roles `SUPERUSER` ni con `BYPASSRLS`**: conectarse con el rol dueño
de las tablas deja la segunda barrera inerte. Por eso existe el rol `ordo_app`
(`NOSUPERUSER NOBYPASSRLS`, creado en `infra/compose/postgres/init-app-role.sh`) y
`Environment.bind()` hace `SET LOCAL ROLE ordo_app` en cada transacción, de modo que
la política se aplique aunque el DSN traiga credenciales privilegiadas.
Además de ocultar filas ajenas, la política impide **escribirlas**.

## Consecuencias

- Positivas: fuga entre tenants requiere fallar dos capas; migración de tenant a DB dedicada sin tocar código.
- Negativas: migraciones N-schemas (herramienta propia en F2); catálogo grande con muchos tenants.
- Invalidaría: >5.000 tenants por cluster con degradación del catálogo.
