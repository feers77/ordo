# F12-02 — Punto de venta: caja y turno (diseño)

Primer tramo del módulo `pos`. La decisión de fondo —que el POS no pasa por
`sale.order`— está en [ADR-019](../../ADR/ADR-019-punto-de-venta.md). Los
tickets, sus cobros y su asiento llegan en F12-02b.

## El turno es la unidad de responsabilidad

Se abre con un fondo declarado, se cierra a ventas nuevas, se cuenta el
efectivo, y la diferencia entre lo contado y lo esperado **se asienta en el
acto**. No existe el turno cerrado cuyo faltante nadie registró: ahí es
exactamente donde el robo hormiga se vuelve invisible.

| De | Acción | A | Aprobación |
|---|---|---|---|
| `draft` | `action_open` | `opened` | no |
| `opened` | `action_close_register` | `closing` | no |
| `closing` | `action_close` | `closed` | **sí** |

`action_close_register` existe porque contar el cajón toma minutos y durante
esos minutos la caja no puede seguir vendiendo. Sin ese estado intermedio, un
ticket cobrado mientras se cuenta convierte cualquier arqueo en ruido.

**`action_close` lleva aprobación** y `action_open` no. La diferencia de caja es
la señal de robo hormiga; que la persona responsable la vea y la autorice es el
control, no un trámite. Abrir con un fondo no compromete nada.

**Una caja tiene un turno abierto o ninguno.** Con dos turnos vivos sobre el
mismo cajón no se sabe contra qué fondo contar ni de quién es la diferencia:
`POS_SESSION_ALREADY_OPEN`.

## El arqueo

```
esperado = fondo + cobros en efectivo - vueltos entregados - retiros
diferencia = contado - esperado        (negativo = faltante)
```

Los cobros con tarjeta no entran: no pasan por el cajón. Toda la aritmética vive
en `modules/pos/cash.py`, sin base de datos, y se prueba con property-based
testing sobre las tres propiedades que importan:

- el esperado **no depende del orden** de los cobros — si dependiera, dos arqueos
  del mismo turno darían distinto y la diferencia sería ruido;
- el vuelto entregado y los retiros lo bajan **exactamente**;
- `diferencia(contado, esperado) == -diferencia(esperado, contado)`.

`validate_payments` aporta una regla que no es aritmética sino de control: **el
vuelto solo sale del efectivo**. Dar vuelto de una tarjeta es sacar del cajón
plata que nunca entró, y el arqueo lo ve como un faltante sin causa.

## El asiento de la diferencia

Faltante de $500:

```
Diferencias de caja        500
    Caja                          500
```

Sobrante, al revés. Se crea y se contabiliza en el mismo acto — un faltante en
borrador es un faltante que nadie mira— y **si la diferencia es cero no hay
asiento**: una partida 0/0 no es contabilidad, y el invariante de partida doble
la rechazaría.

## Costuras hacia F12-02b

`_cash_movements` y `_refuse_pending_tickets` devuelven vacío hoy, con el
comentario que lo dice. No es un stub disimulado: los tickets todavía no existen
como modelo, y el arqueo de un turno sin ventas **es** exactamente su fondo. En
F12-02b esas dos costuras leen `pos.payment` y `pos.order`, y el resto del
servicio no cambia.

## Permisos

Roles nuevos: `cajero` (vende, cobra, abre y cierra su turno; no configura la
caja ni toca cuentas) y `supervisor_tienda` (configura cajas y medios de cobro).
No se crea un rol amplio tipo `pos_admin`: los roles se funden por unión entre
módulos, así que un rol generoso aquí ampliaría permisos en todo el sistema.

## Simulación

`dry_run` sobre `action_open` devuelve el número que *asignaría* sin gastarlo: el
savepoint de `dispatch` se revierte. Importa porque la secuencia de turnos es sin
huecos y un hueco es permanente. Hay un test que lo comprueba abriendo de verdad
después y verificando que el número sigue siendo el mismo.
