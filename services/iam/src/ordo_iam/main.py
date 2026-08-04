"""Servicio ordo-iam: identidad y autorización (F1)."""

from ordo_runtime import create_app

from ordo_iam.api import router
from ordo_iam.worker import install_worker

app = create_app("iam")
app.include_router(router)
install_worker(app)
