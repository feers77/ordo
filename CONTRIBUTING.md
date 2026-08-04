# Cómo contribuir a ORDO

ORDO es software libre bajo **AGPLv3**. Puedes usarlo gratis, también con fines
comerciales, y modificarlo. A cambio, si lo modificas y lo despliegas —incluso si
solo lo ofreces por red, como SaaS— debes publicar el código de tus cambios bajo la
misma licencia. Ver [`ADR/ADR-010-licencia.md`](ADR/ADR-010-licencia.md).

## Firma: DCO en cada commit, CLA una sola vez

**DCO en cada commit.** Firma tus commits para afirmar que tienes derecho a aportar
ese código:

```bash
git commit -s -m "feat: ..."
```

Eso agrega `Signed-off-by: Tu Nombre <tu@email>`, que equivale a aceptar el
[Developer Certificate of Origin](https://developercertificate.org/).

**CLA una sola vez.** En tu primer pull request, un bot te pedirá aceptar el
[CLA de ORDO](CLA.md) con un comentario. Vale para todas tus contribuciones futuras.

Qué implica, en corto: **no cedes tu copyright**, sigues siendo dueño de tu código y
puedes usarlo donde quieras. Otorgas al proyecto una licencia amplia —incluido el
derecho de sublicencia— para distribuirlo. Hoy ORDO se distribuye bajo AGPLv3 y esa
es la intención; el derecho de sublicencia existe para no dejar al proyecto sin
salidas si en el futuro hiciera falta otra licencia. Está escrito de forma explícita
en el CLA en vez de dejarlo en letra chica: si no te acomoda, es preferible saberlo
antes de invertir tiempo.

## Antes de abrir un PR

1. Lee [`CLAUDE.md`](CLAUDE.md): es vinculante para humanos y agentes.
2. Si la decisión es estructural (un servicio, una dependencia, un cambio de contrato),
   escribe primero un ADR en `ADR/` y espera aprobación.
3. En lógica de dominio (contabilidad, impuestos, stock, permisos) **escribe los tests
   antes que la implementación**.
4. Corre `make check` (ruff + mypy strict + tests) y los de integración si tocaste el
   kernel.
5. Un PR es una unidad lógica. Si supera ~600 líneas de diff útil, divídelo.

## Lo que no aceptamos

- **Código copiado de Odoo** o de repos derivados, en ninguna forma: ni código, ni
  planes de cuentas, ni plantillas de impuestos, ni traducciones. Odoo Community es
  LGPLv3 y copiarlo contamina la licencia del producto. Se puede estudiar el
  comportamiento observable y reimplementarlo desde cero.
- **Datos fiscales sin fuente normativa citada** en el `manifest.yaml` del pack.
- **Secretos** de cualquier tipo en el repositorio.
- Dinero en `float`. Siempre `Decimal` en Python, `NUMERIC` en Postgres y string
  decimal en JSON.

## Zonas con revisión humana obligatoria

`packages/ordo-core/` (en especial `domains.py` y `environment.py`), `services/iam/`,
`modules/account/` e `infra/`. Un defecto ahí filtra datos entre tenants o rompe la
contabilidad, así que no se auto-mergean. Ver `.github/CODEOWNERS`.

## Reportar un problema de seguridad

No abras un issue público. Escribe a cferes@qin.cl describiendo el problema y cómo
reproducirlo. Si encuentras un defecto de seguridad en código existente, **repórtalo
antes de arreglarlo**: hay que evaluar si corresponde rotar credenciales o notificar.
