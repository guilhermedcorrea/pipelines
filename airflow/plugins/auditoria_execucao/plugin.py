from __future__ import annotations

from airflow.plugins_manager import AirflowPlugin

import auditoria_execucao.listener as listener
from auditoria_execucao.api import app


class PluginAuditoriaExecucao(AirflowPlugin):
    """Plugin principal da auditoria de execução."""

    name = "plugin_auditoria_execucao"

    fastapi_apps = [
        {
            "app": app,
            "url_prefix": "/auditoria-execucao",
            "name": "API Auditoria Execucao",
        }
    ]

    external_views = [
        {
            "name": "Auditoria de Execução",
            "href": "/auditoria-execucao/painel",
            "destination": "nav",
            "url_route": "auditoria-execucao",
            "category": "browse",
        },
        {
            "name": "Auditoria do Run",
            "href": "/auditoria-execucao/dag/{DAG_ID}/run/{RUN_ID}",
            "destination": "dag_run",
            "url_route": "auditoria-run",
        },
    ]

    listeners = [listener]