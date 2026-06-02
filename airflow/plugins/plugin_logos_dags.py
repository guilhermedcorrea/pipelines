from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable

from airflow import settings
from airflow.plugins_manager import AirflowPlugin
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


PASTA_ATUAL = Path(__file__).resolve().parent
PASTA_STATIC = PASTA_ATUAL / "logos_dags" / "static"

app_static_logos = FastAPI(title="Static Logos Dags")


@app_static_logos.get("/api/dag-tags")
def listar_dags_com_tags() -> JSONResponse:
    """
    Retorna DAGs, descrições e tags direto do metadatabase do Airflow.

    Fonte dos dados:
      - dag.description
      - dag_tag.name

    Esse endpoint evita depender das tags renderizadas no template da tela /dags.
    """
    sessao = settings.Session()

    try:
        linhas = sessao.execute(
            text(
                """
                SELECT
                    d.dag_id,
                    d.description,
                    dt.name AS tag
                FROM dag d
                LEFT JOIN dag_tag dt
                    ON dt.dag_id = d.dag_id
                ORDER BY
                    d.dag_id,
                    dt.name
                """
            )
        ).mappings().all()
    finally:
        sessao.close()

    dags: dict[str, dict] = {}
    tags_por_dag: dict[str, set[str]] = defaultdict(set)

    for linha in linhas:
        dag_id = linha.get("dag_id")
        if not dag_id:
            continue

        if dag_id not in dags:
            dags[dag_id] = {
                "dag_id": dag_id,
                "description": linha.get("description") or "",
                "tags": [],
            }

        tag = linha.get("tag")
        if tag:
            tags_por_dag[dag_id].add(tag)

    for dag_id, tags in tags_por_dag.items():
        dags[dag_id]["tags"] = sorted(tags, key=lambda valor: valor.lower())

    return JSONResponse(
        {
            "ok": True,
            "total": len(dags),
            "items": sorted(dags.values(), key=lambda item: item["dag_id"].lower()),
        }
    )


# Importante: o mount precisa ficar depois da rota /api/dag-tags.
# Se o mount em "/" viesse antes, ele capturaria também /api/dag-tags.
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

        marcador_css = '<link rel="stylesheet" href="/logos-dags-static/css/logos_dags.css?v=13">'
        marcador_js = '<script defer src="/logos-dags-static/js/logos_dags.js?v=13"></script>'

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
