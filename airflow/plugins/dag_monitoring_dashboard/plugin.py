from __future__ import annotations

from pathlib import Path

from airflow.plugins_manager import AirflowPlugin
from fastapi.staticfiles import StaticFiles

from dag_monitoring_dashboard.api import criar_app_fastapi


PASTA_PLUGIN = Path(__file__).resolve().parent
PASTA_ASSETS = PASTA_PLUGIN / "assets"


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
if PASTA_ASSETS.exists() and not _rota_ja_montada(app, "/assets"):
    app.mount(
        "/assets",
        StaticFiles(directory=str(PASTA_ASSETS)),
        name="dag_monitoring_assets",
    )


class DagMonitoringDashboardPlugin(AirflowPlugin):
    """Plugin principal do dashboard de monitoramento de DAGs."""

    name = "dag_monitoring_dashboard"

    fastapi_apps = [
        {
            "app": app,
            "url_prefix": "/dag_monitoring",
            "name": "dag_monitoring_app",
        }
    ]

    external_views = [
        {
            "name": "Dag Monitoring",
            "href": "/dag_monitoring/lista",
            "url_route": "dag_monitoring_lista",
            "destination": "nav",
            "category": "browse",
        }
    ]