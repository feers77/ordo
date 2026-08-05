# F12-02c — El ticket mueve stock y la devolución lo devuelve (diseño)

Tercer tramo del módulo `pos`. Continúa [F12-02b](F12-02b-pos-ticket.md).

## Un picking por ticket

`action_validate` valida el picking de salida en la misma operación que asienta.
No se agregan los movimientos al cierre del turno: si se agregaran, la bodega
mentiría durante ocho horas y la alerta de reposición llegaría cuando ya no
queda nada que reponer.

La mercadería sale de la **sala de ventas de esa caja** (`pos.config.location_id`),
no de la bodega central. Con dos ubicaciones internas vivas, elegir en silencio
sería exactamente el defecto que arregló F12-00 (`STOCK_LOCATION_AMBIGUOUS`).

`modules/pos/fulfillment.py` copia el patrón de `modules/stock/fulfillment.py`
en vez de reusarlo: `pos` conoce a `stock` y nunca al revés, y unas líneas
repetidas cuestan menos que invertir la flecha de dependencia.

Un ticket solo de servicios no mueve nada y devuelve `None`. No es un error: es
una venta sin bodega.

## La devolución

Es un **documento nuevo**, con `refund_of_id`. El ticket original no cambia de
estado — igual que la nota de crédito no toca la factura— y el asiento
contabilizado no se modifica: se revierte (AGENTS.md §2.6).

`action_reverse` deja la reversión en borrador, que es lo correcto para una nota
de crédito que alguien puede querer revisar. Aquí **se contabiliza en el acto**:
la plata ya salió del cajón, y un asiento en borrador dejaría los libros atrás
de la realidad hasta que alguien se acuerde.

Lleva `requires_approval`. Devolver es sacar plata del cajón contra mercadería
que vuelve; es la operación que un cajero no debería poder hacer solo.

### Al costo con que salió, no al promedio de hoy

Si entre la venta y la devolución llegó un lote más caro, valorizar la entrada
al promedio nuevo **infla el inventario y regala margen**; si llegó uno más
barato, lo desinfla. El costo correcto está en la capa de valorización que
generó la salida original, y de ahí se lee (`POS_REFUND_NO_LAYER` si no existe).

### La devolución entra en el turno abierto ahora

No en el de la venta. Si entrara en el turno original, un arqueo ya cerrado y
firmado cambiaría de resultado después. Los cobros del original se replican en
negativo sobre el ticket de devolución: no los usa la contabilidad —eso lo
resuelve la reversión— sino el arqueo, que debe esperar menos efectivo porque
salió del cajón.

### Alcance: devolución total

Parcial (devolver una de dos prendas) queda pendiente: `action_reverse` revierte
el asiento completo, y una devolución parcial necesita un asiento propio en vez
de una reversión. Se anota como deuda antes que fingir que está resuelto.

## Un defecto que apareció al construir esto

`StockService._validate_one` elegía la contrapartida de toda entrada como
"recepciones por facturar", salvo desde ajuste de inventario. Para una
devolución de cliente eso diría que **le debemos la mercadería a un proveedor**,
y es falso: vuelve de un cliente y lo que revierte es el costo de esa venta.
Ahora la contrapartida depende del origen: ajuste, cliente o proveedor. Hoy
nada más produce movimientos `customer → internal`, así que el cambio no afecta
a ningún flujo existente.

## Reportes

- `pos.session_summary` — el Z del turno: tickets, devoluciones, neto por
  impuesto, cobros por medio y el arqueo. Los cobros de las devoluciones vienen
  en negativo, así que cada medio queda neto.
- `pos.cash_differences` — histórico de diferencias por caja. Lista también los
  turnos cuadrados: la ausencia de diferencia es dato, y una caja que nunca
  descuadra ni en un peso también dice algo.
