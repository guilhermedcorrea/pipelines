from __future__ import annotations

from typing import Any

from ml_pipeline_dashboard.services.airflow_runtime_service import montar_dashboard_real
from ml_pipeline_dashboard.services.ml_artifact_service import listar_artefatos_pipeline, listar_artefatos_task
from ml_pipeline_dashboard.services.ml_health_service import montar_health_pipeline
from ml_pipeline_dashboard.services.ml_lineage_service import montar_lineage_pipeline, montar_lineage_task
from ml_pipeline_dashboard.services.ml_metrics_service import consolidar_metricas_pipeline, extrair_metricas_task
from ml_pipeline_dashboard.services.sql_preview_service import listar_tabela_real, obter_preview_tabela


def _garantir_lista(valor: Any) -> list[Any]:
    """Eu normalizo qualquer valor para lista."""
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    if isinstance(valor, tuple):
        return list(valor)
    if isinstance(valor, set):
        return list(valor)
    return [valor]


def _texto(valor: Any) -> str:
    """Eu converto qualquer valor para texto limpo."""
    return str(valor or "").strip()


def _obter_dashboard(dag_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Eu centralizo a busca do dashboard real para reaproveitar entre as rotas."""
    dag_id_limpo = _texto(dag_id)
    if not dag_id_limpo:
        raise ValueError("dag_id é obrigatório.")
    return montar_dashboard_real(dag_id=dag_id_limpo, run_id=_texto(run_id) or None)


def _obter_task_do_dashboard(dashboard: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Eu localizo a task pedida dentro do dashboard da execução."""
    task_id_limpo = _texto(task_id)
    for task in _garantir_lista(dashboard.get("tasks")):
        if _texto(task.get("task_id") or task.get("id")) == task_id_limpo:
            return task
    raise ValueError(f"Task '{task_id_limpo}' não encontrada na DAG '{_texto(dashboard.get('dag_id'))}'.")


def _primeiro_objeto_tabela_visualizavel(task: dict[str, Any]) -> dict[str, Any] | None:
    """Eu encontro a primeira tabela que pode ser aberta na UI."""
    for objeto in _garantir_lista(task.get("objetos")):
        if not isinstance(objeto, dict):
            continue
        if objeto.get("conn_id") and objeto.get("schema") and objeto.get("tabela"):
            return objeto
    return None


def obter_detalhe_task(dag_id: str, task_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Eu devolvo o detalhe enriquecido de uma task específica."""
    dashboard = _obter_dashboard(dag_id=dag_id, run_id=run_id)
    task = _obter_task_do_dashboard(dashboard=dashboard, task_id=task_id)

    artefatos_task = listar_artefatos_task(task)
    metricas_task = extrair_metricas_task(task)
    lineage_task = montar_lineage_task(task)
    health_pipeline = montar_health_pipeline(dashboard)

    return {
        "dag_id": dashboard.get("dag_id"),
        "run_id": dashboard.get("run_id"),
        "task": task,
        "artefatos": artefatos_task,
        "metricas": metricas_task,
        "lineage": lineage_task,
        "health": {
            "pipeline": health_pipeline,
            "task_status": _texto(task.get("status")) or "unknown",
        },
    }


def obter_preview_tabela_task(dag_id: str, task_id: str, run_id: str | None = None, limite: int = 20) -> dict[str, Any]:
    """Eu abro o preview da primeira tabela disponível na task."""
    dashboard = _obter_dashboard(dag_id=dag_id, run_id=run_id)
    task = _obter_task_do_dashboard(dashboard=dashboard, task_id=task_id)
    objeto = _primeiro_objeto_tabela_visualizavel(task)
    if objeto is None:
        raise ValueError(f"A task '{task_id}' não possui objeto de tabela visualizável.")

    return obter_preview_tabela(
        conn_id=_texto(objeto.get("conn_id")),
        banco=_texto(objeto.get("banco")) or None,
        schema=_texto(objeto.get("schema")),
        tabela=_texto(objeto.get("tabela")),
        limite=limite,
    )


def obter_tabela_paginada_task(
    dag_id: str,
    task_id: str,
    run_id: str | None = None,
    pagina: int = 1,
    tamanho_pagina: int = 50,
    texto_busca: str | None = None,
    ordenar_por: str | None = None,
    direcao: str | None = "asc",
    filtros_categoricos: dict[str, list[str]] | None = None,
    filtros_datas_de: dict[str, str] | None = None,
    filtros_datas_ate: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Eu abro a tabela real da task com paginação e filtros."""
    dashboard = _obter_dashboard(dag_id=dag_id, run_id=run_id)
    task = _obter_task_do_dashboard(dashboard=dashboard, task_id=task_id)
    objeto = _primeiro_objeto_tabela_visualizavel(task)
    if objeto is None:
        raise ValueError(f"A task '{task_id}' não possui objeto de tabela visualizável.")

    return listar_tabela_real(
        conn_id=_texto(objeto.get("conn_id")),
        banco=_texto(objeto.get("banco")) or None,
        schema=_texto(objeto.get("schema")),
        tabela=_texto(objeto.get("tabela")),
        pagina=pagina,
        tamanho_pagina=tamanho_pagina,
        texto_busca=texto_busca,
        ordenar_por=ordenar_por,
        direcao=direcao,
        filtros_categoricos=filtros_categoricos or {},
        filtros_datas_de=filtros_datas_de or {},
        filtros_datas_ate=filtros_datas_ate or {},
    )


def obter_resumo_tecnico_pipeline(dag_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Eu consolido visões auxiliares do pipeline inteiro para reaproveitamento."""
    dashboard = _obter_dashboard(dag_id=dag_id, run_id=run_id)
    tasks = _garantir_lista(dashboard.get("tasks"))

    return {
        "dag_id": dashboard.get("dag_id"),
        "run_id": dashboard.get("run_id"),
        "artefatos": listar_artefatos_pipeline(tasks),
        "metricas": consolidar_metricas_pipeline(tasks),
        "lineage": montar_lineage_pipeline(tasks),
        "health": montar_health_pipeline(dashboard),
    }


__all__ = [
    "obter_detalhe_task",
    "obter_preview_tabela_task",
    "obter_tabela_paginada_task",
    "obter_resumo_tecnico_pipeline",
]
