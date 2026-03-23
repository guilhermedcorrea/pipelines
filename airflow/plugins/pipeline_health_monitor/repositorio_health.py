from __future__ import annotations

from typing import Any


class RepositorioHealth:
    """
    Repositório responsável por centralizar a origem dos dados do Health Monitor.

    Neste primeiro momento, ele devolve dados mockados.
    No próximo passo, você pode trocar estes métodos para ler:
    - metadata DB do Airflow
    - tabelas auxiliares do monitor
    - logs agregados
    - checks de dependências
    """

    def buscar_resumo(self) -> dict[str, Any]:
        return {
            "total_pipelines": 28,
            "healthy": 18,
            "degraded": 6,
            "critical": 4,
            "paused": 2,
            "incidents_last_24h": 3,
        }

    def buscar_pipelines(self) -> list[dict[str, Any]]:
        return [
            {
                "dag_id": "etl_ctr_controle_contratos_euromidia",
                "nome": "CTR Controle Contratos Euromídia",
                "last_run": "2026-03-20T09:00:00-03:00",
                "duracao_atual_min": 12,
                "duracao_media_min": 10,
                "last_failure": None,
                "dependency": "SQL Server",
                "data_quality": "ok",
                "pausado": False,
                "atraso_minutos": 0,
                "execucoes_perdidas": 0,
                "taxa_sucesso_percentual": 96,
                "quantidade_falhas_recentes": 0,
                "quantidade_retries_recentes": 1,
                "quantidade_dependencias_down": 0,
                "quantidade_dependencias_warning": 0,
                "quantidade_dependencias_unstable": 0,
                "percentual_rejeicao": 0.1,
                "volume_zerado_inesperado": False,
                "quantidade_incidentes_abertos": 0,
                "quantidade_intervencoes_manuais": 0,
            },
            {
                "dag_id": "Pipeline_Movimento_Financeiro_Omie",
                "nome": "Movimento Financeiro Omie",
                "last_run": "2026-03-20T08:30:00-03:00",
                "duracao_atual_min": 18,
                "duracao_media_min": 12,
                "last_failure": "API timeout",
                "dependency": "Omie API",
                "data_quality": "ok",
                "pausado": False,
                "atraso_minutos": 8,
                "execucoes_perdidas": 0,
                "taxa_sucesso_percentual": 82,
                "quantidade_falhas_recentes": 2,
                "quantidade_retries_recentes": 3,
                "quantidade_dependencias_down": 0,
                "quantidade_dependencias_warning": 1,
                "quantidade_dependencias_unstable": 1,
                "percentual_rejeicao": 0.2,
                "volume_zerado_inesperado": False,
                "quantidade_incidentes_abertos": 1,
                "quantidade_intervencoes_manuais": 1,
            },
            {
                "dag_id": "pipeline_cotacao_diaria_empresas_b3",
                "nome": "Cotação Diária Empresas B3",
                "last_run": "2026-03-20T07:45:00-03:00",
                "duracao_atual_min": 35,
                "duracao_media_min": 15,
                "last_failure": "DB connection error",
                "dependency": "SQL Server",
                "data_quality": "warning",
                "pausado": False,
                "atraso_minutos": 34,
                "execucoes_perdidas": 1,
                "taxa_sucesso_percentual": 58,
                "quantidade_falhas_recentes": 4,
                "quantidade_retries_recentes": 6,
                "quantidade_dependencias_down": 1,
                "quantidade_dependencias_warning": 0,
                "quantidade_dependencias_unstable": 1,
                "percentual_rejeicao": 7.5,
                "volume_zerado_inesperado": False,
                "quantidade_incidentes_abertos": 1,
                "quantidade_intervencoes_manuais": 2,
            },
            {
                "dag_id": "pipeline_cotacao_diaria_dolar",
                "nome": "Cotação Diária Dólar",
                "last_run": "2026-03-20T11:00:00-03:00",
                "duracao_atual_min": 6,
                "duracao_media_min": 7,
                "last_failure": None,
                "dependency": "API Banco Central",
                "data_quality": "ok",
                "pausado": False,
                "atraso_minutos": 0,
                "execucoes_perdidas": 0,
                "taxa_sucesso_percentual": 99,
                "quantidade_falhas_recentes": 0,
                "quantidade_retries_recentes": 0,
                "quantidade_dependencias_down": 0,
                "quantidade_dependencias_warning": 0,
                "quantidade_dependencias_unstable": 0,
                "percentual_rejeicao": 0.0,
                "volume_zerado_inesperado": False,
                "quantidade_incidentes_abertos": 0,
                "quantidade_intervencoes_manuais": 0,
            },
            {
                "dag_id": "etl_omie_clientes_raw",
                "nome": "Omie Clientes Raw",
                "last_run": "2026-03-20T06:30:00-03:00",
                "duracao_atual_min": 22,
                "duracao_media_min": 11,
                "last_failure": "Token expired",
                "dependency": "Omie API",
                "data_quality": "ok",
                "pausado": False,
                "atraso_minutos": 12,
                "execucoes_perdidas": 0,
                "taxa_sucesso_percentual": 74,
                "quantidade_falhas_recentes": 3,
                "quantidade_retries_recentes": 5,
                "quantidade_dependencias_down": 0,
                "quantidade_dependencias_warning": 1,
                "quantidade_dependencias_unstable": 1,
                "percentual_rejeicao": 0.4,
                "volume_zerado_inesperado": False,
                "quantidade_incidentes_abertos": 1,
                "quantidade_intervencoes_manuais": 1,
            },
        ]

    def buscar_pipeline_por_dag_id(self, dag_id: str) -> dict[str, Any] | None:
        for pipeline in self.buscar_pipelines():
            if pipeline.get("dag_id") == dag_id:
                return pipeline
        return None

    def buscar_detalhe_pipeline(self, dag_id: str) -> dict[str, Any]:
        """
        Retorna um mock de detalhe.
        Depois isso pode ser enriquecido com timeline, top problemas,
        histórico de task instances etc.
        """
        pipeline = self.buscar_pipeline_por_dag_id(dag_id)

        if not pipeline:
            return {
                "dag_id": dag_id,
                "timeline": [],
                "top_problemas": [],
            }

        return {
            "dag_id": dag_id,
            "timeline": [
                {"run_id": "run_01", "status": "success", "duracao_min": 11},
                {"run_id": "run_02", "status": "success", "duracao_min": 10},
                {"run_id": "run_03", "status": "failed", "duracao_min": 16},
                {"run_id": "run_04", "status": "success", "duracao_min": 13},
                {"run_id": "run_05", "status": "failed", "duracao_min": 19},
            ],
            "top_problemas": [
                {
                    "tipo": "dependencia",
                    "titulo": "Latência elevada na dependência principal",
                    "descricao": (
                        f"O pipeline depende de "
                        f"{pipeline.get('dependency', 'serviço externo')} "
                        f"e há sinais de instabilidade."
                    ),
                },
                {
                    "tipo": "performance",
                    "titulo": "Duração acima da média",
                    "descricao": (
                        "A duração atual recente está acima do baseline "
                        "histórico esperado."
                    ),
                },
                {
                    "tipo": "execucao",
                    "titulo": "Falhas intermitentes recentes",
                    "descricao": (
                        "Há indícios de instabilidade nas últimas "
                        "execuções do pipeline."
                    ),
                },
            ],
        }

    def buscar_dependencias(self) -> list[dict[str, Any]]:
        return [
            {"nome": "SQL Server", "status": "down", "latencia_ms": None},
            {"nome": "Omie API", "status": "unstable", "latencia_ms": 1450},
            {"nome": "SharePoint", "status": "ok", "latencia_ms": 210},
            {"nome": "S3 Bucket", "status": "warning", "latencia_ms": 520},
            {"nome": "Postgres Airflow", "status": "ok", "latencia_ms": 35},
            {"nome": "Redis Broker", "status": "ok", "latencia_ms": 12},
        ]

    def buscar_incidentes(self) -> list[dict[str, Any]]:
        return [
            {
                "titulo": "Instabilidade SQL Server",
                "severidade": "critical",
                "status": "open",
                "inicio": "2026-03-20T07:40:00-03:00",
                "fim": None,
                "causa_raiz": (
                    "Timeout e indisponibilidade intermitente "
                    "do banco principal."
                ),
                "impacto": "Pipelines de carga e consultas de negócio afetados.",
            },
            {
                "titulo": "Omie API com timeout intermitente",
                "severidade": "warning",
                "status": "open",
                "inicio": "2026-03-20T08:15:00-03:00",
                "fim": None,
                "causa_raiz": "Lentidão na API externa durante sincronizações.",
                "impacto": "Aumento da duração e retries em pipelines Omie.",
            },
            {
                "titulo": "Falha anterior na coleta de câmbio",
                "severidade": "info",
                "status": "resolved",
                "inicio": "2026-03-19T11:00:00-03:00",
                "fim": "2026-03-19T11:24:00-03:00",
                "causa_raiz": (
                    "Retorno inconsistente da fonte externa de cotação."
                ),
                "impacto": "Apenas uma execução afetada, sem impacto persistente.",
            },
        ]