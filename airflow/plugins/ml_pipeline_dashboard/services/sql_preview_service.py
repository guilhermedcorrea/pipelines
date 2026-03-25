from __future__ import annotations

import importlib
import math
import re
from typing import Any

from sqlalchemy import text


PADRAO_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TIPOS_DATA = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "time"}
TIPOS_NUMERICOS = {
    "int",
    "bigint",
    "smallint",
    "tinyint",
    "decimal",
    "numeric",
    "float",
    "real",
    "money",
    "smallmoney",
    "bit",
}
TIPOS_TEXTO = {
    "varchar",
    "nvarchar",
    "char",
    "nchar",
    "text",
    "ntext",
    "uniqueidentifier",
}


def _importar_hook_sqlserver() -> Any:
    """Eu tento localizar o HookSqlServer já existente no projeto."""
    caminhos = (
        "hooks.BancodeDados.SqlServer",
        "hooks.SqlServer",
        "SqlServer",
        "plugins.ml_pipeline_dashboard.SqlServer",
    )
    for nome_modulo in caminhos:
        try:
            modulo = importlib.import_module(nome_modulo)
        except Exception:
            continue
        hook = getattr(modulo, "HookSqlServer", None)
        if hook is not None:
            return hook
    return None


HookSqlServer = _importar_hook_sqlserver()


def _texto(valor: Any) -> str:
    """Eu converto qualquer valor para texto limpo."""
    return str(valor or "").strip()


def _normalizar_identificador(valor: str, nome_campo: str) -> str:
    """Eu valido identificadores simples para evitar injeção em nomes de objeto."""
    texto_limpo = _texto(valor)
    if not texto_limpo:
        raise ValueError(f"{nome_campo} é obrigatório.")
    if not PADRAO_IDENTIFICADOR.fullmatch(texto_limpo):
        raise ValueError(
            f"{nome_campo} contém caracteres inválidos. Use apenas letras, números e underline, começando por letra ou underscore."
        )
    return texto_limpo


def _colchetes(nome: str) -> str:
    """Eu escapo identificadores SQL Server usando colchetes."""
    nome_limpo = _normalizar_identificador(nome, "Identificador")
    return f"[{nome_limpo}]"


def _nome_qualificado_tabela(conn_id: str, banco: str | None, schema: str, tabela: str) -> dict[str, str | None]:
    """Eu monto representações úteis da tabela alvo."""
    schema_norm = _normalizar_identificador(schema, "schema")
    tabela_norm = _normalizar_identificador(tabela, "tabela")
    banco_norm = _normalizar_identificador(banco, "banco") if _texto(banco) else None

    partes = []
    if banco_norm:
        partes.append(_colchetes(banco_norm))
    partes.append(_colchetes(schema_norm))
    partes.append(_colchetes(tabela_norm))

    nome_sql = ".".join(partes)
    nome_qualificado = f"{banco_norm + '.' if banco_norm else ''}{schema_norm}.{tabela_norm}"

    return {
        "conn_id": _normalizar_identificador(conn_id, "conexao_id"),
        "banco": banco_norm,
        "schema": schema_norm,
        "tabela": tabela_norm,
        "nome_sql": nome_sql,
        "nome_qualificado": nome_qualificado,
    }


def _instanciar_hook(conn_id: str):
    """Eu crio o hook SQL Server já configurado com o conn_id solicitado."""
    if HookSqlServer is None:
        raise ValueError(
            "HookSqlServer não foi encontrado no projeto. Garanta que o arquivo SqlServer.py ou o módulo do hook esteja disponível no Airflow."
        )
    return HookSqlServer(conn_id=conn_id)


def _executar_select(conn_id: str, sql: str, parametros: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Eu executo consultas SELECT via SQLAlchemy do hook do projeto."""
    hook = _instanciar_hook(conn_id)
    engine = hook.obter_engine()
    with engine.connect() as conexao:
        resultado = conexao.execute(text(sql), parametros or {})
        linhas = resultado.mappings().all()
    return [dict(linha) for linha in linhas]


def _obter_metadados_colunas(conn_id: str, banco: str | None, schema: str, tabela: str) -> list[dict[str, Any]]:
    """Eu leio metadados de colunas via INFORMATION_SCHEMA."""
    tabela_info = _nome_qualificado_tabela(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)
    prefixo = f"{_colchetes(tabela_info['banco'])}." if tabela_info["banco"] else ""

    sql = f"""
    SELECT
        COLUMN_NAME AS coluna,
        DATA_TYPE AS tipo_dado,
        IS_NULLABLE AS aceita_nulo,
        ORDINAL_POSITION AS posicao
    FROM {prefixo}INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = :schema
      AND TABLE_NAME = :tabela
    ORDER BY ORDINAL_POSITION
    """
    return _executar_select(
        conn_id=tabela_info["conn_id"],
        sql=sql,
        parametros={"schema": tabela_info["schema"], "tabela": tabela_info["tabela"]},
    )


def _obter_top_distinct(conn_id: str, nome_sql_tabela: str, coluna: str, limite: int = 50) -> list[dict[str, str]]:
    """Eu busco valores distintos para montar filtros categóricos úteis."""
    coluna_sql = _colchetes(coluna)
    sql = f"""
    SELECT TOP ({int(limite)})
        CAST({coluna_sql} AS NVARCHAR(4000)) AS valor
    FROM {nome_sql_tabela}
    WHERE {coluna_sql} IS NOT NULL
      AND LTRIM(RTRIM(CAST({coluna_sql} AS NVARCHAR(4000)))) <> ''
    GROUP BY CAST({coluna_sql} AS NVARCHAR(4000))
    ORDER BY COUNT_BIG(1) DESC, CAST({coluna_sql} AS NVARCHAR(4000)) ASC
    """
    linhas = _executar_select(conn_id=conn_id, sql=sql)
    return [{"valor": _texto(linha.get("valor")), "label": _texto(linha.get("valor"))} for linha in linhas if _texto(linha.get("valor"))]


def _montar_filtros_relevantes(conn_id: str, banco: str | None, schema: str, tabela: str, colunas: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu escolho filtros úteis sem sobrecarregar a página."""
    tabela_info = _nome_qualificado_tabela(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)

    categoricos: list[dict[str, Any]] = []
    datas: list[dict[str, Any]] = []

    colunas_texto = [col for col in colunas if _texto(col.get("tipo_dado")).lower() in TIPOS_TEXTO]
    colunas_data = [col for col in colunas if _texto(col.get("tipo_dado")).lower() in TIPOS_DATA]

    for coluna in colunas_texto[:4]:
        chave = _texto(coluna.get("coluna"))
        categoricos.append(
            {
                "chave": chave,
                "label": chave,
                "opcoes": _obter_top_distinct(
                    conn_id=tabela_info["conn_id"],
                    nome_sql_tabela=tabela_info["nome_sql"],
                    coluna=chave,
                    limite=40,
                ),
            }
        )

    for coluna in colunas_data[:3]:
        chave = _texto(coluna.get("coluna"))
        datas.append({"chave": chave, "label": chave})

    return {"categoricos": categoricos, "datas": datas}


def _montar_where(
    colunas_disponiveis: list[str],
    filtros_categoricos: dict[str, list[str]],
    filtros_datas_de: dict[str, str],
    filtros_datas_ate: dict[str, str],
    texto_busca: str | None,
) -> tuple[str, dict[str, Any]]:
    """Eu monto o WHERE parametrizado para paginação segura."""
    clausulas: list[str] = []
    parametros: dict[str, Any] = {}
    colunas_set = {col.lower(): col for col in colunas_disponiveis}

    for chave, valores in (filtros_categoricos or {}).items():
        chave_real = colunas_set.get(_texto(chave).lower())
        if not chave_real:
            continue
        nomes_parametros: list[str] = []
        for indice, valor in enumerate(valores, start=1):
            nome_parametro = f"cat_{chave_real}_{indice}"
            nomes_parametros.append(f":{nome_parametro}")
            parametros[nome_parametro] = valor
        if nomes_parametros:
            clausulas.append(f"{_colchetes(chave_real)} IN ({', '.join(nomes_parametros)})")

    for chave, valor in (filtros_datas_de or {}).items():
        chave_real = colunas_set.get(_texto(chave).lower())
        if not chave_real or not _texto(valor):
            continue
        nome_parametro = f"de_{chave_real}"
        parametros[nome_parametro] = _texto(valor)
        clausulas.append(f"CAST({_colchetes(chave_real)} AS DATE) >= :{nome_parametro}")

    for chave, valor in (filtros_datas_ate or {}).items():
        chave_real = colunas_set.get(_texto(chave).lower())
        if not chave_real or not _texto(valor):
            continue
        nome_parametro = f"ate_{chave_real}"
        parametros[nome_parametro] = _texto(valor)
        clausulas.append(f"CAST({_colchetes(chave_real)} AS DATE) <= :{nome_parametro}")

    if _texto(texto_busca):
        texto_busca_limpo = f"%{_texto(texto_busca)}%"
        parametros["texto_busca"] = texto_busca_limpo
        colunas_busca = [col for col in colunas_disponiveis[:8]]
        if colunas_busca:
            or_clausulas = [f"CAST({_colchetes(coluna)} AS NVARCHAR(4000)) LIKE :texto_busca" for coluna in colunas_busca]
            clausulas.append("(" + " OR ".join(or_clausulas) + ")")

    if not clausulas:
        return "", parametros

    return " WHERE " + " AND ".join(clausulas), parametros


def obter_metadados_tabela(conn_id: str, banco: str | None, schema: str, tabela: str) -> dict[str, Any]:
    """Eu devolvo metadados suficientes para a tela de tabela e seus filtros."""
    tabela_info = _nome_qualificado_tabela(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)
    colunas = _obter_metadados_colunas(
        conn_id=tabela_info["conn_id"],
        banco=tabela_info["banco"],
        schema=tabela_info["schema"],
        tabela=tabela_info["tabela"],
    )
    if not colunas:
        raise ValueError(f"A tabela '{tabela_info['nome_qualificado']}' não possui colunas visíveis ou não foi encontrada.")

    filtros_relevantes = _montar_filtros_relevantes(
        conn_id=tabela_info["conn_id"],
        banco=tabela_info["banco"],
        schema=tabela_info["schema"],
        tabela=tabela_info["tabela"],
        colunas=colunas,
    )

    return {
        **tabela_info,
        "colunas": colunas,
        "filtros_relevantes": filtros_relevantes,
    }


def obter_preview_tabela(conn_id: str, banco: str | None, schema: str, tabela: str, limite: int = 20) -> dict[str, Any]:
    """Eu devolvo uma amostra rápida da tabela real."""
    metadados = obter_metadados_tabela(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)
    limite = max(1, min(int(limite), 100))

    sql = f"SELECT TOP ({limite}) * FROM {metadados['nome_sql']}"
    linhas = _executar_select(conn_id=metadados["conn_id"], sql=sql)
    colunas = [_texto(coluna.get("coluna")) for coluna in metadados["colunas"]]

    return {
        "conn_id": metadados["conn_id"],
        "banco": metadados["banco"],
        "schema": metadados["schema"],
        "tabela": metadados["tabela"],
        "nome_qualificado": metadados["nome_qualificado"],
        "colunas": colunas,
        "linhas": linhas,
        "filtros_relevantes": metadados["filtros_relevantes"],
        "limite": limite,
    }


def listar_tabela_real(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
    pagina: int = 1,
    tamanho_pagina: int = 50,
    texto_busca: str | None = None,
    ordenar_por: str | None = None,
    direcao: str | None = "asc",
    filtros_categoricos: dict[str, list[str]] | None = None,
    filtros_datas_de: dict[str, str] | None = None,
    filtros_datas_ate: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Eu devolvo a tabela paginada com filtros, busca e ordenação."""
    metadados = obter_metadados_tabela(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)
    pagina = max(1, int(pagina))
    tamanho_pagina = max(1, min(int(tamanho_pagina), 500))

    colunas = [_texto(coluna.get("coluna")) for coluna in metadados["colunas"]]
    where_sql, parametros = _montar_where(
        colunas_disponiveis=colunas,
        filtros_categoricos=filtros_categoricos or {},
        filtros_datas_de=filtros_datas_de or {},
        filtros_datas_ate=filtros_datas_ate or {},
        texto_busca=texto_busca,
    )

    ordenar_padrao = colunas[0]
    ordenar_por_real = _texto(ordenar_por) if _texto(ordenar_por) in colunas else ordenar_padrao
    direcao_real = "DESC" if _texto(direcao).lower() == "desc" else "ASC"

    sql_total = f"SELECT COUNT_BIG(1) AS total_linhas FROM {metadados['nome_sql']}{where_sql}"
    total_linhas = int(_executar_select(conn_id=metadados["conn_id"], sql=sql_total, parametros=parametros)[0]["total_linhas"])

    offset = (pagina - 1) * tamanho_pagina
    sql_dados = f"""
    SELECT *
    FROM {metadados['nome_sql']}
    {where_sql}
    ORDER BY {_colchetes(ordenar_por_real)} {direcao_real}
    OFFSET :offset ROWS FETCH NEXT :fetch ROWS ONLY
    """
    parametros_dados = dict(parametros)
    parametros_dados["offset"] = offset
    parametros_dados["fetch"] = tamanho_pagina
    linhas = _executar_select(conn_id=metadados["conn_id"], sql=sql_dados, parametros=parametros_dados)

    total_paginas = max(1, math.ceil(total_linhas / tamanho_pagina)) if total_linhas else 1

    return {
        "conn_id": metadados["conn_id"],
        "banco": metadados["banco"],
        "schema": metadados["schema"],
        "tabela": metadados["tabela"],
        "nome_qualificado": metadados["nome_qualificado"],
        "colunas": colunas,
        "linhas": linhas,
        "pagina": pagina,
        "tamanho_pagina": tamanho_pagina,
        "total_linhas": total_linhas,
        "total_paginas": total_paginas,
        "ordenar_por": ordenar_por_real,
        "direcao": direcao_real.lower(),
        "texto_busca": _texto(texto_busca) or None,
        "filtros_relevantes": metadados["filtros_relevantes"],
    }


__all__ = [
    "obter_metadados_tabela",
    "obter_preview_tabela",
    "listar_tabela_real",
]
