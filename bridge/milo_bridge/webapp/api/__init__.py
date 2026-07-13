"""Route registry: adding a server feature = one import + one line here."""
from aiohttp import web

from . import status


def register_routes(app: web.Application) -> None:
    status.register(app)
