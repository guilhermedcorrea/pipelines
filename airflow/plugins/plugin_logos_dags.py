from __future__ import annotations

from pathlib import Path
from typing import Callable

from airflow.plugins_manager import AirflowPlugin
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


PASTA_ATUAL = Path(__file__).resolve().parent
PASTA_STATIC = PASTA_ATUAL / "logos_dags" / "static"


app_static_logos = FastAPI(title="Static Logos Dags")
app_static_logos.mount(
    "/",
    StaticFiles(directory=str(PASTA_STATIC)),
    name="static_logos_dags",
)


class MiddlewareInjetarLogosDags(BaseHTTPMiddleware):
    """Injeta CSS e JS na UI do Airflow para enriquecer a tela /dags."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        resposta = await call_next(request)

        caminho = request.url.path or ""
        content_type = (resposta.headers.get("content-type") or "").lower()

        if not caminho.startswith("/dags"):
            return resposta

        if "text/html" not in content_type:
            return resposta

        corpo_bytes = b""
        async for trecho in resposta.body_iterator:
            corpo_bytes += trecho

        try:
            corpo_html = corpo_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return Response(
                content=corpo_bytes,
                status_code=resposta.status_code,
                headers=dict(resposta.headers),
                media_type=resposta.media_type,
            )

        marcador_css = '<link rel="stylesheet" href="/logos-dags-static/css/logos_dags.css?v=9">'
        marcador_js = '<script defer src="/logos-dags-static/js/logos_dags.js?v=9"></script>'

        if marcador_css not in corpo_html and "</head>" in corpo_html:
            corpo_html = corpo_html.replace("</head>", f"    {marcador_css}\n</head>")

        if marcador_js not in corpo_html and "</body>" in corpo_html:
            corpo_html = corpo_html.replace("</body>", f"    {marcador_js}\n</body>")

        novos_headers = dict(resposta.headers)
        novos_headers.pop("content-length", None)

        return Response(
            content=corpo_html,
            status_code=resposta.status_code,
            headers=novos_headers,
            media_type="text/html",
        )


class PluginLogosDags(AirflowPlugin):
    name = "plugin_logos_dags"

    fastapi_apps = [
        {
            "app": app_static_logos,
            "url_prefix": "/logos-dags-static",
            "name": "logos_dags_static",
        }
    ]

    fastapi_root_middlewares = [
        {
            "middleware": MiddlewareInjetarLogosDags,
            "args": [],
            "kwargs": {},
            "name": "middleware_injetar_logos_dags",
        }
    ]