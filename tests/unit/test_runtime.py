"""Tests del runtime común (T0.4): healthchecks y formato de error CLAUDE.md §5."""

from fastapi.testclient import TestClient
from ordo_runtime import OrdoError, create_app
from ordo_runtime.health import parse_tcp_checks


def make_client() -> TestClient:
    app = create_app("test")

    @app.get("/boom")
    async def boom() -> None:
        raise OrdoError(
            "El período contable está cerrado.",
            code="ACCOUNT_PERIOD_LOCKED",
            status_code=423,
            model="account.move",
            record_id=4821,
            field="date",
            hint="Usa una fecha posterior o solicita reapertura.",
        )

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("algo inesperado")

    return TestClient(app, raise_server_exceptions=False)


def test_healthz() -> None:
    resp = make_client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "test"}


def test_readyz_without_checks_is_ready() -> None:
    resp = make_client().get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readyz_with_unreachable_dependency(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("READYZ_TCP_CHECKS", "fake=127.0.0.1:1")
    resp = make_client().get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"] == {"fake": False}


def test_domain_error_payload() -> None:
    resp = make_client().get("/boom")
    assert resp.status_code == 423
    error = resp.json()["error"]
    assert error["code"] == "ACCOUNT_PERIOD_LOCKED"
    assert error["model"] == "account.move"
    assert error["record_id"] == 4821
    assert error["field"] == "date"
    assert error["retryable"] is False
    assert error["requires_approval"] is False
    assert "hint" in error
    assert "docs_url" in error
    assert "trace_id" in error


def test_unhandled_error_is_masked() -> None:
    resp = make_client().get("/crash")
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert "algo inesperado" not in error["message"]


def test_request_id_header_roundtrip() -> None:
    resp = make_client().get("/healthz", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


def test_parse_tcp_checks() -> None:
    assert parse_tcp_checks("pg=localhost:5432, redis=127.0.0.1:6379") == {
        "pg": ("localhost", 5432),
        "redis": ("127.0.0.1", 6379),
    }
    assert parse_tcp_checks("") == {}
