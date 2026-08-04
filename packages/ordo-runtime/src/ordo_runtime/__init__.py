"""ORDO runtime: application factory and shared middleware for all services."""

from ordo_runtime.app import create_app
from ordo_runtime.errors import OrdoError

__all__ = ["OrdoError", "create_app"]
