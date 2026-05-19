from __future__ import annotations

import os
import traceback
from datetime import datetime
from pathlib import Path

from ..celery_app import celery_app


@celery_app.task(
    bind=True,
    name="paineis_ocupacao.gerar_relatorio_ocupacao_excel",
)
def gerar_relatorio_ocupacao_excel_async(self, ano: int) -> dict:
    """
    Eu gero o Excel de ocupação em segundo plano.

    Por que existe esta task:
    - a exportação anual pode passar do timeout do Nginx/Gunicorn;
    - a request HTTP não deve ficar presa esperando o Excel inteiro;
    - o worker Celery gera o arquivo e salva em uma pasta compartilhada;
    - depois o Flask apenas entrega o arquivo pronto no endpoint de download.
    """
    try:
        self.update_state(
            state="PROGRESS",
            meta={
                "status": "Iniciando geração do Excel de ocupação...",
                "progresso": 5,
            },
        )

        from app.euromidia.controle_paineis_views import (
            _gerar_excel_ocupacao_ano_bytes,
            _normalizar_ano_exportacao_ocupacao,
            _obter_pasta_relatorios_ocupacao,
        )

        ano_int = _normalizar_ano_exportacao_ocupacao(ano)

        self.update_state(
            state="PROGRESS",
            meta={
                "status": f"Consultando dados e montando grade anual de {ano_int}...",
                "progresso": 35,
            },
        )

        bio, nome_download = _gerar_excel_ocupacao_ano_bytes(ano_int)

        self.update_state(
            state="PROGRESS",
            meta={
                "status": "Salvando arquivo Excel gerado...",
                "progresso": 90,
            },
        )

        pasta = _obter_pasta_relatorios_ocupacao()
        pasta.mkdir(parents=True, exist_ok=True)

        task_id = str(getattr(self.request, "id", "") or "").strip()
        if not task_id:
            task_id = datetime.now().strftime("%Y%m%d%H%M%S%f")

        nome_seguro = f"{task_id}_grade_paineis_{ano_int}.xlsx"
        caminho = pasta / nome_seguro

        bio.seek(0)
        caminho.write_bytes(bio.getvalue())

        return {
            "ok": True,
            "ano": ano_int,
            "arquivo": str(caminho),
            "filename": nome_download or f"grade_paineis_{ano_int}.xlsx",
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
            "tamanho_bytes": int(caminho.stat().st_size),
        }

    except Exception as exc:
        return {
            "ok": False,
            "erro": str(exc),
            "traceback": traceback.format_exc(limit=8),
            "ano": ano,
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
        }
