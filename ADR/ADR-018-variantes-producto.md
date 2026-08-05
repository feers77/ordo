# ADR-018 — Variantes de producto: template agrupador sobre producto plano

- **Estado:** propuesto
- **Fecha:** 2026-08-05
- **Decisores:** @feers77

## Contexto

Una tienda de ropa no vende "polera oversize": vende una polera oversize
talla M roja. Hoy `product.product` es plano —sin atributos, sin variantes, sin
categorías— y `stock`, `sale` y `purchase` lo referencian por id. Cinco modelos
con datos vivos (`stock.move`, `stock.valuation.layer`, `stock.lot`,
`sale.order.line`, `stock.reorder.rule`) apuntan ahí. Cualquier diseño de
variantes que mueva ese punto de anclaje arrastra una migración del núcleo de
inventario.

## Opciones consideradas

1. **Atributos en JSON sobre `product.product`** — un campo
   `attributes = {"talla": "M", "color": "rojo"}`. Barato de escribir, pero el
   compilador de dominios no expone operadores jsonb: "todas las poleras rojas"
   se vuelve un scan en Python. Sin unicidad de combinación, sin catálogo de
   valores válidos y sin `read_group` por talla.
2. **`product.variant` delegando en `product.template` con `_inherits`** — el
   patrón clásico. Descartado por un hecho verificado en el código, no por
   gusto: la delegación está implementada **solo a nivel de metadatos**.
   `Registry._resolve_delegation` clona los campos del padre dentro del hijo,
   pero ni `recordset.py` ni el compilador conocen `delegated_from`, y el
   instalador crea columnas físicas duplicadas en la tabla hija. El nombre se
   escribiría en la variante y se leería de la variante, no del template:
   corrupción silenciosa, que es peor que un error.
3. **Convertir `product.product` en template y crear un modelo de variante
   nuevo** — el diseño "correcto de libro", al costo de migrar los cinco
   modelos citados y reescribir `StockService`, `fulfillment.py` y el reporte
   `stock.on_hand`.
4. **Template como agrupador, `product.product` sigue siendo la variante** —
   `product.template` es un modelo nuevo y `product.product` gana un
   `template_id` nullable.

## Decisión

Se elige la opción 4. El criterio dominante es que `product.product` **ya es**
la unidad vendible y almacenable, y cada talla-color debe tener su propio stock
y su propio costo promedio: una polera M roja y una XL negra se compraron en
lotes distintos y no valen lo mismo. Con este diseño el diff en `stock/`,
`sale/` y `purchase/` es cero, y `template_id = NULL` mantiene funcionando a
los productos sin variantes y a los servicios.

Queda además registrado que **`_inherits` no se usa en este repositorio**
mientras la delegación siga siendo solo metadatos. Se escribe aquí para que
nadie lo intente dentro de seis meses creyendo que funciona.

Nota de forma: el kernel no tiene `Many2many` almacenado (`fields.py` fuerza
`store=False`). El eje de la matriz se guarda como un `Char` de ids separados
por coma en `product.template.attribute.line` —es configuración, se lee entera
siempre— mientras que la pertenencia de la variante usa un modelo real,
`product.variant.value`, porque eso sí hay que filtrarlo.

## Consecuencias

- Positivas: cero migración del inventario; costo promedio y existencias por
  variante desde el primer día; `read_group` por talla o color funciona con la
  maquinaria de agregación existente; el catálogo plano actual sigue válido.
- Negativas / deuda asumida: los datos comunes (nombre, precio de lista, tipo)
  se **copian** del template a cada variante al generarla en vez de leerse por
  referencia, así que cambiar el nombre del modelo no renombra sus variantes
  solo. Es una copia deliberada: mientras la delegación no exista de verdad, la
  alternativa es un campo `related` por cada dato común, que el kernel resuelve
  como compute no almacenado y que dejaría el catálogo sin poder filtrarse por
  nombre en SQL. Se resuelve con una acción de propagación cuando duela.
- Qué invalidaría esta decisión: que `_inherits` se implemente de verdad en el
  runtime (lectura y escritura resolviendo contra la tabla del padre, e
  instalador sin columnas duplicadas). En ese momento conviene reevaluar la
  opción 2 para los campos comunes, sin tocar el anclaje de la variante.
