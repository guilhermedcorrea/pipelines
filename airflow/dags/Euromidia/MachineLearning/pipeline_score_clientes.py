from __future__ import annotations

import json
import math
import os
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd
import pendulum
import polars as pl
from airflow.sdk import dag, task
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sqlalchemy import text

from hooks.BancodeDados.SqlServer import HookSqlServer
from dags._libs.auditoria_task import (
    adicionar_observacao,
    adicionar_validacao,
    criar_resumo_auditoria,
    definir_amostra,
    publicar_resumo_auditoria,
    registrar_erro_no_resumo,
)


@dataclass(frozen=True)
class Configuracao:
    """Configuração central do pipeline."""

    conn_id_sql: str = "mssql_integracao"

    caminho_query_sql: Path = Path(
        "/opt/airflow/queries/Euromidia/MachineLearning/algoritmo_score_cliente.sql"
    )

    pasta_saida_dados: Path = Path(
        "/opt/airflow/Dados/Euromidia/MachineLearning"
    )
    pasta_saida_metricas: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Metricas"
    )
    pasta_saida_intermediarios: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Intermediarios"
    )
    pasta_saida_historico_execucoes: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/HistoricoExecucoes"
    )

    caminho_dataset_bruto_parquet: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Intermediarios/score_cliente_dataset_bruto.parquet"
    )
    caminho_dataset_preparado_parquet: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Intermediarios/score_cliente_dataset_preparado.parquet"
    )
    caminho_metadados_preparacao_json: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Intermediarios/score_cliente_metadados_preparacao.json"
    )
    caminho_resumo_treino_json: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Intermediarios/score_cliente_resumo_treino.json"
    )
    caminho_saida_payload_metricas_json: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Intermediarios/score_cliente_payload_metricas.json"
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
    caminho_saida_dashboard_spec_json: Path = Path(
        "/opt/airflow/Artefatos/Euromidia/MachineLearning/Metricas/score_cliente_dashboard_spec.json"
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
    quantidade_linhas_amostra_auditoria: int = 5


def garantir_pastas_saida(config: Configuracao) -> None:
    """Garante a existência das pastas necessárias."""
    config.pasta_saida_dados.mkdir(parents=True, exist_ok=True)
    config.pasta_saida_metricas.mkdir(parents=True, exist_ok=True)
    config.pasta_saida_intermediarios.mkdir(parents=True, exist_ok=True)
    config.pasta_saida_historico_execucoes.mkdir(parents=True, exist_ok=True)


def obter_identificador_execucao_airflow() -> str:
    """Monta um identificador estável da execução para versionar artefatos por run."""

    run_id = str(os.getenv("AIRFLOW_CTX_DAG_RUN_ID", "")).strip()

    if not run_id:
        run_id = pendulum.now("America/Sao_Paulo").format("YYYYMMDD_HHmmss")

    identificador_execucao = re.sub(r"[^0-9A-Za-z_.-]+", "_", run_id).strip("._-")

    if not identificador_execucao:
        identificador_execucao = pendulum.now("America/Sao_Paulo").format("YYYYMMDD_HHmmss")

    return identificador_execucao[:180]


def construir_caminhos_artefatos_execucao(
    config: Configuracao,
    identificador_execucao: str,
) -> dict[str, Path]:
    """Cria caminhos versionados por execução para preservar histórico real do plugin."""

    pasta_execucao = config.pasta_saida_historico_execucoes / identificador_execucao

    return {
        "pasta_execucao": pasta_execucao,
        "score_historico_csv": pasta_execucao / "score_cliente_historico.csv",
        "score_atual_csv": pasta_execucao / "score_cliente_atual.csv",
        "tabela_final_csv": pasta_execucao / "score_cliente_tabela_final.csv",
        "importancias_csv": pasta_execucao / "score_cliente_importancias.csv",
        "resumo_faixas_csv": pasta_execucao / "score_cliente_faixas.csv",
        "walk_forward_folds_csv": pasta_execucao / "score_cliente_walk_forward_folds.csv",
        "walk_forward_predicoes_csv": pasta_execucao / "score_cliente_walk_forward_predicoes.csv",
        "metricas_json": pasta_execucao / "score_cliente_metricas.json",
        "dashboard_spec_json": pasta_execucao / "score_cliente_dashboard_spec.json",
        "payload_metricas_json": pasta_execucao / "score_cliente_payload_metricas.json",
        "resumo_treino_json": pasta_execucao / "score_cliente_resumo_treino.json",
    }


def publicar_dashboard_analitico_no_resumo(
    resumo: Any,
    payload_metricas: dict[str, Any],
    dashboard_spec: dict[str, Any],
    caminhos_execucao: dict[str, Path] | None = None,
) -> None:
    """Publica o dashboard inline e também os caminhos dos artefatos para facilitar descoberta pelo plugin."""

    resumo.metricas_extras["dashboard_analitico_publicado"] = True
    resumo.metricas_extras["tipo_dashboard"] = str(dashboard_spec.get("tipo_dashboard") or "ml_dinamico")
    resumo.metricas_extras["versao_dashboard"] = str(dashboard_spec.get("versao_dashboard") or "1.0")
    resumo.metricas_extras["titulo_dashboard"] = str(dashboard_spec.get("titulo") or "Dashboard analítico")
    resumo.metricas_extras["subtitulo_dashboard"] = str(dashboard_spec.get("subtitulo") or "")
    resumo.metricas_extras["dashboard_spec"] = dashboard_spec
    resumo.metricas_extras["payload_metricas"] = payload_metricas
    resumo.metricas_extras["metricas_principais"] = payload_metricas.get("metricas_principais", {})
    resumo.metricas_extras["artefatos_relacionados"] = payload_metricas.get("artefatos_relacionados", [])

    if caminhos_execucao is not None:
        resumo.metricas_extras["caminho_payload_metricas_json"] = str(caminhos_execucao["payload_metricas_json"])
        resumo.metricas_extras["caminho_dashboard_spec_json"] = str(caminhos_execucao["dashboard_spec_json"])
        resumo.metricas_extras["caminho_metricas_json"] = str(caminhos_execucao["metricas_json"])
        resumo.metricas_extras["pasta_execucao_historica"] = str(caminhos_execucao["pasta_execucao"])

    for nome_atributo, valor in {
        "dashboard_analitico_publicado": True,
        "tipo_dashboard": dashboard_spec.get("tipo_dashboard", "ml_dinamico"),
        "dashboard_spec": dashboard_spec,
        "payload_metricas": payload_metricas,
    }.items():
        try:
            setattr(resumo, nome_atributo, valor)
        except Exception:
            pass


def criar_engine_sql(config: Configuracao):
    """Cria a conexão com SQL Server via HookSqlServer do projeto."""
    hook_sql_server = HookSqlServer(conn_id=config.conn_id_sql)
    return hook_sql_server.obter_engine()


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


def normalizar_valor_para_json(valor: Any) -> Any:
    """Converte valores para tipos serializáveis e legíveis na auditoria."""

    if valor is None:
        return None

    if isinstance(valor, (datetime, date, pd.Timestamp)):
        return str(valor)

    if isinstance(valor, Decimal):
        return str(valor)

    if isinstance(valor, UUID):
        return str(valor)

    if isinstance(valor, Path):
        return str(valor)

    if isinstance(valor, np.integer):
        return int(valor)

    if isinstance(valor, np.floating):
        return float(valor)

    if isinstance(valor, np.bool_):
        return bool(valor)

    if pd.isna(valor):
        return None

    return valor


def obter_amostra_polars(
    df: pl.DataFrame,
    limite: int = 5,
    colunas: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Monta uma amostra amigável de um DataFrame Polars."""
    if df.is_empty():
        return []

    df_amostra = df
    if colunas:
        colunas_existentes = [coluna for coluna in colunas if coluna in df.columns]
        if colunas_existentes:
            df_amostra = df.select(colunas_existentes)

    linhas = df_amostra.head(limite).to_dicts()

    amostra: list[dict[str, Any]] = []
    for linha in linhas:
        amostra.append(
            {chave: normalizar_valor_para_json(valor) for chave, valor in linha.items()}
        )

    return amostra


def obter_amostra_pandas(
    df: pd.DataFrame,
    limite: int = 5,
    colunas: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Monta uma amostra amigável de um DataFrame pandas."""
    if df.empty:
        return []

    df_amostra = df.copy()
    if colunas:
        colunas_existentes = [coluna for coluna in colunas if coluna in df_amostra.columns]
        if colunas_existentes:
            df_amostra = df_amostra[colunas_existentes].copy()

    linhas = df_amostra.head(limite).to_dict(orient="records")

    amostra: list[dict[str, Any]] = []
    for linha in linhas:
        amostra.append(
            {chave: normalizar_valor_para_json(valor) for chave, valor in linha.items()}
        )

    return amostra


def salvar_json(caminho: Path, payload: dict[str, Any]) -> None:
    """Salva JSON em disco."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as arquivo_json:
        json.dump(payload, arquivo_json, ensure_ascii=False, indent=4, default=str)


def carregar_json(caminho: Path) -> dict[str, Any]:
    """Carrega JSON de disco."""
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo JSON não encontrado: {caminho}")

    with open(caminho, "r", encoding="utf-8") as arquivo_json:
        return json.load(arquivo_json)


def salvar_polars_parquet(df: pl.DataFrame, caminho: Path) -> None:
    """Salva DataFrame Polars em parquet."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(caminho)


def carregar_polars_parquet(caminho: Path) -> pl.DataFrame:
    """Carrega DataFrame Polars em parquet."""
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo parquet não encontrado: {caminho}")
    return pl.read_parquet(caminho)


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


def limitar_float_json(valor: Any) -> Any:
    """Normaliza floats para JSON seguro."""
    try:
        numero = float(valor)
    except Exception:
        return valor

    if math.isnan(numero) or math.isinf(numero):
        return None

    return float(numero)


def serie_numerica_limpa(valores: Any) -> list[float]:
    """Converte uma série em lista de floats válidos para JSON."""
    serie = pd.to_numeric(pd.Series(valores), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return [float(valor) for valor in serie.tolist()]


def texto_periodo_mes(valor: Any) -> str:
    """Padroniza representação textual de mês/período."""
    if valor is None:
        return "-"

    if isinstance(valor, pd.Timestamp):
        return valor.strftime("%Y-%m")

    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m")

    if isinstance(valor, date):
        return valor.strftime("%Y-%m")

    return str(valor)


def construir_curva_roc(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, Any]:
    """Gera estrutura da curva ROC para o dashboard."""
    try:
        fpr, tpr, thresholds = roc_curve(y_true, y_prob)
    except Exception:
        return {"x": [], "y": [], "thresholds": [], "linha_base_x": [0.0, 1.0], "linha_base_y": [0.0, 1.0]}

    return {
        "x": [float(valor) for valor in fpr.tolist()],
        "y": [float(valor) for valor in tpr.tolist()],
        "thresholds": [None if math.isinf(valor) else float(valor) for valor in thresholds.tolist()],
        "linha_base_x": [0.0, 1.0],
        "linha_base_y": [0.0, 1.0],
    }


def construir_curva_precision_recall(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, Any]:
    """Gera estrutura da curva Precision-Recall para o dashboard."""
    try:
        precisao, recall, thresholds = precision_recall_curve(y_true, y_prob)
    except Exception:
        return {"x": [], "y": [], "thresholds": [], "linha_base_x": [0.0, 1.0], "linha_base_y": [0.0, 0.0]}

    taxa_base = float(np.mean(y_true)) if len(y_true) else 0.0

    return {
        "x": [float(valor) for valor in recall.tolist()],
        "y": [float(valor) for valor in precisao.tolist()],
        "thresholds": [float(valor) for valor in thresholds.tolist()],
        "linha_base_x": [0.0, 1.0],
        "linha_base_y": [taxa_base, taxa_base],
    }


def construir_curva_ks(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, Any]:
    """Gera dados completos da curva KS."""
    df_aux = pd.DataFrame({"y": y_true, "score": y_prob}).sort_values("score", ascending=False).reset_index(drop=True)

    if df_aux.empty:
        return {
            "x": [],
            "positivos": [],
            "negativos": [],
            "distancia": [],
            "indice_maximo": None,
            "ks_maximo": None,
            "x_ponto_maximo": None,
            "y_positivo_ponto_maximo": None,
            "y_negativo_ponto_maximo": None,
        }

    positivos = int((df_aux["y"] == 1).sum())
    negativos = int((df_aux["y"] == 0).sum())

    if positivos == 0 or negativos == 0:
        return {
            "x": [],
            "positivos": [],
            "negativos": [],
            "distancia": [],
            "indice_maximo": None,
            "ks_maximo": None,
            "x_ponto_maximo": None,
            "y_positivo_ponto_maximo": None,
            "y_negativo_ponto_maximo": None,
        }

    df_aux["cum_positivos"] = (df_aux["y"] == 1).cumsum() / positivos
    df_aux["cum_negativos"] = (df_aux["y"] == 0).cumsum() / negativos
    df_aux["distancia"] = (df_aux["cum_positivos"] - df_aux["cum_negativos"]).abs()
    df_aux["percentil_populacao"] = (np.arange(len(df_aux)) + 1) / len(df_aux)

    indice_maximo = int(df_aux["distancia"].idxmax())
    linha_max = df_aux.iloc[indice_maximo]

    return {
        "x": [float(valor) for valor in df_aux["percentil_populacao"].tolist()],
        "positivos": [float(valor) for valor in df_aux["cum_positivos"].tolist()],
        "negativos": [float(valor) for valor in df_aux["cum_negativos"].tolist()],
        "distancia": [float(valor) for valor in df_aux["distancia"].tolist()],
        "indice_maximo": indice_maximo,
        "ks_maximo": float(linha_max["distancia"]),
        "x_ponto_maximo": float(linha_max["percentil_populacao"]),
        "y_positivo_ponto_maximo": float(linha_max["cum_positivos"]),
        "y_negativo_ponto_maximo": float(linha_max["cum_negativos"]),
    }


def construir_tabela_calibracao(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    quantidade_bins: int = 10,
) -> pd.DataFrame:
    """Constrói tabela de calibração por quantis."""
    df_aux = pd.DataFrame({"y": y_true, "prob": y_prob}).dropna()

    if df_aux.empty:
        return pd.DataFrame(columns=[
            "Bin", "FaixaProbabilidade", "Quantidade", "ProbabilidadeMediaPrevista",
            "TaxaRealObservada", "DiferencaObservadoMenosPrevisto"
        ])

    quantidade_bins_real = max(2, min(quantidade_bins, len(df_aux)))
    ranking = df_aux["prob"].rank(method="first")
    df_aux["Bin"] = pd.qcut(ranking, q=quantidade_bins_real, labels=False, duplicates="drop") + 1

    resumo = (
        df_aux.groupby("Bin", dropna=False)
        .agg(
            Quantidade=("y", "size"),
            ProbabilidadeMediaPrevista=("prob", "mean"),
            TaxaRealObservada=("y", "mean"),
            ProbMin=("prob", "min"),
            ProbMax=("prob", "max"),
        )
        .reset_index()
        .sort_values("Bin")
    )

    resumo["FaixaProbabilidade"] = resumo.apply(
        lambda linha: f"{linha['ProbMin']:.3f} até {linha['ProbMax']:.3f}",
        axis=1,
    )
    resumo["DiferencaObservadoMenosPrevisto"] = (
        resumo["TaxaRealObservada"] - resumo["ProbabilidadeMediaPrevista"]
    )

    return resumo[
        [
            "Bin",
            "FaixaProbabilidade",
            "Quantidade",
            "ProbabilidadeMediaPrevista",
            "TaxaRealObservada",
            "DiferencaObservadoMenosPrevisto",
        ]
    ]


def construir_histograma_probabilidade_por_classe(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    quantidade_bins: int = 20,
) -> dict[str, Any]:
    """Gera histograma por classe usando bins fixos de 0 a 1."""
    bins = np.linspace(0.0, 1.0, quantidade_bins + 1)
    centros = (bins[:-1] + bins[1:]) / 2

    probs_0 = pd.Series(y_prob)[pd.Series(y_true) == 0].astype(float).to_numpy()
    probs_1 = pd.Series(y_prob)[pd.Series(y_true) == 1].astype(float).to_numpy()

    contagem_0, _ = np.histogram(probs_0, bins=bins)
    contagem_1, _ = np.histogram(probs_1, bins=bins)

    return {
        "x": [float(valor) for valor in centros.tolist()],
        "classe_0": [int(valor) for valor in contagem_0.tolist()],
        "classe_1": [int(valor) for valor in contagem_1.tolist()],
        "bin_inicio": [float(valor) for valor in bins[:-1].tolist()],
        "bin_fim": [float(valor) for valor in bins[1:].tolist()],
    }


def construir_tabela_lift_por_decil(y_true: np.ndarray, y_prob: np.ndarray) -> pd.DataFrame:
    """Resume precisão, participação e lift por decil do ranking."""
    df_aux = pd.DataFrame({"y": y_true, "prob": y_prob}).sort_values("prob", ascending=False).reset_index(drop=True)

    if df_aux.empty:
        return pd.DataFrame(columns=[
            "Decil", "Faixa", "Quantidade", "Positivos", "TaxaReal", "Lift", "RecallAcumulado"
        ])

    ranking = np.arange(1, len(df_aux) + 1)
    df_aux["Decil"] = pd.qcut(ranking, q=min(10, len(df_aux)), labels=False, duplicates="drop") + 1

    resumo = (
        df_aux.groupby("Decil", dropna=False)
        .agg(
            Quantidade=("y", "size"),
            Positivos=("y", "sum"),
            TaxaReal=("y", "mean"),
            ProbMedia=("prob", "mean"),
            ProbMin=("prob", "min"),
            ProbMax=("prob", "max"),
        )
        .reset_index()
        .sort_values("Decil")
    )

    taxa_base = float(df_aux["y"].mean()) if len(df_aux) else float("nan")
    resumo["Lift"] = np.where(taxa_base > 0, resumo["TaxaReal"] / taxa_base, np.nan)
    total_positivos = float(df_aux["y"].sum())
    resumo["RecallAcumulado"] = np.where(
        total_positivos > 0,
        resumo["Positivos"].cumsum() / total_positivos,
        np.nan,
    )
    resumo["Faixa"] = resumo.apply(
        lambda linha: f"{linha['ProbMax']:.3f} até {linha['ProbMin']:.3f}",
        axis=1,
    )

    return resumo[
        ["Decil", "Faixa", "Quantidade", "Positivos", "TaxaReal", "Lift", "RecallAcumulado"]
    ]


def construir_resumo_temporal_resultado(
    resultado_historico: pd.DataFrame,
    nome_coluna_mes_ref: str,
    nome_coluna_alvo: str,
) -> pd.DataFrame:
    """Consolida volume, taxa real e probabilidade média por mês."""
    if resultado_historico.empty:
        return pd.DataFrame(columns=["MesRef", "Volume", "TaxaReal", "ProbabilidadeMedia", "ScoreMedio"])

    resumo = (
        resultado_historico.groupby(nome_coluna_mes_ref, dropna=False)
        .agg(
            Volume=(nome_coluna_alvo, "size"),
            TaxaReal=(nome_coluna_alvo, "mean"),
            ProbabilidadeMedia=("ProbabilidadeContratacao", "mean"),
            ScoreMedio=("ScoreCliente", "mean"),
        )
        .reset_index()
        .sort_values(nome_coluna_mes_ref)
    )

    resumo["MesRefTexto"] = resumo[nome_coluna_mes_ref].apply(texto_periodo_mes)
    return resumo


def construir_distribuicao_target(y_true: np.ndarray) -> pd.DataFrame:
    """Resume distribuição do target."""
    serie = pd.Series(y_true)
    resumo = (
        serie.value_counts(dropna=False)
        .rename_axis("Classe")
        .reset_index(name="Quantidade")
        .sort_values("Classe")
    )
    resumo["ClasseLabel"] = resumo["Classe"].map({0: "Não contratou (0)", 1: "Contratou (1)"}).fillna("Indefinido")
    resumo["Participacao"] = resumo["Quantidade"] / resumo["Quantidade"].sum()
    return resumo[["Classe", "ClasseLabel", "Quantidade", "Participacao"]]


def construir_tabela_missing(
    df: pl.DataFrame,
    colunas_analisar: list[str],
) -> pd.DataFrame:
    """Calcula percentual de missing das features."""
    if not colunas_analisar:
        return pd.DataFrame(columns=["Feature", "QuantidadeNulos", "PercentualNulos"])

    total = max(int(df.height), 1)
    registros = []
    for coluna in colunas_analisar:
        quantidade_nulos = int(df.select(pl.col(coluna).is_null().sum()).item())
        percentual_nulos = quantidade_nulos / total
        registros.append(
            {
                "Feature": coluna,
                "QuantidadeNulos": quantidade_nulos,
                "PercentualNulos": percentual_nulos,
            }
        )

    return pd.DataFrame(registros).sort_values(
        ["PercentualNulos", "QuantidadeNulos", "Feature"],
        ascending=[False, False, True],
    )


def obter_explicacoes_metricas_score() -> list[dict[str, str]]:
    """Retorna explicações detalhadas das métricas principais."""
    return [
        {
            "titulo": "AUC ROC",
            "conteudo": (
                "A área sob a Curva ROC mede a capacidade global do modelo separar positivos e negativos ao longo "
                "de todos os thresholds possíveis. Em termos práticos, ela responde à pergunta: se eu pegar um caso "
                "que contratou e um caso que não contratou, qual a chance de o modelo dar score maior para o caso "
                "positivo? Quanto mais perto de 1, melhor a separação; 0,5 significa comportamento próximo ao aleatório. "
                "É útil para visão geral de discriminação, mas pode parecer boa demais em bases desbalanceadas, porque "
                "ela não pune com tanta dureza uma explosão de falsos positivos."
            ),
        },
        {
            "titulo": "AUC PR",
            "conteudo": (
                "A área sob a Curva Precision-Recall resume o equilíbrio entre precisão e recall quando o threshold varia. "
                "Ela é especialmente importante quando o evento positivo é raro, porque foca na capacidade do modelo "
                "encontrar positivos de verdade sem poluir demais a seleção com falsos positivos. No contexto comercial, "
                "isso é crítico quando a empresa quer priorizar equipes, orçamento e esforço em uma parcela pequena da base."
            ),
        },
        {
            "titulo": "Log Loss",
            "conteudo": (
                "O Log Loss mede o quão boas ou ruins são as probabilidades previstas, penalizando fortemente erros "
                "muito confiantes. Se o modelo diz 0,99 para um cliente que não vai contratar, ele sofre uma penalização "
                "bem maior do que sofreria por um palpite moderado. É uma métrica importante porque não olha só a ordem "
                "do ranking; ela avalia a qualidade probabilística. Menor é melhor."
            ),
        },
        {
            "titulo": "Brier Score",
            "conteudo": (
                "O Brier Score mede o erro quadrático médio entre a probabilidade prevista e o resultado real. "
                "Se o modelo prevê 0,70 e o cliente contrata, o erro é pequeno; se prevê 0,70 e o cliente não contrata, "
                "o erro é bem maior. Ele é uma métrica de calibração e confiabilidade: ajuda a entender se as probabilidades "
                "estão perto do comportamento observado. Menor é melhor."
            ),
        },
        {
            "titulo": "KS",
            "conteudo": (
                "A estatística KS mede a maior distância entre as distribuições acumuladas de positivos e negativos ao "
                "longo do score. Em linguagem prática, ela mostra o ponto em que o modelo mais consegue separar bons e maus "
                "casos. Quanto maior o KS, maior a discriminação entre os dois grupos. É muito usada em score porque traduz "
                "bem a força de separação do ranking."
            ),
        },
        {
            "titulo": "Precision @ 0,5 e Recall @ 0,5",
            "conteudo": (
                "Essas métricas transformam a probabilidade em decisão binária usando threshold fixo de 0,5. "
                "Precision diz: entre os casos que o modelo marcou como positivos, quantos realmente contrataram. "
                "Recall diz: entre todos os positivos reais, quantos o modelo conseguiu capturar. Elas são úteis para "
                "entender o comportamento operacional de uma regra objetiva de corte, mas podem ser enganosas se o threshold "
                "não fizer sentido para o negócio."
            ),
        },
        {
            "titulo": "Precision Top 10%, Recall Top 10% e Lift Top 10%",
            "conteudo": (
                "Essas métricas olham o topo do ranking, que costuma ser a parte mais valiosa comercialmente. "
                "Precision Top 10% mostra a taxa real de contratação dentro do grupo mais bem ranqueado. Recall Top 10% "
                "mostra quanto dos positivos totais ficou concentrado nesse topo. Lift Top 10% compara a taxa do topo com a "
                "taxa média da base. Se o lift for 3, por exemplo, significa que o topo converte 3 vezes mais do que a base "
                "inteira. Para priorização comercial, essas três métricas costumam ser mais acionáveis do que métricas globais."
            ),
        },
    ]


def construir_dashboard_spec_score_clientes(
    config: Configuracao,
    metricas_desenvolvimento: dict[str, Any],
    metricas_top_desenvolvimento: dict[str, Any],
    metricas_walk_forward: dict[str, Any],
    metricas_top_walk_forward: dict[str, Any],
    metricas_teste: dict[str, Any],
    metricas_top_teste: dict[str, Any],
    resumo_faixas_teste: pd.DataFrame,
    df_resumo_folds_walk_forward: pd.DataFrame,
    df_predicoes_walk_forward: pd.DataFrame,
    df_importancias: pd.DataFrame,
    resultado_historico: pd.DataFrame,
    y_teste: np.ndarray,
    prob_teste: np.ndarray,
    df_preparado: pl.DataFrame,
    colunas_features: list[str],
) -> dict[str, Any]:
    """Cria a especificação declarativa do dashboard analítico do plugin."""
    y_walk_forward = pd.to_numeric(
        df_predicoes_walk_forward[config.nome_coluna_alvo],
        errors="coerce",
    ).fillna(0).astype(int).to_numpy()
    prob_walk_forward = pd.to_numeric(
        df_predicoes_walk_forward["ProbabilidadeContratacao"],
        errors="coerce",
    ).fillna(0.0).to_numpy()

    curva_roc_oot = construir_curva_roc(y_teste, prob_teste)
    curva_pr_oot = construir_curva_precision_recall(y_teste, prob_teste)
    curva_ks_oot = construir_curva_ks(y_teste, prob_teste)
    calibracao_oot = construir_tabela_calibracao(y_teste, prob_teste, quantidade_bins=10)
    hist_oot = construir_histograma_probabilidade_por_classe(y_teste, prob_teste, quantidade_bins=20)
    lift_decil_oot = construir_tabela_lift_por_decil(y_teste, prob_teste)

    curva_roc_oof = construir_curva_roc(y_walk_forward, prob_walk_forward)
    curva_pr_oof = construir_curva_precision_recall(y_walk_forward, prob_walk_forward)
    curva_ks_oof = construir_curva_ks(y_walk_forward, prob_walk_forward)
    calibracao_oof = construir_tabela_calibracao(y_walk_forward, prob_walk_forward, quantidade_bins=10)
    hist_oof = construir_histograma_probabilidade_por_classe(y_walk_forward, prob_walk_forward, quantidade_bins=20)

    resumo_temporal = construir_resumo_temporal_resultado(
        resultado_historico=resultado_historico,
        nome_coluna_mes_ref=config.nome_coluna_mes_ref,
        nome_coluna_alvo=config.nome_coluna_alvo,
    )

    distribuicao_target = construir_distribuicao_target(obter_y_numpy(df_preparado, config.nome_coluna_alvo))
    tabela_missing = construir_tabela_missing(df_preparado, colunas_features)
    explicacoes_metricas = obter_explicacoes_metricas_score()

    kpis_principais = [
        {
            "titulo": "AUC ROC OOT",
            "valor": limitar_float_json(metricas_teste["auc_roc"]),
            "formato": "decimal_4",
            "descricao_curta": "Separação global entre positivos e negativos no teste final intocado.",
        },
        {
            "titulo": "AUC PR OOT",
            "valor": limitar_float_json(metricas_teste["auc_pr"]),
            "formato": "decimal_4",
            "descricao_curta": "Qualidade do ranking em cenário de evento relativamente raro.",
        },
        {
            "titulo": "Log Loss OOT",
            "valor": limitar_float_json(metricas_teste["log_loss"]),
            "formato": "decimal_4",
            "descricao_curta": "Penaliza probabilidades ruins, principalmente as excessivamente confiantes.",
        },
        {
            "titulo": "Brier Score OOT",
            "valor": limitar_float_json(metricas_teste["brier_score"]),
            "formato": "decimal_4",
            "descricao_curta": "Erro quadrático médio entre probabilidade prevista e resultado real.",
        },
        {
            "titulo": "KS OOT",
            "valor": limitar_float_json(metricas_teste["ks"]),
            "formato": "decimal_4",
            "descricao_curta": "Maior distância entre as distribuições acumuladas de positivos e negativos.",
        },
        {
            "titulo": "Precision @ 0,5 OOT",
            "valor": limitar_float_json(metricas_teste["precision_threshold_0_5"]),
            "formato": "decimal_4",
            "descricao_curta": "Precisão da decisão binária usando corte fixo em 0,5.",
        },
        {
            "titulo": "Recall @ 0,5 OOT",
            "valor": limitar_float_json(metricas_teste["recall_threshold_0_5"]),
            "formato": "decimal_4",
            "descricao_curta": "Cobertura dos positivos reais usando corte fixo em 0,5.",
        },
        {
            "titulo": "Precision Top 10% OOT",
            "valor": limitar_float_json(metricas_top_teste["precision_top_10"]),
            "formato": "decimal_4",
            "descricao_curta": "Taxa real de contratação dentro do decil mais alto do score.",
        },
        {
            "titulo": "Recall Top 10% OOT",
            "valor": limitar_float_json(metricas_top_teste["recall_top_10"]),
            "formato": "decimal_4",
            "descricao_curta": "Quanto dos positivos totais foi capturado no topo do ranking.",
        },
        {
            "titulo": "Lift Top 10% OOT",
            "valor": limitar_float_json(metricas_top_teste["lift_top_10"]),
            "formato": "decimal_4",
            "descricao_curta": "Quanto o topo converte acima da taxa média da base.",
        },
    ]

    tabela_metricas = [
        {
            "Conjunto": "Desenvolvimento",
            "AUC ROC": limitar_float_json(metricas_desenvolvimento["auc_roc"]),
            "AUC PR": limitar_float_json(metricas_desenvolvimento["auc_pr"]),
            "Log Loss": limitar_float_json(metricas_desenvolvimento["log_loss"]),
            "Brier Score": limitar_float_json(metricas_desenvolvimento["brier_score"]),
            "KS": limitar_float_json(metricas_desenvolvimento["ks"]),
            "Precision @ 0,5": limitar_float_json(metricas_desenvolvimento["precision_threshold_0_5"]),
            "Recall @ 0,5": limitar_float_json(metricas_desenvolvimento["recall_threshold_0_5"]),
            "Precision Top 10%": limitar_float_json(metricas_top_desenvolvimento["precision_top_10"]),
            "Recall Top 10%": limitar_float_json(metricas_top_desenvolvimento["recall_top_10"]),
            "Lift Top 10%": limitar_float_json(metricas_top_desenvolvimento["lift_top_10"]),
        },
        {
            "Conjunto": "Walk-forward OOF",
            "AUC ROC": limitar_float_json(metricas_walk_forward["auc_roc"]),
            "AUC PR": limitar_float_json(metricas_walk_forward["auc_pr"]),
            "Log Loss": limitar_float_json(metricas_walk_forward["log_loss"]),
            "Brier Score": limitar_float_json(metricas_walk_forward["brier_score"]),
            "KS": limitar_float_json(metricas_walk_forward["ks"]),
            "Precision @ 0,5": limitar_float_json(metricas_walk_forward["precision_threshold_0_5"]),
            "Recall @ 0,5": limitar_float_json(metricas_walk_forward["recall_threshold_0_5"]),
            "Precision Top 10%": limitar_float_json(metricas_top_walk_forward["precision_top_10"]),
            "Recall Top 10%": limitar_float_json(metricas_top_walk_forward["recall_top_10"]),
            "Lift Top 10%": limitar_float_json(metricas_top_walk_forward["lift_top_10"]),
        },
        {
            "Conjunto": "Teste final OOT",
            "AUC ROC": limitar_float_json(metricas_teste["auc_roc"]),
            "AUC PR": limitar_float_json(metricas_teste["auc_pr"]),
            "Log Loss": limitar_float_json(metricas_teste["log_loss"]),
            "Brier Score": limitar_float_json(metricas_teste["brier_score"]),
            "KS": limitar_float_json(metricas_teste["ks"]),
            "Precision @ 0,5": limitar_float_json(metricas_teste["precision_threshold_0_5"]),
            "Recall @ 0,5": limitar_float_json(metricas_teste["recall_threshold_0_5"]),
            "Precision Top 10%": limitar_float_json(metricas_top_teste["precision_top_10"]),
            "Recall Top 10%": limitar_float_json(metricas_top_teste["recall_top_10"]),
            "Lift Top 10%": limitar_float_json(metricas_top_teste["lift_top_10"]),
        },
    ]

    top_importancias = df_importancias.head(20).copy()
    top_missing = tabela_missing.head(20).copy()

    return {
        "tipo_dashboard": "ml_dinamico",
        "versao_dashboard": "1.0",
        "titulo": "Dashboard Analítico do Modelo de Score de Empresas",
        "subtitulo": (
            "Painel específico do algoritmo de score, com performance clássica, valor de negócio, "
            "calibração, estabilidade temporal e interpretação do modelo."
        ),
        "secoes": [
            {
                "id": "visao_geral",
                "titulo": "1. Performance principal",
                "descricao": (
                    "Comece aqui. Este bloco resume a qualidade do modelo no teste final OOT, que é a visão mais importante "
                    "para avaliar o que aconteceu em um período realmente intocado."
                ),
                "widgets": [
                    {
                        "tipo": "grupo_kpis",
                        "colunas": 5,
                        "itens": kpis_principais,
                    },
                    {
                        "tipo": "tabela",
                        "titulo": "Comparação entre Desenvolvimento, Walk-forward OOF e Teste final OOT",
                        "descricao": (
                            "Esta tabela mostra a leitura completa por conjunto. A comparação entre desenvolvimento, "
                            "walk-forward e teste final ajuda a enxergar sobreajuste, degradação temporal e estabilidade real."
                        ),
                        "colunas": list(tabela_metricas[0].keys()),
                        "linhas": tabela_metricas,
                    },
                ],
            },
            {
                "id": "explicacoes_metricas",
                "titulo": "2. Explicação detalhada das métricas",
                "descricao": (
                    "Cada métrica abaixo foi explicada com foco no que ela mede, por que existe, qual pergunta responde e "
                    "como deve ser interpretada no contexto comercial."
                ),
                "widgets": [
                    {
                        "tipo": "texto_detalhado",
                        "itens": explicacoes_metricas,
                    }
                ],
            },
            {
                "id": "valor_negocio",
                "titulo": "3. Valor de negócio do score",
                "descricao": (
                    "Esta camada responde à pergunta mais importante para operação comercial: o score realmente concentra "
                    "os melhores casos nas faixas e decis mais altos?"
                ),
                "widgets": [
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "bar_vertical",
                        "titulo": "Taxa real de contratação por faixa de score",
                        "descricao": (
                            "Este é o gráfico mais importante para negócio. Ele mostra se faixas mais altas do score "
                            "realmente entregam taxas reais maiores de contratação."
                        ),
                        "dados": {
                            "x": [str(valor) for valor in resumo_faixas_teste["ClassificacaoScore"].tolist()],
                            "y": serie_numerica_limpa(resumo_faixas_teste["TaxaRealContratacao"]),
                            "texto": [f"{valor:.2%}" for valor in pd.to_numeric(resumo_faixas_teste["TaxaRealContratacao"], errors="coerce").fillna(0).tolist()],
                        },
                        "layout": {
                            "xaxis_title": "Faixa de score",
                            "yaxis_title": "Taxa real de contratação",
                            "yaxis_tickformat": ".0%",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "bar_vertical",
                        "titulo": "Lift por decil do score",
                        "descricao": (
                            "Mostra quanto cada decil converte acima ou abaixo da média da base. "
                            "Os decis do topo deveriam concentrar lifts maiores."
                        ),
                        "dados": {
                            "x": [f"Decil {int(valor)}" for valor in lift_decil_oot["Decil"].tolist()],
                            "y": serie_numerica_limpa(lift_decil_oot["Lift"]),
                            "texto": [f"{valor:.2f}x" for valor in pd.to_numeric(lift_decil_oot["Lift"], errors="coerce").fillna(0).tolist()],
                        },
                        "layout": {
                            "xaxis_title": "Decil",
                            "yaxis_title": "Lift",
                        },
                    },
                    {
                        "tipo": "tabela",
                        "titulo": "Tabela de lift por decil",
                        "descricao": "Detalhamento numérico do valor de negócio por bloco do ranking.",
                        "colunas": list(lift_decil_oot.columns),
                        "linhas": lift_decil_oot.to_dict(orient="records"),
                    },
                ],
            },
            {
                "id": "performance_classica",
                "titulo": "4. Performance clássica de separação",
                "descricao": (
                    "Aqui entram as curvas tradicionais de discriminação. Elas mostram se o score separa bem quem contrata "
                    "e quem não contrata."
                ),
                "widgets": [
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "roc_curve",
                        "titulo": "Curva ROC - Teste final OOT",
                        "descricao": "Capacidade de separação em vários thresholds no período intocado.",
                        "dados": curva_roc_oot,
                        "layout": {
                            "xaxis_title": "False Positive Rate",
                            "yaxis_title": "True Positive Rate",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "pr_curve",
                        "titulo": "Curva Precision-Recall - Teste final OOT",
                        "descricao": "Mais sensível ao desempenho sobre positivos quando o evento é menos frequente.",
                        "dados": curva_pr_oot,
                        "layout": {
                            "xaxis_title": "Recall",
                            "yaxis_title": "Precision",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "ks_curve",
                        "titulo": "Curva KS - Teste final OOT",
                        "descricao": "Mostra as distribuições acumuladas de positivos e negativos e a distância máxima entre elas.",
                        "dados": curva_ks_oot,
                        "layout": {
                            "xaxis_title": "População acumulada",
                            "yaxis_title": "Distribuição acumulada",
                            "yaxis_tickformat": ".0%",
                            "xaxis_tickformat": ".0%",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "histograma_duas_series",
                        "titulo": "Histograma de probabilidades por classe - Teste final OOT",
                        "descricao": "Quanto menor a sobreposição entre as distribuições, melhor a separação prática.",
                        "dados": hist_oot,
                        "layout": {
                            "xaxis_title": "Probabilidade prevista",
                            "yaxis_title": "Quantidade de registros",
                        },
                    },
                ],
            },
            {
                "id": "calibracao",
                "titulo": "5. Confiabilidade da probabilidade",
                "descricao": (
                    "Não basta ordenar bem. Este bloco verifica se a probabilidade prevista conversa com a taxa real observada."
                ),
                "widgets": [
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "calibration_curve",
                        "titulo": "Gráfico de calibração - Teste final OOT",
                        "descricao": (
                            "Se os pontos ficarem perto da diagonal, a probabilidade prevista está coerente com o realizado. "
                            "Acima da diagonal o modelo subestima; abaixo da diagonal ele superestima."
                        ),
                        "dados": {
                            "x": serie_numerica_limpa(calibracao_oot["ProbabilidadeMediaPrevista"]),
                            "y": serie_numerica_limpa(calibracao_oot["TaxaRealObservada"]),
                            "linha_base_x": [0.0, 1.0],
                            "linha_base_y": [0.0, 1.0],
                        },
                        "layout": {
                            "xaxis_title": "Probabilidade média prevista",
                            "yaxis_title": "Taxa real observada",
                            "xaxis_tickformat": ".0%",
                            "yaxis_tickformat": ".0%",
                        },
                    },
                    {
                        "tipo": "tabela",
                        "titulo": "Tabela de calibração - Teste final OOT",
                        "descricao": "Detalha cada bin com previsão média, taxa observada e diferença entre as duas.",
                        "colunas": list(calibracao_oot.columns),
                        "linhas": calibracao_oot.to_dict(orient="records"),
                    },
                ],
            },
            {
                "id": "estabilidade",
                "titulo": "6. Estabilidade temporal",
                "descricao": (
                    "Como o pipeline é temporal, é obrigatório enxergar se o desempenho varia demais entre folds e ao longo do tempo."
                ),
                "widgets": [
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "linha_multiplas_series",
                        "titulo": "Métricas por fold do walk-forward",
                        "descricao": "Permite ver robustez ou instabilidade da performance ao longo dos blocos temporais de validação.",
                        "series": [
                            {
                                "nome": "AUC ROC",
                                "x": [int(valor) for valor in df_resumo_folds_walk_forward["Fold"].tolist()],
                                "y": serie_numerica_limpa(df_resumo_folds_walk_forward["AUC_ROC"]),
                            },
                            {
                                "nome": "AUC PR",
                                "x": [int(valor) for valor in df_resumo_folds_walk_forward["Fold"].tolist()],
                                "y": serie_numerica_limpa(df_resumo_folds_walk_forward["AUC_PR"]),
                            },
                            {
                                "nome": "KS",
                                "x": [int(valor) for valor in df_resumo_folds_walk_forward["Fold"].tolist()],
                                "y": serie_numerica_limpa(df_resumo_folds_walk_forward["KS"]),
                            },
                            {
                                "nome": "Lift Top 10%",
                                "x": [int(valor) for valor in df_resumo_folds_walk_forward["Fold"].tolist()],
                                "y": serie_numerica_limpa(df_resumo_folds_walk_forward["LiftTop10"]),
                            },
                        ],
                        "layout": {
                            "xaxis_title": "Fold",
                            "yaxis_title": "Valor",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "linha_duas_series",
                        "titulo": "Volume e taxa real por mês",
                        "descricao": "Ajuda a diagnosticar sazonalidade, meses fracos e mudança de regime.",
                        "dados": {
                            "x": resumo_temporal["MesRefTexto"].tolist(),
                            "serie_1_nome": "Volume",
                            "serie_1_y": [int(valor) for valor in resumo_temporal["Volume"].tolist()],
                            "serie_2_nome": "Taxa real",
                            "serie_2_y": serie_numerica_limpa(resumo_temporal["TaxaReal"]),
                        },
                        "layout": {
                            "xaxis_title": "Mês de referência",
                            "yaxis_title": "Valor",
                            "serie_2_eixo_secundario": True,
                            "yaxis2_title": "Taxa real",
                            "yaxis2_tickformat": ".0%",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "linha_duas_series",
                        "titulo": "Taxa real e probabilidade média por mês",
                        "descricao": "Compara o comportamento observado com o comportamento médio previsto pelo modelo.",
                        "dados": {
                            "x": resumo_temporal["MesRefTexto"].tolist(),
                            "serie_1_nome": "Taxa real",
                            "serie_1_y": serie_numerica_limpa(resumo_temporal["TaxaReal"]),
                            "serie_2_nome": "Probabilidade média",
                            "serie_2_y": serie_numerica_limpa(resumo_temporal["ProbabilidadeMedia"]),
                        },
                        "layout": {
                            "xaxis_title": "Mês de referência",
                            "yaxis_title": "Taxa / probabilidade",
                            "yaxis_tickformat": ".0%",
                        },
                    },
                    {
                        "tipo": "tabela",
                        "titulo": "Resumo dos folds walk-forward",
                        "descricao": "Tabela completa para auditoria das janelas temporais de treino e validação.",
                        "colunas": list(df_resumo_folds_walk_forward.columns),
                        "linhas": df_resumo_folds_walk_forward.to_dict(orient="records"),
                    },
                ],
            },
            {
                "id": "saude_base",
                "titulo": "7. Saúde da base",
                "descricao": (
                    "Antes de culpar o modelo, é preciso entender a base. Esta seção ajuda a detectar desequilíbrio, "
                    "lacunas de informação e meses anormais."
                ),
                "widgets": [
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "bar_vertical",
                        "titulo": "Distribuição do target",
                        "descricao": "Mostra a quantidade de 0 e 1 na base preparada.",
                        "dados": {
                            "x": distribuicao_target["ClasseLabel"].tolist(),
                            "y": [int(valor) for valor in distribuicao_target["Quantidade"].tolist()],
                            "texto": [f"{valor:.2%}" for valor in pd.to_numeric(distribuicao_target["Participacao"], errors="coerce").fillna(0).tolist()],
                        },
                        "layout": {
                            "xaxis_title": "Classe",
                            "yaxis_title": "Quantidade",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "bar_horizontal",
                        "titulo": "Top 20 variáveis com maior percentual de nulos",
                        "descricao": "Ajuda a ver se o modelo pode estar aprendendo padrões de ausência em vez de padrão de negócio.",
                        "dados": {
                            "x": serie_numerica_limpa(top_missing["PercentualNulos"]),
                            "y": top_missing["Feature"].tolist(),
                            "texto": [f"{valor:.2%}" for valor in pd.to_numeric(top_missing["PercentualNulos"], errors="coerce").fillna(0).tolist()],
                        },
                        "layout": {
                            "xaxis_title": "% de nulos",
                            "yaxis_title": "Feature",
                            "xaxis_tickformat": ".0%",
                        },
                    },
                    {
                        "tipo": "tabela",
                        "titulo": "Tabela de missing values",
                        "descricao": "Percentual de nulos por variável do conjunto preparado.",
                        "colunas": list(tabela_missing.columns),
                        "linhas": tabela_missing.head(50).to_dict(orient="records"),
                    },
                ],
            },
            {
                "id": "interpretacao_modelo",
                "titulo": "8. Interpretação do modelo",
                "descricao": (
                    "Importância de feature ajuda a enxergar influência no modelo, não causalidade econômica. "
                    "Use este bloco para levantar hipóteses, não para decretar verdade de negócio."
                ),
                "widgets": [
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "bar_horizontal",
                        "titulo": "Top 20 features mais importantes",
                        "descricao": "Ranking de influência estatística no CatBoost final.",
                        "dados": {
                            "x": serie_numerica_limpa(top_importancias["Importancia"]),
                            "y": top_importancias["Feature"].tolist(),
                            "texto": [f"{valor:.2f}" for valor in pd.to_numeric(top_importancias["Importancia"], errors="coerce").fillna(0).tolist()],
                        },
                        "layout": {
                            "xaxis_title": "Importância",
                            "yaxis_title": "Feature",
                        },
                    },
                    {
                        "tipo": "tabela",
                        "titulo": "Tabela de importâncias",
                        "descricao": "Detalhamento numérico das principais variáveis.",
                        "colunas": list(df_importancias.columns),
                        "linhas": df_importancias.head(50).to_dict(orient="records"),
                    },
                ],
            },
            {
                "id": "comparacao_oof_oot",
                "titulo": "9. Comparação OOF versus OOT",
                "descricao": (
                    "Este bloco ajuda a separar duas perguntas: o modelo parecia bom em validação temporal e "
                    "continuou bom no teste final realmente intocado?"
                ),
                "widgets": [
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "roc_dupla",
                        "titulo": "Curvas ROC - Walk-forward OOF vs Teste final OOT",
                        "descricao": "Comparação direta de discriminação entre validação temporal e teste final.",
                        "dados": {
                            "oof": curva_roc_oof,
                            "oot": curva_roc_oot,
                        },
                        "layout": {
                            "xaxis_title": "False Positive Rate",
                            "yaxis_title": "True Positive Rate",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "pr_dupla",
                        "titulo": "Curvas Precision-Recall - Walk-forward OOF vs Teste final OOT",
                        "descricao": "Comparação direta entre a validação temporal consolidada e o período final intocado.",
                        "dados": {
                            "oof": curva_pr_oof,
                            "oot": curva_pr_oot,
                        },
                        "layout": {
                            "xaxis_title": "Recall",
                            "yaxis_title": "Precision",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "calibration_dupla",
                        "titulo": "Calibração - Walk-forward OOF vs Teste final OOT",
                        "descricao": "Permite ver se a confiabilidade probabilística degrada do OOF para o OOT.",
                        "dados": {
                            "oof_x": serie_numerica_limpa(calibracao_oof["ProbabilidadeMediaPrevista"]),
                            "oof_y": serie_numerica_limpa(calibracao_oof["TaxaRealObservada"]),
                            "oot_x": serie_numerica_limpa(calibracao_oot["ProbabilidadeMediaPrevista"]),
                            "oot_y": serie_numerica_limpa(calibracao_oot["TaxaRealObservada"]),
                            "linha_base_x": [0.0, 1.0],
                            "linha_base_y": [0.0, 1.0],
                        },
                        "layout": {
                            "xaxis_title": "Probabilidade média prevista",
                            "yaxis_title": "Taxa real observada",
                            "xaxis_tickformat": ".0%",
                            "yaxis_tickformat": ".0%",
                        },
                    },
                    {
                        "tipo": "grafico_plotly",
                        "subtipo": "histograma_dupla_comparacao",
                        "titulo": "Histograma por classe - OOF vs OOT",
                        "descricao": "Comparação visual da separação de score entre validação temporal e teste final.",
                        "dados": {
                            "oof": hist_oof,
                            "oot": hist_oot,
                        },
                        "layout": {
                            "xaxis_title": "Probabilidade prevista",
                            "yaxis_title": "Quantidade",
                        },
                    },
                ],
            },
        ],
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
    dashboard_spec: dict[str, Any],
    df_resumo_folds_walk_forward: pd.DataFrame,
    df_predicoes_walk_forward: pd.DataFrame,
    config: Configuracao,
    caminhos_execucao: dict[str, Path],
) -> None:
    """Salva os artefatos finais do pipeline sem alterar a lógica do algoritmo."""

    config.pasta_saida_dados.mkdir(parents=True, exist_ok=True)
    config.pasta_saida_metricas.mkdir(parents=True, exist_ok=True)
    caminhos_execucao["pasta_execucao"].mkdir(parents=True, exist_ok=True)

    if config.salvar_score_historico_completo:
        resultado_historico.to_csv(
            config.caminho_saida_score_historico_csv,
            index=False,
            encoding="utf-8-sig",
        )
        resultado_historico.to_csv(
            caminhos_execucao["score_historico_csv"],
            index=False,
            encoding="utf-8-sig",
        )

    resultado_atual.to_csv(
        config.caminho_saida_score_atual_csv,
        index=False,
        encoding="utf-8-sig",
    )
    resultado_atual.to_csv(
        caminhos_execucao["score_atual_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    tabela_final.to_csv(
        config.caminho_saida_tabela_final_csv,
        index=False,
        encoding="utf-8-sig",
    )
    tabela_final.to_csv(
        caminhos_execucao["tabela_final_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    df_importancias.to_csv(
        config.caminho_saida_importancias_csv,
        index=False,
        encoding="utf-8-sig",
    )
    df_importancias.to_csv(
        caminhos_execucao["importancias_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    resumo_faixas.to_csv(
        config.caminho_saida_resumo_faixas_csv,
        index=False,
        encoding="utf-8-sig",
    )
    resumo_faixas.to_csv(
        caminhos_execucao["resumo_faixas_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    df_resumo_folds_walk_forward.to_csv(
        config.caminho_saida_walk_forward_folds_csv,
        index=False,
        encoding="utf-8-sig",
    )
    df_resumo_folds_walk_forward.to_csv(
        caminhos_execucao["walk_forward_folds_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    df_predicoes_walk_forward.to_csv(
        config.caminho_saida_walk_forward_predicoes_csv,
        index=False,
        encoding="utf-8-sig",
    )
    df_predicoes_walk_forward.to_csv(
        caminhos_execucao["walk_forward_predicoes_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    salvar_json(config.caminho_saida_metricas_json, payload_metricas)
    salvar_json(config.caminho_saida_payload_metricas_json, payload_metricas)
    salvar_json(config.caminho_saida_dashboard_spec_json, dashboard_spec)

    salvar_json(caminhos_execucao["metricas_json"], payload_metricas)
    salvar_json(caminhos_execucao["payload_metricas_json"], payload_metricas)
    salvar_json(caminhos_execucao["dashboard_spec_json"], dashboard_spec)


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


def executar_etapa_extracao_sql() -> dict[str, Any]:
    """Extrai o dataset bruto do SQL Server e grava artefato intermediário."""
    config = Configuracao()
    garantir_pastas_saida(config)

    resumo = criar_resumo_auditoria(
        nome_amigavel="Extrair dataset bruto do SQL Server",
        descricao_etapa=(
            "Executa a query SQL de score de empresas, captura o result set final, "
            "normaliza tipos e grava o dataset bruto em parquet para as próximas etapas."
        ),
        origem_dados=f"SQL Server via conn_id {config.conn_id_sql}",
        destino_dados=str(config.caminho_dataset_bruto_parquet),
    )

    engine_sql = criar_engine_sql(config)

    try:
        resumo.status = "RUNNING"
        resumo.metricas_extras["conn_id_sql"] = config.conn_id_sql
        resumo.metricas_extras["caminho_query_sql"] = str(config.caminho_query_sql)
        publicar_resumo_auditoria(resumo)

        print("=" * 100)
        print("ETAPA 1 - EXTRAÇÃO DO DATASET BRUTO")
        print("=" * 100)

        query_sql = ler_query_sql(config.caminho_query_sql)
        df = carregar_dados_sql_em_polars(engine_sql, query_sql)

        salvar_polars_parquet(df, config.caminho_dataset_bruto_parquet)

        resumo.status = "SUCCESS"
        resumo.linhas_lidas = int(df.height)
        resumo.linhas_inseridas = int(df.height)
        resumo.metricas_extras["quantidade_colunas"] = int(len(df.columns))
        resumo.metricas_extras["colunas"] = list(df.columns)

        adicionar_validacao(
            resumo,
            nome="dataset_bruto_nao_vazio",
            status="ok" if df.height > 0 else "erro",
            detalhe=f"A consulta retornou {df.height:,} linhas e {len(df.columns):,} colunas.",
        )

        definir_amostra(
            resumo,
            obter_amostra_polars(df, limite=config.quantidade_linhas_amostra_auditoria),
            limite=config.quantidade_linhas_amostra_auditoria,
        )
        publicar_resumo_auditoria(resumo)

        print(f"Linhas carregadas: {df.height:,}")
        print(f"Colunas carregadas: {len(df.columns):,}")
        print(f"Arquivo parquet bruto: {config.caminho_dataset_bruto_parquet}")

        return {
            "linhas": int(df.height),
            "colunas": int(len(df.columns)),
            "caminho_dataset_bruto_parquet": str(config.caminho_dataset_bruto_parquet),
        }

    except Exception as erro:
        resumo.status = "FAILED"
        registrar_erro_no_resumo(resumo, erro)
        publicar_resumo_auditoria(resumo)
        raise

    finally:
        engine_sql.dispose()


def executar_etapa_preparacao_modelagem() -> dict[str, Any]:
    """Prepara dataset para modelagem, cria features e salva metadados."""
    config = Configuracao()
    garantir_pastas_saida(config)

    resumo = criar_resumo_auditoria(
        nome_amigavel="Preparar dataset de modelagem",
        descricao_etapa=(
            "Carrega o dataset bruto, converte datas, limpa linhas inviáveis, "
            "cria features temporais, identifica variáveis numéricas e categóricas "
            "e salva o dataset preparado com os metadados de modelagem."
        ),
        origem_dados=str(config.caminho_dataset_bruto_parquet),
        destino_dados=str(config.caminho_dataset_preparado_parquet),
    )

    try:
        resumo.status = "RUNNING"
        publicar_resumo_auditoria(resumo)

        print("=" * 100)
        print("ETAPA 2 - PREPARAÇÃO DO DATASET DE MODELAGEM")
        print("=" * 100)

        df = carregar_polars_parquet(config.caminho_dataset_bruto_parquet)
        linhas_antes = int(df.height)

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

        meses_ordenados = obter_meses_ordenados(df, config)

        salvar_polars_parquet(df, config.caminho_dataset_preparado_parquet)

        metadados_preparacao = {
            "colunas_features": colunas_features,
            "colunas_numericas": colunas_numericas,
            "colunas_categoricas": colunas_categoricas,
            "colunas_excluir": colunas_excluir,
            "meses_ordenados": [str(mes) for mes in meses_ordenados],
            "linhas_antes_limpeza": linhas_antes,
            "linhas_apos_limpeza": int(df.height),
            "quantidade_features": int(len(colunas_features)),
            "quantidade_features_numericas": int(len(colunas_numericas)),
            "quantidade_features_categoricas": int(len(colunas_categoricas)),
        }
        salvar_json(config.caminho_metadados_preparacao_json, metadados_preparacao)

        resumo.status = "SUCCESS"
        resumo.linhas_lidas = linhas_antes
        resumo.linhas_inseridas = int(df.height)
        resumo.linhas_descartadas = int(max(linhas_antes - df.height, 0))
        resumo.metricas_extras["quantidade_features"] = int(len(colunas_features))
        resumo.metricas_extras["quantidade_features_numericas"] = int(len(colunas_numericas))
        resumo.metricas_extras["quantidade_features_categoricas"] = int(len(colunas_categoricas))
        resumo.metricas_extras["mes_inicial"] = str(meses_ordenados[0]) if meses_ordenados else None
        resumo.metricas_extras["mes_final"] = str(meses_ordenados[-1]) if meses_ordenados else None

        adicionar_validacao(
            resumo,
            nome="features_disponiveis",
            status="ok",
            detalhe=(
                f"Foram mantidas {len(colunas_features):,} features, sendo "
                f"{len(colunas_numericas):,} numéricas e {len(colunas_categoricas):,} categóricas."
            ),
        )
        adicionar_validacao(
            resumo,
            nome="meses_suficientes_para_split_temporal",
            status="ok",
            detalhe=f"Foram identificados {len(meses_ordenados):,} meses únicos ordenados para modelagem temporal.",
        )

        colunas_amostra = [
            config.nome_coluna_mes_ref,
            config.nome_coluna_alvo,
            "IDEmpresa",
            "RazaoSocial",
            "ClasseValor",
            "DiasDesdeUltimaCompra",
        ]
        definir_amostra(
            resumo,
            obter_amostra_polars(
                df,
                limite=config.quantidade_linhas_amostra_auditoria,
                colunas=colunas_amostra,
            ),
            limite=config.quantidade_linhas_amostra_auditoria,
        )
        publicar_resumo_auditoria(resumo)

        print(f"Linhas antes da limpeza: {linhas_antes:,}")
        print(f"Linhas após limpeza: {df.height:,}")
        print(f"Features totais: {len(colunas_features):,}")
        print(f"Features numéricas: {len(colunas_numericas):,}")
        print(f"Features categóricas: {len(colunas_categoricas):,}")
        print(f"Dataset preparado salvo em: {config.caminho_dataset_preparado_parquet}")
        print(f"Metadados salvos em: {config.caminho_metadados_preparacao_json}")

        return {
            "linhas_preparadas": int(df.height),
            "quantidade_features": int(len(colunas_features)),
            "quantidade_features_numericas": int(len(colunas_numericas)),
            "quantidade_features_categoricas": int(len(colunas_categoricas)),
            "caminho_dataset_preparado_parquet": str(config.caminho_dataset_preparado_parquet),
            "caminho_metadados_preparacao_json": str(config.caminho_metadados_preparacao_json),
        }

    except Exception as erro:
        resumo.status = "FAILED"
        registrar_erro_no_resumo(resumo, erro)
        publicar_resumo_auditoria(resumo)
        raise


def executar_etapa_treino_validacao_score() -> dict[str, Any]:
    """Executa walk-forward, treina modelo final, gera scores e salva artefatos."""
    config = Configuracao()
    garantir_pastas_saida(config)
    identificador_execucao = obter_identificador_execucao_airflow()
    caminhos_execucao = construir_caminhos_artefatos_execucao(config, identificador_execucao)

    resumo = criar_resumo_auditoria(
        nome_amigavel="Treinar modelo, validar e gerar scores",
        descricao_etapa=(
            "Carrega o dataset preparado, executa walk-forward temporal, escolhe a iteração final, "
            "treina o CatBoost final, gera scores históricos e do snapshot atual, produz métricas, "
            "faixas, importâncias e grava todos os artefatos finais do pipeline."
        ),
        origem_dados=str(config.caminho_dataset_preparado_parquet),
        destino_dados=str(config.caminho_saida_score_atual_csv),
    )

    engine_sql = criar_engine_sql(config)

    try:
        resumo.status = "RUNNING"
        resumo.metricas_extras["identificador_execucao"] = identificador_execucao
        resumo.metricas_extras["pasta_execucao_historica"] = str(caminhos_execucao["pasta_execucao"])
        publicar_resumo_auditoria(resumo)

        print("=" * 100)
        print("ETAPA 3 - TREINO, VALIDAÇÃO E GERAÇÃO DE SCORES")
        print("=" * 100)

        df = carregar_polars_parquet(config.caminho_dataset_preparado_parquet)
        metadados_preparacao = carregar_json(config.caminho_metadados_preparacao_json)

        colunas_features = metadados_preparacao["colunas_features"]
        colunas_numericas = metadados_preparacao["colunas_numericas"]
        colunas_categoricas = metadados_preparacao["colunas_categoricas"]

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

        dashboard_spec = construir_dashboard_spec_score_clientes(
            config=config,
            metricas_desenvolvimento=metricas_desenvolvimento,
            metricas_top_desenvolvimento=metricas_top_desenvolvimento,
            metricas_walk_forward=metricas_walk_forward,
            metricas_top_walk_forward=metricas_top_walk_forward,
            metricas_teste=metricas_teste,
            metricas_top_teste=metricas_top_teste,
            resumo_faixas_teste=resumo_faixas_teste,
            df_resumo_folds_walk_forward=df_resumo_folds_walk_forward,
            df_predicoes_walk_forward=df_predicoes_walk_forward,
            df_importancias=df_importancias,
            resultado_historico=resultado_historico,
            y_teste=y_teste,
            prob_teste=prob_teste,
            df_preparado=df,
            colunas_features=colunas_features,
        )

        metricas_principais = {
            "AUC ROC OOT": limitar_float_json(metricas_teste["auc_roc"]),
            "AUC PR OOT": limitar_float_json(metricas_teste["auc_pr"]),
            "Log Loss OOT": limitar_float_json(metricas_teste["log_loss"]),
            "Brier Score OOT": limitar_float_json(metricas_teste["brier_score"]),
            "KS OOT": limitar_float_json(metricas_teste["ks"]),
            "Precision @ 0,5 OOT": limitar_float_json(metricas_teste["precision_threshold_0_5"]),
            "Recall @ 0,5 OOT": limitar_float_json(metricas_teste["recall_threshold_0_5"]),
            "Precision Top 10% OOT": limitar_float_json(metricas_top_teste["precision_top_10"]),
            "Recall Top 10% OOT": limitar_float_json(metricas_top_teste["recall_top_10"]),
            "Lift Top 10% OOT": limitar_float_json(metricas_top_teste["lift_top_10"]),
        }

        payload_metricas = {
            "nome_modelo": "CatBoostClassifier",
            "familia_modelo": "Classificação binária de score",
            "versao_modelo": "1.0",
            "identificador_execucao": identificador_execucao,
            "variavel_alvo": config.nome_coluna_alvo,
            "metricas_principais": metricas_principais,
            "dashboard_spec": dashboard_spec,
            "artefatos_relacionados": [
                {"nome": "Métricas JSON (execução)", "tipo": "json", "caminho_arquivo": str(caminhos_execucao["metricas_json"])},
                {"nome": "Payload de métricas (execução)", "tipo": "json", "caminho_arquivo": str(caminhos_execucao["payload_metricas_json"])},
                {"nome": "Dashboard Spec JSON (execução)", "tipo": "json", "caminho_arquivo": str(caminhos_execucao["dashboard_spec_json"])},
                {"nome": "Resumo de treino (execução)", "tipo": "json", "caminho_arquivo": str(caminhos_execucao["resumo_treino_json"])},
                {"nome": "Importâncias (execução)", "tipo": "csv", "caminho_arquivo": str(caminhos_execucao["importancias_csv"])},
                {"nome": "Faixas de score (execução)", "tipo": "csv", "caminho_arquivo": str(caminhos_execucao["resumo_faixas_csv"])},
                {"nome": "Folds walk-forward (execução)", "tipo": "csv", "caminho_arquivo": str(caminhos_execucao["walk_forward_folds_csv"])},
                {"nome": "Predições walk-forward (execução)", "tipo": "csv", "caminho_arquivo": str(caminhos_execucao["walk_forward_predicoes_csv"])},
                {"nome": "Score atual (execução)", "tipo": "csv", "caminho_arquivo": str(caminhos_execucao["score_atual_csv"])},
                {"nome": "Métricas JSON (latest)", "tipo": "json", "caminho_arquivo": str(config.caminho_saida_metricas_json)},
                {"nome": "Dashboard Spec JSON (latest)", "tipo": "json", "caminho_arquivo": str(config.caminho_saida_dashboard_spec_json)},
                {"nome": "Importâncias (latest)", "tipo": "csv", "caminho_arquivo": str(config.caminho_saida_importancias_csv)},
                {"nome": "Faixas de score (latest)", "tipo": "csv", "caminho_arquivo": str(config.caminho_saida_resumo_faixas_csv)},
                {"nome": "Folds walk-forward (latest)", "tipo": "csv", "caminho_arquivo": str(config.caminho_saida_walk_forward_folds_csv)},
                {"nome": "Predições walk-forward (latest)", "tipo": "csv", "caminho_arquivo": str(config.caminho_saida_walk_forward_predicoes_csv)},
                {"nome": "Score histórico (latest)", "tipo": "csv", "caminho_arquivo": str(config.caminho_saida_score_historico_csv)},
                {"nome": "Score atual (latest)", "tipo": "csv", "caminho_arquivo": str(config.caminho_saida_score_atual_csv)},
            ],
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
            "atualizacao_dim_classificacao_clientes": None,
        }

        salvar_resultados(
            resultado_historico=resultado_historico,
            resultado_atual=resultado_atual,
            tabela_final=tabela_final,
            df_importancias=df_importancias,
            resumo_faixas=resumo_faixas_teste,
            payload_metricas=payload_metricas,
            dashboard_spec=dashboard_spec,
            df_resumo_folds_walk_forward=df_resumo_folds_walk_forward,
            df_predicoes_walk_forward=df_predicoes_walk_forward,
            config=config,
            caminhos_execucao=caminhos_execucao,
        )

        resumo_treino = {
            "mes_snapshot_mais_recente": str(mes_mais_recente),
            "iteracoes_finais": int(iteracoes_finais),
            "melhores_iteracoes": [int(valor) for valor in melhores_iteracoes],
            "metricas_walk_forward": metricas_walk_forward,
            "metricas_teste_final_oot": metricas_teste,
            "caminho_score_historico": str(config.caminho_saida_score_historico_csv),
            "caminho_score_atual": str(config.caminho_saida_score_atual_csv),
            "caminho_tabela_final": str(config.caminho_saida_tabela_final_csv),
        }
        salvar_json(config.caminho_resumo_treino_json, resumo_treino)
        salvar_json(caminhos_execucao["resumo_treino_json"], resumo_treino)

        resumo.status = "SUCCESS"
        resumo.linhas_lidas = int(df.height)
        resumo.linhas_inseridas = int(len(resultado_historico))
        resumo.linhas_atualizadas = int(len(resultado_atual))
        resumo.metricas_extras["iteracoes_finais"] = int(iteracoes_finais)
        resumo.metricas_extras["quantidade_folds_walk_forward"] = int(len(df_resumo_folds_walk_forward))
        resumo.metricas_extras["auc_walk_forward"] = float(metricas_walk_forward["auc_roc"])
        resumo.metricas_extras["auc_teste_final_oot"] = float(metricas_teste["auc_roc"])
        resumo.metricas_extras["mes_snapshot_mais_recente"] = str(mes_mais_recente)
        publicar_dashboard_analitico_no_resumo(
            resumo=resumo,
            payload_metricas=payload_metricas,
            dashboard_spec=dashboard_spec,
            caminhos_execucao=caminhos_execucao,
        )

        adicionar_validacao(
            resumo,
            nome="walk_forward_executado",
            status="ok",
            detalhe=f"Foram executados {len(df_resumo_folds_walk_forward):,} folds walk-forward.",
        )
        adicionar_validacao(
            resumo,
            nome="snapshot_atual_gerado",
            status="ok",
            detalhe=f"O snapshot atual gerou {len(resultado_atual):,} linhas scored no mês {mes_mais_recente}.",
        )
        adicionar_validacao(
            resumo,
            nome="artefatos_salvos",
            status="ok",
            detalhe="Histórico, snapshot atual, tabela final, métricas, importâncias e outputs walk-forward foram gravados em disco.",
        )
        adicionar_observacao(
            resumo,
            "A amostra exibida nesta etapa corresponde ao snapshot mais recente já scoreado, ordenado pelos maiores scores.",
        )
        adicionar_observacao(
            resumo,
            "Os artefatos analíticos desta execução foram versionados em pasta própria, preservando histórico por run sem sobrescrever o dashboard anterior do plugin.",
        )

        colunas_amostra_score = [
            "IDEmpresa",
            "RazaoSocial",
            "ClasseValor",
            "ClusterGrupoCliente",
            "ScoreCliente",
            "ProbabilidadeContratacao",
            "ClassificacaoScore",
        ]
        definir_amostra(
            resumo,
            obter_amostra_pandas(
                resultado_atual,
                limite=config.quantidade_linhas_amostra_auditoria,
                colunas=colunas_amostra_score,
            ),
            limite=config.quantidade_linhas_amostra_auditoria,
        )
        publicar_resumo_auditoria(resumo)

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

        return {
            "linhas_total": int(df.height),
            "linhas_snapshot_atual": int(len(resultado_atual)),
            "iteracoes_finais": int(iteracoes_finais),
            "mes_snapshot_mais_recente": str(mes_mais_recente),
            "identificador_execucao": identificador_execucao,
            "dashboard_analitico_publicado": True,
            "tipo_dashboard": dashboard_spec.get("tipo_dashboard", "ml_dinamico"),
            "metricas_principais": metricas_principais,
            "dashboard_spec": dashboard_spec,
            "caminho_score_atual": str(config.caminho_saida_score_atual_csv),
            "caminho_score_atual_execucao": str(caminhos_execucao["score_atual_csv"]),
            "caminho_resumo_treino_json": str(config.caminho_resumo_treino_json),
            "caminho_resumo_treino_json_execucao": str(caminhos_execucao["resumo_treino_json"]),
            "caminho_payload_metricas_json": str(config.caminho_saida_payload_metricas_json),
            "caminho_payload_metricas_json_execucao": str(caminhos_execucao["payload_metricas_json"]),
            "caminho_dashboard_spec_json": str(config.caminho_saida_dashboard_spec_json),
            "caminho_dashboard_spec_json_execucao": str(caminhos_execucao["dashboard_spec_json"]),
        }

    except Exception as erro:
        resumo.status = "FAILED"
        registrar_erro_no_resumo(resumo, erro)
        publicar_resumo_auditoria(resumo)
        raise

    finally:
        engine_sql.dispose()


def executar_etapa_atualizacao_dim() -> dict[str, Any]:
    """Atualiza a dimensão final no SQL Server usando o snapshot scoreado mais recente."""
    config = Configuracao()
    garantir_pastas_saida(config)
    identificador_execucao = obter_identificador_execucao_airflow()
    caminhos_execucao = construir_caminhos_artefatos_execucao(config, identificador_execucao)

    resumo = criar_resumo_auditoria(
        nome_amigavel="Atualizar DimClassificacacaoClientes",
        descricao_etapa=(
            "Carrega o snapshot atual scoreado e atualiza os campos ScorePerfilEmpresa "
            "e ClassificacaoPerfilEmpresa na tabela Integracao.Silver.DimClassificacacaoClientes."
        ),
        origem_dados=str(config.caminho_saida_score_atual_csv),
        destino_dados="[Integracao].[Silver].[DimClassificacacaoClientes]",
    )

    engine_sql = criar_engine_sql(config)

    try:
        resumo.status = "RUNNING"
        resumo.metricas_extras["conn_id_sql"] = config.conn_id_sql
        resumo.metricas_extras["identificador_execucao"] = identificador_execucao
        resumo.metricas_extras["pasta_execucao_historica"] = str(caminhos_execucao["pasta_execucao"])
        publicar_resumo_auditoria(resumo)

        print("=" * 100)
        print("ETAPA 4 - ATUALIZAÇÃO DA DIMCLASSIFICACACAOCLIENTES")
        print("=" * 100)

        if not config.caminho_saida_score_atual_csv.exists():
            raise FileNotFoundError(
                f"Snapshot atual não encontrado para atualização da dimensão: {config.caminho_saida_score_atual_csv}"
            )

        resultado_atual = pd.read_csv(config.caminho_saida_score_atual_csv, encoding="utf-8-sig")

        resumo_atualizacao_dim = atualizar_dim_classificacao_clientes_com_score(
            engine_sql=engine_sql,
            resultado_atual=resultado_atual,
        )

        payload_metricas = carregar_json(config.caminho_saida_payload_metricas_json)
        payload_metricas["atualizacao_dim_classificacao_clientes"] = resumo_atualizacao_dim
        payload_metricas["identificador_execucao"] = identificador_execucao
        salvar_json(config.caminho_saida_payload_metricas_json, payload_metricas)
        salvar_json(config.caminho_saida_metricas_json, payload_metricas)
        salvar_json(caminhos_execucao["payload_metricas_json"], payload_metricas)
        salvar_json(caminhos_execucao["metricas_json"], payload_metricas)

        resumo.status = "SUCCESS"
        resumo.linhas_lidas = int(resumo_atualizacao_dim["linhas_resultado_snapshot"])
        resumo.linhas_inseridas = int(resumo_atualizacao_dim["linhas_enviadas_atualizacao"])
        resumo.linhas_atualizadas = int(resumo_atualizacao_dim["linhas_atualizadas"])
        resumo.linhas_descartadas = int(resumo_atualizacao_dim["linhas_nao_encontradas_destino"])

        dashboard_spec_execucao: dict[str, Any] = {}
        if caminhos_execucao["dashboard_spec_json"].exists():
            dashboard_spec_execucao = carregar_json(caminhos_execucao["dashboard_spec_json"])
        elif config.caminho_saida_dashboard_spec_json.exists():
            dashboard_spec_execucao = carregar_json(config.caminho_saida_dashboard_spec_json)

        if dashboard_spec_execucao:
            publicar_dashboard_analitico_no_resumo(
                resumo=resumo,
                payload_metricas=payload_metricas,
                dashboard_spec=dashboard_spec_execucao,
                caminhos_execucao=caminhos_execucao,
            )

        adicionar_validacao(
            resumo,
            nome="snapshot_para_atualizacao_disponivel",
            status="ok",
            detalhe=f"Foram carregadas {resumo_atualizacao_dim['linhas_resultado_snapshot']:,} linhas do snapshot atual.",
        )
        adicionar_validacao(
            resumo,
            nome="atualizacao_dim_executada",
            status="ok",
            detalhe=(
                f"Foram atualizadas {resumo_atualizacao_dim['linhas_atualizadas']:,} linhas na "
                "DimClassificacacaoClientes."
            ),
        )
        adicionar_observacao(
            resumo,
            "A amostra exibida nesta etapa representa exatamente os registros enviados para atualização da dimensão.",
        )

        df_update = preparar_dataframe_atualizacao_perfil_empresa(resultado_atual)

        definir_amostra(
            resumo,
            obter_amostra_pandas(
                df_update,
                limite=config.quantidade_linhas_amostra_auditoria,
                colunas=["IDEmpresa", "ScorePerfilEmpresa", "ClassificacaoPerfilEmpresa"],
            ),
            limite=config.quantidade_linhas_amostra_auditoria,
        )
        publicar_resumo_auditoria(resumo)

        print(f"Linhas no snapshot atual: {resumo_atualizacao_dim['linhas_resultado_snapshot']:,}")
        print(f"Linhas enviadas para atualização: {resumo_atualizacao_dim['linhas_enviadas_atualizacao']:,}")
        print(f"IDEmpresa encontrados no destino: {resumo_atualizacao_dim['linhas_encontradas_destino']:,}")
        print(f"Linhas efetivamente atualizadas: {resumo_atualizacao_dim['linhas_atualizadas']:,}")
        print(f"IDEmpresa não encontrados no destino:{resumo_atualizacao_dim['linhas_nao_encontradas_destino']:,}")

        return {
            **resumo_atualizacao_dim,
            "identificador_execucao": identificador_execucao,
            "caminho_payload_metricas_json_execucao": str(caminhos_execucao["payload_metricas_json"]),
            "caminho_metricas_json_execucao": str(caminhos_execucao["metricas_json"]),
            "caminho_dashboard_spec_json_execucao": str(caminhos_execucao["dashboard_spec_json"]),
        }

    except Exception as erro:
        resumo.status = "FAILED"
        registrar_erro_no_resumo(resumo, erro)
        publicar_resumo_auditoria(resumo)
        raise

    finally:
        engine_sql.dispose()


@dag(
    dag_id="pipeline_score_empresas",
    description="Pipeline Score Empresas com auditoria por etapa",
    schedule="0 10 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 18, 10, 0, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["Euromidia", "MachineLearning", "Comercial", "Empresas"],
    max_active_runs=1,
)
def pipeline_score_empresas():
    """
    ### Pipeline de Score de Empresas com auditoria por etapa

    Etapas:
    1. Extrair dataset bruto do SQL Server
    2. Preparar dataset de modelagem
    3. Treinar, validar e gerar scores
    4. Atualizar a DimClassificacacaoClientes

    Observação:
    - Cada task publica resumo estruturado de auditoria
    - Cada task publica amostra própria, que fica visível no painel
    - Os artefatos pesados são trocados por arquivos intermediários, e não por XCom
    - A conexão com SQL Server usa o HookSqlServer do projeto
    """

    @task(
        task_id="extrair_dataset_bruto_sql",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=2),
    )
    def tarefa_extrair_dataset_bruto_sql() -> dict[str, Any]:
        return executar_etapa_extracao_sql()

    @task(
        task_id="preparar_dataset_modelagem",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=2),
    )
    def tarefa_preparar_dataset_modelagem() -> dict[str, Any]:
        return executar_etapa_preparacao_modelagem()

    @task(
        task_id="treinar_validar_gerar_scores",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=6),
    )
    def tarefa_treinar_validar_gerar_scores() -> dict[str, Any]:
        return executar_etapa_treino_validacao_score()

    @task(
        task_id="atualizar_dim_classificacao_clientes",
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=2),
    )
    def tarefa_atualizar_dim_classificacao_clientes() -> dict[str, Any]:
        return executar_etapa_atualizacao_dim()

    etapa_1 = tarefa_extrair_dataset_bruto_sql()
    etapa_2 = tarefa_preparar_dataset_modelagem()
    etapa_3 = tarefa_treinar_validar_gerar_scores()
    etapa_4 = tarefa_atualizar_dim_classificacao_clientes()

    etapa_1 >> etapa_2 >> etapa_3 >> etapa_4


pipeline_score_empresas_dag = pipeline_score_empresas()