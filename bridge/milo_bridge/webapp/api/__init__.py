"""Route registry: adding a server feature = one import + one line here."""
from aiohttp import web

from . import media, speak, status


def register_routes(app: web.Application) -> None:
    status.register(app)
    media.register(app)
    speak.register(app)
