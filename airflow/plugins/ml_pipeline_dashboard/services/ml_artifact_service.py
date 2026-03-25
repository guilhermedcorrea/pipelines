from __future__ import annotations

from pathlib import Path
from typing import Any


EXTENSOES_VISUALIZAVEIS_TEXTO = {
    ".txt",
    ".sql",
    ".json",
    ".yml",
    ".yaml",
    ".md",
    ".log",
    ".csv",
    ".html",
    ".htm",
}

EXTENSOES_DOWNLOADAVEIS = {
    ".csv",
    ".xlsx",
    ".xls",
    ".parquet",
    ".json",
    ".txt",
    ".sql",
    ".html",
    ".htm",
    ".md",
    ".log",
    ".pkl",
    ".joblib",
    ".pdf",
    ".zip",
    ".gz",
    ".yml",
    ".yaml",
}

TIPOS_PRIORIDADE = {
    "modelo": 1,
    "metrica": 2,
    "métrica": 2,
    "tabela": 3,
    "arquivo": 4,
    "procedure": 5,
    "objeto_sql": 6,
    "desconhecido": 99,
}


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


def _texto(valor: Any) -> str:
    """Eu converto qualquer valor para texto limpo."""
    return str(valor or "").strip()


def _humanizar(texto: str) -> str:
    """Eu transformo identificadores técnicos em texto amigável."""
    valor = _texto(texto)
    if not valor:
        return "-"
    partes = [parte for parte in valor.replace("-", "_").replace(".", "_").split("_") if parte]
    if not partes:
        return valor
    return " ".join(parte.capitalize() for parte in partes)


def _tipo_artefato(item: dict[str, Any]) -> str:
    """Eu classifico o tipo dominante do artefato."""
    tipo_bruto = _texto(item.get("tipo")).lower()
    nome = _texto(item.get("nome")).lower()
    caminho = _texto(item.get("caminho_arquivo")).lower()

    if tipo_bruto in {"tabela", "arquivo", "procedure", "objeto_sql", "modelo", "metrica", "métrica"}:
        return tipo_bruto

    if any(chave in nome for chave in ("modelo", "model", "catboost", "xgboost", "lightgbm", "bert")):
        return "modelo"

    if any(chave in nome for chave in ("metrica", "métrica", "auc", "ks", "brier", "logloss", "score")):
        return "metrica"

    if item.get("schema") and item.get("tabela"):
        return "tabela"

    if caminho:
        return "arquivo"

    return "desconhecido"


def _grupo_artefato(item: dict[str, Any]) -> str:
    """Eu agrupo o artefato pelo papel no pipeline."""
    direcao = _texto(item.get("direcao")).lower()
    tipo = _tipo_artefato(item)

    if tipo == "modelo":
        return "modelo"
    if tipo in {"metrica", "métrica"}:
        return "metrica"
    if direcao == "entrada":
        return "entrada"
    if direcao == "saida":
        return "saida"
    return "apoio"


def _nome_exibicao_artefato(item: dict[str, Any]) -> str:
    """Eu monto o melhor nome visível para o artefato."""
    nome = _texto(item.get("nome"))
    if nome:
        return nome

    caminho = _texto(item.get("caminho_arquivo"))
    if caminho:
        return Path(caminho).name

    schema = _texto(item.get("schema"))
    tabela = _texto(item.get("tabela"))
    banco = _texto(item.get("banco"))
    if schema and tabela:
        if banco:
            return f"{banco}.{schema}.{tabela}"
        return f"{schema}.{tabela}"

    procedure = _texto(item.get("procedure"))
    if procedure:
        return procedure

    return "Artefato sem nome"


def _descricao_artefato(item: dict[str, Any]) -> str:
    """Eu descrevo o artefato com foco no que o usuário precisa entender."""
    tipo = _tipo_artefato(item)
    grupo = _grupo_artefato(item)
    nome = _nome_exibicao_artefato(item)

    if tipo == "tabela":
        conn_id = _texto(item.get("conn_id")) or "conn_id não informado"
        return f"Tabela {nome} ligada à conexão {conn_id}. Grupo: {grupo}."

    caminho = _texto(item.get("caminho_arquivo"))
    if tipo == "arquivo" and caminho:
        return f"Arquivo {Path(caminho).name} em {caminho}. Grupo: {grupo}."

    if tipo == "modelo":
        return f"Artefato de modelo ou serialização de modelo relacionado a {nome}."

    if tipo in {"metrica", "métrica"}:
        return f"Artefato de métricas ou avaliação associado a {nome}."

    if tipo == "procedure":
        return f"Procedure SQL referenciada: {nome}."

    return f"Artefato {nome}. Grupo: {grupo}."


def _descobrir_extensao(item: dict[str, Any]) -> str:
    """Eu obtenho a extensão do arquivo quando houver caminho físico."""
    caminho = _texto(item.get("caminho_arquivo"))
    if not caminho:
        return ""
    return Path(caminho).suffix.lower()


def _pode_visualizar(item: dict[str, Any]) -> bool:
    """Eu marco se o artefato é visualizável diretamente no plugin."""
    if bool(item.get("visualizavel")):
        return True
    if _tipo_artefato(item) == "tabela":
        return True
    return _descobrir_extensao(item) in EXTENSOES_VISUALIZAVEIS_TEXTO


def _pode_baixar(item: dict[str, Any]) -> bool:
    """Eu marco se o artefato faz sentido ser baixado pelo front."""
    if bool(item.get("downloadable")):
        return True
    return _descobrir_extensao(item) in EXTENSOES_DOWNLOADAVEIS


def normalizar_artefato(item: Any, task_id: str | None = None) -> dict[str, Any]:
    """Eu transformo qualquer representação de artefato em estrutura consistente."""
    base = dict(item) if isinstance(item, dict) else {"nome": _texto(item)}

    tipo = _tipo_artefato(base)
    grupo = _grupo_artefato(base)
    caminho = _texto(base.get("caminho_arquivo"))

    artefato = {
        "task_id": _texto(task_id),
        "nome": _nome_exibicao_artefato(base),
        "nome_amigavel": _humanizar(_nome_exibicao_artefato(base)),
        "descricao": _descricao_artefato(base),
        "tipo": tipo,
        "grupo": grupo,
        "direcao": _texto(base.get("direcao")) or ("entrada" if grupo == "entrada" else "saida" if grupo == "saida" else "apoio"),
        "conn_id": _texto(base.get("conn_id")) or None,
        "banco": _texto(base.get("banco")) or None,
        "schema": _texto(base.get("schema")) or None,
        "tabela": _texto(base.get("tabela")) or None,
        "procedure": _texto(base.get("procedure")) or None,
        "caminho_arquivo": caminho or None,
        "extensao": _descobrir_extensao(base) or None,
        "visualizavel": _pode_visualizar(base),
        "downloadable": _pode_baixar(base),
        "referencia": {
            "conn_id": _texto(base.get("conn_id")) or None,
            "banco": _texto(base.get("banco")) or None,
            "schema": _texto(base.get("schema")) or None,
            "tabela": _texto(base.get("tabela")) or None,
            "caminho_arquivo": caminho or None,
        },
        "payload_original": base,
    }

    return artefato


def _chave_deduplicacao(item: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    """Eu crio a chave lógica usada para remover duplicidades."""
    return (
        _texto(item.get("tipo")).lower(),
        _texto(item.get("conn_id")).lower(),
        _texto(item.get("banco")).lower(),
        _texto(item.get("schema")).lower(),
        _texto(item.get("tabela")).lower(),
        _texto(item.get("caminho_arquivo")).lower(),
    )


def listar_artefatos_task(task: dict[str, Any]) -> dict[str, Any]:
    """Eu organizo os artefatos de uma única task."""
    task_id = _texto(task.get("task_id") or task.get("id"))
    artefatos = [normalizar_artefato(item, task_id=task_id) for item in _garantir_lista(task.get("objetos"))]

    vistos: set[tuple[str, str, str, str, str, str]] = set()
    artefatos_deduplicados: list[dict[str, Any]] = []
    for artefato in artefatos:
        chave = _chave_deduplicacao(artefato)
        if chave in vistos:
            continue
        vistos.add(chave)
        artefatos_deduplicados.append(artefato)

    artefatos_ordenados = sorted(
        artefatos_deduplicados,
        key=lambda item: (
            TIPOS_PRIORIDADE.get(_texto(item.get("tipo")).lower(), 999),
            _texto(item.get("grupo")).lower(),
            _texto(item.get("nome")).lower(),
        ),
    )

    grupos: dict[str, list[dict[str, Any]]] = {
        "entrada": [],
        "saida": [],
        "apoio": [],
        "modelo": [],
        "metrica": [],
    }

    for artefato in artefatos_ordenados:
        grupo = _texto(artefato.get("grupo")).lower()
        if grupo not in grupos:
            grupos[grupo] = []
        grupos[grupo].append(artefato)

    return {
        "task_id": task_id,
        "total": len(artefatos_ordenados),
        "quantidade_visualizavel": sum(1 for item in artefatos_ordenados if item.get("visualizavel")),
        "quantidade_downloadable": sum(1 for item in artefatos_ordenados if item.get("downloadable")),
        "itens": artefatos_ordenados,
        "grupos": grupos,
    }


def listar_artefatos_pipeline(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu consolido artefatos do pipeline inteiro."""
    consolidado: list[dict[str, Any]] = []
    por_task: list[dict[str, Any]] = []

    for task in _garantir_lista(tasks):
        if not isinstance(task, dict):
            continue
        resumo_task = listar_artefatos_task(task)
        por_task.append(resumo_task)
        consolidado.extend(resumo_task["itens"])

    vistos: set[tuple[str, str, str, str, str, str]] = set()
    deduplicados: list[dict[str, Any]] = []
    for item in consolidado:
        chave = _chave_deduplicacao(item)
        if chave in vistos:
            continue
        vistos.add(chave)
        deduplicados.append(item)

    return {
        "total": len(deduplicados),
        "quantidade_visualizavel": sum(1 for item in deduplicados if item.get("visualizavel")),
        "quantidade_downloadable": sum(1 for item in deduplicados if item.get("downloadable")),
        "itens": deduplicados,
        "por_task": por_task,
    }


__all__ = [
    "normalizar_artefato",
    "listar_artefatos_task",
    "listar_artefatos_pipeline",
]
