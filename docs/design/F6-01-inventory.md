# F6-01 — Inventario: stock con valorización y asientos automáticos (diseño)

El inventario es donde el ERP miente más fácil: existencias que no cuadran
con la contabilidad, costos que nadie sabe de dónde salieron. La regla aquí
es la de siempre: el stock se mueve con asiento o no se mueve, y el valor
contable del inventario es la suma exacta de sus capas de valorización.

## Módulos

- **`product`** (depende de `base`, `account`): `product.product` — nombre,
  `product_type` ∈ {consu (almacenable), service}, unidad de medida, precio
  de venta de lista, **costo promedio** (`cost`, lo mantiene el sistema),
  `tracking` ∈ {none, lot, serial}, cuentas opcionales de ingreso/gasto.
- **`stock`** (depende de `base`, `product`, `account`):
  - `stock.warehouse` y `stock.location` (`location_type` ∈ {internal,
    supplier, customer, inventory_loss}). Las ubicaciones virtuales
    (supplier/customer/loss) son el "afuera" contra el que se mueve todo.
  - `stock.lot` — lote o serie por producto.
  - `stock.picking` — la orden de movimiento (in/out/internal), con estado
    `draft → done / cancelled`. Un picking hecho es historia: no se edita.
  - `stock.move` — línea de picking: producto, cantidad (string decimal),
    origen, destino, lote opcional, costo unitario al validarse.
  - `stock.valuation.layer` — cada entrada/salida valorizada deja una capa
    (cantidad, costo unitario, valor firmado). La suma de capas por producto
    ES su valor contable; no hay otro número.
  - `stock.config` — cuentas por compañía: `valuation_account_id`
    (inventario), `input_account_id` (recepciones por facturar),
    `cogs_account_id` (costo de venta), `loss_account_id` (ajustes).
  - `stock.reorder.rule` — mínimo/máximo por producto+ubicación.

## Existencias

`on_hand(product, location)` = Σ entradas − Σ salidas de movimientos
`done`. Sin caché de quants en v1: correcto primero, rápido después (los
índices por producto+ubicación aguantan hasta que duela y se mida).

## Valorización: costo promedio

- **Recepción** (supplier → internal) a costo `p`, cantidad `q`:
  capa `+q x p`; costo promedio nuevo = `(on_handxavg + qxp) / (on_hand+q)`;
  asiento: debe inventario / haber recepciones por facturar.
- **Entrega** (internal → customer), cantidad `q`: capa `−q x avg`;
  asiento: debe costo de venta / haber inventario.
- **Ajuste/merma** (internal → inventory_loss): igual que entrega pero
  contra la cuenta de ajustes; el sentido inverso repone a costo promedio.
- Los servicios no se valorizan ni mueven stock.

Invariantes (con test): el promedio queda siempre entre el mínimo y el
máximo de los precios recibidos; `Σ capas.valor` de un producto ==
`on_hand x avg` (redondeo mediante); un picking `done` no se modifica; no
se entrega más de lo que hay (`STOCK_INSUFFICIENT`), salvo ubicaciones
virtuales.

## Transiciones y acciones

`action_validate` (picking): verifica disponibilidad, marca moves `done`
con su costo, escribe capas, actualiza promedio y contabiliza el asiento en
la misma operación. `action_cancel` solo en borrador. Todo expuesto como
acciones (`requires_approval` en validar salidas… no: validar entrega es
operación diaria; aprobación queda para el ajuste de inventario, que es
donde se roba).

## Qué NO entra en el primer tramo

- FIFO y costo estándar (el promedio es el default; la capa ya guarda todo
  lo necesario para FIFO después).
- Rutas multi-etapa, dropshipping, consignación, barcode.
- Reserva de stock (hoy: validación al momento de mover).
- Integración con ventas/compras y reabastecimiento: segundo tramo (F6-02).
