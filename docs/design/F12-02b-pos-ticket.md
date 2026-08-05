# F12-02b — El ticket: cobro y asiento (diseño)

Segundo tramo del módulo `pos`. Continúa [F12-02](F12-02-pos-turno.md); la
decisión de fondo está en [ADR-019](../../ADR/ADR-019-punto-de-venta.md).

## Una acción cobra y asienta

`pos.order.action_validate` fija los totales, asigna el número y **contabiliza**
el asiento de una vez. No existe el ticket cobrado cuyo asiento sigue pendiente:
en una caja que emite doscientos al día, ese estado intermedio es donde se pierde
la plata.

| De | Acción | A | Aprobación |
|---|---|---|---|
| `draft` | `action_validate` | `paid` | **no** |
| `draft` | `action_cancel` | `cancelled` | no |
| `paid` | — | — | un ticket cobrado se corrige con una devolución (F12-02c) |

`action_validate` **no** lleva aprobación, y eso es una decisión de negocio, no
un olvido: pedirle permiso a la dueña por cada polera mataría la caja. El límite
por venta ya vive en `max_amount_per_op` del capability token del cajero.

## El asiento de un cobro mixto

Un ticket de $23.800 con IVA incluido, cobrado $10.000 en efectivo y $13.800 con
tarjeta:

```
Caja                       10.000
Deudores por tarjetas      13.800
    Ventas                          20.000
    IVA débito fiscal                3.800
```

Esto obligó a partir `build_invoice_lines` en `modules/account/invoicing.py`. El
motor sabía poner **una** contrapartida —la cuenta por cobrar de una factura— y
un ticket tiene tantas como medios de cobro. Ahora `build_revenue_lines` devuelve
las partidas de ingreso e impuestos junto con **cuánto debe sumar la
contrapartida**, y quien llama decide en cuántas partidas la parte. La factura de
siempre sigue poniendo una sola; hay un test que lo comprueba, porque un refactor
del motor de asientos que cambie el comportamiento de las facturas es la peor
clase de regresión.

## El vuelto no es un cobro

Se paga con $30.000 un ticket de $23.800. A caja entran **$23.800**, no $30.000:
los $6.200 salen del cajón en el acto. El asiento debita la cuenta de efectivo
por lo que la tienda se queda, y el vuelto se descuenta de las cuentas de
efectivo, nunca de las de tarjeta — `validate_payments` ya garantizó que el
efectivo alcanza para darlo.

## Dos redes contra el ticket duplicado

La primera es `Idempotency-Key`, que ya da la API. La segunda es
`pos.order.terminal_ref`, indexada: **la clave de idempotencia se pierde con el
corte de red y la referencia del terminal no**. Si el terminal reintenta con una
clave nueva, `POS_DUPLICATE_TERMINAL_REF` devuelve el id del ticket que ya
existe, para que el cajero lo recupere en vez de cobrar dos veces.

## El turno cierra sus costuras

Las dos costuras que F12-02 dejó declaradas ya leen datos:

- `_cash_movements` suma los cobros en **efectivo** de los tickets cobrados y
  resta los vueltos. La tarjeta no entra: no pasa por el cajón, y contarla haría
  aparecer un faltante que no existe.
- `_refuse_pending_tickets` impide cerrar con tickets en borrador. Cerrar
  dejándolos vivos los deja huérfanos: ya no se pueden cobrar —el turno no está
  abierto— ni entraron nunca en el arqueo.

El arqueo completo queda: `fondo + efectivo cobrado - vueltos - retiros`.

## Simulación

`dry_run` sobre `action_validate` devuelve el número y el asiento que *haría*,
sin gastar el folio ni el número de ticket. Ambas secuencias son sin huecos y el
hueco sería permanente. Hay un test que valida de verdad después y comprueba que
el número sigue siendo el mismo.
