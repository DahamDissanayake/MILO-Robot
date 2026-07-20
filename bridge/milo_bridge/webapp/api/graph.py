from aiohttp import web

MAX_SEARCH_LIMIT = 500


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
    try:
        limit = int(request.query.get("limit", "25"))
    except ValueError:
        return web.json_response({"error": "limit must be an integer"}, status=400)
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        return web.json_response(
            {"error": f"limit must be between 1 and {MAX_SEARCH_LIMIT}"}, status=400
        )
    if not q:
        return web.json_response(deps.graph_store.all(limit))
    return web.json_response(deps.graph_store.search_text(q, limit))


async def get_stats(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    return web.json_response(deps.graph_store.stats())


def register(app: web.Application) -> None:
    app.router.add_post("/api/graph", post_graph)
    app.router.add_get("/api/graph/search", get_search)
    app.router.add_get("/api/graph/stats", get_stats)
