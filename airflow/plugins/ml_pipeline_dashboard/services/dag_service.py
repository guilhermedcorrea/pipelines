
from __future__ import annotations

from typing import Any

from ml_pipeline_dashboard.services.airflow_runtime_service import (
    listar_execucoes_reais,
    montar_dashboard_real,
)


def listar_dags_auditoria(
    dag_id: str | None = None,
    status: str | None = None,
    limite: int = 50,
) -> dict[str, Any]:
    """Eu listo execuções reais do Airflow para a camada de DAG service."""
    return listar_execucoes_reais(
        dag_id=dag_id,
        status=status,
        limite=limite,
    )



def obter_dashboard_dag(
    dag_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Eu devolvo o dashboard consolidado da DAG solicitada."""
    dag_id_limpo = str(dag_id or "").strip()
    if not dag_id_limpo:
        raise ValueError("dag_id é obrigatório.")

    return montar_dashboard_real(
        dag_id=dag_id_limpo,
        run_id=str(run_id).strip() if run_id else None,
    )
