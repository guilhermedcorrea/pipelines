from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pendulum
import requests
from airflow.sdk import dag, task
from sqlalchemy import text

from hooks.BancodeDados.SqlServer import HookSqlServer


URL_CDI = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
URL_SELIC = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados"

DIAS_ANO = 252
CONN_ID_SQL_SERVER = "mssql_integracao"
TABELA_DESTINO = "[Integracao].[Silver].[DimTaxaJurosDiaria]"


def obter_engine_sql_server():
    """Eu obtenho a engine SQLAlchemy a partir do hook customizado do Airflow."""
    hook_sql_server = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
    return hook_sql_server.obter_engine()


def obter_data_final_segura() -> date:
    """Eu retorno a última data útil segura para consulta no BCB."""
    hoje = date.today()
    data_final = hoje - timedelta(days=1)

    while data_final.weekday() >= 5:
        data_final = data_final - timedelta(days=1)

    return data_final


def obter_periodo_incremental(engine) -> tuple[str, str] | None:
    """Eu descubro o período incremental a processar com base no banco."""
    data_fim = obter_data_final_segura()

    sql = text(
        """
        SELECT
            COUNT(*) AS total,
            MAX(DataReferencia) AS max_data
        FROM Silver.DimTaxaJurosDiaria
        """
    )

    with engine.begin() as conexao:
        resultado = conexao.execute(sql).fetchone()

    total = resultado[0]
    max_data = resultado[1]

    if total == 0 or max_data is None:
        data_inicio = date(2019, 1, 1)
    else:
        if isinstance(max_data, datetime):
            max_data = max_data.date()
        data_inicio = max_data + timedelta(days=1)

    if data_inicio > data_fim:
        return None

    return (
        data_inicio.strftime("%d/%m/%Y"),
        data_fim.strftime("%d/%m/%Y"),
    )


def baixar_serie(url_base: str, data_ini: str, data_fim: str) -> list[dict[str, Any]]:
    """Eu baixo a série do BCB no período informado."""
    url = f"{url_base}?formato=json&dataInicial={data_ini}&dataFinal={data_fim}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    resposta = requests.get(url, headers=headers, timeout=60)

    if resposta.status_code == 404:
        return []

    resposta.raise_for_status()

    dados = resposta.json()
    if not isinstance(dados, list):
        raise RuntimeError(f"BCB retornou algo inesperado: {type(dados)}")

    return dados


def anualizar_percent_dia(percent_dia: Decimal) -> Decimal:
    """Eu anualizo a taxa diária considerando 252 dias úteis."""
    taxa_dia_decimal = percent_dia / Decimal("100")
    taxa_ano_decimal = (Decimal("1") + taxa_dia_decimal) ** Decimal(DIAS_ANO) - Decimal("1")
    taxa_ano_percent = taxa_ano_decimal * Decimal("100")
    return taxa_ano_percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def arredondar_2_casas(valor: Decimal) -> Decimal:
    """Eu arredondo o valor para 2 casas decimais."""
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_decimal_api(valor: str) -> Decimal:
    """Eu converto o valor textual da API em Decimal."""
    return Decimal(valor.strip())


def preparar_mapa_taxas(
    lista_cdi: list[dict[str, Any]],
    lista_selic: list[dict[str, Any]],
) -> dict[date, dict[str, Decimal]]:
    """Eu monto um mapa por data contendo CDI e SELIC raw."""
    mapa: dict[date, dict[str, Decimal]] = {}

    for item in lista_cdi:
        data_referencia = datetime.strptime(item["data"], "%d/%m/%Y").date()
        valor_raw = parse_decimal_api(item["valor"])
        mapa.setdefault(data_referencia, {})["cdi_raw"] = valor_raw

    for item in lista_selic:
        data_referencia = datetime.strptime(item["data"], "%d/%m/%Y").date()
        valor_raw = parse_decimal_api(item["valor"])
        mapa.setdefault(data_referencia, {})["selic_raw"] = valor_raw

    return mapa


def upsert_dia(
    conexao,
    data_referencia: date,
    cdi_raw,
    cdi_dia,
    cdi_ano,
    selic_raw,
    selic_dia,
    selic_ano,
) -> None:
    """Eu faço upsert de uma data na Silver.DimTaxaJurosDiaria."""
    sql = text(
        f"""
        MERGE {TABELA_DESTINO} AS t
        USING (SELECT :DataReferencia AS DataReferencia) s
            ON t.DataReferencia = s.DataReferencia
        WHEN MATCHED THEN
            UPDATE SET
                CdiPercentDiaRaw = :CdiPercentDiaRaw,
                CdiPercentDia = :CdiPercentDia,
                CdiPercentAno = :CdiPercentAno,
                SelicPercentDiaRaw = :SelicPercentDiaRaw,
                SelicPercentDia = :SelicPercentDia,
                SelicPercentAno = :SelicPercentAno,
                DataAtualizacao = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN
            INSERT
            (
                DataReferencia,
                CdiPercentDiaRaw,
                CdiPercentDia,
                CdiPercentAno,
                SelicPercentDiaRaw,
                SelicPercentDia,
                SelicPercentAno,
                DataAtualizacao
            )
            VALUES
            (
                :DataReferencia,
                :CdiPercentDiaRaw,
                :CdiPercentDia,
                :CdiPercentAno,
                :SelicPercentDiaRaw,
                :SelicPercentDia,
                :SelicPercentAno,
                SYSUTCDATETIME()
            );
        """
    )

    parametros = {
        "DataReferencia": data_referencia,
        "CdiPercentDiaRaw": cdi_raw,
        "CdiPercentDia": cdi_dia,
        "CdiPercentAno": cdi_ano,
        "SelicPercentDiaRaw": selic_raw,
        "SelicPercentDia": selic_dia,
        "SelicPercentAno": selic_ano,
    }

    conexao.execute(sql, parametros)


def executar_carga_taxa_selic() -> dict[str, Any]:
    """
    Eu executo a carga incremental de CDI e SELIC diária do BCB
    para a tabela Integracao.Silver.DimTaxaJurosDiaria.
    """
    engine = obter_engine_sql_server()

    try:
        print("=" * 100)
        print("INÍCIO DO PIPELINE - COTAÇÃO TAXA SELIC")
        print("=" * 100)

        print("1) Descobrindo período incremental...")
        periodo = obter_periodo_incremental(engine)

        if periodo is None:
            print("Tabela já está atualizada até a data final segura. Nada a fazer.")
            return {
                "dias_processados": 0,
                "periodo": None,
                "tabela_destino": TABELA_DESTINO,
            }

        data_ini, data_fim = periodo

        print(f"PERÍODO: {data_ini} até {data_fim}")
        print(f"CDI endpoint: {URL_CDI}")
        print(f"SELIC endpoint: {URL_SELIC}")

        print("2) Baixando série CDI...")
        lista_cdi = baixar_serie(URL_CDI, data_ini, data_fim)

        print("3) Baixando série SELIC...")
        lista_selic = baixar_serie(URL_SELIC, data_ini, data_fim)

        print("4) Consolidando mapa por data...")
        mapa_taxas = preparar_mapa_taxas(lista_cdi, lista_selic)

        if not mapa_taxas:
            print("BCB não retornou dados para o período.")
            return {
                "dias_processados": 0,
                "periodo": {"data_ini": data_ini, "data_fim": data_fim},
                "tabela_destino": TABELA_DESTINO,
            }

        print("5) Fazendo upsert incremental no SQL Server...")
        total_upserts = 0

        with engine.begin() as conexao:
            for data_referencia in sorted(mapa_taxas.keys()):
                cdi_raw = mapa_taxas[data_referencia].get("cdi_raw")
                selic_raw = mapa_taxas[data_referencia].get("selic_raw")

                cdi_dia = arredondar_2_casas(cdi_raw) if cdi_raw is not None else None
                selic_dia = arredondar_2_casas(selic_raw) if selic_raw is not None else None

                cdi_ano = anualizar_percent_dia(cdi_raw) if cdi_raw is not None else None
                selic_ano = anualizar_percent_dia(selic_raw) if selic_raw is not None else None

                upsert_dia(
                    conexao=conexao,
                    data_referencia=data_referencia,
                    cdi_raw=cdi_raw,
                    cdi_dia=cdi_dia,
                    cdi_ano=cdi_ano,
                    selic_raw=selic_raw,
                    selic_dia=selic_dia,
                    selic_ano=selic_ano,
                )
                total_upserts += 1

        print("6) Consultando última data carregada...")
        sql_ultima = text("SELECT MAX(DataReferencia) AS ultima_data FROM Silver.DimTaxaJurosDiaria")
        with engine.begin() as conexao:
            ultima_data = conexao.execute(sql_ultima).scalar()

        print(f"OK. Dias processados: {total_upserts}")
        print(f"Última data na tabela: {ultima_data}")
        print("=" * 100)
        print("FIM DO PIPELINE - COTAÇÃO TAXA SELIC")
        print("=" * 100)

        return {
            "dias_processados": total_upserts,
            "periodo": {"data_ini": data_ini, "data_fim": data_fim},
            "ultima_data_tabela": str(ultima_data) if ultima_data is not None else None,
            "tabela_destino": TABELA_DESTINO,
        }

    finally:
        engine.dispose()


@dag(
    dag_id="pipeline_cotacao_diaria_taxa_selic",
    description="ETL incremental das taxas diárias CDI e SELIC do BCB para SQL Server Integracao.Silver.DimTaxaJurosDiaria",
    schedule="0 10,19 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 19, 10, 0, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["ETL", "Euromidia", "Cotação TAXA SELIC"],
    max_active_runs=1,
)
def pipeline_cotacao_diaria_taxa_selic():
    """
    ### ETL de CDI e SELIC diários -> SQL Server (Integracao.Silver.DimTaxaJurosDiaria)

    **O que este DAG faz:**
    - Descobre o período incremental com base na última `DataReferencia` gravada
    - Consulta as séries do BCB:
      - SGS 12 = CDI diário
      - SGS 11 = SELIC diária
    - Consolida os dados por data
    - Calcula:
      - valor raw diário
      - valor diário arredondado para 2 casas
      - anualização com base em 252 dias úteis
    - Faz `MERGE` por `DataReferencia` na tabela `Integracao.Silver.DimTaxaJurosDiaria`

    **Regras do processo:**
    - Usa como data final segura o último dia útil anterior
    - Se a tabela já estiver atualizada, encerra sem processar
    - Se o BCB não retornar dados no período, encerra sem erro

    **Connection esperada no Airflow:**
    - `mssql_integracao`

    **Tecnologia usada:**
    - Airflow DAG + TaskFlow API
    - Hook customizado `HookSqlServer`
    - SQL Server via SQLAlchemy
    - API do Banco Central do Brasil
    """

    @task(
        task_id="executar_carga_taxa_selic",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=2),
    )
    def tarefa_executar_carga_taxa_selic() -> dict[str, Any]:
        """Eu executo o pipeline incremental de CDI e SELIC."""
        return executar_carga_taxa_selic()

    tarefa_executar_carga_taxa_selic()


pipeline_cotacao_diaria_taxa_selic_dag = pipeline_cotacao_diaria_taxa_selic()