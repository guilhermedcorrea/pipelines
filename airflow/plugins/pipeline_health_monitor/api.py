from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

"""
Nome do arquivo:
plugins/pipeline_health_monitor/api.py

O que este arquivo faz:
1) Garante contexto de pacote para funcionar dentro do loader de plugins do Airflow
2) Expõe o APIRouter consumido pelo plugin.py
3) Conecta os endpoints à camada de serviço real
"""



PASTA_ATUAL = Path(__file__).resolve().parent
PASTA_PLUGINS_PAI = PASTA_ATUAL.parent
NOME_PACOTE_PLUGIN = "pipeline_health_monitor"

if str(PASTA_PLUGINS_PAI) not in sys.path:
    sys.path.insert(0, str(PASTA_PLUGINS_PAI))

modulo_existente = sys.modules.get(NOME_PACOTE_PLUGIN)

if modulo_existente is None:
    modulo_pacote = types.ModuleType(NOME_PACOTE_PLUGIN)
    modulo_pacote.__path__ = [str(PASTA_ATUAL)]
    sys.modules[NOME_PACOTE_PLUGIN] = modulo_pacote
else:
    caminho_existente = list(getattr(modulo_existente, "__path__", []))
    if str(PASTA_ATUAL) not in caminho_existente:
        caminho_existente.append(str(PASTA_ATUAL))
        modulo_existente.__path__ = caminho_existente


# =========================================================
# IMPORTS INTERNOS
# =========================================================
from pipeline_health_monitor.servico_health import ServicoHealth
from pipeline_health_monitor.tradutor_erros import traduzir_erro


# =========================================================
# ROUTER
# =========================================================
router = APIRouter(tags=["pipeline_health_monitor"])


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================
def _resposta_erro(nome_endpoint: str, exc: Exception) -> JSONResponse:
    mensagem = traduzir_erro(str(exc)) or str(exc)

    logger.exception(
        "Pipeline Health Monitor: erro no endpoint %s. Motivo: %s",
        nome_endpoint,
        mensagem,
    )

    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "endpoint": nome_endpoint,
            "erro": mensagem,
        },
    )


# =========================================================
# ENDPOINTS
# =========================================================
@router.get("/resumo")
async def obter_resumo() -> JSONResponse:
    try:
        servico = ServicoHealth()
        resumo = servico.obter_resumo()

        return JSONResponse(
            content={
                "ok": True,
                "total_pipelines": resumo.total_pipelines,
                "healthy": resumo.healthy,
                "degraded": resumo.degraded,
                "critical": resumo.critical,
                "paused": resumo.paused,
                "incidents_last_24h": resumo.incidents_last_24h,
                "status": "Online",
            }
        )
    except Exception as exc:
        return _resposta_erro("resumo", exc)


@router.get("/pipelines")
async def obter_pipelines() -> JSONResponse:
    try:
        servico = ServicoHealth()
        pipelines = servico.obter_pipelines()

        return JSONResponse(
            content={
                "ok": True,
                "items": [item.model_dump(mode="json") for item in pipelines],
            }
        )
    except Exception as exc:
        return _resposta_erro("pipelines", exc)


@router.get("/dependencias")
async def obter_dependencias() -> JSONResponse:
    try:
        servico = ServicoHealth()
        dependencias = servico.obter_dependencias()

        return JSONResponse(
            content={
                "ok": True,
                "items": [item.model_dump(mode="json") for item in dependencias],
            }
        )
    except Exception as exc:
        return _resposta_erro("dependencias", exc)


@router.get("/alertas")
async def obter_alertas() -> JSONResponse:
    try:
        servico = ServicoHealth()
        incidentes = servico.obter_incidentes()

        return JSONResponse(
            content={
                "ok": True,
                "items": [item.model_dump(mode="json") for item in incidentes],
            }
        )
    except Exception as exc:
        return _resposta_erro("alertas", exc)


@router.get("/debug")
async def debug_api() -> JSONResponse:
    try:
        servico = ServicoHealth()

        resumo = servico.obter_resumo()
        pipelines = servico.obter_pipelines()
        dependencias = servico.obter_dependencias()
        alertas = servico.obter_incidentes()

        return JSONResponse(
            content={
                "ok": True,
                "arquivo": str(Path(__file__).resolve()),
                "pacote": NOME_PACOTE_PLUGIN,
                "resumo": resumo.model_dump(mode="json"),
                "quantidade_pipelines": len(pipelines),
                "quantidade_dependencias": len(dependencias),
                "quantidade_alertas": len(alertas),
            }
        )
    except Exception as exc:
        return _resposta_erro("debug", exc)