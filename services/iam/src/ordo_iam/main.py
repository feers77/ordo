"""Servicio ordo-iam: identidad y autorización (F1)."""

from ordo_runtime import create_app

from ordo_iam.api import router

app = create_app("iam")
app.include_router(router)
