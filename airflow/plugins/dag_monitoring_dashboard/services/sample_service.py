from __future__ import annotations

from typing import Any


def _humanizar_texto(texto: str) -> str:
    """Eu transformo texto com underscore em algo amigável para exibição."""
    if not texto:
        return "Pipeline"

    partes = [parte for parte in texto.replace("-", "_").split("_") if parte]
    if not partes:
        return "Pipeline"

    return " ".join(parte.capitalize() for parte in partes)


def _gerar_descricao_curta(dag_id: str) -> str:
    """Eu monto uma descrição curta automática a partir do dag_id."""
    nome_amigavel = _humanizar_texto(dag_id)
    return (
        f"Visualização operacional da DAG {nome_amigavel}, com etapas, objetos manipulados, "
        f"amostra de dados, SQL utilizado e healthcheck da execução."
    )


def _gerar_descricao_detalhada(dag_id: str) -> str:
    """Eu monto uma descrição detalhada automática da DAG."""
    nome_amigavel = _humanizar_texto(dag_id)
    return (
        f"A DAG {nome_amigavel} representa um fluxo de leitura, transformação e persistência de dados. "
        f"Esta visualização mostra o encadeamento das tasks, os objetos usados em cada etapa, "
        f"as estruturas de dados envolvidas, a consulta ou procedure executada e o estado de saúde "
        f"da execução para facilitar auditoria técnica e entendimento operacional."
    )


def _montar_amostra_leitura() -> list[dict[str, Any]]:
    """Eu retorno uma amostra representativa da etapa de leitura."""
    return [
        {"id": 1, "nome": "Registro 1", "email": "registro1@email.com", "cidade": "Campinas"},
        {"id": 2, "nome": "Registro 2", "email": "registro2@email.com", "cidade": "Valinhos"},
        {"id": 3, "nome": "Registro 3", "email": "registro3@email.com", "cidade": "Sumaré"},
        {"id": 4, "nome": "Registro 4", "email": "registro4@email.com", "cidade": "Paulínia"},
        {"id": 5, "nome": "Registro 5", "email": "registro5@email.com", "cidade": "Hortolândia"},
        {"id": 6, "nome": "Registro 6", "email": "registro6@email.com", "cidade": "Vinhedo"},
    ]


def _montar_amostra_transformacao() -> list[dict[str, Any]]:
    """Eu retorno uma amostra representativa da etapa de transformação."""
    return [
        {"id": 1, "nome": "Registro 1", "email": "registro1@email.com", "cidade": "Campinas", "idade": 34},
        {"id": 2, "nome": "Registro 2", "email": "registro2@email.com", "cidade": "Valinhos", "idade": 45},
        {"id": 3, "nome": "Registro 3", "email": "registro3@email.com", "cidade": "Sumaré", "idade": 29},
        {"id": 4, "nome": "Registro 4", "email": "registro4@email.com", "cidade": "Paulínia", "idade": 51},
        {"id": 5, "nome": "Registro 5", "email": "registro5@email.com", "cidade": "Hortolândia", "idade": 27},
        {"id": 6, "nome": "Registro 6", "email": "registro6@email.com", "cidade": "Vinhedo", "idade": 39},
    ]


def _montar_amostra_upsert() -> list[dict[str, Any]]:
    """Eu retorno uma amostra representativa da etapa final de carga."""
    return [
        {"id": 1, "nome": "Registro 1", "status_carga": "Atualizado", "data_carga": "2026-03-23 08:00:11"},
        {"id": 2, "nome": "Registro 2", "status_carga": "Inserido", "data_carga": "2026-03-23 08:00:12"},
        {"id": 3, "nome": "Registro 3", "status_carga": "Atualizado", "data_carga": "2026-03-23 08:00:13"},
        {"id": 4, "nome": "Registro 4", "status_carga": "Atualizado", "data_carga": "2026-03-23 08:00:14"},
        {"id": 5, "nome": "Registro 5", "status_carga": "Inserido", "data_carga": "2026-03-23 08:00:15"},
        {"id": 6, "nome": "Registro 6", "status_carga": "Atualizado", "data_carga": "2026-03-23 08:00:16"},
    ]


def obter_dashboard_exemplo(dag_id: str) -> dict[str, Any]:
    """Eu devolvo uma estrutura de dashboard compatível com o front do plugin."""

    nome_amigavel = _humanizar_texto(dag_id)
    nome_base_tabela = dag_id.lower().replace("-", "_")

    tabela_stage = f"stage.{nome_base_tabela}"
    view_tratada = f"vw_{nome_base_tabela}_tratada"
    tabela_final = f"silver.{nome_base_tabela}"
    procedure_final = f"sp_upsert_{nome_base_tabela}"

    return {
        "dag_id": dag_id,
        "nome": nome_amigavel,
        "status": "Executando",
        "proxima_execucao": "2026-03-23 10:00",
        "ultima_execucao": "2026-03-23 08:00",
        "descricao_curta": _gerar_descricao_curta(dag_id),
        "descricao": _gerar_descricao_detalhada(dag_id),
        "agendamento": "Diariamente às 10:00",
        "inicio": "2026-03-01",
        "owner": "Airflow",
        "tags": ["ETL", "SQL Server", "Pipeline", "Auditoria", "Monitoramento"],
        "health": {
            "dag": "Executando",
            "tasks_saudaveis": "3/3",
            "broker": "Redis - online",
            "fila": "1381",
            "executando": "12",
            "workers_online": "3",
            "percentual_geral": 93,
        },
        "tasks": [
            {
                "id": "ler_tabela",
                "nome": "Ler Tabela",
                "status": "Sucesso",
                "subtitulo": "Leitura da origem de dados",
                "objetivo": "Extrair os dados da origem inicial da pipeline",
                "tipo": "PythonOperator",
                "operacao": "ler_dados_origem",
                "descricao": (
                    "Realiza a leitura inicial da origem de dados, validando a disponibilidade do arquivo "
                    "ou estrutura de entrada antes do processamento."
                ),
                "fonte_dados": f"arquivo://lake/{nome_base_tabela}.parquet",
                "metricas": {
                    "linhas_processadas": "125.430",
                    "tempo_execucao": "00:00:18",
                    "tentativas": "1",
                    "ultimo_status": "Sucesso",
                },
                "objetos": [
                    {
                        "tipo": "Arquivo",
                        "nome": f"{nome_base_tabela}.parquet",
                        "descricao": "Arquivo de origem lido na primeira etapa da DAG.",
                    },
                    {
                        "tipo": "Tabela",
                        "nome": tabela_stage,
                        "descricao": "Estrutura intermediária usada para disponibilizar os dados lidos.",
                    },
                ],
                "sql": (
                    f"SELECT *\n"
                    f"FROM OPENROWSET(\n"
                    f"    BULK 'lake/{nome_base_tabela}.parquet',\n"
                    f"    FORMAT = 'PARQUET'\n"
                    f") AS origem;"
                ),
                "amostra_dados": _montar_amostra_leitura(),
            },
            {
                "id": "tratar_dados",
                "nome": "Tratar Dados",
                "status": "Sucesso",
                "subtitulo": "Padronização, limpeza e enriquecimento",
                "objetivo": "Transformar e padronizar os dados para a carga final",
                "tipo": "PythonOperator",
                "operacao": "tratar_dados_pipeline",
                "descricao": (
                    "Executa limpeza de colunas, padronização de formatos, possíveis deduplicaçõe��್es, "
                    "enriquecimentos e ajustes necessários antes da carga final."
                ),
                "fonte_dados": tabela_stage,
                "metricas": {
                    "linhas_processadas": "124.982",
                    "tempo_execucao": "00:00:33",
                    "tentativas": "1",
                    "ultimo_status": "Sucesso",
                },
                "objetos": [
                    {
                        "tipo": "Tabela",
                        "nome": tabela_stage,
                        "descricao": "Origem usada na etapa de transformação.",
                    },
                    {
                        "tipo": "View",
                        "nome": view_tratada,
                        "descricao": "Camada lógica resultante após as regras de tratamento.",
                    },
                ],
                "sql": (
                    f"SELECT\n"
                    f"    id,\n"
                    f"    nome,\n"
                    f"    LOWER(email) AS email,\n"
                    f"    cidade,\n"
                    f"    idade\n"
                    f"FROM {tabela_stage};"
                ),
                "amostra_dados": _montar_amostra_transformacao(),
            },
            {
                "id": "upsert_nova_tabela",
                "nome": "Upsert Nova Tabela",
                "status": "Sucesso",
                "subtitulo": "Persistência final na camada destino",
                "objetivo": "Gravar os dados tratados na tabela final usando upsert",
                "tipo": "SQLExecuteQueryOperator",
                "operacao": "executar_upsert_final",
                "descricao": (
                    "Executa a rotina final de merge ou upsert para inserir novos registros "
                    "e atualizar registros existentes na camada de destino."
                ),
                "fonte_dados": tabela_final,
                "metricas": {
                    "linhas_processadas": "124.982",
                    "tempo_execucao": "00:00:21",
                    "tentativas": "1",
                    "ultimo_status": "Sucesso",
                },
                "objetos": [
                    {
                        "tipo": "View",
                        "nome": view_tratada,
                        "descricao": "Estrutura lógica usada como origem para a carga final.",
                    },
                    {
                        "tipo": "Tabela",
                        "nome": tabela_final,
                        "descricao": "Tabela final de destino da pipeline.",
                    },
                    {
                        "tipo": "Procedure",
                        "nome": procedure_final,
                        "descricao": "Procedure responsável pelo merge/upsert da carga final.",
                    },
                ],
                "sql": f"EXEC dbo.{procedure_final};",
                "amostra_dados": _montar_amostra_upsert(),
            },
        ],
    }