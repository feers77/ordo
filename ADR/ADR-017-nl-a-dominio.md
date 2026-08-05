# ADR-017 — Traducción de lenguaje natural a dominio

- **Estado:** aceptado (2026-08-05, autorizado por @feers77)
- **Fecha:** 2026-08-05
- **Decisores:** @feers77

## Contexto

El plan declara `POST /meta/v1/translate-query`: convertir una pregunta en
lenguaje natural a un dominio ORDO válido y **devolverlo sin ejecutar**.
Eso exige un modelo de lenguaje, que es una dependencia externa y una
superficie de riesgo nueva (AGENTS.md §2.7).

## Opciones consideradas

1. **SDK de un proveedor concreto** — rápido de escribir, pero ata el
   producto a un proveedor y mete su cliente HTTP en el árbol.
2. **Comando externo configurable** — el servicio invoca un ejecutable
   declarado en `ORDO_NL_COMMAND`, le pasa el prompt por stdin y lee la
   respuesta por stdout. Cero dependencias Python nuevas (`asyncio`
   subprocess y `json` de la biblioteca estándar). El proveedor es una
   decisión de despliegue, no del código.
3. **Endpoint HTTP compatible OpenAI** — también agnóstico, pero obliga a
   elegir formato de API y a manejar credenciales dentro del proceso.

## Decisión

Opción 2. Reglas que la hacen defendible:

- **El traductor nunca ejecuta**. Devuelve el dominio; quién lo ejecuta es
  el agente, con sus permisos, por los endpoints de siempre. Una alucinación
  no puede tocar datos por sí sola.
- **Todo dominio devuelto se valida compilándolo** con el compilador real
  (sin ejecutar el SELECT). Si no compila, se reintenta **una vez** dándole
  el error como contexto; si vuelve a fallar, `NL_INVALID_DOMAIN`. No hay
  bucle: el modelo no se queda intentando a costa del usuario.
- **El prompt lleva solo estructura, nunca datos**: nombres de modelos y
  campos, tipos, `agent_hint` y ejemplos declarados. Ninguna fila del
  tenant sale del sistema. Está probado con un test que lo verifica.
- **Apagado por defecto**: sin `ORDO_NL_COMMAND` el endpoint responde 503
  `NL_UNAVAILABLE`. Un despliegue sin modelo configurado no falla raro: dice
  que no está disponible.
- Timeout (`ORDO_NL_TIMEOUT`, default 30 s) y tope de salida: un proceso
  externo colgado o verborreico no puede tumbar el servicio.
- El comando se parsea con `shlex.split` y se ejecuta **sin shell**: no hay
  interpolación de la pregunta del usuario en una línea de comandos.

## Consecuencias

- Positivas: ninguna dependencia Python nueva; el proveedor se cambia
  editando una variable de entorno; el modelo no tiene acceso a datos ni
  capacidad de ejecutar nada.
- Negativas: cada traducción paga el arranque de un proceso; la calidad
  depende del modelo configurado, y eso es del operador.
- Invalidaría: necesitar streaming o llamadas de herramienta desde el
  modelo, que pedirían un cliente de verdad y un ADR nuevo.
