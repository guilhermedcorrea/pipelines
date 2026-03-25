from __future__ import annotations

from typing import Any


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


def _id_no_task(task: dict[str, Any]) -> str:
    """Eu monto o identificador único do nó de task."""
    return f"task::{_texto(task.get('task_id') or task.get('id'))}"


def _nome_objeto(objeto: dict[str, Any]) -> str:
    """Eu extraio um nome legível para objeto técnico."""
    nome = _texto(objeto.get("nome"))
    if nome:
        return nome
    schema = _texto(objeto.get("schema"))
    tabela = _texto(objeto.get("tabela"))
    banco = _texto(objeto.get("banco"))
    if schema and tabela:
        if banco:
            return f"{banco}.{schema}.{tabela}"
        return f"{schema}.{tabela}"
    caminho = _texto(objeto.get("caminho_arquivo"))
    if caminho:
        return caminho
    procedure = _texto(objeto.get("procedure"))
    if procedure:
        return procedure
    return "Objeto técnico"


def _id_no_objeto(objeto: dict[str, Any]) -> str:
    """Eu monto o identificador único do nó de objeto."""
    return f"obj::{_nome_objeto(objeto)}::{_texto(objeto.get('tipo'))}"


def montar_lineage_pipeline(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu monto nós e arestas do fluxo task -> objeto -> task."""
    nos: list[dict[str, Any]] = []
    arestas: list[dict[str, Any]] = []
    ids_nos: set[str] = set()

    mapa_task_ids: dict[str, dict[str, Any]] = {}

    for indice, task in enumerate(_garantir_lista(tasks), start=1):
        if not isinstance(task, dict):
            continue

        task_id = _texto(task.get("task_id") or task.get("id"))
        if not task_id:
            continue
        mapa_task_ids[task_id] = task

        id_no = _id_no_task(task)
        if id_no not in ids_nos:
            ids_nos.add(id_no)
            nos.append(
                {
                    "id": id_no,
                    "tipo": "task",
                    "task_id": task_id,
                    "nome": _texto(task.get("nome_amigavel") or task.get("nome") or task_id),
                    "status": _texto(task.get("status")) or "unknown",
                    "ordem": indice,
                }
            )

        for upstream in _garantir_lista(task.get("upstream_task_ids")):
            upstream_id = _texto(upstream)
            if not upstream_id:
                continue
            arestas.append(
                {
                    "origem": f"task::{upstream_id}",
                    "destino": id_no,
                    "tipo": "dependencia_task",
                }
            )

        for objeto in _garantir_lista(task.get("objetos")):
            if not isinstance(objeto, dict):
                continue

            id_obj = _id_no_objeto(objeto)
            if id_obj not in ids_nos:
                ids_nos.add(id_obj)
                nos.append(
                    {
                        "id": id_obj,
                        "tipo": "objeto",
                        "subtipo": _texto(objeto.get("tipo")) or "desconhecido",
                        "nome": _nome_objeto(objeto),
                        "conn_id": _texto(objeto.get("conn_id")) or None,
                        "schema": _texto(objeto.get("schema")) or None,
                        "tabela": _texto(objeto.get("tabela")) or None,
                        "caminho_arquivo": _texto(objeto.get("caminho_arquivo")) or None,
                    }
                )

            direcao = _texto(objeto.get("direcao")).lower()
            if direcao == "entrada":
                arestas.append({"origem": id_obj, "destino": id_no, "tipo": "consome"})
            elif direcao == "saida":
                arestas.append({"origem": id_no, "destino": id_obj, "tipo": "produz"})
            else:
                arestas.append({"origem": id_no, "destino": id_obj, "tipo": "referencia"})

    vistos_arestas: set[tuple[str, str, str]] = set()
    arestas_deduplicadas: list[dict[str, Any]] = []
    for aresta in arestas:
        chave = (_texto(aresta.get("origem")), _texto(aresta.get("destino")), _texto(aresta.get("tipo")))
        if chave in vistos_arestas:
            continue
        vistos_arestas.add(chave)
        arestas_deduplicadas.append(aresta)

    return {
        "nos": nos,
        "arestas": arestas_deduplicadas,
        "quantidade_nos": len(nos),
        "quantidade_arestas": len(arestas_deduplicadas),
    }


def montar_lineage_task(task: dict[str, Any]) -> dict[str, Any]:
    """Eu monto um lineage reduzido focado em uma task."""
    task_id = _texto(task.get("task_id") or task.get("id"))
    tasks_minimas = [
        {
            "task_id": task_id,
            "nome_amigavel": _texto(task.get("nome_amigavel") or task.get("nome") or task_id),
            "status": _texto(task.get("status")) or "unknown",
            "upstream_task_ids": _garantir_lista(task.get("upstream_task_ids")),
            "objetos": _garantir_lista(task.get("objetos")),
        }
    ]
    return montar_lineage_pipeline(tasks_minimas)


__all__ = [
    "montar_lineage_pipeline",
    "montar_lineage_task",
]
