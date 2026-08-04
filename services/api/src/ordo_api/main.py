"""Servicio ordo-api: kernel ORM expuesto como API genérica (F2)."""

from ordo_runtime import create_app

from ordo_api.actions import router as actions_router
from ordo_api.authz import install_enforcement
from ordo_api.explain import router as explain_router
from ordo_api.meta import router as meta_router
from ordo_api.records import router as records_router
from ordo_api.reports import router as reports_router
from ordo_api.sandbox import router as sandbox_router

app = create_app("api")
# Acciones, reportes, sandbox y explain van primero: sus rutas comparten
# prefijo con /{model}/{record_id}, que capturaría "reports" o "sandbox"
# como si fueran nombres de modelo.
app.include_router(actions_router)
app.include_router(reports_router)
app.include_router(sandbox_router)
app.include_router(explain_router)
app.include_router(records_router)
app.include_router(meta_router)
install_enforcement(app)
