"""Natural language to ORDO domain (design F3-04, ADR-017).

The translator never executes anything: it returns a domain that the caller
runs later with its own permissions. Every proposed domain is validated by
compiling it with the real compiler (no SELECT is issued), and only the
schema travels in the prompt: model and field names, types, hints and the
examples declared in the model definitions. No tenant row ever leaves.

The language model is an external command declared in `ORDO_NL_COMMAND`:
the provider is a deployment decision, not a code one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
from typing import Any, Protocol

from ordo_core.domains import DomainCompiler
from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.semantic import build_schema

COMMAND_ENV = "ORDO_NL_COMMAND"
TIMEOUT_ENV = "ORDO_NL_TIMEOUT"
RESULT_PATH_ENV = "ORDO_NL_RESULT_PATH"
DEFAULT_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 2
STDERR_EXCERPT = 200


class NlError(KernelError):
    """Translation failure. Codes are part of the public contract."""


class QueryModel(Protocol):
    """Anything able to turn a prompt into text."""

    async def complete(self, prompt: str) -> str: ...


class CommandQueryModel:
    """Configured language model, invoked as an external command.

    The prompt is written to stdin and the answer read from stdout. If
    `result_path` is set the output is parsed as JSON and that key is used:
    command line tools usually wrap the text in an envelope with metadata.

    A missing command is reported when the model is *used*, not when this
    object is built: an unconfigured deployment must still import and boot,
    and answer 503 on the endpoint instead of crashing at startup.
    """

    def __init__(
        self,
        command: str | None = None,
        *,
        timeout_s: float | None = None,
        result_path: str | None = None,
        max_output_bytes: int = 200_000,
    ) -> None:
        self.command = command if command is not None else os.environ.get(COMMAND_ENV)
        self.timeout_s = timeout_s if timeout_s is not None else _timeout_from_env()
        self.result_path = (
            result_path if result_path is not None else os.environ.get(RESULT_PATH_ENV)
        )
        self.max_output_bytes = max_output_bytes

    async def complete(self, prompt: str) -> str:
        argv = shlex.split(self.command or "")
        if not argv:
            raise NlError(
                "NL_UNAVAILABLE",
                "No hay modelo de lenguaje configurado",
                hint=f"Define {COMMAND_ENV} con el comando que traduce preguntas.",
            )
        # Sin shell, siempre: el comando se parte con shlex y se ejecuta como
        # argv. La pregunta del usuario viaja por stdin y nunca se interpola
        # en una línea de comandos, así que no hay nada que un valor pueda
        # escapar (ADR-017).
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode()), timeout=self.timeout_s
            )
        except TimeoutError as exc:
            # Un proceso colgado no puede quedarse con el request.
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise NlError(
                "NL_TIMEOUT",
                f"El modelo de lenguaje no respondió en {self.timeout_s:g} segundos",
                hint="Reintenta o sube ORDO_NL_TIMEOUT si el modelo es lento.",
            ) from exc

        text = stdout[: self.max_output_bytes].decode("utf-8", errors="replace").strip()
        if process.returncode != 0 or not text:
            detail = stderr.decode("utf-8", errors="replace").strip()[:STDERR_EXCERPT]
            raise NlError(
                "NL_MODEL_FAILED",
                f"El comando externo falló (código {process.returncode}): {detail}"
                if detail
                else f"El comando externo no devolvió nada (código {process.returncode})",
                hint="Revisa la configuración del comando en el despliegue.",
            )
        return self._unwrap(text) if self.result_path else text

    def _unwrap(self, text: str) -> str:
        """Pull the answer out of the envelope declared in `result_path`."""
        key = str(self.result_path)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise NlError(
                "NL_MODEL_FAILED",
                f"La salida del comando externo no es JSON y se esperaba la clave '{key}'",
                hint=f"Ajusta {RESULT_PATH_ENV} o haz que el comando devuelva JSON.",
            ) from exc
        if not isinstance(data, dict) or key not in data:
            raise NlError(
                "NL_MODEL_FAILED",
                f"La salida del comando externo no traía la clave '{key}'",
                hint=f"Ajusta {RESULT_PATH_ENV} al nombre real de la clave de la respuesta.",
            )
        return str(data[key])


def _timeout_from_env() -> float:
    try:
        return float(os.environ.get(TIMEOUT_ENV, DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S


def build_prompt(schema: dict[str, Any], question: str, *, error: str | None = None) -> str:
    """Prompt for the configured model: structure only, never rows.

    Written in Spanish because the product is in Spanish and the questions
    arrive in Spanish; mixing languages costs accuracy for nothing.
    """
    parts = [
        "Eres un traductor de preguntas de negocio al lenguaje de dominios de ORDO.",
        "Responde SOLO con un objeto JSON, sin texto ni explicaciones alrededor:",
        '{"model": "<nombre.del.modelo>", "domain": [<términos>]}',
        "",
        "Reglas del lenguaje de dominios:",
        "- El dominio es una lista de términos [campo, operador, valor]; entre",
        "  términos el AND es implícito.",
        "- Operadores válidos: =, !=, >, >=, <, <=, in, not in, ilike, like.",
        '- "in" y "not in" reciben una lista de valores; "ilike" y "like" buscan',
        "  texto por coincidencia parcial.",
        "- Las fechas y horas van en ISO-8601 y los importes como string decimal,",
        "  nunca como número con coma flotante.",
        "- Las rutas con punto navegan relaciones: partner_id.name.",
        "- Filtra solo por campos del schema de abajo. Si la pregunta no pide",
        "  ningún filtro, devuelve una lista vacía.",
        "",
        "Ejemplos de dominio:",
        '[["state", "=", "draft"]]',
        '[["date_order", ">=", "2026-08-01"], ["amount_total", ">", "1000"]]',
        '[["partner_id.name", "ilike", "constructora"]]',
        '[["state", "in", ["confirmed", "invoiced"]]]',
        "",
        "Modelos disponibles (solo estructura, sin datos):",
        json.dumps(schema, ensure_ascii=False),
        "",
        f"Pregunta: {question}",
    ]
    if error:
        parts += [
            "",
            "Tu intento anterior no sirvió. El error fue:",
            error,
            "Corrige el JSON usando únicamente modelos y campos del schema.",
        ]
    return "\n".join(parts)


def extract_json(text: str) -> dict[str, Any]:
    """First balanced JSON object in the text.

    Models often wrap the answer in a fenced block or add a sentence before
    it; refusing those answers would waste a translation that is right.
    """
    for start, char in enumerate(text):
        if char != "{":
            continue
        candidate = _balanced_object(text, start)
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise NlError(
        "NL_INVALID_RESPONSE",
        "La respuesta del modelo no contenía ningún objeto JSON",
        hint="El modelo debe responder solo con el JSON pedido.",
    )


def _balanced_object(text: str, start: int) -> str | None:
    """Slice from `start` up to its matching brace, ignoring braces in strings."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


async def translate_query(
    env: Environment,
    question: str,
    *,
    models: list[str] | None = None,
    client: QueryModel | None = None,
) -> dict[str, Any]:
    """Translate a question into a domain and return it without running it.

    The proposed domain is validated by compiling it: a hallucinated field
    or operator fails here, not against the database. On failure the model
    gets one more turn with the compiler error as context; a second failure
    is `NL_INVALID_DOMAIN`. There is no loop: insisting costs the caller
    time and rarely converges.

    Availability errors (`NL_UNAVAILABLE`, `NL_TIMEOUT`, `NL_MODEL_FAILED`)
    and unparseable answers (`NL_INVALID_RESPONSE`) travel as they are: they
    describe the transport, not the domain, and retrying does not fix them.
    """
    model_client = client or CommandQueryModel()
    schema = build_schema(env.registry, models=models, compact=True)
    compiler = DomainCompiler(env.registry, env.schema)

    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = build_prompt(schema, question, error=last_error or None)
        data = extract_json(await model_client.complete(prompt))
        model_name = data.get("model")
        domain = data.get("domain")
        if not isinstance(model_name, str) or model_name not in env.registry:
            last_error = f"El modelo '{model_name}' no existe"
            continue
        if not isinstance(domain, list):
            last_error = "El dominio debe ser una lista de términos"
            continue
        try:
            compiler.select(model=model_name, domain=domain, limit=1)
        except KernelError as exc:
            last_error = exc.message
            continue
        return {"model": model_name, "domain": domain, "attempts": attempt}

    raise NlError(
        "NL_INVALID_DOMAIN",
        f"El dominio propuesto no es válido: {last_error}",
        hint="Reformula la pregunta o acota los modelos con 'models'.",
    )
