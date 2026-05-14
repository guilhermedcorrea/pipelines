import hashlib
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pendulum
import polars as pl
try:
    from airflow.sdk import dag, task
except ImportError:
    from airflow.decorators import dag, task
from sqlalchemy import text
from sqlalchemy.engine import Engine

try:
    from dags._libs.auditoria_task import (
        adicionar_observacao,
        adicionar_validacao,
        criar_resumo_auditoria,
        definir_amostra,
        publicar_resumo_auditoria,
        registrar_erro_no_resumo,
    )
except Exception:
    class _ResumoAuditoriaFallback:
        """Resumo mínimo para o DAG continuar sendo importado mesmo sem a lib de auditoria."""
        def __init__(
            self,
            nome_amigavel: str,
            descricao_etapa: str,
            origem_dados: str | None = None,
            destino_dados: str | None = None,
        ) -> None:
            self.nome_amigavel = nome_amigavel
            self.descricao_etapa = descricao_etapa
            self.origem_dados = origem_dados
            self.destino_dados = destino_dados
            self.status = "PENDING"
            self.metricas_extras: dict[str, Any] = {}
            self.validacoes: list[dict[str, Any]] = []
            self.observacoes: list[str] = []
            self.amostra: list[dict[str, Any]] = []
            self.linhas_lidas = 0
            self.linhas_inseridas = 0
            self.erro: str | None = None

    def criar_resumo_auditoria(
        nome_amigavel: str,
        descricao_etapa: str,
        origem_dados: str | None = None,
        destino_dados: str | None = None,
    ) -> _ResumoAuditoriaFallback:
        return _ResumoAuditoriaFallback(
            nome_amigavel=nome_amigavel,
            descricao_etapa=descricao_etapa,
            origem_dados=origem_dados,
            destino_dados=destino_dados,
        )

    def adicionar_observacao(resumo: _ResumoAuditoriaFallback, observacao: str) -> None:
        resumo.observacoes.append(observacao)

    def adicionar_validacao(
        resumo: _ResumoAuditoriaFallback,
        nome: str,
        status: str,
        detalhe: str,
    ) -> None:
        resumo.validacoes.append({"nome": nome, "status": status, "detalhe": detalhe})

    def definir_amostra(
        resumo: _ResumoAuditoriaFallback,
        amostra: list[dict[str, Any]],
        limite: int = 10,
    ) -> None:
        resumo.amostra = amostra[:limite]

    def publicar_resumo_auditoria(resumo: _ResumoAuditoriaFallback) -> None:
        logger.info(
            "Auditoria | etapa=%s | status=%s | origem=%s | destino=%s | metricas=%s",
            resumo.nome_amigavel,
            resumo.status,
            resumo.origem_dados,
            resumo.destino_dados,
            resumo.metricas_extras,
        )

    def registrar_erro_no_resumo(resumo: _ResumoAuditoriaFallback, erro: Exception) -> None:
        resumo.erro = repr(erro)
from hooks.BancodeDados.SqlServer import HookSqlServer


logger = logging.getLogger(__name__)


# Nome que deve aparecer no painel do Airflow.
DAG_ID = "pipeline_controle_contratos_euromidia"
FUSO_HORARIO = "America/Sao_Paulo"
CRON_AGENDAMENTO = "0 8,11,15,18 * * *"

CONN_ID_SQL_SERVER = "mssql_integracao"

PASTA_SHAREPOINT_CONTAINER = Path("/opt/airflow/sharepoint_teste")
PASTA_CARGA_CONTAINER = Path("/opt/airflow/Artefatos/CargasSQL/CTR")

# Este é o caminho visto DENTRO dos containers do Airflow.
# No host Linux, por causa do bind mount do docker-compose, o arquivo deve ficar em:
# ./airflow/sharepoint_teste/Copia-Controle de Contratos Euromidia.xlsm
CAMINHO_ARQUIVO_EXCEL = PASTA_SHAREPOINT_CONTAINER / "Copia-Controle de Contratos Euromidia.xlsm"
NOME_ABA_EXCEL = "CTR"

TABELA_STAGE = "dbo.df_fatocontrolecontratos"

mapeamento_colunas = {
    "DATA DO LANÇAMENTO": "DataLancamento",
    "COTA (Exato)": "Cota",
    "PONTO": "CodPonto",
    "CÓDIGO E FACE": "CodFace",
    "CIDADE DA EXIBIÇÃO": "CidadeExibicao",
    "TIPO": "Tipo",
    "ORIGEM ": "Origem",
    "EMPRESA EURO": "EmpresaEuro",
    "CNPJ DA EXIBIDORA (EUROMIDIA)": "CnpjExibibora",
    "TIPO DE DOCUMENTO": "TipoDocumento",
    "NÚMERO DO CONTRATO / AUTORIZAÇÃO": "NumeroContrato",
    "Nº PRÉVIA LOGYCWARE": "NumeroPrevia",
    "RAZÃO SOCIAL / NOME": "RazaoSocial",
    "CNPJ": "CNPJ",
    "CPF": "CPF",
    "MARCA EXIBIDA": "MarcaExibida",
    "VENDEDOR": "Vendedor",
    "SDR (Sales Development Representative)": "SDR",
    "AGÊNCIA": "Agencia",
    "CNPJ AGÊNCIA": "CnpjAgencia",
    "BUREAU": "Bureau",
    "CNPJ BUREAU": "CnpjBureau",
    "INTERMEDIÁRIO": "Intermediario",
    "CNPJ INTERMEDIÁRIO": "CnpjIntermediario",
    "DATA DE ASSINATURA/RENOVAÇÃO (EMISSÃO)": "DataAssinaturaRenovacao",
    "ID. TRIMESTRE": "IDTrimestre",
    "TEMPO DE EXPOSIÇÃO [DIAS]": "TexmpoExposicao",
    "DATA DE INÍCIO PREVISTO": "DataInicioPrevisto",
    "DATA DE TÉRMINO PREVISTO": "DataTerminoPrevisto",
    "INÍCIO/RENOVAÇÃO": "InicioRenovacao",
    "FATURAMENTO BRUTO MENSAL": "FaturamentoBrutoMensal",
    "% PERMUTA": "PercentualPermuta",
    "COTA DE OPORTUNIDADE?": "CotaOportunidade",
    "VALOR PERMUTA": "ValorPermuta",
    "FATURAMENTO LÍQ. (- PERMUTA)": "FaturamentoLiquidoPermuta",
    "Nº DE PARCELAS": "NumeroParcelas",
    "DATA DO 1º VENCIMENTO": "DataInicioVencimento",
    "TOTAL BRUTO DO CONTRATO": "TotalBrutoContrato",
    "TOTAL LÍQUIDO DO CONTRATO (-AG, -BR, -CT ACORDO)": "TotalLiquidoContratoAGBRCTACORDO",
    "TOTAL LÍQUIDO DO CONTRATO (- AG, - BR, -VEND, - GER ,-COOR)": "TotalLiquidoContratoAGBRVENDGERCOOR",
    "% AGÊNCIA": "PercentualAgencia",
    "VALOR DA AGÊNCIA (MENSAL)": "ValorMensalAgencia",
    "% BUREAU": "PercentualBureau",
    "VALOR BUREAU (MENSAL)": "ValorBureauMensal",
    "% CARTA ACORDO": "PercentualCartaAcordo",
    "VALOR CARTA ACORDO (MENSAL)": "ValorCartaAcordoMensal",
    "VALOR OUTRAS COMISSÕES": "ValorOutrasComissoes",
    "FATURAMENTO LÍQUIDO MENSAL": "FaturamentoLiquidoMensal",
    "% COMISSÃO VENDEDOR": "PercentualComissaoVendedor",
    "VALOR VENDEDOR": "ValorVendedor",
    "VALOR VENDEDOR TOTAL": "ValorVendedorTotal",
    "%COMISSÃO COORDENAÇÃO": "PercentualComissaoCoordenacao",
    "VALOR COORDENADOR": "ValorCoordenador",
    "VALOR COORDENADOR TOTAL": "ValorCoordenadorTotal",
    "% COMISSÃO GERÊNCIA": "PercentualComissaoGerencia",
    "VALOR GERÊNCIA": "ValorGerencia",
    "VALOR GERÊNCIA TOTAL": "ValorGerenciaTotal",
    "ATIVO / CANCELAMENTO": "AtivoCancelamento",
    "FATURAMENTO LÍQUIDO FINAL MENSAL": "FaturamentoLiquidoFinalMensal",
    "COMISSÃO GERÊNCIA NORDESTE": "ComissaoGerenciaNordeste",
    "FATURAMENTO": "Faturamento",
    "DATA DE CANCELAMENTO": "DataCancelamento",
}

ORDEM_COLUNAS_SAIDA = list(mapeamento_colunas.values()) + ["OBS"]

schema_overrides = {col: pl.Utf8 for col in mapeamento_colunas.keys()}


def obter_engine_sql_server() -> Engine:
    """Obtém a engine SQL Server via hook centralizado do Airflow."""
    hook_sql = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
    return hook_sql.obter_engine()


def normalizar_valor_auditoria(valor: Any) -> Any:
    """Normaliza valores para exibição no painel de auditoria."""
    if valor is None:
        return None

    if isinstance(valor, (datetime, date)):
        return str(valor)

    try:
        if pd.isna(valor):
            return None
    except Exception:
        pass

    return valor


def df_polars_para_amostra(
    df: pl.DataFrame,
    limite: int = 5,
    colunas: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Converte DataFrame Polars para amostra amigável no painel."""
    if df.is_empty():
        return []

    base = df
    if colunas:
        colunas_existentes = [col for col in colunas if col in df.columns]
        if colunas_existentes:
            base = df.select(colunas_existentes)

    linhas = base.head(limite).to_dicts()

    return [
        {chave: normalizar_valor_auditoria(valor) for chave, valor in linha.items()}
        for linha in linhas
    ]


def consultar_amostra_sql(
    engine: Engine,
    sql: str,
    parametros: dict[str, Any] | None = None,
    limite: int = 5,
) -> list[dict[str, Any]]:
    """Consulta amostra diretamente no SQL Server para auditoria."""
    with engine.begin() as conn:
        resultado = conn.execute(text(sql), parametros or {})
        linhas = resultado.mappings().fetchall()

    amostra = []
    for linha in linhas[:limite]:
        amostra.append(
            {
                chave: normalizar_valor_auditoria(valor)
                for chave, valor in dict(linha).items()
            }
        )
    return amostra


def _somente_existentes(nomes: list[str], existentes: set[str]) -> list[str]:
    return [n for n in nomes if n in existentes]


def _limpar_texto(expr: pl.Expr) -> pl.Expr:
    return (
        expr.cast(pl.Utf8, strict=False)
        .str.replace_all("\u00A0", " ", literal=True)
        .str.replace_all("\u200B", "", literal=True)
        .str.replace_all("\u200C", "", literal=True)
        .str.replace_all("\u200D", "", literal=True)
        .str.replace_all("\ufeff", "", literal=True)
        .str.strip_chars()
    )


def parse_data_br(expr: pl.Expr) -> pl.Expr:
    s = _limpar_texto(expr)

    iso_d = s.str.to_date("%Y-%m-%d", strict=False)
    iso_dt1 = s.str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False).dt.date()
    iso_dt2 = s.str.to_datetime("%Y-%m-%d %H:%M", strict=False).dt.date()
    iso_t1 = s.str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False).dt.date()
    iso_t2 = s.str.to_datetime("%Y-%m-%dT%H:%M", strict=False).dt.date()
    iso_ms1 = s.str.to_datetime("%Y-%m-%d %H:%M:%S%.f", strict=False).dt.date()
    iso_ms2 = s.str.to_datetime("%Y-%m-%dT%H:%M:%S%.f", strict=False).dt.date()

    br_d = s.str.to_date("%d/%m/%Y", strict=False)
    br_dt1 = s.str.to_datetime("%d/%m/%Y %H:%M:%S", strict=False).dt.date()
    br_dt2 = s.str.to_datetime("%d/%m/%Y %H:%M", strict=False).dt.date()
    br_ms = s.str.to_datetime("%d/%m/%Y %H:%M:%S%.f", strict=False).dt.date()

    excel_serial = (
        s.str.replace_all(".", "", literal=True)
        .str.replace_all(",", ".", literal=True)
        .cast(pl.Float64, strict=False)
    )
    dias_int = excel_serial.floor().cast(pl.Int64, strict=False)
    base = pl.lit("1899-12-30").str.to_date("%Y-%m-%d", strict=True).cast(pl.Date)
    d_serial = base + pl.duration(days=dias_int)

    return pl.coalesce(
        [
            br_d,
            br_dt1,
            br_dt2,
            br_ms,
            iso_d,
            iso_dt1,
            iso_dt2,
            iso_t1,
            iso_t2,
            iso_ms1,
            iso_ms2,
            d_serial,
        ]
    )


def parse_float_br(expr: pl.Expr) -> pl.Expr:
    s = _limpar_texto(expr)

    s_norm = (
        pl.when(s.str.contains(",", literal=True))
        .then(
            s.str.replace_all(".", "", literal=True).str.replace_all(",", ".", literal=True)
        )
        .otherwise(s)
    )

    return s_norm.cast(pl.Float64, strict=False)


def parse_int(expr: pl.Expr) -> pl.Expr:
    return _limpar_texto(expr).cast(pl.Int32, strict=False)


def parse_br_money(expr: pl.Expr) -> pl.Expr:
    s = _limpar_texto(expr)

    s = (
        s.str.replace_all('"', "", literal=True)
        .str.replace_all("R$", "", literal=True)
        .str.replace_all(" ", "", literal=True)
        .str.strip_chars()
    )

    s_norm = (
        pl.when(s.str.contains(",", literal=True))
        .then(
            s.str.replace_all(".", "", literal=True).str.replace_all(",", ".", literal=True)
        )
        .otherwise(s)
    )

    return s_norm.cast(pl.Float64, strict=False)


def parse_id_trimestre(expr: pl.Expr) -> pl.Expr:
    s = _limpar_texto(expr)
    return (
        s.str.replace_all(r"\s+", " ")
        .str.replace_all("TRI", "Tri", literal=True)
        .str.replace_all("tri", "Tri", literal=True)
        .cast(pl.Utf8, strict=False)
    )


def parse_cnpj(expr: pl.Expr) -> pl.Expr:
    s = _limpar_texto(expr)
    s = (
        s.str.replace_all(".", "", literal=True)
        .str.replace_all("-", "", literal=True)
        .str.replace_all("/", "", literal=True)
        .str.replace_all(" ", "", literal=True)
    )
    return s.str.replace_all(r"\D+", "").cast(pl.Utf8, strict=False)


def ler_aba_ctr_xlsm_lazy(caminho_arquivo: str) -> pl.LazyFrame:
    caminho = Path(caminho_arquivo)

    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")

    if caminho.suffix.lower() not in {".xlsm", ".xlsx", ".xls"}:
        raise ValueError(
            f"Extensão inesperada ({caminho.suffix}). Esperado Excel (.xlsm/.xlsx/.xls)."
        )

    df = pl.read_excel(
        str(caminho),
        sheet_name=NOME_ABA_EXCEL,
        engine="calamine",
        infer_schema_length=0,
        schema_overrides=schema_overrides,
    )
    return df.lazy()


def _norm_text(v: Any) -> str:
    if v is None:
        return "SEM"
    s = str(v).strip()
    if not s:
        return "SEM"
    s = " ".join(s.split())
    return s.upper()


def _only_digits(v: Any) -> str:
    if v is None:
        return "SEM"
    s = str(v)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits if digits else "SEM"


def _norm_date(v: Any) -> str:
    if v is None:
        return "SEM"
    if isinstance(v, date) and not isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, datetime):
        return v.date().strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s:
        return "SEM"
    return s


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = []
    while n > 0:
        n, r = divmod(n, 36)
        out.append(alphabet[r])
    return "".join(reversed(out))


def _hash_base36_16(assinatura: str) -> str:
    digest = hashlib.sha256(assinatura.encode("utf-8")).digest()
    as_int = int.from_bytes(digest, byteorder="big", signed=False)
    b36 = _to_base36(as_int)
    return b36[:16]


def aplicar_hash_contrato_e_previa(df: pl.DataFrame) -> pl.DataFrame:
    colunas_necessarias = [
        "DataLancamento",
        "Origem",
        "CNPJ",
        "MarcaExibida",
        "DataAssinaturaRenovacao",
        "DataTerminoPrevisto",
        "NumeroContrato",
        "NumeroPrevia",
    ]
    faltantes = [c for c in colunas_necessarias if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltam colunas no df para aplicar hash: {faltantes}")

    df_out = (
        df.with_columns(
            [
                pl.struct(
                    [
                        "DataLancamento",
                        "Origem",
                        "CNPJ",
                        "MarcaExibida",
                        "DataAssinaturaRenovacao",
                        "DataTerminoPrevisto",
                    ]
                )
                .map_elements(
                    lambda r: (
                        _norm_date(r["DataLancamento"])
                        + "|"
                        + _norm_text(r["Origem"])
                        + "|"
                        + _only_digits(r["CNPJ"])
                        + "|"
                        + _norm_text(r["MarcaExibida"])
                        + "|"
                        + _norm_date(r["DataAssinaturaRenovacao"])
                        + "|"
                        + _norm_date(r["DataTerminoPrevisto"])
                    ),
                    return_dtype=pl.Utf8,
                )
                .alias("__assinatura_contrato"),
                pl.struct(
                    [
                        "DataLancamento",
                        "CNPJ",
                        "MarcaExibida",
                        "DataAssinaturaRenovacao",
                        "DataTerminoPrevisto",
                    ]
                )
                .map_elements(
                    lambda r: (
                        _norm_date(r["DataLancamento"])
                        + "|"
                        + _only_digits(r["CNPJ"])
                        + "|"
                        + _norm_text(r["MarcaExibida"])
                        + "|"
                        + _norm_date(r["DataAssinaturaRenovacao"])
                        + "|"
                        + _norm_date(r["DataTerminoPrevisto"])
                    ),
                    return_dtype=pl.Utf8,
                )
                .alias("__assinatura_previa"),
            ]
        )
        .with_columns(
            [
                pl.col("__assinatura_contrato")
                .map_elements(lambda s: "HASHC-" + _hash_base36_16(s), return_dtype=pl.Utf8)
                .alias("__hash_contrato"),
                pl.col("__assinatura_previa")
                .map_elements(lambda s: "HASHP-" + _hash_base36_16(s), return_dtype=pl.Utf8)
                .alias("__hash_previa"),
            ]
        )
        .with_columns(
            [
                pl.when(
                    pl.col("NumeroContrato").is_null()
                    | (pl.col("NumeroContrato").cast(pl.Utf8, strict=False).str.strip_chars() == "")
                )
                .then(pl.col("__hash_contrato"))
                .otherwise(pl.col("NumeroContrato"))
                .alias("NumeroContrato"),
                pl.when(
                    pl.col("NumeroPrevia").is_null()
                    | (pl.col("NumeroPrevia").cast(pl.Utf8, strict=False).str.strip_chars() == "")
                )
                .then(pl.col("__hash_previa"))
                .otherwise(pl.col("NumeroPrevia"))
                .alias("NumeroPrevia"),
            ]
        )
    )

    return df_out.drop(
        [
            "__assinatura_contrato",
            "__assinatura_previa",
            "__hash_contrato",
            "__hash_previa",
        ]
    )


def garantir_colunas_saida(df: pl.DataFrame) -> pl.DataFrame:
    colunas_faltantes = [c for c in ORDEM_COLUNAS_SAIDA if c not in df.columns]
    if colunas_faltantes:
        df = df.with_columns([pl.lit(None, dtype=pl.Utf8).alias(c) for c in colunas_faltantes])
    return df.select(ORDEM_COLUNAS_SAIDA)


def executar_sql(nome_etapa: str, sql: str) -> None:
    engine = obter_engine_sql_server()
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql)
        logger.info("%s executado com sucesso.", nome_etapa)
    finally:
        engine.dispose()


def garantir_pasta_escrita(pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)

    arquivo_teste = pasta / "_teste_escrita_airflow.tmp"
    with open(arquivo_teste, "w", encoding="utf-8") as arquivo:
        arquivo.write("ok")

    arquivo_teste.unlink(missing_ok=True)


def separar_schema_tabela(nome_completo: str) -> tuple[str, str]:
    partes = nome_completo.split(".", maxsplit=1)
    if len(partes) != 2:
        raise ValueError(
            f"TABELA_STAGE deve estar no formato schema.tabela. Valor recebido: {nome_completo}"
        )
    return partes[0], partes[1]


def carregar_dataframe_stage_sql_server(df: pl.DataFrame, tabela_stage: str) -> None:
    schema_sql, nome_tabela = separar_schema_tabela(tabela_stage)

    df_stage = df.with_columns(
        [pl.col(col).cast(pl.Utf8, strict=False).alias(col) for col in df.columns]
    )

    df_pandas = df_stage.to_pandas()
    df_pandas = df_pandas.where(pd.notnull(df_pandas), None)

    engine = obter_engine_sql_server()

    try:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {tabela_stage}"))

        df_pandas.to_sql(
            name=nome_tabela,
            con=engine,
            schema=schema_sql,
            if_exists="append",
            index=False,
            chunksize=2000,
            method=None,
        )

        logger.info(
            "Stage %s carregada com sucesso via INSERT em lote. Linhas: %s | Colunas: %s",
            tabela_stage,
            len(df_pandas),
            len(df_pandas.columns),
        )
    finally:
        engine.dispose()


MERGE_CONTRATOS_SQL = r"""
;WITH Base AS (
    SELECT
          TRY_CONVERT(date, [DataLancamento]) AS [DataLancamento]
        , [Cota] AS [Cota]
        , TRY_CONVERT(int, [CodPonto]) AS [CodPonto]
        , LEFT(TRY_CONVERT(varchar(200), [CodFace]), 20) AS [CodFace]
        , LEFT(TRY_CONVERT(nvarchar(200), [CidadeExibicao]), 100) AS [CidadeExibicao]
        , LEFT(TRY_CONVERT(nvarchar(200), [Tipo]), 70) AS [Tipo]
        , LEFT(TRY_CONVERT(nvarchar(50), [Origem]), 10) AS [Origem]
        , LEFT(TRY_CONVERT(nvarchar(200), [EmpresaEuro]), 100) AS [EmpresaEuro]
        , LEFT(TRY_CONVERT(char(50), [CnpjExibibora]), 20) AS [CnpjExibibora]
        , LEFT(TRY_CONVERT(nvarchar(200), [TipoDocumento]), 70) AS [TipoDocumento]
        , LEFT(TRY_CONVERT(varchar(300), [NumeroContrato]), 150) AS [NumeroContrato]
        , LEFT(TRY_CONVERT(varchar(300), [NumeroPrevia]), 150) AS [NumeroPrevia]
        , LEFT(TRY_CONVERT(nvarchar(400), [RazaoSocial]), 200) AS [RazaoSocial]
        , LEFT(TRY_CONVERT(char(50), [CNPJ]), 20) AS [CNPJ]
        , LEFT(TRY_CONVERT(char(50), [CPF]), 20) AS [CPF]
        , LEFT(TRY_CONVERT(nvarchar(200), [MarcaExibida]), 100) AS [MarcaExibida]
        , LEFT(TRY_CONVERT(nvarchar(200), [Vendedor]), 100) AS [Vendedor]
        , LEFT(TRY_CONVERT(char(50),[SDR]), 20) AS [SDR]
        , LEFT(TRY_CONVERT(nvarchar(200), [Agencia]), 100) AS [Agencia]
        , LEFT(TRY_CONVERT(char(50), [CnpjAgencia]), 20) AS [CnpjAgencia]
        , LEFT(TRY_CONVERT(nvarchar(200), [Bureau]), 100) AS [Bureau]
        , LEFT(TRY_CONVERT(char(50), [CnpjBureau]), 20) AS [CnpjBureau]
        , LEFT(TRY_CONVERT(nvarchar(200), [Intermediario]), 100) AS [Intermediario]
        , LEFT(TRY_CONVERT(char(50), [CnpjIntermediario]), 20) AS [CnpjIntermediario]
        , TRY_CONVERT(date, [DataAssinaturaRenovacao]) AS [DataAssinaturaRenovacao]
        , LEFT(TRY_CONVERT(varchar(200), [IDTrimestre]), 20) AS [IDTrimestre]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoBrutoMensal],'.',''),',','.')) AS [FaturamentoBrutoMensal]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualPermuta],'.',''),',','.')) AS [PercentualPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([CotaOportunidade],'.',''),',','.')) AS [CotaOportunidade]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorPermuta],'.',''),',','.')) AS [ValorPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoPermuta],'.',''),',','.')) AS [FaturamentoLiquidoPermuta]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalBrutoContrato],'.',''),',','.')) AS [TotalBrutoContrato]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRCTACORDO],'.',''),',','.')) AS [TotalLiquidoContratoAGBRCTACORDO]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRVENDGERCOOR],'.',''),',','.')) AS [TotalLiquidoContratoAGBRVENDGERCOOR]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualAgencia],'.',''),',','.')) AS [PercentualAgencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorMensalAgencia],'.',''),',','.')) AS [ValorMensalAgencia]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualBureau],'.',''),',','.')) AS [PercentualBureau]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorBureauMensal],'.',''),',','.')) AS [ValorBureauMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualCartaAcordo],'.',''),',','.')) AS [PercentualCartaAcordo]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCartaAcordoMensal],'.',''),',','.')) AS [ValorCartaAcordoMensal]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorOutrasComissoes],'.',''),',','.')) AS [ValorOutrasComissoes]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoMensal],'.',''),',','.')) AS [FaturamentoLiquidoMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoVendedor],'.',''),',','.')) AS [PercentualComissaoVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedor],'.',''),',','.')) AS [ValorVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedorTotal],'.',''),',','.')) AS [ValorVendedorTotal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoCoordenacao],'.',''),',','.')) AS [PercentualComissaoCoordenacao]
    FROM [Integracao].[dbo].[df_fatocontrolecontratos]
),
BaseComRef AS (
    SELECT
          b.*
        , CONVERT(varchar(64),
            HASHBYTES('SHA2_256',
                CONCAT(
                    UPPER(LTRIM(RTRIM(COALESCE(b.NumeroContrato,'')))), '|',
                    UPPER(LTRIM(RTRIM(COALESCE(b.NumeroPrevia,'')))),  '|',
                    REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(b.CNPJ,''),'.',''),'-',''),'/',''),' ','')
                )
            ), 2
          ) AS Referencia
    FROM Base AS b
),
Agg AS (
    SELECT
          Referencia

        , MAX(NumeroContrato) AS NumeroContrato
        , MAX(NumeroPrevia)   AS NumeroPrevia
        , MAX(CNPJ)           AS CNPJ

        , MAX(DataAssinaturaRenovacao) AS DataAssinaturaRenovacao
        , MAX(IDTrimestre) AS IDTrimestre
        , MAX(DataLancamento) AS DataLancamento
        , MAX(RazaoSocial) AS RazaoSocial
        , MAX(CPF) AS CPF
        , MAX(MarcaExibida) AS MarcaExibida
        , MAX(Vendedor) AS Vendedor
        , MAX(TipoDocumento) AS TipoDocumento
        , MAX(Origem) AS Origem
        , MAX(SDR)  AS SDR
        , MAX(Agencia) AS Agencia
        , MAX(CnpjAgencia) AS CnpjAgencia
        , MAX(Bureau)  AS Bureau
        , MAX(CnpjBureau)  AS CnpjBureau
        , MAX(Intermediario)  AS Intermediario
        , MAX(CnpjIntermediario)  AS CnpjIntermediario

        , COUNT(DISTINCT CodPonto) AS QuantidadePontos
        , COUNT(DISTINCT CodFace)  AS QuantidadeFaces

        , SUM(COALESCE(FaturamentoBrutoMensal, 0)) AS TotalFaturamentoBrutoMensal
        , SUM(COALESCE(PercentualPermuta, 0)) AS TotalPercentualPermuta
        , SUM(COALESCE(CotaOportunidade, 0)) AS TotalCotaOportunidade
        , SUM(COALESCE(ValorPermuta, 0)) AS TotalValorPermuta
        , SUM(COALESCE(FaturamentoLiquidoPermuta, 0)) AS TotalFaturamentoLiquidoPermuta

        , SUM(COALESCE(TotalBrutoContrato, 0)) AS TotalBrutoContrato
        , SUM(COALESCE(TotalLiquidoContratoAGBRCTACORDO, 0))   AS TotalLiquidoContratoAGBRCTACORDO
        , SUM(COALESCE(TotalLiquidoContratoAGBRVENDGERCOOR, 0)) AS TotalLiquidoContratoAGBRVENDGERCOOR

        , SUM(COALESCE(PercentualAgencia, 0)) AS TotalPercentualAgencia
        , SUM(COALESCE(ValorMensalAgencia, 0)) AS TotalValorMensalAgencia

        , SUM(COALESCE(PercentualBureau, 0)) AS TotalPercentualBureau
        , SUM(COALESCE(ValorBureauMensal, 0))  AS TotalValorBureauMensal

        , SUM(COALESCE(PercentualCartaAcordo, 0))  AS TotalPercentualCartaAcordo
        , SUM(COALESCE(ValorCartaAcordoMensal, 0)) AS TotalValorCartaAcordoMensal

        , SUM(COALESCE(ValorOutrasComissoes, 0)) AS TotalValorOutrasComissoes
        , SUM(COALESCE(FaturamentoLiquidoMensal, 0)) AS TotalFaturamentoLiquidoMensal

        , SUM(COALESCE(PercentualComissaoVendedor, 0)) AS TotalPercentualComissaoVendedor
        , SUM(COALESCE(ValorVendedor, 0)) AS TotalValorVendedor
        , SUM(COALESCE(ValorVendedorTotal, 0)) AS ValorVendedorTotal

        , SUM(COALESCE(PercentualComissaoCoordenacao, 0)) AS TotalPercentualComissaoCoordenacao
    FROM BaseComRef
    GROUP BY
          Referencia
),
Src AS (
    SELECT
          Referencia
        , NumeroContrato
        , NumeroPrevia
        , CNPJ
        , DataAssinaturaRenovacao
        , IDTrimestre
        , DataLancamento
        , RazaoSocial
        , CPF
        , MarcaExibida
        , Vendedor
        , TipoDocumento
        , Origem
        , SDR
        , Agencia
        , CnpjAgencia
        , Bureau
        , CnpjBureau
        , Intermediario
        , CnpjIntermediario
        , QuantidadePontos
        , QuantidadeFaces
        , TotalFaturamentoBrutoMensal
        , TotalPercentualPermuta
        , TotalCotaOportunidade
        , TotalValorPermuta
        , TotalFaturamentoLiquidoPermuta
        , TotalBrutoContrato
        , TotalLiquidoContratoAGBRCTACORDO
        , TotalLiquidoContratoAGBRVENDGERCOOR
        , TotalPercentualAgencia
        , TotalValorMensalAgencia
        , TotalPercentualBureau
        , TotalValorBureauMensal
        , TotalPercentualCartaAcordo
        , TotalValorCartaAcordoMensal
        , TotalValorOutrasComissoes
        , TotalFaturamentoLiquidoMensal
        , TotalPercentualComissaoVendedor
        , TotalValorVendedor
        , ValorVendedorTotal
        , TotalPercentualComissaoCoordenacao
    FROM Agg
)
MERGE [Silver].[FatoControleContratosEuromidia] AS T
USING Src AS S
    ON T.[Referencia] = S.[Referencia]
WHEN MATCHED AND (
       ISNULL(T.NumeroContrato,'') <> ISNULL(S.NumeroContrato,'')
    OR ISNULL(T.NumeroPrevia,'')   <> ISNULL(S.NumeroPrevia,'')
    OR ISNULL(T.CNPJ,'')           <> ISNULL(S.CNPJ,'')
    OR ISNULL(T.DataAssinaturaRenovacao,'19000101') <> ISNULL(S.DataAssinaturaRenovacao,'19000101')
    OR ISNULL(T.IDTrimestre,'')     <> ISNULL(S.IDTrimestre,'')
    OR ISNULL(T.DataLancamento,'19000101') <> ISNULL(S.DataLancamento,'19000101')
    OR ISNULL(T.RazaoSocial,'')   <> ISNULL(S.RazaoSocial,'')
    OR ISNULL(T.CPF,'')  <> ISNULL(S.CPF,'')
    OR ISNULL(T.MarcaExibida,'')  <> ISNULL(S.MarcaExibida,'')
    OR ISNULL(T.Vendedor,'') <> ISNULL(S.Vendedor,'')
    OR ISNULL(T.TipoDocumento,'')  <> ISNULL(S.TipoDocumento,'')
    OR ISNULL(T.Origem,'')  <> ISNULL(S.Origem,'')
    OR ISNULL(T.SDR,'')  <> ISNULL(S.SDR,'')
    OR ISNULL(T.Agencia,'')  <> ISNULL(S.Agencia,'')
    OR ISNULL(T.CnpjAgencia,'')   <> ISNULL(S.CnpjAgencia,'')
    OR ISNULL(T.Bureau,'') <> ISNULL(S.Bureau,'')
    OR ISNULL(T.CnpjBureau,'')   <> ISNULL(S.CnpjBureau,'')
    OR ISNULL(T.Intermediario,'')   <> ISNULL(S.Intermediario,'')
    OR ISNULL(T.CnpjIntermediario,'') <> ISNULL(S.CnpjIntermediario,'')
    OR ISNULL(T.QuantidadePontos,-1) <> ISNULL(S.QuantidadePontos,-1)
    OR ISNULL(T.QuantidadeFaces,-1)  <> ISNULL(S.QuantidadeFaces,-1)
    OR ISNULL(T.TotalFaturamentoBrutoMensal,0) <> ISNULL(S.TotalFaturamentoBrutoMensal,0)
    OR ISNULL(T.TotalPercentualPermuta,0) <> ISNULL(S.TotalPercentualPermuta,0)
    OR ISNULL(T.TotalCotaOportunidade,0)  <> ISNULL(S.TotalCotaOportunidade,0)
    OR ISNULL(T.TotalValorPermuta,0)  <> ISNULL(S.TotalValorPermuta,0)
    OR ISNULL(T.TotalFaturamentoLiquidoPermuta,0) <> ISNULL(S.TotalFaturamentoLiquidoPermuta,0)
    OR ISNULL(T.TotalBrutoContrato,0) <> ISNULL(S.TotalBrutoContrato,0)
    OR ISNULL(T.TotalLiquidoContratoAGBRCTACORDO,0) <> ISNULL(S.TotalLiquidoContratoAGBRCTACORDO,0)
    OR ISNULL(T.TotalLiquidoContratoAGBRVENDGERCOOR,0) <> ISNULL(S.TotalLiquidoContratoAGBRVENDGERCOOR,0)
    OR ISNULL(T.TotalPercentualAgencia,0)  <> ISNULL(S.TotalPercentualAgencia,0)
    OR ISNULL(T.TotalValorMensalAgencia,0) <> ISNULL(S.TotalValorMensalAgencia,0)
    OR ISNULL(T.TotalPercentualBureau,0) <> ISNULL(S.TotalPercentualBureau,0)
    OR ISNULL(T.TotalValorBureauMensal,0)   <> ISNULL(S.TotalValorBureauMensal,0)
    OR ISNULL(T.TotalPercentualCartaAcordo,0)  <> ISNULL(S.TotalPercentualCartaAcordo,0)
    OR ISNULL(T.TotalValorCartaAcordoMensal,0) <> ISNULL(S.TotalValorCartaAcordoMensal,0)
    OR ISNULL(T.TotalValorOutrasComissoes,0)   <> ISNULL(S.TotalValorOutrasComissoes,0)
    OR ISNULL(T.TotalFaturamentoLiquidoMensal,0) <> ISNULL(S.TotalFaturamentoLiquidoMensal,0)
    OR ISNULL(T.TotalPercentualComissaoVendedor,0) <> ISNULL(S.TotalPercentualComissaoVendedor,0)
    OR ISNULL(T.TotalValorVendedor,0)  <> ISNULL(S.TotalValorVendedor,0)
    OR ISNULL(T.ValorVendedorTotal,0)   <> ISNULL(S.ValorVendedorTotal,0)
    OR ISNULL(T.TotalPercentualComissaoCoordenacao,0) <> ISNULL(S.TotalPercentualComissaoCoordenacao,0)
)
THEN UPDATE SET
      T.[DataAtualizacao] = GETDATE()
    , T.[NumeroContrato] = S.[NumeroContrato]
    , T.[NumeroPrevia]   = S.[NumeroPrevia]
    , T.[CNPJ]  = S.[CNPJ]
    , T.[DataAssinaturaRenovacao] = S.[DataAssinaturaRenovacao]
    , T.[IDTrimestre]  = S.[IDTrimestre]
    , T.[DataLancamento] = S.[DataLancamento]
    , T.[RazaoSocial]  = S.[RazaoSocial]
    , T.[CPF]  = S.[CPF]
    , T.[MarcaExibida]   = S.[MarcaExibida]
    , T.[Vendedor] = S.[Vendedor]
    , T.[TipoDocumento]  = S.[TipoDocumento]
    , T.[Origem] = S.[Origem]
    , T.[SDR]  = S.[SDR]
    , T.[Agencia]     = S.[Agencia]
    , T.[CnpjAgencia]  = S.[CnpjAgencia]
    , T.[Bureau]  = S.[Bureau]
    , T.[CnpjBureau]     = S.[CnpjBureau]
    , T.[Intermediario]  = S.[Intermediario]
    , T.[CnpjIntermediario] = S.[CnpjIntermediario]
    , T.[QuantidadePontos] = S.[QuantidadePontos]
    , T.[QuantidadeFaces]  = S.[QuantidadeFaces]
    , T.[TotalFaturamentoBrutoMensal] = S.[TotalFaturamentoBrutoMensal]
    , T.[TotalPercentualPermuta] = S.[TotalPercentualPermuta]
    , T.[TotalCotaOportunidade]  = S.[TotalCotaOportunidade]
    , T.[TotalValorPermuta]   = S.[TotalValorPermuta]
    , T.[TotalFaturamentoLiquidoPermuta] = S.[TotalFaturamentoLiquidoPermuta]
    , T.[TotalBrutoContrato]  = S.[TotalBrutoContrato]
    , T.[TotalLiquidoContratoAGBRCTACORDO] = S.[TotalLiquidoContratoAGBRCTACORDO]
    , T.[TotalLiquidoContratoAGBRVENDGERCOOR] = S.[TotalLiquidoContratoAGBRVENDGERCOOR]
    , T.[TotalPercentualAgencia] = S.[TotalPercentualAgencia]
    , T.[TotalValorMensalAgencia] = S.[TotalValorMensalAgencia]
    , T.[TotalPercentualBureau] = S.[TotalPercentualBureau]
    , T.[TotalValorBureauMensal]  = S.[TotalValorBureauMensal]
    , T.[TotalPercentualCartaAcordo]  = S.[TotalPercentualCartaAcordo]
    , T.[TotalValorCartaAcordoMensal] = S.[TotalValorCartaAcordoMensal]
    , T.[TotalValorOutrasComissoes]   = S.[TotalValorOutrasComissoes]
    , T.[TotalFaturamentoLiquidoMensal] = S.[TotalFaturamentoLiquidoMensal]
    , T.[TotalPercentualComissaoVendedor] = S.[TotalPercentualComissaoVendedor]
    , T.[TotalValorVendedor]  = S.[TotalValorVendedor]
    , T.[ValorVendedorTotal]  = S.[ValorVendedorTotal]
    , T.[TotalPercentualComissaoCoordenacao] = S.[TotalPercentualComissaoCoordenacao]
WHEN NOT MATCHED BY TARGET
THEN INSERT (
      [Referencia]
    , [NumeroContrato]
    , [NumeroPrevia]
    , [CNPJ]
    , [DataAssinaturaRenovacao]
    , [IDTrimestre]
    , [DataLancamento]
    , [RazaoSocial]
    , [CPF]
    , [MarcaExibida]
    , [Vendedor]
    , [TipoDocumento]
    , [Origem]
    , [SDR]
    , [Agencia]
    , [CnpjAgencia]
    , [Bureau]
    , [CnpjBureau]
    , [Intermediario]
    , [CnpjIntermediario]
    , [QuantidadePontos]
    , [QuantidadeFaces]
    , [TotalFaturamentoBrutoMensal]
    , [TotalPercentualPermuta]
    , [TotalCotaOportunidade]
    , [TotalValorPermuta]
    , [TotalFaturamentoLiquidoPermuta]
    , [TotalBrutoContrato]
    , [TotalLiquidoContratoAGBRCTACORDO]
    , [TotalLiquidoContratoAGBRVENDGERCOOR]
    , [TotalPercentualAgencia]
    , [TotalValorMensalAgencia]
    , [TotalPercentualBureau]
    , [TotalValorBureauMensal]
    , [TotalPercentualCartaAcordo]
    , [TotalValorCartaAcordoMensal]
    , [TotalValorOutrasComissoes]
    , [TotalFaturamentoLiquidoMensal]
    , [TotalPercentualComissaoVendedor]
    , [TotalValorVendedor]
    , [ValorVendedorTotal]
    , [TotalPercentualComissaoCoordenacao]
) VALUES (
      S.[Referencia]
    , S.[NumeroContrato]
    , S.[NumeroPrevia]
    , S.[CNPJ]
    , S.[DataAssinaturaRenovacao]
    , S.[IDTrimestre]
    , S.[DataLancamento]
    , S.[RazaoSocial]
    , S.[CPF]
    , S.[MarcaExibida]
    , S.[Vendedor]
    , S.[TipoDocumento]
    , S.[Origem]
    , S.[SDR]
    , S.[Agencia]
    , S.[CnpjAgencia]
    , S.[Bureau]
    , S.[CnpjBureau]
    , S.[Intermediario]
    , S.[CnpjIntermediario]
    , S.[QuantidadePontos]
    , S.[QuantidadeFaces]
    , S.[TotalFaturamentoBrutoMensal]
    , S.[TotalPercentualPermuta]
    , S.[TotalCotaOportunidade]
    , S.[TotalValorPermuta]
    , S.[TotalFaturamentoLiquidoPermuta]
    , S.[TotalBrutoContrato]
    , S.[TotalLiquidoContratoAGBRCTACORDO]
    , S.[TotalLiquidoContratoAGBRVENDGERCOOR]
    , S.[TotalPercentualAgencia]
    , S.[TotalValorMensalAgencia]
    , S.[TotalPercentualBureau]
    , S.[TotalValorBureauMensal]
    , S.[TotalPercentualCartaAcordo]
    , S.[TotalValorCartaAcordoMensal]
    , S.[TotalValorOutrasComissoes]
    , S.[TotalFaturamentoLiquidoMensal]
    , S.[TotalPercentualComissaoVendedor]
    , S.[TotalValorVendedor]
    , S.[ValorVendedorTotal]
    , S.[TotalPercentualComissaoCoordenacao]
);
"""

MERGE_ITENS_SQL = r"""
;WITH Itens AS (
    SELECT
          TRY_CONVERT(date, [DataLancamento]) AS [DataLancamento]
        , TRY_CONVERT(int, TRY_CONVERT(decimal(18,2), REPLACE([Cota], ',', '.'))) AS [Cota]

        , TRY_CONVERT(int, [CodPonto]) AS [CodPonto]
        , LEFT(TRY_CONVERT(varchar(200), [CodFace]), 20) AS [CodFace]
        , LEFT(TRY_CONVERT(nvarchar(200), [CidadeExibicao]), 100) AS [CidadeExibicao]
        , LEFT(TRY_CONVERT(nvarchar(200), [Tipo]), 70) AS [Tipo]
        , LEFT(TRY_CONVERT(nvarchar(50), [Origem]), 10) AS [Origem]
        , LEFT(TRY_CONVERT(nvarchar(200), [EmpresaEuro]), 100) AS [EmpresaEuro]
        , LEFT(TRY_CONVERT(char(50), [CnpjExibibora]), 20) AS [CnpjExibibora]
        , LEFT(TRY_CONVERT(nvarchar(200), [TipoDocumento]), 70) AS [TipoDocumento]

        , LEFT(TRY_CONVERT(varchar(300), [NumeroContrato]), 150) AS [NumeroContrato]
        , LEFT(TRY_CONVERT(varchar(300), [NumeroPrevia]), 150) AS [NumeroPrevia]
        , LEFT(TRY_CONVERT(char(50), [CNPJ]), 20) AS [CNPJ]
        , LEFT(TRY_CONVERT(char(50), [CPF]), 20) AS [CPF]

        , LEFT(TRY_CONVERT(nvarchar(400), [RazaoSocial]), 200) AS [RazaoSocial]
        , LEFT(TRY_CONVERT(nvarchar(200), [MarcaExibida]), 100) AS [MarcaExibida]
        , LEFT(TRY_CONVERT(nvarchar(200), [Vendedor]), 100) AS [Vendedor]
        , LEFT(TRY_CONVERT(char(50), [SDR]), 20) AS [SDR]
        , LEFT(TRY_CONVERT(nvarchar(200), [Agencia]), 100) AS [Agencia]
        , LEFT(TRY_CONVERT(char(50), [CnpjAgencia]), 20) AS [CnpjAgencia]
        , LEFT(TRY_CONVERT(nvarchar(200), [Bureau]), 100) AS [Bureau]
        , LEFT(TRY_CONVERT(char(50), [CnpjBureau]), 20) AS [CnpjBureau]
        , LEFT(TRY_CONVERT(nvarchar(200), [Intermediario]), 100) AS [Intermediario]
        , LEFT(TRY_CONVERT(char(50), [CnpjIntermediario]), 20) AS [CnpjIntermediario]

        , TRY_CONVERT(date, [DataAssinaturaRenovacao]) AS [DataAssinaturaRenovacao]
        , LEFT(TRY_CONVERT(varchar(200), [IDTrimestre]), 20) AS [IDTrimestre]
        , TRY_CONVERT(int, [TexmpoExposicao]) AS [TexmpoExposicao]
        , TRY_CONVERT(date, [DataInicioPrevisto]) AS [DataInicioPrevisto]
        , TRY_CONVERT(date, [DataTerminoPrevisto]) AS [DataTerminoPrevisto]
        , LEFT(TRY_CONVERT(char(10), [InicioRenovacao]), 2) AS [InicioRenovacao]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoBrutoMensal],'.',''),',','.')) AS [FaturamentoBrutoMensal]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualPermuta],'.',''),',','.')) AS [PercentualPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([CotaOportunidade],'.',''),',','.')) AS [CotaOportunidade]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorPermuta],'.',''),',','.')) AS [ValorPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoPermuta],'.',''),',','.')) AS [FaturamentoLiquidoPermuta]

        , TRY_CONVERT(int, [NumeroParcelas]) AS [NumeroParcelas]
        , TRY_CONVERT(date, [DataInicioVencimento]) AS [DataInicioVencimento]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalBrutoContrato],'.',''),',','.')) AS [TotalBrutoContrato]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRCTACORDO],'.',''),',','.')) AS [TotalLiquidoContratoAGBRCTACORDO]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRVENDGERCOOR],'.',''),',','.')) AS [TotalLiquidoContratoAGBRVENDGERCOOR]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualAgencia],'.',''),',','.')) AS [PercentualAgencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorMensalAgencia],'.',''),',','.')) AS [ValorMensalAgencia]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualBureau],'.',''),',','.')) AS [PercentualBureau]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([ValorBureauMensal],'.',''),',','.')) AS [ValorBureauMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualCartaAcordo],'.',''),',','.')) AS [PercentualCartaAcordo]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCartaAcordoMensal],'.',''),',','.')) AS [ValorCartaAcordoMensal]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorOutrasComissoes],'.',''),',','.')) AS [ValorOutrasComissoes]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoMensal],'.',''),',','.')) AS [FaturamentoLiquidoMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoVendedor],'.',''),',','.')) AS [PercentualComissaoVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedor],'.',''),',','.')) AS [ValorVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedorTotal],'.',''),',','.')) AS [ValorVendedorTotal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoCoordenacao],'.',''),',','.')) AS [PercentualComissaoCoordenacao]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCoordenador],'.',''),',','.')) AS [ValorCoordenador]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCoordenadorTotal],'.',''),',','.')) AS [ValorCoordenadorTotal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoGerencia],'.',''),',','.')) AS [PercentualComissaoGerencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorGerencia],'.',''),',','.')) AS [ValorGerencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorGerenciaTotal],'.',''),',','.')) AS [ValorGerenciaTotal]

        , LEFT(TRY_CONVERT(char(10), [AtivoCancelamento]), 2) AS [AtivoCancelamento]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoFinalMensal],'.',''),',','.')) AS [FaturamentoLiquidoFinalMensal]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([ComissaoGerenciaNordeste],'.',''),',','.')) AS [ComissaoGerenciaNordeste]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([Faturamento],'.',''),',','.')) AS [Faturamento]
        , TRY_CONVERT(date, [DataCancelamento]) AS [DataCancelamento]
        , LEFT(TRY_CONVERT(nvarchar(300), [OBS]), 150) AS [OBS]
    FROM [Integracao].[dbo].[df_fatocontrolecontratos]
),
Final AS (
    SELECT
        CONVERT(varchar(64),
            HASHBYTES('SHA2_256',
                CONCAT(
                    UPPER(LTRIM(RTRIM(COALESCE(NumeroContrato,'')))), '|',
                    UPPER(LTRIM(RTRIM(COALESCE(NumeroPrevia,'')))),  '|',
                    REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(CNPJ,''),'.',''),'-',''),'/',''),' ',''), '|',
                    COALESCE(CONVERT(varchar(30), CodPonto), ''), '|',
                    UPPER(LTRIM(RTRIM(COALESCE(CodFace,'')))), '|',
                    COALESCE(CONVERT(varchar(10), DataInicioPrevisto, 23), ''), '|',
                    COALESCE(CONVERT(varchar(10), DataTerminoPrevisto, 23), '')
                )
            ), 2
        ) AS [Referencia]
      , CAST(NULL AS INT) AS [IDFatoControleContratoEuromidia]
      , [NumeroContrato]
      , [NumeroPrevia]
      , [CNPJ]
      , [CodPonto]
      , [CodFace]
      , [DataLancamento]
      , [Cota]
      , [CidadeExibicao]
      , [Tipo]
      , [Origem]
      , [EmpresaEuro]
      , [CnpjExibibora]
      , [TipoDocumento]
      , [RazaoSocial]
      , [CPF]
      , [MarcaExibida]
      , [Vendedor]
      , [SDR]
      , [Agencia]
      , [CnpjAgencia]
      , [Bureau]
      , [CnpjBureau]
      , [Intermediario]
      , [CnpjIntermediario]
      , [DataAssinaturaRenovacao]
      , [IDTrimestre]
      , [TexmpoExposicao]
      , [DataInicioPrevisto]
      , [DataTerminoPrevisto]
      , [InicioRenovacao]
      , [FaturamentoBrutoMensal]
      , [PercentualPermuta]
      , [CotaOportunidade]
      , [ValorPermuta]
      , [FaturamentoLiquidoPermuta]
      , [NumeroParcelas]
      , [DataInicioVencimento]
      , [TotalBrutoContrato]
      , [TotalLiquidoContratoAGBRCTACORDO]
      , [TotalLiquidoContratoAGBRVENDGERCOOR]
      , [PercentualAgencia]
      , [ValorMensalAgencia]
      , [PercentualBureau]
      , [ValorBureauMensal]
      , [PercentualCartaAcordo]
      , [ValorCartaAcordoMensal]
      , [ValorOutrasComissoes]
      , [FaturamentoLiquidoMensal]
      , [PercentualComissaoVendedor]
      , [ValorVendedor]
      , [ValorVendedorTotal]
      , [PercentualComissaoCoordenacao]
      , [ValorCoordenador]
      , [ValorCoordenadorTotal]
      , [PercentualComissaoGerencia]
      , [ValorGerencia]
      , [ValorGerenciaTotal]
      , [AtivoCancelamento]
      , [FaturamentoLiquidoFinalMensal]
      , [ComissaoGerenciaNordeste]
      , [Faturamento]
      , [DataCancelamento]
      , [OBS]
    FROM Itens
),
Fonte AS (
    SELECT
          f.Referencia
        , MAX(f.IDFatoControleContratoEuromidia) AS IDFatoControleContratoEuromidia
        , MAX(f.NumeroContrato) AS NumeroContrato
        , MAX(f.NumeroPrevia) AS NumeroPrevia
        , MAX(f.CNPJ) AS CNPJ
        , MAX(f.CodPonto) AS CodPonto
        , MAX(f.CodFace) AS CodFace
        , MAX(f.DataLancamento) AS DataLancamento
        , MAX(f.Cota) AS Cota
        , MAX(f.CidadeExibicao) AS CidadeExibicao
        , MAX(f.Tipo) AS Tipo
        , MAX(f.Origem) AS Origem
        , MAX(f.EmpresaEuro) AS EmpresaEuro
        , MAX(f.CnpjExibibora) AS CnpjExibibora
        , MAX(f.TipoDocumento) AS TipoDocumento
        , MAX(f.RazaoSocial) AS RazaoSocial
        , MAX(f.CPF) AS CPF
        , MAX(f.MarcaExibida) AS MarcaExibida
        , MAX(f.Vendedor) AS Vendedor
        , MAX(f.SDR) AS SDR
        , MAX(f.Agencia) AS Agencia
        , MAX(f.CnpjAgencia) AS CnpjAgencia
        , MAX(f.Bureau) AS Bureau
        , MAX(f.CnpjBureau) AS CnpjBureau
        , MAX(f.Intermediario) AS Intermediario
        , MAX(f.CnpjIntermediario) AS CnpjIntermediario
        , MAX(f.DataAssinaturaRenovacao) AS DataAssinaturaRenovacao
        , MAX(f.IDTrimestre) AS IDTrimestre
        , MAX(f.TexmpoExposicao) AS TexmpoExposicao
        , MAX(f.DataInicioPrevisto) AS DataInicioPrevisto
        , MAX(f.DataTerminoPrevisto) AS DataTerminoPrevisto
        , MAX(f.InicioRenovacao) AS InicioRenovacao
        , MAX(f.FaturamentoBrutoMensal) AS FaturamentoBrutoMensal
        , MAX(f.PercentualPermuta) AS PercentualPermuta
        , MAX(f.CotaOportunidade) AS CotaOportunidade
        , MAX(f.ValorPermuta) AS ValorPermuta
        , MAX(f.FaturamentoLiquidoPermuta) AS FaturamentoLiquidoPermuta
        , MAX(f.NumeroParcelas) AS NumeroParcelas
        , MAX(f.DataInicioVencimento) AS DataInicioVencimento
        , MAX(f.TotalBrutoContrato) AS TotalBrutoContrato
        , MAX(f.TotalLiquidoContratoAGBRCTACORDO) AS TotalLiquidoContratoAGBRCTACORDO
        , MAX(f.TotalLiquidoContratoAGBRVENDGERCOOR) AS TotalLiquidoContratoAGBRVENDGERCOOR
        , MAX(f.PercentualAgencia) AS PercentualAgencia
        , MAX(f.ValorMensalAgencia) AS ValorMensalAgencia
        , MAX(f.PercentualBureau) AS PercentualBureau
        , MAX(f.ValorBureauMensal) AS ValorBureauMensal
        , MAX(f.PercentualCartaAcordo) AS PercentualCartaAcordo
        , MAX(f.ValorCartaAcordoMensal) AS ValorCartaAcordoMensal
        , MAX(f.ValorOutrasComissoes) AS ValorOutrasComissoes
        , MAX(f.FaturamentoLiquidoMensal) AS FaturamentoLiquidoMensal
        , MAX(f.PercentualComissaoVendedor) AS PercentualComissaoVendedor
        , MAX(f.ValorVendedor) AS ValorVendedor
        , MAX(f.ValorVendedorTotal) AS ValorVendedorTotal
        , MAX(f.PercentualComissaoCoordenacao) AS PercentualComissaoCoordenacao
        , MAX(f.ValorCoordenador) AS ValorCoordenador
        , MAX(f.ValorCoordenadorTotal) AS ValorCoordenadorTotal
        , MAX(f.PercentualComissaoGerencia) AS PercentualComissaoGerencia
        , MAX(f.ValorGerencia) AS ValorGerencia
        , MAX(f.ValorGerenciaTotal) AS ValorGerenciaTotal
        , MAX(f.AtivoCancelamento) AS AtivoCancelamento
        , MAX(f.FaturamentoLiquidoFinalMensal) AS FaturamentoLiquidoFinalMensal
        , MAX(f.ComissaoGerenciaNordeste) AS ComissaoGerenciaNordeste
        , MAX(f.Faturamento) AS Faturamento
        , MAX(f.DataCancelamento) AS DataCancelamento
        , MAX(f.OBS) AS OBS
    FROM Final f
    GROUP BY f.Referencia
)
MERGE INTO [Silver].[FatoControleContratosItensEuromidia] AS T
USING Fonte AS S
    ON T.Referencia = S.Referencia
WHEN MATCHED THEN
    UPDATE SET
          T.DataAtualizacao = GETDATE()
        , T.IDFatoControleContratoEuromidia = S.IDFatoControleContratoEuromidia
        , T.NumeroContrato = S.NumeroContrato
        , T.NumeroPrevia = S.NumeroPrevia
        , T.CNPJ = S.CNPJ
        , T.CodPonto = S.CodPonto
        , T.CodFace = S.CodFace
        , T.DataLancamento = S.DataLancamento
        , T.Cota = S.Cota
        , T.CidadeExibicao = S.CidadeExibicao
        , T.Tipo = S.Tipo
        , T.Origem = S.Origem
        , T.EmpresaEuro = S.EmpresaEuro
        , T.CnpjExibibora = S.CnpjExibibora
        , T.TipoDocumento = S.TipoDocumento
        , T.RazaoSocial = S.RazaoSocial
        , T.CPF = S.CPF
        , T.MarcaExibida = S.MarcaExibida
        , T.Vendedor = S.Vendedor
        , T.SDR = S.SDR
        , T.Agencia = S.Agencia
        , T.CnpjAgencia = S.CnpjAgencia
        , T.Bureau = S.Bureau
        , T.CnpjBureau = S.CnpjBureau
        , T.Intermediario = S.Intermediario
        , T.CnpjIntermediario = S.CnpjIntermediario
        , T.DataAssinaturaRenovacao = S.DataAssinaturaRenovacao
        , T.IDTrimestre = S.IDTrimestre
        , T.TexmpoExposicao = S.TexmpoExposicao
        , T.DataInicioPrevisto = S.DataInicioPrevisto
        , T.DataTerminoPrevisto = S.DataTerminoPrevisto
        , T.InicioRenovacao = S.InicioRenovacao
        , T.FaturamentoBrutoMensal = S.FaturamentoBrutoMensal
        , T.PercentualPermuta = S.PercentualPermuta
        , T.CotaOportunidade = S.CotaOportunidade
        , T.ValorPermuta = S.ValorPermuta
        , T.FaturamentoLiquidoPermuta = S.FaturamentoLiquidoPermuta
        , T.NumeroParcelas = S.NumeroParcelas
        , T.DataInicioVencimento = S.DataInicioVencimento
        , T.TotalBrutoContrato = S.TotalBrutoContrato
        , T.TotalLiquidoContratoAGBRCTACORDO = S.TotalLiquidoContratoAGBRCTACORDO
        , T.TotalLiquidoContratoAGBRVENDGERCOOR = S.TotalLiquidoContratoAGBRVENDGERCOOR
        , T.PercentualAgencia = S.PercentualAgencia
        , T.ValorMensalAgencia = S.ValorMensalAgencia
        , T.PercentualBureau = S.PercentualBureau
        , T.ValorBureauMensal = S.ValorBureauMensal
        , T.PercentualCartaAcordo = S.PercentualCartaAcordo
        , T.ValorCartaAcordoMensal = S.ValorCartaAcordoMensal
        , T.ValorOutrasComissoes = S.ValorOutrasComissoes
        , T.FaturamentoLiquidoMensal = S.FaturamentoLiquidoMensal
        , T.PercentualComissaoVendedor = S.PercentualComissaoVendedor
        , T.ValorVendedor = S.ValorVendedor
        , T.ValorVendedorTotal = S.ValorVendedorTotal
        , T.PercentualComissaoCoordenacao = S.PercentualComissaoCoordenacao
        , T.ValorCoordenador = S.ValorCoordenador
        , T.ValorCoordenadorTotal = S.ValorCoordenadorTotal
        , T.PercentualComissaoGerencia = S.PercentualComissaoGerencia
        , T.ValorGerencia = S.ValorGerencia
        , T.ValorGerenciaTotal = S.ValorGerenciaTotal
        , T.AtivoCancelamento = S.AtivoCancelamento
        , T.FaturamentoLiquidoFinalMensal = S.FaturamentoLiquidoFinalMensal
        , T.ComissaoGerenciaNordeste = S.ComissaoGerenciaNordeste
        , T.Faturamento = S.Faturamento
        , T.DataCancelamento = S.DataCancelamento
        , T.OBS = S.OBS
WHEN NOT MATCHED BY TARGET THEN
    INSERT (
          [IDFatoControleContratoEuromidia]
        , [Referencia]
        , [NumeroContrato]
        , [NumeroPrevia]
        , [CNPJ]
        , [CodPonto]
        , [CodFace]
        , [DataLancamento]
        , [Cota]
        , [CidadeExibicao]
        , [Tipo]
        , [Origem]
        , [EmpresaEuro]
        , [CnpjExibibora]
        , [TipoDocumento]
        , [RazaoSocial]
        , [CPF]
        , [MarcaExibida]
        , [Vendedor]
        , [SDR]
        , [Agencia]
        , [CnpjAgencia]
        , [Bureau]
        , [CnpjBureau]
        , [Intermediario]
        , [CnpjIntermediario]
        , [DataAssinaturaRenovacao]
        , [IDTrimestre]
        , [TexmpoExposicao]
        , [DataInicioPrevisto]
        , [DataTerminoPrevisto]
        , [InicioRenovacao]
        , [FaturamentoBrutoMensal]
        , [PercentualPermuta]
        , [CotaOportunidade]
        , [ValorPermuta]
        , [FaturamentoLiquidoPermuta]
        , [NumeroParcelas]
        , [DataInicioVencimento]
        , [TotalBrutoContrato]
        , [TotalLiquidoContratoAGBRCTACORDO]
        , [TotalLiquidoContratoAGBRVENDGERCOOR]
        , [PercentualAgencia]
        , [ValorMensalAgencia]
        , [PercentualBureau]
        , [ValorBureauMensal]
        , [PercentualCartaAcordo]
        , [ValorCartaAcordoMensal]
        , [ValorOutrasComissoes]
        , [FaturamentoLiquidoMensal]
        , [PercentualComissaoVendedor]
        , [ValorVendedor]
        , [ValorVendedorTotal]
        , [PercentualComissaoCoordenacao]
        , [ValorCoordenador]
        , [ValorCoordenadorTotal]
        , [PercentualComissaoGerencia]
        , [ValorGerencia]
        , [ValorGerenciaTotal]
        , [AtivoCancelamento]
        , [FaturamentoLiquidoFinalMensal]
        , [ComissaoGerenciaNordeste]
        , [Faturamento]
        , [DataCancelamento]
        , [OBS]
    )
    VALUES (
          S.IDFatoControleContratoEuromidia
        , S.Referencia
        , S.NumeroContrato
        , S.NumeroPrevia
        , S.CNPJ
        , S.CodPonto
        , S.CodFace
        , S.DataLancamento
        , S.Cota
        , S.CidadeExibicao
        , S.Tipo
        , S.Origem
        , S.EmpresaEuro
        , S.CnpjExibibora
        , S.TipoDocumento
        , S.RazaoSocial
        , S.CPF
        , S.MarcaExibida
        , S.Vendedor
        , S.SDR
        , S.Agencia
        , S.CnpjAgencia
        , S.Bureau
        , S.CnpjBureau
        , S.Intermediario
        , S.CnpjIntermediario
        , S.DataAssinaturaRenovacao
        , S.IDTrimestre
        , S.TexmpoExposicao
        , S.DataInicioPrevisto
        , S.DataTerminoPrevisto
        , S.InicioRenovacao
        , S.FaturamentoBrutoMensal
        , S.PercentualPermuta
        , S.CotaOportunidade
        , S.ValorPermuta
        , S.FaturamentoLiquidoPermuta
        , S.NumeroParcelas
        , S.DataInicioVencimento
        , S.TotalBrutoContrato
        , S.TotalLiquidoContratoAGBRCTACORDO
        , S.TotalLiquidoContratoAGBRVENDGERCOOR
        , S.PercentualAgencia
        , S.ValorMensalAgencia
        , S.PercentualBureau
        , S.ValorBureauMensal
        , S.PercentualCartaAcordo
        , S.ValorCartaAcordoMensal
        , S.ValorOutrasComissoes
        , S.FaturamentoLiquidoMensal
        , S.PercentualComissaoVendedor
        , S.ValorVendedor
        , S.ValorVendedorTotal
        , S.PercentualComissaoCoordenacao
        , S.ValorCoordenador
        , S.ValorCoordenadorTotal
        , S.PercentualComissaoGerencia
        , S.ValorGerencia
        , S.ValorGerenciaTotal
        , S.AtivoCancelamento
        , S.FaturamentoLiquidoFinalMensal
        , S.ComissaoGerenciaNordeste
        , S.Faturamento
        , S.DataCancelamento
        , S.OBS
    );
"""

UPDATE_FK_SQL = r"""
UPDATE ftci
SET
    ftci.IDFatoControleContratoEuromidia = ftc.IDFatoControleContratosEuromidia
FROM [Silver].[FatoControleContratosItensEuromidia] AS ftci
INNER JOIN [Silver].[FatoControleContratosEuromidia] AS ftc
    ON  ftc.NumeroContrato = ftci.NumeroContrato
    AND ftc.NumeroPrevia   = ftci.NumeroPrevia
WHERE
    ftci.IDFatoControleContratoEuromidia IS NULL
    OR ftci.IDFatoControleContratoEuromidia <> ftc.IDFatoControleContratosEuromidia;
"""

UPDATE_VENDEDOR_SQL = r"""
UPDATE fctti
SET
    fctti.IDVendedor = vdd.IDVendedor
FROM [Silver].[FatoControleContratosItensEuromidia] AS fctti
INNER JOIN [Integracao].[dbo].[Vendedores] AS vdd
    ON vdd.NomeVendedor = fctti.Vendedor
WHERE
    fctti.Vendedor IS NOT NULL
    AND LTRIM(RTRIM(fctti.Vendedor)) <> ''
    AND (
        fctti.IDVendedor IS NULL
        OR fctti.IDVendedor <> vdd.IDVendedor
    );
"""

UPDATE_EMPRESA_SQL = r"""
UPDATE fct
SET
    fct.IDEmpresa = emp.IDEmpresa
FROM [Silver].[FatoControleContratosEuromidia] AS fct
INNER JOIN [Integracao].[Silver].[DimEmpresas] AS emp
    ON emp.CNPJ = fct.CNPJ;
"""

UPDATE_PONTOS_SQL = r"""
UPDATE fcti
SET fcti.IDPainelEuromidia = dpie.IDDimPaineisEuromidia
FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS fcti
INNER JOIN [Integracao].[Silver].[DimPaineisEuromidia] AS dpie
    ON dpie.CodPonto = fcti.CodPonto
WHERE
    fcti.IDPainelEuromidia IS NULL
    OR fcti.IDPainelEuromidia <> dpie.IDDimPaineisEuromidia;
"""

UPDATE_FACES_SQL = r"""
UPDATE fcti
SET fcti.IDDimFacesPaineis = dpe.IDDimFacesPaineis
FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS fcti
INNER JOIN [Integracao].[Silver].[DimFacesPaineis] AS dpe
    ON dpe.CodFace = fcti.CodFace
WHERE
    fcti.IDDimFacesPaineis IS NULL
    OR fcti.IDDimFacesPaineis <> dpe.IDDimFacesPaineis;
"""

CALL_PROCEDURE_SQL = r"""
EXEC Silver.sp_UpsertDimCalendario;
"""

UPDATE_OCUPACAO_SQL = r"""
SET NOCOUNT ON;

;WITH Fonte AS (
    SELECT
        i.DataAtualizacao,
        i.Referencia,
        CodPonto = i.CodPonto,
        CodFace  = LTRIM(RTRIM(i.CodFace)),
        IDPainelEuromidia = i.IDPainelEuromidia,
        Origem = CAST('CONTRATO' AS varchar(20)),
        Status = CAST(CASE WHEN i.DataCancelamento IS NULL THEN 'ATIVO' ELSE 'CANCELADO' END AS varchar(20)),
        DataInicio = i.DataInicioPrevisto,
        DataFim    = COALESCE(i.DataCancelamento, i.DataTerminoPrevisto),
        LoopInicio = CAST(NULL AS int),
        LoopFim    = CAST(NULL AS int),
        SpanQtd    = CAST(NULL AS int),
        Cota       = i.Cota,
        MarcaExibida = i.MarcaExibida,
        Vendedor     = i.Vendedor,
        IDVendedor   = i.IDVendedor,
        IDCliente    = c.IDEmpresa,
        IDFatoControleContratos = i.IDFatoControleContratoEuromidia,
        NumeroContrato = i.NumeroContrato,
        NumeroPrevia   = i.NumeroPrevia,
        TextoOriginal = CONCAT(
            'CONTRATO:', COALESCE(i.NumeroContrato,''),
            ' | PRÉVIA:', COALESCE(i.NumeroPrevia,'')
        ),
        CriadoEm = CAST(COALESCE(i.DataLancamento, CAST(i.DataAtualizacao AS date)) AS datetime2(0)),
        CriadoPorIDUsuario = ISNULL(i.IDVendedor, 0),
        ExpiraEm = CAST(NULL AS datetime2(0)),
        CanceladoEm = CASE
                        WHEN i.DataCancelamento IS NULL THEN NULL
                        ELSE CAST(i.DataAtualizacao AS datetime2(0))
                      END,
        CanceladoPorIDUsuario = CAST(NULL AS int),
        Observacao = i.OBS,
        Dias = CASE
                 WHEN i.DataInicioPrevisto IS NULL THEN NULL
                 WHEN COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) IS NULL THEN NULL
                 WHEN COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) < i.DataInicioPrevisto THEN NULL
                 ELSE DATEDIFF(day, i.DataInicioPrevisto, COALESCE(i.DataCancelamento, i.DataTerminoPrevisto)) + 1
               END,
        rn = ROW_NUMBER() OVER (
                PARTITION BY i.Referencia
                ORDER BY i.DataAtualizacao DESC, i.IDFatoControleContratosItensEuromidia DESC
             )
    FROM Integracao.Silver.FatoControleContratosItensEuromidia i
    LEFT JOIN Integracao.Silver.FatoControleContratosEuromidia c
        ON  c.NumeroContrato = i.NumeroContrato
        AND c.NumeroPrevia   = i.NumeroPrevia
        AND ISNULL(c.CNPJ,'') = ISNULL(i.CNPJ,'')

    WHERE
        i.Referencia IS NOT NULL
        AND i.Referencia <> ''
        AND i.CodPonto IS NOT NULL
        AND NULLIF(LTRIM(RTRIM(i.CodFace)), '') IS NOT NULL
        AND i.DataInicioPrevisto IS NOT NULL
        AND COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) IS NOT NULL
),
FonteFinal AS (
    SELECT *
    FROM Fonte
    WHERE rn = 1
)

MERGE Integracao.Silver.FatoOcupacaoPaineisEuromidia AS T
USING FonteFinal AS S
    ON  T.Referencia = S.Referencia
    AND T.Origem = 'CONTRATO'

WHEN MATCHED AND (
       ISNULL(T.DataAtualizacao, '19000101') < ISNULL(S.DataAtualizacao, '19000101')
    OR ISNULL(T.CodPonto, -1) <> ISNULL(S.CodPonto, -1)
    OR ISNULL(T.CodFace, '') <> ISNULL(S.CodFace, '')
    OR ISNULL(T.DataInicio, '19000101') <> ISNULL(S.DataInicio, '19000101')
    OR ISNULL(T.DataFim, '19000101') <> ISNULL(S.DataFim, '19000101')
    OR ISNULL(T.Cota, -1) <> ISNULL(S.Cota, -1)
    OR ISNULL(T.MarcaExibida, '') <> ISNULL(S.MarcaExibida, '')
    OR ISNULL(T.Vendedor, '') <> ISNULL(S.Vendedor, '')
    OR ISNULL(T.IDVendedor, -1) <> ISNULL(S.IDVendedor, -1)
    OR ISNULL(T.IDCliente, -1) <> ISNULL(S.IDCliente, -1)
    OR ISNULL(T.IDFatoControleContratos, -1) <> ISNULL(S.IDFatoControleContratos, -1)
    OR ISNULL(T.NumeroContrato, '') <> ISNULL(S.NumeroContrato, '')
    OR ISNULL(T.NumeroPrevia, '') <> ISNULL(S.NumeroPrevia, '')
    OR ISNULL(T.Status, '') <> ISNULL(S.Status, '')
    OR ISNULL(T.Observacao, '') <> ISNULL(S.Observacao, '')
)
THEN UPDATE SET
    T.DataAtualizacao = S.DataAtualizacao,
    T.CodPonto = S.CodPonto,
    T.CodFace = S.CodFace,
    T.IDPainelEuromidia = S.IDPainelEuromidia,
    T.Status = S.Status,
    T.DataInicio = S.DataInicio,
    T.DataFim = S.DataFim,
    T.LoopInicio = S.LoopInicio,
    T.LoopFim = S.LoopFim,
    T.SpanQtd = S.SpanQtd,
    T.Cota = S.Cota,
    T.MarcaExibida = S.MarcaExibida,
    T.Vendedor = S.Vendedor,
    T.IDVendedor = S.IDVendedor,
    T.IDCliente = S.IDCliente,
    T.IDFatoControleContratos = S.IDFatoControleContratos,
    T.NumeroContrato = S.NumeroContrato,
    T.NumeroPrevia = S.NumeroPrevia,
    T.TextoOriginal = S.TextoOriginal,
    T.CriadoEm = S.CriadoEm,
    T.CriadoPorIDUsuario = S.CriadoPorIDUsuario,
    T.ExpiraEm = S.ExpiraEm,
    T.CanceladoEm = S.CanceladoEm,
    T.CanceladoPorIDUsuario = S.CanceladoPorIDUsuario,
    T.Observacao = S.Observacao,
    T.Dias = S.Dias

WHEN NOT MATCHED BY TARGET
THEN INSERT (
    DataAtualizacao,
    Referencia,
    CodPonto,
    CodFace,
    IDPainelEuromidia,
    Origem,
    Status,
    DataInicio,
    DataFim,
    LoopInicio,
    LoopFim,
    SpanQtd,
    Cota,
    MarcaExibida,
    Vendedor,
    IDVendedor,
    IDCliente,
    IDFatoControleContratos,
    NumeroContrato,
    NumeroPrevia,
    TextoOriginal,
    CriadoEm,
    CriadoPorIDUsuario,
    ExpiraEm,
    CanceladoEm,
    CanceladoPorIDUsuario,
    Observacao,
    Dias
)
VALUES (
    S.DataAtualizacao,
    S.Referencia,
    S.CodPonto,
    S.CodFace,
    S.IDPainelEuromidia,
    S.Origem,
    S.Status,
    S.DataInicio,
    S.DataFim,
    S.LoopInicio,
    S.LoopFim,
    S.SpanQtd,
    S.Cota,
    S.MarcaExibida,
    S.Vendedor,
    S.IDVendedor,
    S.IDCliente,
    S.IDFatoControleContratos,
    S.NumeroContrato,
    S.NumeroPrevia,
    S.TextoOriginal,
    S.CriadoEm,
    S.CriadoPorIDUsuario,
    S.ExpiraEm,
    S.CanceladoEm,
    S.CanceladoPorIDUsuario,
    S.Observacao,
    S.Dias
);
"""


def executar_sql_auditado(
    nome_amigavel: str,
    descricao_etapa: str,
    sql_execucao: str,
    sql_amostra: str | None = None,
    destino_dados: str | None = None,
    origem_dados: str | None = None,
) -> dict[str, Any]:
    """Executa SQL com auditoria estruturada e amostra opcional."""
    resumo = criar_resumo_auditoria(
        nome_amigavel=nome_amigavel,
        descricao_etapa=descricao_etapa,
        origem_dados=origem_dados,
        destino_dados=destino_dados,
    )

    engine = obter_engine_sql_server()

    try:
        resumo.status = "RUNNING"
        resumo.metricas_extras["conn_id_sql_server"] = CONN_ID_SQL_SERVER
        publicar_resumo_auditoria(resumo)

        with engine.begin() as conn:
            conn.exec_driver_sql(sql_execucao)

        resumo.status = "SUCCESS"
        adicionar_validacao(
            resumo,
            nome="sql_executado",
            status="ok",
            detalhe=f"A etapa SQL '{nome_amigavel}' foi executada com sucesso.",
        )

        amostra = []
        if sql_amostra:
            amostra = consultar_amostra_sql(engine=engine, sql=sql_amostra, limite=5)
            if amostra:
                definir_amostra(resumo, amostra, limite=10)

        publicar_resumo_auditoria(resumo)

        return {
            "etapa": nome_amigavel,
            "amostra": amostra,
        }
    except Exception as erro:
        resumo.status = "FAILED"
        registrar_erro_no_resumo(resumo, erro)
        publicar_resumo_auditoria(resumo)
        raise
    finally:
        engine.dispose()


@dag(
    dag_id=DAG_ID,
    schedule=CRON_AGENDAMENTO,
    start_date=pendulum.datetime(2026, 3, 19, 0, 0, tz=FUSO_HORARIO),
    catchup=False,
    max_active_runs=1,
    tags=["Euromidia", "ETL", "Contratos", "Sharepoint", "SQLServer"],
    description=(
        "Pipeline ETL do controle de contratos da Euromídia. Lê a aba CTR de um arquivo Excel montado "
        "no container, padroniza datas, números, percentuais, CNPJ e campos textuais, gera identificadores "
        "hash para contratos e prévias ausentes, produz um CSV técnico, recarrega a stage SQL Server via "
        "insert em lote e executa a cadeia de consolidação nas tabelas Silver e ocupação."
    ),
    doc_md=r"""
# ETL CTR - Controle de Contratos Euromídia

## Visão geral

Este DAG implementa o fluxo de ingestão e consolidação do arquivo de **controle de contratos CTR** da Euromídia.

Ele foi desenhado para resolver um problema operacional muito comum em ambientes Docker + Airflow + Windows/WSL:
a fragilidade de cargas baseadas em filesystem externo e permissões inconsistentes de bind mount.

Por isso, o fluxo foi estruturado para:

1. ler o Excel diretamente no container;
2. tratar e normalizar os dados com Polars;
3. gerar um CSV técnico em uma pasta estável do container;
4. recarregar a tabela stage por insert em lote via SQLAlchemy;
5. consolidar os fatos e relacionamentos nas tabelas Silver;
6. atualizar ocupação, dimensões auxiliares e chaves de relacionamento.

---

## Objetivo de negócio

O pipeline existe para transformar uma planilha operacional de contratos em uma base estruturada para:

- consolidação de contratos;
- detalhamento por item/face/ponto;
- relacionamento com vendedor, cliente, painel e face;
- visão de ocupação dos painéis;
- apoio a análises comerciais, faturamento, ocupação e performance operacional.

---

## Origem dos dados

Arquivo Excel montado no container:

`/opt/airflow/sharepoint_teste/Copia-Controle de Contratos Euromidia.xlsm`

Aba lida:

`CTR`

---

## Destino intermediário e final

### Stage
- `dbo.df_fatocontrolecontratos`

### Silver
- `Silver.FatoControleContratosEuromidia`
- `Silver.FatoControleContratosItensEuromidia`
- `Silver.FatoOcupacaoPaineisEuromidia`

### Procedimentos e atualizações auxiliares
- `Silver.sp_UpsertDimCalendario`
- relacionamento com:
  - `DimEmpresas`
  - `DimPaineisEuromidia`
  - `DimFacesPaineis`
  - `Vendedores`

---

## Frequência de execução

O DAG executa:

- de segunda a sábado
- às 09:00
- e às 18:00

Cron:

`0 9,18 * * 1-6`

---

## Motivação técnica da arquitetura

### Problema original
O modelo tradicional usando `BULK INSERT` em arquivo hospedado em bind mount do Windows é frágil em ambientes:

- Docker Desktop
- WSL
- Airflow
- permissões heterogêneas entre host e container

Isso costuma causar:
- erro de permissão
- arquivo visível no host mas inacessível ao SQL Server
- inconsistência de path
- falha intermitente entre ambientes

### Solução aplicada
O DAG foi estruturado para:
- processar o Excel no container
- escrever o CSV técnico em uma pasta interna estável:
  `/opt/airflow/Artefatos/CargasSQL/CTR`
- carregar a stage via `to_sql`/insert em lote
- remover a dependência crítica de `BULK INSERT` em caminho externo

Essa abordagem é mais robusta, mais portátil e mais compatível com execução containerizada.

---

## Etapas detalhadas do processo

### 1. Leitura da planilha CTR
A aba `CTR` é lida com `polars.read_excel`, usando `calamine`, com inferência mínima e schema textual base para garantir maior tolerância a planilhas “sujas”.

### 2. Padronização dos dados
São tratados:
- datas em formatos brasileiros, ISO, datetime e serial Excel;
- inteiros;
- percentuais;
- valores monetários;
- CNPJ;
- textos com caracteres invisíveis.

### 3. Renomeação das colunas
As colunas de origem da planilha são renomeadas para nomes técnicos padronizados do pipeline.

### 4. Geração de hashes
Quando `NumeroContrato` ou `NumeroPrevia` estiverem vazios, são gerados hashes determinísticos baseados em assinatura de negócio.

Isso reduz perda de rastreabilidade e melhora a estabilidade do relacionamento entre registros.

### 5. Geração do CSV técnico
O CSV é gravado em pasta estável do container, com:
- `;` como separador
- vírgula decimal
- `utf-8-sig`
- formato de data `YYYY-MM-DD`

### 6. Recarga da stage
O CSV é relido e enviado para a stage:
- `dbo.df_fatocontrolecontratos`

A carga usa insert em lote via SQLAlchemy.

### 7. Consolidação do contrato
A tabela `Silver.FatoControleContratosEuromidia` recebe uma visão agregada por referência contratual.

### 8. Consolidação dos itens
A tabela `Silver.FatoControleContratosItensEuromidia` recebe a granularidade detalhada por item.

### 9. Atualização de vínculos
São atualizadas:
- FK de itens para contratos
- ID do vendedor
- ID da empresa
- ID do painel
- ID da face

### 10. Calendário e ocupação
Por fim:
- atualiza dimensão calendário
- executa MERGE de ocupação em `FatoOcupacaoPaineisEuromidia`

---

## Observabilidade e auditoria

Cada task publica auditoria estruturada contendo:
- nome amigável
- descrição da etapa
- origem e destino
- validações executadas
- observações técnicas
- amostras reais dos dados

Isso foi feito para que a execução fique legível no painel e não dependa só de log bruto.

---

## Conexões esperadas

### SQL Server
- `mssql_integracao`

---

## Premissas operacionais

- O arquivo Excel deve existir no caminho esperado antes da execução.
- A aba `CTR` precisa existir.
- A stage deve existir previamente no banco.
- As tabelas Silver e dimensões relacionadas devem existir.
- O Airflow precisa ter acesso à connection `mssql_integracao`.

---

## Benefícios da abordagem atual

- menor dependência de filesystem externo
- maior estabilidade em ambiente Docker
- maior rastreabilidade
- tratamento explícito de formatos mistos
- melhor observabilidade por etapa
- redução de falhas silenciosas em planilhas operacionais

---
""",
)
def pipeline_controle_contratos_euromidia():
    @task(task_id="gerar_csv_ctr")
    def gerar_csv_ctr() -> dict[str, Any]:
        resumo = criar_resumo_auditoria(
            nome_amigavel="Gerar CSV técnico do CTR",
            descricao_etapa=(
                "Lê a aba CTR do arquivo Excel, aplica normalização de datas, números, percentuais, "
                "CNPJ e textos, renomeia colunas, gera hashes para contrato/prévia ausentes e grava "
                "um CSV técnico em pasta estável do container."
            ),
            origem_dados=str(CAMINHO_ARQUIVO_EXCEL),
            destino_dados=str(PASTA_CARGA_CONTAINER),
        )

        try:
            resumo.status = "RUNNING"
            resumo.metricas_extras["nome_aba_excel"] = NOME_ABA_EXCEL
            resumo.metricas_extras["conn_id_sql_server"] = CONN_ID_SQL_SERVER
            publicar_resumo_auditoria(resumo)

            logger.info("PASTA_SHAREPOINT_CONTAINER: %s", PASTA_SHAREPOINT_CONTAINER)
            logger.info("CAMINHO_ARQUIVO_EXCEL: %s", CAMINHO_ARQUIVO_EXCEL)
            logger.info("PASTA_SHAREPOINT_CONTAINER.exists(): %s", PASTA_SHAREPOINT_CONTAINER.exists())
            logger.info("CAMINHO_ARQUIVO_EXCEL.exists(): %s", CAMINHO_ARQUIVO_EXCEL.exists())
            logger.info("PASTA_CARGA_CONTAINER: %s", PASTA_CARGA_CONTAINER)
            logger.info("PASTA_CARGA_CONTAINER.exists(): %s", PASTA_CARGA_CONTAINER.exists())

            garantir_pasta_escrita(PASTA_CARGA_CONTAINER)

            lazy = ler_aba_ctr_xlsm_lazy(str(CAMINHO_ARQUIVO_EXCEL))
            colunas_existentes = set(lazy.collect_schema().names())

            cols_datas = _somente_existentes(
                [
                    "DATA DO LANÇAMENTO",
                    "DATA DE ASSINATURA/RENOVAÇÃO (EMISSÃO)",
                    "DATA DE INÍCIO PREVISTO",
                    "DATA DE TÉRMINO PREVISTO",
                    "DATA DO 1º VENCIMENTO",
                    "DATA DE CANCELAMENTO",
                ],
                colunas_existentes,
            )

            cols_int = _somente_existentes(
                [
                    "PONTO",
                    "TEMPO DE EXPOSIÇÃO [DIAS]",
                    "Nº DE PARCELAS",
                ],
                colunas_existentes,
            )

            cols_idtri = _somente_existentes(["ID. TRIMESTRE"], colunas_existentes)

            cols_float = _somente_existentes(
                [
                    "COTA (Exato)",
                    "% PERMUTA",
                    "COTA DE OPORTUNIDADE?",
                    "% AGÊNCIA",
                    "% BUREAU",
                    "% CARTA ACORDO",
                    "% COMISSÃO VENDEDOR",
                    "%COMISSÃO COORDENAÇÃO",
                    "% COMISSÃO GERÊNCIA",
                ],
                colunas_existentes,
            )

            cols_money = _somente_existentes(
                [
                    "FATURAMENTO BRUTO MENSAL",
                    "VALOR PERMUTA",
                    "FATURAMENTO LÍQ. (- PERMUTA)",
                    "TOTAL BRUTO DO CONTRATO",
                    "TOTAL LÍQUIDO DO CONTRATO (-AG, -BR, -CT ACORDO)",
                    "TOTAL LÍQUIDO DO CONTRATO (- AG, - BR, -VEND, - GER ,-COOR)",
                    "VALOR DA AGÊNCIA (MENSAL)",
                    "VALOR BUREAU (MENSAL)",
                    "VALOR CARTA ACORDO (MENSAL)",
                    "VALOR OUTRAS COMISSÕES",
                    "FATURAMENTO LÍQUIDO MENSAL",
                    "VALOR VENDEDOR",
                    "VALOR VENDEDOR TOTAL",
                    "VALOR COORDENADOR",
                    "VALOR COORDENADOR TOTAL",
                    "VALOR GERÊNCIA",
                    "VALOR GERÊNCIA TOTAL",
                    "FATURAMENTO LÍQUIDO FINAL MENSAL",
                    "COMISSÃO GERÊNCIA NORDESTE",
                    "FATURAMENTO",
                ],
                colunas_existentes,
            )

            cols_cnpj = _somente_existentes(
                [
                    "CNPJ",
                    "CNPJ AGÊNCIA",
                    "CNPJ BUREAU",
                    "CNPJ INTERMEDIÁRIO",
                    "CNPJ DA EXIBIDORA (EUROMIDIA)",
                ],
                colunas_existentes,
            )

            exprs = []

            for c in cols_datas:
                exprs.append(parse_data_br(pl.col(c)).alias(c))

            for c in cols_int:
                exprs.append(parse_int(pl.col(c)).alias(c))

            for c in cols_idtri:
                exprs.append(parse_id_trimestre(pl.col(c)).alias(c))

            for c in cols_float:
                exprs.append(parse_float_br(pl.col(c)).alias(c))

            for c in cols_money:
                exprs.append(parse_br_money(pl.col(c)).alias(c))

            for c in cols_cnpj:
                exprs.append(parse_cnpj(pl.col(c)).alias(c))

            if "OBS" in colunas_existentes:
                exprs.append(_limpar_texto(pl.col("OBS")).alias("OBS"))

            rename_map = {
                orig: dest
                for orig, dest in mapeamento_colunas.items()
                if orig in colunas_existentes
            }

            lazy_tratado = lazy.with_columns(exprs).rename(rename_map)

            try:
                df = lazy_tratado.collect(engine="streaming")
            except Exception:
                df = lazy_tratado.collect()

            df = aplicar_hash_contrato_e_previa(df)
            df = garantir_colunas_saida(df)

            agora = datetime.now()
            data_hora = agora.strftime("%Y%m%d_%H%M%S")
            nome_arquivo_csv = f"df_fatocontrolecontratos_{data_hora}.csv"

            caminho_saida_linux = PASTA_CARGA_CONTAINER / nome_arquivo_csv
            caminho_saida_linux.parent.mkdir(parents=True, exist_ok=True)

            csv_texto = df.write_csv(
                separator=";",
                decimal_comma=True,
                date_format="%Y-%m-%d",
            )

            with open(caminho_saida_linux, "w", encoding="utf-8-sig", newline="") as arquivo_saida:
                arquivo_saida.write(csv_texto)

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = int(df.height)
            resumo.linhas_inseridas = int(df.height)
            resumo.metricas_extras["nome_arquivo_csv"] = nome_arquivo_csv
            resumo.metricas_extras["caminho_csv_linux"] = str(caminho_saida_linux)
            resumo.metricas_extras["colunas_exportadas"] = int(df.width)

            adicionar_validacao(
                resumo,
                nome="arquivo_excel_disponivel",
                status="ok",
                detalhe=f"O arquivo Excel foi encontrado em {CAMINHO_ARQUIVO_EXCEL}.",
            )
            adicionar_validacao(
                resumo,
                nome="csv_tecnico_gerado",
                status="ok",
                detalhe=f"O CSV técnico foi gerado com {df.height:,} linhas e {df.width:,} colunas.",
            )
            adicionar_observacao(
                resumo,
                "A amostra mostra o dataset já padronizado e pronto para carga na stage técnica.",
            )

            definir_amostra(
                resumo,
                df_polars_para_amostra(
                    df,
                    limite=5,
                    colunas=[
                        "DataLancamento",
                        "NumeroContrato",
                        "NumeroPrevia",
                        "CNPJ",
                        "MarcaExibida",
                        "CodPonto",
                        "CodFace",
                        "DataInicioPrevisto",
                        "DataTerminoPrevisto",
                        "FaturamentoBrutoMensal",
                        "OBS",
                    ],
                ),
                limite=10,
            )
            publicar_resumo_auditoria(resumo)

            logger.info("Arquivo Excel lido com sucesso: %s", CAMINHO_ARQUIVO_EXCEL)
            logger.info("Linhas tratadas: %s", df.height)
            logger.info("Colunas exportadas: %s", df.width)
            logger.info("CSV técnico gerado em: %s", caminho_saida_linux)
            logger.info("Amostra:\n%s", df.head(5))

            return {
                "nome_arquivo_csv": nome_arquivo_csv,
                "caminho_csv_linux": str(caminho_saida_linux),
                "linhas": int(df.height),
                "colunas": int(df.width),
            }
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            raise

    @task(task_id="carregar_stage")
    def carregar_stage(info_csv: dict[str, Any]) -> dict[str, Any]:
        resumo = criar_resumo_auditoria(
            nome_amigavel="Carregar stage técnica",
            descricao_etapa=(
                "Relê o CSV técnico gerado, converte todos os campos para formato textual controlado "
                "e recarrega a tabela stage dbo.df_fatocontrolecontratos por insert em lote."
            ),
            origem_dados=info_csv["caminho_csv_linux"],
            destino_dados=TABELA_STAGE,
        )

        caminho_csv_linux = Path(info_csv["caminho_csv_linux"])

        try:
            resumo.status = "RUNNING"
            publicar_resumo_auditoria(resumo)

            if not caminho_csv_linux.exists():
                raise FileNotFoundError(f"CSV não encontrado para carga da stage: {caminho_csv_linux}")

            schema_csv = {col: pl.Utf8 for col in ORDEM_COLUNAS_SAIDA}

            df_stage = pl.read_csv(
                str(caminho_csv_linux),
                separator=";",
                encoding="utf8-lossy",
                infer_schema_length=0,
                schema_overrides=schema_csv,
                null_values=["", "null", "None"],
            )

            logger.info(
                "CSV carregado para stage. Arquivo: %s | Linhas: %s | Colunas: %s",
                caminho_csv_linux,
                df_stage.height,
                df_stage.width,
            )

            carregar_dataframe_stage_sql_server(df_stage, TABELA_STAGE)

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = int(df_stage.height)
            resumo.linhas_inseridas = int(df_stage.height)

            adicionar_validacao(
                resumo,
                nome="csv_relido_com_sucesso",
                status="ok",
                detalhe=f"O CSV técnico foi relido com {df_stage.height:,} linhas.",
            )
            adicionar_validacao(
                resumo,
                nome="stage_recarregada",
                status="ok",
                detalhe=f"A tabela {TABELA_STAGE} foi truncada e recarregada com sucesso.",
            )
            adicionar_observacao(
                resumo,
                "A amostra representa exatamente o conteúdo enviado para a stage técnica antes dos MERGEs de consolidação.",
            )

            definir_amostra(
                resumo,
                df_polars_para_amostra(
                    df_stage,
                    limite=5,
                    colunas=[
                        "DataLancamento",
                        "NumeroContrato",
                        "NumeroPrevia",
                        "CNPJ",
                        "MarcaExibida",
                        "CodPonto",
                        "CodFace",
                        "DataInicioPrevisto",
                        "DataTerminoPrevisto",
                    ],
                ),
                limite=10,
            )
            publicar_resumo_auditoria(resumo)

            return {
                "tabela_stage": TABELA_STAGE,
                "linhas_stage": int(df_stage.height),
            }
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            raise

    @task(task_id="merge_contratos")
    def merge_contratos() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="MERGE contratos consolidados",
            descricao_etapa=(
                "Consolida a visão agregada de contratos na tabela Silver.FatoControleContratosEuromidia, "
                "agrupando os registros por referência contratual e calculando totais, quantidades e "
                "atributos principais de negócio."
            ),
            sql_execucao=MERGE_CONTRATOS_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    NumeroContrato,
                    NumeroPrevia,
                    CNPJ,
                    RazaoSocial,
                    QuantidadePontos,
                    QuantidadeFaces,
                    TotalBrutoContrato,
                    TotalFaturamentoLiquidoMensal,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados=TABELA_STAGE,
            destino_dados="[Silver].[FatoControleContratosEuromidia]",
        )

    @task(task_id="merge_itens")
    def merge_itens() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="MERGE itens de contratos",
            descricao_etapa=(
                "Consolida a granularidade detalhada dos contratos por item, ponto e face, "
                "alimentando a tabela Silver.FatoControleContratosItensEuromidia."
            ),
            sql_execucao=MERGE_ITENS_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    NumeroContrato,
                    NumeroPrevia,
                    CNPJ,
                    CodPonto,
                    CodFace,
                    MarcaExibida,
                    DataInicioPrevisto,
                    DataTerminoPrevisto,
                    FaturamentoBrutoMensal,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosItensEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados=TABELA_STAGE,
            destino_dados="[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_fk_contratos_itens")
    def update_fk_contratos_itens() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar vínculo itens para contratos",
            descricao_etapa=(
                "Preenche a chave estrangeira dos itens para a tabela consolidada de contratos, "
                "garantindo integridade entre contrato agregado e seus itens detalhados."
            ),
            sql_execucao=UPDATE_FK_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    IDFatoControleContratoEuromidia,
                    NumeroContrato,
                    NumeroPrevia,
                    CodPonto,
                    CodFace,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosItensEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Silver].[FatoControleContratosEuromidia] + [Silver].[FatoControleContratosItensEuromidia]",
            destino_dados="[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_id_vendedor")
    def update_id_vendedor() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID do vendedor",
            descricao_etapa=(
                "Relaciona o nome do vendedor presente nos itens de contratos com a dimensão de vendedores, "
                "preenchendo o identificador técnico do vendedor."
            ),
            sql_execucao=UPDATE_VENDEDOR_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    Vendedor,
                    IDVendedor,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosItensEuromidia]
                WHERE IDVendedor IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Silver].[FatoControleContratosItensEuromidia] + [Integracao].[dbo].[Vendedores]",
            destino_dados="[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_id_empresa")
    def update_id_empresa() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID da empresa",
            descricao_etapa=(
                "Relaciona o CNPJ do contrato consolidado à dimensão de empresas para preenchimento do IDEmpresa."
            ),
            sql_execucao=UPDATE_EMPRESA_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    NumeroContrato,
                    NumeroPrevia,
                    CNPJ,
                    IDEmpresa,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosEuromidia]
                WHERE IDEmpresa IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Silver].[FatoControleContratosEuromidia] + [Integracao].[Silver].[DimEmpresas]",
            destino_dados="[Silver].[FatoControleContratosEuromidia]",
        )

    @task(task_id="update_id_painel")
    def update_id_painel() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID do painel",
            descricao_etapa=(
                "Relaciona o código do ponto dos itens contratuais à dimensão de painéis, "
                "preenchendo o IDPainelEuromidia."
            ),
            sql_execucao=UPDATE_PONTOS_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    CodPonto,
                    IDPainelEuromidia,
                    CodFace,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                WHERE IDPainelEuromidia IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia] + [Integracao].[Silver].[DimPaineisEuromidia]",
            destino_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_id_face")
    def update_id_face() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID da face",
            descricao_etapa=(
                "Relaciona o código da face presente nos itens contratuais à dimensão de faces de painéis."
            ),
            sql_execucao=UPDATE_FACES_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    CodFace,
                    IDDimFacesPaineis,
                    CodPonto,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                WHERE IDDimFacesPaineis IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia] + [Integracao].[Silver].[DimFacesPaineis]",
            destino_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="upsert_dim_calendario")
    def upsert_dim_calendario() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar dimensão calendário",
            descricao_etapa=(
                "Executa o procedimento de upsert da dimensão calendário, garantindo suporte temporal "
                "para análises baseadas em datas de contratos, vencimentos e ocupação."
            ),
            sql_execucao=CALL_PROCEDURE_SQL,
            sql_amostra="""
                SELECT TOP 5 *
                FROM [Silver].[DimCalendario]
                ORDER BY Data DESC
            """,
            origem_dados="Procedimento Silver.sp_UpsertDimCalendario",
            destino_dados="[Silver].[DimCalendario]",
        )

    @task(task_id="upsert_ocupacao")
    def upsert_ocupacao() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="MERGE ocupação dos painéis",
            descricao_etapa=(
                "Deriva a ocupação contratual dos painéis Euromídia a partir dos itens de contratos, "
                "calculando início, fim, status, cliente, vendedor e demais metadados operacionais."
            ),
            sql_execucao=UPDATE_OCUPACAO_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    CodPonto,
                    CodFace,
                    Origem,
                    Status,
                    DataInicio,
                    DataFim,
                    Cota,
                    MarcaExibida,
                    Vendedor,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia]",
            destino_dados="[Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]",
        )

    info_csv = gerar_csv_ctr()
    stage = carregar_stage(info_csv)
    contratos = merge_contratos()
    itens = merge_itens()
    fk = update_fk_contratos_itens()
    vendedor = update_id_vendedor()
    empresa = update_id_empresa()
    painel = update_id_painel()
    face = update_id_face()
    calendario = upsert_dim_calendario()
    ocupacao = upsert_ocupacao()

    info_csv >> stage >> contratos >> itens >> fk >> vendedor >> empresa >> painel >> face >> calendario >> ocupacao


dag = pipeline_controle_contratos_euromidia()