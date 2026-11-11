"""Vercel entry point (see [tool.vercel] in pyproject.toml): the server's Starlette ASGI app."""

from server import build_http_app

app = build_http_app()
