from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pendulum
from airflow.sdk import dag, task
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from dags._libs.auditoria_task import (
    adicionar_observacao,
    adicionar_validacao,
    criar_resumo_auditoria,
    definir_amostra,
    publicar_resumo_auditoria,
    registrar_erro_no_resumo,
)
from hooks.BancodeDados.SqlServer import HookSqlServer
from hooks.api.omie.omie import OmieHook


logger = logging.getLogger(__name__)


JSON_KWARGS = {
    "ensure_ascii": False,
    "separators": (",", ":"),
}

CONN_ID_SQL_SERVER = "mssql_integracao"

CONN_IDS_OMIE = [
    "omie_sp",
    "omie_sinamovel",
]

TIPOS_LANCAMENTO = ["CPCR", "BX", "CC"]

OVERLAP_DIAS = 3
REGISTROS_POR_PAGINA = 500
MAX_TENTATIVAS_DB = 5

PRIMEIRA_CARGA_DESDE = date(2020, 1, 1)
JANELA_DIAS = 30


def obter_engine_sql() -> Engine:
    """Obtém a engine do SQL Server via hook."""
    hook_sql = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
    return hook_sql.obter_engine()


def normalizar_valor_para_auditoria(valor: Any) -> Any:
    """Normaliza valores para exibição amigável na auditoria."""
    if valor is None:
        return None

    if isinstance(valor, (datetime, date, dtime)):
        return str(valor)

    if isinstance(valor, Decimal):
        return str(valor)

    if isinstance(valor, bytes):
        try:
            return valor.decode("utf-8")
        except Exception:
            return str(valor)

    try:
        import numpy as np

        if isinstance(valor, np.integer):
            return int(valor)
        if isinstance(valor, np.floating):
            return float(valor)
        if isinstance(valor, np.bool_):
            return bool(valor)
    except Exception:
        pass

    try:
        import pandas as pd

        if pd.isna(valor):
            return None
    except Exception:
        pass

    return valor


def limitar_amostra(lista_linhas: List[Dict[str, Any]], limite: int = 10) -> List[Dict[str, Any]]:
    """Limita e normaliza amostra para exibição no painel."""
    amostra: List[Dict[str, Any]] = []

    for linha in lista_linhas[:limite]:
        amostra.append(
            {
                chave: normalizar_valor_para_auditoria(valor)
                for chave, valor in linha.items()
            }
        )

    return amostra


def consultar_amostra_sql(
    engine: Engine,
    sql: str,
    parametros: Optional[Dict[str, Any]] = None,
    limite: int = 5,
) -> List[Dict[str, Any]]:
    """Consulta amostra simples no SQL para exibição na auditoria."""
    with engine.begin() as conn:
        resultado = conn.execute(text(sql), parametros or {})
        linhas = resultado.mappings().fetchall()

    return limitar_amostra([dict(linha) for linha in linhas], limite=limite)


def parse_data_ddmmaaaa(valor: Optional[str]) -> Optional[date]:
    """Converte string dd/mm/aaaa em date."""
    if not valor:
        return None
    return datetime.strptime(valor, "%d/%m/%Y").date()


def parse_hora_hhmmss(valor: Optional[str]) -> Optional[dtime]:
    """Converte string hh:mm ou hh:mm:ss em time."""
    if not valor:
        return None
    fmt = "%H:%M:%S" if valor.count(":") == 2 else "%H:%M"
    return datetime.strptime(valor, fmt).time()


def combinar_data_hora(d: Optional[date], h: Optional[dtime]) -> Optional[datetime]:
    """Combina date e time em datetime."""
    if d is None:
        return None
    if h is None:
        return datetime(d.year, d.month, d.day, 0, 0, 0)
    return datetime(d.year, d.month, d.day, h.hour, h.minute, h.second)


ESCALA_DECIMAL_4 = Decimal("0.0001")


def _quantizar_decimal_4(d: Decimal) -> Optional[Decimal]:
    """Arredonda Decimal para 4 casas."""
    try:
        return d.quantize(ESCALA_DECIMAL_4, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def to_int(v: Any) -> Optional[int]:
    """Converte valores diversos para int."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, (float, Decimal)):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        neg = s.startswith("-")
        s = "".join(ch for ch in s if ch.isdigit())
        if not s:
            return None
        n = int(s)
        return -n if neg else n
    return None


def to_decimal(v: Any) -> Optional[Decimal]:
    """Converte valores diversos para Decimal."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None

        s = s.replace("R$", "").replace(" ", "")

        if "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", ".")

        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    return None


def to_decimal_18_4(v: Any) -> Optional[Decimal]:
    """Converte para Decimal(18,4) respeitando limite."""
    d = to_decimal(v)
    if d is None:
        return None

    d = _quantizar_decimal_4(d)
    if d is None:
        return None

    limite = Decimal("99999999999999.9999")
    if d.copy_abs() > limite:
        return None

    return d


DDL_WATERMARK = """
IF OBJECT_ID('dbo.MovFinWatermark', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.MovFinWatermark
    (
        IDEmpresaProprietaria INT NOT NULL,
        TipoLancamento VARCHAR(10) NOT NULL,
        UltimaDataHora DATETIME2(0) NOT NULL,
        DataHoraAtualizacao DATETIME2(0) NOT NULL CONSTRAINT DF_MovFinWatermark_DataHora DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_MovFinWatermark PRIMARY KEY (IDEmpresaProprietaria, TipoLancamento)
    );
END
"""


def garantir_tabela_watermark(conn) -> None:
    """Garante a existência da tabela de watermark."""
    conn.execute(text(DDL_WATERMARK))


def obter_watermark(conn, id_empresa: int, tipo: str) -> datetime:
    """Busca watermark atual por empresa e tipo."""
    sql = text(
        """
        SELECT UltimaDataHora
        FROM dbo.MovFinWatermark
        WHERE IDEmpresaProprietaria = :id_empresa AND TipoLancamento = :tipo
        """
    )
    row = conn.execute(sql, {"id_empresa": id_empresa, "tipo": tipo}).fetchone()
    if row and row[0]:
        return row[0]

    return datetime(
        PRIMEIRA_CARGA_DESDE.year,
        PRIMEIRA_CARGA_DESDE.month,
        PRIMEIRA_CARGA_DESDE.day,
        0,
        0,
        0,
    )


def atualizar_watermark(conn, id_empresa: int, tipo: str, nova_datahora: datetime) -> None:
    """Atualiza watermark por empresa e tipo."""
    sql = text(
        """
        MERGE dbo.MovFinWatermark AS tgt
        USING (
            SELECT
                :id_empresa AS IDEmpresaProprietaria,
                :tipo AS TipoLancamento,
                :dt AS UltimaDataHora
        ) AS src
        ON (
            tgt.IDEmpresaProprietaria = src.IDEmpresaProprietaria
            AND tgt.TipoLancamento = src.TipoLancamento
        )
        WHEN MATCHED THEN
            UPDATE SET
                UltimaDataHora = src.UltimaDataHora,
                DataHoraAtualizacao = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN
            INSERT (
                IDEmpresaProprietaria,
                TipoLancamento,
                UltimaDataHora
            )
            VALUES (
                src.IDEmpresaProprietaria,
                src.TipoLancamento,
                src.UltimaDataHora
            );
        """
    )
    conn.execute(
        sql,
        {
            "id_empresa": id_empresa,
            "tipo": tipo,
            "dt": nova_datahora,
        },
    )


def montar_tipo_evento_e_chaves(
    id_empresa: int,
    tipo_api: str,
    nCodBaixa: Optional[int],
    nCodMovCC: Optional[int],
    nCodTitulo: Optional[int],
    cGrupo: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Monta TipoLancamento, EventKey e DocumentoKey."""
    grupo = (cGrupo or "").strip()
    tipo_lancamento = (tipo_api or "").strip() or None

    if nCodBaixa is not None:
        event_key = f"{id_empresa}|BX|{nCodBaixa}"
    elif nCodMovCC is not None:
        event_key = f"{id_empresa}|CC|{nCodMovCC}"
    elif nCodTitulo is not None:
        event_key = f"{id_empresa}|{tipo_api}|{grupo}|{nCodTitulo}"
    else:
        event_key = None

    documento_key = None
    if nCodTitulo is not None:
        documento_key = f"{id_empresa}|{grupo}|TIT|{nCodTitulo}"

    return tipo_lancamento, event_key, documento_key


COLUNAS_TABELA = [
    "IDEmpresaProprietaria",
    "TipoLancamento",
    "EventKey",
    "DocumentoKey",
    "nPagina",
    "nTotPaginas",
    "nRegistros",
    "nTotRegistros",
    "nCodTitulo",
    "cCodIntTitulo",
    "cNumTitulo",
    "dDtEmissao",
    "dDtVenc",
    "dDtPrevisao",
    "dDtPagamento",
    "nCodCliente",
    "cCPFCNPJCliente",
    "nCodCtr",
    "cNumCtr",
    "nCodOS",
    "cNumOS",
    "nCodCC",
    "cStatus",
    "cNatureza",
    "cTipo",
    "cOperacao",
    "cNumDocFiscal",
    "cCodCateg",
    "cNumParcela",
    "nValorTitulo",
    "nValorPIS",
    "cRetPIS",
    "nValorCOFINS",
    "cRetCOFINS",
    "nValorCSLL",
    "cRetCSLL",
    "nValorIR",
    "cRetIR",
    "nValorISS",
    "cRetISS",
    "nValorINSS",
    "cRetINSS",
    "nCodProjeto",
    "observacao",
    "cCodVendedor",
    "nCodComprador",
    "cCodigoBarras",
    "cNSU",
    "nCodNF",
    "dDtRegistro",
    "cNumBoleto",
    "cChaveNFe",
    "cOrigem",
    "nCodTitRepet",
    "cGrupo",
    "nCodMovCC",
    "nValorMovCC",
    "nCodMovCCRepet",
    "nCodBaixa",
    "dDtCredito",
    "dDtConcilia",
    "cHrConcilia",
    "cUsConcilia",
    "dDtInc",
    "cHrInc",
    "cUsInc",
    "dDtAlt",
    "cHrAlt",
    "cUsAlt",
    "cLiquidado",
    "nValPago",
    "nValAberto",
    "nDesconto",
    "nJuros",
    "nMulta",
    "nValLiquido",
    "CategoriasJson",
    "DepartamentosJson",
    "MovimentoJson",
]

DEC_COLS = {
    "nValorTitulo",
    "nValorPIS",
    "nValorCOFINS",
    "nValorCSLL",
    "nValorIR",
    "nValorISS",
    "nValorINSS",
    "nValorMovCC",
    "nValPago",
    "nValAberto",
    "nDesconto",
    "nJuros",
    "nMulta",
    "nValLiquido",
}

INT_COLS = {
    "IDEmpresaProprietaria",
    "nPagina",
    "nTotPaginas",
    "nRegistros",
    "nTotRegistros",
    "nCodTitulo",
    "nCodCliente",
    "nCodCtr",
    "nCodOS",
    "nCodCC",
    "nCodProjeto",
    "cCodVendedor",
    "nCodComprador",
    "nCodNF",
    "nCodTitRepet",
    "nCodMovCC",
    "nCodMovCCRepet",
    "nCodBaixa",
}


def _normalizar_tipos(linha: Dict[str, Any]) -> None:
    """Normaliza tipos numéricos da linha."""
    for c in DEC_COLS:
        linha[c] = to_decimal_18_4(linha.get(c))
    for c in INT_COLS:
        linha[c] = to_int(linha.get(c))


def achatar_movimento(
    id_empresa: int,
    meta: Dict[str, Any],
    movimento: Dict[str, Any],
    tipo_lancamento_api: str,
) -> Dict[str, Any]:
    """Achata o JSON de movimento da Omie em uma linha tabular."""
    detalhes = movimento.get("detalhes", {}) or {}
    resumo = movimento.get("resumo", {}) or {}
    categorias = movimento.get("categorias", None)
    departamentos = movimento.get("departamentos", None)

    linha: Dict[str, Any] = {}

    linha["IDEmpresaProprietaria"] = id_empresa
    linha["nPagina"] = meta.get("nPagina")
    linha["nTotPaginas"] = meta.get("nTotPaginas")
    linha["nRegistros"] = meta.get("nRegistros")
    linha["nTotRegistros"] = meta.get("nTotRegistros")

    linha["nCodTitulo"] = detalhes.get("nCodTitulo")
    linha["cCodIntTitulo"] = detalhes.get("cCodIntTitulo")
    linha["cNumTitulo"] = detalhes.get("cNumTitulo")

    linha["dDtEmissao"] = parse_data_ddmmaaaa(detalhes.get("dDtEmissao"))
    linha["dDtVenc"] = parse_data_ddmmaaaa(detalhes.get("dDtVenc"))
    linha["dDtPrevisao"] = parse_data_ddmmaaaa(detalhes.get("dDtPrevisao"))
    linha["dDtPagamento"] = parse_data_ddmmaaaa(detalhes.get("dDtPagamento"))

    linha["nCodCliente"] = detalhes.get("nCodCliente")
    linha["cCPFCNPJCliente"] = detalhes.get("cCPFCNPJCliente")

    linha["nCodCtr"] = detalhes.get("nCodCtr")
    linha["cNumCtr"] = detalhes.get("cNumCtr")

    linha["nCodOS"] = detalhes.get("nCodOS")
    linha["cNumOS"] = detalhes.get("cNumOS")

    linha["nCodCC"] = detalhes.get("nCodCC")

    linha["cStatus"] = detalhes.get("cStatus")
    linha["cNatureza"] = detalhes.get("cNatureza")
    linha["cTipo"] = detalhes.get("cTipo")
    linha["cOperacao"] = detalhes.get("cOperacao")

    linha["cNumDocFiscal"] = detalhes.get("cNumDocFiscal")
    linha["cCodCateg"] = detalhes.get("cCodCateg")

    linha["cNumParcela"] = detalhes.get("cNumParcela")
    linha["nValorTitulo"] = detalhes.get("nValorTitulo")

    linha["nValorPIS"] = detalhes.get("nValorPIS")
    linha["cRetPIS"] = detalhes.get("cRetPIS")

    linha["nValorCOFINS"] = detalhes.get("nValorCOFINS")
    linha["cRetCOFINS"] = detalhes.get("cRetCOFINS")

    linha["nValorCSLL"] = detalhes.get("nValorCSLL")
    linha["cRetCSLL"] = detalhes.get("cRetCSLL")

    linha["nValorIR"] = detalhes.get("nValorIR")
    linha["cRetIR"] = detalhes.get("cRetIR")

    linha["nValorISS"] = detalhes.get("nValorISS")
    linha["cRetISS"] = detalhes.get("cRetISS")

    linha["nValorINSS"] = detalhes.get("nValorINSS")
    linha["cRetINSS"] = detalhes.get("cRetINSS")

    linha["nCodProjeto"] = detalhes.get("nCodProjeto")
    linha["observacao"] = detalhes.get("observacao")

    linha["cCodVendedor"] = detalhes.get("cCodVendedor")
    linha["nCodComprador"] = detalhes.get("nCodComprador")

    linha["cCodigoBarras"] = detalhes.get("cCodigoBarras")
    linha["cNSU"] = detalhes.get("cNSU")

    linha["nCodNF"] = detalhes.get("nCodNF")
    linha["dDtRegistro"] = parse_data_ddmmaaaa(detalhes.get("dDtRegistro"))

    linha["cNumBoleto"] = detalhes.get("cNumBoleto")
    linha["cChaveNFe"] = detalhes.get("cChaveNFe")

    linha["cOrigem"] = detalhes.get("cOrigem")

    linha["nCodTitRepet"] = detalhes.get("nCodTitRepet")
    linha["cGrupo"] = detalhes.get("cGrupo")

    linha["nCodMovCC"] = detalhes.get("nCodMovCC")
    linha["nValorMovCC"] = detalhes.get("nValorMovCC")
    linha["nCodMovCCRepet"] = detalhes.get("nCodMovCCRepet")

    linha["nCodBaixa"] = detalhes.get("nCodBaixa")

    linha["dDtCredito"] = parse_data_ddmmaaaa(detalhes.get("dDtCredito"))

    linha["dDtConcilia"] = parse_data_ddmmaaaa(detalhes.get("dDtConcilia"))
    linha["cHrConcilia"] = parse_hora_hhmmss(detalhes.get("cHrConcilia"))
    linha["cUsConcilia"] = detalhes.get("cUsConcilia")

    linha["dDtInc"] = parse_data_ddmmaaaa(detalhes.get("dDtInc"))
    linha["cHrInc"] = parse_hora_hhmmss(detalhes.get("cHrInc"))
    linha["cUsInc"] = detalhes.get("cUsInc")

    linha["dDtAlt"] = parse_data_ddmmaaaa(detalhes.get("dDtAlt"))
    linha["cHrAlt"] = parse_hora_hhmmss(detalhes.get("cHrAlt"))
    linha["cUsAlt"] = detalhes.get("cUsAlt")

    linha["cLiquidado"] = resumo.get("cLiquidado")
    linha["nValPago"] = resumo.get("nValPago")
    linha["nValAberto"] = resumo.get("nValAberto")
    linha["nDesconto"] = resumo.get("nDesconto")
    linha["nJuros"] = resumo.get("nJuros")
    linha["nMulta"] = resumo.get("nMulta")
    linha["nValLiquido"] = resumo.get("nValLiquido")

    linha["CategoriasJson"] = (
        json.dumps(categorias, **JSON_KWARGS) if categorias is not None else None
    )
    linha["DepartamentosJson"] = (
        json.dumps(departamentos, **JSON_KWARGS) if departamentos is not None else None
    )
    linha["MovimentoJson"] = json.dumps(movimento, **JSON_KWARGS)

    _normalizar_tipos(linha)

    tipo_lanc, event_key, doc_key = montar_tipo_evento_e_chaves(
        id_empresa=id_empresa,
        tipo_api=tipo_lancamento_api,
        nCodBaixa=linha.get("nCodBaixa"),
        nCodMovCC=linha.get("nCodMovCC"),
        nCodTitulo=linha.get("nCodTitulo"),
        cGrupo=linha.get("cGrupo"),
    )
    linha["TipoLancamento"] = tipo_lanc
    linha["EventKey"] = event_key
    linha["DocumentoKey"] = doc_key

    return linha


def montar_amostra_movimento_raw(linha: Dict[str, Any]) -> Dict[str, Any]:
    """Monta amostra enxuta e legível de um evento financeiro raw."""
    return {
        "IDEmpresaProprietaria": linha.get("IDEmpresaProprietaria"),
        "TipoLancamento": linha.get("TipoLancamento"),
        "EventKey": linha.get("EventKey"),
        "DocumentoKey": linha.get("DocumentoKey"),
        "nCodTitulo": linha.get("nCodTitulo"),
        "nCodBaixa": linha.get("nCodBaixa"),
        "nCodMovCC": linha.get("nCodMovCC"),
        "cGrupo": linha.get("cGrupo"),
        "cNatureza": linha.get("cNatureza"),
        "cStatus": linha.get("cStatus"),
        "dDtVenc": linha.get("dDtVenc"),
        "dDtPagamento": linha.get("dDtPagamento"),
        "nValorTitulo": linha.get("nValorTitulo"),
        "nValPago": linha.get("nValPago"),
        "nValAberto": linha.get("nValAberto"),
        "dDtAlt": linha.get("dDtAlt"),
        "cHrAlt": linha.get("cHrAlt"),
    }


def extrair_momento_alteracao(linha: Dict[str, Any]) -> Optional[datetime]:
    """Extrai o melhor datetime de alteração disponível."""
    dt_alt = combinar_data_hora(linha.get("dDtAlt"), linha.get("cHrAlt"))
    if dt_alt:
        return dt_alt
    return combinar_data_hora(linha.get("dDtInc"), linha.get("cHrInc"))


def upsert_pagina(conn, linhas: List[Dict[str, Any]]) -> None:
    """Carrega página em tabela stage temporária e aplica MERGE na raw."""
    if not linhas:
        return

    linhas_validas = [l for l in linhas if l.get("EventKey")]
    if not linhas_validas:
        return

    conn.execute(
        text(
            """
            IF OBJECT_ID('tempdb..#MovFinStage') IS NOT NULL DROP TABLE #MovFinStage;

            CREATE TABLE #MovFinStage
            (
                IDEmpresaProprietaria INT NOT NULL,

                TipoLancamento VARCHAR(10) COLLATE DATABASE_DEFAULT NULL,
                EventKey VARCHAR(200) COLLATE DATABASE_DEFAULT NULL,
                DocumentoKey VARCHAR(200) COLLATE DATABASE_DEFAULT NULL,

                nPagina INT NULL,
                nTotPaginas INT NULL,
                nRegistros INT NULL,
                nTotRegistros INT NULL,

                nCodTitulo BIGINT NULL,
                cCodIntTitulo VARCHAR(80) COLLATE DATABASE_DEFAULT NULL,
                cNumTitulo VARCHAR(40) COLLATE DATABASE_DEFAULT NULL,

                dDtEmissao DATE NULL,
                dDtVenc DATE NULL,
                dDtPrevisao DATE NULL,
                dDtPagamento DATE NULL,

                nCodCliente BIGINT NULL,
                cCPFCNPJCliente VARCHAR(30) COLLATE DATABASE_DEFAULT NULL,

                nCodCtr BIGINT NULL,
                cNumCtr VARCHAR(30) COLLATE DATABASE_DEFAULT NULL,

                nCodOS BIGINT NULL,
                cNumOS VARCHAR(20) COLLATE DATABASE_DEFAULT NULL,

                nCodCC BIGINT NULL,

                cStatus VARCHAR(120) COLLATE DATABASE_DEFAULT NULL,
                cNatureza CHAR(1) COLLATE DATABASE_DEFAULT NULL,
                cTipo VARCHAR(20) COLLATE DATABASE_DEFAULT NULL,
                cOperacao VARCHAR(10) COLLATE DATABASE_DEFAULT NULL,

                cNumDocFiscal VARCHAR(40) COLLATE DATABASE_DEFAULT NULL,
                cCodCateg VARCHAR(40) COLLATE DATABASE_DEFAULT NULL,

                cNumParcela VARCHAR(15) COLLATE DATABASE_DEFAULT NULL,

                nValorTitulo DECIMAL(18,4) NULL,

                nValorPIS DECIMAL(18,4) NULL,
                cRetPIS CHAR(1) COLLATE DATABASE_DEFAULT NULL,

                nValorCOFINS DECIMAL(18,4) NULL,
                cRetCOFINS CHAR(1) COLLATE DATABASE_DEFAULT NULL,

                nValorCSLL DECIMAL(18,4) NULL,
                cRetCSLL CHAR(1) COLLATE DATABASE_DEFAULT NULL,

                nValorIR DECIMAL(18,4) NULL,
                cRetIR CHAR(1) COLLATE DATABASE_DEFAULT NULL,

                nValorISS DECIMAL(18,4) NULL,
                cRetISS CHAR(1) COLLATE DATABASE_DEFAULT NULL,

                nValorINSS DECIMAL(18,4) NULL,
                cRetINSS CHAR(1) COLLATE DATABASE_DEFAULT NULL,

                nCodProjeto BIGINT NULL,
                observacao VARCHAR(MAX) COLLATE DATABASE_DEFAULT NULL,

                cCodVendedor BIGINT NULL,
                nCodComprador BIGINT NULL,

                cCodigoBarras VARCHAR(100) COLLATE DATABASE_DEFAULT NULL,
                cNSU VARCHAR(120) COLLATE DATABASE_DEFAULT NULL,

                nCodNF BIGINT NULL,
                dDtRegistro DATE NULL,

                cNumBoleto VARCHAR(50) COLLATE DATABASE_DEFAULT NULL,
                cChaveNFe VARCHAR(60) COLLATE DATABASE_DEFAULT NULL,

                cOrigem VARCHAR(10) COLLATE DATABASE_DEFAULT NULL,

                nCodTitRepet BIGINT NULL,
                cGrupo VARCHAR(40) COLLATE DATABASE_DEFAULT NULL,

                nCodMovCC BIGINT NULL,
                nValorMovCC DECIMAL(18,4) NULL,
                nCodMovCCRepet BIGINT NULL,

                nCodBaixa BIGINT NULL,

                dDtCredito DATE NULL,

                dDtConcilia DATE NULL,
                cHrConcilia TIME(0) NULL,
                cUsConcilia VARCHAR(30) COLLATE DATABASE_DEFAULT NULL,

                dDtInc DATE NULL,
                cHrInc TIME(0) NULL,
                cUsInc VARCHAR(30) COLLATE DATABASE_DEFAULT NULL,

                dDtAlt DATE NULL,
                cHrAlt TIME(0) NULL,
                cUsAlt VARCHAR(30) COLLATE DATABASE_DEFAULT NULL,

                cLiquidado CHAR(1) COLLATE DATABASE_DEFAULT NULL,
                nValPago DECIMAL(18,4) NULL,
                nValAberto DECIMAL(18,4) NULL,
                nDesconto DECIMAL(18,4) NULL,
                nJuros DECIMAL(18,4) NULL,
                nMulta DECIMAL(18,4) NULL,
                nValLiquido DECIMAL(18,4) NULL,

                CategoriasJson NVARCHAR(MAX) COLLATE DATABASE_DEFAULT NULL,
                DepartamentosJson NVARCHAR(MAX) COLLATE DATABASE_DEFAULT NULL,
                MovimentoJson NVARCHAR(MAX) COLLATE DATABASE_DEFAULT NULL
            );
            """
        )
    )

    import pyodbc

    raw_conn = conn.connection
    cur = raw_conn.cursor()
    cur.fast_executemany = True

    cols = COLUNAS_TABELA
    placeholders = ",".join(["?"] * len(cols))
    insert_sql = f"INSERT INTO #MovFinStage ({','.join(cols)}) VALUES ({placeholders})"
    valores: List[Tuple[Any, ...]] = [tuple(l.get(c) for c in cols) for l in linhas_validas]

    VARCHAR_BIND_MAX = 8000
    NVARCHAR_BIND_MAX = 4000

    TYPE_BY_COL: Dict[str, Tuple[Any, ...]] = {
        "IDEmpresaProprietaria": (pyodbc.SQL_INTEGER,),
        "nPagina": (pyodbc.SQL_INTEGER,),
        "nTotPaginas": (pyodbc.SQL_INTEGER,),
        "nRegistros": (pyodbc.SQL_INTEGER,),
        "nTotRegistros": (pyodbc.SQL_INTEGER,),
        "nCodTitulo": (pyodbc.SQL_BIGINT,),
        "nCodCliente": (pyodbc.SQL_BIGINT,),
        "nCodCtr": (pyodbc.SQL_BIGINT,),
        "nCodOS": (pyodbc.SQL_BIGINT,),
        "nCodCC": (pyodbc.SQL_BIGINT,),
        "nCodProjeto": (pyodbc.SQL_BIGINT,),
        "cCodVendedor": (pyodbc.SQL_BIGINT,),
        "nCodComprador": (pyodbc.SQL_BIGINT,),
        "nCodNF": (pyodbc.SQL_BIGINT,),
        "nCodTitRepet": (pyodbc.SQL_BIGINT,),
        "nCodMovCC": (pyodbc.SQL_BIGINT,),
        "nCodMovCCRepet": (pyodbc.SQL_BIGINT,),
        "nCodBaixa": (pyodbc.SQL_BIGINT,),
        "dDtEmissao": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtVenc": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtPrevisao": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtPagamento": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtRegistro": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtCredito": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtConcilia": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtInc": (pyodbc.SQL_TYPE_DATE, 10),
        "dDtAlt": (pyodbc.SQL_TYPE_DATE, 10),
        "cHrConcilia": (pyodbc.SQL_TYPE_TIME, 8, 0),
        "cHrInc": (pyodbc.SQL_TYPE_TIME, 8, 0),
        "cHrAlt": (pyodbc.SQL_TYPE_TIME, 8, 0),
        "nValorTitulo": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValorPIS": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValorCOFINS": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValorCSLL": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValorIR": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValorISS": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValorINSS": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValorMovCC": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValPago": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValAberto": (pyodbc.SQL_DECIMAL, 18, 4),
        "nDesconto": (pyodbc.SQL_DECIMAL, 18, 4),
        "nJuros": (pyodbc.SQL_DECIMAL, 18, 4),
        "nMulta": (pyodbc.SQL_DECIMAL, 18, 4),
        "nValLiquido": (pyodbc.SQL_DECIMAL, 18, 4),
        "cCodIntTitulo": (pyodbc.SQL_VARCHAR, 80),
        "cNumTitulo": (pyodbc.SQL_VARCHAR, 40),
        "cCPFCNPJCliente": (pyodbc.SQL_VARCHAR, 30),
        "cNumCtr": (pyodbc.SQL_VARCHAR, 30),
        "cNumOS": (pyodbc.SQL_VARCHAR, 20),
        "cStatus": (pyodbc.SQL_VARCHAR, 120),
        "cTipo": (pyodbc.SQL_VARCHAR, 20),
        "cOperacao": (pyodbc.SQL_VARCHAR, 10),
        "cNumDocFiscal": (pyodbc.SQL_VARCHAR, 40),
        "cCodCateg": (pyodbc.SQL_VARCHAR, 40),
        "cNumParcela": (pyodbc.SQL_VARCHAR, 15),
        "cCodigoBarras": (pyodbc.SQL_VARCHAR, 100),
        "cNSU": (pyodbc.SQL_VARCHAR, 120),
        "cNumBoleto": (pyodbc.SQL_VARCHAR, 50),
        "cChaveNFe": (pyodbc.SQL_VARCHAR, 60),
        "cOrigem": (pyodbc.SQL_VARCHAR, 10),
        "cGrupo": (pyodbc.SQL_VARCHAR, 40),
        "cUsConcilia": (pyodbc.SQL_VARCHAR, 30),
        "cUsInc": (pyodbc.SQL_VARCHAR, 30),
        "cUsAlt": (pyodbc.SQL_VARCHAR, 30),
        "cNatureza": (pyodbc.SQL_CHAR, 1),
        "cRetPIS": (pyodbc.SQL_CHAR, 1),
        "cRetCOFINS": (pyodbc.SQL_CHAR, 1),
        "cRetCSLL": (pyodbc.SQL_CHAR, 1),
        "cRetIR": (pyodbc.SQL_CHAR, 1),
        "cRetISS": (pyodbc.SQL_CHAR, 1),
        "cRetINSS": (pyodbc.SQL_CHAR, 1),
        "cLiquidado": (pyodbc.SQL_CHAR, 1),
        "observacao": (pyodbc.SQL_LONGVARCHAR, VARCHAR_BIND_MAX),
        "CategoriasJson": (pyodbc.SQL_WLONGVARCHAR, NVARCHAR_BIND_MAX),
        "DepartamentosJson": (pyodbc.SQL_WLONGVARCHAR, NVARCHAR_BIND_MAX),
        "MovimentoJson": (pyodbc.SQL_WLONGVARCHAR, NVARCHAR_BIND_MAX),
        "TipoLancamento": (pyodbc.SQL_VARCHAR, 10),
        "EventKey": (pyodbc.SQL_VARCHAR, 200),
        "DocumentoKey": (pyodbc.SQL_VARCHAR, 200),
    }

    sizes = []
    for c in cols:
        t = TYPE_BY_COL.get(c)
        if t is None:
            raise RuntimeError(f"Coluna sem tipo no setinputsizes: {c}")
        sizes.append(t)

    cur.setinputsizes(sizes)

    def _debug_param_map(row: Tuple[Any, ...]) -> None:
        for idx, (col, val) in enumerate(zip(cols, row), start=1):
            tp = type(val).__name__
            if isinstance(val, str):
                vv = f"str(len={len(val)})"
            else:
                vv = repr(val)
            logger.error("    [%02d] %s: %s = %s", idx, col, tp, vv)

    try:
        cur.executemany(insert_sql, valores)
    except Exception as e:
        for i, row in enumerate(valores):
            try:
                cur.execute(insert_sql, row)
            except Exception as e2:
                linha_debug = linhas_validas[i]
                logger.error(">>> ERRO NA LINHA i = %s", i)
                logger.error(">>> insert_sql: %s", insert_sql)
                logger.error(
                    ">>> Chaves: %s",
                    {
                        "IDEmpresaProprietaria": linha_debug.get("IDEmpresaProprietaria"),
                        "TipoLancamento": linha_debug.get("TipoLancamento"),
                        "EventKey": linha_debug.get("EventKey"),
                        "DocumentoKey": linha_debug.get("DocumentoKey"),
                        "nCodTitulo": linha_debug.get("nCodTitulo"),
                        "nCodBaixa": linha_debug.get("nCodBaixa"),
                        "nCodMovCC": linha_debug.get("nCodMovCC"),
                        "cGrupo": linha_debug.get("cGrupo"),
                    },
                )
                logger.error(">>> MAPEAMENTO DE PARAMETROS (ordem do INSERT):")
                _debug_param_map(row)
                raise e2
        raise e
    finally:
        cur.close()

    conn.execute(
        text(
            """
            MERGE dbo.MovimentacaoFinanceiro AS tgt
            USING #MovFinStage AS src
              ON tgt.IDEmpresaProprietaria = src.IDEmpresaProprietaria
             AND tgt.EventKey = src.EventKey
            WHEN MATCHED THEN
              UPDATE SET
                tgt.TipoLancamento = src.TipoLancamento,
                tgt.EventKey = src.EventKey,
                tgt.DocumentoKey = src.DocumentoKey,
                tgt.nPagina = src.nPagina,
                tgt.nTotPaginas = src.nTotPaginas,
                tgt.nRegistros = src.nRegistros,
                tgt.nTotRegistros = src.nTotRegistros,
                tgt.nCodTitulo = src.nCodTitulo,
                tgt.cCodIntTitulo = src.cCodIntTitulo,
                tgt.cNumTitulo = src.cNumTitulo,
                tgt.dDtEmissao = src.dDtEmissao,
                tgt.dDtVenc = src.dDtVenc,
                tgt.dDtPrevisao = src.dDtPrevisao,
                tgt.dDtPagamento = src.dDtPagamento,
                tgt.nCodCliente = src.nCodCliente,
                tgt.cCPFCNPJCliente = src.cCPFCNPJCliente,
                tgt.nCodCtr = src.nCodCtr,
                tgt.cNumCtr = src.cNumCtr,
                tgt.nCodOS = src.nCodOS,
                tgt.cNumOS = src.cNumOS,
                tgt.nCodCC = src.nCodCC,
                tgt.cStatus = src.cStatus,
                tgt.cNatureza = src.cNatureza,
                tgt.cTipo = src.cTipo,
                tgt.cOperacao = src.cOperacao,
                tgt.cNumDocFiscal = src.cNumDocFiscal,
                tgt.cCodCateg = src.cCodCateg,
                tgt.cNumParcela = src.cNumParcela,
                tgt.nValorTitulo = src.nValorTitulo,
                tgt.nValorPIS = src.nValorPIS,
                tgt.cRetPIS = src.cRetPIS,
                tgt.nValorCOFINS = src.nValorCOFINS,
                tgt.cRetCOFINS = src.cRetCOFINS,
                tgt.nValorCSLL = src.nValorCSLL,
                tgt.cRetCSLL = src.cRetCSLL,
                tgt.nValorIR = src.nValorIR,
                tgt.cRetIR = src.cRetIR,
                tgt.nValorISS = src.nValorISS,
                tgt.cRetISS = src.cRetISS,
                tgt.nValorINSS = src.nValorINSS,
                tgt.cRetINSS = src.cRetINSS,
                tgt.nCodProjeto = src.nCodProjeto,
                tgt.observacao = src.observacao,
                tgt.cCodVendedor = src.cCodVendedor,
                tgt.nCodComprador = src.nCodComprador,
                tgt.cCodigoBarras = src.cCodigoBarras,
                tgt.cNSU = src.cNSU,
                tgt.nCodNF = src.nCodNF,
                tgt.dDtRegistro = src.dDtRegistro,
                tgt.cNumBoleto = src.cNumBoleto,
                tgt.cChaveNFe = src.cChaveNFe,
                tgt.cOrigem = src.cOrigem,
                tgt.nCodTitRepet = src.nCodTitRepet,
                tgt.cGrupo = src.cGrupo,
                tgt.nCodMovCC = src.nCodMovCC,
                tgt.nValorMovCC = src.nValorMovCC,
                tgt.nCodMovCCRepet = src.nCodMovCCRepet,
                tgt.nCodBaixa = src.nCodBaixa,
                tgt.dDtCredito = src.dDtCredito,
                tgt.dDtConcilia = src.dDtConcilia,
                tgt.cHrConcilia = src.cHrConcilia,
                tgt.cUsConcilia = src.cUsConcilia,
                tgt.dDtInc = src.dDtInc,
                tgt.cHrInc = src.cHrInc,
                tgt.cUsInc = src.cUsInc,
                tgt.dDtAlt = src.dDtAlt,
                tgt.cHrAlt = src.cHrAlt,
                tgt.cUsAlt = src.cUsAlt,
                tgt.cLiquidado = src.cLiquidado,
                tgt.nValPago = src.nValPago,
                tgt.nValAberto = src.nValAberto,
                tgt.nDesconto = src.nDesconto,
                tgt.nJuros = src.nJuros,
                tgt.nMulta = src.nMulta,
                tgt.nValLiquido = src.nValLiquido,
                tgt.CategoriasJson = src.CategoriasJson,
                tgt.DepartamentosJson = src.DepartamentosJson,
                tgt.MovimentoJson = src.MovimentoJson,
                tgt.DataHoraCarga = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
              INSERT (
                IDEmpresaProprietaria,
                TipoLancamento,
                EventKey,
                DocumentoKey,
                nPagina,
                nTotPaginas,
                nRegistros,
                nTotRegistros,
                nCodTitulo,
                cCodIntTitulo,
                cNumTitulo,
                dDtEmissao,
                dDtVenc,
                dDtPrevisao,
                dDtPagamento,
                nCodCliente,
                cCPFCNPJCliente,
                nCodCtr,
                cNumCtr,
                nCodOS,
                cNumOS,
                nCodCC,
                cStatus,
                cNatureza,
                cTipo,
                cOperacao,
                cNumDocFiscal,
                cCodCateg,
                cNumParcela,
                nValorTitulo,
                nValorPIS,
                cRetPIS,
                nValorCOFINS,
                cRetCOFINS,
                nValorCSLL,
                cRetCSLL,
                nValorIR,
                cRetIR,
                nValorISS,
                cRetISS,
                nValorINSS,
                cRetINSS,
                nCodProjeto,
                observacao,
                cCodVendedor,
                nCodComprador,
                cCodigoBarras,
                cNSU,
                nCodNF,
                dDtRegistro,
                cNumBoleto,
                cChaveNFe,
                cOrigem,
                nCodTitRepet,
                cGrupo,
                nCodMovCC,
                nValorMovCC,
                nCodMovCCRepet,
                nCodBaixa,
                dDtCredito,
                dDtConcilia,
                cHrConcilia,
                cUsConcilia,
                dDtInc,
                cHrInc,
                cUsInc,
                dDtAlt,
                cHrAlt,
                cUsAlt,
                cLiquidado,
                nValPago,
                nValAberto,
                nDesconto,
                nJuros,
                nMulta,
                nValLiquido,
                CategoriasJson,
                DepartamentosJson,
                MovimentoJson,
                DataHoraCarga
              )
              VALUES (
                src.IDEmpresaProprietaria,
                src.TipoLancamento,
                src.EventKey,
                src.DocumentoKey,
                src.nPagina,
                src.nTotPaginas,
                src.nRegistros,
                src.nTotRegistros,
                src.nCodTitulo,
                src.cCodIntTitulo,
                src.cNumTitulo,
                src.dDtEmissao,
                src.dDtVenc,
                src.dDtPrevisao,
                src.dDtPagamento,
                src.nCodCliente,
                src.cCPFCNPJCliente,
                src.nCodCtr,
                src.cNumCtr,
                src.nCodOS,
                src.cNumOS,
                src.nCodCC,
                src.cStatus,
                src.cNatureza,
                src.cTipo,
                src.cOperacao,
                src.cNumDocFiscal,
                src.cCodCateg,
                src.cNumParcela,
                src.nValorTitulo,
                src.nValorPIS,
                src.cRetPIS,
                src.nValorCOFINS,
                src.cRetCOFINS,
                src.nValorCSLL,
                src.cRetCSLL,
                src.nValorIR,
                src.cRetIR,
                src.nValorISS,
                src.cRetISS,
                src.nValorINSS,
                src.cRetINSS,
                src.nCodProjeto,
                src.observacao,
                src.cCodVendedor,
                src.nCodComprador,
                src.cCodigoBarras,
                src.cNSU,
                src.nCodNF,
                src.dDtRegistro,
                src.cNumBoleto,
                src.cChaveNFe,
                src.cOrigem,
                src.nCodTitRepet,
                src.cGrupo,
                src.nCodMovCC,
                src.nValorMovCC,
                src.nCodMovCCRepet,
                src.nCodBaixa,
                src.dDtCredito,
                src.dDtConcilia,
                src.cHrConcilia,
                src.cUsConcilia,
                src.dDtInc,
                src.cHrInc,
                src.cUsInc,
                src.dDtAlt,
                src.cHrAlt,
                src.cUsAlt,
                src.cLiquidado,
                src.nValPago,
                src.nValAberto,
                src.nDesconto,
                src.nJuros,
                src.nMulta,
                src.nValLiquido,
                src.CategoriasJson,
                src.DepartamentosJson,
                src.MovimentoJson,
                SYSUTCDATETIME()
              );
            """
        )
    )


def executar_com_retry_db(func, *args, **kwargs):
    """Executa operação de banco com retry simples."""
    for tentativa in range(1, MAX_TENTATIVAS_DB + 1):
        try:
            return func(*args, **kwargs)
        except Exception:
            if tentativa == MAX_TENTATIVAS_DB:
                raise
            time.sleep(1.5 * tentativa)


def _iterar_fatias(inicio: date, fim: date, janela_dias: int) -> Iterator[Tuple[date, date]]:
    """Divide período em fatias de N dias."""
    atual = inicio
    while atual <= fim:
        ate = min(fim, atual + timedelta(days=janela_dias - 1))
        yield atual, ate
        atual = ate + timedelta(days=1)


def sincronizar_ambiente_por_tipo(
    engine: Engine,
    hook_omie: OmieHook,
    tipo: str,
    limite_amostra: int = 5,
) -> Dict[str, Any]:
    """Sincroniza um ambiente Omie para um tipo de lançamento e retorna estatísticas da execução."""
    config_omie = hook_omie.obter_configuracao()
    id_empresa = config_omie.id_empresa_proprietaria

    if id_empresa is None:
        raise ValueError(
            f"A connection '{config_omie.conn_id}' não possui "
            f"'id_empresa_proprietaria' configurado no extra."
        )

    hoje = date.today()

    with engine.begin() as conn:
        executar_com_retry_db(garantir_tabela_watermark, conn)
        ultimo_dt = obter_watermark(conn, id_empresa, tipo)

    inicio_base = ultimo_dt.date() - timedelta(days=OVERLAP_DIAS)
    fim_base = hoje

    maior_momento_global: Optional[datetime] = None
    total_processados = 0
    total_paginas = 0
    total_fatias = 0
    amostra_linhas: List[Dict[str, Any]] = []

    for dt_de, dt_ate in _iterar_fatias(inicio_base, fim_base, JANELA_DIAS):
        total_fatias += 1

        logger.info(
            "[RANGE] Conn=%s Empresa=%s Tipo=%s AltDe=%s AltAte=%s",
            config_omie.conn_id,
            id_empresa,
            tipo,
            dt_de,
            dt_ate,
        )

        pagina = 1
        maior_momento_fatia: Optional[datetime] = None

        while True:
            resp = hook_omie.listar_movimentos(
                tipo_lancamento=tipo,
                pagina=pagina,
                registros_por_pagina=REGISTROS_POR_PAGINA,
                data_alteracao_de=dt_de.strftime("%d/%m/%Y"),
                data_alteracao_ate=dt_ate.strftime("%d/%m/%Y"),
            )

            n_tot_paginas = int(resp.get("nTotPaginas", 0) or 0)
            movimentos = resp.get("movimentos", []) or []
            qtd = len(movimentos)
            total_paginas += 1

            meta = {
                "nPagina": resp.get("nPagina"),
                "nTotPaginas": resp.get("nTotPaginas"),
                "nRegistros": resp.get("nRegistros"),
                "nTotRegistros": resp.get("nTotRegistros"),
            }

            logger.info(
                "[PAGE] Conn=%s Empresa=%s Tipo=%s Pagina=%s/%s Movimentos=%s",
                config_omie.conn_id,
                id_empresa,
                tipo,
                pagina,
                n_tot_paginas or 0,
                qtd,
            )

            linhas = []
            for mov in movimentos:
                linha = achatar_movimento(id_empresa, meta, mov, tipo)
                linhas.append(linha)

                if len(amostra_linhas) < limite_amostra and linha.get("EventKey"):
                    amostra_linhas.append(montar_amostra_movimento_raw(linha))

                momento = extrair_momento_alteracao(linha)
                if momento is not None:
                    if maior_momento_fatia is None or momento > maior_momento_fatia:
                        maior_momento_fatia = momento

            def aplicar_pagina():
                with engine.begin() as tx_conn:
                    upsert_pagina(tx_conn, linhas)

            executar_com_retry_db(aplicar_pagina)

            total_processados += qtd

            linhas.clear()
            del linhas
            del movimentos
            del resp

            if n_tot_paginas == 0 or pagina >= n_tot_paginas:
                break

            pagina += 1

        if maior_momento_fatia is not None:
            if maior_momento_global is None or maior_momento_fatia > maior_momento_global:
                maior_momento_global = maior_momento_fatia

    logger.info(
        "[DONE] Conn=%s Empresa=%s Tipo=%s TotalProcessados=%s",
        config_omie.conn_id,
        id_empresa,
        tipo,
        total_processados,
    )

    if maior_momento_global is not None:

        def aplicar_watermark():
            with engine.begin() as tx_conn:
                garantir_tabela_watermark(tx_conn)
                atualizar_watermark(
                    tx_conn,
                    id_empresa,
                    tipo,
                    maior_momento_global,
                )

        executar_com_retry_db(aplicar_watermark)

        logger.info(
            "[WATERMARK] Conn=%s Empresa=%s Tipo=%s AtualizadoPara=%s",
            config_omie.conn_id,
            id_empresa,
            tipo,
            maior_momento_global,
        )

    return {
        "conn_id_omie": config_omie.conn_id,
        "id_empresa_proprietaria": id_empresa,
        "tipo_lancamento": tipo,
        "watermark_anterior": str(ultimo_dt),
        "inicio_base": str(inicio_base),
        "fim_base": str(fim_base),
        "maior_momento_global": str(maior_momento_global) if maior_momento_global else None,
        "total_processados": int(total_processados),
        "total_paginas": int(total_paginas),
        "total_fatias": int(total_fatias),
        "amostra": limitar_amostra(amostra_linhas, limite=limite_amostra),
    }


def executar_merge_silver(engine: Engine) -> Dict[str, Any]:
    """Executa MERGE da camada Silver e retorna estatísticas com amostra."""
    SQL_MERGE_FATO_MOV_FIN_OMIE = r"""
DECLARE @ultima_carga datetime2(0) =
(
    SELECT ISNULL(MAX(DataHoraCarga), CONVERT(datetime2(0), '1900-01-01'))
    FROM Silver.FatoMovimentacaoFinanceiroOmie
);

;WITH FonteRaw AS (
    SELECT
        mf.IDMovimentacaoFinanceiro
        , mf.IDEmpresaProprietaria
        , mf.nCodTitulo
        , mf.cCodIntTitulo
        , mf.cNumTitulo
        , mf.dDtEmissao
        , mf.dDtVenc
        , mf.dDtPrevisao
        , mf.dDtPagamento
        , mf.nCodCliente
        , mf.cCPFCNPJCliente
        , mf.nCodCtr
        , mf.cNumCtr
        , mf.nCodOS
        , mf.cNumOS
        , mf.nCodCC
        , mf.cStatus
        , mf.cNatureza
        , CASE
            WHEN mf.cNatureza IS NULL OR LTRIM(RTRIM(mf.cNatureza)) = '' THEN 'Sem natureza'
            WHEN UPPER(LTRIM(RTRIM(mf.cNatureza))) = 'P' THEN 'P - Contas a Pagar'
            WHEN UPPER(LTRIM(RTRIM(mf.cNatureza))) = 'R' THEN 'R - Contas a Receber'
            ELSE CONCAT('Natureza não mapeada: ', mf.cNatureza)
        END AS cNaturezaDescricao

        , mf.cTipo
        , COALESCE(
            tdo.descricao,
            CASE
                WHEN mf.cTipo IS NULL OR LTRIM(RTRIM(mf.cTipo)) = '' THEN 'Sem tipo'
                ELSE CONCAT('Tipo não encontrado na Dim: ', mf.cTipo)
            END
        ) AS cTipoDescricao

        , mf.cOperacao
        , CASE
            WHEN mf.cOperacao IS NULL OR LTRIM(RTRIM(mf.cOperacao)) = '' THEN 'Sem operação'
            WHEN mf.cOperacao = '01' THEN 'Venda de Serviço'
            WHEN mf.cOperacao = '11' THEN 'Venda de Produto'
            WHEN mf.cOperacao = '12' THEN 'Venda de Produto pelo PDV'
            WHEN mf.cOperacao = '13' THEN 'Devolução de Venda'
            WHEN mf.cOperacao = '14' THEN 'Remessa de Produto'
            WHEN mf.cOperacao = '16' THEN 'Transferência'
            WHEN mf.cOperacao = '21' THEN 'Compra de Produto'
            WHEN mf.cOperacao = '22' THEN 'Compra de Produto (Importação)'
            WHEN mf.cOperacao = '23' THEN 'Devolução ao Fornecedor'
            WHEN mf.cOperacao = '24' THEN 'Retorno de Remessa'
            WHEN mf.cOperacao = '26' THEN 'Nota Complementar de Entrada'
            WHEN mf.cOperacao = '28' THEN 'Ordem de Produção'
            ELSE CONCAT('Código não mapeado: ', mf.cOperacao)
        END AS cOperacaoDescricao

        , mf.cNumDocFiscal
        , mf.cCodCateg
        , PLC.Nivel1
        , mf.cNumParcela
        , mf.nValorTitulo
        , mf.nValorPIS
        , mf.cRetPIS
        , mf.nValorCOFINS
        , mf.cRetCOFINS
        , mf.nValorCSLL
        , mf.cRetCSLL
        , mf.nValorIR
        , mf.cRetIR
        , mf.nValorISS
        , mf.cRetISS
        , mf.nValorINSS
        , mf.cRetINSS
        , mf.nCodProjeto
        , mf.observacao
        , TRY_CONVERT(bigint, mf.cCodVendedor) AS cCodVendedor
        , mf.nCodComprador
        , mf.cCodigoBarras
        , mf.cNSU
        , mf.nCodNF
        , mf.dDtRegistro
        , mf.cNumBoleto
        , mf.cChaveNFe
        , mf.cOrigem
        , CASE
            WHEN mf.cOrigem IS NULL OR LTRIM(RTRIM(mf.cOrigem)) = '' THEN 'Sem origem'
            WHEN mf.cOrigem = 'APBP' THEN 'APBP - Integração de Pagamento de Conta'
            WHEN mf.cOrigem = 'APBR' THEN 'APBR - Integração de Recebimento de Conta'
            WHEN mf.cOrigem = 'APEP' THEN 'APEP - Integração de Lançamento de Despesa'
            WHEN mf.cOrigem = 'APER' THEN 'APER - Integração de Lançamento de Receita'
            WHEN mf.cOrigem = 'APIP' THEN 'APIP - Integração de Conta a Pagar'
            WHEN mf.cOrigem = 'APIR' THEN 'APIR - Integração de Conta a Receber'
            WHEN mf.cOrigem = 'BARP' THEN 'BARP - Conta a Pagar Importada por Código de Barras'
            WHEN mf.cOrigem = 'BARR' THEN 'BARR - Conta a Receber Importada por Código de Barras'
            WHEN mf.cOrigem = 'BAXP' THEN 'BAXP - Pagamento de Conta a Pagar'
            WHEN mf.cOrigem = 'BAXR' THEN 'BAXR - Recebimento de Conta a Receber'
            WHEN mf.cOrigem = 'COMP' THEN 'COMP - Parcela a Pagar de Compras'
            WHEN mf.cOrigem = 'DEVP' THEN 'DEVP - Conta a Pagar da Devolução de Venda'
            WHEN mf.cOrigem = 'DEVR' THEN 'DEVR - Conta a Receber da Devolução ao Fornecedor'
            WHEN mf.cOrigem = 'EXTP' THEN 'EXTP - Lançamento Manual de Despesa'
            WHEN mf.cOrigem = 'EXTR' THEN 'EXTR - Lançamento Manual de Receita'
            WHEN mf.cOrigem = 'IMPP' THEN 'IMPP - Parcela a Pagar de uma Nota de Importação'
            WHEN mf.cOrigem = 'MANP' THEN 'MANP - Lançamento Manual de Conta a Pagar'
            WHEN mf.cOrigem = 'MANR' THEN 'MANR - Lançamento Manual de Conta a Receber'
            WHEN mf.cOrigem = 'NFEP' THEN 'NFEP - Conta a Pagar Importada de uma NF-e'
            WHEN mf.cOrigem = 'NFER' THEN 'NFER - Conta a Receber Importada de uma NF-e'
            WHEN mf.cOrigem = 'OFXP' THEN 'OFXP - Pagamento Importado de um arquivo OFX'
            WHEN mf.cOrigem = 'OFXR' THEN 'OFXR - Recebimento Importado de um arquivo OFX'
            WHEN mf.cOrigem = 'RPTP' THEN 'RPTP - Repetição de Contas a Pagar'
            WHEN mf.cOrigem = 'RPTR' THEN 'RPTR - Repetição de Contas a Receber'
            WHEN mf.cOrigem = 'TRAP' THEN 'TRAP - Débito de Transf. entre Contas Correntes'
            WHEN mf.cOrigem = 'TRAR' THEN 'TRAR - Crédito de Transf. entre Contas Correntes'
            WHEN mf.cOrigem = 'VENR' THEN 'VENR - Parcela a Receber de Vendas'
            WHEN mf.cOrigem = 'XMLP' THEN 'XMLP - Conta a Pagar Importada de um arquivo XML'
            WHEN mf.cOrigem = 'XMLR' THEN 'XMLR - Conta a Receber Importada de um arquivo XML'
            ELSE CONCAT('Origem não mapeada: ', mf.cOrigem)
        END AS cOrigemDescricao

        , mf.nCodTitRepet
        , mf.cGrupo
        , mf.nCodMovCC
        , mf.nValorMovCC
        , mf.nCodMovCCRepet
        , mf.nCodBaixa
        , mf.dDtCredito
        , mf.dDtConcilia
        , mf.cHrConcilia
        , mf.cUsConcilia
        , mf.dDtInc
        , mf.cHrInc
        , mf.cUsInc
        , mf.dDtAlt
        , mf.cHrAlt
        , mf.cUsAlt
        , mf.cLiquidado
        , mf.nValPago
        , mf.nValAberto
        , mf.nDesconto
        , mf.nJuros
        , mf.nMulta
        , mf.nValLiquido
        , mf.DataHoraCarga
        , mf.IdChaveOmie
        , mf.TipoLancamento
        , CASE
            WHEN mf.TipoLancamento IS NULL OR LTRIM(RTRIM(mf.TipoLancamento)) = '' THEN 'Sem tipo de lançamento'
            WHEN UPPER(LTRIM(RTRIM(mf.TipoLancamento))) = 'BX' THEN 'BX - Baixa (liquidação do título: pagamento/recebimento efetivo)'
            WHEN UPPER(LTRIM(RTRIM(mf.TipoLancamento))) = 'CC' THEN 'CC - Conta Corrente (lançamento direto no extrato / movimento bancário)'
            WHEN UPPER(LTRIM(RTRIM(mf.TipoLancamento))) = 'CPCR' THEN 'CPCR - Compensação Pagar/Receber (baixa por compensação interna, sem dinheiro no banco)'
            ELSE CONCAT('TipoLancamento não mapeado: ', mf.TipoLancamento)
        END AS TipoLancamentoDescricao

        , mf.EventKey
        , mf.DocumentoKey

        , c.RazaoSocial AS ClienteRazaoSocial
        , c.NomeFantasia AS ClienteNomeFantasia
        , c.CnpjCpf AS ClienteCnpjCpf
        , c.Email AS ClienteEmail
        , c.Cidade AS ClienteCidade
        , c.Estado AS ClienteEstado

    FROM Integracao.dbo.MovimentacaoFinanceiro mf
    LEFT JOIN Integracao.dbo.TipoDocumentoOmie tdo
        ON tdo.IDEmpresaProprietaria = mf.IDEmpresaProprietaria
        AND UPPER(LTRIM(RTRIM(tdo.codigo))) = UPPER(LTRIM(RTRIM(mf.cTipo)))
    INNER JOIN Integracao.dbo.PlanoCategoriasGerencial PLC
        ON PLC.CategoriaFonte = mf.cCodCateg
    LEFT JOIN Integracao.dbo.ClientesOmie c
        ON c.IDEmpresaProprietaria = mf.IDEmpresaProprietaria
        AND c.CodigoClienteOmie = mf.nCodCliente
    WHERE
        mf.DataHoraCarga >= @ultima_carga
        OR (mf.dDtAlt IS NOT NULL AND mf.dDtAlt >= CAST(@ultima_carga AS date))
),
Fonte AS (
    SELECT
        fr.*,
        ROW_NUMBER() OVER (
            PARTITION BY fr.IDEmpresaProprietaria, fr.DocumentoKey, fr.EventKey
            ORDER BY fr.DataHoraCarga DESC, fr.dDtAlt DESC
        ) AS rn
    FROM FonteRaw fr
)
MERGE Silver.FatoMovimentacaoFinanceiroOmie WITH (HOLDLOCK) AS T
USING (SELECT * FROM Fonte WHERE rn = 1) AS S
ON T.IDEmpresaProprietaria = S.IDEmpresaProprietaria
AND T.DocumentoKey = S.DocumentoKey
AND T.EventKey = S.EventKey

WHEN MATCHED THEN
    UPDATE SET
        T.IDMovimentacaoFinanceiro = S.IDMovimentacaoFinanceiro
        , T.nCodTitulo = S.nCodTitulo
        , T.cCodIntTitulo = S.cCodIntTitulo
        , T.cNumTitulo = S.cNumTitulo
        , T.dDtEmissao = S.dDtEmissao
        , T.dDtVenc = S.dDtVenc
        , T.dDtPrevisao = S.dDtPrevisao
        , T.dDtPagamento = S.dDtPagamento
        , T.nCodCliente = S.nCodCliente
        , T.cCPFCNPJCliente = S.cCPFCNPJCliente
        , T.nCodCtr = S.nCodCtr
        , T.cNumCtr = S.cNumCtr
        , T.nCodOS = S.nCodOS
        , T.cNumOS = S.cNumOS
        , T.nCodCC = S.nCodCC
        , T.cStatus = S.cStatus
        , T.cNatureza = S.cNatureza
        , T.cNaturezaDescricao = S.cNaturezaDescricao
        , T.cTipo = S.cTipo
        , T.cTipoDescricao = S.cTipoDescricao
        , T.cOperacao = S.cOperacao
        , T.cOperacaoDescricao = S.cOperacaoDescricao
        , T.cNumDocFiscal = S.cNumDocFiscal
        , T.cCodCateg = S.cCodCateg
        , T.Nivel1 = S.Nivel1
        , T.cNumParcela = S.cNumParcela
        , T.nValorTitulo = S.nValorTitulo
        , T.nValorPIS = S.nValorPIS
        , T.cRetPIS = S.cRetPIS
        , T.nValorCOFINS = S.nValorCOFINS
        , T.cRetCOFINS = S.cRetCOFINS
        , T.nValorCSLL = S.nValorCSLL
        , T.cRetCSLL = S.cRetCSLL
        , T.nValorIR = S.nValorIR
        , T.cRetIR = S.cRetIR
        , T.nValorISS = S.nValorISS
        , T.cRetISS = S.cRetISS
        , T.nValorINSS = S.nValorINSS
        , T.cRetINSS = S.cRetINSS
        , T.nCodProjeto = S.nCodProjeto
        , T.observacao = S.observacao
        , T.cCodVendedor = S.cCodVendedor
        , T.nCodComprador = S.nCodComprador
        , T.cCodigoBarras = S.cCodigoBarras
        , T.cNSU = S.cNSU
        , T.nCodNF = S.nCodNF
        , T.dDtRegistro = S.dDtRegistro
        , T.cNumBoleto = S.cNumBoleto
        , T.cChaveNFe = S.cChaveNFe
        , T.cOrigem = S.cOrigem
        , T.cOrigemDescricao = S.cOrigemDescricao
        , T.nCodTitRepet = S.nCodTitRepet
        , T.cGrupo = S.cGrupo
        , T.nCodMovCC = S.nCodMovCC
        , T.nValorMovCC = S.nValorMovCC
        , T.nCodMovCCRepet = S.nCodMovCCRepet
        , T.nCodBaixa = S.nCodBaixa
        , T.dDtCredito = S.dDtCredito
        , T.dDtConcilia = S.dDtConcilia
        , T.cHrConcilia = S.cHrConcilia
        , T.cUsConcilia = S.cUsConcilia
        , T.dDtInc = S.dDtInc
        , T.cHrInc = S.cHrInc
        , T.cUsInc = S.cUsInc
        , T.dDtAlt = S.dDtAlt
        , T.cHrAlt = S.cHrAlt
        , T.cUsAlt = S.cUsAlt
        , T.cLiquidado = S.cLiquidado
        , T.nValPago = S.nValPago
        , T.nValAberto = S.nValAberto
        , T.nDesconto = S.nDesconto
        , T.nJuros = S.nJuros
        , T.nMulta = S.nMulta
        , T.nValLiquido = S.nValLiquido
        , T.DataHoraCarga = S.DataHoraCarga
        , T.IdChaveOmie = S.IdChaveOmie
        , T.TipoLancamento = S.TipoLancamento
        , T.TipoLancamentoDescricao = S.TipoLancamentoDescricao
        , T.ClienteRazaoSocial = S.ClienteRazaoSocial
        , T.ClienteNomeFantasia = S.ClienteNomeFantasia
        , T.ClienteCnpjCpf = S.ClienteCnpjCpf
        , T.ClienteEmail = S.ClienteEmail
        , T.ClienteCidade = S.ClienteCidade
        , T.ClienteEstado = S.ClienteEstado
        , T.DataAtualizacao = SYSDATETIME()

WHEN NOT MATCHED BY TARGET THEN
    INSERT (
        IDMovimentacaoFinanceiro
        , IDEmpresaProprietaria
        , nCodTitulo
        , cCodIntTitulo
        , cNumTitulo
        , dDtEmissao
        , dDtVenc
        , dDtPrevisao
        , dDtPagamento
        , nCodCliente
        , cCPFCNPJCliente
        , nCodCtr
        , cNumCtr
        , nCodOS
        , cNumOS
        , nCodCC
        , cStatus
        , cNatureza
        , cNaturezaDescricao
        , cTipo
        , cTipoDescricao
        , cOperacao
        , cOperacaoDescricao
        , cNumDocFiscal
        , cCodCateg
        , Nivel1
        , cNumParcela
        , nValorTitulo
        , nValorPIS
        , cRetPIS
        , nValorCOFINS
        , cRetCOFINS
        , nValorCSLL
        , cRetCSLL
        , nValorIR
        , cRetIR
        , nValorISS
        , cRetISS
        , nValorINSS
        , cRetINSS
        , nCodProjeto
        , observacao
        , cCodVendedor
        , nCodComprador
        , cCodigoBarras
        , cNSU
        , nCodNF
        , dDtRegistro
        , cNumBoleto
        , cChaveNFe
        , cOrigem
        , cOrigemDescricao
        , nCodTitRepet
        , cGrupo
        , nCodMovCC
        , nValorMovCC
        , nCodMovCCRepet
        , nCodBaixa
        , dDtCredito
        , dDtConcilia
        , cHrConcilia
        , cUsConcilia
        , dDtInc
        , cHrInc
        , cUsInc
        , dDtAlt
        , cHrAlt
        , cUsAlt
        , cLiquidado
        , nValPago
        , nValAberto
        , nDesconto
        , nJuros
        , nMulta
        , nValLiquido
        , DataHoraCarga
        , IdChaveOmie
        , TipoLancamento
        , TipoLancamentoDescricao
        , EventKey
        , DocumentoKey
        , ClienteRazaoSocial
        , ClienteNomeFantasia
        , ClienteCnpjCpf
        , ClienteEmail
        , ClienteCidade
        , ClienteEstado
        , DataAtualizacao
    )
    VALUES (
        S.IDMovimentacaoFinanceiro
        , S.IDEmpresaProprietaria
        , S.nCodTitulo
        , S.cCodIntTitulo
        , S.cNumTitulo
        , S.dDtEmissao
        , S.dDtVenc
        , S.dDtPrevisao
        , S.dDtPagamento
        , S.nCodCliente
        , S.cCPFCNPJCliente
        , S.nCodCtr
        , S.cNumCtr
        , S.nCodOS
        , S.cNumOS
        , S.nCodCC
        , S.cStatus
        , S.cNatureza
        , S.cNaturezaDescricao
        , S.cTipo
        , S.cTipoDescricao
        , S.cOperacao
        , S.cOperacaoDescricao
        , S.cNumDocFiscal
        , S.cCodCateg
        , S.Nivel1
        , S.cNumParcela
        , S.nValorTitulo
        , S.nValorPIS
        , S.cRetPIS
        , S.nValorCOFINS
        , S.cRetCOFINS
        , S.nValorCSLL
        , S.cRetCSLL
        , S.nValorIR
        , S.cRetIR
        , S.nValorISS
        , S.cRetISS
        , S.nValorINSS
        , S.cRetINSS
        , S.nCodProjeto
        , S.observacao
        , S.cCodVendedor
        , S.nCodComprador
        , S.cCodigoBarras
        , S.cNSU
        , S.nCodNF
        , S.dDtRegistro
        , S.cNumBoleto
        , S.cChaveNFe
        , S.cOrigem
        , S.cOrigemDescricao
        , S.nCodTitRepet
        , S.cGrupo
        , S.nCodMovCC
        , S.nValorMovCC
        , S.nCodMovCCRepet
        , S.nCodBaixa
        , S.dDtCredito
        , S.dDtConcilia
        , S.cHrConcilia
        , S.cUsConcilia
        , S.dDtInc
        , S.cHrInc
        , S.cUsInc
        , S.dDtAlt
        , S.cHrAlt
        , S.cUsAlt
        , S.cLiquidado
        , S.nValPago
        , S.nValAberto
        , S.nDesconto
        , S.nJuros
        , S.nMulta
        , S.nValLiquido
        , S.DataHoraCarga
        , S.IdChaveOmie
        , S.TipoLancamento
        , S.TipoLancamentoDescricao
        , S.EventKey
        , S.DocumentoKey
        , S.ClienteRazaoSocial
        , S.ClienteNomeFantasia
        , S.ClienteCnpjCpf
        , S.ClienteEmail
        , S.ClienteCidade
        , S.ClienteEstado
        , SYSDATETIME()
    )
;
"""
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(SQL_MERGE_FATO_MOV_FIN_OMIE)

        amostra = consultar_amostra_sql(
            engine=engine,
            sql="""
                SELECT TOP 5
                    IDEmpresaProprietaria,
                    DocumentoKey,
                    EventKey,
                    TipoLancamento,
                    cNatureza,
                    cStatus,
                    Nivel1,
                    nValorTitulo,
                    nValPago,
                    nValAberto,
                    DataAtualizacao
                FROM Silver.FatoMovimentacaoFinanceiroOmie
                ORDER BY DataAtualizacao DESC, IDEmpresaProprietaria DESC
            """,
            limite=5,
        )

        with engine.begin() as conn:
            total = conn.execute(
                text("SELECT COUNT(1) FROM Silver.FatoMovimentacaoFinanceiroOmie")
            ).scalar()

        logger.info("[MERGE] Silver.FatoMovimentacaoFinanceiroOmie OK")

        return {
            "tabela_destino": "Silver.FatoMovimentacaoFinanceiroOmie",
            "total_registros_tabela": int(total or 0),
            "amostra": amostra,
        }
    except SQLAlchemyError:
        logger.exception("[MERGE][ERRO] Falhou na Silver")
        raise


def executar_merge_gold(engine: Engine) -> Dict[str, Any]:
    """Executa MERGE da camada Gold e retorna estatísticas com amostra."""
    SQL_MERGE_GOLD_FATO_MOV_FIN_OMIE = r"""
;WITH FonteRaw AS (
SELECT
    o.nCodTitulo
    ,o.IDEmpresaProprietaria
    ,o.dDtEmissao
    ,o.dDtVenc
    ,o.dDtPrevisao
    ,o.nCodCliente
    ,o.cStatus
    ,o.cCodCateg
    ,o.Nivel1
    ,o.cNatureza
    ,o.cNaturezaDescricao
    ,o.cNumParcela
    ,o.cOrigem
    ,o.cOrigemDescricao
    ,o.cGrupo
    ,o.nCodMovCC
    ,o.nCodBaixa
    ,o.dDtInc
    ,o.cHrInc
    ,o.dDtAlt
    ,o.cHrAlt
    ,o.nValorTitulo
    ,o.nValPago
    ,o.nValAberto
    ,o.nValLiquido
    ,o.TipoLancamento
    ,o.TipoLancamentoDescricao
    ,o.EventKey
    ,o.DocumentoKey
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN 'Serviço'
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN 'Pedido'
        ELSE 'Outros'
    END AS Tipo
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN ISNULL(os.cNumOS, '')
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN ISNULL(p.NumeroPedidoOmie, '')
        ELSE ''
    END AS ReferenciaPedidoOS
    ,'A RECEBER - EM ATRASO' AS Movimento
FROM [Integracao].[Silver].[FatoMovimentacaoFinanceiroOmie] o
LEFT JOIN Integracao.dbo.OrdemServicosOmie os
    ON os.nCodOS = o.nCodOS
LEFT JOIN [Integracao].[dbo].[PedidoOmie] p
    ON p.CodigoPedidoOmie = o.nCodOS
WHERE o.cNatureza = 'R'
AND o.nValPago = 0
AND o.dDtVenc < CAST(GETDATE() AS date)
AND o.cStatus <> 'CANCELADO'
AND o.nCodBaixa IS NULL

UNION ALL

SELECT
    o.nCodTitulo
    ,o.IDEmpresaProprietaria
    ,o.dDtEmissao
    ,o.dDtVenc
    ,o.dDtPrevisao
    ,o.nCodCliente
    ,o.cStatus
    ,o.cCodCateg
    ,o.Nivel1
    ,o.cNatureza
    ,o.cNaturezaDescricao
    ,o.cNumParcela
    ,o.cOrigem
    ,o.cOrigemDescricao
    ,o.cGrupo
    ,o.nCodMovCC
    ,o.nCodBaixa
    ,o.dDtInc
    ,o.cHrInc
    ,o.dDtAlt
    ,o.cHrAlt
    ,o.nValorTitulo
    ,-ABS(o.nValPago) AS nValPago
    ,o.nValAberto
    ,o.nValLiquido
    ,o.TipoLancamento
    ,o.TipoLancamentoDescricao
    ,o.EventKey
    ,o.DocumentoKey
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN 'Serviço'
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN 'Pedido'
        ELSE 'Outros'
    END AS Tipo
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN ISNULL(os.cNumOS, '')
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN ISNULL(p.NumeroPedidoOmie, '')
        ELSE ''
    END AS ReferenciaPedidoOS
    ,'A PAGAR - EM ATRASO' AS Movimento
FROM [Integracao].[Silver].[FatoMovimentacaoFinanceiroOmie] o
LEFT JOIN Integracao.dbo.OrdemServicosOmie os
    ON os.nCodOS = o.nCodOS
LEFT JOIN [Integracao].[dbo].[PedidoOmie] p
    ON p.CodigoPedidoOmie = o.nCodOS
WHERE o.cNatureza = 'P'
AND o.nValPago = 0
AND o.dDtVenc < CAST(GETDATE() AS date)
AND o.cStatus <> 'CANCELADO'
AND o.nCodBaixa IS NULL

UNION ALL

SELECT
    o.nCodTitulo
    ,o.IDEmpresaProprietaria
    ,o.dDtEmissao
    ,o.dDtVenc
    ,o.dDtPrevisao
    ,o.nCodCliente
    ,o.cStatus
    ,o.cCodCateg
    ,o.Nivel1
    ,o.cNatureza
    ,o.cNaturezaDescricao
    ,o.cNumParcela
    ,o.cOrigem
    ,o.cOrigemDescricao
    ,o.cGrupo
    ,o.nCodMovCC
    ,o.nCodBaixa
    ,o.dDtInc
    ,o.cHrInc
    ,o.dDtAlt
    ,o.cHrAlt
    ,o.nValorTitulo
    ,o.nValPago
    ,o.nValAberto
    ,o.nValLiquido
    ,o.TipoLancamento
    ,o.TipoLancamentoDescricao
    ,o.EventKey
    ,o.DocumentoKey
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN 'Serviço'
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN 'Pedido'
        ELSE 'Outros'
    END AS Tipo
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN ISNULL(os.cNumOS, '')
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN ISNULL(p.NumeroPedidoOmie, '')
        ELSE ''
    END AS ReferenciaPedidoOS
    ,'RECEBIDOS' AS Movimento
FROM [Integracao].[Silver].[FatoMovimentacaoFinanceiroOmie] o
LEFT JOIN Integracao.dbo.OrdemServicosOmie os
    ON os.nCodOS = o.nCodOS
LEFT JOIN [Integracao].[dbo].[PedidoOmie] p
    ON p.CodigoPedidoOmie = o.nCodOS
WHERE o.cNatureza = 'R'
AND o.dDtPagamento IS NOT NULL
AND o.nCodBaixa IS NOT NULL

UNION ALL

SELECT
    o.nCodTitulo
    ,o.IDEmpresaProprietaria
    ,o.dDtEmissao
    ,o.dDtVenc
    ,o.dDtPrevisao
    ,o.nCodCliente
    ,o.cStatus
    ,o.cCodCateg
    ,o.Nivel1
    ,o.cNatureza
    ,o.cNaturezaDescricao
    ,o.cNumParcela
    ,o.cOrigem
    ,o.cOrigemDescricao
    ,o.cGrupo
    ,o.nCodMovCC
    ,o.nCodBaixa
    ,o.dDtInc
    ,o.cHrInc
    ,o.dDtAlt
    ,o.cHrAlt
    ,o.nValorTitulo
    ,-ABS(o.nValPago) AS nValPago
    ,o.nValAberto
    ,o.nValLiquido
    ,o.TipoLancamento
    ,o.TipoLancamentoDescricao
    ,o.EventKey
    ,o.DocumentoKey
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN 'Serviço'
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN 'Pedido'
        ELSE 'Outros'
    END AS Tipo
    ,CASE
        WHEN os.nCodOS IS NOT NULL THEN ISNULL(os.cNumOS, '')
        WHEN p.CodigoPedidoOmie IS NOT NULL THEN ISNULL(p.NumeroPedidoOmie, '')
        ELSE ''
    END AS ReferenciaPedidoOS
    ,'PAGAMENTOS REALIZADOS' AS Movimento
FROM [Integracao].[Silver].[FatoMovimentacaoFinanceiroOmie] o
LEFT JOIN Integracao.dbo.OrdemServicosOmie os
    ON os.nCodOS = o.nCodOS
LEFT JOIN [Integracao].[dbo].[PedidoOmie] p
    ON p.CodigoPedidoOmie = o.nCodOS
WHERE o.cNatureza = 'P'
AND o.dDtPagamento IS NOT NULL
AND o.nCodBaixa IS NOT NULL
),
Fonte AS (
    SELECT
        fr.*,
        ROW_NUMBER() OVER (
            PARTITION BY fr.IDEmpresaProprietaria, fr.DocumentoKey, fr.EventKey, fr.Movimento
            ORDER BY fr.dDtAlt DESC, fr.dDtInc DESC
        ) AS rn
    FROM FonteRaw fr
)

MERGE [DataMart].[Gold].[FatoMovimentacaoFinanceiraOmie] WITH (HOLDLOCK) AS T
USING (
    SELECT
        nCodTitulo
        , IDEmpresaProprietaria
        , dDtEmissao
        , dDtVenc
        , dDtPrevisao
        , nCodCliente
        , cStatus
        , cCodCateg
        , Nivel1
        , cNatureza
        , cNaturezaDescricao
        , cNumParcela
        , cOrigem
        , cOrigemDescricao
        , cGrupo
        , nCodMovCC
        , nCodBaixa
        , dDtInc
        , cHrInc
        , dDtAlt
        , cHrAlt
        , nValorTitulo
        , nValPago
        , nValAberto
        , nValLiquido
        , TipoLancamento
        , TipoLancamentoDescricao
        , EventKey
        , DocumentoKey
        , Tipo
        , ReferenciaPedidoOS
        , Movimento
    FROM Fonte
    WHERE rn = 1
) AS S
ON T.IDEmpresaProprietaria = S.IDEmpresaProprietaria
AND T.DocumentoKey = S.DocumentoKey
AND T.EventKey = S.EventKey
AND T.Movimento = S.Movimento

WHEN MATCHED THEN
    UPDATE SET
        T.DataAtualizacao = GETDATE()
        , T.nCodTitulo = S.nCodTitulo
        , T.dDtEmissao = S.dDtEmissao
        , T.dDtVenc = S.dDtVenc
        , T.dDtPrevisao = S.dDtPrevisao
        , T.nCodCliente = S.nCodCliente
        , T.cStatus = S.cStatus
        , T.cCodCateg = S.cCodCateg
        , T.Nivel1 = S.Nivel1
        , T.cNatureza = S.cNatureza
        , T.cNaturezaDescricao = S.cNaturezaDescricao
        , T.cNumParcela = S.cNumParcela
        , T.cOrigem = S.cOrigem
        , T.cOrigemDescricao = S.cOrigemDescricao
        , T.cGrupo = S.cGrupo
        , T.nCodMovCC = S.nCodMovCC
        , T.nCodBaixa = S.nCodBaixa
        , T.dDtInc = S.dDtInc
        , T.cHrInc = S.cHrInc
        , T.dDtAlt = S.dDtAlt
        , T.cHrAlt = S.cHrAlt
        , T.nValorTitulo = S.nValorTitulo
        , T.nValPago = S.nValPago
        , T.nValAberto = S.nValAberto
        , T.nValLiquido = S.nValLiquido
        , T.TipoLancamento = S.TipoLancamento
        , T.TipoLancamentoDescricao = S.TipoLancamentoDescricao
        , T.Tipo = S.Tipo
        , T.ReferenciaPedidoOS = S.ReferenciaPedidoOS

WHEN NOT MATCHED BY TARGET THEN
    INSERT (
        DataAtualizacao
        , nCodTitulo
        , IDEmpresaProprietaria
        , dDtEmissao
        , dDtVenc
        , dDtPrevisao
        , nCodCliente
        , cStatus
        , cCodCateg
        , Nivel1
        , cNatureza
        , cNaturezaDescricao
        , cNumParcela
        , cOrigem
        , cOrigemDescricao
        , cGrupo
        , nCodMovCC
        , nCodBaixa
        , dDtInc
        , cHrInc
        , dDtAlt
        , cHrAlt
        , nValorTitulo
        , nValPago
        , nValAberto
        , nValLiquido
        , TipoLancamento
        , TipoLancamentoDescricao
        , EventKey
        , DocumentoKey
        , Tipo
        , ReferenciaPedidoOS
        , Movimento
    )
    VALUES (
        GETDATE()
        , S.nCodTitulo
        , S.IDEmpresaProprietaria
        , S.dDtEmissao
        , S.dDtVenc
        , S.dDtPrevisao
        , S.nCodCliente
        , S.cStatus
        , S.cCodCateg
        , S.Nivel1
        , S.cNatureza
        , S.cNaturezaDescricao
        , S.cNumParcela
        , S.cOrigem
        , S.cOrigemDescricao
        , S.cGrupo
        , S.nCodMovCC
        , S.nCodBaixa
        , S.dDtInc
        , S.cHrInc
        , S.dDtAlt
        , S.cHrAlt
        , S.nValorTitulo
        , S.nValPago
        , S.nValAberto
        , S.nValLiquido
        , S.TipoLancamento
        , S.TipoLancamentoDescricao
        , S.EventKey
        , S.DocumentoKey
        , S.Tipo
        , S.ReferenciaPedidoOS
        , S.Movimento
    )
;
"""
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(SQL_MERGE_GOLD_FATO_MOV_FIN_OMIE)

        amostra = consultar_amostra_sql(
            engine=engine,
            sql="""
                SELECT TOP 5
                    IDEmpresaProprietaria,
                    DocumentoKey,
                    EventKey,
                    Movimento,
                    TipoLancamento,
                    Tipo,
                    ReferenciaPedidoOS,
                    nValorTitulo,
                    nValPago,
                    nValAberto,
                    DataAtualizacao
                FROM [DataMart].[Gold].[FatoMovimentacaoFinanceiraOmie]
                ORDER BY DataAtualizacao DESC, IDEmpresaProprietaria DESC
            """,
            limite=5,
        )

        with engine.begin() as conn:
            total = conn.execute(
                text("SELECT COUNT(1) FROM [DataMart].[Gold].[FatoMovimentacaoFinanceiraOmie]")
            ).scalar()

        logger.info("[MERGE] Gold.FatoMovimentacaoFinanceiraOmie OK")

        return {
            "tabela_destino": "[DataMart].[Gold].[FatoMovimentacaoFinanceiraOmie]",
            "total_registros_tabela": int(total or 0),
            "amostra": amostra,
        }
    except SQLAlchemyError:
        logger.exception("[MERGE][ERRO] Falhou na Gold")
        raise


@dag(
    dag_id="Pipeline_Movimento_Financeiro_Omie",
    description=(
        "Pipeline incremental de movimentação financeira da Omie, com carga RAW por watermark, "
        "consolidação Silver e publicação analítica Gold. O fluxo consome múltiplas empresas Omie, "
        "sincroniza eventos financeiros por tipo de lançamento, aplica deduplicação por chaves "
        "de negócio e transforma os dados em camadas operacionais e analíticas voltadas para "
        "controle financeiro, aging, realizado e visão por documento/evento."
    ),
    schedule="0 10,13,15,18,19 * * 1-6",
    start_date=pendulum.datetime(2026, 3, 1, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["ETL", "Financeiro", "Sp", "Omie"],
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        "retries": 0,
    },
)
def pipeline_movimento_financeiro_omie():
    """
    ### Pipeline de Movimentação Financeira Omie

    Este pipeline foi desenhado para sincronizar a movimentação financeira da Omie de forma
    incremental, consistente e auditável, separando a responsabilidade em três etapas:

    1. **RAW**
       - Consulta a API da Omie para múltiplas empresas (`omie_sp`, `omie_sinamovel`).
       - Processa os tipos de lançamento:
         - `CPCR` = compensação pagar/receber
         - `BX` = baixa efetiva
         - `CC` = movimento de conta corrente
       - Usa watermark por `IDEmpresaProprietaria + TipoLancamento`.
       - Reprocessa uma janela de sobreposição (`OVERLAP_DIAS`) para evitar perda de eventos
         alterados retroativamente.
       - Divide o período em fatias (`JANELA_DIAS`) e pagina (`REGISTROS_POR_PAGINA`) para
         reduzir risco operacional e facilitar retry.
       - Achata o JSON em estrutura tabular.
       - Faz upsert na tabela RAW `dbo.MovimentacaoFinanceiro`.

    2. **Silver**
       - Consolida a RAW em `Silver.FatoMovimentacaoFinanceiroOmie`.
       - Faz enriquecimento com:
         - tipo de documento
         - plano de categorias gerencial
         - clientes Omie
       - Tradução de códigos em descrições de negócio.
       - Deduplicação por:
         - `IDEmpresaProprietaria`
         - `DocumentoKey`
         - `EventKey`
       - Prioriza o registro mais recente por `DataHoraCarga` e `dDtAlt`.

    3. **Gold**
       - Materializa visão analítica em `[DataMart].[Gold].[FatoMovimentacaoFinanceiraOmie]`.
       - Separa movimentos de negócio como:
         - A RECEBER - EM ATRASO
         - A PAGAR - EM ATRASO
         - RECEBIDOS
         - PAGAMENTOS REALIZADOS
       - Enriquecimento com classificação de origem, tipo e referência operacional.
       - Pronta para análises financeiras, aging, fluxo realizado, cobrança e acompanhamento.

    ---

    ### Lógica técnica do incremental

    O pipeline não depende apenas de data de vencimento ou pagamento.
    Ele usa a melhor combinação de campos de alteração da Omie:

    - `dDtAlt + cHrAlt`
    - fallback em `dDtInc + cHrInc`

    Isso é importante porque o objetivo não é apenas capturar novos títulos, mas também:
    - baixas posteriores
    - conciliações
    - alterações em valores
    - mudanças de status

    A estratégia de watermark com overlap minimiza risco de perda por atraso de atualização na origem.

    ---

    ### Chaves de negócio adotadas

    O pipeline constrói chaves explícitas para manter rastreabilidade:

    - `EventKey`
      - identifica o evento financeiro específico
      - varia conforme baixa, movimento de conta corrente ou título

    - `DocumentoKey`
      - identifica o documento financeiro de negócio
      - útil para agrupar eventos de um mesmo título

    Essas chaves são essenciais para:
    - deduplicação
    - versionamento operacional
    - MERGE seguro
    - visão analítica consistente na Silver e Gold

    ---

    ### Auditoria estruturada

    Cada task publica:
    - descrição amigável
    - origem e destino
    - métricas principais
    - validações executadas
    - observações operacionais
    - amostras reais da etapa

    Isso permite que a tela de auditoria mostre:
    - como a API respondeu
    - que tipo de eventos foram persistidos
    - amostras da Silver consolidada
    - amostras da Gold analítica

    Em outras palavras:
    o log bruto continua útil para debug técnico,
    mas a auditoria estruturada passa a explicar o processo de forma operacional e legível.

    ---

    ### Conexões esperadas

    - SQL Server:
      - `mssql_integracao`

    - Omie:
      - `omie_sp`
      - `omie_sinamovel`

    Essas connections devem estar corretamente configuradas no Airflow.

    ---

    ### Cuidados operacionais

    - O DAG roda com `max_active_runs=1` e `max_active_tasks=1` para evitar concorrência
      sobre as mesmas tabelas e watermarks.
    - O retry do banco é feito manualmente em pontos críticos.
    - O pipeline foi desenhado para priorizar consistência e rastreabilidade,
      mesmo que isso implique uma execução mais conservadora.
    """

    @task(
        task_id="sincronizar_movimentacao_financeiro_raw",
        execution_timeout=timedelta(hours=2),
    )
    def sincronizar_movimentacao_financeiro_raw():
        """
        Sincroniza a camada RAW da movimentação financeira Omie.

        Esta etapa:
        - percorre todas as empresas Omie configuradas
        - percorre todos os tipos de lançamento
        - usa watermark + overlap
        - consulta a API Omie em fatias temporais e páginas
        - achata o payload em formato relacional
        - aplica upsert incremental na tabela RAW
        - atualiza o watermark ao final

        A auditoria desta etapa mostra:
        - empresas e tipos processados
        - volume total carregado
        - watermark anterior e novo watermark
        - amostra dos eventos financeiros raw
        """
        resumo = criar_resumo_auditoria(
            nome_amigavel="Sincronizar movimentação financeira RAW",
            descricao_etapa=(
                "Consome a API da Omie por empresa e tipo de lançamento, usando watermark incremental "
                "com sobreposição temporal. Achata os payloads financeiros em linhas tabulares e faz "
                "upsert na tabela RAW dbo.MovimentacaoFinanceiro."
            ),
            origem_dados=(
                "API Omie (connections: omie_sp, omie_sinamovel) | "
                "Tipos: CPCR, BX, CC"
            ),
            destino_dados="dbo.MovimentacaoFinanceiro + dbo.MovFinWatermark",
        )

        engine = obter_engine_sql()

        try:
            resumo.status = "RUNNING"
            resumo.metricas_extras["conn_id_sql_server"] = CONN_ID_SQL_SERVER
            resumo.metricas_extras["conn_ids_omie"] = CONN_IDS_OMIE
            resumo.metricas_extras["tipos_lancamento"] = TIPOS_LANCAMENTO
            resumo.metricas_extras["overlap_dias"] = OVERLAP_DIAS
            resumo.metricas_extras["registros_por_pagina"] = REGISTROS_POR_PAGINA
            resumo.metricas_extras["janela_dias"] = JANELA_DIAS
            resumo.metricas_extras["primeira_carga_desde"] = str(PRIMEIRA_CARGA_DESDE)
            publicar_resumo_auditoria(resumo)

            total_processados = 0
            total_paginas = 0
            total_fatias = 0
            ambientes_processados = 0
            detalhes_execucao: List[Dict[str, Any]] = []
            amostra_total: List[Dict[str, Any]] = []

            for conn_id_omie in CONN_IDS_OMIE:
                hook_omie = OmieHook(omie_conn_id=conn_id_omie)
                config_omie = hook_omie.obter_configuracao()

                for tipo in TIPOS_LANCAMENTO:
                    logger.info(
                        "[SYNC] Conn=%s Empresa=%s Tipo=%s ...",
                        config_omie.conn_id,
                        config_omie.id_empresa_proprietaria,
                        tipo,
                    )

                    resultado_sync = sincronizar_ambiente_por_tipo(
                        engine=engine,
                        hook_omie=hook_omie,
                        tipo=tipo,
                        limite_amostra=4,
                    )

                    ambientes_processados += 1
                    total_processados += int(resultado_sync["total_processados"])
                    total_paginas += int(resultado_sync["total_paginas"])
                    total_fatias += int(resultado_sync["total_fatias"])

                    detalhes_execucao.append(
                        {
                            "conn_id_omie": resultado_sync["conn_id_omie"],
                            "id_empresa_proprietaria": resultado_sync["id_empresa_proprietaria"],
                            "tipo_lancamento": resultado_sync["tipo_lancamento"],
                            "watermark_anterior": resultado_sync["watermark_anterior"],
                            "maior_momento_global": resultado_sync["maior_momento_global"],
                            "total_processados": resultado_sync["total_processados"],
                            "total_paginas": resultado_sync["total_paginas"],
                            "total_fatias": resultado_sync["total_fatias"],
                        }
                    )

                    for linha_amostra in resultado_sync["amostra"]:
                        if len(amostra_total) < 12:
                            amostra_total.append(linha_amostra)

                    logger.info(
                        "[OK] Conn=%s Empresa=%s Tipo=%s",
                        config_omie.conn_id,
                        config_omie.id_empresa_proprietaria,
                        tipo,
                    )

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = int(total_processados)
            resumo.linhas_inseridas = int(total_processados)
            resumo.linhas_atualizadas = int(total_processados)

            resumo.metricas_extras["ambientes_processados"] = int(ambientes_processados)
            resumo.metricas_extras["total_paginas"] = int(total_paginas)
            resumo.metricas_extras["total_fatias"] = int(total_fatias)
            resumo.metricas_extras["detalhes_execucao"] = detalhes_execucao

            adicionar_validacao(
                resumo,
                nome="empresas_e_tipos_processados",
                status="ok",
                detalhe=(
                    f"Foram processadas {ambientes_processados:,} combinações de empresa e tipo "
                    f"de lançamento na RAW."
                ),
            )
            adicionar_validacao(
                resumo,
                nome="movimentos_raw_processados",
                status="ok",
                detalhe=f"Foram processados {total_processados:,} movimentos financeiros na camada RAW.",
            )
            adicionar_validacao(
                resumo,
                nome="watermark_atualizado",
                status="ok",
                detalhe="Os watermarks por empresa e tipo foram recalculados ao final da sincronização.",
            )

            adicionar_observacao(
                resumo,
                "A amostra desta etapa representa eventos financeiros reais já achatados, antes da consolidação Silver.",
            )
            adicionar_observacao(
                resumo,
                "Mesmo quando um título já existia, ele pode ter sido reprocessado por overlap para capturar alterações retroativas.",
            )

            definir_amostra(resumo, limitar_amostra(amostra_total, limite=10), limite=10)
            publicar_resumo_auditoria(resumo)

            logger.info("Sincronização RAW concluída.")

            return {
                "ambientes_processados": int(ambientes_processados),
                "total_processados": int(total_processados),
                "total_paginas": int(total_paginas),
                "total_fatias": int(total_fatias),
            }
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            raise
        finally:
            engine.dispose()

    @task(
        task_id="sincronizar_silver_fato_movimentacao_financeiro_omie",
        execution_timeout=timedelta(hours=2),
    )
    def sincronizar_silver_fato_movimentacao_financeiro_omie():
        """
        Consolida a camada Silver da movimentação financeira Omie.

        Esta etapa:
        - lê a RAW incrementalmente
        - enriquece com dimensões auxiliares
        - traduz códigos técnicos em descrições legíveis
        - deduplica por chaves de negócio
        - faz MERGE na `Silver.FatoMovimentacaoFinanceiroOmie`

        A auditoria desta etapa mostra:
        - amostra da Silver resultante
        - totais da tabela
        - validações do merge
        """
        resumo = criar_resumo_auditoria(
            nome_amigavel="Consolidar Silver da movimentação financeira",
            descricao_etapa=(
                "Executa o MERGE da camada Silver, consolidando a tabela financeira a partir da RAW, "
                "com enriquecimento por cliente, tipo documental e plano gerencial, além de deduplicação "
                "por chaves de negócio e priorização do registro mais recente."
            ),
            origem_dados="dbo.MovimentacaoFinanceiro + dimensões auxiliares Omie",
            destino_dados="Silver.FatoMovimentacaoFinanceiroOmie",
        )

        engine = obter_engine_sql()

        try:
            resumo.status = "RUNNING"
            publicar_resumo_auditoria(resumo)

            resultado_merge = executar_merge_silver(engine)

            resumo.status = "SUCCESS"
            resumo.metricas_extras["tabela_destino"] = resultado_merge["tabela_destino"]
            resumo.metricas_extras["total_registros_tabela"] = int(resultado_merge["total_registros_tabela"])
            resumo.linhas_inseridas = int(resultado_merge["total_registros_tabela"])

            adicionar_validacao(
                resumo,
                nome="merge_silver_executado",
                status="ok",
                detalhe="O MERGE da camada Silver foi executado com sucesso.",
            )
            adicionar_validacao(
                resumo,
                nome="silver_disponivel_para_consulta",
                status="ok",
                detalhe=(
                    f"A tabela {resultado_merge['tabela_destino']} possui "
                    f"{resultado_merge['total_registros_tabela']:,} registros após o MERGE."
                ),
            )

            adicionar_observacao(
                resumo,
                "A amostra desta etapa mostra a visão consolidada da Silver, já com descrições operacionais e chaves de negócio.",
            )

            definir_amostra(resumo, resultado_merge["amostra"], limite=10)
            publicar_resumo_auditoria(resumo)

            return resultado_merge
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            raise
        finally:
            engine.dispose()

    @task(
        task_id="sincronizar_gold_fato_movimentacao_financeira_omie",
        execution_timeout=timedelta(hours=2),
    )
    def sincronizar_gold_fato_movimentacao_financeira_omie():
        """
        Publica a visão Gold analítica da movimentação financeira Omie.

        Esta etapa:
        - deriva visões financeiras de negócio a partir da Silver
        - classifica movimentos em atrasos, recebidos e pagamentos realizados
        - consolida a análise no DataMart Gold

        A auditoria desta etapa mostra:
        - amostra da tabela Gold
        - total consolidado
        - exemplos de movimentos de negócio já prontos para consumo analítico
        """
        resumo = criar_resumo_auditoria(
            nome_amigavel="Publicar Gold da movimentação financeira",
            descricao_etapa=(
                "Executa o MERGE da camada Gold, derivando movimentos analíticos de negócio como "
                "a receber em atraso, a pagar em atraso, recebidos e pagamentos realizados, a partir "
                "da Silver consolidada."
            ),
            origem_dados="Silver.FatoMovimentacaoFinanceiroOmie",
            destino_dados="[DataMart].[Gold].[FatoMovimentacaoFinanceiraOmie]",
        )

        engine = obter_engine_sql()

        try:
            resumo.status = "RUNNING"
            publicar_resumo_auditoria(resumo)

            resultado_merge = executar_merge_gold(engine)

            resumo.status = "SUCCESS"
            resumo.metricas_extras["tabela_destino"] = resultado_merge["tabela_destino"]
            resumo.metricas_extras["total_registros_tabela"] = int(resultado_merge["total_registros_tabela"])
            resumo.linhas_inseridas = int(resultado_merge["total_registros_tabela"])

            adicionar_validacao(
                resumo,
                nome="merge_gold_executado",
                status="ok",
                detalhe="O MERGE da camada Gold foi executado com sucesso.",
            )
            adicionar_validacao(
                resumo,
                nome="gold_disponivel_para_analise",
                status="ok",
                detalhe=(
                    f"A tabela {resultado_merge['tabela_destino']} possui "
                    f"{resultado_merge['total_registros_tabela']:,} registros após o MERGE."
                ),
            )

            adicionar_observacao(
                resumo,
                "A amostra desta etapa representa a visão analítica final, já classificada por movimento de negócio.",
            )

            definir_amostra(resumo, resultado_merge["amostra"], limite=10)
            publicar_resumo_auditoria(resumo)

            return resultado_merge
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            raise
        finally:
            engine.dispose()

    tarefa_raw = sincronizar_movimentacao_financeiro_raw()
    tarefa_silver = sincronizar_silver_fato_movimentacao_financeiro_omie()
    tarefa_gold = sincronizar_gold_fato_movimentacao_financeira_omie()

    tarefa_raw >> tarefa_silver >> tarefa_gold


dag_pipeline_movimento_financeiro_omie = pipeline_movimento_financeiro_omie()