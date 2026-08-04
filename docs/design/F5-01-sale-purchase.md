# F5-01 — Ventas y compras (diseño)

Primer tramo de F5: órdenes de venta y de compra cuyo ciclo termina en un
asiento contable generado automáticamente. Sin asiento automático, "confirmar
una venta" es una promesa; con él, es contabilidad.

## Modelos

- `account.tax` (en `account`) — el impuesto como registro, no solo como
  dataclass del motor: `code`, `name`, `rate` (string decimal), `tax_type`,
  `price_include`, `is_withholding`, `applies_to` ∈ {sale, purchase, both},
  `account_id` (cuenta donde se acumula el impuesto), `company_id`. Los packs
  de localización son la fuente de los datos; este modelo los hace operables
  y les cuelga la cuenta contable, que es decisión de cada empresa.
- `account.settings` (en `account`) — configuración contable por compañía:
  `receivable_account_id`, `payable_account_id`. Sin esto no se puede asentar
  una factura; faltar es `ACCOUNT_SETTINGS_MISSING`.
- `sale.order` — `name` (secuencia al confirmar), `partner_id`, `date_order`,
  `currency_id`, `journal_id`, `state` ∈ {draft, confirmed, invoiced,
  cancelled}, `amount_untaxed`, `amount_tax`, `amount_total`,
  `invoice_move_id`, `company_id`.
- `sale.order.line` — `order_id`, `name`, `quantity`, `price_unit`,
  `discount_percent`, `tax_codes` (códigos separados por coma, resueltos
  contra `account.tax`), `income_account_id` (opcional; cae al
  `default_account_id` del diario).
- `purchase.order` / `purchase.order.line` — espejo de venta:
  `vendor_ref` en lugar de folio propio, `expense_account_id` en la línea.

## Transiciones

```
sale.order:     draft → confirmed → invoiced        draft|confirmed → cancelled
purchase.order: draft → confirmed → billed          draft|confirmed → cancelled
```

`action_confirm` valida líneas, resuelve impuestos y fija los totales con el
motor de F4-02 (redondeo por línea, `Decimal` siempre). `action_invoice` /
`action_bill` genera el asiento y lo contabiliza en la misma transacción.

## El asiento automático

Venta (factura cliente):

| Cuenta | Debe | Haber |
|---|---|---|
| Por cobrar (settings) | total con impuestos − retenciones | |
| Retención (por impuesto retenido) | importe | |
| Ingreso (por línea) | | base de la línea |
| Impuesto (cuenta del `account.tax`) | | importe del impuesto |

Compra: espejo — por pagar al haber, gasto e IVA crédito al debe. El asiento
sale del `AccountingService` existente: los invariantes de partida doble ya
están probados con property-based testing y aquí solo se construyen líneas
que cuadran por construcción.

## Errores

`SALE_ORDER_EMPTY`, `SALE_INVALID_TRANSITION`, `SALE_TAX_UNKNOWN`,
`SALE_NO_INCOME_ACCOUNT`, `ACCOUNT_SETTINGS_MISSING`, y sus espejos
`PURCHASE_*`.

## Qué NO entra aquí

- Listas de precios, promociones, equipos de venta (resto de F5).
- Stock: la guía de despacho y la valorización llegan con F6.
- Pagos y conciliación (F4 restante).
