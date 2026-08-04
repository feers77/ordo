# ADR-012 — CLA de contribuyentes para preservar la opción de relicenciar

- **Estado:** aprobado por @feers77 el 2026-08-04
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

ADR-010 adoptó AGPLv3. Esa decisión sola tiene una consecuencia irreversible: en
cuanto entre la primera contribución externa, el copyright del proyecto queda repartido
y **relicenciar exigiría el permiso de cada contribuyente**. En la práctica eso cierra
la puerta para siempre, porque basta que una persona no responda o se niegue.

Al momento de decidir, la ventana estaba completamente abierta: 37 commits, **un solo
autor**, cero forks y cero contribuyentes externos. El proyecto aún no se ha abierto y
los módulos de negocio los escribirá el propio equipo antes del lanzamiento.

No es que exista hoy la intención de vender licencias propietarias —la intención
declarada es que ORDO sea libre y que las mejoras vuelvan al proyecto—. Se trata de no
destruir una opción por omisión, cuando conservarla cuesta un clic por contribuyente.

## Opciones consideradas

1. **CLA tipo Apache ICLA** — el contribuyente conserva su copyright y otorga licencia
   perpetua e irrevocable **con derecho de sublicencia**. Ese derecho es lo que permite
   redistribuir bajo otra licencia. Fricción: aceptar una vez, vía bot.
2. **Solo DCO** — cero fricción y máxima adopción, pero renuncia definitiva a la opción.
3. **FLA-2.0 (FSFE)** — cesión fiduciaria con obligación de mantener el software libre.
   Permite relicenciar pero ata a condiciones; más pesado de firmar.
4. **CAA con cesión de copyright** — máximo control, mala recepción en la comunidad.

## Decisión

**CLA tipo Apache ICLA** (`CLA.md`), verificado por bot en cada PR externo. El titular
del repositorio queda en la allowlist para no bloquear su propio trabajo. DCO sigue
siendo obligatorio en cada commit; el CLA se acepta una sola vez.

Dos condiciones que hacen honesta esta decisión:

- **El derecho de sublicencia se declara de forma explícita** en el CLA, con el ejemplo
  concreto de relicenciamiento comercial, en vez de esconderlo en fraseo legal. Quien
  no esté de acuerdo debe poder saberlo antes de invertir tiempo.
- **Todo el producto sigue siendo AGPLv3**, incluidos los módulos de negocio y las
  localizaciones. No se reserva ninguna parte como propietaria hoy.

## Consecuencias

- Positivas: la opción de relicenciar queda preservada sin costo para nadie; el
  proyecto puede responder a un cambio de contexto legal o comercial sin quedar
  bloqueado por contribuyentes ilocalizables.
- Negativas: algunos desarrolladores rechazan por principio cualquier CLA con derecho
  de sublicencia, porque implica una asimetría entre el titular y quienes contribuyen.
  Esa fricción es real y se paga en adopción.
- Operativa: el bot requiere un secret `PERSONAL_ACCESS_TOKEN`. Mientras no exista, el
  workflow se salta en vez de fallar — el repositorio todavía no está abierto.
- **Pendiente antes de abrir el proyecto:** revisión del `CLA.md` por asesoría legal en
  Chile. El texto actual es un borrador de trabajo, no fue redactado por un abogado.

## Qué invalidaría esta decisión

Que el proyecto adopte gobernanza de fundación (tipo Apache o Eclipse), donde el
copyright se cede a la entidad y el CLA se reemplaza por el instrumento de esa
fundación.
