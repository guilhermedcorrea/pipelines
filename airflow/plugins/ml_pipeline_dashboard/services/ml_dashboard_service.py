
from __future__ import annotations

import importlib
import re
from typing import Any

from ml_pipeline_dashboard.config import CONFIGURACAO
from ml_pipeline_dashboard.services.airflow_runtime_service import (
    listar_execucoes_reais,
    montar_dashboard_real,
)


def _normalizar_tag(valor: Any) -> str:
    """Eu normalizo tags para comparar sem diferença de caixa, espaço ou separador."""
    texto = str(valor or "").strip().lower()
    return texto.replace("-", "").replace("_", "").replace(" ", "")


def _valor_preenchido(valor: Any) -> bool:
    """Eu verifico se o valor realmente contém informação útil."""
    if valor is None:
        return False

    if isinstance(valor, str):
        return bool(valor.strip())

    if isinstance(valor, (list, tuple, set, dict)):
        return len(valor) > 0

    return True


def _primeiro_preenchido(*valores: Any) -> Any:
    """Eu devolvo o primeiro valor útil entre vários candidatos."""
    for valor in valores:
        if _valor_preenchido(valor):
            return valor
    return None


def _garantir_lista(valor: Any) -> list[Any]:
    """Eu normalizo qualquer valor para lista."""
    if valor is None:
        return []

    if isinstance(valor, list):
        return valor

    if isinstance(valor, tuple):
        return list(valor)

    if isinstance(valor, set):
        return list(valor)

    return [valor]


def _resolver_funcao(modulos: tuple[str, ...], nomes_funcoes: tuple[str, ...]):
    """Eu localizo funções opcionais sem quebrar a importação do service."""
    for nome_modulo in modulos:
        try:
            modulo = importlib.import_module(nome_modulo)
        except Exception:
            continue

        for nome_funcao in nomes_funcoes:
            funcao = getattr(modulo, nome_funcao, None)
            if callable(funcao):
                return funcao

    return None


_OBTER_PREVIEW_TABELA = _resolver_funcao(
    modulos=("ml_pipeline_dashboard.services.sql_preview_service",),
    nomes_funcoes=("obter_preview_tabela",),
)

_LISTAR_TABELA_REAL = _resolver_funcao(
    modulos=("ml_pipeline_dashboard.services.sql_preview_service",),
    nomes_funcoes=("listar_tabela_real",),
)


def _eh_pipeline_ml_por_item(item: dict[str, Any]) -> bool:
    """Eu identifico se um item da listagem pertence a Machine Learning."""
    tags_item = {_normalizar_tag(tag) for tag in _garantir_lista(item.get("dag_tags"))}
    tags_config = {_normalizar_tag(tag) for tag in CONFIGURACAO.tags_pipeline_ml}

    if tags_item and tags_item.intersection(tags_config):
        return True

    dag_id = str(item.get("dag_id") or "").strip().lower()
    if not dag_id:
        return False

    return any(palavra in dag_id for palavra in CONFIGURACAO.palavras_chave_heuristica_ml)


def _classificar_tipo_pipeline(tags: list[str], dag_id: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu tento classificar o pipeline de forma genérica e reutilizável."""
    texto_busca = " ".join(
        [str(dag_id or "")]
        + [str(tag) for tag in tags]
        + [str(task.get("task_id") or "") for task in tasks]
        + [str(task.get("descricao") or "") for task in tasks]
    ).lower()

    if any(chave in texto_busca for chave in ("classifica", "classificação", "auc", "precision", "recall")):
        subtipo = "classificacao"
    elif any(chave in texto_busca for chave in ("regress", "rmse", "mae", "mape")):
        subtipo = "regressao"
    elif any(chave in texto_busca for chave in ("forecast", "previsao", "previsão", "time series", "série temporal")):
        subtipo = "forecast"
    elif any(chave in texto_busca for chave in ("cluster", "segmenta", "segmentação")):
        subtipo = "clusterizacao"
    else:
        subtipo = "machine_learning"

    dominio = None
    if any(chave in texto_busca for chave in ("cliente", "empresa", "perfil", "score")):
        dominio = "clientes_empresas"
    elif any(chave in texto_busca for chave in ("noticia", "notícias", "macro", "setor")):
        dominio = "noticias_macro"
    elif any(chave in texto_busca for chave in ("comercial", "venda", "campanha")):
        dominio = "comercial"

    return {
        "tipo_pipeline": "machine_learning",
        "subtipo_pipeline": subtipo,
        "dominio": dominio,
    }


def _classificar_tipo_etapa(task: dict[str, Any]) -> str:
    """Eu classifico a etapa para o front conseguir renderizar comportamento e ícones diferentes."""
    texto = " ".join(
        [
            str(task.get("task_id") or ""),
            str(task.get("nome_amigavel") or ""),
            str(task.get("descricao") or ""),
            str(task.get("operacao") or ""),
        ]
    ).lower()

    if any(chave in texto for chave in ("extrair", "extract", "query", "sql", "coleta", "dataset bruto")):
        return "extracao"
    if any(chave in texto for chave in ("prepar", "feature", "limpa", "transform", "dataset preparado")):
        return "preparacao"
    if any(chave in texto for chave in ("trein", "valid", "score", "catboost", "modelo")):
        return "treino_validacao_score"
    if any(chave in texto for chave in ("atualiza", "upsert", "merge", "dimclass", "persist", "grava")):
        return "persistencia"
    return "etapa_generica"


def _inferir_modelo_usado(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu procuro evidências do modelo nos textos, métricas e artefatos das tasks."""
    candidatos_textuais: list[str] = []
    metricas_principais: dict[str, Any] = {}
    artefatos_modelo: list[dict[str, Any]] = []

    for task in tasks:
        candidatos_textuais.extend(
            [
                str(task.get("descricao") or ""),
                str(task.get("nome_amigavel") or ""),
                str(task.get("operacao") or ""),
                str(task.get("sql") or ""),
            ]
        )
        metricas_extras = task.get("metricas_extras") or {}
        if isinstance(metricas_extras, dict):
            for chave in (
                "auc_walk_forward",
                "auc_teste_final_oot",
                "iteracoes_finais",
                "quantidade_folds_walk_forward",
                "mes_snapshot_mais_recente",
            ):
                if chave in metricas_extras and chave not in metricas_principais:
                    metricas_principais[chave] = metricas_extras[chave]

        for objeto in _garantir_lista(task.get("objetos")):
            nome_objeto = str(objeto.get("nome") or "").lower()
            caminho = str(objeto.get("caminho_arquivo") or "").lower()
            if any(chave in nome_objeto for chave in ("metrica", "score", "import", "faixa", "walk_forward")) or any(
                chave in caminho for chave in ("metric", "score", "import", "faixa", "walk_forward")
            ):
                artefatos_modelo.append(objeto)

    texto_total = " ".join(candidatos_textuais).lower()

    nome_modelo = None
    familia_modelo = None
    if "catboost" in texto_total:
        nome_modelo = "CatBoost"
        familia_modelo = "gradient_boosting"
    elif "xgboost" in texto_total:
        nome_modelo = "XGBoost"
        familia_modelo = "gradient_boosting"
    elif "lightgbm" in texto_total:
        nome_modelo = "LightGBM"
        familia_modelo = "gradient_boosting"
    elif any(chave in texto_total for chave in ("bert", "transformer", "nlp")):
        nome_modelo = "Transformer / BERT"
        familia_modelo = "deep_learning"

    return {
        "nome_modelo": nome_modelo,
        "familia_modelo": familia_modelo,
        "metricas_principais": metricas_principais,
        "artefatos_relacionados": artefatos_modelo,
    }


def _montar_resumo_objetos(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu consolido entradas, saídas e artefatos para o painel superior do pipeline."""
    entradas: list[dict[str, Any]] = []
    saidas: list[dict[str, Any]] = []
    apoio: list[dict[str, Any]] = []

    for task in tasks:
        for objeto in _garantir_lista(task.get("objetos")):
            direcao = str(objeto.get("direcao") or "neutro").lower()
            if direcao == "entrada":
                entradas.append(objeto)
            elif direcao == "saida":
                saidas.append(objeto)
            else:
                apoio.append(objeto)

    def deduplicar(lista: list[dict[str, Any]]) -> list[dict[str, Any]]:
        vistos: set[tuple[str, str, str, str]] = set()
        retorno: list[dict[str, Any]] = []
        for item in lista:
            chave = (
                str(item.get("tipo") or ""),
                str(item.get("conn_id") or ""),
                str(item.get("schema") or ""),
                str(item.get("tabela") or item.get("caminho_arquivo") or item.get("nome") or ""),
            )
            if chave in vistos:
                continue
            vistos.add(chave)
            retorno.append(item)
        return retorno

    return {
        "entradas": deduplicar(entradas),
        "saidas": deduplicar(saidas),
        "apoio": deduplicar(apoio),
    }


def _normalizar_validacoes(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu consolido validações por task e contadores globais."""
    todas_validacoes: list[dict[str, Any]] = []
    ok = 0
    alerta = 0
    erro = 0

    for task in tasks:
        for validacao in _garantir_lista(task.get("validacoes")):
            if isinstance(validacao, dict):
                item = dict(validacao)
            else:
                item = {"descricao": str(validacao)}

            item["task_id"] = task.get("task_id")
            item["task_nome"] = task.get("nome_amigavel") or task.get("nome") or task.get("task_id")
            status = str(item.get("status") or "").strip().lower()
            if status == "ok":
                ok += 1
            elif status in {"alerta", "warning"}:
                alerta += 1
            elif status in {"erro", "error", "failed"}:
                erro += 1
            todas_validacoes.append(item)

    return {
        "ok": ok,
        "alerta": alerta,
        "erro": erro,
        "itens": todas_validacoes,
    }


def _normalizar_observacoes(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eu consolido observações livres das tasks."""
    retorno: list[dict[str, Any]] = []
    for task in tasks:
        for observacao in _garantir_lista(task.get("observacoes")):
            if isinstance(observacao, dict):
                item = dict(observacao)
            else:
                item = {"descricao": str(observacao)}
            item.setdefault("task_id", task.get("task_id"))
            item.setdefault("task_nome", task.get("nome_amigavel") or task.get("nome") or task.get("task_id"))
            retorno.append(item)
    return retorno


def _enriquecer_tasks_ml(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eu adiciono classificação de etapa e indicadores úteis para o front de ML."""
    retorno: list[dict[str, Any]] = []

    for indice, task in enumerate(tasks, start=1):
        copia = dict(task)
        tipo_etapa_ml = _classificar_tipo_etapa(copia)
        copia["tipo_etapa_ml"] = tipo_etapa_ml
        copia["ordem_pipeline"] = indice
        copia["tem_sql"] = bool(str(copia.get("sql") or "").strip())
        copia["tem_preview_tabela"] = bool(_garantir_lista(copia.get("tabela", {}).get("linhas") if isinstance(copia.get("tabela"), dict) else []))
        copia["tem_objetos_visualizaveis"] = any(bool(objeto.get("visualizavel")) for objeto in _garantir_lista(copia.get("objetos")))
        copia["tem_arquivos_baixaveis"] = any(bool(objeto.get("downloadable")) for objeto in _garantir_lista(copia.get("objetos")))
        retorno.append(copia)

    return retorno


def _extrair_documentacao_pipeline(dashboard: dict[str, Any]) -> dict[str, Any]:
    """Eu separo documentação de negócio e documentação técnica."""
    return {
        "dag_descricao": dashboard.get("dag_descricao") or dashboard.get("descricao"),
        "documentacao_dag": dashboard.get("documentacao_dag"),
        "explicacao_execucao": (
            "Este painel mostra a execução real do pipeline de Machine Learning, incluindo tarefas, "
            "auditoria por etapa, objetos de entrada e saída, amostras reais, métricas, validações e artefatos."
        ),
    }


def list_pipelines_ml_filtrados(payload: dict[str, Any]) -> dict[str, Any]:
    """Eu filtro a listagem base para manter apenas pipelines compatíveis com ML."""
    itens = [item for item in _garantir_lista(payload.get("itens")) if isinstance(item, dict) and _eh_pipeline_ml_por_item(item)]
    return {
        "total": len(itens),
        "itens": itens,
        "filtro_aplicado": "machine_learning",
    }


def listar_pipelines_ml(
    dag_id: str | None = None,
    status: str | None = None,
    limite: int = 50,
    apenas_ml: bool = True,
) -> dict[str, Any]:
    """Eu listo execuções e retorno apenas os DAGs classificados como Machine Learning."""
    payload = listar_execucoes_reais(dag_id=dag_id, status=status, limite=limite)
    if not apenas_ml:
        return payload
    return list_pipelines_ml_filtrados(payload)


def obter_dashboard_pipeline_ml(dag_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Eu especializo o dashboard real do Airflow para o domínio de Machine Learning."""
    dashboard = montar_dashboard_real(dag_id=dag_id, run_id=run_id)

    tasks_enriquecidas = _enriquecer_tasks_ml(_garantir_lista(dashboard.get("tasks")))
    classificacao_pipeline = _classificar_tipo_pipeline(
        tags=_garantir_lista(dashboard.get("tags")),
        dag_id=str(dashboard.get("dag_id") or dag_id),
        tasks=tasks_enriquecidas,
    )
    modelo = _inferir_modelo_usado(tasks_enriquecidas)
    objetos = _montar_resumo_objetos(tasks_enriquecidas)
    validacoes = _normalizar_validacoes(tasks_enriquecidas)
    observacoes = _normalizar_observacoes(tasks_enriquecidas)
    documentacao = _extrair_documentacao_pipeline(dashboard)

    dashboard_enriquecido = dict(dashboard)
    dashboard_enriquecido["tasks"] = tasks_enriquecidas
    dashboard_enriquecido["pipeline"] = {
        **classificacao_pipeline,
        "modelo": modelo,
        "objetos": objetos,
        "validacoes": validacoes,
        "observacoes": observacoes,
        "documentacao": documentacao,
    }
    dashboard_enriquecido["tipo_pipeline"] = classificacao_pipeline["tipo_pipeline"]
    dashboard_enriquecido["subtipo_pipeline"] = classificacao_pipeline["subtipo_pipeline"]
    dashboard_enriquecido["dominio"] = classificacao_pipeline["dominio"]
    dashboard_enriquecido["modelo"] = modelo
    dashboard_enriquecido["entradas"] = objetos["entradas"]
    dashboard_enriquecido["saidas"] = objetos["saidas"]
    dashboard_enriquecido["artefatos_apoio"] = objetos["apoio"]
    dashboard_enriquecido["validacoes_consolidadas"] = validacoes
    dashboard_enriquecido["observacoes_consolidadas"] = observacoes
    dashboard_enriquecido["documentacao"] = documentacao

    return dashboard_enriquecido


def obter_detalhe_task_pipeline_ml(dag_id: str, task_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Eu devolvo o detalhe de uma task a partir do dashboard consolidado do pipeline."""
    dashboard = obter_dashboard_pipeline_ml(dag_id=dag_id, run_id=run_id)
    tasks = _garantir_lista(dashboard.get("tasks"))

    for task in tasks:
        if str(task.get("task_id") or "").strip() == str(task_id or "").strip():
            return {
                "dag_id": dashboard.get("dag_id"),
                "run_id": dashboard.get("run_id"),
                "pipeline": dashboard.get("pipeline"),
                "task": task,
            }

    raise ValueError(f"Task '{task_id}' não encontrada na DAG '{dag_id}'.")


def _obter_primeiro_objeto_tabela_visualizavel(task: dict[str, Any]) -> dict[str, Any] | None:
    """Eu localizo a melhor tabela visualizável associada à task."""
    for objeto in _garantir_lista(task.get("objetos")):
        if bool(objeto.get("visualizavel")):
            return objeto
    return None


def obter_preview_tabela_task_pipeline_ml(
    dag_id: str,
    task_id: str,
    run_id: str | None = None,
    limite: int = 20,
) -> dict[str, Any]:
    """Eu abro um preview da tabela real associada à task, se existir um objeto visualizável."""
    if _OBTER_PREVIEW_TABELA is None:
        raise ValueError(
            "sql_preview_service.obter_preview_tabela não está disponível. "
            "Crie o service de preview SQL antes de usar esta rota."
        )

    detalhe = obter_detalhe_task_pipeline_ml(dag_id=dag_id, task_id=task_id, run_id=run_id)
    task = detalhe["task"]
    objeto = _obter_primeiro_objeto_tabela_visualizavel(task)
    if objeto is None:
        raise ValueError(
            f"A task '{task_id}' não possui nenhum objeto de tabela visualizável com conn_id/schema/tabela."
        )

    return _OBTER_PREVIEW_TABELA(
        conn_id=objeto["conn_id"],
        banco=objeto.get("banco"),
        schema=objeto["schema"],
        tabela=objeto["tabela"],
        limite=limite,
    )


def obter_tabela_paginada_task_pipeline_ml(
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
    """Eu devolvo a tabela paginada da task, reaproveitando o service SQL quando existir."""
    if _LISTAR_TABELA_REAL is None:
        raise ValueError(
            "sql_preview_service.listar_tabela_real não está disponível. "
            "Crie o service de preview SQL antes de usar esta rota."
        )

    detalhe = obter_detalhe_task_pipeline_ml(dag_id=dag_id, task_id=task_id, run_id=run_id)
    task = detalhe["task"]
    objeto = _obter_primeiro_objeto_tabela_visualizavel(task)
    if objeto is None:
        raise ValueError(
            f"A task '{task_id}' não possui nenhum objeto de tabela visualizável com conn_id/schema/tabela."
        )

    return _LISTAR_TABELA_REAL(
        conn_id=objeto["conn_id"],
        banco=objeto.get("banco"),
        schema=objeto["schema"],
        tabela=objeto["tabela"],
        pagina=pagina,
        tamanho_pagina=tamanho_pagina,
        texto_busca=texto_busca,
        ordenar_por=ordenar_por,
        direcao=direcao,
        filtros_categoricos=filtros_categoricos or {},
        filtros_datas_de=filtros_datas_de or {},
        filtros_datas_ate=filtros_datas_ate or {},
    )


__all__ = [
    "listar_pipelines_ml",
    "obter_dashboard_pipeline_ml",
    "obter_detalhe_task_pipeline_ml",
    "obter_preview_tabela_task_pipeline_ml",
    "obter_tabela_paginada_task_pipeline_ml",
]
