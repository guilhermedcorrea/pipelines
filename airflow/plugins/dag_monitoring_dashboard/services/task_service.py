from __future__ import annotations

import re
from typing import Any

from dag_monitoring_dashboard.services.airflow_runtime_service import montar_dashboard_real
from dag_monitoring_dashboard.services.sql_preview_service import (
    listar_tabela_real,
    obter_preview_tabela,
)


CONN_ID_SQL_PADRAO = "mssql_integracao"

PADRAO_TABELA_2_PARTES = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"
)

PADRAO_TABELA_3_PARTES = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"
)

PADRAO_TABELA_COLCHETES_2 = re.compile(
    r"\[([^\[\]]+)\]\.\[([^\[\]]+)\]"
)

PADRAO_TABELA_COLCHETES_3 = re.compile(
    r"\[([^\[\]]+)\]\.\[([^\[\]]+)\]\.\[([^\[\]]+)\]"
)


def _primeiro_preenchido(*valores: Any) -> Any:
    """Eu retorno o primeiro valor útil."""
    for valor in valores:
        if valor is None:
            continue

        if isinstance(valor, str) and not valor.strip():
            continue

        if isinstance(valor, (list, tuple, set)) and len(valor) == 0:
            continue

        if isinstance(valor, dict) and len(valor) == 0:
            continue

        return valor

    return None


def _garantir_lista(valor: Any) -> list[Any]:
    """Eu garanto que o retorno seja lista."""
    if valor is None:
        return []

    if isinstance(valor, list):
        return valor

    if isinstance(valor, tuple):
        return list(valor)

    return [valor]


def _texto_limpo(valor: Any) -> str:
    """Eu transformo qualquer valor em texto limpo."""
    if valor is None:
        return ""

    texto = str(valor).strip()
    if not texto:
        return ""

    return texto


def _coletar_textos_task(task: dict[str, Any]) -> list[str]:
    """Eu reúno campos textuais onde podem existir referências a tabela."""
    textos: list[str] = []

    campos_simples = [
        "fonte_dados",
        "origem_dados",
        "destino_dados",
        "descricao",
        "objetivo",
        "sql",
        "sql_preview",
        "query",
        "consulta",
        "procedure_real",
        "task_doc_md",
    ]

    for campo in campos_simples:
        texto = _texto_limpo(task.get(campo))
        if texto:
            textos.append(texto)

    metricas = task.get("metricas")
    if isinstance(metricas, dict):
        for valor in metricas.values():
            texto = _texto_limpo(valor)
            if texto:
                textos.append(texto)

    regras_upsert = _garantir_lista(task.get("regras_upsert"))
    for item in regras_upsert:
        if isinstance(item, dict):
            for chave in ("titulo", "descricao"):
                texto = _texto_limpo(item.get(chave))
                if texto:
                    textos.append(texto)
        else:
            texto = _texto_limpo(item)
            if texto:
                textos.append(texto)

    guia_transformacoes = _garantir_lista(
        _primeiro_preenchido(
            task.get("guia_transformacoes"),
            task.get("transformation_guide"),
        )
    )
    for item in guia_transformacoes:
        if isinstance(item, dict):
            for chave in ("titulo", "descricao"):
                texto = _texto_limpo(item.get(chave))
                if texto:
                    textos.append(texto)
        else:
            texto = _texto_limpo(item)
            if texto:
                textos.append(texto)

    return textos


def _extrair_referencias_tabela_de_texto(texto: str) -> list[dict[str, str]]:
    """
    Eu extraio candidatas de schema/tabela de um texto livre.

    Regras:
    - 3 partes -> uso as 2 últimas como schema.tabela
      Ex.: DataMart.Gold.FatoX -> Gold.FatoX
    - 2 partes -> uso direto
      Ex.: Silver.FatoX
    """
    referencias: list[dict[str, str]] = []

    if not texto:
        return referencias

    for grupo in PADRAO_TABELA_COLCHETES_3.findall(texto):
        schema = grupo[1].strip()
        tabela = grupo[2].strip()
        if schema and tabela:
            referencias.append({"schema": schema, "tabela": tabela})

    for grupo in PADRAO_TABELA_COLCHETES_2.findall(texto):
        schema = grupo[0].strip()
        tabela = grupo[1].strip()
        if schema and tabela:
            referencias.append({"schema": schema, "tabela": tabela})

    for grupo in PADRAO_TABELA_3_PARTES.findall(texto):
        schema = grupo[1].strip()
        tabela = grupo[2].strip()
        if schema and tabela:
            referencias.append({"schema": schema, "tabela": tabela})

    for grupo in PADRAO_TABELA_2_PARTES.findall(texto):
        schema = grupo[0].strip()
        tabela = grupo[1].strip()
        if schema and tabela:
            referencias.append({"schema": schema, "tabela": tabela})

    referencias_filtradas: list[dict[str, str]] = []
    vistos: set[tuple[str, str]] = set()

    for item in referencias:
        chave = (item["schema"].lower(), item["tabela"].lower())
        if chave in vistos:
            continue
        vistos.add(chave)
        referencias_filtradas.append(item)

    return referencias_filtradas


def _deduzir_conn_id(task: dict[str, Any], objeto: dict[str, Any] | None = None) -> str:
    """Eu tento descobrir o conn_id sem hardcode por DAG."""
    if objeto and objeto.get("conn_id"):
        return str(objeto["conn_id"])

    for campo in ("conn_id", "connection_id", "sql_conn_id"):
        valor = task.get(campo)
        if valor:
            return str(valor)

    origem_tabela = task.get("origem_tabela")
    if isinstance(origem_tabela, dict) and origem_tabela.get("conexao_id"):
        return str(origem_tabela["conexao_id"])

    if isinstance(origem_tabela, dict) and origem_tabela.get("conn_id"):
        return str(origem_tabela["conn_id"])

    destino_tabela = task.get("destino_tabela")
    if isinstance(destino_tabela, dict) and destino_tabela.get("conexao_id"):
        return str(destino_tabela["conexao_id"])

    if isinstance(destino_tabela, dict) and destino_tabela.get("conn_id"):
        return str(destino_tabela["conn_id"])

    objetos = _garantir_lista(task.get("objetos"))
    for item in objetos:
        if isinstance(item, dict) and item.get("conn_id"):
            return str(item["conn_id"])

    return CONN_ID_SQL_PADRAO


def _normalizar_preview_struct(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
) -> dict[str, str | None]:
    """Eu padronizo o objeto previewável."""
    return {
        "conn_id": str(conn_id),
        "banco": str(banco) if banco else None,
        "schema": str(schema),
        "tabela": str(tabela),
    }


def _deduzir_objeto_previewavel(task: dict[str, Any]) -> dict[str, str | None] | None:
    """
    Eu tento descobrir automaticamente qual tabela real abrir.

    Ordem correta:
    1) destino_tabela estruturado
    2) origem_tabela estruturado
    3) objetos estruturados
    4) textos da task
    """
    destino_tabela = task.get("destino_tabela")
    if isinstance(destino_tabela, dict):
        conn_id = _primeiro_preenchido(
            destino_tabela.get("conexao_id"),
            destino_tabela.get("conn_id"),
            CONN_ID_SQL_PADRAO,
        )
        banco = _primeiro_preenchido(destino_tabela.get("banco"), destino_tabela.get("database"))
        schema = _primeiro_preenchido(destino_tabela.get("schema"))
        tabela = _primeiro_preenchido(destino_tabela.get("tabela"))

        if conn_id and schema and tabela:
            return _normalizar_preview_struct(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)

    origem_tabela = task.get("origem_tabela")
    if isinstance(origem_tabela, dict):
        conn_id = _primeiro_preenchido(
            origem_tabela.get("conexao_id"),
            origem_tabela.get("conn_id"),
            CONN_ID_SQL_PADRAO,
        )
        banco = _primeiro_preenchido(origem_tabela.get("banco"), origem_tabela.get("database"))
        schema = _primeiro_preenchido(origem_tabela.get("schema"))
        tabela = _primeiro_preenchido(origem_tabela.get("tabela"))

        if conn_id and schema and tabela:
            return _normalizar_preview_struct(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)

    objetos = _garantir_lista(task.get("objetos"))
    for objeto in objetos:
        if not isinstance(objeto, dict):
            continue

        conn_id = _deduzir_conn_id(task, objeto)
        banco = _primeiro_preenchido(objeto.get("banco"), objeto.get("database"))
        schema = _primeiro_preenchido(objeto.get("schema"))
        tabela = _primeiro_preenchido(objeto.get("tabela"))

        if conn_id and schema and tabela:
            return _normalizar_preview_struct(conn_id=conn_id, banco=banco, schema=schema, tabela=tabela)

    textos = _coletar_textos_task(task)

    for texto in textos:
        referencias = _extrair_referencias_tabela_de_texto(texto)
        if referencias:
            referencia = referencias[0]
            return _normalizar_preview_struct(
                conn_id=_deduzir_conn_id(task),
                banco=None,
                schema=referencia["schema"],
                tabela=referencia["tabela"],
            )

    return None


def _obter_task_dashboard(
    dag_id: str,
    task_id: str,
    run_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Eu busco o dashboard da DAG e localizo a task solicitada."""
    dashboard = montar_dashboard_real(dag_id=dag_id, run_id=run_id)

    tasks = _garantir_lista(dashboard.get("tasks"))
    task_encontrada = None

    for task in tasks:
        atual_id = _primeiro_preenchido(task.get("task_id"), task.get("id"))
        if str(atual_id) == str(task_id):
            task_encontrada = task
            break

    if task_encontrada is None:
        raise ValueError(f"Task '{task_id}' não encontrada na DAG '{dag_id}'.")

    return dashboard, task_encontrada


def obter_detalhe_task(
    dag_id: str,
    task_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Eu retorno os detalhes completos de uma task específica."""
    _dashboard, task = _obter_task_dashboard(
        dag_id=dag_id,
        task_id=task_id,
        run_id=run_id,
    )
    return task


def obter_preview_tabela_task(
    dag_id: str,
    task_id: str,
    run_id: str | None = None,
    limite: int = 20,
) -> dict[str, Any]:
    """Eu abro um preview rápido da tabela real associada à task."""
    _dashboard, task = _obter_task_dashboard(
        dag_id=dag_id,
        task_id=task_id,
        run_id=run_id,
    )

    preview = _deduzir_objeto_previewavel(task)
    if preview is None:
        raise ValueError(
            "Não foi possível descobrir automaticamente conn_id/schema/tabela para "
            f"a task '{task_id}'. A task precisa publicar esses metadados na auditoria."
        )

    resultado = obter_preview_tabela(
        conn_id=str(preview["conn_id"]),
        banco=str(preview["banco"]) if preview.get("banco") else None,
        schema=str(preview["schema"]),
        tabela=str(preview["tabela"]),
        limite=limite,
    )

    return {
        "dag_id": dag_id,
        "run_id": run_id,
        "task_id": task_id,
        "conn_id": resultado["conn_id"],
        "banco": resultado["banco"],
        "schema": resultado["schema"],
        "tabela": resultado["tabela"],
        "nome_qualificado": resultado["nome_qualificado"],
        "limite_amostra": resultado["limite_amostra"],
        "total_linhas": resultado["total_linhas"],
        "colunas": resultado["colunas"],
        "linhas": resultado["linhas"],
    }


def obter_tabela_paginada_task(
    dag_id: str,
    task_id: str,
    run_id: str | None = None,
    pagina: int = 1,
    tamanho_pagina: int = 50,
    texto_busca: str | None = None,
    ordenar_por: str | None = None,
    direcao: str | None = "asc",
    filtros_categoricos: dict[str, list[str]] | None = None,
    filtros_datas_de: dict[str, str] | None = None,
    filtros_datas_ate: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Eu abro a tabela real associada à task com paginação completa."""
    _dashboard, task = _obter_task_dashboard(
        dag_id=dag_id,
        task_id=task_id,
        run_id=run_id,
    )

    preview = _deduzir_objeto_previewavel(task)
    if preview is None:
        raise ValueError(
            "Não foi possível descobrir automaticamente conn_id/schema/tabela para "
            f"a task '{task_id}'. A task precisa publicar esses metadados na auditoria."
        )

    resultado = listar_tabela_real(
        conn_id=str(preview["conn_id"]),
        banco=str(preview["banco"]) if preview.get("banco") else None,
        schema=str(preview["schema"]),
        tabela=str(preview["tabela"]),
        pagina=pagina,
        tamanho_pagina=tamanho_pagina,
        texto_busca=texto_busca,
        ordenar_por=ordenar_por,
        direcao=direcao,
        filtros_categoricos=filtros_categoricos,
        filtros_datas_de=filtros_datas_de,
        filtros_datas_ate=filtros_datas_ate,
    )

    return {
        "dag_id": dag_id,
        "run_id": run_id,
        "task_id": task_id,
        **resultado,
    }
