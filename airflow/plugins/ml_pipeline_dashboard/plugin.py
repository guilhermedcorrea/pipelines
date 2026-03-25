from __future__ import annotations

from airflow.plugins_manager import AirflowPlugin
from fastapi.staticfiles import StaticFiles

from ml_pipeline_dashboard.api import criar_app_fastapi
from ml_pipeline_dashboard.config import CONFIGURACAO, PASTA_ASSETS


def _rota_ja_montada(app, caminho: str) -> bool:
    """Eu verifico se a aplicação FastAPI já possui uma rota montada nesse caminho."""
    for rota in getattr(app, "routes", []):
        if getattr(rota, "path", None) == caminho:
            return True
    return False


app = criar_app_fastapi()

"""
Eu monto apenas /assets aqui.

Motivo técnico:
- o /static já é montado dentro do api.py
- se eu tentar montar /static de novo no plugin, posso criar conflito ou duplicidade
- /assets fica opcional e só entra se a pasta existir
"""
if CONFIGURACAO.habilitar_assets_opcionais and PASTA_ASSETS.exists() and not _rota_ja_montada(app, "/assets"):
    app.mount(
        "/assets",
        StaticFiles(directory=str(PASTA_ASSETS)),
        name="ml_pipeline_dashboard_assets",
    )


class MLPipelineDashboardPlugin(AirflowPlugin):
    """Plugin principal do painel de pipelines de Machine Learning."""

    name = CONFIGURACAO.nome_plugin

    fastapi_apps = [
        {
            "app": app,
            "url_prefix": CONFIGURACAO.url_prefixo,
            "name": f"{CONFIGURACAO.nome_plugin}_app",
        }
    ]

    external_views = [
        {
            "name": CONFIGURACAO.nome_menu,
            "href": f"{CONFIGURACAO.url_prefixo}{CONFIGURACAO.rota_lista_html}",
            "url_route": f"{CONFIGURACAO.nome_plugin}_lista",
            "destination": "nav",
            "category": "browse",
        }
    ]