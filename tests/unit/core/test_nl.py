"""Traducción de lenguaje natural a dominio (F3-04): sin DB y sin proceso externo.

El traductor solo necesita `registry` y `schema` del entorno: valida
compilando, nunca ejecuta. Por eso el `Environment` se construye con
`session=None` — ninguna de las rutas ejercitadas aquí toca la base.
"""

import json
from pathlib import Path

import pytest
from ordo_core import Environment
from ordo_core.modules import ModuleLoader
from ordo_core.nl import CommandQueryModel, NlError, build_prompt, extract_json, translate_query
from ordo_core.registry import Registry
from ordo_core.semantic import build_schema

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"

DRAFT = '{"model": "sale.order", "domain": [["state", "=", "draft"]]}'
BAD_FIELD = '{"model": "sale.order", "domain": [["no_existe", "=", "x"]]}'
UNKNOWN_MODEL = '{"model": "no.existe", "domain": []}'


class StubModel:
    """Modelo de lenguaje de mentira: responde lo que le pasaron, en orden."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else "{}"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.build(ModuleLoader([MODULES_ROOT]).load())


@pytest.fixture
def env(registry: Registry) -> Environment:
    return Environment(session=None, tenant="demo", registry=registry, app_role=None)  # type: ignore[arg-type]


class TestTranslateQuery:
    async def test_happy_path_returns_domain_without_executing(self, env: Environment) -> None:
        client = StubModel(DRAFT)
        result = await translate_query(
            env, "órdenes en borrador", models=["sale.order"], client=client
        )
        assert result == {
            "model": "sale.order",
            "domain": [["state", "=", "draft"]],
            "attempts": 1,
        }
        assert len(client.prompts) == 1

    async def test_json_wrapped_in_a_fenced_block_is_extracted(self, env: Environment) -> None:
        client = StubModel(f"Claro, aquí va:\n```json\n{DRAFT}\n```\n¿Te sirve?")
        result = await translate_query(env, "borradores", models=["sale.order"], client=client)
        assert result["domain"] == [["state", "=", "draft"]]
        assert result["attempts"] == 1

    async def test_retry_carries_the_compiler_error(self, env: Environment) -> None:
        """El reintento no es a ciegas: el error real del compilador vuelve al modelo."""
        client = StubModel(BAD_FIELD, DRAFT)
        result = await translate_query(env, "borradores", models=["sale.order"], client=client)
        assert result["attempts"] == 2
        assert "no_existe" in client.prompts[1]
        assert "no_existe" not in client.prompts[0]

    async def test_two_failed_attempts_stop(self, env: Environment) -> None:
        client = StubModel(BAD_FIELD, BAD_FIELD)
        with pytest.raises(NlError) as excinfo:
            await translate_query(env, "algo", models=["sale.order"], client=client)
        assert excinfo.value.code == "NL_INVALID_DOMAIN"
        assert "no_existe" in excinfo.value.message
        assert len(client.prompts) == 2

    async def test_answer_without_json_is_rejected(self, env: Environment) -> None:
        client = StubModel("No puedo ayudarte con eso.")
        with pytest.raises(NlError) as excinfo:
            await translate_query(env, "algo", models=["sale.order"], client=client)
        assert excinfo.value.code == "NL_INVALID_RESPONSE"

    async def test_unknown_model_is_retried_then_refused(self, env: Environment) -> None:
        client = StubModel(UNKNOWN_MODEL, UNKNOWN_MODEL)
        with pytest.raises(NlError) as excinfo:
            await translate_query(env, "algo", models=["sale.order"], client=client)
        assert excinfo.value.code == "NL_INVALID_DOMAIN"
        assert "no.existe" in excinfo.value.message
        assert "no.existe" in client.prompts[1]


class TestPromptPrivacy:
    """El prompt lleva estructura, nunca filas del tenant (ADR-017)."""

    async def test_prompt_carries_fields_but_no_data(self, env: Environment) -> None:
        client = StubModel(DRAFT)
        await translate_query(
            env, "órdenes confirmadas de agosto", models=["sale.order"], client=client
        )
        [prompt] = client.prompts
        assert "sale.order" in prompt
        assert "amount_total" in prompt
        assert "date_order" in prompt
        # Nombres de clientes o cualquier otro valor de una fila: nunca. Se
        # acota a sale.order para que la comprobación sea real: "ACME SpA" es
        # un `examples` declarado en res.partner, metadato del código y no
        # dato del tenant, pero enturbiaría la lectura de este test.
        for value in ("ACME", "Cliente Demo"):
            assert value not in prompt
        # Y la prueba de fondo: el prompt es función pura del schema y la
        # pregunta, así que no hay por dónde colar una fila.
        expected = build_prompt(
            build_schema(env.registry, models=["sale.order"], compact=True),
            "órdenes confirmadas de agosto",
        )
        assert prompt == expected

    def test_prompt_includes_the_error_only_on_retry(self, env: Environment) -> None:
        schema = {"models": [{"model": "sale.order", "fields": {}}]}
        first = build_prompt(schema, "pregunta")
        retry = build_prompt(schema, "pregunta", error="El campo 'x' no existe en sale.order")
        assert "intento anterior" not in first
        assert "El campo 'x' no existe en sale.order" in retry


class TestExtractJson:
    def test_takes_the_first_balanced_object(self) -> None:
        assert extract_json('ruido {"a": {"b": 1}} más ruido') == {"a": {"b": 1}}

    def test_braces_inside_strings_do_not_confuse_it(self) -> None:
        assert extract_json('{"a": "no cierra aquí }"}') == {"a": "no cierra aquí }"}

    def test_without_json_it_raises(self) -> None:
        with pytest.raises(NlError) as excinfo:
            extract_json("solo texto")
        assert excinfo.value.code == "NL_INVALID_RESPONSE"


class TestCommandQueryModel:
    async def test_without_command_configured_it_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORDO_NL_COMMAND", raising=False)
        with pytest.raises(NlError) as excinfo:
            await CommandQueryModel().complete("hola")
        assert excinfo.value.code == "NL_UNAVAILABLE"

    async def test_runs_the_command_and_reads_stdout(self) -> None:
        # /bin/cat devuelve por stdout lo que reciba por stdin: sirve de
        # modelo trivial para comprobar el transporte completo.
        answer = await CommandQueryModel(command="/bin/cat").complete("el prompt")
        assert answer == "el prompt"

    async def test_failing_command_is_model_failed(self) -> None:
        with pytest.raises(NlError) as excinfo:
            await CommandQueryModel(command="/bin/false").complete("el prompt")
        assert excinfo.value.code == "NL_MODEL_FAILED"

    async def test_result_path_unwraps_the_envelope(self) -> None:
        client = CommandQueryModel(command="/bin/cat", result_path="result")
        assert await client.complete(json.dumps({"result": "hola"})) == "hola"

    async def test_result_path_missing_in_the_output(self) -> None:
        client = CommandQueryModel(command="/bin/cat", result_path="result")
        with pytest.raises(NlError) as excinfo:
            await client.complete(json.dumps({"otra": "cosa"}))
        assert excinfo.value.code == "NL_MODEL_FAILED"
        assert "result" in excinfo.value.message

    async def test_timeout_kills_the_process(self) -> None:
        client = CommandQueryModel(command="/bin/cat", timeout_s=0.2)
        # /bin/cat sin EOF en stdin nunca termina por su cuenta... salvo que
        # se le cierre la entrada, cosa que communicate() hace; se usa sleep
        # para tener un proceso que sí se cuelga.
        client.command = "/bin/sleep 5"
        with pytest.raises(NlError) as excinfo:
            await client.complete("el prompt")
        assert excinfo.value.code == "NL_TIMEOUT"
