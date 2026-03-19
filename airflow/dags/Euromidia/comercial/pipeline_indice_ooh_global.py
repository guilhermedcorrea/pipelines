import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import pendulum
import yfinance as yf
from airflow.sdk import dag, task
from sqlalchemy import text

from hooks.BancodeDados.SqlServer import HookSqlServer


@dataclass(frozen=True)
class ConfiguracaoIndiceOOHGlobal:
    data_inicial: str = "2015-01-01"
    data_final_inclusiva: str = "2026-02-21"

    conn_id_sql_datamining: str = "mssql_datamining"
    conn_id_sql_integracao: str = "mssql_integracao"

    acoes_ooh_global: tuple[str, ...] = ("LAMR", "OUT", "CCO")

    pesos_ooh_global: dict[str, float] | None = None
    mapa_tabelas_cotacao: dict[str, str] | None = None

    tabela_indice_ooh_global: str = "[Integracao].[Silver].[FatoIndiceOOHGlobal]"


def obter_pesos_ooh_global() -> dict[str, float]:
    """Eu retorno os pesos base do índice OOH Global."""
    return {
        "LAMR": 1 / 3,
        "OUT": 1 / 3,
        "CCO": 1 / 3,
    }


def obter_mapa_tabelas_cotacao() -> dict[str, str]:
    """Eu retorno o mapa de ticker para tabela destino."""
    return {
        "LAMR": "[DataMining].[Silver].[FatoCotacaoDiariaLAMR]",
        "OUT": "[DataMining].[Silver].[FatoCotacaoDiariaOUT]",
        "CCO": "[DataMining].[Silver].[FatoCotacaoDiariaCCO]",
    }


def criar_engine_sql(conn_id: str):
    """Eu crio a engine SQL Server a partir do hook customizado do Airflow."""
    hook_sql_server = HookSqlServer(conn_id=conn_id)
    return hook_sql_server.obter_engine()


def normalizar_pesos(dicionario_pesos: dict[str, float]) -> dict[str, float]:
    """Função normalizar_pesos: eu normalizo os pesos para somarem 1."""
    soma = sum(dicionario_pesos.values())
    if soma == 0:
        raise ValueError("A soma dos pesos é zero.")
    return {chave: valor / soma for chave, valor in dicionario_pesos.items()}


def normalizar_indice_para_data_pura(indice) -> pd.DatetimeIndex:
    """Função normalizar_indice_para_data_pura: eu removo horário e timezone."""
    indice = pd.to_datetime(indice)

    if isinstance(indice, pd.DatetimeIndex) and indice.tz is not None:
        indice = indice.tz_localize(None)

    return indice.normalize()


def filtrar_intervalo_dataframe(
    df: pd.DataFrame,
    data_inicio: str | pd.Timestamp,
    data_fim_inclusiva: str | pd.Timestamp,
) -> pd.DataFrame:
    """Função filtrar_intervalo_dataframe: eu filtro o DataFrame pelo intervalo informado."""
    base = df.copy()
    base.index = normalizar_indice_para_data_pura(base.index)
    base = base[~base.index.duplicated(keep="last")]
    base = base.sort_index()

    data_inicio = pd.to_datetime(data_inicio).normalize()
    data_fim_inclusiva = pd.to_datetime(data_fim_inclusiva).normalize()

    return base.loc[
        (base.index >= data_inicio)
        & (base.index <= data_fim_inclusiva)
    ]


def filtrar_intervalo_serie(
    serie: pd.Series,
    data_inicio: str | pd.Timestamp,
    data_fim_inclusiva: str | pd.Timestamp,
) -> pd.Series:
    """Função filtrar_intervalo_serie: eu filtro a série pelo intervalo informado."""
    base = serie.copy()
    base.index = normalizar_indice_para_data_pura(base.index)
    base = base[~base.index.duplicated(keep="last")]
    base = base.sort_index()

    data_inicio = pd.to_datetime(data_inicio).normalize()
    data_fim_inclusiva = pd.to_datetime(data_fim_inclusiva).normalize()

    return base.loc[
        (base.index >= data_inicio)
        & (base.index <= data_fim_inclusiva)
    ]


def achatar_colunas_yfinance(dados: pd.DataFrame) -> pd.DataFrame:
    """Função achatar_colunas_yfinance: eu removo MultiIndex do retorno do yfinance."""
    base = dados.copy()

    if isinstance(base.columns, pd.MultiIndex):
        if len(base.columns.levels) >= 2:
            base.columns = base.columns.get_level_values(0)
        else:
            base.columns = [col[0] if isinstance(col, tuple) else col for col in base.columns]

    base.columns = [str(col).strip() for col in base.columns]
    return base


def converter_para_float_ou_none(valor):
    """Função converter_para_float_ou_none: eu transformo NaN em None e número em float."""
    if pd.isna(valor):
        return None
    return float(valor)


def converter_para_date_ou_none(valor):
    """Função converter_para_date_ou_none: eu transformo Timestamp em date."""
    if pd.isna(valor):
        return None
    return pd.to_datetime(valor).date()


def baixar_cotacao_diaria_ticker(
    ticker: str,
    data_inicio: str,
    data_fim_inclusiva: str,
) -> pd.DataFrame:
    """Função baixar_cotacao_diaria_ticker: eu baixo OHLCV diário de um ticker."""
    data_fim_exclusiva = (
        pd.to_datetime(data_fim_inclusiva) + pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")

    dados = yf.download(
        ticker,
        start=data_inicio,
        end=data_fim_exclusiva,
        progress=False,
        threads=False,
        auto_adjust=False,
        actions=False,
    )

    if dados is None or dados.empty:
        raise ValueError(f"{ticker} veio vazio no yfinance.")

    dados = achatar_colunas_yfinance(dados)
    dados = filtrar_intervalo_dataframe(dados, data_inicio, data_fim_inclusiva)

    colunas_necessarias = ["Open", "High", "Low", "Close", "Volume"]
    colunas_faltantes = [col for col in colunas_necessarias if col not in dados.columns]

    if colunas_faltantes:
        raise ValueError(
            f"{ticker} não retornou todas as colunas necessárias. Faltando: {colunas_faltantes}"
        )

    dados = dados[colunas_necessarias].copy()
    dados = dados.apply(pd.to_numeric, errors="coerce")
    dados = dados.dropna(how="all")

    if dados.empty:
        raise ValueError(f"{ticker} ficou vazio após limpeza.")

    dados.index = normalizar_indice_para_data_pura(dados.index)
    dados.index.name = "DataCotacao"

    return dados


def baixar_cotacoes_todos_tickers(
    lista_tickers: list[str],
    data_inicio: str,
    data_fim_inclusiva: str,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Função baixar_cotacoes_todos_tickers: eu baixo um ticker por vez."""
    resultado: dict[str, pd.DataFrame] = {}
    erros: list[str] = []

    for ticker in lista_tickers:
        try:
            resultado[ticker] = baixar_cotacao_diaria_ticker(
                ticker=ticker,
                data_inicio=data_inicio,
                data_fim_inclusiva=data_fim_inclusiva,
            )
            print(f"{ticker}: {len(resultado[ticker])} linhas baixadas.")
        except Exception as erro:
            erros.append(f"{ticker}: {erro}")

        time.sleep(0.5)

    if not resultado:
        raise ValueError("Nenhum ticker retornou dados.\n" + "\n".join(erros))

    if erros:
        print("Ocorreram erros em alguns tickers:")
        for erro in erros:
            print("-", erro)

    return resultado, erros


def carregar_serie_dolar_brl(
    engine,
    data_inicio: str,
    data_fim_inclusiva: str,
    usar_coluna: str = "CotacaoVenda",
) -> pd.Series:
    """Função carregar_serie_dolar_brl: eu busco a última cotação do dólar de cada dia."""
    data_inicio_busca = (
        pd.to_datetime(data_inicio) - pd.Timedelta(days=10)
    ).strftime("%Y-%m-%d")

    sql = text(
        f"""
        ;WITH cotacao_mais_recente AS
        (
            SELECT
                CAST([DataCotacao] AS date) AS DataCotacao,
                CAST([{usar_coluna}] AS float) AS CotacaoDolar,
                ROW_NUMBER() OVER
                (
                    PARTITION BY CAST([DataCotacao] AS date)
                    ORDER BY [DataHoraCotacao] DESC, [IDDimCotacaoDolar] DESC
                ) AS rn
            FROM [Integracao].[Silver].[DimCotacaoDolar]
            WHERE [DataCotacao] >= :data_inicio_busca
              AND [DataCotacao] <= :data_fim
        )
        SELECT
            [DataCotacao],
            [CotacaoDolar]
        FROM cotacao_mais_recente
        WHERE rn = 1
        ORDER BY [DataCotacao];
        """
    )

    with engine.begin() as conexao:
        dolar_df = pd.read_sql_query(
            sql=sql,
            con=conexao,
            params={
                "data_inicio_busca": data_inicio_busca,
                "data_fim": data_fim_inclusiva,
            },
        )

    if dolar_df.empty:
        raise ValueError("A consulta da cotação do dólar retornou vazia.")

    dolar_df["DataCotacao"] = pd.to_datetime(dolar_df["DataCotacao"]).dt.normalize()
    dolar_df["CotacaoDolar"] = pd.to_numeric(dolar_df["CotacaoDolar"], errors="coerce")

    serie_dolar = dolar_df.set_index("DataCotacao")["CotacaoDolar"].copy()
    serie_dolar.name = "DOLAR_BRL"
    serie_dolar = filtrar_intervalo_serie(
        serie=serie_dolar,
        data_inicio=data_inicio_busca,
        data_fim_inclusiva=data_fim_inclusiva,
    )
    serie_dolar = serie_dolar.dropna()

    if serie_dolar.empty:
        raise ValueError("A série do dólar ficou vazia após limpeza.")

    return serie_dolar


def determinar_data_inicio_efetiva(
    cotacoes_usd_por_ticker: dict[str, pd.DataFrame],
    serie_dolar_brl: pd.Series,
    data_inicio_solicitada: str,
) -> pd.Timestamp:
    """Função determinar_data_inicio_efetiva: eu encontro a primeira data em que todas as bases coexistem."""
    datas_minimas = [pd.to_datetime(data_inicio_solicitada).normalize()]

    for ticker, df in cotacoes_usd_por_ticker.items():
        if df.empty:
            raise ValueError(f"{ticker} veio vazio ao determinar a data inicial efetiva.")
        datas_minimas.append(pd.to_datetime(df.index.min()).normalize())

    if serie_dolar_brl.empty:
        raise ValueError("A série do dólar veio vazia ao determinar a data inicial efetiva.")

    datas_minimas.append(pd.to_datetime(serie_dolar_brl.index.min()).normalize())

    return max(datas_minimas)


def alinhar_dolar_para_datas(
    datas_base: pd.DatetimeIndex,
    serie_dolar_brl: pd.Series,
) -> pd.Series:
    """Função alinhar_dolar_para_datas: eu alinho o dólar às datas das ações."""
    dolar = serie_dolar_brl.copy()
    dolar.index = normalizar_indice_para_data_pura(dolar.index)
    dolar = dolar.sort_index()

    serie_alinhada = dolar.reindex(datas_base, method="ffill")

    if serie_alinhada.isna().any():
        primeiras_datas_sem_dolar = serie_alinhada[serie_alinhada.isna()].index[:5].tolist()
        raise ValueError(
            f"Mesmo após o ajuste da data inicial, ainda faltou dólar nestas datas: {primeiras_datas_sem_dolar}"
        )

    return serie_alinhada


def montar_fato_cotacao_ticker(
    ticker: str,
    df_usd: pd.DataFrame,
    serie_dolar_brl: pd.Series,
    data_insercao: datetime,
) -> pd.DataFrame:
    """Função montar_fato_cotacao_ticker: eu monto o layout final da tabela fato de cotação."""
    base_usd = df_usd.copy()
    base_usd.index = normalizar_indice_para_data_pura(base_usd.index)

    dolar_alinhado = alinhar_dolar_para_datas(
        datas_base=base_usd.index,
        serie_dolar_brl=serie_dolar_brl,
    )

    base_brl = pd.DataFrame(index=base_usd.index)
    base_brl["Open"] = base_usd["Open"] * dolar_alinhado
    base_brl["High"] = base_usd["High"] * dolar_alinhado
    base_brl["Low"] = base_usd["Low"] * dolar_alinhado
    base_brl["Close"] = base_usd["Close"] * dolar_alinhado

    var_usd = base_usd["Close"].pct_change(fill_method=None) * 100.0
    var_brl = base_brl["Close"].pct_change(fill_method=None) * 100.0

    fato = pd.DataFrame(index=base_usd.index)
    fato["DataCotacao"] = base_usd.index
    fato["UltimoUSD"] = pd.to_numeric(base_usd["Close"], errors="coerce")
    fato["AberturaUSD"] = pd.to_numeric(base_usd["Open"], errors="coerce")
    fato["MaximaUSD"] = pd.to_numeric(base_usd["High"], errors="coerce")
    fato["MinimaUSD"] = pd.to_numeric(base_usd["Low"], errors="coerce")
    fato["Vol"] = pd.to_numeric(base_usd["Volume"], errors="coerce")
    fato["VarUSD"] = pd.to_numeric(var_usd, errors="coerce")

    fato["UltimoBRL"] = pd.to_numeric(base_brl["Close"], errors="coerce")
    fato["AberturaBRL"] = pd.to_numeric(base_brl["Open"], errors="coerce")
    fato["MaximaBRL"] = pd.to_numeric(base_brl["High"], errors="coerce")
    fato["MinimaBRL"] = pd.to_numeric(base_brl["Low"], errors="coerce")
    fato["VarBRL"] = pd.to_numeric(var_brl, errors="coerce")

    fato["DataInsercao"] = data_insercao
    fato = fato.reset_index(drop=True)

    return fato


def calcular_retorno_diario_carteira(
    precos_fechamento_brl: pd.DataFrame,
    pesos: dict[str, float],
) -> pd.Series:
    """Função calcular_retorno_diario_carteira: eu calculo o retorno diário ponderado."""
    retornos = precos_fechamento_brl.pct_change(fill_method=None)
    pesos_series = pd.Series(pesos, dtype=float).reindex(precos_fechamento_brl.columns).fillna(0.0)

    def calcular_retorno_linha(linha: pd.Series):
        pesos_validos = pesos_series.where(~linha.isna(), other=0.0)
        soma_pesos_validos = pesos_validos.sum()

        if soma_pesos_validos == 0:
            return None

        return (linha.fillna(0.0) * pesos_validos).sum() / soma_pesos_validos

    retorno_carteira = retornos.apply(calcular_retorno_linha, axis=1)
    retorno_carteira.name = "RetornoDiario"

    return retorno_carteira


def reconstruir_curva_base_100(serie_retorno: pd.Series) -> pd.Series:
    """Função reconstruir_curva_base_100: eu reconstruo a curva base 100 do índice."""
    retorno_para_curva = pd.to_numeric(serie_retorno, errors="coerce").fillna(0.0)
    curva = (1.0 + retorno_para_curva).cumprod() * 100.0
    curva.name = "INDICE_OOH_GLOBAL"
    return curva


def montar_fato_indice_ooh_global(
    fatos_tickers: dict[str, pd.DataFrame],
    pesos: dict[str, float],
) -> pd.DataFrame:
    """Função montar_fato_indice_ooh_global: eu monto a tabela fato do índice OOH Global."""
    precos_fechamento = {}

    for ticker, df_fato in fatos_tickers.items():
        serie = df_fato[["DataCotacao", "UltimoBRL"]].copy()
        serie["DataCotacao"] = pd.to_datetime(serie["DataCotacao"]).dt.normalize()
        serie = serie.set_index("DataCotacao")["UltimoBRL"].sort_index()
        precos_fechamento[ticker] = pd.to_numeric(serie, errors="coerce")

    df_fechamentos_brl = pd.concat(precos_fechamento, axis=1).sort_index()
    df_fechamentos_brl.index = normalizar_indice_para_data_pura(df_fechamentos_brl.index)

    retorno_diario = calcular_retorno_diario_carteira(
        precos_fechamento_brl=df_fechamentos_brl,
        pesos=pesos,
    )

    indice_base_100 = reconstruir_curva_base_100(retorno_diario)
    periodo_anterior = indice_base_100.shift(1)
    variacao_percent = retorno_diario * 100.0

    fato_indice = pd.DataFrame(
        {
            "Data": indice_base_100.index,
            "PeriodoAnterior": periodo_anterior.values,
            "PeriodoAtual": indice_base_100.values,
            "VariacaoPercent": variacao_percent.values,
        }
    )

    fato_indice = fato_indice.reset_index(drop=True)
    return fato_indice


def upsert_fato_cotacao_ticker(
    engine,
    tabela_destino: str,
    df_fato: pd.DataFrame,
):
    """Função upsert_fato_cotacao_ticker: eu faço upsert por DataCotacao."""
    sql_upsert = text(
        f"""
        UPDATE destino
           SET destino.[UltimoUSD] = :UltimoUSD,
               destino.[AberturaUSD] = :AberturaUSD,
               destino.[MaximaUSD] = :MaximaUSD,
               destino.[MinimaUSD] = :MinimaUSD,
               destino.[Vol] = :Vol,
               destino.[VarUSD] = :VarUSD,
               destino.[UltimoBRL] = :UltimoBRL,
               destino.[AberturaBRL] = :AberturaBRL,
               destino.[MaximaBRL] = :MaximaBRL,
               destino.[MinimaBRL] = :MinimaBRL,
               destino.[VarBRL] = :VarBRL,
               destino.[DataInsercao] = :DataInsercao
        FROM {tabela_destino} AS destino
        WHERE destino.[DataCotacao] = :DataCotacao;

        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO {tabela_destino}
            (
                [DataCotacao],
                [UltimoUSD],
                [AberturaUSD],
                [MaximaUSD],
                [MinimaUSD],
                [Vol],
                [VarUSD],
                [UltimoBRL],
                [AberturaBRL],
                [MaximaBRL],
                [MinimaBRL],
                [VarBRL],
                [DataInsercao]
            )
            VALUES
            (
                :DataCotacao,
                :UltimoUSD,
                :AberturaUSD,
                :MaximaUSD,
                :MinimaUSD,
                :Vol,
                :VarUSD,
                :UltimoBRL,
                :AberturaBRL,
                :MaximaBRL,
                :MinimaBRL,
                :VarBRL,
                :DataInsercao
            );
        END;
        """
    )

    registros = []
    for _, linha in df_fato.iterrows():
        registros.append(
            {
                "DataCotacao": converter_para_date_ou_none(linha["DataCotacao"]),
                "UltimoUSD": converter_para_float_ou_none(linha["UltimoUSD"]),
                "AberturaUSD": converter_para_float_ou_none(linha["AberturaUSD"]),
                "MaximaUSD": converter_para_float_ou_none(linha["MaximaUSD"]),
                "MinimaUSD": converter_para_float_ou_none(linha["MinimaUSD"]),
                "Vol": converter_para_float_ou_none(linha["Vol"]),
                "VarUSD": converter_para_float_ou_none(linha["VarUSD"]),
                "UltimoBRL": converter_para_float_ou_none(linha["UltimoBRL"]),
                "AberturaBRL": converter_para_float_ou_none(linha["AberturaBRL"]),
                "MaximaBRL": converter_para_float_ou_none(linha["MaximaBRL"]),
                "MinimaBRL": converter_para_float_ou_none(linha["MinimaBRL"]),
                "VarBRL": converter_para_float_ou_none(linha["VarBRL"]),
                "DataInsercao": linha["DataInsercao"].to_pydatetime()
                if hasattr(linha["DataInsercao"], "to_pydatetime")
                else linha["DataInsercao"],
            }
        )

    with engine.begin() as conexao:
        for parametros in registros:
            conexao.execute(sql_upsert, parametros)


def upsert_fato_indice_ooh_global(
    engine,
    tabela_destino: str,
    df_fato_indice: pd.DataFrame,
):
    """Função upsert_fato_indice_ooh_global: eu faço upsert por Data."""
    sql_upsert = text(
        f"""
        UPDATE destino
           SET destino.[PeriodoAnterior] = :PeriodoAnterior,
               destino.[PeriodoAtual] = :PeriodoAtual,
               destino.[VariacaoPercent] = :VariacaoPercent
        FROM {tabela_destino} AS destino
        WHERE destino.[Data] = :Data;

        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO {tabela_destino}
            (
                [Data],
                [PeriodoAnterior],
                [PeriodoAtual],
                [VariacaoPercent]
            )
            VALUES
            (
                :Data,
                :PeriodoAnterior,
                :PeriodoAtual,
                :VariacaoPercent
            );
        END;
        """
    )

    registros = []
    for _, linha in df_fato_indice.iterrows():
        registros.append(
            {
                "Data": converter_para_date_ou_none(linha["Data"]),
                "PeriodoAnterior": converter_para_float_ou_none(linha["PeriodoAnterior"]),
                "PeriodoAtual": converter_para_float_ou_none(linha["PeriodoAtual"]),
                "VariacaoPercent": converter_para_float_ou_none(linha["VariacaoPercent"]),
            }
        )

    with engine.begin() as conexao:
        for parametros in registros:
            conexao.execute(sql_upsert, parametros)


def executar_carga_ooh_global() -> dict:
    """Função executar_carga_ooh_global: eu orquestro download, corte da data comum, cálculo e upserts."""
    config = ConfiguracaoIndiceOOHGlobal(
        pesos_ooh_global=obter_pesos_ooh_global(),
        mapa_tabelas_cotacao=obter_mapa_tabelas_cotacao(),
    )

    pesos_normalizados = normalizar_pesos(config.pesos_ooh_global)
    data_insercao = datetime.now().replace(microsecond=0)

    engine_datamining = criar_engine_sql(conn_id=config.conn_id_sql_datamining)
    engine_integracao = criar_engine_sql(conn_id=config.conn_id_sql_integracao)

    try:
        print("=" * 100)
        print("INÍCIO DO PIPELINE - ÍNDICE OOH GLOBAL")
        print("=" * 100)

        print("1) Baixando cotações dos tickers...")
        cotacoes_usd_por_ticker, erros_download = baixar_cotacoes_todos_tickers(
            lista_tickers=list(config.acoes_ooh_global),
            data_inicio=config.data_inicial,
            data_fim_inclusiva=config.data_final_inclusiva,
        )

        print("2) Carregando série do dólar...")
        serie_dolar_brl = carregar_serie_dolar_brl(
            engine=engine_integracao,
            data_inicio=config.data_inicial,
            data_fim_inclusiva=config.data_final_inclusiva,
            usar_coluna="CotacaoVenda",
        )

        print("3) Determinando a primeira data comum válida...")
        data_inicio_efetiva = determinar_data_inicio_efetiva(
            cotacoes_usd_por_ticker=cotacoes_usd_por_ticker,
            serie_dolar_brl=serie_dolar_brl,
            data_inicio_solicitada=config.data_inicial,
        )

        if data_inicio_efetiva > pd.to_datetime(config.data_inicial).normalize():
            print(
                f"Atenção: a carga não pode começar em {config.data_inicial}. "
                f"A primeira data comum válida entre ações e dólar é {data_inicio_efetiva.date()}."
            )

        cotacoes_usd_por_ticker_ajustadas = {}
        for ticker, df in cotacoes_usd_por_ticker.items():
            df_ajustado = filtrar_intervalo_dataframe(
                df=df,
                data_inicio=data_inicio_efetiva,
                data_fim_inclusiva=config.data_final_inclusiva,
            )

            if df_ajustado.empty:
                raise ValueError(f"{ticker} ficou vazio após aplicar a data inicial efetiva.")

            cotacoes_usd_por_ticker_ajustadas[ticker] = df_ajustado

        serie_dolar_brl_ajustada = filtrar_intervalo_serie(
            serie=serie_dolar_brl,
            data_inicio=data_inicio_efetiva,
            data_fim_inclusiva=config.data_final_inclusiva,
        )

        if serie_dolar_brl_ajustada.empty:
            raise ValueError("A série do dólar ficou vazia após aplicar a data inicial efetiva.")

        print("4) Montando fatos de cotação por ticker...")
        fatos_cotacoes = {}

        for ticker, df_usd in cotacoes_usd_por_ticker_ajustadas.items():
            fatos_cotacoes[ticker] = montar_fato_cotacao_ticker(
                ticker=ticker,
                df_usd=df_usd,
                serie_dolar_brl=serie_dolar_brl_ajustada,
                data_insercao=data_insercao,
            )

        print("5) Montando índice OOH Global...")
        fato_indice_ooh_global = montar_fato_indice_ooh_global(
            fatos_tickers=fatos_cotacoes,
            pesos=pesos_normalizados,
        )

        print("6) Fazendo upsert das cotações no DataMining...")
        for ticker, df_fato in fatos_cotacoes.items():
            tabela_destino = config.mapa_tabelas_cotacao[ticker]
            upsert_fato_cotacao_ticker(
                engine=engine_datamining,
                tabela_destino=tabela_destino,
                df_fato=df_fato,
            )
            print(f"{ticker}: {len(df_fato)} linhas processadas em {tabela_destino}")

        print("7) Fazendo upsert do índice no Integracao...")
        upsert_fato_indice_ooh_global(
            engine=engine_integracao,
            tabela_destino=config.tabela_indice_ooh_global,
            df_fato_indice=fato_indice_ooh_global,
        )
        print(
            f"OOH Global: {len(fato_indice_ooh_global)} linhas processadas em {config.tabela_indice_ooh_global}"
        )

        if erros_download:
            print("Alguns tickers tiveram erro no download:")
            for erro in erros_download:
                print("-", erro)

        print("Processo concluído com sucesso.")
        print("=" * 100)
        print("FIM DO PIPELINE - ÍNDICE OOH GLOBAL")
        print("=" * 100)

        return {
            "linhas_indice_global": int(len(fato_indice_ooh_global)),
            "data_minima": str(fato_indice_ooh_global["Data"].min()),
            "data_maxima": str(fato_indice_ooh_global["Data"].max()),
            "erros_download": erros_download,
            "tabela_destino_indice": config.tabela_indice_ooh_global,
        }

    finally:
        engine_datamining.dispose()
        engine_integracao.dispose()


@dag(
    dag_id="pipeline_indice_ooh_global_diario",
    description="Pipeline ETL do Índice OOH Global Diário",
    schedule="30 7 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 18, 7, 30, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["Euromidia", "Comercial", "ETL", "Indice OOH Global"],
    max_active_runs=1,
)
def pipeline_indice_ooh_global_diario():
    @task(
        task_id="executar_carga_ooh_global",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=2),
    )
    def tarefa_executar_carga_ooh_global() -> dict:
        """Eu executo o pipeline do índice OOH Global."""
        return executar_carga_ooh_global()

    tarefa_executar_carga_ooh_global()


pipeline_indice_ooh_global_diario_dag = pipeline_indice_ooh_global_diario()