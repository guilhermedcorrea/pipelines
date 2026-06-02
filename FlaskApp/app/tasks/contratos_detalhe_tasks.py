import os
from typing import Any

import requests

from app.celery_app import celery_app
from app.extensions import cache


@celery_app.task(name="contratos_detalhe.aquecer_cache_html", bind=True)
def aquecer_cache_html_contrato_detalhe(
    self,
    *,
    url_full: str,
    cookies: dict[str, str] | None = None,
    id_contrato: int | None = None,
    cache_key_html: str | None = None,
    timeout_segundos: int = 120,
) -> dict[str, Any]:
    """
    Eu aqueço em background o HTML completo do detalhe do contrato.

    A estratégia é usar a própria sessão do navegador que pediu a página,
    porque a rota /paineis/contratos/<id> tem login, permissão e regras de
    vendedor. O Celery faz a chamada completa por fora da request principal,
    a rota renderiza tudo e grava o HTML no Redis pelo cache existente.
    """
    url_full = str(url_full or "").strip()
    if not url_full:
        raise ValueError("url_full não informada para aquecer o detalhe do contrato.")

    timeout_segundos = int(timeout_segundos or 120)
    timeout_segundos = max(30, min(timeout_segundos, 600))

    headers = {
        "User-Agent": "FlaskApp-Celery-ContratoDetalhePreload/1.0",
        "X-Contrato-Detalhe-Preload": "1",
    }

    resposta = requests.get(
        url_full,
        cookies=cookies or {},
        headers=headers,
        timeout=(5, timeout_segundos),
    )

    cache_pronto = False
    if cache_key_html:
        try:
            cache_pronto = bool(cache.get(cache_key_html))
        except Exception:
            cache_pronto = False

    return {
        "ok": resposta.status_code == 200 and cache_pronto,
        "status_code": resposta.status_code,
        "id_contrato": int(id_contrato or 0),
        "cache_key_html": cache_key_html,
        "cache_pronto": cache_pronto,
        "content_length": len(resposta.content or b""),
    }
