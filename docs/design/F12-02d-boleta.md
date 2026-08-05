# F12-02d — La boleta electrónica del ticket (diseño)

Cuarto y último tramo del POS. Continúa [F12-02c](F12-02c-pos-stock.md).

## El puente vive en `pos`

`modules/pos/einvoicing.py` es el espejo de `modules/einvoicing/bridge.py`. Vive
en `pos` y no en `einvoicing` porque la flecha apunta de lo específico a lo
genérico: el punto de venta conoce el framework de documentos electrónicos, y el
framework no debe enterarse nunca de que existe una caja.

## El receptor: la única diferencia real con una factura

En retail el 90 % de los tickets es anónimo, y la boleta se emite igual. El
receptor se resuelve en tres pasos: el cliente identificado del ticket; si no
hay, el contacto genérico de la caja (`pos.config.anonymous_partner_id`); si
tampoco, el identificador que la autoridad reserva para consumidor final —
`66666666-6` en Chile, que además pasa la validación de dígito verificador del
adaptador—.

## La devolución no emite otra boleta

Emitir un 39 por una devolución **sumaría venta en vez de restarla**. El tipo
correcto es la nota de crédito del país (61 en Chile, 5 en Paraguay), y se toma
del mismo mapa que ya usa la nota de crédito de una orden de venta.

La nota de crédito referencia al documento original como `tipo/folio`. Si el
ticket original nunca se boleteó no hay a qué referirse: se avisa con
`EDI_REFERENCE_MISSING` en vez de emitir una referencia inventada, que la
autoridad rechazaría de todos modos.

## Lo que cambia en el pack chileno

Dos cosas estructurales, y solo esas:

- **`IndServicio` en `IdDoc`.** El esquema del SII lo exige para boletas (39 y
  41) y la factura no lo lleva. El valor por defecto es 3 —boleta de venta y
  servicios, que es el caso de una tienda— y es configurable porque un local
  que solo presta servicios usa otro.
- **`EnvioBOLETA` en vez de `EnvioDTE`.** No es el mismo sobre ni el mismo
  destino: meter boletas en un `EnvioDTE` es un rechazo garantizado. El tipo de
  sobre pasa a ser un parámetro explícito, no algo que se deduzca por descuido.

El detalle con precio bruto no necesitó cambio: en una boleta el `price_unit`
del ticket ya viene con IVA incluido (`price_includes_tax` de la caja), y el
motor de impuestos con `price_include` deriva neto e IVA para la cabecera. La
misma función construye factura y boleta correctamente.

## Lo que NO hace esta entrega

Se declara explícitamente para que nadie construya sobre una promesa:

- **No envía al SII.** La máquina de estados ya tiene `sent`, `accepted`,
  `rejected` y `contingency` esperando; falta el transporte HTTP real y el
  ambiente de certificación.
- **No emite el RCOF** (consumo de folios diario, obligatorio para boletas). Es
  un job programado con su propio ciclo de reintento y pertenece a la fase de
  localización, no a la primera entrega del POS.
- **No genera representación impresa** (el voucher con su PDF417).

## Advertencia vigente

El pack fiscal chileno **sigue en borrador**. La estructura del XML se apoya en
el esquema publicado del SII, pero nada de esto está certificado en el ambiente
de pruebas de la autoridad ni revisado por un contador. No se use para declarar
impuestos hasta que ambas cosas ocurran.
