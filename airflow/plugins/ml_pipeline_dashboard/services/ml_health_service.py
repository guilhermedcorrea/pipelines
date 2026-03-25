from __future__ import annotations

from typing import Any


STATUS_SUCESSO = {"success", "successful", "ok", "done"}
STATUS_ALERTA = {"running", "queued", "scheduled", "up_for_retry", "upstream_failed", "skipped"}
STATUS_FALHA = {"failed", "error", "erro"}


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


def _status_categoria(status: Any) -> str:
    """Eu converto um status textual em categoria de saúde."""
    valor = _texto(status).lower()
    if valor in STATUS_SUCESSO:
        return "sucesso"
    if valor in STATUS_FALHA:
        return "falha"
    if valor in STATUS_ALERTA:
        return "alerta"
    return "neutro"


def _status_dominante(contadores: dict[str, int]) -> str:
    """Eu transformo contadores em um status final simples."""
    if contadores.get("falha", 0) > 0:
        return "critico"
    if contadores.get("alerta", 0) > 0:
        return "alerta"
    if contadores.get("sucesso", 0) > 0:
        return "saudavel"
    return "indeterminado"


def avaliar_health_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu avalio a saúde operacional das tasks da execução."""
    contadores = {"sucesso": 0, "alerta": 0, "falha": 0, "neutro": 0}
    itens: list[dict[str, Any]] = []

    for task in _garantir_lista(tasks):
        if not isinstance(task, dict):
            continue

        categoria = _status_categoria(task.get("status"))
        contadores[categoria] += 1
        itens.append(
            {
                "task_id": _texto(task.get("task_id") or task.get("id")),
                "nome": _texto(task.get("nome_amigavel") or task.get("nome") or task.get("task_id")),
                "status": _texto(task.get("status")) or "unknown",
                "categoria": categoria,
                "tentativas": (task.get("metricas") or {}).get("tentativas"),
                "tempo_execucao": (task.get("metricas") or {}).get("tempo_execucao"),
            }
        )

    total = sum(contadores.values())
    percentual_sucesso = round((contadores["sucesso"] / total) * 100, 2) if total else 0.0

    return {
        "status": _status_dominante(contadores),
        "total_tasks": total,
        "contadores": contadores,
        "percentual_sucesso": percentual_sucesso,
        "itens": itens,
    }


def avaliar_health_dados(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu avalio a saúde dos dados a partir de validações e amostras registradas."""
    contadores = {"ok": 0, "alerta": 0, "erro": 0}
    itens: list[dict[str, Any]] = []
    tasks_sem_amostra = 0

    for task in _garantir_lista(tasks):
        if not isinstance(task, dict):
            continue

        linhas_amostra = _garantir_lista(((task.get("tabela") or {}).get("linhas")))
        if not linhas_amostra:
            tasks_sem_amostra += 1

        for validacao in _garantir_lista(task.get("validacoes")):
            if isinstance(validacao, dict):
                status = _texto(validacao.get("status")).lower() or "ok"
                descricao = _texto(validacao.get("descricao") or validacao.get("detalhe") or validacao.get("nome"))
            else:
                status = "ok"
                descricao = _texto(validacao)

            if status not in contadores:
                if status in {"warning", "warn"}:
                    status = "alerta"
                elif status in {"error", "failed"}:
                    status = "erro"
                else:
                    status = "ok"

            contadores[status] += 1
            itens.append(
                {
                    "task_id": _texto(task.get("task_id")),
                    "task_nome": _texto(task.get("nome_amigavel") or task.get("nome") or task.get("task_id")),
                    "status": status,
                    "descricao": descricao,
                }
            )

    if contadores["erro"] > 0:
        status = "critico"
    elif contadores["alerta"] > 0 or tasks_sem_amostra > 0:
        status = "alerta"
    else:
        status = "saudavel"

    return {
        "status": status,
        "contadores": contadores,
        "tasks_sem_amostra": tasks_sem_amostra,
        "itens": itens,
    }


def avaliar_health_modelo(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu avalio a saúde do modelo usando métricas registradas nas tasks."""
    sinais_positivos = 0
    sinais_alerta = 0
    detalhes: list[dict[str, Any]] = []

    for task in _garantir_lista(tasks):
        if not isinstance(task, dict):
            continue

        metricas_extras = task.get("metricas_extras") or {}
        chaves_relevantes = (
            "auc_walk_forward",
            "auc_teste_final_oot",
            "ks",
            "auc_roc",
            "auc_pr",
            "brier_score",
            "lift_top_10",
        )

        metricas_task = {chave: metricas_extras.get(chave) for chave in chaves_relevantes if metricas_extras.get(chave) is not None}
        if not metricas_task:
            continue

        auc = metricas_task.get("auc_teste_final_oot") or metricas_task.get("auc_walk_forward") or metricas_task.get("auc_roc")
        ks = metricas_task.get("ks")

        categoria = "neutro"
        if isinstance(auc, (int, float)):
            if auc >= 0.70:
                categoria = "sucesso"
                sinais_positivos += 1
            elif auc >= 0.60:
                categoria = "alerta"
                sinais_alerta += 1
            else:
                categoria = "falha"
                sinais_alerta += 1
        elif isinstance(ks, (int, float)):
            if ks >= 0.25:
                categoria = "sucesso"
                sinais_positivos += 1
            elif ks >= 0.15:
                categoria = "alerta"
                sinais_alerta += 1
            else:
                categoria = "falha"
                sinais_alerta += 1

        detalhes.append(
            {
                "task_id": _texto(task.get("task_id")),
                "task_nome": _texto(task.get("nome_amigavel") or task.get("nome") or task.get("task_id")),
                "categoria": categoria,
                "metricas": metricas_task,
            }
        )

    if any(item["categoria"] == "falha" for item in detalhes):
        status = "critico"
    elif sinais_alerta > 0:
        status = "alerta"
    elif sinais_positivos > 0:
        status = "saudavel"
    else:
        status = "indeterminado"

    return {
        "status": status,
        "quantidade_sinais_positivos": sinais_positivos,
        "quantidade_sinais_alerta": sinais_alerta,
        "itens": detalhes,
    }


def montar_health_pipeline(dashboard: dict[str, Any]) -> dict[str, Any]:
    """Eu monto o health consolidado do pipeline usando tarefas e health base do runtime."""
    tasks = _garantir_lista(dashboard.get("tasks"))
    health_base = dashboard.get("health") or {}

    health_tasks = avaliar_health_tasks(tasks)
    health_dados = avaliar_health_dados(tasks)
    health_modelo = avaliar_health_modelo(tasks)

    estados = [
        health_tasks.get("status"),
        health_dados.get("status"),
        health_modelo.get("status"),
        _texto(health_base.get("status") or "").lower(),
    ]

    if any(estado in {"critico", "failed", "falha"} for estado in estados):
        status_geral = "critico"
    elif any(estado == "alerta" for estado in estados):
        status_geral = "alerta"
    elif any(estado in {"saudavel", "healthy", "ok"} for estado in estados):
        status_geral = "saudavel"
    else:
        status_geral = "indeterminado"

    return {
        "status_geral": status_geral,
        "health_runtime": health_base,
        "health_tasks": health_tasks,
        "health_dados": health_dados,
        "health_modelo": health_modelo,
    }


__all__ = [
    "avaliar_health_tasks",
    "avaliar_health_dados",
    "avaliar_health_modelo",
    "montar_health_pipeline",
]
