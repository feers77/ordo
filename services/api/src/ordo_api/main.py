"""Servicio ordo-api: kernel ORM expuesto como API genérica (F2)."""

from ordo_runtime import create_app

from ordo_api.actions import router as actions_router
from ordo_api.meta import router as meta_router
from ordo_api.records import router as records_router

app = create_app("api")
# El router de acciones va primero: /{model}/actions debe resolverse antes
# de que /{model}/{record_id} intente tratar "actions" como un id.
app.include_router(actions_router)
app.include_router(records_router)
app.include_router(meta_router)
