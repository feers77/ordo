# F4-04 — Tesorería: pagos, conciliación, extractos y reportes (diseño)

Sin pagos, una factura queda "por cobrar" para siempre; sin conciliación,
nadie puede afirmar que se cobró; sin extracto, nadie puede probarlo contra
el banco. Esta pieza cierra ese circuito y agrega los reportes que lo hacen
visible.

## Modelos (en `account`)

- `account.payment` — cobro (`inbound`) o pago (`outbound`) contra un diario
  de banco o caja. `action_post` genera su asiento (banco contra la cuenta
  por cobrar/pagar de `account.settings`) y lo contabiliza. Un pago
  contabilizado no se anula: se revierte su asiento.
- `account.reconcile` — grupo de partidas conciliadas.
  `account.move.line.reconcile_id` apunta al grupo.
- `account.bank.statement` / `.line` — el extracto del banco con sus
  movimientos firmados (positivo entra, negativo sale) y el saldo inicial y
  final declarados por el banco.

## Conciliación

`ReconcileService.reconcile(line_ids)`: misma cuenta, cuenta marcada
`reconcile=true`, asientos contabilizados, ninguna partida ya conciliada y
**suma exactamente cero**. La conciliación parcial es un concepto futuro,
no un grupo que "casi" cuadra. `open_items()` lista las partidas abiertas
para que un agente decida qué saldar. `unreconcile` deshace el grupo entero.

## Extractos

El emparejamiento automático es conservador por diseño: una línea se
empareja solo cuando existe **exactamente un** candidato con el mismo
importe entre las partidas del banco sin usar. Dos candidatos idénticos =
la línea queda para decisión manual (`match_line`). `action_validate` exige
todo emparejado y `saldo_inicial + Σ movimientos = saldo_final`.

## Reportes (`ordo_core.reports`)

Registro simétrico al de acciones: los módulos declaran reportes en
`reports.py` y la API los sirve en `GET /api/v1/reports/{name}`.

- `account.trial_balance` — sumas y saldos por cuenta; `balanced` debe ser
  verdadero siempre (si no, el bug es del kernel).
- `account.income_statement` — ingresos menos gastos del período.
- `account.balance_sheet` — activo contra pasivo + patrimonio + resultado
  del ejercicio, con su propio check de cuadratura.

Importes como string decimal, como todo en la API.

## Acciones nuevas

| Modelo | Acción | Aprobación |
|---|---|---|
| `account.payment` | `action_post` | sí |
| `account.payment` | `action_cancel` | no |
| `account.move.line` | `action_reconcile` (params `with_line_ids`) | no |
| `account.reconcile` | `action_unreconcile` | sí |
| `account.bank.statement` | `action_auto_match` | no |
| `account.bank.statement` | `action_validate` | sí |

## Qué NO entra aquí

- Conciliación parcial y diferencias de cambio.
- Import de extractos (OFX/CSV/CAMT): llegará como parser aparte.
- F29, libros de compra/venta y formatos legales por país: motor de
  reportes legales declarativo (PLAN §6.2.2), sobre esta base.
