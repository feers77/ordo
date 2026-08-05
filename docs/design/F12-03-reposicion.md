# F12-03 — Reposición de bodega a tienda (diseño)

`stock.reorder.rule` existía y avisaba. Avisar sin poder actuar deja el trabajo
real —elegir el origen, calcular la cantidad, redondear a caja completa, crear
el picking— en manos de quien lea la alerta, que es exactamente lo que un ERP
debería hacer por él.

## La regla dice desde dónde

Faltaba el dato central: **en retail el 90 % de la reposición es un traslado
bodega→tienda, no una compra**. La regla gana `route` (`internal` | `buy`),
`source_location_id`, `supplier_id` y `multiple_quantity`.

## Dos acciones, una por ruta

| Acción | Módulo | Qué hace | Aprobación |
|---|---|---|---|
| `stock.reorder.rule.action_replenish` | `stock` | Crea y **valida** el traslado interno | no |
| `stock.reorder.rule.action_replenish_buy` | `purchase` | Crea la orden de compra **en borrador** | no |

Están separadas porque comprar es crear una `purchase.order`, y `stock` no
puede depender de `purchase` sin invertir la flecha —`purchase` ya depende de
`stock`—. Para que la separación no sea un enredo para quien consume la API,
cada línea del reporte trae `suggested_action` con la que le corresponde.

Ninguna lleva aprobación: un traslado interno no cambia el valor del inventario
ni saca nada de la compañía, y la orden de compra queda en borrador con su
propio `action_confirm`. **Proponer no es comprometer.**

## La cantidad

```
si stock >= mínimo:  0
si no:               (máximo - stock), redondeado hacia arriba al múltiplo
```

Dos decisiones dentro de esa fórmula:

- **Se dispara bajo el mínimo, no bajo el máximo.** Reponer en cada venta
  llenaría la bodega de traslados de una unidad.
- **El múltiplo redondea hacia arriba.** Si el proveedor vende cajas de 12 y
  faltan 13, se piden 24: quedarse corto es el error caro.

Vive en `modules/stock/replenishment.py`, sin base de datos, y se prueba como
propiedades: nunca negativa, después de reponer el stock nunca queda bajo el
mínimo —si quedara, la alerta se volvería a disparar sola—, sobre el mínimo no
pide nada, el resultado siempre es múltiplo, y redondear nunca queda corto.

## La alerta, por variante

"Quedan 2 poleras" no sirve; "quedan 0 en talla M" sí. En moda, un modelo con
stock suficiente en total puede estar agotado justo en la talla que se vende.
`stock.reorder_alerts` agrupa por modelo en `by_template` y desglosa por
variante, conservando la lista plana en `alerts` para quien ya la consumía.

Cada alerta trae `can_replenish`: falso significa que la acción **fallaría** —o
no hay origen declarado, o el origen tampoco tiene—. Decirlo en el reporte evita
el intento inútil.

`stock.replenishment_plan` es el plan completo de una ubicación en una llamada,
partido en `ready` y `blocked`. Los bloqueados se listan aparte y **no se
ocultan**: una línea bloqueada es trabajo que alguien tiene que resolver, no
ruido que convenga esconder.

## Reglas en lote

`product.template.action_apply_reorder_rules` propaga los niveles a todas las
variantes del modelo. Diez modelos por seis variantes son sesenta reglas: a mano
es inviable, y una tienda que no las crea se queda sin la mitad de las tallas
sin enterarse. Es idempotente: vuelve a aplicarse para cambiar los niveles y
actualiza en vez de duplicar.

## Un detalle de contrato

`STOCK_REPLENISH_NOT_NEEDED` es el único código de inventario marcado como
**retryable**: depende del stock del momento, y el mismo intento puede tener
sentido media hora después. El resto describe datos, y reintentarlos igual
vuelve a fallar.
