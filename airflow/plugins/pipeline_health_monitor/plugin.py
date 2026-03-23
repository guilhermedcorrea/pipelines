from __future__ import annotations

import importlib
import logging
import mimetypes
import sys
import types
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from airflow.plugins_manager import AirflowPlugin

logger = logging.getLogger(__name__)



PASTA_PLUGIN = Path(__file__).resolve().parent
PASTA_PLUGINS_PAI = PASTA_PLUGIN.parent
PASTA_STATIC = PASTA_PLUGIN / "static"
PASTA_ASSETS = PASTA_PLUGIN / "assets"
PASTA_REACT_DIST = PASTA_PLUGIN / "react_app" / "dist"
PASTA_TEMPLATES = PASTA_PLUGIN / "templates"

ARQUIVO_FALLBACK_HTML = PASTA_TEMPLATES / "fallback.html"

NOME_PACOTE_PLUGIN = "pipeline_health_monitor"
URL_PREFIXO_PLUGIN = "/pipeline-health-monitor"

mimetypes.add_type("application/javascript", ".cjs")
mimetypes.add_type("application/javascript", ".js")


# =========================================================
# FUNÇÕES AUXILIARES DE ARQUIVO
# =========================================================
def _arquivo_existe(caminho: Path) -> bool:
    return caminho.exists() and caminho.is_file()


def _pasta_existe(caminho: Path) -> bool:
    return caminho.exists() and caminho.is_dir()


def _localizar_bundle_react() -> str | None:
    """
    Procura o bundle principal do React dentro de react_app/dist.
    Retorna apenas o nome do arquivo.
    """
    if not _pasta_existe(PASTA_REACT_DIST):
        logger.warning(
            "Pipeline Health Monitor: pasta do build React não encontrada em %s",
            PASTA_REACT_DIST,
        )
        return None

    candidatos_prioritarios = [
        "main.umd.cjs",
        "main.cjs",
        "main.js",
        "index.umd.cjs",
        "index.cjs",
        "index.js",
    ]

    for nome in candidatos_prioritarios:
        caminho = PASTA_REACT_DIST / nome
        if _arquivo_existe(caminho):
            return nome

    for extensao in ("*.cjs", "*.js"):
        encontrados = sorted(PASTA_REACT_DIST.glob(extensao))
        if encontrados:
            return encontrados[0].name

    logger.warning(
        "Pipeline Health Monitor: nenhum bundle .cjs/.js encontrado em %s",
        PASTA_REACT_DIST,
    )
    return None


def _montar_bundle_url(nome_arquivo_bundle: str | None) -> str | None:
    """
    Gera a URL pública do bundle servido pelo FastAPI do plugin.
    """
    if not nome_arquivo_bundle:
        return None
    return f"{URL_PREFIXO_PLUGIN}/react/{nome_arquivo_bundle}"


def _ler_fallback_html() -> str:
    """
    Lê o fallback.html se existir.
    Se não existir, devolve HTML embutido.
    """
    if _arquivo_existe(ARQUIVO_FALLBACK_HTML):
        return ARQUIVO_FALLBACK_HTML.read_text(encoding="utf-8")

    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Pipeline Health Monitor</title>
        <link rel="stylesheet" href="{URL_PREFIXO_PLUGIN}/static/pipeline_health.css" />
    </head>
    <body>
        <div class="pagina">
            <div class="topo">
                <div class="topo-esquerda">
                    <div class="icone-titulo">❤</div>
                    <div>
                        <h1>Pipeline Health Monitor</h1>
                        <p>Monitor avançado de saúde operacional dos pipelines do Airflow</p>
                    </div>
                </div>
            </div>

            <section class="secao">
                <div class="cartao">
                    <div class="mensagem-vazia">
                        O plugin carregou, mas a API ou o bundle do React ainda não foram carregados corretamente.
                    </div>
                </div>
            </section>
        </div>

        <script src="{URL_PREFIXO_PLUGIN}/static/pipeline_health.js"></script>
    </body>
    </html>
    """


# =========================================================
# FUNÇÕES AUXILIARES DE IMPORTAÇÃO ROBUSTA
# =========================================================
def _garantir_contexto_de_pacote() -> None:
    """
    Garante que o Python enxergue a pasta do plugin como um pacote real.

    Por que isso existe:
    - O Airflow pode carregar plugin.py como módulo solto
    - Nesse cenário, imports relativos em api.py quebram
    - Ao registrar manualmente o pacote com __path__, o import relativo volta a funcionar
    """
    if str(PASTA_PLUGINS_PAI) not in sys.path:
        sys.path.insert(0, str(PASTA_PLUGINS_PAI))

    modulo_existente = sys.modules.get(NOME_PACOTE_PLUGIN)

    if modulo_existente is None:
        modulo_pacote = types.ModuleType(NOME_PACOTE_PLUGIN)
        modulo_pacote.__path__ = [str(PASTA_PLUGIN)]
        sys.modules[NOME_PACOTE_PLUGIN] = modulo_pacote
        logger.info(
            "Pipeline Health Monitor: pacote '%s' registrado dinamicamente com __path__=%s",
            NOME_PACOTE_PLUGIN,
            [str(PASTA_PLUGIN)],
        )
        return

    caminho_existente = list(getattr(modulo_existente, "__path__", []))
    if str(PASTA_PLUGIN) not in caminho_existente:
        caminho_existente.append(str(PASTA_PLUGIN))
        modulo_existente.__path__ = caminho_existente
        logger.info(
            "Pipeline Health Monitor: __path__ do pacote '%s' atualizado para %s",
            NOME_PACOTE_PLUGIN,
            caminho_existente,
        )


def _carregar_router_api() -> Any:
    """
    Carrega pipeline_health_monitor.api.router de forma robusta.

    Isso permite que imports relativos dentro do api.py funcionem.
    """
    _garantir_contexto_de_pacote()

    nome_modulo_api = f"{NOME_PACOTE_PLUGIN}.api"
    modulo_api = importlib.import_module(nome_modulo_api)
    router = getattr(modulo_api, "router", None)

    if router is None:
        raise AttributeError(
            f"O módulo '{nome_modulo_api}' foi importado, mas não possui atributo 'router'."
        )

    logger.info(
        "Pipeline Health Monitor: router carregado com sucesso do módulo %s",
        nome_modulo_api,
    )
    return router


def _router_ja_tem_prefixo_api(router: Any) -> bool:
    """
    Verifica se alguma rota já começa com /api.
    """
    for rota in getattr(router, "routes", []):
        caminho = getattr(rota, "path", "") or ""
        if caminho == "/api" or caminho.startswith("/api/"):
            return True
    return False


def _listar_rotas_app(app_fastapi: FastAPI) -> list[str]:
    """
    Lista rotas registradas no app para diagnóstico.
    """
    rotas: list[str] = []

    for rota in app_fastapi.router.routes:
        caminho = getattr(rota, "path", None)
        metodos = getattr(rota, "methods", None)

        if not caminho:
            continue

        if metodos:
            rotas.append(f"{','.join(sorted(metodos))} {caminho}")
        else:
            rotas.append(caminho)

    return sorted(rotas)


def _incluir_router_api(app_fastapi: FastAPI) -> list[str]:
    """
    Inclui o router do api.py no app.

    Regras:
    - se o router já vier com /api/... -> inclui sem prefixo extra
    - se o router vier sem /api -> inclui com prefixo /api
    """
    router_health = _carregar_router_api()
    router_tem_api = _router_ja_tem_prefixo_api(router_health)

    if router_tem_api:
        app_fastapi.include_router(router_health)
        logger.info(
            "Pipeline Health Monitor: router incluído sem prefixo extra, "
            "pois api.py já possui rotas com /api."
        )
    else:
        app_fastapi.include_router(router_health, prefix="/api")
        logger.info(
            "Pipeline Health Monitor: router incluído com prefixo '/api', "
            "pois api.py não possuía /api nas rotas."
        )

    caminhos_router: list[str] = []
    for rota in getattr(router_health, "routes", []):
        caminho = getattr(rota, "path", "") or ""
        if router_tem_api:
            caminhos_router.append(caminho)
        else:
            caminhos_router.append(f"/api{caminho}")

    return sorted(caminhos_router)


# =========================================================
# FASTAPI APP DO PLUGIN
# =========================================================
app = FastAPI(
    title="Pipeline Health Monitor",
    description="Monitor avançado de saúde dos pipelines do Airflow",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# =========================================================
# MONTAGEM DE ARQUIVOS ESTÁTICOS
# =========================================================
if _pasta_existe(PASTA_STATIC):
    app.mount(
        "/static",
        StaticFiles(directory=str(PASTA_STATIC)),
        name="pipeline_health_monitor_static",
    )
else:
    logger.warning(
        "Pipeline Health Monitor: pasta static não encontrada em %s",
        PASTA_STATIC,
    )

if _pasta_existe(PASTA_ASSETS):
    app.mount(
        "/assets",
        StaticFiles(directory=str(PASTA_ASSETS)),
        name="pipeline_health_monitor_assets",
    )
else:
    logger.warning(
        "Pipeline Health Monitor: pasta assets não encontrada em %s",
        PASTA_ASSETS,
    )

if _pasta_existe(PASTA_REACT_DIST):
    app.mount(
        "/react",
        StaticFiles(directory=str(PASTA_REACT_DIST), html=True),
        name="pipeline_health_monitor_react",
    )
else:
    logger.warning(
        "Pipeline Health Monitor: pasta react_app/dist não encontrada em %s",
        PASTA_REACT_DIST,
    )


# =========================================================
# ROTAS BÁSICAS
# =========================================================
@app.get("/", response_class=HTMLResponse)
async def pagina_raiz() -> HTMLResponse:
    return HTMLResponse(_ler_fallback_html())


@app.get("/health", response_model=None)
async def healthcheck() -> JSONResponse:
    nome_bundle = _localizar_bundle_react()
    return JSONResponse(
        {
            "ok": True,
            "plugin": "pipeline_health_monitor",
            "static_existe": _pasta_existe(PASTA_STATIC),
            "assets_existe": _pasta_existe(PASTA_ASSETS),
            "react_dist_existe": _pasta_existe(PASTA_REACT_DIST),
            "bundle_detectado": nome_bundle,
            "bundle_url": _montar_bundle_url(nome_bundle),
            "url_prefix": URL_PREFIXO_PLUGIN,
            "react_apps_habilitado": False,
            "rotas_registradas": _listar_rotas_app(app),
        }
    )


@app.get("/icon", response_model=None)
async def icon() -> FileResponse | JSONResponse:
    arquivo_svg = PASTA_ASSETS / "logo-health-monitor.svg"
    if _arquivo_existe(arquivo_svg):
        return FileResponse(arquivo_svg, media_type="image/svg+xml")

    return JSONResponse(
        {"ok": False, "erro": "Ícone do plugin não encontrado"},
        status_code=404,
    )


@app.get("/react-index", response_class=HTMLResponse)
async def react_index() -> HTMLResponse:
    arquivo_index = PASTA_REACT_DIST / "index.html"

    if _arquivo_existe(arquivo_index):
        return HTMLResponse(arquivo_index.read_text(encoding="utf-8"))

    return HTMLResponse(_ler_fallback_html(), status_code=404)


@app.get("/debug/routes", response_model=None)
async def debug_routes() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "url_prefix": URL_PREFIXO_PLUGIN,
            "rotas": _listar_rotas_app(app),
        }
    )


# =========================================================
# INTEGRAÇÃO DO ROUTER DE api.py
# =========================================================
_caminhos_router_api: list[str] = []

try:
    _caminhos_router_api = _incluir_router_api(app)
    logger.info(
        "Pipeline Health Monitor: rotas de API registradas com sucesso: %s",
        _caminhos_router_api,
    )
except Exception as exc:
    logger.exception(
        "Pipeline Health Monitor: falha ao incluir router de api.py. Motivo: %s",
        exc,
    )


# =========================================================
# METADADOS DO PLUGIN
# =========================================================
_nome_bundle = _localizar_bundle_react()
_bundle_url = _montar_bundle_url(_nome_bundle)
_url_icone = f"{URL_PREFIXO_PLUGIN}/icon"

EXTERNAL_VIEW_METADATA: dict[str, Any] = {
    "name": "Pipeline Health Monitor",
    "href": f"{URL_PREFIXO_PLUGIN}/",
    "destination": "nav",
    "category": "browse",
    "icon": _url_icone,
    "icon_dark_mode": _url_icone,
    "url_route": "pipeline-health-monitor",
}

FASTAPI_APP_METADATA: dict[str, Any] = {
    "app": app,
    "url_prefix": URL_PREFIXO_PLUGIN,
    "name": "Pipeline Health Monitor API",
}


class PipelineHealthMonitorPlugin(AirflowPlugin):
    name = "pipeline_health_monitor"

    fastapi_apps = [FASTAPI_APP_METADATA]
    react_apps: list[dict[str, Any]] = []
    external_views = [EXTERNAL_VIEW_METADATA]

    @staticmethod
    def on_load(*args: Any, **kwargs: Any) -> None:
        logger.info(
            "Pipeline Health Monitor carregado | static=%s | dist=%s | bundle=%s | bundle_url=%s | assets=%s | api_rotas=%s",
            PASTA_STATIC,
            PASTA_REACT_DIST,
            _nome_bundle,
            _bundle_url,
            PASTA_ASSETS,
            _caminhos_router_api,
        )