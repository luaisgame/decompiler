"""HTML shell + static assets for dm.luaisgame.com.

The frontend is a single-page app, so every page (/, /invite/x, /s/x, /servers/1/...)
returns the same document and the client decides what to render.
"""

import os

from aiohttp import web

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>DM</title>
<meta name="theme-color" content="#1e1f22">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='24' fill='%235865f2'/%3E%3Ctext x='50' y='68' font-size='52' text-anchor='middle' fill='white' font-family='sans-serif' font-weight='bold'%3EDM%3C/text%3E%3C/svg%3E">
<link rel="stylesheet" href="/dm-assets/style.css">
</head>
<body>
<div id="app"><div class="boot"><div class="boot-logo">DM</div><div class="boot-text">Loading&hellip;</div></div></div>
<div id="toasts"></div>
<div id="modals"></div>
<script src="/dm-assets/app.js"></script>
</body>
</html>
"""


def set_static_dir(path: str) -> None:
    global _STATIC_DIR
    _STATIC_DIR = path


async def index_page(request):
    return web.Response(text=_INDEX, content_type="text/html",
                        headers={"Cache-Control": "no-store"})


async def static_file(request):
    name = os.path.basename(request.match_info.get("name") or "")
    path = os.path.join(_STATIC_DIR, name)
    if not name or not os.path.exists(path):
        raise web.HTTPNotFound(text="not found")
    ctype = {"js": "application/javascript", "css": "text/css",
             "svg": "image/svg+xml", "png": "image/png", "woff2": "font/woff2",
             "map": "application/json"}.get(name.rsplit(".", 1)[-1], "text/plain")
    return web.FileResponse(path, headers={"Content-Type": ctype,
                                           "Cache-Control": "no-cache"})


def register(app) -> None:
    app.router.add_get("/dm-assets/{name}", static_file)
    # vanity + invite links land on the server, the SPA finishes the job
    app.router.add_get("/invite/{code}", index_page)
    app.router.add_get("/s/{slug}", index_page)
    app.router.add_get("/app/{rest:.*}", index_page)
