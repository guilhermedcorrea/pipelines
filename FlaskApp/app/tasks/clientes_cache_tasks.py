from __future__ import annotations

from app.celery_app import celery_app


FILTROS_PADRAO_CLIENTES = {
    "q": "",
    "municipio": "",
    "porte": "",
    "classe": [],
    "setor": [],
    "subclasse": [],
    "empresa_proprietaria": [],
    "classe_valor": [],
    "tipo_escala_operacional": [],
    "classe_estrutural": [],
    "classe_geo": [],
    "classe_frequencia": [],
    "classe_recencia": [],
    "nome_perfil_publico": [],
    "tipo_uso_territorio": [],
    "classificacao_macro": [],
    "cliente": "todos",
}


@celery_app.task(name="clientes_cache.aquecer_cache_clientes_lista")
def aquecer_cache_clientes_lista(filtros: dict | None = None, page: int = 1, per_page: int = 20):
    """
    Eu aqueço o cache da lista de clientes fora da requisição do usuário.

    A tela continua funcionando mesmo sem essa task, mas quando o worker está ligado
    o Redis fica quente para página atual, próxima página e filtros principais.
    """
    from app.midia.controle_paineis_views import (
        _obter_total_clientes_cacheado,
        _obter_itens_clientes_cacheados,
        _obter_valores_distintos_filtro_clientes,
        DEFINICOES_FILTROS_CLIENTES,
        LIMITE_OPCOES_FILTRO_CLIENTES,
    )

    filtros_base = dict(FILTROS_PADRAO_CLIENTES)
    if filtros:
        filtros_base.update(filtros)

    page = max(int(page or 1), 1)
    per_page = max(5, min(int(per_page or 20), 200))

    total = _obter_total_clientes_cacheado(filtros_base)
    _obter_itens_clientes_cacheados(filtros_base, page, per_page)

    if page > 1:
        _obter_itens_clientes_cacheados(filtros_base, page - 1, per_page)
    _obter_itens_clientes_cacheados(filtros_base, page + 1, per_page)

    filtros_prioritarios = [
        "municipio",
        "porte",
        "classe",
        "setor",
        "classificacao_macro",
        "subclasse",
        "empresa_proprietaria",
    ]

    for nome_filtro in filtros_prioritarios:
        if nome_filtro in DEFINICOES_FILTROS_CLIENTES:
            _obter_valores_distintos_filtro_clientes(
                nome_filtro=nome_filtro,
                filtros=filtros_base,
                termo="",
                limite=LIMITE_OPCOES_FILTRO_CLIENTES,
            )

    return {
        "ok": True,
        "total": int(total or 0),
        "page": page,
        "per_page": per_page,
        "filtros_aquecidos": filtros_prioritarios,
    }


@celery_app.task(name="clientes_cache.aquecer_cache_clientes_inicial")
def aquecer_cache_clientes_inicial():
    """Eu aqueço a primeira tela de clientes, útil para rodar no deploy ou por agendamento."""
    return aquecer_cache_clientes_lista(dict(FILTROS_PADRAO_CLIENTES), 1, 20)
