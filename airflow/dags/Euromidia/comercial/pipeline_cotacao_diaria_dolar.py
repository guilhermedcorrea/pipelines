from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

import pendulum
import requests
from airflow.sdk import dag, task
from hooks.BancodeDados.SqlServer import HookSqlServer


URL_INICIAL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
    "?@dataInicial='01-01-2016'&@dataFinalCotacao='03-02-2026'&$format=json"
)

TIMEOUT_HTTP = 60
TAMANHO_LOTE_INSERT = 5000
CASAS_5 = Decimal("0.00001")
CONN_ID_SQL_SERVER = "mssql_integracao"
TABELA_DESTINO = "[Integracao].[Silver].[DimCotacaoDolar]"


def obter_engine_sql_server():
    """Eu obtenho a engine SQLAlchemy a partir do hook customizado do Airflow."""
    hook_sql_server = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
    return hook_sql_server.obter_engine()


def quantizar_5_casas_para_string(valor: Any) -> str:
    """Eu converto o valor numérico para texto decimal com 5 casas."""
    decimal_valor = Decimal(str(valor)).quantize(CASAS_5, rounding=ROUND_HALF_UP)
    return format(decimal_valor, "f")


def parsear_datahora_bcb(data_hora_str: str) -> datetime:
    """Eu converto a data/hora do BCB em datetime Python."""
    return datetime.fromisoformat(data_hora_str)


def normalizar_para_datetime2_3(data_hora: datetime) -> datetime:
    """
    Eu normalizo para a precisão do SQL DATETIME2(3),
    arredondando para o milissegundo mais próximo.
    """
    microssegundos = data_hora.microsecond
    milissegundos_float = microssegundos / 1000.0
    milissegundos_int = int(
        Decimal(str(milissegundos_float)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    if milissegundos_int >= 1000:
        data_hora = data_hora.replace(microsecond=0) + timedelta(seconds=1)
        milissegundos_int = 0

    return data_hora.replace(microsecond=milissegundos_int * 1000)


def baixar_pagina(
    sessao_http: requests.Session,
    url: str,
    tentativas: int = 6,
    pausa_segundos: float = 0.8,
) -> Dict[str, Any]:
    """Eu baixo uma página do endpoint do BCB com retry simples."""
    ultima_excecao: Optional[Exception] = None

    for tentativa in range(tentativas):
        try:
            resposta = sessao_http.get(url, timeout=TIMEOUT_HTTP)
            resposta.raise_for_status()
            return resposta.json()
        except Exception as excecao:
            ultima_excecao = excecao
            time.sleep(pausa_segundos * (tentativa + 1))

    raise RuntimeError(
        f"Falha ao baixar página do BCB após {tentativas} tentativas. "
        f"Último erro: {ultima_excecao}"
    )


def iterar_cotacoes_bcb(
    sessao_http: requests.Session,
    url_inicial: str,
):
    """
    Eu itero as cotações do BCB com paginação OData.

    Retorno:
    - DataHoraCotacao normalizada para DATETIME2(3)
    - CotacaoCompra em texto com 5 casas
    - CotacaoVenda em texto com 5 casas
    """
    url_atual = url_inicial

    while url_atual:
        payload = baixar_pagina(sessao_http=sessao_http, url=url_atual)

        registros = payload.get("value") or []
        for registro in registros:
            data_hora = str(registro.get("dataHoraCotacao") or "").strip()
            if not data_hora:
                continue

            cotacao_compra_raw = registro.get("cotacaoCompra")
            cotacao_venda_raw = registro.get("cotacaoVenda")

            if cotacao_compra_raw is None or cotacao_venda_raw is None:
                continue

            data_hora_python = parsear_datahora_bcb(data_hora)
            data_hora_sql = normalizar_para_datetime2_3(data_hora_python)

            cotacao_compra_str = quantizar_5_casas_para_string(cotacao_compra_raw)
            cotacao_venda_str = quantizar_5_casas_para_string(cotacao_venda_raw)

            yield (
                data_hora_sql,
                cotacao_compra_str,
                cotacao_venda_str,
            )

        proximo_link = payload.get("@odata.nextLink") or payload.get("odata.nextLink")
        url_atual = str(proximo_link).strip() if proximo_link else ""


def garantir_schema_silver(cursor) -> None:
    """Eu garanto que o schema Silver exista no banco."""
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'Silver')
        BEGIN
            EXEC('CREATE SCHEMA Silver');
        END
        """
    )


def inserir_dim_cotacao_dolar_sem_duplicar() -> dict[str, Any]:
    """
    Eu faço a carga incremental da PTAX do BCB para Integracao.Silver.DimCotacaoDolar.

    Estratégia para não estourar memória:
    - Eu não acumulo tudo em lista gigante.
    - Eu leio página por página.
    - Eu deduplico por DataHoraCotacao dentro do lote atual.
    - Eu gravo em staging em lotes.
    - Depois faço MERGE no destino.
    """
    engine = obter_engine_sql_server()
    total_lido = 0
    total_unico_staging = 0

    try:
        conexao_bruta = engine.raw_connection()
        conexao_bruta.autocommit = False
        cursor = conexao_bruta.cursor()

        try:
            print("=" * 100)
            print("INÍCIO DO PIPELINE - COTAÇÃO DIÁRIA DÓLAR")
            print("=" * 100)

            print("1) Garantindo schema Silver...")
            garantir_schema_silver(cursor)
            conexao_bruta.commit()

            print("2) Criando staging temporária...")
            cursor.execute(
                """
                IF OBJECT_ID('tempdb..#StgCotacaoDolar') IS NOT NULL
                    DROP TABLE #StgCotacaoDolar;

                CREATE TABLE #StgCotacaoDolar
                (
                    DataHoraCotacao  DATETIME2(3)   NOT NULL,
                    CotacaoCompra    DECIMAL(18,5)  NOT NULL,
                    CotacaoVenda     DECIMAL(18,5)  NOT NULL
                );

                CREATE INDEX IX_StgCotacaoDolar_DataHora
                    ON #StgCotacaoDolar (DataHoraCotacao);
                """
            )
            conexao_bruta.commit()

            sql_insert_staging = """
                INSERT INTO #StgCotacaoDolar
                (
                    DataHoraCotacao,
                    CotacaoCompra,
                    CotacaoVenda
                )
                VALUES
                (
                    ?,
                    CAST(? AS DECIMAL(18,5)),
                    CAST(? AS DECIMAL(18,5))
                );
            """

            cursor.fast_executemany = True
            lote_dict: dict[datetime, tuple[datetime, str, str]] = {}

            def flush_lote() -> int:
                """Eu despejo o lote atual na staging."""
                nonlocal lote_dict

                if not lote_dict:
                    return 0

                dados = list(lote_dict.values())
                cursor.executemany(sql_insert_staging, dados)
                conexao_bruta.commit()

                quantidade = len(dados)
                lote_dict.clear()
                return quantidade

            print("3) Baixando páginas do BCB e gravando staging em lotes...")
            with requests.Session() as sessao_http:
                sessao_http.headers.update({"User-Agent": "airflow-pipeline-cotacao-dolar/1.0"})

                for data_hora_sql, compra_str, venda_str in iterar_cotacoes_bcb(
                    sessao_http=sessao_http,
                    url_inicial=URL_INICIAL,
                ):
                    total_lido += 1

                    # deduplicação no lote pela chave DataHoraCotacao
                    lote_dict[data_hora_sql] = (data_hora_sql, compra_str, venda_str)

                    if len(lote_dict) >= TAMANHO_LOTE_INSERT:
                        total_unico_staging += flush_lote()
                        print(
                            f"Lote gravado na staging. "
                            f"Total lido bruto: {total_lido:,} | "
                            f"Total único staging: {total_unico_staging:,}"
                        )

            total_unico_staging += flush_lote()

            print("4) Fazendo MERGE no destino...")
            cursor.execute(
                f"""
                MERGE {TABELA_DESTINO} AS tgt
                USING
                (
                    SELECT
                        s.DataHoraCotacao,
                        MAX(s.CotacaoCompra) AS CotacaoCompra,
                        MAX(s.CotacaoVenda) AS CotacaoVenda
                    FROM #StgCotacaoDolar s
                    GROUP BY s.DataHoraCotacao
                ) AS src
                    ON tgt.DataHoraCotacao = src.DataHoraCotacao

                WHEN MATCHED AND
                    (
                        tgt.CotacaoCompra <> src.CotacaoCompra
                        OR tgt.CotacaoVenda <> src.CotacaoVenda
                    )
                    THEN UPDATE SET
                        tgt.CotacaoCompra = src.CotacaoCompra,
                        tgt.CotacaoVenda = src.CotacaoVenda

                WHEN NOT MATCHED BY TARGET THEN
                    INSERT
                    (
                        DataHoraCotacao,
                        CotacaoCompra,
                        CotacaoVenda
                    )
                    VALUES
                    (
                        src.DataHoraCotacao,
                        src.CotacaoCompra,
                        src.CotacaoVenda
                    );
                """
            )
            conexao_bruta.commit()

            print("5) Limpando staging temporária...")
            cursor.execute("DROP TABLE #StgCotacaoDolar;")
            conexao_bruta.commit()

            print(f"Total lido do BCB (bruto): {total_lido:,}")
            print(f"Total único por DataHoraCotacao (staging): {total_unico_staging:,}")
            print("Carga finalizada: Silver.DimCotacaoDolar atualizada sem duplicar.")
            print("=" * 100)
            print("FIM DO PIPELINE - COTAÇÃO DIÁRIA DÓLAR")
            print("=" * 100)

            return {
                "total_lido_bcb_bruto": total_lido,
                "total_unico_staging": total_unico_staging,
                "tabela_destino": TABELA_DESTINO,
            }

        except Exception:
            conexao_bruta.rollback()
            raise
        finally:
            conexao_bruta.close()

    finally:
        engine.dispose()


@dag(
    dag_id="pipeline_cotacao_diaria_dolar",
    description="ETL da cotação diária do dólar PTAX do BCB para SQL Server Integracao.Silver.DimCotacaoDolar",
    schedule="0 10,19 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 19, 10, 0, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["ETL", "Euromidia", "Cotação Dolar"],
    max_active_runs=1,
)
def pipeline_cotacao_diaria_dolar():
    """
    ### ETL PTAX (BCB) -> SQL Server (Integracao.Silver.DimCotacaoDolar)

    **O que este DAG faz:**
    - Busca cotações no endpoint OData do BCB (PTAX) com paginação via `nextLink`
    - Carrega em staging temporária `#StgCotacaoDolar`
    - Faz `MERGE` na tabela `Integracao.Silver.DimCotacaoDolar` sem duplicar
    - Deduplica por `DataHoraCotacao`
    - Envia cotação como texto e converte no SQL para evitar perda de precisão decimal

    **Cuidados de memória e estabilidade:**
    - Não acumula toda a API em memória
    - Processa página por página
    - Grava em lotes na staging
    - Faz deduplicação por lote
    - Usa uma única task para manter a sessão transacional simples

    **Connection esperada no Airflow:**
    - `mssql_integracao`

    **Requisitos de ambiente:**
    - `requests`
    - `pyodbc`
    - `ODBC Driver 18 for SQL Server`
    - Hook customizado `HookSqlServer`
    """

    @task(
        task_id="executar_carga_cotacao_dolar",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=2),
    )
    def tarefa_executar_carga_cotacao_dolar() -> dict[str, Any]:
        """Eu executo a carga da cotação diária do dólar."""
        return inserir_dim_cotacao_dolar_sem_duplicar()

    tarefa_executar_carga_cotacao_dolar()


pipeline_cotacao_diaria_dolar_dag = pipeline_cotacao_diaria_dolar()