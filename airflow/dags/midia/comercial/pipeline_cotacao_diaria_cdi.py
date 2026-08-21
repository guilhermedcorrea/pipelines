from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pendulum
import requests
from airflow.sdk import dag, task
from sqlalchemy import text

from hooks.BancodeDados.SqlServer import HookSqlServer
from _libs.auditoria_task import (
    adicionar_observacao,
    adicionar_validacao,
    criar_resumo_auditoria,
    definir_amostra,
    publicar_resumo_auditoria,
    registrar_erro_no_resumo,
)


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


def obter_amostra_tabela_destino(engine, limite: int = 5) -> list[dict[str, Any]]:
    """Eu busco uma amostra das últimas linhas gravadas na tabela de destino."""
    sql = text(
        f"""
        SELECT TOP ({limite})
            DataReferencia,
            CdiPercentDiaRaw,
            CdiPercentDia,
            CdiPercentAno,
            SelicPercentDiaRaw,
            SelicPercentDia,
            SelicPercentAno
        FROM {TABELA_DESTINO}
        ORDER BY DataReferencia DESC
        """
    )

    with engine.begin() as conexao:
        resultado = conexao.execute(sql)
        linhas = resultado.mappings().all()

    amostra: list[dict[str, Any]] = []

    for linha in linhas:
        amostra.append(
            {
                "DataReferencia": str(linha["DataReferencia"]) if linha["DataReferencia"] is not None else None,
                "CdiPercentDiaRaw": str(linha["CdiPercentDiaRaw"]) if linha["CdiPercentDiaRaw"] is not None else None,
                "CdiPercentDia": str(linha["CdiPercentDia"]) if linha["CdiPercentDia"] is not None else None,
                "CdiPercentAno": str(linha["CdiPercentAno"]) if linha["CdiPercentAno"] is not None else None,
                "SelicPercentDiaRaw": str(linha["SelicPercentDiaRaw"]) if linha["SelicPercentDiaRaw"] is not None else None,
                "SelicPercentDia": str(linha["SelicPercentDia"]) if linha["SelicPercentDia"] is not None else None,
                "SelicPercentAno": str(linha["SelicPercentAno"]) if linha["SelicPercentAno"] is not None else None,
            }
        )

    return amostra


def executar_carga_taxa_selic() -> dict[str, Any]:
    """
    Eu executo a carga incremental de CDI e SELIC diária do BCB
    para a tabela Integracao.Silver.DimTaxaJurosDiaria.
    """
    resumo = criar_resumo_auditoria(
        nome_amigavel="Carga incremental de CDI e SELIC diários",
        descricao_etapa=(
            "Consulta as séries diárias do Banco Central, consolida CDI e SELIC por data, "
            "calcula versões diárias e anualizadas e faz upsert incremental na tabela Silver."
        ),
        origem_dados="API SGS do Banco Central do Brasil (séries 12 e 11)",
        destino_dados=TABELA_DESTINO,
    )

    engine = obter_engine_sql_server()

    try:
        print("=" * 100)
        print("INÍCIO DO PIPELINE - COTAÇÃO TAXA SELIC")
        print("=" * 100)

        resumo.status = "RUNNING"
        resumo.metricas_extras["conn_id_sql_server"] = CONN_ID_SQL_SERVER
        resumo.metricas_extras["url_cdi"] = URL_CDI
        resumo.metricas_extras["url_selic"] = URL_SELIC
        resumo.metricas_extras["dias_ano_anualizacao"] = DIAS_ANO
        publicar_resumo_auditoria(resumo)

        print("1) Descobrindo período incremental...")
        periodo = obter_periodo_incremental(engine)

        if periodo is None:
            print("Tabela já está atualizada até a data final segura. Nada a fazer.")

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = 0
            resumo.linhas_inseridas = 0
            resumo.linhas_atualizadas = 0
            resumo.linhas_descartadas = 0

            adicionar_validacao(
                resumo,
                nome="periodo_incremental_disponivel",
                status="ok",
                detalhe="A tabela já estava atualizada até a última data útil segura.",
            )
            adicionar_observacao(
                resumo,
                "Nenhum processamento foi necessário porque não havia novas datas para carregar.",
            )

            amostra_tabela = obter_amostra_tabela_destino(engine, limite=5)
            definir_amostra(resumo, amostra_tabela, limite=5)

            publicar_resumo_auditoria(resumo)

            return {
                "dias_processados": 0,
                "periodo": None,
                "tabela_destino": TABELA_DESTINO,
            }

        data_ini, data_fim = periodo

        resumo.metricas_extras["periodo_processado"] = {
            "data_ini": data_ini,
            "data_fim": data_fim,
        }
        publicar_resumo_auditoria(resumo)

        print(f"PERÍODO: {data_ini} até {data_fim}")
        print(f"CDI endpoint: {URL_CDI}")
        print(f"SELIC endpoint: {URL_SELIC}")

        print("2) Baixando série CDI...")
        lista_cdi = baixar_serie(URL_CDI, data_ini, data_fim)

        print("3) Baixando série SELIC...")
        lista_selic = baixar_serie(URL_SELIC, data_ini, data_fim)

        resumo.metricas_extras["quantidade_registros_api_cdi"] = len(lista_cdi)
        resumo.metricas_extras["quantidade_registros_api_selic"] = len(lista_selic)

        adicionar_validacao(
            resumo,
            nome="retorno_api_bcb",
            status="ok" if (lista_cdi or lista_selic) else "alerta",
            detalhe=(
                f"CDI retornou {len(lista_cdi)} registros e SELIC retornou {len(lista_selic)} registros "
                f"para o período {data_ini} até {data_fim}."
            ),
        )
        publicar_resumo_auditoria(resumo)

        print("4) Consolidando mapa por data...")
        mapa_taxas = preparar_mapa_taxas(lista_cdi, lista_selic)

        if not mapa_taxas:
            print("BCB não retornou dados para o período.")

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = 0
            resumo.linhas_inseridas = 0
            resumo.linhas_atualizadas = 0
            resumo.linhas_descartadas = 0

            adicionar_validacao(
                resumo,
                nome="mapa_taxas_preenchido",
                status="alerta",
                detalhe="As APIs não retornaram dados consolidados para o período solicitado.",
            )
            adicionar_observacao(
                resumo,
                "O processo terminou sem erro, porém sem dados novos para gravar na tabela de destino.",
            )

            amostra_tabela = obter_amostra_tabela_destino(engine, limite=5)
            definir_amostra(resumo, amostra_tabela, limite=5)

            publicar_resumo_auditoria(resumo)

            return {
                "dias_processados": 0,
                "periodo": {"data_ini": data_ini, "data_fim": data_fim},
                "tabela_destino": TABELA_DESTINO,
            }

        total_dias_consolidados = len(mapa_taxas)
        resumo.linhas_lidas = total_dias_consolidados

        adicionar_validacao(
            resumo,
            nome="mapa_taxas_preenchido",
            status="ok",
            detalhe=f"Foram consolidadas {total_dias_consolidados} datas com informação de CDI e/ou SELIC.",
        )

        amostra_taxas: list[dict[str, Any]] = []
        for data_referencia in sorted(mapa_taxas.keys())[:5]:
            item = mapa_taxas[data_referencia]
            amostra_taxas.append(
                {
                    "DataReferencia": str(data_referencia),
                    "CdiPercentDiaRaw": str(item.get("cdi_raw")) if item.get("cdi_raw") is not None else None,
                    "SelicPercentDiaRaw": str(item.get("selic_raw")) if item.get("selic_raw") is not None else None,
                }
            )

        definir_amostra(resumo, amostra_taxas, limite=5)
        publicar_resumo_auditoria(resumo)

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

        resumo.linhas_inseridas = total_upserts
        resumo.linhas_atualizadas = 0
        resumo.linhas_descartadas = max(total_dias_consolidados - total_upserts, 0)

        adicionar_validacao(
            resumo,
            nome="upsert_sql_server_concluido",
            status="ok",
            detalhe=f"Foram executados {total_upserts} upserts na tabela de destino.",
        )
        publicar_resumo_auditoria(resumo)

        print("6) Consultando última data carregada...")
        sql_ultima = text("SELECT MAX(DataReferencia) AS ultima_data FROM Silver.DimTaxaJurosDiaria")
        with engine.begin() as conexao:
            ultima_data = conexao.execute(sql_ultima).scalar()

        resumo.status = "SUCCESS"
        resumo.metricas_extras["ultima_data_tabela"] = str(ultima_data) if ultima_data is not None else None

        adicionar_validacao(
            resumo,
            nome="ultima_data_consultada",
            status="ok",
            detalhe=f"A última data presente na tabela após a carga é {ultima_data}.",
        )

        adicionar_observacao(
            resumo,
            "O processo utiliza o último dia útil anterior como data final segura para evitar consultar um dia ainda não consolidado pelo BCB.",
        )

        amostra_tabela_final = obter_amostra_tabela_destino(engine, limite=5)
        definir_amostra(resumo, amostra_tabela_final, limite=5)

        publicar_resumo_auditoria(resumo)

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

    except Exception as erro:
        resumo.status = "FAILED"
        registrar_erro_no_resumo(resumo, erro)
        publicar_resumo_auditoria(resumo)
        raise

    finally:
        engine.dispose()


@dag(
    dag_id="pipeline_cotacao_diaria_taxa_selic",
    description="ETL incremental das taxas diárias CDI e SELIC do BCB para SQL Server Integracao.Silver.DimTaxaJurosDiaria",
    schedule="0 10,19 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 19, 10, 0, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["ETL", "Midia", "Cotação TAXA SELIC"],
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
    - Auditoria estruturada de execução via plugin customizado
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