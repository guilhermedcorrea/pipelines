from __future__ import annotations

import socket
from typing import Any

from airflow.listeners import hookimpl
from airflow.models import TaskInstance

from auditoria_execucao.schemas import (
    EventoDagPersistencia,
    EventoTaskPersistencia,
    serializar_json_seguro,
)
from auditoria_execucao.servico_auditoria import ServicoAuditoriaExecucao
from auditoria_execucao.tradutor_erros import traduzir_erro


def _extrair_atributo(objeto: Any, nome: str, padrao=None):
    """Extrai atributo com segurança."""
    return getattr(objeto, nome, padrao)


def _para_iso(valor) -> str | None:
    """Converte datas para ISO string."""
    if valor is None:
        return None
    try:
        return valor.isoformat()
    except Exception:
        return str(valor)


def _duracao_segundos(inicio, fim) -> float | None:
    """Calcula duração em segundos."""
    if not inicio or not fim:
        return None
    try:
        return round((fim - inicio).total_seconds(), 3)
    except Exception:
        return None


def _ler_xcom_resumo(task_instance) -> dict:
    """Lê o resumo publicado pela própria task via XCom."""
    try:
        if isinstance(task_instance, TaskInstance):
            return {}
        contexto = task_instance.get_template_context()
        ti = contexto["ti"]
        return ti.xcom_pull(task_ids=task_instance.task_id, key="auditoria_resumo_execucao") or {}
    except Exception:
        return {}


def _registrar_task(task_instance, status: str, erro: str | None = None) -> None:
    """Consolida os dados do listener e persiste no banco."""
    resumo = _ler_xcom_resumo(task_instance)

    dag_id = _extrair_atributo(task_instance, "dag_id", "")
    run_id = _extrair_atributo(task_instance, "run_id", "")
    task_id = _extrair_atributo(task_instance, "task_id", "")
    try_number = _extrair_atributo(task_instance, "try_number", 1)
    start_date = _extrair_atributo(task_instance, "start_date")
    end_date = _extrair_atributo(task_instance, "end_date")

    operator = None
    if isinstance(task_instance, TaskInstance):
        operator = _extrair_atributo(task_instance, "operator")
    else:
        try:
            contexto = task_instance.get_template_context()
            task = contexto.get("task")
            operator = getattr(task, "task_type", None) if task else None
        except Exception:
            operator = None

    erro_final = erro or resumo.get("erro_tecnico")
    traducao = traduzir_erro(erro_final)

    evento = EventoTaskPersistencia(
        dag_id=dag_id,
        run_id=run_id,
        task_id=task_id,
        try_number=try_number or 1,
        status=status,
        operator=operator,
        start_date=_para_iso(start_date),
        end_date=_para_iso(end_date),
        duracao_segundos=_duracao_segundos(start_date, end_date),
        nome_amigavel=resumo.get("nome_amigavel"),
        descricao_etapa=resumo.get("descricao_etapa"),
        origem_dados=resumo.get("origem_dados"),
        destino_dados=resumo.get("destino_dados"),
        linhas_lidas=resumo.get("linhas_lidas"),
        linhas_inseridas=resumo.get("linhas_inseridas"),
        linhas_atualizadas=resumo.get("linhas_atualizadas"),
        linhas_descartadas=resumo.get("linhas_descartadas"),
        validacoes_json=serializar_json_seguro(resumo.get("validacoes", [])),
        amostra_json=serializar_json_seguro(resumo.get("amostra", [])),
        metricas_json=serializar_json_seguro(resumo.get("metricas_extras", {})),
        observacoes_json=serializar_json_seguro(resumo.get("observacoes", [])),
        erro_tecnico=erro_final,
        erro_traduzido=resumo.get("erro_traduzido") or traducao.get("erro_traduzido"),
        causa_provavel=resumo.get("causa_provavel") or traducao.get("causa_provavel"),
        acao_sugerida=resumo.get("acao_sugerida") or traducao.get("acao_sugerida"),
        host_execucao=socket.gethostname(),
    )

    ServicoAuditoriaExecucao.registrar_task_run(evento)


def _registrar_dag_run(dag_run, status: str, mensagem: str | None = None) -> None:
    """Persiste informações consolidadas da execução da DAG."""
    start_date = _extrair_atributo(dag_run, "start_date")
    end_date = _extrair_atributo(dag_run, "end_date")
    queued_at = _extrair_atributo(dag_run, "queued_at")

    evento = EventoDagPersistencia(
        dag_id=_extrair_atributo(dag_run, "dag_id", ""),
        run_id=_extrair_atributo(dag_run, "run_id", ""),
        status=status,
        run_type=str(_extrair_atributo(dag_run, "run_type", "")),
        queued_at=_para_iso(queued_at),
        start_date=_para_iso(start_date),
        end_date=_para_iso(end_date),
        duracao_segundos=_duracao_segundos(start_date, end_date),
        mensagem_resumo=mensagem,
    )
    ServicoAuditoriaExecucao.registrar_dag_run(evento)


@hookimpl
def on_task_instance_running(previous_state, task_instance) -> None:
    """Evento disparado quando task entra em RUNNING."""
    _registrar_task(task_instance, status="RUNNING")


@hookimpl
def on_task_instance_success(previous_state, task_instance) -> None:
    """Evento disparado quando task entra em SUCCESS."""
    _registrar_task(task_instance, status="SUCCESS")


@hookimpl
def on_task_instance_failed(previous_state, task_instance, error=None) -> None:
    """Evento disparado quando task entra em FAILED."""
    _registrar_task(task_instance, status="FAILED", erro=str(error) if error else None)


@hookimpl
def on_dag_run_running(dag_run, msg: str) -> None:
    """Evento disparado quando dag_run entra em RUNNING."""
    _registrar_dag_run(dag_run, status="RUNNING", mensagem=msg)


@hookimpl
def on_dag_run_success(dag_run, msg: str) -> None:
    """Evento disparado quando dag_run entra em SUCCESS."""
    _registrar_dag_run(dag_run, status="SUCCESS", mensagem=msg)


@hookimpl
def on_dag_run_failed(dag_run, msg: str) -> None:
    """Evento disparado quando dag_run entra em FAILED."""
    _registrar_dag_run(dag_run, status="FAILED", mensagem=msg)