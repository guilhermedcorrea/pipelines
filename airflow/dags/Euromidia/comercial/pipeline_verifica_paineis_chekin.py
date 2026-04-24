# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pendulum

from airflow import DAG
from airflow.operators.python import PythonOperator


NOME_DAG = "pipeline_verifica_paineis_chekin"

CAMINHO_BASE_FLASK_APP = Path("/home/guilherme_correa/PythonJobs/pipelines/FlaskApp")

PASTA_CHEKIN_PONTOS = CAMINHO_BASE_FLASK_APP / "chekin" / "pontos"

EXTENSOES_IMAGEM_PERMITIDAS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
}

ORIGENS_PAINEIS: tuple[dict[str, Any], ...] = (
    {
        "nome_origem": "digitais",
        "pasta_origem": CAMINHO_BASE_FLASK_APP / "paineis" / "digitais",
        "criar_pasta_face_padrao": True,
        "sufixo_face_padrao": "AD",
    },
    {
        "nome_origem": "empena",
        "pasta_origem": CAMINHO_BASE_FLASK_APP / "paineis" / "empena",
        "criar_pasta_face_padrao": False,
        "sufixo_face_padrao": None,
    },
    {
        "nome_origem": "front",
        "pasta_origem": CAMINHO_BASE_FLASK_APP / "paineis" / "front",
        "criar_pasta_face_padrao": False,
        "sufixo_face_padrao": None,
    },
    {
        "nome_origem": "mobiliário",
        "pasta_origem": CAMINHO_BASE_FLASK_APP / "paineis" / "mobiliario",
        "criar_pasta_face_padrao": False,
        "sufixo_face_padrao": None,
    },
)


def _eh_arquivo_imagem(caminho_arquivo: Path) -> bool:
    if not caminho_arquivo.is_file():
        return False

    if caminho_arquivo.name.startswith("."):
        return False

    return caminho_arquivo.suffix.lower() in EXTENSOES_IMAGEM_PERMITIDAS


def _obter_cod_ponto_pelo_nome_arquivo(caminho_imagem: Path) -> str | None:
    cod_ponto = caminho_imagem.stem.strip()

    if not cod_ponto:
        return None

    return cod_ponto


def _existe_imagem_equivalente_na_pasta_fundo(
    *,
    pasta_fundo: Path,
    cod_ponto: str,
) -> bool:
    if not pasta_fundo.exists():
        return False

    cod_ponto_normalizado = cod_ponto.casefold()

    for arquivo_existente in pasta_fundo.iterdir():
        if not arquivo_existente.is_file():
            continue

        if arquivo_existente.suffix.lower() not in EXTENSOES_IMAGEM_PERMITIDAS:
            continue

        if arquivo_existente.stem.strip().casefold() == cod_ponto_normalizado:
            return True

    return False


def _garantir_pasta(caminho_pasta: Path) -> bool:
    """
    Retorna True quando a pasta precisou ser criada.
    Retorna False quando ela já existia.
    """
    if caminho_pasta.exists():
        if not caminho_pasta.is_dir():
            raise RuntimeError(f"O caminho existe, mas não é uma pasta: {caminho_pasta}")
        return False

    caminho_pasta.mkdir(parents=True, exist_ok=True)
    return True


def _copiar_imagem_para_fundo(
    *,
    caminho_imagem_origem: Path,
    pasta_fundo_destino: Path,
    cod_ponto: str,
) -> Path:
    nome_arquivo_destino = f"{cod_ponto}{caminho_imagem_origem.suffix}"
    caminho_imagem_destino = pasta_fundo_destino / nome_arquivo_destino

    shutil.copy2(caminho_imagem_origem, caminho_imagem_destino)

    return caminho_imagem_destino


def _processar_imagem_painel(
    *,
    caminho_imagem_origem: Path,
    nome_origem: str,
    criar_pasta_face_padrao: bool,
    sufixo_face_padrao: str | None,
    resumo: dict[str, int],
) -> None:
    cod_ponto = _obter_cod_ponto_pelo_nome_arquivo(caminho_imagem_origem)

    if not cod_ponto:
        resumo["arquivos_ignorados"] += 1
        logging.warning(
            "Arquivo ignorado porque não foi possível obter CodPonto pelo nome: %s",
            caminho_imagem_origem,
        )
        return

    pasta_cod_ponto = PASTA_CHEKIN_PONTOS / cod_ponto
    pasta_fundo = pasta_cod_ponto / "fundo"

    pasta_cod_ponto_criada = _garantir_pasta(pasta_cod_ponto)
    if pasta_cod_ponto_criada:
        resumo["pastas_cod_ponto_criadas"] += 1
        logging.info("Pasta do CodPonto criada: %s", pasta_cod_ponto)

    pasta_fundo_criada = _garantir_pasta(pasta_fundo)
    if pasta_fundo_criada:
        resumo["pastas_fundo_criadas"] += 1
        logging.info("Pasta fundo criada: %s", pasta_fundo)

    if criar_pasta_face_padrao:
        if not sufixo_face_padrao:
            raise RuntimeError(
                f"Origem {nome_origem} está marcada para criar face padrão, "
                "mas sufixo_face_padrao veio vazio."
            )

        cod_face_padrao = f"{cod_ponto}{sufixo_face_padrao}"
        pasta_face_padrao = pasta_cod_ponto / cod_face_padrao

        pasta_face_criada = _garantir_pasta(pasta_face_padrao)
        if pasta_face_criada:
            resumo["pastas_face_criadas"] += 1
            logging.info("Pasta de face padrão criada: %s", pasta_face_padrao)

    imagem_ja_existe = _existe_imagem_equivalente_na_pasta_fundo(
        pasta_fundo=pasta_fundo,
        cod_ponto=cod_ponto,
    )

    if imagem_ja_existe:
        resumo["imagens_ja_existiam_no_fundo"] += 1
        logging.info(
            "Imagem de fundo já existe para CodPonto %s. Origem ignorada: %s",
            cod_ponto,
            caminho_imagem_origem,
        )
        return

    caminho_destino = _copiar_imagem_para_fundo(
        caminho_imagem_origem=caminho_imagem_origem,
        pasta_fundo_destino=pasta_fundo,
        cod_ponto=cod_ponto,
    )

    resumo["imagens_copiadas_para_fundo"] += 1

    logging.info(
        "Imagem copiada para fundo | origem=%s | cod_ponto=%s | destino=%s",
        nome_origem,
        cod_ponto,
        caminho_destino,
    )


def executar_verificacao_paineis_chekin() -> dict[str, int]:
    logging.info("Iniciando DAG %s", NOME_DAG)

    resumo = {
        "pastas_origem_encontradas": 0,
        "pastas_origem_nao_encontradas": 0,
        "imagens_encontradas": 0,
        "imagens_processadas": 0,
        "imagens_copiadas_para_fundo": 0,
        "imagens_ja_existiam_no_fundo": 0,
        "pastas_cod_ponto_criadas": 0,
        "pastas_fundo_criadas": 0,
        "pastas_face_criadas": 0,
        "arquivos_ignorados": 0,
        "erros": 0,
    }

    _garantir_pasta(PASTA_CHEKIN_PONTOS)

    for origem in ORIGENS_PAINEIS:
        nome_origem = origem["nome_origem"]
        pasta_origem: Path = origem["pasta_origem"]
        criar_pasta_face_padrao = bool(origem["criar_pasta_face_padrao"])
        sufixo_face_padrao = origem["sufixo_face_padrao"]

        logging.info(
            "Verificando imagens da origem '%s' na pasta: %s",
            nome_origem,
            pasta_origem,
        )

        if not pasta_origem.exists():
            resumo["pastas_origem_nao_encontradas"] += 1
            logging.warning("Pasta de origem não encontrada: %s", pasta_origem)
            continue

        if not pasta_origem.is_dir():
            resumo["pastas_origem_nao_encontradas"] += 1
            logging.warning("Caminho de origem existe, mas não é pasta: %s", pasta_origem)
            continue

        resumo["pastas_origem_encontradas"] += 1

        imagens_origem = sorted(
            caminho
            for caminho in pasta_origem.iterdir()
            if _eh_arquivo_imagem(caminho)
        )

        resumo["imagens_encontradas"] += len(imagens_origem)

        logging.info(
            "Origem '%s' possui %s imagem(ns) válida(s).",
            nome_origem,
            len(imagens_origem),
        )

        for caminho_imagem in imagens_origem:
            try:
                _processar_imagem_painel(
                    caminho_imagem_origem=caminho_imagem,
                    nome_origem=nome_origem,
                    criar_pasta_face_padrao=criar_pasta_face_padrao,
                    sufixo_face_padrao=sufixo_face_padrao,
                    resumo=resumo,
                )
                resumo["imagens_processadas"] += 1

            except Exception:
                resumo["erros"] += 1
                logging.exception(
                    "Erro ao processar imagem da origem '%s': %s",
                    nome_origem,
                    caminho_imagem,
                )

    logging.info("Resumo final do DAG %s: %s", NOME_DAG, resumo)

    if resumo["pastas_origem_encontradas"] == 0:
        raise RuntimeError(
            "Nenhuma pasta de origem foi encontrada. "
            "Verifique se o Airflow tem acesso ao caminho do FlaskApp."
        )

    if resumo["erros"] > 0:
        raise RuntimeError(
            f"O processamento terminou com {resumo['erros']} erro(s). "
            "Verifique os logs do Airflow."
        )

    return resumo


with DAG(
    dag_id=NOME_DAG,
    description="Pipeline verifica painéis chekin: sincroniza imagens de painéis para pasta fundo do check-in.",
    schedule="*/10 * * * *",
    start_date=pendulum.datetime(2026, 4, 24, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "guilherme",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["euromidia", "paineis", "chekin", "imagens"],
) as dag:

    verificar_paineis_chekin = PythonOperator(
        task_id="verificar_e_sincronizar_imagens_fundo",
        python_callable=executar_verificacao_paineis_chekin,
    )

    verificar_paineis_chekin