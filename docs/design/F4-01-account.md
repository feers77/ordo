
# F4-01 — Motor contable (diseño)

La contabilidad es donde un error no se nota hasta el cierre y ya es tarde. Por eso
aquí los invariantes no son tests que acompañan al código: son la especificación.

## Modelos

- `account.account` — `code`, `name`, `account_type` ∈ {asset, liability, equity,
  income, expense}, `reconcile`, `currency_id`, `company_id`. El tipo determina el
  signo natural y a qué estado financiero pertenece.
- `account.journal` — `code`, `name`, `journal_type` ∈ {sale, purchase, cash, bank,
  general}, `sequence_code` (secuencia legal, `no_gap` para diarios fiscales),
  `default_account_id`.
- `account.move` — el asiento. `name` (número legal), `journal_id`, `date`, `ref`,
  `state` ∈ {draft, posted, cancel}, `move_type`, `company_id`, `currency_id`,
  `amount_total`.
- `account.move.line` — la partida. `move_id`, `account_id`, `partner_id`, `name`,
  `debit`, `credit`, `balance` (calculado), `tax_ids`, `date_maturity`,
  `reconciled`, `full_reconcile_id`.

## Invariantes (property-based, bloqueantes)

1. **Partida doble**: en todo asiento, `sum(debit) == sum(credit)`. Un asiento que
   no cuadra no se contabiliza; ni siquiera se guarda como borrador desbalanceado
   al confirmarlo.
2. **Débito y crédito nunca simultáneos** en una línea: uno de los dos es cero.
3. **Ambos no negativos**: un crédito negativo es un débito mal escrito.
4. **Inalterabilidad**: un asiento `posted` no se modifica ni se borra. Corregir es
   emitir un asiento de reversión (AGENTS.md §2.6).
5. **Numeración sin huecos**: el número se toma de una secuencia `no_gap` al
   contabilizar, no al crear el borrador. Un borrador descartado no consume número.
6. **Fecha dentro de período abierto**: contabilizar en un período cerrado es
   `ACCOUNT_PERIOD_LOCKED`.
7. **Moneda coherente**: todas las líneas comparten la moneda del asiento; los
   importes en otra moneda llevan su contravalor.
8. **Conciliación balanceada**: las líneas conciliadas entre sí suman cero.

## Transiciones

```
draft --action_post--> posted --action_reverse--> (nuevo asiento de reversión)
  |
  +--action_cancel--> cancel     posted NO vuelve a draft
```

`action_post` es un método explícito: la API nunca escribe `state` directamente
(AGENTS.md §4). Declara `requires_approval` para que el PDP pueda exigir HITL.

## Períodos

`account.period` con `date_from`, `date_to`, `state` ∈ {open, closed}. Cerrar un
período impide contabilizar en él; reabrir requiere permiso explícito y queda en
la auditoría.

## Fuera de alcance en esta entrega

Impuestos (F4-02), conciliación bancaria automática, reportes financieros,
analítica y cierre de ejercicio.
