from __future__ import annotations

import math
import re
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

try:
    from hooks.BancodeDados.SqlServer import HookSqlServer
except Exception:  # pragma: no cover
    HookSqlServer = None

try:
    from airflow.sdk.bases.hook import BaseHook
except Exception:  # pragma: no cover
    from airflow.hooks.base import BaseHook


IDENTIFICADOR_SEGURO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LIMITE_MAXIMO_PREVIEW = 100
LIMITE_MAXIMO_PAGINA = 200
LIMITE_MAXIMO_DISTINTOS = 200
LIMITE_MAXIMO_COLUNAS_FILTRO = 8


def _validar_identificador(nome: str, campo: str) -> str:
    """Eu valido nomes de banco, schema, tabela e coluna para impedir injeção em identificadores."""
    nome_limpo = str(nome or "").strip()

    if not nome_limpo:
        raise ValueError(f"O campo '{campo}' é obrigatório.")

    if not IDENTIFICADOR_SEGURO.fullmatch(nome_limpo):
        raise ValueError(f"O campo '{campo}' possui valor inválido: {nome}")

    return nome_limpo


def _montar_nome_qualificado(
    banco: str | None,
    schema: str,
    tabela: str,
) -> str:
    """Eu monto um nome qualificado seguro para SQL Server."""
    schema_validado = _validar_identificador(schema, "schema")
    tabela_validada = _validar_identificador(tabela, "tabela")

    if banco and str(banco).strip():
        banco_validado = _validar_identificador(str(banco).strip(), "banco")
        return f"[{banco_validado}].[{schema_validado}].[{tabela_validada}]"

    return f"[{schema_validado}].[{tabela_validada}]"


def _normalizar_valor_preview(valor: Any) -> Any:
    """Eu converto valores do banco para formato seguro no JSON e no HTML."""
    if valor is None:
        return None

    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, datetime):
        return valor.isoformat(sep=" ")

    if isinstance(valor, date):
        return valor.isoformat()

    if isinstance(valor, time):
        return valor.isoformat()

    if isinstance(valor, bytes):
        try:
            return valor.decode("utf-8", errors="replace")
        except Exception:
            return str(valor)

    return valor


def _normalizar_linha(linha: dict[str, Any]) -> dict[str, Any]:
    """Eu normalizo todos os valores de uma linha retornada pelo banco."""
    return {chave: _normalizar_valor_preview(valor) for chave, valor in linha.items()}


def _criar_engine_por_conn_id(conn_id: str):
    """Eu crio a engine SQLAlchemy usando o padrão oficial do projeto, com fallback para conexão do Airflow."""
    conn_id_limpo = str(conn_id or "").strip()
    if not conn_id_limpo:
        raise ValueError("conn_id é obrigatório.")

    if HookSqlServer is not None:
        hook_sql = HookSqlServer(conn_id=conn_id_limpo)
        return hook_sql.obter_engine()

    conn = BaseHook.get_connection(conn_id_limpo)
    uri = conn.get_uri()
    return create_engine(uri, pool_pre_ping=True, future=True)


def _executar_query_mappings(sql: str, parametros: dict[str, Any], conn_id: str) -> list[dict[str, Any]]:
    """Eu executo SQL e devolvo linhas em formato mapping."""
    engine = _criar_engine_por_conn_id(conn_id=conn_id)

    try:
        with engine.connect() as conexao:
            resultado = conexao.execute(text(sql), parametros)
            return [dict(linha) for linha in resultado.mappings().all()]
    finally:
        engine.dispose()


def _obter_colunas_tabela(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
) -> list[str]:
    """Eu descubro as colunas sem precisar ler dados desnecessários."""
    nome_qualificado = _montar_nome_qualificado(banco=banco, schema=schema, tabela=tabela)
    sql = f"""
    SELECT TOP (0) *
    FROM {nome_qualificado}
    """

    engine = _criar_engine_por_conn_id(conn_id=conn_id)

    try:
        with engine.connect() as conexao:
            resultado = conexao.execute(text(sql))
            return list(resultado.keys())
    finally:
        engine.dispose()


def _obter_total_linhas(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
    where_sql: str = "",
    parametros: dict[str, Any] | None = None,
) -> int:
    """Eu conto o total de linhas considerando os filtros atuais."""
    nome_qualificado = _montar_nome_qualificado(banco=banco, schema=schema, tabela=tabela)
    parametros = parametros or {}

    sql = f"""
    SELECT COUNT_BIG(1) AS total_linhas
    FROM {nome_qualificado}
    {where_sql}
    """

    linhas = _executar_query_mappings(sql=sql, parametros=parametros, conn_id=conn_id)
    if not linhas:
        return 0

    return int(linhas[0].get("total_linhas") or 0)


def _obter_amostra_tabela(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
    limite: int,
) -> list[dict[str, Any]]:
    """Eu trago uma amostra pequena da tabela."""
    limite = max(1, min(int(limite), LIMITE_MAXIMO_PREVIEW))
    nome_qualificado = _montar_nome_qualificado(banco=banco, schema=schema, tabela=tabela)

    sql = f"""
    SELECT TOP ({limite}) *
    FROM {nome_qualificado}
    ORDER BY (SELECT 1)
    """

    linhas = _executar_query_mappings(sql=sql, parametros={}, conn_id=conn_id)
    return [_normalizar_linha(linha) for linha in linhas]


def _quote_coluna(nome_coluna: str) -> str:
    """Eu devolvo uma coluna segura no formato SQL Server."""
    nome_validado = _validar_identificador(nome_coluna, "coluna")
    return f"[{nome_validado}]"


def _eh_coluna_data(nome_coluna: str) -> bool:
    """Eu identifico colunas com forte chance de serem de data."""
    nome = str(nome_coluna or "").strip().lower()

    palavras = (
        "data",
        "date",
        "dt_",
        "dt",
        "hora",
        "time",
        "created",
        "updated",
        "alterado",
        "inserido",
        "execucao",
        "carga",
        "timestamp",
    )
    return any(palavra in nome for palavra in palavras)


def _eh_coluna_status(nome_coluna: str) -> bool:
    """Eu identifico colunas com forte chance de serem status ou categoria operacional."""
    nome = str(nome_coluna or "").strip().lower()

    palavras = (
        "status",
        "situacao",
        "situação",
        "state",
        "resultado",
        "tipo",
        "classe",
        "categoria",
        "origem",
        "destino",
        "uf",
        "flag",
        "bit",
    )
    return any(palavra in nome for palavra in palavras)


def _coletar_valores_distintos_coluna(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
    coluna: str,
    limite: int = LIMITE_MAXIMO_DISTINTOS,
) -> list[str]:
    """Eu trago valores distintos para montar filtros categóricos."""
    limite = max(1, min(int(limite), LIMITE_MAXIMO_DISTINTOS))
    nome_qualificado = _montar_nome_qualificado(banco=banco, schema=schema, tabela=tabela)
    coluna_sql = _quote_coluna(coluna)

    sql = f"""
    SELECT TOP ({limite})
        CAST({coluna_sql} AS NVARCHAR(4000)) AS valor
    FROM {nome_qualificado}
    WHERE {coluna_sql} IS NOT NULL
      AND LTRIM(RTRIM(CAST({coluna_sql} AS NVARCHAR(4000)))) <> ''
    GROUP BY CAST({coluna_sql} AS NVARCHAR(4000))
    ORDER BY CAST({coluna_sql} AS NVARCHAR(4000))
    """

    linhas = _executar_query_mappings(sql=sql, parametros={}, conn_id=conn_id)

    valores: list[str] = []
    vistos: set[str] = set()

    for linha in linhas:
        valor = str(linha.get("valor") or "").strip()
        if not valor:
            continue

        chave = valor.casefold()
        if chave in vistos:
            continue

        vistos.add(chave)
        valores.append(valor)

    return valores


def _inferir_filtros_relevantes(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
    colunas: list[str],
) -> dict[str, Any]:
    """
    Eu monto um conjunto de filtros relevantes.

    Regra:
    - sempre existe busca global
    - eu priorizo colunas de status/categoria
    - eu priorizo colunas com cara de data
    - eu evito explodir a tela com colunas demais
    """
    categoricos: list[dict[str, Any]] = []
    datas: list[dict[str, Any]] = []

    colunas_candidatas_categoricas: list[str] = []
    colunas_candidatas_data: list[str] = []

    for coluna in colunas:
        if _eh_coluna_data(coluna):
            colunas_candidatas_data.append(coluna)
            continue

        if _eh_coluna_status(coluna):
            colunas_candidatas_categoricas.append(coluna)

    if not colunas_candidatas_categoricas:
        for coluna in colunas:
            nome = coluna.lower()
            if any(
                palavra in nome
                for palavra in ("tipo", "classe", "categoria", "grupo", "perfil", "uf", "origem", "destino")
            ):
                colunas_candidatas_categoricas.append(coluna)

    colunas_candidatas_categoricas = colunas_candidatas_categoricas[:LIMITE_MAXIMO_COLUNAS_FILTRO]
    colunas_candidatas_data = colunas_candidatas_data[:3]

    for coluna in colunas_candidatas_categoricas:
        opcoes = _coletar_valores_distintos_coluna(
            conn_id=conn_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
            coluna=coluna,
        )

        if not opcoes:
            continue

        if len(opcoes) > 80:
            continue

        categoricos.append(
            {
                "chave": coluna,
                "rotulo": coluna,
                "tipo": "categorico_multiplo",
                "opcoes": opcoes,
            }
        )

    for coluna in colunas_candidatas_data:
        datas.append(
            {
                "chave": coluna,
                "rotulo": coluna,
                "tipo": "data_intervalo",
            }
        )

    return {
        "texto_global": True,
        "categoricos": categoricos,
        "datas": datas,
    }


def obter_metadados_tabela(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
) -> dict[str, Any]:
    """Eu retorno metadados completos da tabela para a tela de exploração."""
    colunas = _obter_colunas_tabela(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
    )

    filtros_relevantes = _inferir_filtros_relevantes(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
        colunas=colunas,
    )

    return {
        "conn_id": conn_id,
        "banco": banco,
        "schema": schema,
        "tabela": tabela,
        "nome_qualificado": _montar_nome_qualificado(banco=banco, schema=schema, tabela=tabela),
        "colunas": colunas,
        "filtros_relevantes": filtros_relevantes,
    }


def _montar_where_sql(
    colunas: list[str],
    texto_busca: str | None,
    filtros_categoricos: dict[str, list[str]] | None,
    filtros_datas_de: dict[str, str] | None,
    filtros_datas_ate: dict[str, str] | None,
) -> tuple[str, dict[str, Any]]:
    """Eu monto a cláusula WHERE de forma segura."""
    condicoes: list[str] = []
    parametros: dict[str, Any] = {}

    if texto_busca and str(texto_busca).strip():
        texto_limpo = str(texto_busca).strip()
        parametros["busca_global"] = f"%{texto_limpo}%"

        partes_or: list[str] = []
        for coluna in colunas:
            coluna_sql = _quote_coluna(coluna)
            partes_or.append(f"CAST({coluna_sql} AS NVARCHAR(MAX)) LIKE :busca_global")

        if partes_or:
            condicoes.append("(" + " OR ".join(partes_or) + ")")

    filtros_categoricos = filtros_categoricos or {}
    for coluna, valores in filtros_categoricos.items():
        if coluna not in colunas:
            continue

        valores_limpos = [str(valor).strip() for valor in (valores or []) if str(valor).strip()]
        if not valores_limpos:
            continue

        coluna_sql = _quote_coluna(coluna)
        marcadores: list[str] = []

        for indice, valor in enumerate(valores_limpos):
            nome_parametro = f"cat_{coluna}_{indice}"
            marcadores.append(f":{nome_parametro}")
            parametros[nome_parametro] = valor

        condicoes.append(
            f"CAST({coluna_sql} AS NVARCHAR(4000)) IN ({', '.join(marcadores)})"
        )

    filtros_datas_de = filtros_datas_de or {}
    for coluna, valor in filtros_datas_de.items():
        if coluna not in colunas:
            continue

        valor_limpo = str(valor or "").strip()
        if not valor_limpo:
            continue

        coluna_sql = _quote_coluna(coluna)
        nome_parametro = f"de_{coluna}"
        parametros[nome_parametro] = valor_limpo
        condicoes.append(f"TRY_CONVERT(datetime2, {coluna_sql}) >= TRY_CONVERT(datetime2, :{nome_parametro})")

    filtros_datas_ate = filtros_datas_ate or {}
    for coluna, valor in filtros_datas_ate.items():
        if coluna not in colunas:
            continue

        valor_limpo = str(valor or "").strip()
        if not valor_limpo:
            continue

        coluna_sql = _quote_coluna(coluna)
        nome_parametro = f"ate_{coluna}"
        parametros[nome_parametro] = valor_limpo
        condicoes.append(
            f"TRY_CONVERT(datetime2, {coluna_sql}) < DATEADD(DAY, 1, TRY_CONVERT(datetime2, :{nome_parametro}))"
        )

    if not condicoes:
        return "", parametros

    return "WHERE " + "\n  AND ".join(condicoes), parametros


def _montar_order_by(colunas: list[str], ordenar_por: str | None, direcao: str | None) -> str:
    """Eu monto ORDER BY seguro."""
    if ordenar_por and ordenar_por in colunas:
        direcao_limpa = str(direcao or "asc").strip().lower()
        direcao_sql = "DESC" if direcao_limpa == "desc" else "ASC"
        return f"ORDER BY {_quote_coluna(ordenar_por)} {direcao_sql}"

    return "ORDER BY (SELECT 1)"


def obter_preview_tabela(
    conn_id: str,
    schema: str,
    tabela: str,
    banco: str | None = None,
    limite: int = 20,
) -> dict[str, Any]:
    """Eu retorno preview pequeno para modal rápido."""
    limite = max(1, min(int(limite), LIMITE_MAXIMO_PREVIEW))

    colunas = _obter_colunas_tabela(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
    )

    linhas = _obter_amostra_tabela(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
        limite=limite,
    )

    total_linhas = _obter_total_linhas(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
    )

    return {
        "conn_id": conn_id,
        "banco": banco,
        "schema": schema,
        "tabela": tabela,
        "nome_qualificado": _montar_nome_qualificado(banco=banco, schema=schema, tabela=tabela),
        "limite_amostra": limite,
        "total_linhas": total_linhas,
        "colunas": colunas,
        "linhas": linhas,
    }


def listar_tabela_real(
    conn_id: str,
    schema: str,
    tabela: str,
    banco: str | None = None,
    pagina: int = 1,
    tamanho_pagina: int = 50,
    texto_busca: str | None = None,
    ordenar_por: str | None = None,
    direcao: str | None = "asc",
    filtros_categoricos: dict[str, list[str]] | None = None,
    filtros_datas_de: dict[str, str] | None = None,
    filtros_datas_ate: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Eu retorno a página completa da tabela com filtros relevantes e paginação."""
    pagina = max(1, int(pagina))
    tamanho_pagina = max(1, min(int(tamanho_pagina), LIMITE_MAXIMO_PAGINA))
    offset = (pagina - 1) * tamanho_pagina

    colunas = _obter_colunas_tabela(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
    )

    filtros_relevantes = _inferir_filtros_relevantes(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
        colunas=colunas,
    )

    where_sql, parametros = _montar_where_sql(
        colunas=colunas,
        texto_busca=texto_busca,
        filtros_categoricos=filtros_categoricos,
        filtros_datas_de=filtros_datas_de,
        filtros_datas_ate=filtros_datas_ate,
    )

    total_linhas = _obter_total_linhas(
        conn_id=conn_id,
        banco=banco,
        schema=schema,
        tabela=tabela,
        where_sql=where_sql,
        parametros=parametros,
    )

    total_paginas = max(1, math.ceil(total_linhas / tamanho_pagina)) if tamanho_pagina else 1
    pagina = max(1, min(pagina, total_paginas))
    offset = (pagina - 1) * tamanho_pagina

    order_by_sql = _montar_order_by(
        colunas=colunas,
        ordenar_por=ordenar_por,
        direcao=direcao,
    )

    nome_qualificado = _montar_nome_qualificado(banco=banco, schema=schema, tabela=tabela)

    sql = f"""
    SELECT *
    FROM {nome_qualificado}
    {where_sql}
    {order_by_sql}
    OFFSET :offset ROWS
    FETCH NEXT :tamanho_pagina ROWS ONLY
    """

    parametros_dados = dict(parametros)
    parametros_dados["offset"] = offset
    parametros_dados["tamanho_pagina"] = tamanho_pagina

    linhas = _executar_query_mappings(
        sql=sql,
        parametros=parametros_dados,
        conn_id=conn_id,
    )

    linhas_normalizadas = [_normalizar_linha(linha) for linha in linhas]

    inicio = (pagina - 1) * tamanho_pagina + 1 if total_linhas > 0 else 0
    fim = min(pagina * tamanho_pagina, total_linhas) if total_linhas > 0 else 0

    return {
        "conn_id": conn_id,
        "banco": banco,
        "schema": schema,
        "tabela": tabela,
        "nome_qualificado": nome_qualificado,
        "pagina": pagina,
        "tamanho_pagina": tamanho_pagina,
        "total_linhas": total_linhas,
        "total_paginas": total_paginas,
        "inicio": inicio,
        "fim": fim,
        "has_prev": pagina > 1,
        "has_next": pagina < total_paginas,
        "prev_page": pagina - 1 if pagina > 1 else 1,
        "next_page": pagina + 1 if pagina < total_paginas else total_paginas,
        "colunas": colunas,
        "linhas": linhas_normalizadas,
        "ordenar_por": ordenar_por if ordenar_por in colunas else "",
        "direcao": "desc" if str(direcao or "").lower() == "desc" else "asc",
        "texto_busca": str(texto_busca or "").strip(),
        "filtros_categoricos": filtros_categoricos or {},
        "filtros_datas_de": filtros_datas_de or {},
        "filtros_datas_ate": filtros_datas_ate or {},
        "filtros_relevantes": filtros_relevantes,
    }