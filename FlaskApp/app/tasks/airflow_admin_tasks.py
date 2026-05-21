import os
from datetime import datetime
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from flask import current_app

from app.celery_app import celery_app


def _env_bool(nome_variavel: str, padrao: str = "0") -> bool:
    valor = (os.getenv(nome_variavel, padrao) or "").strip().lower()
    return valor in ("1", "true", "sim", "s", "yes", "y", "on")


def _airflow_timeout_segundos() -> int:
    try:
        return max(1, int(os.getenv("AIRFLOW_API_TIMEOUT_SEGUNDOS", "5") or "5"))
    except Exception:
        return 5


def _airflow_base_url() -> str:
    base_url = (os.getenv("AIRFLOW_API_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError(
            "AIRFLOW_API_BASE_URL não foi definido no .env do Flask/Celery. "
            "Exemplo: AIRFLOW_API_BASE_URL=http://airflow-apiserver:8080"
        )
    return base_url


def _airflow_credenciais_api() -> tuple[str, str]:
    usuario = (
        os.getenv("AIRFLOW_API_USERNAME")
        or os.getenv("AIRFLOW_ADMIN_USERNAME")
        or ""
    ).strip()

    senha = (
        os.getenv("AIRFLOW_API_PASSWORD")
        or os.getenv("AIRFLOW_ADMIN_PASSWORD")
        or ""
    ).strip()

    if not usuario or not senha:
        raise RuntimeError(
            "Credenciais da API do Airflow não foram definidas no ambiente do worker Celery. "
            "Defina AIRFLOW_API_USERNAME e AIRFLOW_API_PASSWORD."
        )

    return usuario, senha


def _airflow_obter_token_api() -> str:
    if requests is None:
        raise RuntimeError(
            "A biblioteca 'requests' não está instalada no container do Celery/Flask."
        )

    base_url = _airflow_base_url()
    usuario, senha = _airflow_credenciais_api()
    timeout = _airflow_timeout_segundos()
    url_token = f"{base_url}/auth/token"

    resposta = requests.post(
        url_token,
        json={"username": usuario, "password": senha},
        timeout=timeout,
    )

    if resposta.status_code in (400, 415, 422):
        resposta = requests.post(
            url_token,
            data={"username": usuario, "password": senha},
            timeout=timeout,
        )

    try:
        resposta.raise_for_status()
    except Exception as exc:
        corpo = (resposta.text or "")[:1000]
        raise RuntimeError(
            f"Falha ao obter token do Airflow. Status={resposta.status_code}. Resposta={corpo}"
        ) from exc

    dados = resposta.json() or {}
    token = dados.get("access_token") or dados.get("token")
    if not token:
        raise RuntimeError(f"Airflow respondeu sem access_token. Resposta={dados}")

    return token


def _normalizar_int_ou_none(valor: Any) -> int | None:
    if valor in (None, "", 0, "0"):
        return None
    try:
        return int(valor)
    except Exception:
        return None


def _postar_dag_run_airflow(*, dag_id: str, dag_run_id: str, conf: dict[str, Any], note: str) -> dict[str, Any]:
    base_url = _airflow_base_url()
    token = _airflow_obter_token_api()
    timeout = _airflow_timeout_segundos()
    url_trigger = f"{base_url}/api/v2/dags/{dag_id}/dagRuns"

    resposta = requests.post(
        url_trigger,
        json={
            "dag_run_id": dag_run_id,
            "logical_date": None,
            "conf": conf,
            "note": note,
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )

    if resposta.status_code == 409:
        return {
            "ok": True,
            "status": "ja_existia",
            "dag_id": dag_id,
            "dag_run_id": dag_run_id,
        }

    try:
        resposta.raise_for_status()
    except Exception as exc:
        corpo = (resposta.text or "")[:1500]
        raise RuntimeError(
            f"Falha ao disparar DAG no Airflow. dag_id={dag_id}. "
            f"Status={resposta.status_code}. Resposta={corpo}"
        ) from exc

    try:
        dados_resposta = resposta.json() or {}
    except Exception:
        dados_resposta = {"texto": (resposta.text or "")[:1500]}

    return {
        "ok": True,
        "status": "disparado",
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "resposta": dados_resposta,
    }


@celery_app.task(
    bind=True,
    name="airflow_admin.disparar_dags_aprovacao_contrato",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def tarefa_disparar_airflow_aprovacao_contrato(
    self,
    *,
    id_contrato: int | None,
    id_solicitacao: int | None = None,
    id_card: int | None = None,
    id_usuario_logado: int | None = None,
) -> dict[str, Any]:
    """
    Dispara as DAGs do Airflow após a aprovação do contrato sem bloquear a tela Flask.

    Esta tarefa deve rodar no worker Celery/Redis. A rota Flask apenas enfileira esta
    task depois do commit da aprovação. Assim, se o Airflow estiver lento ou fora do ar,
    o contrato aprovado não fica preso no navegador do usuário.
    """

    id_contrato_int = _normalizar_int_ou_none(id_contrato)
    id_solicitacao_int = _normalizar_int_ou_none(id_solicitacao) or 0
    id_card_int = _normalizar_int_ou_none(id_card)
    id_usuario_int = _normalizar_int_ou_none(id_usuario_logado)

    if id_contrato_int is None:
        return {
            "ok": False,
            "status": "ignorado",
            "motivo": "id_contrato vazio",
            "id_solicitacao": id_solicitacao_int or None,
            "id_card": id_card_int,
        }

    resultados: dict[str, Any] = {
        "ok": True,
        "id_contrato": id_contrato_int,
        "id_solicitacao": id_solicitacao_int or None,
        "id_card": id_card_int,
        "id_usuario_logado": id_usuario_int,
        "executado_em_utc": datetime.utcnow().isoformat(timespec="seconds"),
        "dags": {},
    }

    conf_base = {
        "origem": "flask_admin_aprovacao_contrato_celery",
        "id_contrato": id_contrato_int,
        "id_solicitacao": id_solicitacao_int or None,
        "id_card": id_card_int,
        "id_usuario_logado": id_usuario_int,
    }

    if _env_bool("AIRFLOW_TRIGGER_MENSAGERIA_HABILITADO", "1"):
        dag_id_mensageria = (
            os.getenv("AIRFLOW_DAG_MENSAGERIA_CAMPANHAS")
            or "euromidia_mensageria_campanhas"
        ).strip()

        resultado_mensageria = _postar_dag_run_airflow(
            dag_id=dag_id_mensageria,
            dag_run_id=f"contrato_aprovado__{id_contrato_int}__solicitacao__{id_solicitacao_int}",
            conf=conf_base,
            note=f"Disparo Celery após aprovação do contrato {id_contrato_int}",
        )
        resultados["dags"]["mensageria_campanhas"] = resultado_mensageria
        current_app.logger.info(
            "AIRFLOW_ADMIN_TASK | mensageria disparada | resultado=%s", resultado_mensageria
        )
    else:
        resultados["dags"]["mensageria_campanhas"] = {"ok": False, "status": "desabilitado"}

    if _env_bool("AIRFLOW_TRIGGER_PRIORIDADE_RESERVAS_HABILITADO", "1"):
        dag_id_prioridade = (
            os.getenv("AIRFLOW_DAG_PRIORIDADE_RESERVAS")
            or "pipeline_prioridade_reservas"
        ).strip()

        conf_prioridade = dict(conf_base)
        conf_prioridade["modo_processamento"] = "contrato_aprovado"

        sufixo_execucao = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")

        resultado_prioridade = _postar_dag_run_airflow(
            dag_id=dag_id_prioridade,
            dag_run_id=(
                f"prioridade_reservas__contrato_aprovado__{id_contrato_int}"
                f"__solicitacao__{id_solicitacao_int}__{sufixo_execucao}"
            ),
            conf=conf_prioridade,
            note=f"Disparo Celery de prioridade/reservas após aprovação do contrato {id_contrato_int}",
        )
        resultados["dags"]["prioridade_reservas"] = resultado_prioridade
        current_app.logger.info(
            "AIRFLOW_ADMIN_TASK | prioridade/reservas disparada | resultado=%s", resultado_prioridade
        )
    else:
        resultados["dags"]["prioridade_reservas"] = {"ok": False, "status": "desabilitado"}

    return resultados


def _montar_multidict_formulario(form_data: dict[str, Any] | None):
    """Eu reconstruo o request.form serializado para uso dentro do worker Celery."""
    try:
        from werkzeug.datastructures import MultiDict
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Werkzeug/MultiDict não está disponível no worker Celery.") from exc

    formulario = MultiDict()
    for chave, valor in (form_data or {}).items():
        if isinstance(valor, (list, tuple)):
            for item in valor:
                formulario.add(str(chave), "" if item is None else str(item))
        else:
            formulario.add(str(chave), "" if valor is None else str(valor))
    return formulario


@celery_app.task(
    bind=True,
    name="airflow_admin.processar_aprovacao_contrato",
    time_limit=1800,
    soft_time_limit=1500,
)
def tarefa_processar_aprovacao_contrato(
    self,
    *,
    id_solicitacao: int,
    id_usuario_logado: int | None = None,
    form_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Processa a aprovação completa do contrato fora da requisição HTTP.

    Esta é a correção do timeout 504: a tela apenas salva a solicitação,
    marca como PROCESSANDO_APROVACAO e enfileira esta task. O worker faz a
    aprovação pesada: contrato, itens, ocupação, preço praticado, vencimentos,
    vínculos, card, histórico e disparo das DAGs posteriores.
    """

    id_solicitacao_int = _normalizar_int_ou_none(id_solicitacao)
    id_usuario_int = _normalizar_int_ou_none(id_usuario_logado)

    if id_solicitacao_int is None:
        return {
            "ok": False,
            "status": "ignorado",
            "motivo": "id_solicitacao vazio",
        }

    from app.extensions import db

    try:
        from app.admin.admin_views import _processar_aprovacao_contrato_admin

        formulario = _montar_multidict_formulario(form_data)

        current_app.logger.info(
            "APROVACAO_CONTRATO_TASK | iniciando aprovação | task_id=%s | id_solicitacao=%s | usuario=%s",
            getattr(self.request, "id", None),
            id_solicitacao_int,
            id_usuario_int,
        )

        resultado = _processar_aprovacao_contrato_admin(
            id_solicitacao=id_solicitacao_int,
            id_usuario_logado=id_usuario_int,
            form=formulario,
            enfileirar_airflow=True,
        )

        current_app.logger.info(
            "APROVACAO_CONTRATO_TASK | aprovação concluída | task_id=%s | resultado=%s",
            getattr(self.request, "id", None),
            resultado,
        )
        return resultado

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "APROVACAO_CONTRATO_TASK | erro ao processar aprovação | task_id=%s | id_solicitacao=%s",
            getattr(self.request, "id", None),
            id_solicitacao_int,
        )

        try:
            from sqlalchemy import text

            db.session.execute(
                text("""
                    UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
                       SET StatusSolicitacao = 'ERRO_APROVACAO',
                           IDDimStatusContratos = 10,
                           DataAtualizacao = GETDATE()
                     WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
                """),
                {"id_solicitacao": int(id_solicitacao_int)},
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "APROVACAO_CONTRATO_TASK | falhou ao marcar solicitação como ERRO_APROVACAO | id_solicitacao=%s",
                id_solicitacao_int,
            )

        raise exc
