from __future__ import annotations

from typing import Any


CHAVES_PRIORITARIAS = (
    "auc_teste_final_oot",
    "auc_walk_forward",
    "auc_roc",
    "auc_pr",
    "log_loss",
    "brier_score",
    "ks",
    "precision_top_10",
    "recall_top_10",
    "lift_top_10",
)

NOMES_AMIGAVEIS = {
    "auc_teste_final_oot": "AUC Teste Final OOT",
    "auc_walk_forward": "AUC Walk-Forward",
    "auc_roc": "AUC ROC",
    "auc_pr": "AUC PR",
    "log_loss": "Log Loss",
    "brier_score": "Brier Score",
    "ks": "KS",
    "precision_top_10": "Precision Top 10%",
    "recall_top_10": "Recall Top 10%",
    "lift_top_10": "Lift Top 10%",
    "iteracoes_finais": "Iterações Finais",
    "quantidade_folds_walk_forward": "Qtd. Folds Walk-Forward",
    "mes_snapshot_mais_recente": "Mês Snapshot Mais Recente",
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


def _eh_numero(valor: Any) -> bool:
    """Eu verifico se o valor é numérico real."""
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _normalizar_nome(chave: str) -> str:
    """Eu devolvo o nome amigável da métrica."""
    return NOMES_AMIGAVEIS.get(chave, _texto(chave).replace("_", " ").title())


def _achatar_metricas(valor: Any, prefixo: str = "") -> dict[str, Any]:
    """Eu achato dicionários aninhados para facilitar busca e exibição."""
    retorno: dict[str, Any] = {}

    if isinstance(valor, dict):
        for chave, item in valor.items():
            chave_texto = _texto(chave)
            novo_prefixo = f"{prefixo}_{chave_texto}" if prefixo else chave_texto
            retorno.update(_achatar_metricas(item, prefixo=novo_prefixo))
        return retorno

    if isinstance(valor, list):
        return retorno

    if prefixo:
        retorno[prefixo] = valor
    return retorno


def extrair_metricas_task(task: dict[str, Any]) -> dict[str, Any]:
    """Eu consolido métricas básicas e métricas extras de uma task."""
    metricas_base = dict((task.get("metricas") or {}))
    metricas_extras = dict((task.get("metricas_extras") or {}))

    metricas_flat = {}
    metricas_flat.update(_achatar_metricas(metricas_base))
    metricas_flat.update(_achatar_metricas(metricas_extras))

    principais: list[dict[str, Any]] = []
    complementares: list[dict[str, Any]] = []

    chaves_ordenadas = list(dict.fromkeys([*CHAVES_PRIORITARIAS, *metricas_flat.keys()]))
    for chave in chaves_ordenadas:
        if chave not in metricas_flat:
            continue
        valor = metricas_flat[chave]
        item = {
            "chave": chave,
            "label": _normalizar_nome(chave),
            "valor": valor,
            "numerico": _eh_numero(valor),
        }
        if chave in CHAVES_PRIORITARIAS:
            principais.append(item)
        else:
            complementares.append(item)

    return {
        "task_id": _texto(task.get("task_id") or task.get("id")),
        "principais": principais,
        "complementares": complementares,
        "flat": metricas_flat,
    }


def _extrair_dados_folds(metricas_extras: dict[str, Any]) -> list[dict[str, Any]]:
    """Eu normalizo informações de folds quando vierem no payload da auditoria."""
    candidatos = (
        metricas_extras.get("walk_forward_folds"),
        metricas_extras.get("folds_walk_forward"),
        metricas_extras.get("folds"),
        metricas_extras.get("resumo_folds"),
    )

    for candidato in candidatos:
        if isinstance(candidato, list):
            retorno: list[dict[str, Any]] = []
            for indice, item in enumerate(candidato, start=1):
                if not isinstance(item, dict):
                    continue
                linha = dict(item)
                linha.setdefault("ordem_fold", indice)
                retorno.append(linha)
            if retorno:
                return retorno

    return []


def montar_graficos_metricas_pipeline(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu preparo payloads simples para Plotly no front."""
    barras_metricas: list[dict[str, Any]] = []
    series_folds: list[dict[str, Any]] = []

    for task in _garantir_lista(tasks):
        if not isinstance(task, dict):
            continue

        nome_task = _texto(task.get("nome_amigavel") or task.get("nome") or task.get("task_id"))
        metricas = extrair_metricas_task(task)

        for item in metricas["principais"]:
            if item.get("numerico"):
                barras_metricas.append(
                    {
                        "task": nome_task,
                        "metric_key": item["chave"],
                        "metric_label": item["label"],
                        "valor": item["valor"],
                    }
                )

        folds = _extrair_dados_folds(task.get("metricas_extras") or {})
        for fold in folds:
            ordem = fold.get("ordem_fold")
            for chave in ("AUC_ROC", "AUC_PR", "LogLoss", "BrierScore", "KS", "LiftTop10"):
                valor = fold.get(chave)
                if _eh_numero(valor):
                    series_folds.append(
                        {
                            "task": nome_task,
                            "fold": ordem,
                            "metric_key": chave,
                            "metric_label": chave,
                            "valor": valor,
                        }
                    )

    return {
        "barras_metricas": barras_metricas,
        "series_folds": series_folds,
    }


def consolidar_metricas_pipeline(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu consolido métricas do pipeline inteiro em formato amigável para o front."""
    por_task: list[dict[str, Any]] = []
    resumo_principal: dict[str, Any] = {}

    for task in _garantir_lista(tasks):
        if not isinstance(task, dict):
            continue

        metricas_task = extrair_metricas_task(task)
        por_task.append(
            {
                "task_id": _texto(task.get("task_id") or task.get("id")),
                "task_nome": _texto(task.get("nome_amigavel") or task.get("nome") or task.get("task_id")),
                **metricas_task,
            }
        )

        for item in metricas_task["principais"]:
            if item["chave"] not in resumo_principal:
                resumo_principal[item["chave"]] = item

    return {
        "resumo_principal": list(resumo_principal.values()),
        "por_task": por_task,
        "graficos": montar_graficos_metricas_pipeline(tasks),
    }


__all__ = [
    "extrair_metricas_task",
    "montar_graficos_metricas_pipeline",
    "consolidar_metricas_pipeline",
]
