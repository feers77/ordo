"""Traducción de lenguaje natural por HTTP (F3-04): devuelve el dominio, no lo ejecuta."""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.commercial import build_shop

pytestmark = pytest.mark.integration

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"
TENANT_PREFIX = "nlq"

DRAFT = '{"model": "sale.order", "domain": [["state", "=", "draft"]]}'
BAD_FIELD = '{"model": "sale.order", "domain": [["no_existe", "=", "x"]]}'


class StubModel:
    """Modelo de lenguaje de mentira: responde lo que le pasaron, en orden."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else "{}"


@pytest.fixture
async def shop(core_db_url: str) -> AsyncIterator[dict[str, Any]]:
    tenant = f"{TENANT_PREFIX}{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(core_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    data = await build_shop(session, tenant, modules_root=MODULES_ROOT)
    data["tenant"] = tenant
    yield data
    await session.close()
    await engine.dispose()


@pytest.fixture
def app(shop: dict[str, Any]) -> FastAPI:
    """App propia con los routers necesarios y sin enforcement."""
    from ordo_api.deps import get_env, get_registry, get_session
    from ordo_api.meta import router as meta_router
    from ordo_api.records import router as records_router
    from ordo_runtime import create_app

    env = shop["env"]
    application = create_app("api")
    application.include_router(meta_router)
    application.include_router(records_router)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield env.session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_registry] = lambda: env.registry
    application.dependency_overrides[get_env] = lambda: env
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def use_model(app: FastAPI, model: StubModel) -> StubModel:
    """Sustituye el modelo configurado por el stub, sin tocar la ruta."""
    from ordo_api.meta import get_query_model

    app.dependency_overrides[get_query_model] = lambda: model
    return model


async def translate(client: httpx.AsyncClient, shop: dict[str, Any], **body: Any) -> httpx.Response:
    return await client.post(
        "/meta/v1/translate-query", json=body, headers={"X-Ordo-Tenant": shop["tenant"]}
    )


class TestTranslateQueryEndpoint:
    async def test_returns_the_domain_without_executing_it(
        self, app: FastAPI, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        model = use_model(app, StubModel(DRAFT))
        response = await translate(
            client, shop, question="órdenes en borrador", models=["sale.order"]
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "model": "sale.order",
            "domain": [["state", "=", "draft"]],
            "attempts": 1,
        }
        # El schema viajó al modelo; ninguna fila del tenant lo hizo.
        assert "amount_total" in model.prompts[0]

    async def test_domain_that_never_compiles_is_422(
        self, app: FastAPI, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        use_model(app, StubModel(BAD_FIELD, BAD_FIELD))
        response = await translate(client, shop, question="lo que sea", models=["sale.order"])
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "NL_INVALID_DOMAIN"

    async def test_without_a_configured_model_it_is_503(
        self,
        client: httpx.AsyncClient,
        shop: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sin override se usa el modelo real, que sin comando no está disponible."""
        monkeypatch.delenv("ORDO_NL_COMMAND", raising=False)
        response = await translate(client, shop, question="órdenes de agosto")
        assert response.status_code == 503, response.text
        payload = response.json()["error"]
        assert payload["code"] == "NL_UNAVAILABLE"
        assert "ORDO_NL_COMMAND" in payload["hint"]
