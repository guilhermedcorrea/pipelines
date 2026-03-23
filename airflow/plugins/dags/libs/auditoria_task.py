from __future__ import annotations

from typing import Any

from airflow.sdk import get_current_context

from plugins.auditoria_execucao.schemas import CHAVE_XCOM_AUDITORIA, ResumoAuditoriaTask
from plugins.auditoria_execucao.tradutor_erros import traduzir_erro


def _enriquecer_identificadores(resumo: ResumoAuditoriaTask) -> ResumoAuditoriaTask:
    """Preenche dag_id, run_id, task_id e try_number a partir do contexto da task."""
    contexto = get_current_context()
    ti = contexto["ti"]

    resumo.dag_id = ti.dag_id
    resumo.run_id = ti.run_id
    resumo.task_id = ti.task_id
    resumo.try_number = ti.try_number

    return resumo


def criar_resumo_auditoria(
    nome_amigavel: str,
    descricao_etapa: str,
    origem_dados: str | None = None,
    destino_dados: str | None = None,
) -> ResumoAuditoriaTask:
    """Cria um resumo base padronizado para a task."""
    resumo = ResumoAuditoriaTask(
        nome_amigavel=nome_amigavel,
        descricao_etapa=descricao_etapa,
        origem_dados=origem_dados,
        destino_dados=destino_dados,
    )
    return _enriquecer_identificadores(resumo)


def publicar_resumo_auditoria(resumo: ResumoAuditoriaTask) -> None:
    """Publica o resumo no XCom para o listener persistir."""
    contexto = get_current_context()
    ti = contexto["ti"]
    ti.xcom_push(key=CHAVE_XCOM_AUDITORIA, value=resumo.para_dict())


def registrar_erro_no_resumo(resumo: ResumoAuditoriaTask, erro: Exception | str) -> ResumoAuditoriaTask:
    """Enriquece o resumo com erro técnico e tradução amigável."""
    mensagem = str(erro)
    traducao = traduzir_erro(mensagem)

    resumo.erro_tecnico = mensagem
    resumo.erro_traduzido = traducao.get("erro_traduzido")
    resumo.causa_provavel = traducao.get("causa_provavel")
    resumo.acao_sugerida = traducao.get("acao_sugerida")
    return resumo


def adicionar_validacao(
    resumo: ResumoAuditoriaTask,
    nome: str,
    status: str,
    detalhe: str,
) -> ResumoAuditoriaTask:
    """Adiciona uma validação ao resumo."""
    resumo.validacoes.append(
        {
            "nome": nome,
            "status": status,
            "detalhe": detalhe,
        }
    )
    return resumo


def adicionar_observacao(resumo: ResumoAuditoriaTask, observacao: str) -> ResumoAuditoriaTask:
    """Adiciona observação livre ao resumo."""
    resumo.observacoes.append(observacao)
    return resumo


def definir_amostra(resumo: ResumoAuditoriaTask, linhas: list[dict[str, Any]], limite: int = 10) -> ResumoAuditoriaTask:
    """Define amostra tabular limitada."""
    resumo.amostra = (linhas or [])[:limite]
    return resumo