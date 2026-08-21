from __future__ import annotations

import traceback
from datetime import datetime

from ..celery_app import celery_app


@celery_app.task(
    bind=True,
    name="paineis_ocupacao.gerar_relatorio_ocupacao_excel",
)
def gerar_relatorio_ocupacao_excel_async(self, ano: int, dt_ini=None, dt_fim=None) -> dict:
    """
    Eu gero o Excel da grade anual de ocupação em segundo plano.

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

        from app.midia.controle_paineis_views import (
            _gerar_excel_ocupacao_ano_bytes,
            _normalizar_periodo_exportacao_ocupacao,
            _obter_pasta_relatorios_ocupacao,
        )

        ano_int, dt_ini_periodo, dt_fim_periodo = _normalizar_periodo_exportacao_ocupacao(ano, dt_ini, dt_fim)

        self.update_state(
            state="PROGRESS",
            meta={
                "status": f"Consultando dados e montando grade de {dt_ini_periodo:%d/%m/%Y} a {dt_fim_periodo:%d/%m/%Y}...",
                "progresso": 35,
            },
        )

        bio, nome_download = _gerar_excel_ocupacao_ano_bytes(ano_int, dt_ini_periodo, dt_fim_periodo)

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

        nome_seguro = f"{task_id}_grade_paineis_{ano_int}_{dt_ini_periodo:%Y%m%d}_{dt_fim_periodo:%Y%m%d}.xlsx"
        caminho = pasta / nome_seguro

        bio.seek(0)
        caminho.write_bytes(bio.getvalue())

        return {
            "ok": True,
            "ano": ano_int,
            "tipo_relatorio": "grade_paineis",
            "dt_ini": dt_ini_periodo.isoformat(),
            "dt_fim": dt_fim_periodo.isoformat(),
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
            "tipo_relatorio": "grade_paineis",
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
        }


@celery_app.task(
    bind=True,
    name="paineis_ocupacao.gerar_relatorio_ocupacao_clientes_excel",
)
def gerar_relatorio_ocupacao_clientes_excel_async(self, ano: int, dt_ini=None, dt_fim=None) -> dict:
    """
    Eu gero o Excel "Ocupação Clientes" em segundo plano.

    Correção crítica:
    - esta função precisa ficar dentro de app/tasks/paineis_relatorios_tasks.py;
    - a rota /paineis/exportar_ocupacao_clientes importa exatamente:
      from ..tasks.paineis_relatorios_tasks import gerar_relatorio_ocupacao_clientes_excel_async;
    - se esta função ficar em outro arquivo, por exemplo
      paineis_relatorios_tasks_ocupacao_clientes.py, o Flask continua quebrando
      com ImportError.
    """
    try:
        self.update_state(
            state="PROGRESS",
            meta={
                "status": "Iniciando geração do relatório Ocupação Clientes...",
                "progresso": 5,
            },
        )

        from app.midia.controle_paineis_views import (
            _gerar_excel_ocupacao_clientes_bytes,
            _normalizar_periodo_exportacao_ocupacao,
            _obter_pasta_relatorios_ocupacao,
        )

        ano_int, dt_ini_periodo, dt_fim_periodo = _normalizar_periodo_exportacao_ocupacao(ano, dt_ini, dt_fim)

        self.update_state(
            state="PROGRESS",
            meta={
                "status": f"Consultando Ocupação Clientes de {dt_ini_periodo:%d/%m/%Y} a {dt_fim_periodo:%d/%m/%Y}...",
                "progresso": 35,
            },
        )

        bio, nome_download = _gerar_excel_ocupacao_clientes_bytes(ano_int, dt_ini_periodo, dt_fim_periodo)

        self.update_state(
            state="PROGRESS",
            meta={
                "status": "Salvando arquivo Ocupação Clientes...",
                "progresso": 90,
            },
        )

        pasta = _obter_pasta_relatorios_ocupacao()
        pasta.mkdir(parents=True, exist_ok=True)

        task_id = str(getattr(self.request, "id", "") or "").strip()
        if not task_id:
            task_id = datetime.now().strftime("%Y%m%d%H%M%S%f")

        nome_seguro = f"{task_id}_ocupacao_clientes_{ano_int}_{dt_ini_periodo:%Y%m%d}_{dt_fim_periodo:%Y%m%d}.xlsx"
        caminho = pasta / nome_seguro

        bio.seek(0)
        caminho.write_bytes(bio.getvalue())

        return {
            "ok": True,
            "ano": ano_int,
            "tipo_relatorio": "ocupacao_clientes",
            "dt_ini": dt_ini_periodo.isoformat(),
            "dt_fim": dt_fim_periodo.isoformat(),
            "arquivo": str(caminho),
            "filename": nome_download or f"Ocupação Clientes {ano_int}.xlsx",
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
            "tamanho_bytes": int(caminho.stat().st_size),
        }

    except Exception as exc:
        return {
            "ok": False,
            "erro": str(exc),
            "traceback": traceback.format_exc(limit=8),
            "ano": ano,
            "tipo_relatorio": "ocupacao_clientes",
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
        }
