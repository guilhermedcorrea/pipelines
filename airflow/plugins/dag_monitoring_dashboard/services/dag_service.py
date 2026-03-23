from __future__ import annotations

from dag_monitoring_dashboard.services.airflow_runtime_service import (
    listar_execucoes_reais,
    montar_dashboard_real,
)


def listar_dags_auditoria(
    dag_id: str | None = None,
    status: str | None = None,
    limite: int = 50,
) -> dict:
    """Eu listo execuções reais para a tela principal."""
    return listar_execucoes_reais(
        dag_id=dag_id,
        status=status,
        limite=limite,
    )


def obter_dashboard_dag(
    dag_id: str,
    run_id: str | None = None,
) -> dict:
    """Eu retorno o dashboard consolidado de uma DAG."""
    if not dag_id or not str(dag_id).strip():
        raise ValueError("dag_id é obrigatório.")

    return montar_dashboard_real(
        dag_id=str(dag_id).strip(),
        run_id=str(run_id).strip() if run_id else None,
    )