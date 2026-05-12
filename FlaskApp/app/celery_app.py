import os
from celery import Celery

from config import CACHE_REDIS_URL


def _obter_broker_url() -> str:
    return (
        os.getenv("CELERY_BROKER_URL")
        or os.getenv("CELERY_REDIS_URL")
        or CACHE_REDIS_URL
    )


def _obter_result_backend() -> str:
    return (
        os.getenv("CELERY_RESULT_BACKEND")
        or os.getenv("CELERY_REDIS_RESULT_URL")
        or _obter_broker_url()
    )


celery_app = Celery("flaskapp")

celery_app.conf.update(
    broker_url=_obter_broker_url(),
    result_backend=_obter_result_backend(),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Sao_Paulo",
    enable_utc=False,
    task_track_started=True,
    task_default_queue="checkin_upload",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=86400,
    imports=(
        "app.tasks.checkin_tasks",
        "app.tasks.clientes_cache_tasks",
        "app.tasks.paineis_tempo_real_tasks",
    ),
    task_routes={
        "clientes_cache.*": {"queue": "clientes_cache"},
        "paineis_ocupacao.*": {"queue": "paineis_ocupacao"},
        "app.tasks.checkin_tasks.*": {"queue": "checkin_upload"},
    },
)

_app_flask_cache = None


def _obter_app_flask():
    global _app_flask_cache

    if _app_flask_cache is None:
        from . import create_app
        _app_flask_cache = create_app()

    return _app_flask_cache


class TarefaComContexto(celery_app.Task):
    abstract = True

    def __call__(self, *args, **kwargs):
        app = _obter_app_flask()
        with app.app_context():
            return super().__call__(*args, **kwargs)


celery_app.Task = TarefaComContexto