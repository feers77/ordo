"""Servicio ordo-api: kernel ORM expuesto como API genérica (F2)."""

from ordo_runtime import create_app

from ordo_api.meta import router as meta_router
from ordo_api.records import router as records_router

app = create_app("api")
app.include_router(records_router)
app.include_router(meta_router)
