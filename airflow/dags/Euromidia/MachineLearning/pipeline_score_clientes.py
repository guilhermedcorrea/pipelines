import json
import math
import statistics
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd
import pendulum
import polars as pl
from airflow.sdk import dag, task
from catboost import CatBoostClassifier, Pool
from sqlalchemy import create_engine
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


@dataclass(frozen=True)
class Configuracao:
    """Configuração central do pipeline."""

    servidor_sql: str = r"192.168.40.177"
    banco_sql: str = "DataMining"
    usuario_sql: str = "sa"
    senha_sql: str = "Mudar@123ab"

    caminho_query_sql: Path = Path(
        "/opt/airflow/queries/Euromidia/MachineLearning/algoritmo_score_cliente.sql"
    )

    pasta_saida_dados: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning"
    )
    pasta_saida_metricas: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Metricas"
    )

    caminho_saida_score_historico_csv: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning/score_cliente_historico.csv"
    )
    caminho_saida_score_atual_csv: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning/score_cliente_atual.csv"
    )
    caminho_saida_metricas_json: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Metricas/score_cliente_metricas.json"
    )
    caminho_saida_importancias_csv: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning/score_cliente_importancias.csv"
    )
    caminho_saida_resumo_faixas_csv: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning/score_cliente_faixas.csv"
    )
    caminho_saida_tabela_final_csv: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning/score_cliente_tabela_final.csv"
    )
    caminho_saida_walk_forward_folds_csv: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning/score_cliente_walk_forward_folds.csv"
    )
    caminho_saida_walk_forward_predicoes_csv: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning/score_cliente_walk_forward_predicoes.csv"
    )

    nome_coluna_alvo: str = "ContratouProx90d"
    nome_coluna_mes_ref: str = "MesRef"
    nome_coluna_snapshot: str = "DataSnapshot"

    quantidade_meses_validacao: int = 6
    quantidade_meses_teste: int = 6
    quantidade_minima_meses_treino_walk_forward: int = 24
    quantidade_maxima_folds_walk_forward: int | None = None

    usar_pesos_balanceamento: bool = True

    semente_aleatoria: int = 42

    iteracoes_catboost: int = 1200
    learning_rate_catboost: float = 0.03
    profundidade_catboost: int = 6
    l2_leaf_reg_catboost: float = 8.0
    random_strength_catboost: float = 1.5
    bagging_temperature_catboost: float = 0.5

    early_stopping_rounds_catboost: int = 150
    iteracoes_minimas_modelo_final: int = 100

    probabilidade_minima_score: float = 0.001
    probabilidade_maxima_score: float = 0.999

    salvar_score_historico_completo: bool = True
    quantidade_linhas_exibir_tabela_final: int = 50


def criar_engine_sql(config: Configuracao):
    """Cria a conexão com SQL Server via SQLAlchemy."""

    params_datamining = urllib.parse.quote_plus(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={config.servidor_sql};"
        f"DATABASE={config.banco_sql};"
        f"UID={config.usuario_sql};"
        f"PWD={config.senha_sql};"
        "Connection Timeout=30;"
        "TrustServerCertificate=yes;"
    )

    engine_sql = create_engine(
        f"mssql+pyodbc:///?odbc_connect={params_datamining}",
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )
    return engine_sql


def ler_query_sql(caminho_query_sql: Path) -> str:
    """Lê a query SQL do arquivo."""

    if not caminho_query_sql.exists():
        raise FileNotFoundError(f"Arquivo de query não encontrado: {caminho_query_sql}")

    texto_query = caminho_query_sql.read_text(encoding="utf-8")
    if not texto_query.strip():
        raise ValueError(f"O arquivo de query está vazio: {caminho_query_sql}")

    return texto_query


def normalizar_valor_sql(valor):
    """Normaliza valores vindos do SQL Server para tipos Python mais previsíveis."""

    if valor is None:
        return None

    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, UUID):
        return str(valor)

    if isinstance(valor, bytes):
        try:
            return valor.decode("utf-8")
        except Exception:
            return str(valor)

    return valor


def coluna_tem_tipos_mistos_criticos(serie: pd.Series) -> bool:
    """Verifica se a coluna tem mistura problemática de tipos."""

    valores_validos = [
        valor for valor in serie.tolist()
        if valor is not None and not pd.isna(valor)
    ]

    if not valores_validos:
        return False

    tipos = {type(valor) for valor in valores_validos}

    tipos_numericos = {
        int, float, bool,
        np.int8, np.int16, np.int32, np.int64,
        np.uint8, np.uint16, np.uint32, np.uint64,
        np.float16, np.float32, np.float64,
        np.bool_,
    }

    tipos_data = {
        date, datetime, pd.Timestamp,
    }

    if tipos.issubset(tipos_numericos):
        return False

    if tipos.issubset(tipos_data):
        return False

    if len(tipos) == 1:
        return False

    return True


def normalizar_dataframe_pandas_para_polars(df_pandas: pd.DataFrame) -> pd.DataFrame:
    """Padroniza tipos do pandas para permitir conversão robusta ao Polars sem pyarrow."""

    for coluna in df_pandas.columns:
        serie = df_pandas[coluna].map(normalizar_valor_sql)

        if coluna_tem_tipos_mistos_criticos(serie):
            df_pandas[coluna] = serie.astype("string")
            continue

        serie_numerica = pd.to_numeric(serie, errors="coerce")
        quantidade_na_original = serie.isna().sum()
        quantidade_na_convertida = serie_numerica.isna().sum()

        if quantidade_na_convertida <= quantidade_na_original and serie_numerica.notna().sum() > 0:
            df_pandas[coluna] = serie_numerica
            continue

        serie_data = pd.to_datetime(serie, errors="coerce")
        quantidade_na_data = serie_data.isna().sum()

        if quantidade_na_data <= quantidade_na_original and serie_data.notna().sum() > 0:
            df_pandas[coluna] = serie_data
            continue

        if pd.api.types.is_bool_dtype(serie):
            df_pandas[coluna] = serie.astype("boolean")
            continue

        df_pandas[coluna] = serie.astype("string")

    return df_pandas


def converter_pandas_para_polars_sem_pyarrow(df_pandas: pd.DataFrame) -> pl.DataFrame:
    """Converte pandas para Polars sem usar pl.from_pandas()."""

    dados_por_coluna = {}

    for coluna in df_pandas.columns:
        serie = df_pandas[coluna]

        if pd.api.types.is_datetime64_any_dtype(serie):
            dados_por_coluna[coluna] = [
                valor.to_pydatetime() if pd.notna(valor) else None
                for valor in serie
            ]
            continue

        if pd.api.types.is_numeric_dtype(serie):
            dados_por_coluna[coluna] = [
                None if pd.isna(valor) else valor
                for valor in serie.tolist()
            ]
            continue

        if pd.api.types.is_bool_dtype(serie) or str(serie.dtype) == "boolean":
            dados_por_coluna[coluna] = [
                None if pd.isna(valor) else bool(valor)
                for valor in serie.tolist()
            ]
            continue

        dados_por_coluna[coluna] = [
            None if pd.isna(valor) else str(valor)
            for valor in serie.tolist()
        ]

    return pl.DataFrame(dados_por_coluna)


def polars_para_pandas_sem_pyarrow(df: pl.DataFrame) -> pd.DataFrame:
    """Converte Polars para pandas sem usar to_pandas()."""

    return pd.DataFrame(df.to_dicts())


def carregar_dados_sql_em_polars(engine_sql, query_sql: str) -> pl.DataFrame:
    """
    Executa batch SQL Server com múltiplas instruções,
    captura o último result set com linhas,
    normaliza tipos e converte para Polars sem depender de pyarrow.
    """

    conexao_bruta = engine_sql.raw_connection()
    cursor = None

    try:
        cursor = conexao_bruta.cursor()
        cursor.execute(query_sql)

        colunas = None
        linhas = None

        while True:
            if cursor.description is not None:
                colunas = [coluna[0] for coluna in cursor.description]
                linhas = cursor.fetchall()

            tem_proximo = cursor.nextset()
            if not tem_proximo:
                break

        if colunas is None or linhas is None:
            raise ValueError(
                "A query foi executada, mas nenhum result set com linhas foi retornado. "
                "Verifique se a query termina com um SELECT final."
            )

        if len(linhas) == 0:
            raise ValueError("A query retornou um result set final, mas sem linhas.")

        df_pandas = pd.DataFrame.from_records(linhas, columns=colunas)
        df_pandas = normalizar_dataframe_pandas_para_polars(df_pandas)

        if df_pandas.empty:
            raise ValueError("A query retornou dados, mas o DataFrame pandas ficou vazio após normalização.")

        df_polars = converter_pandas_para_polars_sem_pyarrow(df_pandas)

        if df_polars.height == 0:
            raise ValueError("A conversão para Polars gerou DataFrame vazio.")

        return df_polars

    finally:
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            pass

        try:
            conexao_bruta.close()
        except Exception:
            pass


def converter_datas(df: pl.DataFrame, config: Configuracao) -> pl.DataFrame:
    """Converte colunas temporais para tipo date/datetime quando possível."""

    colunas_temporais = [
        config.nome_coluna_mes_ref,
        config.nome_coluna_snapshot,
        "UltimaDataCompra",
    ]

    expressoes = []
    for coluna in colunas_temporais:
        if coluna in df.columns:
            tipo_atual = df.schema[coluna]

            if tipo_atual == pl.Utf8:
                expressoes.append(
                    pl.col(coluna).str.strptime(pl.Date, strict=False).alias(coluna)
                )
            elif tipo_atual == pl.Datetime:
                expressoes.append(pl.col(coluna).dt.date().alias(coluna))
            elif tipo_atual == pl.Date:
                expressoes.append(pl.col(coluna))
            else:
                expressoes.append(pl.col(coluna).cast(pl.Date, strict=False).alias(coluna))

    if expressoes:
        df = df.with_columns(expressoes)

    return df


def validar_colunas_essenciais(df: pl.DataFrame, config: Configuracao) -> None:
    """Valida a existência das colunas mínimas do pipeline."""

    colunas_obrigatorias = [
        config.nome_coluna_alvo,
        config.nome_coluna_mes_ref,
    ]

    faltantes = [coluna for coluna in colunas_obrigatorias if coluna not in df.columns]
    if faltantes:
        raise ValueError(
            f"As seguintes colunas obrigatórias não existem no dataset: {faltantes}"
        )


def limpar_e_padronizar_dataset(df: pl.DataFrame, config: Configuracao) -> pl.DataFrame:
    """Padroniza tipos e remove linhas inviáveis para treino."""

    validar_colunas_essenciais(df, config)
    df = converter_datas(df, config)

    if config.nome_coluna_alvo in df.columns:
        df = df.filter(pl.col(config.nome_coluna_alvo).is_not_null())
        df = df.with_columns(
            pl.col(config.nome_coluna_alvo).cast(pl.Int8, strict=False).alias(config.nome_coluna_alvo)
        )

    if config.nome_coluna_mes_ref in df.columns:
        df = df.filter(pl.col(config.nome_coluna_mes_ref).is_not_null())

    return df


def adicionar_features_temporais(df: pl.DataFrame, config: Configuracao) -> pl.DataFrame:
    """Cria features derivadas do calendário do snapshot."""

    expressoes = []

    if config.nome_coluna_mes_ref in df.columns:
        expressoes.extend(
            [
                pl.col(config.nome_coluna_mes_ref).dt.year().alias("AnoMesRef"),
                pl.col(config.nome_coluna_mes_ref).dt.month().alias("MesDoAno"),
                (((pl.col(config.nome_coluna_mes_ref).dt.month() - 1) // 3) + 1).alias("Trimestre"),
            ]
        )

    if "UltimaDataCompra" in df.columns and config.nome_coluna_snapshot in df.columns:
        expressoes.append(
            (pl.col(config.nome_coluna_snapshot) - pl.col("UltimaDataCompra"))
            .dt.total_days()
            .alias("DiasDesdeUltimaCompraRecalculado")
        )

    if expressoes:
        df = df.with_columns(expressoes)

    if "DiasDesdeUltimaCompra" not in df.columns and "DiasDesdeUltimaCompraRecalculado" in df.columns:
        df = df.with_columns(
            pl.col("DiasDesdeUltimaCompraRecalculado").alias("DiasDesdeUltimaCompra")
        )

    return df


def obter_colunas_excluir_por_padrao(df: pl.DataFrame, config: Configuracao) -> list[str]:
    """Define colunas que não devem entrar no treino inicial."""

    colunas_excluir = {
        config.nome_coluna_alvo,
        "CNPJ_LIMPO",
        "IDEmpresa",
        "RazaoSocial",
        "DescricaoCNAE",
        "DescricaoClasseValor",
        "UltimaDataCompra",
        "DataSnapshot",
        "MesRef",
    }

    colunas_potencialmente_perigosas = {
        "ScoreRetornoCluster",
        "ClusterGrupoCliente",
        "ScoreRetornoTecnico",
        "ReceitaTotal",
        "PercReceitaAcumulada",
        "ClasseValor",
        "ClassePotencial",
    }

    colunas_excluir.update(colunas_potencialmente_perigosas)

    return [coluna for coluna in df.columns if coluna in colunas_excluir]


def obter_meses_ordenados(df: pl.DataFrame, config: Configuracao) -> list[Any]:
    """Extrai meses únicos ordenados para split temporal."""

    meses = (
        df.select(pl.col(config.nome_coluna_mes_ref))
        .unique()
        .sort(config.nome_coluna_mes_ref)
        .get_column(config.nome_coluna_mes_ref)
        .to_list()
    )

    minimo_necessario = (
        config.quantidade_meses_teste
        + config.quantidade_meses_validacao
        + config.quantidade_minima_meses_treino_walk_forward
    )

    if len(meses) < minimo_necessario:
        raise ValueError(
            "Poucos meses para fazer split temporal com segurança. "
            f"Meses disponíveis: {len(meses)} | "
            f"Mínimo exigido: {minimo_necessario}"
        )

    return meses


def filtrar_dataframe_por_meses(
    df: pl.DataFrame,
    nome_coluna_mes_ref: str,
    meses: list[Any],
) -> pl.DataFrame:
    """Filtra o dataframe pelos meses informados."""

    if not meses:
        return df.head(0)

    return df.filter(pl.col(nome_coluna_mes_ref).is_in(meses))


def separar_desenvolvimento_teste_temporal(
    df: pl.DataFrame,
    config: Configuracao,
) -> tuple[pl.DataFrame, pl.DataFrame, list[Any], list[Any]]:
    """
    Separa dados em:
    - desenvolvimento: tudo antes do bloco final OOT
    - teste final OOT: últimos meses totalmente intocados
    """

    meses_ordenados = obter_meses_ordenados(df, config)

    meses_teste = meses_ordenados[-config.quantidade_meses_teste:]
    meses_desenvolvimento = meses_ordenados[:-config.quantidade_meses_teste]

    df_desenvolvimento = filtrar_dataframe_por_meses(
        df=df,
        nome_coluna_mes_ref=config.nome_coluna_mes_ref,
        meses=meses_desenvolvimento,
    )

    df_teste = filtrar_dataframe_por_meses(
        df=df,
        nome_coluna_mes_ref=config.nome_coluna_mes_ref,
        meses=meses_teste,
    )

    if df_desenvolvimento.height == 0 or df_teste.height == 0:
        raise ValueError("O split entre desenvolvimento e teste final ficou vazio.")

    return df_desenvolvimento, df_teste, meses_desenvolvimento, meses_teste


def gerar_folds_walk_forward(
    meses_desenvolvimento: list[Any],
    config: Configuracao,
) -> list[dict[str, Any]]:
    """Gera folds walk-forward por mês."""

    tamanho_janela_validacao = config.quantidade_meses_validacao
    tamanho_minimo_treino = config.quantidade_minima_meses_treino_walk_forward

    quantidade_total_meses = len(meses_desenvolvimento)

    if quantidade_total_meses < (tamanho_minimo_treino + tamanho_janela_validacao):
        raise ValueError(
            "Meses insuficientes para walk-forward. "
            f"Meses de desenvolvimento: {quantidade_total_meses}, "
            f"mínimo de treino: {tamanho_minimo_treino}, "
            f"janela de validação: {tamanho_janela_validacao}"
        )

    folds = []
    indice_inicio_validacao = tamanho_minimo_treino

    while (indice_inicio_validacao + tamanho_janela_validacao) <= quantidade_total_meses:
        meses_treino_fold = meses_desenvolvimento[:indice_inicio_validacao]
        meses_validacao_fold = meses_desenvolvimento[
            indice_inicio_validacao: indice_inicio_validacao + tamanho_janela_validacao
        ]

        folds.append(
            {
                "numero_fold": len(folds) + 1,
                "meses_treino": meses_treino_fold,
                "meses_validacao": meses_validacao_fold,
            }
        )

        indice_inicio_validacao += tamanho_janela_validacao

    if config.quantidade_maxima_folds_walk_forward is not None:
        folds = folds[-config.quantidade_maxima_folds_walk_forward:]

        for indice, fold in enumerate(folds, start=1):
            fold["numero_fold"] = indice

    if not folds:
        raise ValueError("Nenhum fold de walk-forward foi gerado.")

    return folds


def identificar_tipos_de_features(
    df: pl.DataFrame,
    colunas_features: list[str],
) -> tuple[list[str], list[str]]:
    """Separa features numéricas de categóricas."""

    colunas_categoricas = []
    colunas_numericas = []

    for coluna in colunas_features:
        tipo = df.schema[coluna]

        if tipo in {
            pl.Utf8,
            pl.Categorical,
            pl.Enum,
            pl.Boolean,
            pl.Date,
            pl.Datetime,
        }:
            colunas_categoricas.append(coluna)
        elif tipo.is_numeric():
            colunas_numericas.append(coluna)
        else:
            colunas_categoricas.append(coluna)

    return colunas_numericas, colunas_categoricas


def preparar_dataframe_para_modelo(
    df: pl.DataFrame,
    colunas_features: list[str],
    colunas_categoricas: list[str],
    config: Configuracao,
    incluir_alvo: bool = True,
) -> pd.DataFrame:
    """Converte Polars para pandas sem pyarrow e padroniza missing/categóricas."""

    colunas_base = list(colunas_features)
    if incluir_alvo:
        colunas_base.append(config.nome_coluna_alvo)

    df_base = polars_para_pandas_sem_pyarrow(df.select(colunas_base))

    for coluna in colunas_categoricas:
        if coluna in df_base.columns:
            df_base[coluna] = df_base[coluna].astype("object").where(df_base[coluna].notna(), "MISSING")
            df_base[coluna] = df_base[coluna].astype(str)

    for coluna in colunas_features:
        if coluna not in colunas_categoricas:
            df_base[coluna] = pd.to_numeric(df_base[coluna], errors="coerce")

    if incluir_alvo:
        df_base[config.nome_coluna_alvo] = pd.to_numeric(
            df_base[config.nome_coluna_alvo],
            errors="coerce",
        ).fillna(0).astype(int)

    return df_base


def calcular_peso_classes(y: pd.Series) -> dict[int, float]:
    """Calcula pesos para balancear classes."""

    contagem = y.value_counts(dropna=False).to_dict()
    quantidade_total = len(y)

    if 0 not in contagem or 1 not in contagem:
        raise ValueError("O target precisa ter as classes 0 e 1 no conjunto de treino.")

    peso_0 = quantidade_total / (2 * contagem[0])
    peso_1 = quantidade_total / (2 * contagem[1])

    return {0: float(peso_0), 1: float(peso_1)}


def montar_parametros_catboost(
    config: Configuracao,
    pesos_classes: list[float] | None,
    verbose: int = 100,
    iteracoes: int | None = None,
) -> dict[str, Any]:
    """Monta os parâmetros do CatBoost."""

    return {
        "loss_function": "Logloss",
        "eval_metric": "AUC",
        "iterations": int(iteracoes if iteracoes is not None else config.iteracoes_catboost),
        "learning_rate": config.learning_rate_catboost,
        "depth": config.profundidade_catboost,
        "l2_leaf_reg": config.l2_leaf_reg_catboost,
        "random_strength": config.random_strength_catboost,
        "bagging_temperature": config.bagging_temperature_catboost,
        "random_seed": config.semente_aleatoria,
        "verbose": verbose,
        "auto_class_weights": None,
        "class_weights": pesos_classes,
        "allow_writing_files": False,
    }


def treinar_modelo_catboost_com_validacao(
    df_treino: pl.DataFrame,
    df_validacao: pl.DataFrame,
    colunas_features: list[str],
    colunas_categoricas: list[str],
    config: Configuracao,
    verbose: int = 100,
) -> tuple[CatBoostClassifier, pd.DataFrame, pd.DataFrame]:
    """Treina o CatBoost com validação temporal."""

    df_treino_pd = preparar_dataframe_para_modelo(
        df=df_treino,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
        incluir_alvo=True,
    )

    df_validacao_pd = preparar_dataframe_para_modelo(
        df=df_validacao,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
        incluir_alvo=True,
    )

    x_treino = df_treino_pd[colunas_features]
    y_treino = df_treino_pd[config.nome_coluna_alvo].astype(int)

    x_validacao = df_validacao_pd[colunas_features]
    y_validacao = df_validacao_pd[config.nome_coluna_alvo].astype(int)

    indices_categoricos = [colunas_features.index(coluna) for coluna in colunas_categoricas]

    train_pool = Pool(
        data=x_treino,
        label=y_treino,
        cat_features=indices_categoricos,
    )

    valid_pool = Pool(
        data=x_validacao,
        label=y_validacao,
        cat_features=indices_categoricos,
    )

    pesos_classes = None
    if config.usar_pesos_balanceamento:
        mapa_pesos = calcular_peso_classes(y_treino)
        pesos_classes = [mapa_pesos[0], mapa_pesos[1]]

    parametros_modelo = montar_parametros_catboost(
        config=config,
        pesos_classes=pesos_classes,
        verbose=verbose,
        iteracoes=config.iteracoes_catboost,
    )

    modelo = CatBoostClassifier(**parametros_modelo)

    modelo.fit(
        train_pool,
        eval_set=valid_pool,
        use_best_model=True,
        early_stopping_rounds=config.early_stopping_rounds_catboost,
    )

    return modelo, df_treino_pd, df_validacao_pd


def treinar_modelo_final_catboost(
    df_treino_final: pl.DataFrame,
    colunas_features: list[str],
    colunas_categoricas: list[str],
    config: Configuracao,
    iteracoes_finais: int,
    verbose: int = 100,
) -> tuple[CatBoostClassifier, pd.DataFrame]:
    """Treina o modelo final em todo o conjunto de desenvolvimento."""

    df_treino_pd = preparar_dataframe_para_modelo(
        df=df_treino_final,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
        incluir_alvo=True,
    )

    x_treino = df_treino_pd[colunas_features]
    y_treino = df_treino_pd[config.nome_coluna_alvo].astype(int)

    indices_categoricos = [colunas_features.index(coluna) for coluna in colunas_categoricas]

    train_pool = Pool(
        data=x_treino,
        label=y_treino,
        cat_features=indices_categoricos,
    )

    pesos_classes = None
    if config.usar_pesos_balanceamento:
        mapa_pesos = calcular_peso_classes(y_treino)
        pesos_classes = [mapa_pesos[0], mapa_pesos[1]]

    parametros_modelo = montar_parametros_catboost(
        config=config,
        pesos_classes=pesos_classes,
        verbose=verbose,
        iteracoes=iteracoes_finais,
    )

    modelo = CatBoostClassifier(**parametros_modelo)
    modelo.fit(train_pool)

    return modelo, df_treino_pd


def prever_probabilidade(
    modelo: CatBoostClassifier,
    df: pl.DataFrame,
    colunas_features: list[str],
    colunas_categoricas: list[str],
    config: Configuracao,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Gera probabilidades para qualquer subconjunto."""

    df_pd = preparar_dataframe_para_modelo(
        df=df,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
        incluir_alvo=True,
    )

    x = df_pd[colunas_features]
    probabilidades = modelo.predict_proba(x)[:, 1]

    return df_pd, probabilidades


def calcular_ks(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Calcula a estatística KS."""

    df_aux = pd.DataFrame({"y": y_true, "score": y_score}).sort_values("score", ascending=False)
    positivos = (df_aux["y"] == 1).sum()
    negativos = (df_aux["y"] == 0).sum()

    if positivos == 0 or negativos == 0:
        return float("nan")

    df_aux["cum_positivos"] = (df_aux["y"] == 1).cumsum() / positivos
    df_aux["cum_negativos"] = (df_aux["y"] == 0).cumsum() / negativos

    ks = (df_aux["cum_positivos"] - df_aux["cum_negativos"]).abs().max()
    return float(ks)


def calcular_metricas_classificacao(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Calcula métricas principais do classificador."""

    y_pred = (y_prob >= threshold).astype(int)

    try:
        auc_roc = roc_auc_score(y_true, y_prob)
    except Exception:
        auc_roc = float("nan")

    try:
        auc_pr = average_precision_score(y_true, y_prob)
    except Exception:
        auc_pr = float("nan")

    try:
        perda_log = log_loss(y_true, y_prob, labels=[0, 1])
    except Exception:
        perda_log = float("nan")

    try:
        brier = brier_score_loss(y_true, y_prob)
    except Exception:
        brier = float("nan")

    try:
        ks = calcular_ks(y_true, y_prob)
    except Exception:
        ks = float("nan")

    try:
        precisao = precision_score(y_true, y_pred, zero_division=0)
    except Exception:
        precisao = float("nan")

    try:
        recall = recall_score(y_true, y_pred, zero_division=0)
    except Exception:
        recall = float("nan")

    try:
        tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    except Exception:
        tn, fp, fn, tp = 0, 0, 0, 0

    return {
        "auc_roc": float(auc_roc),
        "auc_pr": float(auc_pr),
        "log_loss": float(perda_log),
        "brier_score": float(brier),
        "ks": float(ks),
        "precision_threshold_0_5": float(precisao),
        "recall_threshold_0_5": float(recall),
        "true_negative": int(tn),
        "false_positive": int(fp),
        "false_negative": int(fn),
        "true_positive": int(tp),
        "taxa_positivos_real": float(np.mean(y_true)),
        "taxa_positivos_predita_media": float(np.mean(y_prob)),
    }


def calcular_metricas_top_decile(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, float]:
    """Calcula força comercial do topo do ranking."""

    df_aux = pd.DataFrame({"y": y_true, "prob": y_prob}).sort_values("prob", ascending=False).reset_index(drop=True)

    if df_aux.empty:
        return {
            "precision_top_10": float("nan"),
            "recall_top_10": float("nan"),
            "lift_top_10": float("nan"),
        }

    quantidade_top = max(1, math.ceil(len(df_aux) * 0.10))
    df_top = df_aux.iloc[:quantidade_top]

    taxa_base = df_aux["y"].mean()
    taxa_top = df_top["y"].mean()

    if taxa_base == 0:
        lift = float("nan")
    else:
        lift = taxa_top / taxa_base

    positivos_totais = df_aux["y"].sum()
    if positivos_totais == 0:
        recall_top = float("nan")
    else:
        recall_top = df_top["y"].sum() / positivos_totais

    return {
        "precision_top_10": float(taxa_top),
        "recall_top_10": float(recall_top),
        "lift_top_10": float(lift),
    }


def probabilidade_para_score_serasa(probabilidade: np.ndarray) -> np.ndarray:
    """Converte probabilidade em score de 0 a 1000."""

    probabilidade = np.clip(probabilidade, 0.001, 0.999)
    score = np.rint(probabilidade * 1000).astype(int)
    score = np.clip(score, 0, 1000)
    return score


def classificar_label_score(score: pd.Series) -> pd.Series:
    """Classifica o score em labels interpretáveis."""

    condicoes = [
        score < 200,
        (score >= 200) & (score < 400),
        (score >= 400) & (score < 600),
        (score >= 600) & (score < 800),
        score >= 800,
    ]

    escolhas = [
        "Muito Baixo",
        "Baixo",
        "Médio",
        "Alto",
        "Muito Alto",
    ]

    labels = np.select(condicoes, escolhas, default="Indefinido")
    return pd.Series(labels, index=score.index)


def avaliar_por_faixa_score(
    df_resultado: pd.DataFrame,
    nome_coluna_alvo: str,
) -> pd.DataFrame:
    """Mostra taxa real de contratação por faixa de score."""

    resumo = (
        df_resultado.groupby("ClassificacaoScore", dropna=False)
        .agg(
            Quantidade=("ScoreCliente", "size"),
            TaxaRealContratacao=(nome_coluna_alvo, "mean"),
            ProbabilidadeMedia=("ProbabilidadeContratacao", "mean"),
            ScoreMedio=("ScoreCliente", "mean"),
        )
        .reset_index()
        .sort_values("ScoreMedio")
    )

    return resumo


def montar_dataframe_resultado(
    df_original: pl.DataFrame,
    probabilidades: np.ndarray,
    config: Configuracao,
) -> pd.DataFrame:
    """Monta o dataframe final scored com colunas principais de saída."""

    df_saida = polars_para_pandas_sem_pyarrow(df_original)

    df_saida["ProbabilidadeContratacao"] = probabilidades
    df_saida["ScoreCliente"] = probabilidade_para_score_serasa(probabilidades)
    df_saida["ClassificacaoScore"] = classificar_label_score(df_saida["ScoreCliente"])

    colunas_ordenacao = []
    for coluna in ["ScoreCliente", "ProbabilidadeContratacao", config.nome_coluna_mes_ref]:
        if coluna in df_saida.columns:
            colunas_ordenacao.append(coluna)

    if colunas_ordenacao:
        df_saida = df_saida.sort_values(
            by=colunas_ordenacao,
            ascending=[False, False, False][: len(colunas_ordenacao)],
        )

    return df_saida


def montar_tabela_final_exibicao(resultado_atual: pd.DataFrame) -> pd.DataFrame:
    """Monta a tabela final com exatamente as colunas pedidas."""

    colunas_desejadas = [
        "CNPJ_LIMPO",
        "IDEmpresa",
        "RazaoSocial",
        "ClasseValor",
        "DescricaoClasseValor",
        "ClasseEstrutural",
        "ClasseGeo",
        "ClassePotencial",
        "ClusterGrupoCliente",
        "DiasDesdeUltimaCompra",
        "ValorVida",
        "ScoreCliente",
        "ClassificacaoScore",
    ]

    colunas_existentes = [coluna for coluna in colunas_desejadas if coluna in resultado_atual.columns]

    if not colunas_existentes:
        raise ValueError(
            "Nenhuma das colunas desejadas para a tabela final existe no resultado. "
            "Verifique se a query está retornando esses campos."
        )

    tabela_final = resultado_atual[colunas_existentes].copy()

    if "ScoreCliente" in tabela_final.columns:
        tabela_final = tabela_final.sort_values("ScoreCliente", ascending=False)

    return tabela_final


def extrair_importancias(
    modelo: CatBoostClassifier,
    colunas_features: list[str],
) -> pd.DataFrame:
    """Extrai importâncias do modelo."""

    importancias = modelo.get_feature_importance()
    df_importancias = pd.DataFrame(
        {
            "Feature": colunas_features,
            "Importancia": importancias,
        }
    ).sort_values("Importancia", ascending=False)

    return df_importancias


def obter_y_numpy(df: pl.DataFrame, nome_coluna_alvo: str) -> np.ndarray:
    """Converte a coluna alvo para numpy int de forma segura."""

    y = df.get_column(nome_coluna_alvo).to_list()
    return np.array([0 if valor is None else int(valor) for valor in y], dtype=int)


def executar_walk_forward_validation(
    df_desenvolvimento: pl.DataFrame,
    meses_desenvolvimento: list[Any],
    colunas_features: list[str],
    colunas_categoricas: list[str],
    config: Configuracao,
) -> tuple[pd.DataFrame, pd.DataFrame, list[int]]:
    """Executa o walk-forward."""

    folds = gerar_folds_walk_forward(meses_desenvolvimento, config)

    registros_resumo_folds: list[dict[str, Any]] = []
    registros_predicoes_validacao: list[pd.DataFrame] = []
    melhores_iteracoes: list[int] = []

    print("\n" + "=" * 100)
    print("INÍCIO DO WALK-FORWARD VALIDATION")
    print("=" * 100)
    print(f"Quantidade de folds gerados:{len(folds)}")
    print(f"Meses de desenvolvimento:{len(meses_desenvolvimento)}")
    print(f"Janela de validação por fold:{config.quantidade_meses_validacao}")
    print(f"Mínimo de meses em treino:{config.quantidade_minima_meses_treino_walk_forward}")

    for fold in folds:
        numero_fold = fold["numero_fold"]
        meses_treino_fold = fold["meses_treino"]
        meses_validacao_fold = fold["meses_validacao"]

        df_treino_fold = filtrar_dataframe_por_meses(
            df=df_desenvolvimento,
            nome_coluna_mes_ref=config.nome_coluna_mes_ref,
            meses=meses_treino_fold,
        )

        df_validacao_fold = filtrar_dataframe_por_meses(
            df=df_desenvolvimento,
            nome_coluna_mes_ref=config.nome_coluna_mes_ref,
            meses=meses_validacao_fold,
        )

        if df_treino_fold.height == 0 or df_validacao_fold.height == 0:
            raise ValueError(f"Fold {numero_fold} ficou vazio. Revise a geração dos folds.")

        print("\n" + "-" * 100)
        print(f"Fold {numero_fold}")
        print(f"Treino: {meses_treino_fold[0]} até {meses_treino_fold[-1]} | linhas: {df_treino_fold.height:,}")
        print(f"Validação:{meses_validacao_fold[0]} até {meses_validacao_fold[-1]} | linhas: {df_validacao_fold.height:,}")

        modelo_fold, _, _ = treinar_modelo_catboost_com_validacao(
            df_treino=df_treino_fold,
            df_validacao=df_validacao_fold,
            colunas_features=colunas_features,
            colunas_categoricas=colunas_categoricas,
            config=config,
            verbose=100,
        )

        melhor_iteracao_fold = int(modelo_fold.get_best_iteration())
        if melhor_iteracao_fold <= 0:
            melhor_iteracao_fold = int(modelo_fold.tree_count_)

        melhores_iteracoes.append(melhor_iteracao_fold)

        _, prob_validacao_fold = prever_probabilidade(
            modelo=modelo_fold,
            df=df_validacao_fold,
            colunas_features=colunas_features,
            colunas_categoricas=colunas_categoricas,
            config=config,
        )

        y_validacao_fold = obter_y_numpy(df_validacao_fold, config.nome_coluna_alvo)

        metricas_fold = calcular_metricas_classificacao(y_validacao_fold, prob_validacao_fold)
        metricas_topo_fold = calcular_metricas_top_decile(y_validacao_fold, prob_validacao_fold)

        registros_resumo_folds.append(
            {
                "Fold": numero_fold,
                "MesInicioTreino": str(meses_treino_fold[0]),
                "MesFimTreino": str(meses_treino_fold[-1]),
                "MesInicioValidacao": str(meses_validacao_fold[0]),
                "MesFimValidacao": str(meses_validacao_fold[-1]),
                "LinhasTreino": int(df_treino_fold.height),
                "LinhasValidacao": int(df_validacao_fold.height),
                "BestIteration": int(melhor_iteracao_fold),
                "AUC_ROC": float(metricas_fold["auc_roc"]),
                "AUC_PR": float(metricas_fold["auc_pr"]),
                "LogLoss": float(metricas_fold["log_loss"]),
                "BrierScore": float(metricas_fold["brier_score"]),
                "KS": float(metricas_fold["ks"]),
                "Precision_0_5": float(metricas_fold["precision_threshold_0_5"]),
                "Recall_0_5": float(metricas_fold["recall_threshold_0_5"]),
                "TaxaBase": float(metricas_fold["taxa_positivos_real"]),
                "ProbMedia": float(metricas_fold["taxa_positivos_predita_media"]),
                "PrecisionTop10": float(metricas_topo_fold["precision_top_10"]),
                "RecallTop10": float(metricas_topo_fold["recall_top_10"]),
                "LiftTop10": float(metricas_topo_fold["lift_top_10"]),
            }
        )

        predicoes_validacao_fold = montar_dataframe_resultado(
            df_original=df_validacao_fold,
            probabilidades=prob_validacao_fold,
            config=config,
        ).copy()

        predicoes_validacao_fold["FoldWalkForward"] = numero_fold
        registros_predicoes_validacao.append(predicoes_validacao_fold)

    df_resumo_folds = pd.DataFrame(registros_resumo_folds)
    df_predicoes_validacao = pd.concat(registros_predicoes_validacao, ignore_index=True)

    return df_resumo_folds, df_predicoes_validacao, melhores_iteracoes


def escolher_iteracoes_finais(
    melhores_iteracoes: list[int],
    config: Configuracao,
) -> int:
    """Define o número de iterações do modelo final."""

    if not melhores_iteracoes:
        raise ValueError("A lista de melhores iterações do walk-forward está vazia.")

    mediana_iteracoes = int(round(statistics.median(melhores_iteracoes)))
    mediana_iteracoes = max(config.iteracoes_minimas_modelo_final, mediana_iteracoes)
    mediana_iteracoes = min(config.iteracoes_catboost, mediana_iteracoes)

    return mediana_iteracoes





def imprimir_resumo_dataset_walk_forward(
    df_total: pl.DataFrame,
    df_desenvolvimento: pl.DataFrame,
    df_teste: pl.DataFrame,
    meses_desenvolvimento: list[Any],
    meses_teste: list[Any],
    config: Configuracao,
) -> None:
    """Imprime resumo geral da base."""

    print("\n" + "=" * 100)
    print("RESUMO DO DATASET")
    print("=" * 100)
    print(f"Linhas totais:{df_total.height:,}")
    print(f"Linhas desenvolvimento:{df_desenvolvimento.height:,}")
    print(f"Linhas teste final OOT: {df_teste.height:,}")
    print("-" * 100)

    taxa_desenvolvimento = (
        df_desenvolvimento.select(pl.col(config.nome_coluna_alvo).mean()).item()
        if df_desenvolvimento.height > 0
        else None
    )

    taxa_teste = (
        df_teste.select(pl.col(config.nome_coluna_alvo).mean()).item()
        if df_teste.height > 0
        else None
    )

    print("Desenvolvimento")
    print(f"Período: {meses_desenvolvimento[0]} até {meses_desenvolvimento[-1]}")
    print(f"Taxa média do target: {taxa_desenvolvimento:.4f}" if taxa_desenvolvimento is not None else "Taxa média do target: n/a")
    print("-" * 100)
    print("Teste Final OOT")
    print(f"Período: {meses_teste[0]} até {meses_teste[-1]}")
    print(f"Taxa média do target: {taxa_teste:.4f}" if taxa_teste is not None else "Taxa média do target: n/a")



def imprimir_metricas(
    nome_bloco: str,
    metricas_base: dict[str, Any],
    metricas_topo: dict[str, Any],
) -> None:
    """Imprime as métricas principais."""

    print("\n" + "=" * 100)
    print(f"MÉTRICAS - {nome_bloco.upper()}")
    print("=" * 100)
    print(f"AUC ROC:{metricas_base['auc_roc']:.6f}")
    print(f"AUC PR:{metricas_base['auc_pr']:.6f}")
    print(f"Log Loss:{metricas_base['log_loss']:.6f}")
    print(f"Brier Score:{metricas_base['brier_score']:.6f}")
    print(f"KS:{metricas_base['ks']:.6f}")
    print(f"Precision @ 0.5:{metricas_base['precision_threshold_0_5']:.6f}")
    print(f"Recall @ 0.5:{metricas_base['recall_threshold_0_5']:.6f}")
    print(f"Taxa base real:{metricas_base['taxa_positivos_real']:.6f}")
    print(f"Probabilidade média:{metricas_base['taxa_positivos_predita_media']:.6f}")
    print("-" * 100)
    print(f"Precision Top 10%:{metricas_topo['precision_top_10']:.6f}")
    print(f"Recall Top 10%:{metricas_topo['recall_top_10']:.6f}")
    print(f"Lift Top 10%:{metricas_topo['lift_top_10']:.6f}")
    print("-" * 100)
    print(f"TN: {metricas_base['true_negative']:,}")
    print(f"FP: {metricas_base['false_positive']:,}")
    print(f"FN: {metricas_base['false_negative']:,}")
    print(f"TP: {metricas_base['true_positive']:,}")


def imprimir_resumo_walk_forward_folds(df_resumo_folds: pd.DataFrame) -> None:
    """Imprime um resumo consolidado do walk-forward."""

    print("\n" + "=" * 100)
    print("RESUMO DOS FOLDS - WALK-FORWARD")
    print("=" * 100)
    print(df_resumo_folds.to_string(index=False))

    print("\n" + "-" * 100)
    print("MÉDIAS DOS FOLDS WALK-FORWARD")
    print("-" * 100)

    colunas_metricas = [
        "AUC_ROC",
        "AUC_PR",
        "LogLoss",
        "BrierScore",
        "KS",
        "Precision_0_5",
        "Recall_0_5",
        "TaxaBase",
        "ProbMedia",
        "PrecisionTop10",
        "RecallTop10",
        "LiftTop10",
        "BestIteration",
    ]

    medias = df_resumo_folds[colunas_metricas].mean(numeric_only=True)
    for coluna, valor in medias.items():
        print(f"{coluna:20} {valor:.6f}")


def salvar_resultados(
    resultado_historico: pd.DataFrame,
    resultado_atual: pd.DataFrame,
    tabela_final: pd.DataFrame,
    df_importancias: pd.DataFrame,
    resumo_faixas: pd.DataFrame,
    payload_metricas: dict[str, Any],
    df_resumo_folds_walk_forward: pd.DataFrame,
    df_predicoes_walk_forward: pd.DataFrame,
    config: Configuracao,
) -> None:
    """Salva os artefatos finais do pipeline."""

    config.pasta_saida_dados.mkdir(parents=True, exist_ok=True)
    config.pasta_saida_metricas.mkdir(parents=True, exist_ok=True)

    if config.salvar_score_historico_completo:
        resultado_historico.to_csv(
            config.caminho_saida_score_historico_csv,
            index=False,
            encoding="utf-8-sig",
        )

    resultado_atual.to_csv(
        config.caminho_saida_score_atual_csv,
        index=False,
        encoding="utf-8-sig",
    )
    tabela_final.to_csv(
        config.caminho_saida_tabela_final_csv,
        index=False,
        encoding="utf-8-sig",
    )
    df_importancias.to_csv(
        config.caminho_saida_importancias_csv,
        index=False,
        encoding="utf-8-sig",
    )
    resumo_faixas.to_csv(
        config.caminho_saida_resumo_faixas_csv,
        index=False,
        encoding="utf-8-sig",
    )
    df_resumo_folds_walk_forward.to_csv(
        config.caminho_saida_walk_forward_folds_csv,
        index=False,
        encoding="utf-8-sig",
    )
    df_predicoes_walk_forward.to_csv(
        config.caminho_saida_walk_forward_predicoes_csv,
        index=False,
        encoding="utf-8-sig",
    )

    with open(config.caminho_saida_metricas_json, "w", encoding="utf-8") as arquivo_json:
        json.dump(payload_metricas, arquivo_json, ensure_ascii=False, indent=4, default=str)


def preparar_dataframe_atualizacao_perfil_empresa(resultado_atual: pd.DataFrame) -> pd.DataFrame:
    """
    Prepara o dataframe mínimo para atualizar a DimClassificacacaoClientes.

    Regra:
    - ScorePerfilEmpresa         <- ScoreCliente
    - ClassificacaoPerfilEmpresa <- ClassificacaoScore
    - chave de match            <- IDEmpresa
    """

    colunas_obrigatorias = {"IDEmpresa", "ScoreCliente", "ClassificacaoScore"}
    faltantes = [coluna for coluna in colunas_obrigatorias if coluna not in resultado_atual.columns]
    if faltantes:
        raise ValueError(
            "O resultado atual não possui as colunas necessárias para atualizar a "
            f"DimClassificacacaoClientes: {faltantes}"
        )

    df_update = resultado_atual[["IDEmpresa", "ScoreCliente", "ClassificacaoScore"]].copy()

    df_update["IDEmpresa"] = pd.to_numeric(df_update["IDEmpresa"], errors="coerce")
    df_update["ScoreCliente"] = pd.to_numeric(df_update["ScoreCliente"], errors="coerce")
    df_update["ClassificacaoScore"] = df_update["ClassificacaoScore"].astype("string")

    df_update = df_update.dropna(subset=["IDEmpresa"])
    df_update["IDEmpresa"] = df_update["IDEmpresa"].astype(int)

    df_update["ScoreCliente"] = df_update["ScoreCliente"].round().astype("Int64")

    df_update = df_update.rename(
        columns={
            "ScoreCliente": "ScorePerfilEmpresa",
            "ClassificacaoScore": "ClassificacaoPerfilEmpresa",
        }
    )

    df_update = df_update.drop_duplicates(subset=["IDEmpresa"], keep="first")
    df_update = df_update.sort_values("IDEmpresa").reset_index(drop=True)

    return df_update


def atualizar_dim_classificacao_clientes_com_score(
    engine_sql,
    resultado_atual: pd.DataFrame,
) -> dict[str, Any]:
    """
    Atualiza os campos:
      - ScorePerfilEmpresa
      - ClassificacaoPerfilEmpresa

    na tabela:
      [Integracao].[Silver].[DimClassificacacaoClientes]

    usando IDEmpresa como chave.

    Regra:
    - ScorePerfilEmpresa         <- ScoreCliente
    - ClassificacaoPerfilEmpresa <- ClassificacaoScore

    Observação:
    - Esta rotina atualiza apenas registros já existentes.
    - Não faz insert de novos IDEmpresa.
    """

    colunas_obrigatorias = {"IDEmpresa", "ScoreCliente", "ClassificacaoScore"}
    faltantes = [coluna for coluna in colunas_obrigatorias if coluna not in resultado_atual.columns]
    if faltantes:
        raise ValueError(
            "O resultado atual não possui as colunas necessárias para atualizar a "
            f"DimClassificacacaoClientes: {faltantes}"
        )

    df_update = resultado_atual[["IDEmpresa", "ScoreCliente", "ClassificacaoScore"]].copy()

    df_update["IDEmpresa"] = pd.to_numeric(df_update["IDEmpresa"], errors="coerce")
    df_update["ScoreCliente"] = pd.to_numeric(df_update["ScoreCliente"], errors="coerce")
    df_update["ClassificacaoScore"] = df_update["ClassificacaoScore"].astype("string")

    df_update = df_update.dropna(subset=["IDEmpresa"])
    df_update["IDEmpresa"] = df_update["IDEmpresa"].astype(int)

    df_update["ScoreCliente"] = df_update["ScoreCliente"].round().astype("Int64")

    df_update = df_update.drop_duplicates(subset=["IDEmpresa"], keep="first")
    df_update = df_update.sort_values("IDEmpresa").reset_index(drop=True)

    if df_update.empty:
        return {
            "linhas_resultado_snapshot": int(len(resultado_atual)),
            "linhas_enviadas_atualizacao": 0,
            "linhas_encontradas_destino": 0,
            "linhas_atualizadas": 0,
            "linhas_nao_encontradas_destino": 0,
        }

    conexao_bruta = engine_sql.raw_connection()
    cursor = None

    try:
        cursor = conexao_bruta.cursor()

        ide_empresas = [int(valor) for valor in df_update["IDEmpresa"].tolist()]
        placeholders = ",".join("?" for _ in ide_empresas)

        cursor.execute(
            f"""
            SELECT COUNT(1)
            FROM [Integracao].[Silver].[DimClassificacacaoClientes]
            WHERE IDEmpresa IN ({placeholders});
            """,
            ide_empresas,
        )
        linhas_encontradas_destino = int(cursor.fetchone()[0])

        linhas_nao_encontradas_destino = int(len(df_update) - linhas_encontradas_destino)

        registros_update = []
        for linha in df_update.itertuples(index=False):
            score_valor = None if pd.isna(linha.ScoreCliente) else int(linha.ScoreCliente)
            classificacao_valor = None if pd.isna(linha.ClassificacaoScore) else str(linha.ClassificacaoScore)

            registros_update.append(
                (
                    score_valor,
                    classificacao_valor,
                    int(linha.IDEmpresa),
                )
            )

        cursor.executemany(
            """
            UPDATE [Integracao].[Silver].[DimClassificacacaoClientes]
               SET ScorePerfilEmpresa = ?,
                   ClassificacaoPerfilEmpresa = ?
             WHERE IDEmpresa = ?;
            """,
            registros_update,
        )

        linhas_atualizadas = (
            int(cursor.rowcount)
            if cursor.rowcount is not None and cursor.rowcount >= 0
            else linhas_encontradas_destino
        )

        conexao_bruta.commit()

        return {
            "linhas_resultado_snapshot": int(len(resultado_atual)),
            "linhas_enviadas_atualizacao": int(len(df_update)),
            "linhas_encontradas_destino": int(linhas_encontradas_destino),
            "linhas_atualizadas": int(linhas_atualizadas),
            "linhas_nao_encontradas_destino": int(linhas_nao_encontradas_destino),
        }

    except Exception:
        try:
            conexao_bruta.rollback()
        except Exception:
            pass
        raise

    finally:
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            pass

        try:
            conexao_bruta.close()
        except Exception:
            pass


def executar_pipeline() -> None:
    """Executa o fluxo completo do score de propensão de contratação do cliente."""

    config = Configuracao()

    print("=" * 100)
    print("INÍCIO DO PIPELINE - SCORE DE PROPENSÃO DE CONTRATAÇÃO")
    print("=" * 100)

    engine_sql = criar_engine_sql(config)
    query_sql = ler_query_sql(config.caminho_query_sql)

    print("\nLendo dados do SQL Server...")
    df = carregar_dados_sql_em_polars(engine_sql, query_sql)
    print(f"Linhas carregadas: {df.height:,}")
    print(f"Colunas carregadas: {len(df.columns):,}")

    df = limpar_e_padronizar_dataset(df, config)
    df = adicionar_features_temporais(df, config)

    colunas_excluir = obter_colunas_excluir_por_padrao(df, config)
    colunas_features = [
        coluna
        for coluna in df.columns
        if coluna not in colunas_excluir
    ]

    if not colunas_features:
        raise ValueError("Nenhuma feature sobrou após exclusões. Revise a query e as regras do pipeline.")

    colunas_numericas, colunas_categoricas = identificar_tipos_de_features(df, colunas_features)

    print("\nResumo das features:")
    print(f"Total de features:{len(colunas_features):,}")
    print(f"Features numéricas:{len(colunas_numericas):,}")
    print(f"Features categóricas: {len(colunas_categoricas):,}")

    print("\nSeparando desenvolvimento e teste final OOT por tempo...")
    df_desenvolvimento, df_teste, meses_desenvolvimento, meses_teste = separar_desenvolvimento_teste_temporal(df, config)

    imprimir_resumo_dataset_walk_forward(
        df_total=df,
        df_desenvolvimento=df_desenvolvimento,
        df_teste=df_teste,
        meses_desenvolvimento=meses_desenvolvimento,
        meses_teste=meses_teste,
        config=config,
    )

    (
        df_resumo_folds_walk_forward,
        df_predicoes_walk_forward,
        melhores_iteracoes,
    ) = executar_walk_forward_validation(
        df_desenvolvimento=df_desenvolvimento,
        meses_desenvolvimento=meses_desenvolvimento,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
    )

    imprimir_resumo_walk_forward_folds(df_resumo_folds_walk_forward)

    iteracoes_finais = escolher_iteracoes_finais(melhores_iteracoes, config)

    print("\n" + "=" * 100)
    print("DEFINIÇÃO DO MODELO FINAL")
    print("=" * 100)
    print(f"Melhores iterações por fold: {melhores_iteracoes}")
    print(f"Iterações finais escolhidas (mediana dos folds): {iteracoes_finais}")

    print("\nTreinando modelo final em todo o desenvolvimento...")
    modelo_final, _ = treinar_modelo_final_catboost(
        df_treino_final=df_desenvolvimento,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
        iteracoes_finais=iteracoes_finais,
        verbose=100,
    )

    print("\nGerando previsões do modelo final...")
    _, prob_desenvolvimento = prever_probabilidade(
        modelo=modelo_final,
        df=df_desenvolvimento,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
    )

    _, prob_teste = prever_probabilidade(
        modelo=modelo_final,
        df=df_teste,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
    )

    y_desenvolvimento = obter_y_numpy(df_desenvolvimento, config.nome_coluna_alvo)
    y_teste = obter_y_numpy(df_teste, config.nome_coluna_alvo)

    y_walk_forward = pd.to_numeric(
        df_predicoes_walk_forward[config.nome_coluna_alvo],
        errors="coerce",
    ).fillna(0).astype(int).to_numpy()

    prob_walk_forward = pd.to_numeric(
        df_predicoes_walk_forward["ProbabilidadeContratacao"],
        errors="coerce",
    ).fillna(0.0).to_numpy()

    metricas_desenvolvimento = calcular_metricas_classificacao(y_desenvolvimento, prob_desenvolvimento)
    metricas_walk_forward = calcular_metricas_classificacao(y_walk_forward, prob_walk_forward)
    metricas_teste = calcular_metricas_classificacao(y_teste, prob_teste)

    metricas_top_desenvolvimento = calcular_metricas_top_decile(y_desenvolvimento, prob_desenvolvimento)
    metricas_top_walk_forward = calcular_metricas_top_decile(y_walk_forward, prob_walk_forward)
    metricas_top_teste = calcular_metricas_top_decile(y_teste, prob_teste)

    imprimir_metricas(
        "Desenvolvimento (In-Sample)",
        metricas_desenvolvimento,
        metricas_top_desenvolvimento,
    )
    imprimir_metricas(
        "Walk-Forward OOF",
        metricas_walk_forward,
        metricas_top_walk_forward,
    )
    imprimir_metricas(
        "Teste Final OOT",
        metricas_teste,
        metricas_top_teste,
    )

    print("\nMontando score histórico completo com modelo final...")
    _, prob_total = prever_probabilidade(
        modelo=modelo_final,
        df=df,
        colunas_features=colunas_features,
        colunas_categoricas=colunas_categoricas,
        config=config,
    )

    resultado_historico = montar_dataframe_resultado(
        df_original=df,
        probabilidades=prob_total,
        config=config,
    )

    if config.nome_coluna_mes_ref not in resultado_historico.columns:
        raise ValueError("A coluna MesRef não está disponível no resultado final.")

    mes_mais_recente = resultado_historico[config.nome_coluna_mes_ref].max()
    resultado_atual = resultado_historico.loc[
        resultado_historico[config.nome_coluna_mes_ref] == mes_mais_recente
    ].copy()

    resultado_atual = resultado_atual.sort_values(
        ["ScoreCliente", "ProbabilidadeContratacao"],
        ascending=[False, False],
    )

    meses_teste_unicos = df_teste.get_column(config.nome_coluna_mes_ref).unique().to_list()

    resumo_faixas_teste = avaliar_por_faixa_score(
        df_resultado=resultado_historico.loc[
            resultado_historico[config.nome_coluna_mes_ref].isin(meses_teste_unicos)
        ].copy(),
        nome_coluna_alvo=config.nome_coluna_alvo,
    )

    df_importancias = extrair_importancias(modelo_final, colunas_features)
    tabela_final = montar_tabela_final_exibicao(resultado_atual)

    print("\n" + "=" * 100)
    print("TOP 25 FEATURES MAIS IMPORTANTES")
    print("=" * 100)
    print(df_importancias.head(25).to_string(index=False))

    print("\n" + "=" * 100)
    print("RESUMO POR CLASSIFICAÇÃO DE SCORE - TESTE FINAL OOT")
    print("=" * 100)
    print(resumo_faixas_teste.to_string(index=False))

    print("\n" + "=" * 100)
    print("TABELA FINAL DE SAÍDA - SNAPSHOT MAIS RECENTE")
    print("=" * 100)
    print(
        tabela_final.head(config.quantidade_linhas_exibir_tabela_final).to_string(index=False)
    )

    print("\n" + "=" * 100)
    print("ATUALIZAÇÃO DA DIMCLASSIFICACACAOCLIENTES")
    print("=" * 100)
    resumo_atualizacao_dim = atualizar_dim_classificacao_clientes_com_score(
        engine_sql=engine_sql,
        resultado_atual=resultado_atual,
    )
    print(f"Linhas no snapshot atual: {resumo_atualizacao_dim['linhas_resultado_snapshot']:,}")
    print(f"Linhas enviadas para atualização: {resumo_atualizacao_dim['linhas_enviadas_atualizacao']:,}")
    print(f"IDEmpresa encontrados no destino: {resumo_atualizacao_dim['linhas_encontradas_destino']:,}")
    print(f"Linhas efetivamente atualizadas: {resumo_atualizacao_dim['linhas_atualizadas']:,}")
    print(f"IDEmpresa não encontrados no destino:{resumo_atualizacao_dim['linhas_nao_encontradas_destino']:,}")

    payload_metricas = {
        "configuracao": {
            "quantidade_meses_validacao": config.quantidade_meses_validacao,
            "quantidade_meses_teste": config.quantidade_meses_teste,
            "quantidade_minima_meses_treino_walk_forward": config.quantidade_minima_meses_treino_walk_forward,
            "quantidade_maxima_folds_walk_forward": config.quantidade_maxima_folds_walk_forward,
            "iteracoes_catboost": config.iteracoes_catboost,
            "iteracoes_finais_modelo": iteracoes_finais,
            "learning_rate_catboost": config.learning_rate_catboost,
            "profundidade_catboost": config.profundidade_catboost,
            "l2_leaf_reg_catboost": config.l2_leaf_reg_catboost,
            "random_strength_catboost": config.random_strength_catboost,
            "bagging_temperature_catboost": config.bagging_temperature_catboost,
            "early_stopping_rounds_catboost": config.early_stopping_rounds_catboost,
            "semente_aleatoria": config.semente_aleatoria,
            "usar_pesos_balanceamento": config.usar_pesos_balanceamento,
        },
        "resumo_dataset": {
            "linhas_total": int(df.height),
            "linhas_desenvolvimento": int(df_desenvolvimento.height),
            "linhas_teste_final_oot": int(df_teste.height),
            "quantidade_features": int(len(colunas_features)),
            "quantidade_features_numericas": int(len(colunas_numericas)),
            "quantidade_features_categoricas": int(len(colunas_categoricas)),
            "quantidade_folds_walk_forward": int(len(df_resumo_folds_walk_forward)),
        },
        "periodos": {
            "inicio_desenvolvimento": str(meses_desenvolvimento[0]),
            "fim_desenvolvimento": str(meses_desenvolvimento[-1]),
            "inicio_teste_final_oot": str(meses_teste[0]),
            "fim_teste_final_oot": str(meses_teste[-1]),
            "mes_snapshot_mais_recente": str(mes_mais_recente),
        },
        "walk_forward": {
            "melhores_iteracoes_por_fold": [int(valor) for valor in melhores_iteracoes],
            "media_auc_roc_folds": float(df_resumo_folds_walk_forward["AUC_ROC"].mean()),
            "media_auc_pr_folds": float(df_resumo_folds_walk_forward["AUC_PR"].mean()),
            "media_logloss_folds": float(df_resumo_folds_walk_forward["LogLoss"].mean()),
            "media_brier_folds": float(df_resumo_folds_walk_forward["BrierScore"].mean()),
            "media_ks_folds": float(df_resumo_folds_walk_forward["KS"].mean()),
        },
        "metricas_desenvolvimento": metricas_desenvolvimento,
        "metricas_top_desenvolvimento": metricas_top_desenvolvimento,
        "metricas_walk_forward_oof": metricas_walk_forward,
        "metricas_top_walk_forward_oof": metricas_top_walk_forward,
        "metricas_teste_final_oot": metricas_teste,
        "metricas_top_teste_final_oot": metricas_top_teste,
        "colunas_tabela_final": list(tabela_final.columns),
        "quantidade_linhas_tabela_final": int(len(tabela_final)),
        "atualizacao_dim_classificacao_clientes": resumo_atualizacao_dim,
    }

    salvar_resultados(
        resultado_historico=resultado_historico,
        resultado_atual=resultado_atual,
        tabela_final=tabela_final,
        df_importancias=df_importancias,
        resumo_faixas=resumo_faixas_teste,
        payload_metricas=payload_metricas,
        df_resumo_folds_walk_forward=df_resumo_folds_walk_forward,
        df_predicoes_walk_forward=df_predicoes_walk_forward,
        config=config,
    )

    print("\n" + "=" * 100)
    print("ARQUIVOS GERADOS")
    print("=" * 100)
    print(f"Histórico scored: {config.caminho_saida_score_historico_csv}")
    print(f"Snapshot atual: {config.caminho_saida_score_atual_csv}")
    print(f"Tabela final: {config.caminho_saida_tabela_final_csv}")
    print(f"Métricas JSON: {config.caminho_saida_metricas_json}")
    print(f"Importâncias: {config.caminho_saida_importancias_csv}")
    print(f"Faixas de score: {config.caminho_saida_resumo_faixas_csv}")
    print(f"Resumo folds walk-forward: {config.caminho_saida_walk_forward_folds_csv}")
    print(f"Predições walk-forward OOF: {config.caminho_saida_walk_forward_predicoes_csv}")

    print("\n" + "=" * 100)
    print("FIM DO PIPELINE")
    print("=" * 100)


@dag(
    dag_id="pipeline_score_empresas",
    description="Pipeline Score Empresas",
    schedule="0 10 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 18, 10, 0, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["Euromidia", "MachineLearning", "Comercial", "Empresas"],
)
def pipeline_score_empresas():
    @task(task_id="executar_pipeline_score_empresas")
    def tarefa_executar_pipeline() -> None:
        executar_pipeline()

    tarefa_executar_pipeline()


pipeline_score_empresas_dag = pipeline_score_empresas()