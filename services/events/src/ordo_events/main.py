"""Servicio ordo-events: entrega de webhooks desde el outbox (F3-02).

La app existe para el healthcheck y el ciclo de vida; el trabajo real lo
hace el worker de fondo que se instala sobre su lifespan.
"""

from ordo_runtime import create_app

from ordo_events.worker import install_worker

app = create_app("events")
install_worker(app)
