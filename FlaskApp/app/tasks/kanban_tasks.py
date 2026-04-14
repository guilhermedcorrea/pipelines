from celery import shared_task

from app import create_app
from app.extensions import db
from app.retry_deadlock import eh_deadlock_sql_server
from app.kanban.kanban_views import (
    _executar_movimento_card_core,
    _finalizar_pos_movimento_card,
)


class ErroDeadlockRetentavel(Exception):
    """Eu sinalizo para o Celery que o erro pode ser tentado de novo."""


@shared_task(
    bind=True,
    name="app.kanban.tarefa_retry_movimento_card",
    autoretry_for=(ErroDeadlockRetentavel,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 6},
)
def tarefa_retry_movimento_card(self, payload: dict) -> dict:
    app = create_app()

    with app.app_context():
        try:
            id_card = int(payload["id_card"])
            id_usuario = int(payload["id_usuario"])
            id_emp = int(payload["id_emp"])
            payload_movimento = dict(payload.get("payload") or {})

            resultado_core = _executar_movimento_card_core(
                id_card=id_card,
                id_usuario=id_usuario,
                id_emp=id_emp,
                payload=payload_movimento,
            )

            db.session.commit()

            resposta = _finalizar_pos_movimento_card(
                id_card=id_card,
                id_emp=id_emp,
                resultado_core=resultado_core,
            )

            return resposta

        except Exception as exc:
            db.session.rollback()

            if eh_deadlock_sql_server(exc):
                raise ErroDeadlockRetentavel(str(exc)) from exc

            raise