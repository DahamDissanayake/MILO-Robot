from aiohttp import web

from ...poses import POSES
from ..motion import list_faces


async def get_poses(request: web.Request) -> web.Response:
    return web.json_response({"poses": sorted(POSES)})


async def get_faces(request: web.Request) -> web.Response:
    return web.json_response({"faces": list_faces()})


def register(app: web.Application) -> None:
    app.router.add_get("/api/poses", get_poses)
    app.router.add_get("/api/faces", get_faces)
