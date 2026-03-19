import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import numpy as np
import pandas as pd
import pendulum
import yfinance as yf
from airflow.sdk import dag, task

from hooks.BancodeDados.SqlServer import HookSqlServer


@dataclass(frozen=True)
class ConfiguracaoIndiceOOH:
    """Eu centralizo as configurações do pipeline do índice OOH."""

    data_inicial: str = "2015-01-01"
    data_final_inclusiva: str = "2026-02-21"

    conn_id_sql_server: str = "mssql_integracao"

    tabela_indice_consumo: str = "[DataMining].[Silver].[FatoCotacaoDiariaIndiceConsumo]"
    tabela_indice_imobiliario: str = "[DataMining].[Silver].[FatoCotacaoDiariaIndiceImobiliario]"
    tabela_indice_industrial: str = "[DataMining].[Silver].[FatoCotacaoDiariaIndiceIndustrial]"
    tabela_cdi: str = "[Integracao].[Silver].[DimTaxaJurosDiaria]"
    tabela_indice_ooh_destino: str = "[Integracao].[Silver].[FatoIndiceOOHDiario]"

    casas_decimais_sql: Decimal = Decimal("0.000001")
    tamanho_lote_sql: int = 500

    acoes_ooh_brasil: tuple[str, ...] = (
        "VIVT3.SA", "TIMS3.SA",
        "DIRR3.SA", "MRVE3.SA", "CYRE3.SA", "EZTC3.SA", "JHSF3.SA",
        "COGN3.SA", "YDUQ3.SA",
        "POMO4.SA",
        "DASA3.SA", "HAPV3.SA", "RDOR3.SA", "RADL3.SA",
        "MGLU3.SA", "BHIA3.SA", "LREN3.SA", "AZZA3.SA", "VIVA3.SA",
        "ITSA4.SA",
        "BBAS3.SA", "ITUB4.SA", "BBDC4.SA",
    )

    pesos_empresas: dict[str, float] | None = None
    pesos_blocos: dict[str, float] | None = None


def obter_pesos_empresas() -> dict[str, float]:
    """Eu retorno os pesos das ações da carteira OOH."""
    return {
        "VIVT3.SA": 0.26225,
        "TIMS3.SA": 0.26225,
        "DIRR3.SA": 0.03156,
        "MRVE3.SA": 0.03156,
        "CYRE3.SA": 0.03156,
        "EZTC3.SA": 0.03156,
        "JHSF3.SA": 0.03156,
        "COGN3.SA": 0.06345,
        "YDUQ3.SA": 0.06345,
        "POMO4.SA": 0.07630,
        "DASA3.SA": 0.010925,
        "HAPV3.SA": 0.010925,
        "RDOR3.SA": 0.010925,
        "RADL3.SA": 0.010925,
        "MGLU3.SA": 0.0048,
        "BHIA3.SA": 0.0048,
        "LREN3.SA": 0.0048,
        "AZZA3.SA": 0.0048,
        "VIVA3.SA": 0.0048,
        "ITSA4.SA": 0.0166,
        "BBAS3.SA": 0.00333333333,
        "ITUB4.SA": 0.00333333333,
        "BBDC4.SA": 0.00333333333,
    }


def obter_pesos_blocos() -> dict[str, float]:
    """Eu retorno os pesos dos blocos do índice OOH."""
    return {
        "carteira_empresas": 0.60,
        "indice_consumo": 0.15,
        "indice_imobiliario": 0.10,
        "indice_industrial": 0.10,
        "cdi": 0.05,
    }


def criar_engine_sql(conn_id: str):
    """Eu crio a engine SQL Server a partir do hook customizado do Airflow."""
    hook_sql_server = HookSqlServer(conn_id=conn_id)
    return hook_sql_server.obter_engine()


def normalizar_pesos(dicionario_pesos: dict[str, float]) -> dict[str, float]:
    """Eu normalizo os pesos para a soma fechar exatamente em 1."""
    soma = sum(float(valor) for valor in dicionario_pesos.values())
    if soma == 0:
        raise ValueError("A soma dos pesos é zero.")
    return {chave: float(valor) / soma for chave, valor in dicionario_pesos.items()}


def normalizar_indice_para_data_pura(indice) -> pd.DatetimeIndex:
    """Eu deixo o índice só com a data, sem hora e sem timezone."""
    indice = pd.to_datetime(indice)

    if isinstance(indice, pd.DatetimeIndex) and indice.tz is not None:
        indice = indice.tz_localize(None)

    return indice.normalize()


def filtrar_intervalo_serie(
    serie: pd.Series,
    data_inicio: str,
    data_fim_inclusiva: str,
) -> pd.Series:
    """Eu filtro a série para o período desejado."""
    serie = serie.copy()
    serie.index = normalizar_indice_para_data_pura(serie.index)
    serie = serie.loc[
        (serie.index >= pd.to_datetime(data_inicio))
        & (serie.index <= pd.to_datetime(data_fim_inclusiva))
    ]
    serie = serie[~serie.index.duplicated(keep="last")]
    serie = serie.sort_index()
    return serie


def extrair_close_serie(dados: pd.DataFrame, nome_saida: str) -> pd.Series:
    """Eu extraio a coluna Close do retorno do Yahoo Finance."""
    if dados is None or dados.empty:
        raise ValueError(f"{nome_saida} veio vazio.")

    if "Close" not in dados.columns:
        raise ValueError(f"{nome_saida} não possui coluna Close.")

    close = dados["Close"]

    if isinstance(close, pd.DataFrame):
        serie = close.iloc[:, 0].copy()
    else:
        serie = close.copy()

    serie = pd.to_numeric(serie, errors="coerce")
    serie.name = nome_saida
    return serie


def baixar_acao_individual(
    ticker: str,
    data_inicio: str,
    data_fim_inclusiva: str,
) -> pd.Series:
    """Eu baixo uma ação individual do Yahoo Finance."""
    dados = yf.download(
        ticker,
        start=data_inicio,
        end=(pd.to_datetime(data_fim_inclusiva) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        progress=False,
        threads=False,
        auto_adjust=False,
    )

    if dados is None or dados.empty:
        raise ValueError(f"{ticker} veio vazio.")

    serie = extrair_close_serie(dados, ticker)
    serie = filtrar_intervalo_serie(serie, data_inicio, data_fim_inclusiva)
    serie = serie.dropna()

    if serie.empty:
        raise ValueError(f"{ticker} ficou vazio após o filtro de datas.")

    return serie


def baixar_acoes(
    lista_tickers: list[str],
    data_inicio: str,
    data_fim_inclusiva: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Eu baixo as ações uma a uma para reduzir falhas em lote."""
    lista_series: list[pd.Series] = []
    erros: list[str] = []

    for ticker in lista_tickers:
        try:
            serie = baixar_acao_individual(
                ticker=ticker,
                data_inicio=data_inicio,
                data_fim_inclusiva=data_fim_inclusiva,
            )
            lista_series.append(serie)
        except Exception as erro:
            erros.append(f"{ticker}: {erro}")

        time.sleep(0.3)

    if not lista_series:
        raise ValueError("Nenhuma ação retornou dados.\n" + "\n".join(erros))

    precos = pd.concat(lista_series, axis=1).sort_index()
    precos.index = normalizar_indice_para_data_pura(precos.index)
    precos = precos.apply(pd.to_numeric, errors="coerce")
    precos = precos[~precos.index.duplicated(keep="last")]
    precos = precos.loc[
        (precos.index >= pd.to_datetime(data_inicio))
        & (precos.index <= pd.to_datetime(data_fim_inclusiva))
    ]

    return precos, erros


def carregar_serie_indice_fato_sql(
    engine,
    tabela_sql: str,
    nome_saida: str,
    data_inicio: str,
    data_fim_inclusiva: str,
) -> pd.Series:
    """Eu leio a cotação diária tratada das tabelas fato dos índices."""
    sql = f"""
    SELECT
        [DataCotacao],
        [UltimoBRL]
    FROM {tabela_sql}
    WHERE [DataCotacao] >= '{data_inicio}'
      AND [DataCotacao] <= '{data_fim_inclusiva}'
    ORDER BY [DataCotacao]
    """

    df = pd.read_sql(sql, engine)

    if df.empty:
        raise ValueError(f"A consulta da tabela {tabela_sql} retornou vazia.")

    df["DataCotacao"] = pd.to_datetime(df["DataCotacao"]).dt.normalize()
    df["UltimoBRL"] = pd.to_numeric(df["UltimoBRL"], errors="coerce")

    serie = df.set_index("DataCotacao")["UltimoBRL"].copy()
    serie.name = nome_saida
    serie = filtrar_intervalo_serie(serie, data_inicio, data_fim_inclusiva)
    serie = serie.dropna()

    if serie.empty:
        raise ValueError(f"A série {nome_saida} ficou vazia após o tratamento.")

    return serie


def carregar_serie_cdi_sql(
    engine,
    tabela_cdi: str,
    data_inicio: str,
    data_fim_inclusiva: str,
) -> pd.Series:
    """Eu leio o CDI diário do SQL e converto para taxa decimal diária."""
    sql = f"""
    SELECT
        [DataReferencia],
        [CdiPercentDiaRaw],
        [CdiPercentDia]
    FROM {tabela_cdi}
    WHERE [DataReferencia] >= '{data_inicio}'
      AND [DataReferencia] <= '{data_fim_inclusiva}'
    ORDER BY [DataReferencia]
    """

    df = pd.read_sql(sql, engine)

    if df.empty:
        raise ValueError(f"A consulta do CDI retornou vazia em {tabela_cdi}.")

    df["DataReferencia"] = pd.to_datetime(df["DataReferencia"]).dt.normalize()
    df["CdiPercentDiaRaw"] = pd.to_numeric(df["CdiPercentDiaRaw"], errors="coerce")
    df["CdiPercentDia"] = pd.to_numeric(df["CdiPercentDia"], errors="coerce")

    df["TaxaEscolhida"] = df["CdiPercentDiaRaw"]
    mascara_sem_raw = df["TaxaEscolhida"].isna()
    df.loc[mascara_sem_raw, "TaxaEscolhida"] = df.loc[mascara_sem_raw, "CdiPercentDia"]

    serie = df.set_index("DataReferencia")["TaxaEscolhida"].copy()

    mediana_absoluta = serie.dropna().abs().median()
    if pd.notna(mediana_absoluta) and mediana_absoluta > 0.01:
        serie = serie / 100.0

    serie.name = "CDI_DIARIO"
    serie = filtrar_intervalo_serie(serie, data_inicio, data_fim_inclusiva)
    serie = serie.dropna()

    if serie.empty:
        raise ValueError("A série do CDI ficou vazia após o tratamento.")

    return serie


def calcular_retorno_carteira(precos: pd.DataFrame, pesos: dict[str, float], nome_saida: str) -> pd.Series:
    """Eu calculo o retorno diário ponderado da carteira."""
    retornos = precos.pct_change(fill_method=None)
    pesos_series = pd.Series(pesos, dtype=float).reindex(retornos.columns).fillna(0.0)

    retorno_carteira = retornos.mul(pesos_series, axis=1).sum(axis=1, min_count=1)
    retorno_carteira = retorno_carteira.fillna(0.0)
    retorno_carteira.name = nome_saida
    return retorno_carteira


def reconstruir_curva_base_100(serie_retorno: pd.Series) -> pd.Series:
    """Eu transformo os retornos em curva acumulada com base 100."""
    serie = pd.to_numeric(serie_retorno, errors="coerce").fillna(0.0)
    curva = (1.0 + serie).cumprod() * 100.0
    return curva


def montar_base_upsert_indice_ooh(
    indice_ooh: pd.Series,
    retorno_indice_ooh: pd.Series,
) -> pd.DataFrame:
    """Eu monto a base final no formato da tabela de destino."""
    base = pd.DataFrame(
        {
            "Data": pd.to_datetime(indice_ooh.index).normalize(),
            "PeriodoAnterior": indice_ooh.shift(1).values,
            "PeriodoAtual": indice_ooh.values,
            "VariacaoPercent": (retorno_indice_ooh * 100.0).values,
        }
    )

    base = base.sort_values("Data").drop_duplicates(subset=["Data"], keep="last").reset_index(drop=True)
    base = base.replace([np.inf, -np.inf], np.nan)

    base["PeriodoAnterior"] = pd.to_numeric(base["PeriodoAnterior"], errors="coerce").round(6)
    base["PeriodoAtual"] = pd.to_numeric(base["PeriodoAtual"], errors="coerce").round(6)
    base["VariacaoPercent"] = pd.to_numeric(base["VariacaoPercent"], errors="coerce").round(6)

    base = base[base["PeriodoAtual"].notna()].copy()

    if base.empty:
        raise ValueError("A base final do índice OOH ficou vazia.")

    return base


def normalizar_decimal_para_sql(
    valor,
    nome_coluna: str,
    indice_linha: int,
    casas_decimais_sql: Decimal,
) -> str | None:
    """Eu transformo o valor numérico em texto decimal seguro para o SQL Server."""
    if valor is None or pd.isna(valor):
        return None

    valor_texto = str(valor).strip()

    if valor_texto == "":
        return None

    valor_texto = valor_texto.replace(",", ".")

    try:
        valor_decimal = Decimal(valor_texto)
    except InvalidOperation as erro:
        raise ValueError(
            f"Valor inválido na coluna {nome_coluna}, linha {indice_linha}: {valor!r}"
        ) from erro

    if not valor_decimal.is_finite():
        return None

    valor_decimal = valor_decimal.quantize(
        casas_decimais_sql,
        rounding=ROUND_HALF_UP,
    )

    return format(valor_decimal, "f")


def preparar_registros_upsert_indice_ooh(
    df_upsert: pd.DataFrame,
    casas_decimais_sql: Decimal,
) -> list[tuple]:
    """Eu preparo os registros como tipos nativos e textos decimais seguros."""
    registros: list[tuple] = []

    for indice_linha, linha in enumerate(df_upsert.itertuples(index=False), start=1):
        data_linha = pd.to_datetime(linha.Data).date()

        periodo_anterior = normalizar_decimal_para_sql(
            valor=linha.PeriodoAnterior,
            nome_coluna="PeriodoAnterior",
            indice_linha=indice_linha,
            casas_decimais_sql=casas_decimais_sql,
        )
        periodo_atual = normalizar_decimal_para_sql(
            valor=linha.PeriodoAtual,
            nome_coluna="PeriodoAtual",
            indice_linha=indice_linha,
            casas_decimais_sql=casas_decimais_sql,
        )
        variacao_percent = normalizar_decimal_para_sql(
            valor=linha.VariacaoPercent,
            nome_coluna="VariacaoPercent",
            indice_linha=indice_linha,
            casas_decimais_sql=casas_decimais_sql,
        )

        if periodo_atual is None:
            raise ValueError(
                f"PeriodoAtual não pode ser nulo. Linha problemática: {indice_linha}."
            )

        registros.append(
            (
                data_linha,
                periodo_anterior,
                periodo_atual,
                variacao_percent,
            )
        )

    if not registros:
        raise ValueError("Nenhum registro foi preparado para o upsert do índice OOH.")

    return registros


def dividir_em_lotes(lista_registros: list[tuple], tamanho_lote: int) -> list[list[tuple]]:
    """Eu separo os registros em lotes para reduzir risco de erro no driver."""
    return [
        lista_registros[posicao:posicao + tamanho_lote]
        for posicao in range(0, len(lista_registros), tamanho_lote)
    ]


def fazer_upsert_indice_ooh_sql(
    engine,
    df_upsert: pd.DataFrame,
    tabela_destino: str,
    casas_decimais_sql: Decimal,
    tamanho_lote_sql: int,
) -> None:
    """Eu carrego a base final em staging temporária e faço MERGE no destino."""
    registros = preparar_registros_upsert_indice_ooh(
        df_upsert=df_upsert,
        casas_decimais_sql=casas_decimais_sql,
    )

    conexao_bruta = engine.raw_connection()

    try:
        cursor = conexao_bruta.cursor()
        cursor.fast_executemany = False

        cursor.execute("""
        IF OBJECT_ID('tempdb..#StagingIndiceOOH') IS NOT NULL
            DROP TABLE #StagingIndiceOOH;

        CREATE TABLE #StagingIndiceOOH
        (
            [Data] date NOT NULL,
            [PeriodoAnteriorTxt] varchar(64) NULL,
            [PeriodoAtualTxt] varchar(64) NOT NULL,
            [VariacaoPercentTxt] varchar(64) NULL
        );
        """)

        sql_insert_staging = """
        INSERT INTO #StagingIndiceOOH
        (
            [Data],
            [PeriodoAnteriorTxt],
            [PeriodoAtualTxt],
            [VariacaoPercentTxt]
        )
        VALUES (?, ?, ?, ?)
        """

        lotes = dividir_em_lotes(registros, tamanho_lote_sql)

        for lote in lotes:
            cursor.executemany(sql_insert_staging, lote)

        cursor.execute(f"""
        IF EXISTS
        (
            SELECT 1
            FROM #StagingIndiceOOH
            WHERE [PeriodoAtualTxt] IS NOT NULL
              AND TRY_CONVERT(decimal(18, 6), [PeriodoAtualTxt]) IS NULL
        )
        BEGIN
            THROW 50001, 'Falha ao converter PeriodoAtualTxt para decimal(18,6).', 1;
        END;

        IF EXISTS
        (
            SELECT 1
            FROM #StagingIndiceOOH
            WHERE [PeriodoAnteriorTxt] IS NOT NULL
              AND TRY_CONVERT(decimal(18, 6), [PeriodoAnteriorTxt]) IS NULL
        )
        BEGIN
            THROW 50002, 'Falha ao converter PeriodoAnteriorTxt para decimal(18,6).', 1;
        END;

        IF EXISTS
        (
            SELECT 1
            FROM #StagingIndiceOOH
            WHERE [VariacaoPercentTxt] IS NOT NULL
              AND TRY_CONVERT(decimal(12, 6), [VariacaoPercentTxt]) IS NULL
        )
        BEGIN
            THROW 50003, 'Falha ao converter VariacaoPercentTxt para decimal(12,6).', 1;
        END;

        ;WITH origem_tratada AS
        (
            SELECT
                [Data],
                TRY_CONVERT(decimal(18, 6), [PeriodoAnteriorTxt]) AS [PeriodoAnterior],
                TRY_CONVERT(decimal(18, 6), [PeriodoAtualTxt]) AS [PeriodoAtual],
                TRY_CONVERT(decimal(12, 6), [VariacaoPercentTxt]) AS [VariacaoPercent]
            FROM #StagingIndiceOOH
        )
        MERGE {tabela_destino} AS destino
        USING
        (
            SELECT
                [Data],
                [PeriodoAnterior],
                [PeriodoAtual],
                [VariacaoPercent]
            FROM origem_tratada
            WHERE [PeriodoAtual] IS NOT NULL
        ) AS origem
            ON destino.[Data] = origem.[Data]
        WHEN MATCHED THEN
            UPDATE SET
                destino.[PeriodoAnterior] = origem.[PeriodoAnterior],
                destino.[PeriodoAtual] = origem.[PeriodoAtual],
                destino.[VariacaoPercent] = origem.[VariacaoPercent],
                destino.[DataAtualizacao] = SYSDATETIME()
        WHEN NOT MATCHED BY TARGET THEN
            INSERT
            (
                [Data],
                [PeriodoAnterior],
                [PeriodoAtual],
                [VariacaoPercent],
                [DataAtualizacao]
            )
            VALUES
            (
                origem.[Data],
                origem.[PeriodoAnterior],
                origem.[PeriodoAtual],
                origem.[VariacaoPercent],
                SYSDATETIME()
            );
        """)

        conexao_bruta.commit()

    except Exception:
        conexao_bruta.rollback()
        raise

    finally:
        conexao_bruta.close()


def executar_pipeline_indice_ooh() -> dict:
    """Eu executo o pipeline completo do índice OOH."""
    config = ConfiguracaoIndiceOOH(
        pesos_empresas=obter_pesos_empresas(),
        pesos_blocos=obter_pesos_blocos(),
    )

    pesos_empresas = normalizar_pesos(config.pesos_empresas)
    pesos_blocos = normalizar_pesos(config.pesos_blocos)

    engine_sql = criar_engine_sql(conn_id=config.conn_id_sql_server)

    try:
        print("=" * 100)
        print("INÍCIO DO PIPELINE - ÍNDICE OOH")
        print("=" * 100)

        precos_acoes, erros_acoes = baixar_acoes(
            lista_tickers=list(config.acoes_ooh_brasil),
            data_inicio=config.data_inicial,
            data_fim_inclusiva=config.data_final_inclusiva,
        )

        precos_acoes = precos_acoes.dropna(how="all")

        if precos_acoes.empty:
            raise ValueError("A base de ações ficou vazia.")

        if erros_acoes:
            print("Algumas ações falharam no download:")
            for erro in erros_acoes:
                print(erro)

        serie_indice_consumo = carregar_serie_indice_fato_sql(
            engine=engine_sql,
            tabela_sql=config.tabela_indice_consumo,
            nome_saida="INDICE_CONSUMO",
            data_inicio=config.data_inicial,
            data_fim_inclusiva=config.data_final_inclusiva,
        )

        serie_indice_imobiliario = carregar_serie_indice_fato_sql(
            engine=engine_sql,
            tabela_sql=config.tabela_indice_imobiliario,
            nome_saida="INDICE_IMOBILIARIO",
            data_inicio=config.data_inicial,
            data_fim_inclusiva=config.data_final_inclusiva,
        )

        serie_indice_industrial = carregar_serie_indice_fato_sql(
            engine=engine_sql,
            tabela_sql=config.tabela_indice_industrial,
            nome_saida="INDICE_INDUSTRIAL",
            data_inicio=config.data_inicial,
            data_fim_inclusiva=config.data_final_inclusiva,
        )

        serie_cdi = carregar_serie_cdi_sql(
            engine=engine_sql,
            tabela_cdi=config.tabela_cdi,
            data_inicio=config.data_inicial,
            data_fim_inclusiva=config.data_final_inclusiva,
        )

        retorno_carteira_empresas = calcular_retorno_carteira(
            precos=precos_acoes,
            pesos=pesos_empresas,
            nome_saida="retorno_carteira_empresas",
        )

        retorno_indice_consumo = serie_indice_consumo.pct_change(fill_method=None).fillna(0.0)
        retorno_indice_consumo.name = "retorno_indice_consumo"

        retorno_indice_imobiliario = serie_indice_imobiliario.pct_change(fill_method=None).fillna(0.0)
        retorno_indice_imobiliario.name = "retorno_indice_imobiliario"

        retorno_indice_industrial = serie_indice_industrial.pct_change(fill_method=None).fillna(0.0)
        retorno_indice_industrial.name = "retorno_indice_industrial"

        retorno_cdi = serie_cdi.fillna(0.0)
        retorno_cdi.name = "retorno_cdi"

        retornos_componentes = pd.concat(
            [
                retorno_carteira_empresas,
                retorno_indice_consumo,
                retorno_indice_imobiliario,
                retorno_indice_industrial,
                retorno_cdi,
            ],
            axis=1,
        ).sort_index()

        retornos_componentes.index = normalizar_indice_para_data_pura(retornos_componentes.index)
        retornos_componentes = retornos_componentes.loc[
            (retornos_componentes.index >= pd.to_datetime(config.data_inicial))
            & (retornos_componentes.index <= pd.to_datetime(config.data_final_inclusiva))
        ]
        retornos_componentes = retornos_componentes.fillna(0.0)

        contribuicao_empresas = pesos_blocos["carteira_empresas"] * retornos_componentes["retorno_carteira_empresas"]
        contribuicao_consumo = pesos_blocos["indice_consumo"] * retornos_componentes["retorno_indice_consumo"]
        contribuicao_imobiliario = pesos_blocos["indice_imobiliario"] * retornos_componentes["retorno_indice_imobiliario"]
        contribuicao_industrial = pesos_blocos["indice_industrial"] * retornos_componentes["retorno_indice_industrial"]
        contribuicao_cdi_invertida = -pesos_blocos["cdi"] * retornos_componentes["retorno_cdi"]

        retorno_indice_ooh = (
            contribuicao_empresas
            + contribuicao_consumo
            + contribuicao_imobiliario
            + contribuicao_industrial
            + contribuicao_cdi_invertida
        )
        retorno_indice_ooh.name = "retorno_indice_ooh"

        indice_ooh = reconstruir_curva_base_100(retorno_indice_ooh)
        indice_ooh.name = "INDICE_OOH_BRASIL"

        base_upsert_indice_ooh = montar_base_upsert_indice_ooh(
            indice_ooh=indice_ooh,
            retorno_indice_ooh=retorno_indice_ooh,
        )

        fazer_upsert_indice_ooh_sql(
            engine=engine_sql,
            df_upsert=base_upsert_indice_ooh,
            tabela_destino=config.tabela_indice_ooh_destino,
            casas_decimais_sql=config.casas_decimais_sql,
            tamanho_lote_sql=config.tamanho_lote_sql,
        )

        print(f"Upsert concluído com {len(base_upsert_indice_ooh)} linhas em {config.tabela_indice_ooh_destino}.")
        print(base_upsert_indice_ooh.tail(10))

        print("=" * 100)
        print("FIM DO PIPELINE - ÍNDICE OOH")
        print("=" * 100)

        return {
            "linhas_processadas": int(len(base_upsert_indice_ooh)),
            "data_minima": str(base_upsert_indice_ooh["Data"].min()),
            "data_maxima": str(base_upsert_indice_ooh["Data"].max()),
            "ticker_com_erro": erros_acoes,
            "tabela_destino": config.tabela_indice_ooh_destino,
        }

    finally:
        engine_sql.dispose()


@dag(
    dag_id="pipeline_indice_ooh_diario",
    description="Pipeline ETL do Índice OOH Diário",
    schedule="0 7 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 18, 7, 0, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["Euromidia", "Comercial", "ETL", "Indice OOH"],
    max_active_runs=1,
)
def pipeline_indice_ooh_diario():
    @task(
        task_id="executar_pipeline_indice_ooh",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=2),
    )
    def tarefa_executar_pipeline_indice_ooh() -> dict:
        """Eu executo o ETL do índice OOH e retorno um resumo."""
        return executar_pipeline_indice_ooh()

    tarefa_executar_pipeline_indice_ooh()


pipeline_indice_ooh_diario_dag = pipeline_indice_ooh_diario()