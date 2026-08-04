# F2-07 — Sistema de módulos nativos (diseño)

La base sobre la que se escribirán todos los módulos de negocio. Un módulo debe poder
declararse, instalarse, migrar su schema y extender modelos ajenos sin tocar el kernel.

## Anatomía

```
modules/sale/
├── manifest.yaml         nombre, versión, depends, descripción
├── __init__.py           expone MODULE
├── models.py             modelos y extensiones
├── migrations/           001_initial.sql, 002_....sql (por módulo, ordenadas)
└── tests/
```

`manifest.yaml`:
```yaml
name: sale
version: 1.0.0
summary: Órdenes de venta
depends: [base, partner]
category: Ventas
```

## Carga

`ModuleLoader.discover(paths)` lee los manifiestos, valida el grafo (ciclos y
dependencias faltantes fallan al arrancar, no en runtime) y devuelve `Module` del
registry en orden topológico. `Registry.build()` ya consume esa lista.

Reglas:
- Un módulo **no puede** declarar un modelo que ya definió otro con `_name`; para
  ampliarlo usa `_inherit`. Colisión ⇒ `MODULE_MODEL_CONFLICT`.
- Un módulo solo puede extender modelos de módulos que declara en `depends`; si no,
  `MODULE_UNDECLARED_DEPENDENCY`. Esto evita el acoplamiento invisible que en otros
  ERP hace que el orden de carga sea folclore.
- La versión sigue semver; `ModuleState` guarda qué versión está instalada por tenant.

## Instalación y migraciones por tenant

`ir_module` (por tenant): `name`, `version`, `state` ∈ {installed, to_upgrade},
`installed_at`. El instalador aplica en orden los `.sql` de `migrations/` que aún no
constan en `ir_module_migration`, dentro de una transacción por módulo: si una
migración falla, ese módulo no queda a medias.

Las tablas de los modelos las genera el kernel desde el registry (`create_tables`), así
que un módulo simple no necesita escribir DDL: solo declara modelos. Las migraciones
son para cambios que el generador no puede inferir (renombres, backfills, índices
especiales).

## Scaffolding

`make new-module NAME=sale` crea el esqueleto completo con manifiesto, modelo de
ejemplo con `agent_hint` y `examples`, test que ya pasa, y lo registra en el árbol.

## Tests (primero)

Descubrimiento desde disco; orden topológico; dependencia faltante y ciclo detectados;
colisión de `_name` rechazada; extender sin declarar dependencia rechazado; instalación
registra versión; migración se aplica una sola vez; migración fallida no deja el módulo
a medias; generación de tablas desde el registry; scaffolding produce un módulo válido.
