from aiohttp import web

from ..telemetry import collect_telemetry


async def get_status(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    body = collect_telemetry(deps)
    body.pop("t", None)
    body.update(
        robot_id=deps.config.robot_id,
        robot_name=deps.config.robot_name,
        hardware={
            "camera": deps.camera is not None,
            "audio": deps.audio is not None,
            "imu": deps.imu is not None,
            "display": deps.display is not None,
        },
    )
    return web.json_response(body)


def register(app: web.Application) -> None:
    app.router.add_get("/api/status", get_status)
