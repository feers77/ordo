"""Adaptadores disponibles en esta instalación.

El registro por defecto conoce todos los países cuyo adaptador está en el
repo. Firmar y enviar quedan fuera hasta que exista material criptográfico
en el vault y transporte contra el ambiente de certificación.
"""

from __future__ import annotations

from localizations.cl.einvoicing.adapter import SiiAdapter
from localizations.py.einvoicing.adapter import SifenAdapter

from modules.einvoicing.contracts import AdapterRegistry


def default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(SiiAdapter())
    registry.register(SifenAdapter())
    return registry
