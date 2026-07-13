from aiohttp import web


async def post_graph(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"})
    return web.json_response(deps.graph_api.handle(body))


async def get_search(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    q = request.query.get("q", "").strip()
    limit = int(request.query.get("limit", "25"))
    if not q:
        return web.json_response(deps.graph_store.all(limit))
    return web.json_response(deps.graph_store.search_text(q, limit))


def register(app: web.Application) -> None:
    app.router.add_post("/api/graph", post_graph)
    app.router.add_get("/api/graph/search", get_search)
