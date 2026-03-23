from __future__ import annotations

from typing import Any

from airflow.sdk import get_current_context

from plugins.auditoria_execucao.schemas import (
    CHAVE_XCOM_AUDITORIA,
    ResumoAuditoriaTask,
)
from plugins.auditoria_execucao.tradutor_erros import traduzir_erro


def _enriquecer_identificadores(resumo: ResumoAuditoriaTask) -> ResumoAuditoriaTask:
    """Eu preencho os identificadores da execução a partir do contexto atual da task."""
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
    """Eu crio o resumo base padronizado da task para auditoria estruturada."""
    resumo = ResumoAuditoriaTask(
        nome_amigavel=nome_amigavel,
        descricao_etapa=descricao_etapa,
        origem_dados=origem_dados,
        destino_dados=destino_dados,
    )
    return _enriquecer_identificadores(resumo)


def publicar_resumo_auditoria(resumo: ResumoAuditoriaTask) -> None:
    """Eu publico no XCom o resumo da execução para o listener persistir no banco."""
    contexto = get_current_context()
    ti = contexto["ti"]
    ti.xcom_push(key=CHAVE_XCOM_AUDITORIA, value=resumo.para_dict())


def registrar_erro_no_resumo(
    resumo: ResumoAuditoriaTask,
    erro: Exception | str,
) -> ResumoAuditoriaTask:
    """Eu registro o erro técnico e a tradução amigável no resumo."""
    mensagem_erro = str(erro)
    traducao = traduzir_erro(mensagem_erro)

    resumo.erro_tecnico = mensagem_erro
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
    """Eu adiciono uma validação executada pela task ao resumo."""
    resumo.validacoes.append(
        {
            "nome": nome,
            "status": status,
            "detalhe": detalhe,
        }
    )
    return resumo


def adicionar_observacao(
    resumo: ResumoAuditoriaTask,
    observacao: str,
) -> ResumoAuditoriaTask:
    """Eu adiciono uma observação livre ao resumo."""
    resumo.observacoes.append(observacao)
    return resumo


def definir_amostra(
    resumo: ResumoAuditoriaTask,
    linhas: list[dict[str, Any]],
    limite: int = 10,
) -> ResumoAuditoriaTask:
    """Eu defino uma amostra tabular limitada para exibição na tela de auditoria."""
    resumo.amostra = (linhas or [])[:limite]
    return resumo