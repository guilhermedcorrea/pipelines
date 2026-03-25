from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _ler_bool_env(nome_variavel: str, padrao: bool) -> bool:
    """Eu converto uma variável de ambiente para booleano de forma previsível."""
    valor = str(os.getenv(nome_variavel, "")).strip().lower()

    if not valor:
        return bool(padrao)

    return valor in {"1", "true", "t", "sim", "s", "yes", "y", "on"}


def _ler_int_env(nome_variavel: str, padrao: int, minimo: int | None = None, maximo: int | None = None) -> int:
    """Eu leio inteiro de ambiente com proteção contra valores inválidos."""
    valor_bruto = str(os.getenv(nome_variavel, "")).strip()
    try:
        valor = int(valor_bruto) if valor_bruto else int(padrao)
    except Exception:
        valor = int(padrao)

    if minimo is not None:
        valor = max(minimo, valor)

    if maximo is not None:
        valor = min(maximo, valor)

    return valor


def _ler_lista_env(nome_variavel: str, padrao: tuple[str, ...]) -> tuple[str, ...]:
    """Eu leio lista separada por vírgula do ambiente, removendo vazios e espaços."""
    valor_bruto = str(os.getenv(nome_variavel, "")).strip()

    if not valor_bruto:
        return tuple(dict.fromkeys(str(item).strip() for item in padrao if str(item).strip()))

    itens = [parte.strip() for parte in valor_bruto.split(",")]
    itens_limpos = [item for item in itens if item]

    if not itens_limpos:
        return tuple(dict.fromkeys(str(item).strip() for item in padrao if str(item).strip()))

    return tuple(dict.fromkeys(itens_limpos))


def _resolver_roots_permitidos() -> tuple[Path, ...]:
    """Eu monto a lista de raízes permitidas para download e leitura segura."""
    roots_padrao = [
        "/opt/airflow/Artefatos",
        "/opt/airflow/Dados",
        "/opt/airflow/include",
        "/opt/airflow/plugins",
        "/opt/airflow",
    ]

    roots_extras = _ler_lista_env("ML_PIPELINE_DASHBOARD_ROOTS_EXTRAS", tuple())
    todas = [*roots_padrao, *roots_extras]

    roots_resolvidos: list[Path] = []
    for item in todas:
        try:
            caminho = Path(str(item)).expanduser().resolve()
            if caminho not in roots_resolvidos:
                roots_resolvidos.append(caminho)
        except Exception:
            continue

    return tuple(roots_resolvidos)


@dataclass(frozen=True, slots=True)
class ConfiguracaoPluginMl:
    """Eu centralizo a configuração do plugin para evitar constantes espalhadas."""

    nome_plugin: str
    titulo_plugin: str
    subtitulo_plugin: str
    nome_menu: str
    url_prefixo: str
    rota_lista_html: str
    rota_lista_api: str
    rota_detalhe_html: str
    rota_detalhe_api_base: str
    rota_health: str
    rota_download: str
    rota_tabela_html: str
    rota_tabela_preview_api: str
    rota_tabela_dados_api: str
    limite_listagem_padrao: int
    limite_preview_padrao: int
    tamanho_pagina_padrao: int
    tamanho_pagina_maximo: int
    buscar_tags_por_dashboard_na_lista: bool
    habilitar_assets_opcionais: bool
    titulo_html_lista: str
    titulo_html_detalhe: str
    nome_template_lista: str
    nome_template_detalhe: str
    nome_template_tabela: str
    tags_pipeline_ml: tuple[str, ...]
    palavras_chave_heuristica_ml: tuple[str, ...]
    roots_arquivos_permitidos: tuple[Path, ...]
    paleta_cores: dict[str, str]
    contexto_frontend: dict[str, Any]


PASTA_PLUGIN = Path(__file__).resolve().parent
PASTA_TEMPLATES = PASTA_PLUGIN / "templates"
PASTA_STATIC = PASTA_PLUGIN / "static"
PASTA_ASSETS = PASTA_PLUGIN / "assets"

PALETA_ASTRO = {
    "fundo_primario": "#0B1020",
    "fundo_secundario": "#11182D",
    "card": "#151F38",
    "card_hover": "#1A2748",
    "borda": "#26345C",
    "texto_primario": "#E8EEFF",
    "texto_secundario": "#A8B3D1",
    "roxo_primario": "#6E56CF",
    "roxo_hover": "#7C66D9",
    "azul_destaque": "#3B82F6",
    "ciano_destaque": "#22D3EE",
    "verde_sucesso": "#22C55E",
    "amarelo_alerta": "#F59E0B",
    "vermelho_falha": "#EF4444",
    "cinza_chip": "#2A3557",
    "sombra_roxa": "0 20px 60px rgba(110, 86, 207, 0.18)",
}

TAGS_PIPELINE_ML_PADRAO = (
    "machinelearning",
    "machine_learning",
    "ml",
    "modelo",
    "modelagem",
    "treino",
    "inferencia",
    "inferencia_ml",
    "predicao",
    "predição",
    "classificacao",
    "classificação",
    "regressao",
    "regressão",
    "forecast",
    "score",
    "scoring",
    "segmentacao",
    "segmentação",
    "clusterizacao",
    "clusterização",
    "nlp",
)

PALAVRAS_CHAVE_HEURISTICA_ML_PADRAO = (
    "ml",
    "model",
    "modelo",
    "score",
    "scoring",
    "forecast",
    "classif",
    "pred",
    "segment",
    "cluster",
    "cliente",
    "noticia",
    "notícias",
    "treino",
    "inferencia",
    "inference",
)

CONFIGURACAO = ConfiguracaoPluginMl(
    nome_plugin="ml_pipeline_dashboard",
    titulo_plugin="ML Pipeline Dashboard",
    subtitulo_plugin="Observabilidade, documentação e análise de pipelines de Machine Learning",
    nome_menu="ML Pipelines",
    url_prefixo="/ml_pipeline_dashboard",
    rota_lista_html="/pipelines",
    rota_lista_api="/api/pipelines",
    rota_detalhe_html="/pipelines/{dag_id}",
    rota_detalhe_api_base="/api/pipelines",
    rota_health="/health",
    rota_download="/arquivo/download",
    rota_tabela_html="/tabela",
    rota_tabela_preview_api="/api/table-preview",
    rota_tabela_dados_api="/api/table-data",
    limite_listagem_padrao=_ler_int_env("ML_PIPELINE_DASHBOARD_LIMITE_LISTAGEM", 50, minimo=1, maximo=500),
    limite_preview_padrao=_ler_int_env("ML_PIPELINE_DASHBOARD_LIMITE_PREVIEW", 20, minimo=1, maximo=100),
    tamanho_pagina_padrao=_ler_int_env("ML_PIPELINE_DASHBOARD_TAMANHO_PAGINA", 50, minimo=1, maximo=200),
    tamanho_pagina_maximo=_ler_int_env("ML_PIPELINE_DASHBOARD_TAMANHO_PAGINA_MAXIMO", 200, minimo=10, maximo=1000),
    buscar_tags_por_dashboard_na_lista=_ler_bool_env("ML_PIPELINE_DASHBOARD_BUSCAR_TAGS_POR_DASHBOARD", False),
    habilitar_assets_opcionais=_ler_bool_env("ML_PIPELINE_DASHBOARD_HABILITAR_ASSETS", True),
    titulo_html_lista="Pipelines de Machine Learning",
    titulo_html_detalhe="Detalhe do Pipeline de Machine Learning",
    nome_template_lista="lista_pipelines_ml.html",
    nome_template_detalhe="detalhe_pipeline_ml.html",
    nome_template_tabela="tabela_dados.html",
    tags_pipeline_ml=_ler_lista_env("ML_PIPELINE_DASHBOARD_TAGS_ML", TAGS_PIPELINE_ML_PADRAO),
    palavras_chave_heuristica_ml=_ler_lista_env(
        "ML_PIPELINE_DASHBOARD_PALAVRAS_CHAVE_HEURISTICA",
        PALAVRAS_CHAVE_HEURISTICA_ML_PADRAO,
    ),
    roots_arquivos_permitidos=_resolver_roots_permitidos(),
    paleta_cores=PALETA_ASTRO,
    contexto_frontend={
        "nome_plugin": "ml_pipeline_dashboard",
        "titulo_plugin": "ML Pipeline Dashboard",
        "subtitulo_plugin": "Observabilidade, documentação e análise de pipelines de Machine Learning",
        "url_prefixo": "/ml_pipeline_dashboard",
        "rota_lista_html": "/pipelines",
        "rota_lista_api": "/api/pipelines",
        "rota_detalhe_api_base": "/api/pipelines",
        "rota_health": "/health",
        "rota_download": "/arquivo/download",
        "rota_tabela_html": "/tabela",
        "rota_tabela_preview_api": "/api/table-preview",
        "rota_tabela_dados_api": "/api/table-data",
        "paleta": PALETA_ASTRO,
    },
)


def obter_configuracao() -> ConfiguracaoPluginMl:
    """Eu devolvo a configuração congelada do plugin."""
    return CONFIGURACAO


def obter_contexto_frontend() -> dict[str, Any]:
    """Eu retorno apenas o pedaço de configuração útil para HTML e JavaScript."""
    return dict(CONFIGURACAO.contexto_frontend)


__all__ = [
    "CONFIGURACAO",
    "ConfiguracaoPluginMl",
    "PASTA_PLUGIN",
    "PASTA_TEMPLATES",
    "PASTA_STATIC",
    "PASTA_ASSETS",
    "PALETA_ASTRO",
    "obter_configuracao",
    "obter_contexto_frontend",
]