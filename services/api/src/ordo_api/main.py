"""Servicio ordo-api: kernel ORM expuesto como API genérica (F2)."""

from ordo_runtime import create_app

from ordo_api.records import router

app = create_app("api")
app.include_router(router)
