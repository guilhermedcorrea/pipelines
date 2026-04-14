import random
import time
from typing import Any, Callable, TypeVar

from flask import current_app
from sqlalchemy.exc import DBAPIError, OperationalError

from .extensions import db

T = TypeVar("T")


def _texto_erro_completo(erro: BaseException) -> str:
    partes: list[str] = []
    atual: BaseException | None = erro
    visitados: set[int] = set()

    while atual and id(atual) not in visitados:
        visitados.add(id(atual))

        try:
            if getattr(atual, "args", None):
                partes.extend(str(item) for item in atual.args if item is not None)
        except Exception:
            partes.append(str(atual))

        proximo = getattr(atual, "orig", None) or getattr(atual, "__cause__", None)
        atual = proximo if isinstance(proximo, BaseException) else None

    return " | ".join(partes).lower()


def eh_deadlock_sql_server(erro: BaseException) -> bool:
    texto = _texto_erro_completo(erro)
    return (
        "1205" in texto
        or "deadlock" in texto
        or "vítima de deadlock" in texto
        or "deadlock victim" in texto
    )


def _eh_erro_retentavel_sql_server(erro: BaseException) -> bool:
    if isinstance(erro, (DBAPIError, OperationalError)):
        return eh_deadlock_sql_server(erro)

    return eh_deadlock_sql_server(erro)


def executar_transacao_com_retry_deadlock(
    funcao_transacional: Callable[[], T],
    *,
    max_tentativas: int = 4,
    atraso_inicial_segundos: float = 0.25,
    multiplicador_backoff: float = 2.0,
    jitter_max_segundos: float = 0.35,
) -> T:
    ultima_excecao: BaseException | None = None

    for tentativa in range(1, max_tentativas + 1):
        try:
            resultado = funcao_transacional()
            db.session.commit()
            return resultado

        except Exception as erro:
            db.session.rollback()
            ultima_excecao = erro

            if not _eh_erro_retentavel_sql_server(erro):
                raise

            if tentativa >= max_tentativas:
                raise

            atraso_base = atraso_inicial_segundos * (multiplicador_backoff ** (tentativa - 1))
            atraso_final = atraso_base + random.uniform(0, jitter_max_segundos)

            current_app.logger.warning(
                "Deadlock detectado no SQL Server. tentativa=%s/%s atraso=%.2fs erro=%s",
                tentativa,
                max_tentativas,
                atraso_final,
                str(erro),
            )

            time.sleep(atraso_final)

            try:
                db.session.remove()
            except Exception:
                db.session.close()

    raise RuntimeError("Falha inesperada no retry de deadlock") from ultima_excecao


def executar_transacao_com_retry_deadlock_ou_enfileirar(
    funcao_transacional: Callable[[], T],
    *,
    funcao_enfileirar: Callable[[BaseException], str | None],
    max_tentativas: int = 4,
    atraso_inicial_segundos: float = 0.25,
    multiplicador_backoff: float = 2.0,
    jitter_max_segundos: float = 0.35,
) -> dict[str, Any]:
    ultima_excecao: BaseException | None = None

    for tentativa in range(1, max_tentativas + 1):
        try:
            resultado = funcao_transacional()
            db.session.commit()
            return {
                "modo_execucao": "sincrono",
                "resultado": resultado,
                "task_id": None,
            }

        except Exception as erro:
            db.session.rollback()
            ultima_excecao = erro

            if not _eh_erro_retentavel_sql_server(erro):
                raise

            if tentativa >= max_tentativas:
                current_app.logger.warning(
                    "Deadlock persistente apos %s tentativas. Enfileirando operacao para retry no Redis/Celery.",
                    max_tentativas,
                )

                task_id = funcao_enfileirar(erro)
                if not task_id:
                    raise RuntimeError(
                        "Nao foi possivel enfileirar a operacao apos deadlock persistente."
                    ) from erro

                return {
                    "modo_execucao": "fila",
                    "resultado": None,
                    "task_id": task_id,
                }

            atraso_base = atraso_inicial_segundos * (multiplicador_backoff ** (tentativa - 1))
            atraso_final = atraso_base + random.uniform(0, jitter_max_segundos)

            current_app.logger.warning(
                "Deadlock detectado no SQL Server. tentativa=%s/%s atraso=%.2fs erro=%s",
                tentativa,
                max_tentativas,
                atraso_final,
                str(erro),
            )

            time.sleep(atraso_final)

            try:
                db.session.remove()
            except Exception:
                db.session.close()

    raise RuntimeError("Falha inesperada no retry de deadlock com fallback em fila") from ultima_excecao