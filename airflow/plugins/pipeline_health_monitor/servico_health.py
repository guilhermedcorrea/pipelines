from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from typing import Any

PASTA_ATUAL = Path(__file__).resolve().parent
PASTA_PLUGINS_PAI = PASTA_ATUAL.parent
NOME_PACOTE_PLUGIN = "pipeline_health_monitor"

if str(PASTA_PLUGINS_PAI) not in sys.path:
    sys.path.insert(0, str(PASTA_PLUGINS_PAI))

modulo_existente = sys.modules.get(NOME_PACOTE_PLUGIN)

if modulo_existente is None:
    modulo_pacote = types.ModuleType(NOME_PACOTE_PLUGIN)
    modulo_pacote.__path__ = [str(PASTA_ATUAL)]
    sys.modules[NOME_PACOTE_PLUGIN] = modulo_pacote
else:
    caminho_existente = list(getattr(modulo_existente, "__path__", []))
    if str(PASTA_ATUAL) not in caminho_existente:
        caminho_existente.append(str(PASTA_ATUAL))
        modulo_existente.__path__ = caminho_existente

from pipeline_health_monitor.regras_health import calcular_scores_pipeline
from pipeline_health_monitor.repositorio_health import RepositorioHealth
from pipeline_health_monitor.schemas import (
    DependenciaItemSchema,
    DetalhePipelineSchema,
    IncidenteItemSchema,
    PipelineHealthItemSchema,
    ProblemaItemSchema,
    ResumoHealthSchema,
    TimelineItemSchema,
)
from pipeline_health_monitor.tradutor_erros import traduzir_erro

logger = logging.getLogger(__name__)


class ServicoHealth:
    """
    Camada de serviço do Pipeline Health Monitor.

    Responsabilidades:
    - buscar os dados no repositório
    - aplicar as regras de score
    - traduzir erros técnicos
    - montar payload consistente para a API
    """

    def __init__(self, repositorio: RepositorioHealth | None = None) -> None:
        self._repositorio = repositorio or RepositorioHealth()

    def obter_resumo(self) -> ResumoHealthSchema:
        """
        Busca o resumo consolidado e valida no schema.
        """
        dados = self._repositorio.buscar_resumo()
        return ResumoHealthSchema.model_validate(dados)

    def obter_pipelines(self) -> list[PipelineHealthItemSchema]:
        """
        Busca os pipelines, calcula scores e devolve a lista validada.
        """
        registros = self._repositorio.buscar_pipelines()
        resultado: list[PipelineHealthItemSchema] = []

        for registro in registros:
            scores = calcular_scores_pipeline(registro)

            pipeline = {
                "dag_id": registro.get("dag_id"),
                "nome": registro.get("nome"),
                "health_score": scores.get("health_score"),
                "status": scores.get("status"),
                "last_run": registro.get("last_run"),
                "duration_atual_min": registro.get("duracao_atual_min"),
                "duration_media_min": registro.get("duracao_media_min"),
                "last_failure": self._montar_last_failure_legivel(registro),
                "dependency": registro.get("dependency"),
                "data_quality": registro.get("data_quality"),
            }

            resultado.append(PipelineHealthItemSchema.model_validate(pipeline))

        return resultado

    def obter_pipeline_por_dag_id(
        self,
        dag_id: str,
    ) -> PipelineHealthItemSchema | None:
        """
        Retorna um pipeline específico pelo dag_id.
        """
        registros = self.obter_pipelines()

        for item in registros:
            if item.dag_id == dag_id:
                return item

        return None

    def obter_detalhe_pipeline(self, dag_id: str) -> DetalhePipelineSchema:
        """
        Monta o payload detalhado de uma pipeline.
        """
        registro = self._repositorio.buscar_pipeline_por_dag_id(dag_id)
        detalhe_base = self._repositorio.buscar_detalhe_pipeline(dag_id) or {}

        if not registro:
            payload_vazio = {
                "dag_id": dag_id,
                "health_score": 0,
                "status": "critical",
                "agendamento": 0,
                "execucao": 0,
                "performance": 0,
                "dependencias": 0,
                "dados": 0,
                "confiabilidade": 0,
                "timeline": [],
                "top_problemas": [],
            }
            return DetalhePipelineSchema.model_validate(payload_vazio)

        scores = calcular_scores_pipeline(registro)

        timeline = [
            TimelineItemSchema.model_validate(item).model_dump()
            for item in detalhe_base.get("timeline", [])
        ]

        top_problemas = [
            ProblemaItemSchema.model_validate(item).model_dump()
            for item in detalhe_base.get("top_problemas", [])
        ]

        """
        Eu acrescento o erro técnico traduzido como problema visível no topo,
        quando existir last_failure no registro principal.
        """
        erro_original = registro.get("last_failure")
        erro_traduzido = traduzir_erro(erro_original) if erro_original else None

        if erro_original and erro_traduzido:
            top_problemas.insert(
                0,
                ProblemaItemSchema(
                    tipo="erro",
                    titulo=str(erro_original),
                    descricao=str(erro_traduzido),
                ).model_dump(),
            )

        payload = {
            "dag_id": registro.get("dag_id", dag_id),
            "health_score": scores.get("health_score", 0),
            "status": scores.get("status", "critical"),
            "agendamento": scores.get("agendamento", 0),
            "execucao": scores.get("execucao", 0),
            "performance": scores.get("performance", 0),
            "dependencias": scores.get("dependencias", 0),
            "dados": scores.get("dados", 0),
            "confiabilidade": scores.get("confiabilidade", 0),
            "timeline": timeline,
            "top_problemas": top_problemas,
        }

        return DetalhePipelineSchema.model_validate(payload)

    def obter_dependencias(self) -> list[DependenciaItemSchema]:
        """
        Busca dependências e valida item a item.
        """
        dados = self._repositorio.buscar_dependencias()
        return [DependenciaItemSchema.model_validate(item) for item in dados]

    def obter_incidentes(self) -> list[IncidenteItemSchema]:
        """
        Busca incidentes e valida item a item.
        """
        dados = self._repositorio.buscar_incidentes()
        return [IncidenteItemSchema.model_validate(item) for item in dados]

    def _montar_last_failure_legivel(self, registro: dict[str, Any]) -> str | None:
        """
        Monta uma mensagem mais legível para o último erro.
        """
        erro_original = registro.get("last_failure")

        if not erro_original:
            return None

        erro_traduzido = traduzir_erro(erro_original)

        if not erro_traduzido:
            return str(erro_original)

        return f"{erro_original} | {erro_traduzido}"