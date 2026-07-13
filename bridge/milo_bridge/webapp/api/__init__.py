"""Route registry: adding a server feature = one import + one line here."""
from aiohttp import web

from . import auth, graph, logs, media, motion_meta, speak, status


def register_routes(app: web.Application) -> None:
    auth.register(app)
    status.register(app)
    media.register(app)
    speak.register(app)
    graph.register(app)
    motion_meta.register(app)
    logs.register(app)
