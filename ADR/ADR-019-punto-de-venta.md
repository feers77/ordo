# ADR-019 — El punto de venta no pasa por la orden de venta

- **Estado:** propuesto
- **Fecha:** 2026-08-05
- **Decisores:** @feers77

## Contexto

Una tienda de ropa emite doscientos tickets al día, casi todos anónimos y
cobrados en el acto. Ya existen `sale.order` con su ciclo confirmar → facturar
→ entregar, y `einvoicing` con su máquina de estados. La pregunta es si el POS
reutiliza esa cadena o es un documento comercial propio.

## Opciones consideradas

1. **`pos.order` crea una `sale.order` y la recorre** — reutiliza todo. Pero son
   cinco llamadas por ticket (crear, confirmar, facturar, entregar, cobrar) y
   `AGENTS.md §6` exige que ningún caso de uso común pase de tres;
   `sale.order.partner_id` es `required` y el 90 % de los tickets es anónimo;
   `sale.order.action_invoice` ya declara `requires_approval=True`, o sea HITL
   por cada boleta; y quema numeración `SO/` por ticket, llenando de ruido el
   pipeline de ventas.
2. **Agregar los tickets del día en una `sale.order` por turno** — barato en
   documentos, pero rompe el 1:1 boleta↔asiento que `edi.document.move_id`
   asume, y deja sin documento que referenciar a la nota de crédito de un
   ticket individual.
3. **`pos.order` asienta y mueve stock por sí misma** — un modelo comercial más
   y reportes de venta que deben unir dos documentos.

## Decisión

Se elige la opción 3. El criterio dominante es el ritmo de la caja: cualquier
diseño que meta una aprobación humana o cinco llamadas entre el cliente y el
vuelto no se usa. `pos` depende de `account`, `stock` y `einvoicing`, nunca al
revés.

Dos consecuencias que se deciden aquí y no en el código:

**Un asiento por ticket, no uno por turno.** Cada boleta es un documento legal
con folio. Agregar al cierre dejaría `edi.document.move_id` colgando y haría
imposible acreditar un ticket suelto. Doscientos asientos al día es exactamente
para lo que existe la contabilidad. Lo único que asienta el cierre es la
diferencia de arqueo.

**El límite de monto lo pone el capability token, no `requires_approval`.**
Marcar `action_validate` como sujeta a aprobación mata el negocio: sería pedirle
permiso a la dueña por cada polera. El control ya existe donde corresponde —
`max_amount_per_op` y `max_amount_per_day` del cap, con `CAP_AMOUNT_EXCEEDED`—
y acota al cajero sin bloquear la caja. `requires_approval` se reserva para lo
que un cajero no debería poder hacer solo: **cerrar el turno** (ahí aparece el
faltante) y **emitir una devolución**.

## Consecuencias

- Positivas: vender es una llamada; el ticket anónimo es el caso normal y no la
  excepción; la nota de crédito de un ticket tiene su documento; el arqueo
  queda asentado en el acto en vez de ser una planilla aparte.
- Negativas / deuda asumida: los reportes de venta consolidados deben unir
  `sale.order` y `pos.order`. Es el costo real de la decisión y se paga en el
  reporte, no en la caja. Además el motor de asientos de `account/invoicing.py`
  necesita partirse para admitir varias contrapartidas: un ticket se cobra con
  efectivo *y* tarjeta, y hoy `build_invoice_lines` inserta una sola.
- Qué invalidaría esta decisión: que el POS deje de ser mostrador y pase a ser
  venta con crédito, despacho diferido y cliente identificado siempre. Eso ya
  es una orden de venta y debería usar `sale.order`.
