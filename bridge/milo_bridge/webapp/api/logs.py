from aiohttp import web


async def get_logs(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    if deps.log_buffer is None:
        return web.json_response({"lines": []})
    n = int(request.query.get("n", "200"))
    return web.json_response({"lines": deps.log_buffer.lines(n)})


def register(app: web.Application) -> None:
    app.router.add_get("/api/logs", get_logs)
