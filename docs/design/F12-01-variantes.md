# F12-01 — Catálogo con variantes (diseño)

Una tienda de ropa no vende "polera oversize": vende una polera oversize talla M
roja. Este documento define el catálogo que lo permite. La decisión estructural
—y por qué no se usa `_inherits`— está en [ADR-018](../../ADR/ADR-018-variantes-producto.md).

## Modelo de datos

```
product.category ─┐
                  ├─ product.template ─ product.template.attribute.line ─ product.attribute
                  │         │                                                    │
                  │         └─(genera)─► product.product ─ product.variant.value ─┴─ product.attribute.value
                  └────────────────────────────┘
```

| Modelo | Qué es | Cardinalidad típica |
|---|---|---|
| `product.category` | Familia comercial, con árbol por `parent_id` | decenas |
| `product.attribute` | Talla, Color | 2–4 por vertical |
| `product.attribute.value` | S, M, L / Rojo, Negro | 5–20 por atributo |
| `product.template` | El modelo del que cuelgan las variantes | cientos |
| `product.template.attribute.line` | Un eje de la matriz de un modelo | 1–3 por modelo |
| `product.product` | **La variante**: lo que se vende, se cuenta y se valoriza | miles |
| `product.variant.value` | Qué valor toma la variante en cada atributo | 1–3 por variante |

`product.product` gana tres campos: `template_id` (nullable — vacío es producto
sin variantes), `variant_label` ("M / Rojo", compuesto por el sistema) y
`category_id`.

## Por qué la variante es `product.product` y no un modelo nuevo

Porque `stock.move`, `stock.valuation.layer`, `stock.lot`, `sale.order.line` y
`stock.reorder.rule` ya apuntan ahí. Mover el anclaje obliga a migrar cinco
modelos con datos vivos y a reescribir `StockService`, `fulfillment.py` y el
reporte `stock.on_hand`. Con este diseño el diff en `stock/`, `sale/` y
`purchase/` es exactamente cero.

Y es lo correcto además del lado contable: cada talla-color mantiene su propio
costo promedio, porque se compró en un lote distinto y no vale lo mismo.

## El eje de la matriz es un `Char` de ids

`product.template.attribute.line.value_ids` guarda `"3,4,5"`. Feo pero honesto:
el kernel no tiene `Many2many` almacenado (`fields.py` fuerza `store=False`), y
la alternativa —un modelo puente— agrega un modelo entero para un dato de
configuración que siempre se lee completo.

La pertenencia de la variante **sí** usa un modelo real, `product.variant.value`,
porque eso hay que filtrarlo: "qué queda en talla M" es
`[("attribute_id", "=", talla), ("value_id", "=", m)]` sobre ese modelo, resuelto
en SQL y agregable con `read_group`. Es la diferencia entre configuración y dato
consultable.

## Datos comunes: se copian, no se referencian

Al generar una variante, `name`, `product_type`, `list_price`, `tracking`,
`uom_id` y las cuentas se **copian** del template. No son `related`: el kernel
resuelve `related` como compute no almacenado, y un catálogo cuyo nombre no vive
en una columna no se puede filtrar ni ordenar en SQL — con 600 SKUs eso es la
diferencia entre una búsqueda en el punto de venta y un scan.

El costo es que renombrar el modelo no renombra sus variantes. Es deuda
consciente, anotada en el ADR: se paga con una acción de propagación cuando
duela, no con un diseño que rompe la búsqueda desde el primer día.

## Qué entra en este documento y qué no

Esto es el **catálogo**: modelos, campos y permisos. La generación de la matriz
(el producto cartesiano, la composición del SKU y del `variant_label`, la
idempotencia al regenerar, el archivado con stock) es F12-01b y llega con sus
acciones, sus errores `PRODUCT_*` y el reporte `product.variant_matrix`.

## Permisos

El catálogo lo mantiene `inventario`; `ventas` y `compras` leen. La matriz de
variantes es configuración, no operación: quien la toca decide qué existe en la
bodega, así que `product.template.attribute.line` solo lo escribe `inventario`.

## Compatibilidad

`template_id` nullable: los productos planos actuales y todos los servicios
siguen exactamente igual. Los tenants ya instalados reciben las columnas nuevas
con `make upgrade-tenant`, que re-ejecuta `create_tables` de los módulos
instalados; por eso `product` sube a `0.2.0`, para dejar rastro en `ir_module`.
