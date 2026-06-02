from flask_sqlalchemy import SQLAlchemy
from ..extensions import db, limiter, csrf, cache
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify,current_app,abort, send_file
from ..models.admin_models import FatoMovimentoFinanceiroEmpresas, DimEmpresaProprietaria,DimProdutoAuvo
from datetime import datetime, date, timedelta
from sqlalchemy import func, case,text
from flask_login import login_required, current_user
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from ..autenticacao.autenticacao_views import requer_permissao
from pathlib import Path
import hashlib
import json
import re

import os
import shutil
import uuid

try:
    import requests
except ImportError: 
    requests = None

from app.socket_events import emitir_resumo_mensagens_usuario, emitir_nova_mensagem_usuario


admin = Blueprint("admin", __name__)




ID_STATUS_CONTRATO_APROVADO = 2
ID_FASE_FORMULARIO_CONTRATO = 4
TABELA_CARD_OCORRENCIA = "[Integracao].[Silver].[FatoCardOCorrencia]"
TABELA_VENCIMENTO_CAMPANHA = "[Integracao].[Silver].[FatoVencimentoCampanhaEuromidia]"
TABELA_OCUPACAO_PAINEIS_EUROMIDIA_ADMIN = "[Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]"
TABELA_KANBAN_CARD_RENOVACAO = "[Kanban].[Silver].[FatoKanbanCard]"
TABELA_KANBAN_CARD_TAG_RENOVACAO = "[Kanban].[Silver].[FatoKanbanCardTag]"
TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO = "[Kanban].[Silver].[FatoKanbanCardPainelFace]"
TABELA_CONTRATO_EMPRESA_RELACIONADA = "[Integracao].[Silver].[FatoContratoEmpresaRelacionada]"
TABELA_EMAIL_CONTRATO_ADMIN = "[Integracao].[Silver].[DimEmailContrato]"
TABELA_HISTORICO_CONTRATOS_D4_ADMIN = "[Integracao].[Silver].[DimHistoricoContratosD4]"
TABELA_ANEXOS_CONTRATOS_ADMIN = "[Integracao].[Silver].[FatoAnexosContratosEuromidia]"
TABELA_KANBAN_FASE_RENOVACAO = "[Kanban].[Silver].[DimKanbanFase]"
TABELA_KANBAN_TAG_RENOVACAO = "[Kanban].[Silver].[DimKanbanTag]"
TABELA_KANBAN_STATUS_CARD_RENOVACAO = "[Kanban].[Silver].[DimKanbanStatusCard]"

ID_KANBAN_RENOVACAO_CAMPANHA = 1
ID_TAG_RENOVACAO_CAMPANHA = 17
ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN = 8
ID_TAG_TIPO_CONTRATO_NOVO_ADMIN = 9
ID_EMPRESA_PROPRIETARIA_EUROMIDIA_RENOVACAO = 3
ID_EMPRESA_PROPRIETARIA_HISTORICO_D4_ADMIN = 3
ID_STATUS_D4_PROCESSANDO_ADMIN = 1
STATUS_CARD_RENOVACAO_PADRAO = "ATIVO"

ID_STATUS_CAMPANHA_FUTURA = 1
ID_STATUS_CAMPANHA_ATIVA = 2
ID_STATUS_CAMPANHA_VENCIDA = 4
ID_STATUS_CAMPANHA_CANCELADA = 5
ID_STATUS_CAMPANHA_SEM_DATA_TERMINO = 6
ID_STATUS_CAMPANHA_RENOVADA = 8

ID_TIPOS_DOCUMENTO_GERAM_CAMPANHA = {1, 3}


def _env_bool(nome_variavel: str, padrao: str = "0") -> bool:
    valor = (os.getenv(nome_variavel, padrao) or "").strip().lower()
    return valor in ("1", "true", "sim", "yes", "y", "on")


def _airflow_timeout_segundos() -> int:
    try:
        return max(1, int(os.getenv("AIRFLOW_API_TIMEOUT_SEGUNDOS", "5") or "5"))
    except Exception:
        return 5


def _airflow_base_url() -> str:
    base_url = (os.getenv("AIRFLOW_API_BASE_URL") or "").strip().rstrip("/")

    if not base_url:
        raise RuntimeError(
            "AIRFLOW_API_BASE_URL não foi definido no .env do Flask. "
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
            "Credenciais da API do Airflow não foram definidas. "
            "Defina AIRFLOW_API_USERNAME e AIRFLOW_API_PASSWORD no .env do Flask."
        )

    return usuario, senha


def _airflow_obter_token_api() -> str:
    """
    Obtém o token JWT na API pública do Airflow.

    Primeiro tenta JSON. Se o Airflow responder que o formato não foi aceito,
    tenta form-data como fallback.
    """

    if requests is None:
        raise RuntimeError(
            "A biblioteca 'requests' não está instalada no container Flask. "
            "Adicione requests no requirements.txt ou instale a dependência na imagem."
        )

    base_url = _airflow_base_url()
    usuario, senha = _airflow_credenciais_api()
    timeout = _airflow_timeout_segundos()
    url_token = f"{base_url}/auth/token"

    resposta = requests.post(
        url_token,
        json={
            "username": usuario,
            "password": senha,
        },
        timeout=timeout,
    )

    if resposta.status_code in (400, 415, 422):
        resposta = requests.post(
            url_token,
            data={
                "username": usuario,
                "password": senha,
            },
            timeout=timeout,
        )

    try:
        resposta.raise_for_status()
    except Exception as exc:
        corpo = (resposta.text or "")[:1000]
        raise RuntimeError(
            f"Falha ao obter token do Airflow. "
            f"Status={resposta.status_code}. Resposta={corpo}"
        ) from exc

    dados = resposta.json() or {}
    token = dados.get("access_token") or dados.get("token")

    if not token:
        raise RuntimeError(
            f"Airflow respondeu sem access_token. Resposta={dados}"
        )

    return token


def _airflow_disparar_dag_mensageria_campanhas(
    *,
    id_contrato: int | None,
    id_solicitacao: int | None = None,
    id_card: int | None = None,
    id_usuario_logado: int | None = None,
) -> dict:
    """
    Dispara a DAG de mensageria de campanhas no Airflow.

    Importante:
    - esta função só dispara a DAG;
    - não espera a DAG terminar;
    - se falhar, a aprovação do contrato não deve ser desfeita;
    - o schedule de 10 em 10 minutos continua como rede de segurança.
    """

    if not _env_bool("AIRFLOW_TRIGGER_MENSAGERIA_HABILITADO", "1"):
        return {
            "ok": False,
            "status": "desabilitado",
            "mensagem": "Disparo automático da DAG está desabilitado por env.",
        }

    if id_contrato in (None, "", 0):
        return {
            "ok": False,
            "status": "sem_id_contrato",
            "mensagem": "Não disparei a DAG porque id_contrato veio vazio.",
        }

    if requests is None:
        raise RuntimeError(
            "A biblioteca 'requests' não está instalada no container Flask. "
            "Adicione requests no requirements.txt ou instale a dependência na imagem."
        )

    base_url = _airflow_base_url()
    token = _airflow_obter_token_api()

    dag_id = (
        os.getenv("AIRFLOW_DAG_MENSAGERIA_CAMPANHAS")
        or "euromidia_mensageria_campanhas"
    ).strip()

    id_contrato_int = int(id_contrato)
    id_solicitacao_int = int(id_solicitacao) if id_solicitacao not in (None, "", 0) else 0
    id_card_int = int(id_card) if id_card not in (None, "", 0) else None
    id_usuario_int = int(id_usuario_logado) if id_usuario_logado not in (None, "", 0) else None

    dag_run_id = f"contrato_aprovado__{id_contrato_int}__solicitacao__{id_solicitacao_int}"

    payload = {
        "dag_run_id": dag_run_id,
        "logical_date": None,
        "conf": {
            "origem": "flask_admin_aprovacao_contrato",
            "id_contrato": id_contrato_int,
            "id_solicitacao": id_solicitacao_int or None,
            "id_card": id_card_int,
            "id_usuario_logado": id_usuario_int,
        },
        "note": f"Disparo automático após aprovação do contrato {id_contrato_int}",
    }

    timeout = _airflow_timeout_segundos()
    url_trigger = f"{base_url}/api/v2/dags/{dag_id}/dagRuns"

    resposta = requests.post(
        url_trigger,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )

    if resposta.status_code == 409:
        current_app.logger.warning(
            "AIRFLOW_MENSAGERIA | DAG run já existia | dag_id=%s | dag_run_id=%s | id_contrato=%s",
            dag_id,
            dag_run_id,
            id_contrato_int,
        )

        return {
            "ok": True,
            "status": "ja_existia",
            "dag_id": dag_id,
            "dag_run_id": dag_run_id,
            "id_contrato": id_contrato_int,
        }

    try:
        resposta.raise_for_status()
    except Exception as exc:
        corpo = (resposta.text or "")[:1500]
        raise RuntimeError(
            f"Falha ao disparar DAG no Airflow. "
            f"Status={resposta.status_code}. Resposta={corpo}"
        ) from exc

    try:
        dados_resposta = resposta.json() or {}
    except Exception:
        dados_resposta = {"texto": (resposta.text or "")[:1500]}

    current_app.logger.info(
        "AIRFLOW_MENSAGERIA | DAG disparada com sucesso | dag_id=%s | dag_run_id=%s | id_contrato=%s",
        dag_id,
        dag_run_id,
        id_contrato_int,
    )

    return {
        "ok": True,
        "status": "disparado",
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "id_contrato": id_contrato_int,
        "resposta": dados_resposta,
    }













def _airflow_disparar_dag_prioridade_reservas(
    *,
    id_contrato: int | None,
    id_solicitacao: int | None = None,
    id_card: int | None = None,
    id_usuario_logado: int | None = None,
) -> dict:
    """
    Disparo a DAG de prioridade/reservas logo após a aprovação do contrato.

    A aprovação precisa gravar antes a ocupação contratual em
    Integracao.Silver.FatoOcupacaoPaineisEuromidia. Depois disso, a DAG usa essa
    ocupação como origem para criar reserva futura de preferência, quando a regra
    de 6 meses ou mais for atendida.
    """

    if not _env_bool("AIRFLOW_TRIGGER_PRIORIDADE_RESERVAS_HABILITADO", "1"):
        return {
            "ok": False,
            "status": "desabilitado",
            "mensagem": "Disparo automático da DAG de prioridade/reservas está desabilitado por env.",
        }

    if id_contrato in (None, "", 0):
        return {
            "ok": False,
            "status": "sem_id_contrato",
            "mensagem": "Não disparei a DAG de prioridade/reservas porque id_contrato veio vazio.",
        }

    if requests is None:
        raise RuntimeError(
            "A biblioteca 'requests' não está instalada no container Flask. "
            "Adicione requests no requirements.txt ou instale a dependência na imagem."
        )

    base_url = _airflow_base_url()
    token = _airflow_obter_token_api()

    dag_id = (
        os.getenv("AIRFLOW_DAG_PRIORIDADE_RESERVAS")
        or "pipeline_prioridade_reservas"
    ).strip()

    id_contrato_int = int(id_contrato)
    id_solicitacao_int = int(id_solicitacao) if id_solicitacao not in (None, "", 0) else 0
    id_card_int = int(id_card) if id_card not in (None, "", 0) else None
    id_usuario_int = int(id_usuario_logado) if id_usuario_logado not in (None, "", 0) else None

    agora = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    dag_run_id = f"prioridade_reservas__contrato_aprovado__{id_contrato_int}__solicitacao__{id_solicitacao_int}__{agora}"

    payload = {
        "dag_run_id": dag_run_id,
        "logical_date": None,
        "conf": {
            "origem": "flask_admin_aprovacao_contrato",
            "modo_processamento": "contrato_aprovado",
            "id_contrato": id_contrato_int,
            "id_solicitacao": id_solicitacao_int or None,
            "id_card": id_card_int,
            "id_usuario_logado": id_usuario_int,
        },
        "note": f"Disparo automático de prioridade/reservas após aprovação do contrato {id_contrato_int}",
    }

    timeout = _airflow_timeout_segundos()
    url_trigger = f"{base_url}/api/v2/dags/{dag_id}/dagRuns"

    resposta = requests.post(
        url_trigger,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )

    if resposta.status_code == 409:
        current_app.logger.warning(
            "AIRFLOW_PRIORIDADE_RESERVAS | DAG run já existia | dag_id=%s | dag_run_id=%s | id_contrato=%s",
            dag_id,
            dag_run_id,
            id_contrato_int,
        )

        return {
            "ok": True,
            "status": "ja_existia",
            "dag_id": dag_id,
            "dag_run_id": dag_run_id,
            "id_contrato": id_contrato_int,
        }

    try:
        resposta.raise_for_status()
    except Exception as exc:
        corpo = (resposta.text or "")[:1500]
        raise RuntimeError(
            f"Falha ao disparar DAG de prioridade/reservas no Airflow. "
            f"Status={resposta.status_code}. Resposta={corpo}"
        ) from exc

    try:
        dados_resposta = resposta.json() or {}
    except Exception:
        dados_resposta = {"texto": (resposta.text or "")[:1500]}

    current_app.logger.info(
        "AIRFLOW_PRIORIDADE_RESERVAS | DAG disparada com sucesso | dag_id=%s | dag_run_id=%s | id_contrato=%s",
        dag_id,
        dag_run_id,
        id_contrato_int,
    )

    return {
        "ok": True,
        "status": "disparado",
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "id_contrato": id_contrato_int,
        "resposta": dados_resposta,
    }


def _parse_date_br(s: str) -> date | None:
    if not s:
        return None

    s = (s or "").strip()
    if not s:
        return None

    formatos = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")
    for fmt in formatos:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    return None


def _fim_exclusivo_do_dia(dt: date):
    return dt + timedelta(days=1)



def _tem_filtro_ativo(
    q: str,
    sistema: str,
    movimento: str,
    categoria: str,
    status: str,
    empresa_raw: str,
    data_ini_raw: str,
    data_fim_raw: str,
    modo: str,
) -> bool:
    
    modo_u = (modo or "").strip().upper()
    if modo_u in ("TUDO", "MES_ATUAL"):
        return True

    if (q or "").strip():
        return True
    if (sistema or "").strip():
        return True
    if (movimento or "").strip():
        return True
    if (categoria or "").strip():
        return True
    if (status or "").strip():
        return True
    if (empresa_raw or "").strip():
        return True
    if (data_ini_raw or "").strip():
        return True
    if (data_fim_raw or "").strip():
        return True

    return False






@admin.route("/movimentacao/empresas", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def lista_movimentacao_empresas():

    q = (request.args.get("q") or "").strip()
    sistema = (request.args.get("sistema") or "").strip()


    movimento_raw = (request.args.get("movimento") or "").strip()

    categoria = (request.args.get("categoria") or "").strip()
    status = (request.args.get("status") or "").strip()
    empresa_raw = (request.args.get("empresa") or "").strip()
    print(empresa_raw)


    modo_param = (request.args.get("modo") or "").strip()
    modo = (modo_param or "MES_ATUAL").strip().upper()

    data_ini_raw = (request.args.get("data_ini") or "").strip()
    data_fim_raw = (request.args.get("data_fim") or "").strip()

    try:
        page = int(request.args.get("page") or "1")
    except Exception:
        page = 1

    try:
        per_page = int(request.args.get("per_page") or "20")
    except Exception:
        per_page = 20

    per_page = max(5, min(per_page, 100))
    page = max(1, page)

    empresa_id = None
    if empresa_raw:
        try:
            empresa_id = int(empresa_raw)
        except Exception:
            empresa_id = None

    hoje = date.today()


    datas_foram_informadas = bool((request.args.get("data_ini") or "").strip() or (request.args.get("data_fim") or "").strip())
    modo_foi_informado = bool(modo_param)


    sem_filtros_reais = (
        (not q)
        and (not sistema)
        and (not movimento_raw)
        and (not categoria)
        and (not status)
        and (not empresa_raw)
        and (not datas_foram_informadas)
        and (not modo_foi_informado)
    )


    if sem_filtros_reais:
        modo = "MES_ATUAL"
        data_ini_raw = date(hoje.year, hoje.month, 1).strftime("%Y-%m-%d")
        data_fim_raw = hoje.strftime("%Y-%m-%d")

   
    if modo == "TUDO":
        data_ini_raw = ""
        data_fim_raw = ""


    somente_empresa = (
        bool(empresa_id is not None)
        and (not q)
        and (not sistema)
        and (not movimento_raw)
        and (not categoria)
        and (not status)
    )

    if somente_empresa and (not datas_foram_informadas) and (not modo_foi_informado):
        modo = "TUDO"
        data_ini_raw = ""
        data_fim_raw = ""

    data_ini = _parse_date_br(data_ini_raw)
    data_fim = _parse_date_br(data_fim_raw)

    data_ini_iso = data_ini.strftime("%Y-%m-%d") if data_ini else ""
    data_fim_iso = data_fim.strftime("%Y-%m-%d") if data_fim else ""

    dt_ini = None
    dt_fim_exclusivo = None

    if modo == "MES_ATUAL":
        dt_ini = date(hoje.year, hoje.month, 1)
        dt_fim_exclusivo = _fim_exclusivo_do_dia(hoje)

    elif modo == "TUDO":
        dt_ini = None
        dt_fim_exclusivo = None

    else:
        if data_ini and data_fim:
            if data_fim < data_ini:
                data_ini, data_fim = data_fim, data_ini

            dt_ini = data_ini
            dt_fim_exclusivo = _fim_exclusivo_do_dia(data_fim)

        elif data_ini and not data_fim:
            dt_ini = data_ini
            dt_fim_exclusivo = None

        elif (not data_ini) and data_fim:
            dt_ini = None
            dt_fim_exclusivo = _fim_exclusivo_do_dia(data_fim)

        else:
            dt_ini = None
            dt_fim_exclusivo = None


    movimento = ""
    if movimento_raw:
        m = (movimento_raw or "").strip()
        m_u = m.upper()

        mapa_alias = {
            "A PAGAR": "A PAGAR - FUTURO",
            "A RECEBER": "A RECEBER - FUTURO",
            "PAGO": "PAGAMENTOS REALIZADOS",
            "RECEBIDO": "RECEBIDOS",

            "RECEBIMENTOS EM ATRASO": "A RECEBER - EM ATRASO",
            "RECEBIDOS EM ATRASO": "A RECEBER - EM ATRASO",
            "A RECEBER EM ATRASO": "A RECEBER - EM ATRASO",

            "PAGAMENTOS EM ATRASO": "A PAGAR - EM ATRASO",
            "A PAGAR EM ATRASO": "A PAGAR - EM ATRASO",
        }

        movimento = mapa_alias.get(m_u, m)

    base_q = (
        db.session.query(
            FatoMovimentoFinanceiroEmpresas.IDFatoMovimentoFinanceiroEmpresas.label("ID"),
            FatoMovimentoFinanceiroEmpresas.Sistema.label("SistemaOrigem"),
            FatoMovimentoFinanceiroEmpresas.IDEmpresaProprietaria.label("Empresa"),
            FatoMovimentoFinanceiroEmpresas.Status.label("Status"),
            FatoMovimentoFinanceiroEmpresas.DataVencimento.label("DataVencimento"),
            FatoMovimentoFinanceiroEmpresas.DataPagamento.label("DataPagamento"),
            FatoMovimentoFinanceiroEmpresas.DataCompetencia.label("DataCompetencia"),
            FatoMovimentoFinanceiroEmpresas.Categoria.label("Categoria"),
            FatoMovimentoFinanceiroEmpresas.Valor.label("Valor"),
            FatoMovimentoFinanceiroEmpresas.nCodTitulo.label("Referencia"),
            FatoMovimentoFinanceiroEmpresas.Tipo.label("Tipo"),
            FatoMovimentoFinanceiroEmpresas.Nivel1.label("Nivel1"),
            FatoMovimentoFinanceiroEmpresas.ReferenciaPedidoOS.label("ReferenciaPedidoOS"),
            FatoMovimentoFinanceiroEmpresas.Movimento.label("Movimento"),
            DimEmpresaProprietaria.RazaoSocial.label("RazaoSocial"),
            DimEmpresaProprietaria.Logo.label("Logo"),
            DimEmpresaProprietaria.CNPJ.label("CNPJ"),
        )
        .outerjoin(
            DimEmpresaProprietaria,
            DimEmpresaProprietaria.IDEmpresaProprietaria == FatoMovimentoFinanceiroEmpresas.IDEmpresaProprietaria,
        )
    )

    if dt_ini is not None:
        base_q = base_q.filter(FatoMovimentoFinanceiroEmpresas.DataVencimento >= dt_ini)
    if dt_fim_exclusivo is not None:
        base_q = base_q.filter(FatoMovimentoFinanceiroEmpresas.DataVencimento < dt_fim_exclusivo)

    if sistema:
        base_q = base_q.filter(FatoMovimentoFinanceiroEmpresas.Sistema == sistema)

    if movimento:
        base_q = base_q.filter(FatoMovimentoFinanceiroEmpresas.Movimento == movimento)

    if status:
        base_q = base_q.filter(FatoMovimentoFinanceiroEmpresas.Status == status)

    if categoria:
        base_q = base_q.filter(FatoMovimentoFinanceiroEmpresas.Categoria == categoria)

    if empresa_id is not None:
        base_q = base_q.filter(FatoMovimentoFinanceiroEmpresas.IDEmpresaProprietaria == empresa_id)

    if q:
        like = f"%{q}%"
        base_q = base_q.filter(
            func.coalesce(FatoMovimentoFinanceiroEmpresas.Categoria, "").like(like)
            | func.coalesce(FatoMovimentoFinanceiroEmpresas.Tipo, "").like(like)
            | func.coalesce(FatoMovimentoFinanceiroEmpresas.Movimento, "").like(like)
            | func.coalesce(FatoMovimentoFinanceiroEmpresas.nCodTitulo, "").like(like)
            | func.coalesce(FatoMovimentoFinanceiroEmpresas.ReferenciaPedidoOS, "").like(like)
            | func.coalesce(DimEmpresaProprietaria.RazaoSocial, "").like(like)
        )

    total = base_q.count()

    tem_filtro_ativo = _tem_filtro_ativo(
        q, sistema, movimento, categoria, status, empresa_raw, data_ini_raw, data_fim_raw, modo
    )

    resumo = None

    if tem_filtro_ativo:
        sq = base_q.subquery()

        total_reg = db.session.query(func.count()).select_from(sq).scalar()
        total_valor = db.session.query(func.coalesce(func.sum(sq.c.Valor), 0)).select_from(sq).scalar()

        def _soma_mov(mov_nome: str):
            return db.session.query(
                func.coalesce(
                    func.sum(case((sq.c.Movimento == mov_nome, sq.c.Valor), else_=0)),
                    0,
                )
            ).select_from(sq).scalar()

        def _qtd_mov(mov_nome: str):
            return db.session.query(
                func.coalesce(
                    func.sum(case((sq.c.Movimento == mov_nome, 1), else_=0)),
                    0,
                )
            ).select_from(sq).scalar()

        a_pagar_valor = _soma_mov("A PAGAR - FUTURO")
        a_pagar_qtd   = _qtd_mov("A PAGAR - FUTURO")

        a_receber_valor = _soma_mov("A RECEBER - FUTURO")
        a_receber_qtd   = _qtd_mov("A RECEBER - FUTURO")

        pago_valor = _soma_mov("PAGAMENTOS REALIZADOS")
        pago_qtd   = _qtd_mov("PAGAMENTOS REALIZADOS")

        recebido_valor = _soma_mov("RECEBIDOS")
        recebido_qtd   = _qtd_mov("RECEBIDOS")

        receb_em_atraso_valor = _soma_mov("A RECEBER - EM ATRASO")
        receb_em_atraso_qtd   = _qtd_mov("A RECEBER - EM ATRASO")

        pag_em_atraso_valor = _soma_mov("A PAGAR - EM ATRASO")
        pag_em_atraso_qtd   = _qtd_mov("A PAGAR - EM ATRASO")

        movimentos_validos = [
            "A PAGAR - FUTURO",
            "A RECEBER - FUTURO",
            "PAGAMENTOS REALIZADOS",
            "RECEBIDOS",
            "A RECEBER - EM ATRASO",
            "A PAGAR - EM ATRASO",
        ]

        aberto_outros_valor = db.session.query(
            func.coalesce(
                func.sum(
                    case(
                        (
                            (sq.c.Movimento.is_(None))
                            | (func.ltrim(func.rtrim(sq.c.Movimento)) == "")
                            | (~sq.c.Movimento.in_(movimentos_validos)),
                            sq.c.Valor,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        ).select_from(sq).scalar()

        aberto_outros_qtd = db.session.query(
            func.coalesce(
                func.sum(
                    case(
                        (
                            (sq.c.Movimento.is_(None))
                            | (func.ltrim(func.rtrim(sq.c.Movimento)) == "")
                            | (~sq.c.Movimento.in_(movimentos_validos)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        ).select_from(sq).scalar()

        cat_row = (
            db.session.query(
                sq.c.Categoria.label("Categoria"),
                func.coalesce(func.sum(sq.c.Valor), 0).label("SomaValor"),
            )
            .select_from(sq)
            .group_by(sq.c.Categoria)
            .order_by(func.abs(func.coalesce(func.sum(sq.c.Valor), 0)).desc())
            .first()
        )

        resumo = {
            "total_reg": int(total_reg or 0),
            "total_valor": float(total_valor or 0),

            "pagar_valor": float(a_pagar_valor or 0),
            "pagar_qtd": int(a_pagar_qtd or 0),

            "receber_valor": float(a_receber_valor or 0),
            "receber_qtd": int(a_receber_qtd or 0),

            "pago_valor": float(pago_valor or 0),
            "pago_qtd": int(pago_qtd or 0),

            "recebido_valor": float(recebido_valor or 0),
            "recebido_qtd": int(recebido_qtd or 0),

            "receb_em_atraso_valor": float(receb_em_atraso_valor or 0),
            "receb_em_atraso_qtd": int(receb_em_atraso_qtd or 0),

            "pag_em_atraso_valor": float(pag_em_atraso_valor or 0),
            "pag_em_atraso_qtd": int(pag_em_atraso_qtd or 0),

            "aberto_outros_valor": float(aberto_outros_valor or 0),
            "aberto_outros_qtd": int(aberto_outros_qtd or 0),

            "cat_top_nome": (cat_row.Categoria if cat_row and cat_row.Categoria else "—"),
            "cat_top_valor": float(cat_row.SomaValor if cat_row else 0),
        }

    ordem = [
        case((FatoMovimentoFinanceiroEmpresas.DataVencimento.is_(None), 1), else_=0).asc(),
        FatoMovimentoFinanceiroEmpresas.DataVencimento.desc(),
        case((FatoMovimentoFinanceiroEmpresas.DataCompetencia.is_(None), 1), else_=0).asc(),
        FatoMovimentoFinanceiroEmpresas.DataCompetencia.desc(),
        FatoMovimentoFinanceiroEmpresas.IDFatoMovimentoFinanceiroEmpresas.desc(),
    ]

    rows = (
        base_q.order_by(*ordem)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    itens = []
    for r in rows:
        itens.append(
            {
                "ID": r.ID,
                "SistemaOrigem": r.SistemaOrigem,
                "Empresa": r.Empresa,
                "RazaoSocial": r.RazaoSocial or "Empresa não identificada",
                "Logo": (r.Logo or "").strip(),
                "CNPJ": r.CNPJ or "",
                "Status": r.Status or "",
                "DataVencimento": r.DataVencimento,
                "DataPagamento": r.DataPagamento,
                "DataCompetencia": r.DataCompetencia,
                "Descricao": "",
                "Contraparte": "",
                "DocumentoContraparte": "",
                "Categoria": r.Categoria or "",
                "Valor": r.Valor,
                "Referencia": r.Referencia or "",
                "Tipo": r.Tipo or "",
                "DataAtualizacao": None,
                "Movimento": r.Movimento or "",
                "Nivel1": r.Nivel1 or "",
                "ReferenciaPedidoOS": r.ReferenciaPedidoOS or "",
            }
        )

    total_pages = max(1, (total + per_page - 1) // per_page)

    sistemas = (
        db.session.query(FatoMovimentoFinanceiroEmpresas.Sistema)
        .filter(
            FatoMovimentoFinanceiroEmpresas.Sistema != None,
            FatoMovimentoFinanceiroEmpresas.Sistema != "",
        )
        .distinct()
        .order_by(FatoMovimentoFinanceiroEmpresas.Sistema.asc())
        .all()
    )
    sistemas = [x[0] for x in sistemas if x and x[0]]

    movimentos = (
        db.session.query(FatoMovimentoFinanceiroEmpresas.Movimento)
        .filter(
            FatoMovimentoFinanceiroEmpresas.Movimento != None,
            FatoMovimentoFinanceiroEmpresas.Movimento != "",
        )
        .distinct()
        .order_by(FatoMovimentoFinanceiroEmpresas.Movimento.asc())
        .all()
    )
    movimentos = [x[0] for x in movimentos if x and x[0]]

    status_list = (
        db.session.query(FatoMovimentoFinanceiroEmpresas.Status)
        .filter(
            FatoMovimentoFinanceiroEmpresas.Status != None,
            FatoMovimentoFinanceiroEmpresas.Status != "",
        )
        .distinct()
        .order_by(FatoMovimentoFinanceiroEmpresas.Status.asc())
        .all()
    )
    status_list = [x[0] for x in status_list if x and x[0]]

    categorias = (
        db.session.query(FatoMovimentoFinanceiroEmpresas.Categoria)
        .filter(
            FatoMovimentoFinanceiroEmpresas.Categoria != None,
            FatoMovimentoFinanceiroEmpresas.Categoria != "",
        )
        .distinct()
        .order_by(FatoMovimentoFinanceiroEmpresas.Categoria.asc())
        .all()
    )
    categorias = [x[0] for x in categorias if x and x[0]]

    empresas_rows = (
        db.session.query(
            DimEmpresaProprietaria.IDEmpresaProprietaria,
            DimEmpresaProprietaria.RazaoSocial,
        )
        .join(
            FatoMovimentoFinanceiroEmpresas,
            FatoMovimentoFinanceiroEmpresas.IDEmpresaProprietaria == DimEmpresaProprietaria.IDEmpresaProprietaria,
        )
        .filter(
            DimEmpresaProprietaria.IDEmpresaProprietaria != None,
            func.coalesce(DimEmpresaProprietaria.RazaoSocial, "") != "",
        )
        .distinct()
        .order_by(DimEmpresaProprietaria.RazaoSocial.asc())
        .all()
    )
    empresas = [{"id": x[0], "razao": x[1]} for x in empresas_rows if x and x[0]]

    print("DEBUG FILTRO => modo:", modo,
      "data_ini_raw:", data_ini_raw,
      "data_fim_raw:", data_fim_raw,
      "dt_ini:", dt_ini,
      "dt_fim_exclusivo:", dt_fim_exclusivo,
      "empresa_id:", empresa_id)



    return render_template(
        "admin/movimentacao_empresas_lista.html",
        itens=itens,
        sistemas=sistemas,
        movimentos=movimentos,
        status_list=status_list,
        categorias=categorias,
        empresas=empresas,
        resumo=resumo,
        tem_filtro_ativo=tem_filtro_ativo,
        filtros={
            "q": q,
            "sistema": sistema,
            "movimento": movimento,
            "status": status,
            "categoria": categoria,
            "empresa": empresa_id if empresa_id is not None else "",
            "data_ini": data_ini_raw,
            "data_fim": data_fim_raw,
            "data_ini_iso": data_ini_iso,
            "data_fim_iso": data_fim_iso,
            "modo": modo,
            "per_page": per_page,
        },
        paginacao={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "inicio": (page - 1) * per_page + 1 if total > 0 else 0,
            "fim": min(page * per_page, total),
        },
    )






from flask import abort, render_template
from sqlalchemy.orm import aliased

@admin.route("/movimentacao/empresas/<int:id_mov>", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def movimentacao_empresas_detalhe(id_mov: int):
 
    r = (
        db.session.query(
            FatoMovimentoFinanceiroEmpresas,
            DimEmpresaProprietaria.RazaoSocial.label("RazaoSocial"),
            DimEmpresaProprietaria.Logo.label("Logo"),
            DimEmpresaProprietaria.CNPJ.label("CNPJ"),
        )
        .join(
            DimEmpresaProprietaria,
            DimEmpresaProprietaria.IDEmpresaProprietaria == FatoMovimentoFinanceiroEmpresas.IDEmpresaProprietaria,
        )
        .filter(FatoMovimentoFinanceiroEmpresas.IDFatoMovimentoFinanceiroEmpresas == id_mov)
        .first()
    )

    if not r:
        abort(404)

    # r[0] é a entidade (registro da fato); os outros vêm pelos labels
    mov = r[0]

    empresa_info = {
        "RazaoSocial": (r.RazaoSocial or "Empresa não identificada"),
        "Logo": (r.Logo or "").strip(),
        "CNPJ": (r.CNPJ or ""),
    }

  
    sistema = (mov.Sistema or "").strip().upper()

    detalhe_origem = None
    detalhe_origem_fonte = None
    aviso_match = None


    if sistema == "OMIE":
        detalhe_origem, aviso_match = _buscar_detalhe_omie_por_heuristica(mov)
        detalhe_origem_fonte = "OMIE"
    elif sistema == "GRANATUM":
        detalhe_origem, aviso_match = _buscar_detalhe_granatum_por_heuristica(mov)
        detalhe_origem_fonte = "GRANATUM"
    else:
        detalhe_origem = None
        detalhe_origem_fonte = None
        aviso_match = "Sistema de origem não reconhecido para detalhamento."

    return render_template(
        "admin/movimentacao_empresas_detalhe.html",
        mov=mov,
        empresa_info=empresa_info,
        detalhe=detalhe_origem,
        detalhe_fonte=detalhe_origem_fonte,
        aviso_match=aviso_match,
    )




from sqlalchemy import text
from sqlalchemy import text

def _buscar_detalhe_omie_por_heuristica(mov):
   

    sql = text("""
        SELECT TOP 2
            o.*
        FROM Integracao.Silver.FatoMovimentacaoFinanceiroOmie o
        WHERE
            o.IDEmpresaProprietaria = :empresa
            AND (
                (o.dDtVenc = :dt_venc) OR (:dt_venc IS NULL AND o.dDtVenc IS NULL)
            )
            AND (
                (o.dDtEmissao = :dt_comp) OR (:dt_comp IS NULL AND o.dDtEmissao IS NULL)
            )
            AND (
                ABS(COALESCE(o.nValLiquido, o.nValorTitulo, 0) - :valor) < 0.01
            )
            AND (
                :categoria IS NULL
                OR :categoria = ''
                OR COALESCE(o.cCodCateg, '') = :categoria
                OR COALESCE(o.Nivel1, '') = :categoria
            )
            AND (
                :ref_titulo IS NULL
                OR :ref_titulo = ''
                OR COALESCE(CAST(o.nCodTitulo AS varchar(300)), '') = :ref_titulo
            )
        ORDER BY
            ISNULL(o.DataHoraCarga, '1900-01-01') DESC,
            ISNULL(o.dDtAlt, '1900-01-01') DESC,
            ISNULL(o.cHrAlt, '00:00:00') DESC
    """)

    empresa = getattr(mov, "IDEmpresaProprietaria", None)
    if empresa is None:
        return None, "Não consegui buscar detalhe Omie porque 'IDEmpresaProprietaria' está NULL na tabela consolidada."

    categoria = (getattr(mov, "Categoria", None) or "").strip()
    categoria_param = categoria if categoria else None

    ref_titulo = (getattr(mov, "nCodTitulo", None) or "").strip()
    ref_titulo_param = ref_titulo if ref_titulo else None

    valor_raw = getattr(mov, "Valor", None)
    try:
        valor_param = float(valor_raw or 0)
    except Exception:
        valor_param = 0.0

    params = {
        "empresa": int(empresa),
        "dt_venc": getattr(mov, "DataVencimento", None),
        "dt_comp": getattr(mov, "DataCompetencia", None),
        "valor": valor_param,
        "categoria": categoria_param,
        "ref_titulo": ref_titulo_param,
    }

    rows = db.session.execute(sql, params).mappings().all()

    if not rows:
        return None, "Não encontrei correspondência na Omie (match por empresa/datas/valor/categoria)."

    if len(rows) > 1:
        return dict(rows[0]), "Atenção: encontrei mais de 1 possível correspondência na Omie; estou exibindo a mais recente."

    return dict(rows[0]), None







def _buscar_detalhe_granatum_por_heuristica(mov):
   

    sql = text("""
        SELECT TOP 2
            g.*
        FROM Integracao.Silver.FatoMovimentoFinanceiroGranatumEuromidia g
        WHERE
            (
                (g.DataVencimento = :dt_venc) OR (:dt_venc IS NULL AND g.DataVencimento IS NULL)
            )
            AND (
                (g.DataCompetencia = :dt_comp) OR (:dt_comp IS NULL AND g.DataCompetencia IS NULL)
            )
            AND (
                ABS(COALESCE(g.Valor, 0) - :valor) < 0.01
            )
            AND (
                :categoria IS NULL
                OR :categoria = ''
                OR COALESCE(g.Categoria, '') = :categoria
            )
            AND (
                :ref IS NULL
                OR :ref = ''
                OR COALESCE(g.Referencia, '') = :ref
            )
        ORDER BY g.IDFatoMovimentoFinanceiroGranatumEuromidia DESC
    """)

    categoria = (getattr(mov, "Categoria", None) or "").strip()
    categoria_param = categoria if categoria else None

    ref = (getattr(mov, "nCodTitulo", None) or "").strip()
    ref_param = ref if ref else None

    params = {
        "dt_venc": getattr(mov, "DataVencimento", None),
        "dt_comp": getattr(mov, "DataCompetencia", None),
        "valor": float(getattr(mov, "Valor", 0) or 0),
        "categoria": categoria_param,
        "ref": ref_param,
    }

    rows = db.session.execute(sql, params).mappings().all()

    if not rows:
        return None, "Não encontrei correspondência no Granatum (match por datas/valor/categoria/referência)."

    if len(rows) > 1:
        return dict(rows[0]), "Atenção: encontrei mais de 1 possível correspondência no Granatum; estou exibindo a mais recente."

    return dict(rows[0]), None






from datetime import datetime, date, timedelta
from sqlalchemy import text



def _parse_int(s: str):
  
    try:
        if s is None:
            return None
        s = str(s).strip()
        if s == "":
            return None
        return int(s)
    except Exception:
        return None





@admin.route("/listadevedores", methods=["GET"])
@admin.route("/ListaDevedores", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def listadevedores():

    empresa_raw = (request.args.get("empresa") or "").strip()

    try:
        page = int(request.args.get("page") or "1")
    except Exception:
        page = 1

    try:
        per_page = int(request.args.get("per_page") or "20")
    except Exception:
        per_page = 20

    per_page = max(5, min(per_page, 100))
    page = max(1, page)

    empresa_id = _parse_int(empresa_raw)


    sql_base = """
SELECT
     d.[nCodTitulo]
    ,d.[nCodOS]
    ,d.[cNumOS]
    ,d.[cOrigemDescricao]
    ,d.[IDEmpresaProprietaria]

 
    ,de.RazaoSocial AS EmpresaRazaoSocial
    ,de.CNPJ  AS EmpresaCNPJ
    ,de.Logo  AS EmpresaLogo

    ,d.[dDtEmissao]
    ,d.[dDtVenc]
    ,d.[dDtPrevisao]
    ,d.[dDtPagamento]
    ,d.[nCodCliente]
    ,d.[cStatus]
    ,d.[cNatureza]
    ,d.[cNaturezaDescricao]
    ,d.[cTipo]
    ,d.[cTipoDescricao]
    ,d.[cNumDocFiscal]
    ,d.[cNumParcela]
    ,d.[nValorTitulo]
    ,d.[nCodNF]
    ,d.[cLiquidado]
    ,d.[nValPago]
    ,d.[nValAberto]
    ,d.[nValLiquido]
    ,d.[EventKey]
    ,d.[DataHoraEvento]
    ,d.[DiasAtraso]
    ,d.[FaixaAtraso]
    ,d.[FaixaAtrasoVisual]
    ,d.[Tipo]
    ,d.[TotalParcelas]
    ,d.[TotalParcelasPagas]
    ,d.[TotalParcelasEmAberto]
    ,d.[TotalParcelasEmAtraso]
    ,d.[TotalParcelasAVencer]
    ,d.[TotalParcelas_R$]
    ,d.[TotalPago_R$]
    ,d.[TotalEmAberto_R$]
    ,d.[TotalEmAtraso_R$]
    ,d.[TotalAVencer_R$]
FROM [Integracao].[Silver].[DimVencimentoAtraso] d
LEFT JOIN [dbo].[EmpresaProprietaria] de
    ON de.IDEmpresaProprietaria = d.IDEmpresaProprietaria
WHERE 1=1
"""

    params = {}

    if empresa_id is not None:
        sql_base += "\n  AND d.IDEmpresaProprietaria = :empresa_id\n"
        params["empresa_id"] = empresa_id


    sql_base += "\n  AND (d.cTipoDescricao IS NULL OR d.cTipoDescricao <> 'Dividendo')\n"

    sql_list = sql_base + """
ORDER BY
    d.DiasAtraso DESC,
    ISNULL(d.nValAberto, 0) DESC,
    d.dDtVenc ASC,
    d.nCodOS DESC,
    d.nCodTitulo DESC
OFFSET :offset ROWS
FETCH NEXT :limit ROWS ONLY
"""

    sql_count = """
SELECT COUNT(1) AS Total
FROM (
""" + sql_base + """
) x
"""

    offset = (page - 1) * per_page
    params_list = dict(params)
    params_list["offset"] = offset
    params_list["limit"] = per_page

    total = 0
    try:
        r_total = db.session.execute(text(sql_count), params).mappings().first()
        total = int(r_total["Total"]) if r_total and r_total.get("Total") is not None else 0
    except Exception:
        total = 0

    rows = db.session.execute(text(sql_list), params_list).mappings().all()

    itens = []
    for r in rows:
        itens.append(
            {
                "nCodTitulo": r.get("nCodTitulo"),
                "nCodOS": r.get("nCodOS"),
                "cNumOS": r.get("cNumOS"),
                "cOrigemDescricao": r.get("cOrigemDescricao"),
                "IDEmpresaProprietaria": r.get("IDEmpresaProprietaria"),

                "EmpresaRazaoSocial": r.get("EmpresaRazaoSocial") or "Empresa",
                "EmpresaCNPJ": r.get("EmpresaCNPJ") or "",
                "EmpresaLogo": (r.get("EmpresaLogo") or "").strip(),

                "dDtEmissao": r.get("dDtEmissao"),
                "dDtVenc": r.get("dDtVenc"),
                "dDtPrevisao": r.get("dDtPrevisao"),
                "dDtPagamento": r.get("dDtPagamento"),

                "nCodCliente": r.get("nCodCliente"),
                "cStatus": r.get("cStatus"),
                "cNatureza": r.get("cNatureza"),
                "cNaturezaDescricao": r.get("cNaturezaDescricao"),

                "cTipo": r.get("cTipo"),
                "cTipoDescricao": r.get("cTipoDescricao"),
                "cNumDocFiscal": r.get("cNumDocFiscal"),
                "cNumParcela": r.get("cNumParcela"),

                "nValorTitulo": r.get("nValorTitulo"),
                "nCodNF": r.get("nCodNF"),
                "cLiquidado": r.get("cLiquidado"),
                "nValPago": r.get("nValPago"),
                "nValAberto": r.get("nValAberto"),
                "nValLiquido": r.get("nValLiquido"),
                "EventKey": r.get("EventKey"),

                "DataHoraEvento": r.get("DataHoraEvento"),
                "DiasAtraso": r.get("DiasAtraso"),
                "FaixaAtraso": r.get("FaixaAtraso"),
                "FaixaAtrasoVisual": r.get("FaixaAtrasoVisual"),
                "Tipo": r.get("Tipo"),

                "TotalParcelas": r.get("TotalParcelas"),
                "TotalParcelasPagas": r.get("TotalParcelasPagas"),
                "TotalParcelasEmAberto": r.get("TotalParcelasEmAberto"),
                "TotalParcelasEmAtraso": r.get("TotalParcelasEmAtraso"),
                "TotalParcelasAVencer": r.get("TotalParcelasAVencer"),

                "TotalParcelas_R$": r.get("TotalParcelas_R$"),
                "TotalPago_R$": r.get("TotalPago_R$"),
                "TotalEmAberto_R$": r.get("TotalEmAberto_R$"),
                "TotalEmAtraso_R$": r.get("TotalEmAtraso_R$"),
                "TotalAVencer_R$": r.get("TotalAVencer_R$"),
            }
        )

    total_pages = max(1, (total + per_page - 1) // per_page)

    empresas_rows = (
        db.session.query(
            DimEmpresaProprietaria.IDEmpresaProprietaria,
            DimEmpresaProprietaria.RazaoSocial,
            DimEmpresaProprietaria.Logo,
            DimEmpresaProprietaria.CNPJ,
        )
        .filter(
            DimEmpresaProprietaria.IDEmpresaProprietaria != None,
            func.coalesce(DimEmpresaProprietaria.RazaoSocial, "") != "",
        )
        .order_by(DimEmpresaProprietaria.RazaoSocial.asc())
        .all()
    )

    empresas = []
    for x in empresas_rows:
        empresas.append(
            {
                "id": x[0],
                "razao": x[1] or "Empresa",
                "logo": (x[2] or "").strip(),
                "cnpj": x[3] or "",
            }
        )

    return render_template(
        "admin/lista_devedores.html",
        itens=itens,
        empresas=empresas,
        filtros={
            "empresa": empresa_id if empresa_id is not None else "",
            "per_page": per_page,
        },
        paginacao={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "inicio": (page - 1) * per_page + 1 if total > 0 else 0,
            "fim": min(page * per_page, total),
        },
    )




"""Integração AUVO"""


@admin.route("/auvo/produtos", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def lista_produtos_auvo():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    q = (request.args.get("q") or "").strip()

    if per_page not in (10, 20, 30, 50, 100):
        per_page = 20
    if page < 1:
        page = 1

    offset = (page - 1) * per_page

    sql_total = text("""
        SELECT COUNT(1)
        FROM [Integracao].[Silver].[DimProdutoAuvo] AS a
        WHERE
            (:q = '')
            OR (
                (a.Nome IS NOT NULL AND a.Nome LIKE '%' + :q + '%')
                OR (a.IDDimProduto IS NOT NULL AND CAST(a.IDDimProduto AS varchar(50)) LIKE '%' + :q + '%')
            )
    """)
    total = int(db.session.execute(sql_total, {"q": q}).scalar() or 0)

    sql_itens = text("""
        SELECT
            a.IDDimProdutoAuvo,
            a.IDDimProduto,
            a.Nome,
            a.UnitarioValor,
            a.CriadoEm,
            a.BitAtivo,
            dp.NomeProduto AS NomeProdutoVinculado
        FROM [Integracao].[Silver].[DimProdutoAuvo] AS a
        LEFT JOIN [Integracao].[Silver].[DimProduto] AS dp
            ON dp.IDDimProduto = a.IDDimProduto
        WHERE
            (:q = '')
            OR (
                (a.Nome IS NOT NULL AND a.Nome LIKE '%' + :q + '%')
                OR (a.IDDimProduto IS NOT NULL AND CAST(a.IDDimProduto AS varchar(50)) LIKE '%' + :q + '%')
            )
        ORDER BY a.IDDimProdutoAuvo DESC
        OFFSET :offset ROWS
        FETCH NEXT :per_page ROWS ONLY
    """)
    itens = db.session.execute(sql_itens, {"q": q, "offset": offset, "per_page": per_page}).fetchall()

    sql_produtos = text("""
        SELECT TOP (1000)
            p.IDDimProduto,
            p.NomeProduto
        FROM [Integracao].[Silver].[DimProduto] AS p
        WHERE ISNULL(p.BitAtivo, 1) = 1
        ORDER BY p.NomeProduto ASC
    """)
    produtos = db.session.execute(sql_produtos).fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    inicio = 0 if total == 0 else (offset + 1)
    fim = min(offset + per_page, total)

    paginacao = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "inicio": inicio,
        "fim": fim,
    }
    filtros = {"q": q, "per_page": per_page}

    return render_template(
        "admin/lista_produtos_auvo.html",
        itens=itens,
        produtos=produtos,
        filtros=filtros,
        paginacao=paginacao,
    )


@admin.route("/auvo/produtos/<int:id_dim_produto_auvo>/vincular", methods=["POST"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
@limiter.limit("120 per minute", methods=["POST"])
def produto_auvo_vincular_dimproduto(id_dim_produto_auvo: int):
 
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    q = (request.args.get("q") or "").strip()

 
    valor_bruto = (request.form.get("id_dim_produto_input") or "").strip()

 
    id_str = valor_bruto.split("|", 1)[0].strip()
    try:
        id_dim_produto = int(id_str)
    except Exception:
      
        return redirect(url_for("admin.lista_produtos_auvo", page=page, per_page=per_page, q=q))

 
    sql_existe = text("""
        SELECT TOP 1 IDDimProduto
        FROM [Integracao].[Silver].[DimProduto]
        WHERE IDDimProduto = :id
    """)
    existe = db.session.execute(sql_existe, {"id": id_dim_produto}).scalar()
    if not existe:
        return redirect(url_for("admin.lista_produtos_auvo", page=page, per_page=per_page, q=q))

  
    sql_upd = text("""
        UPDATE [Integracao].[Silver].[DimProdutoAuvo]
        SET IDDimProduto = :id_dim_produto
        WHERE IDDimProdutoAuvo = :id_dim_produto_auvo
    """)
    db.session.execute(sql_upd, {
        "id_dim_produto": id_dim_produto,
        "id_dim_produto_auvo": id_dim_produto_auvo
    })
    db.session.commit()

    return redirect(url_for("admin.lista_produtos_auvo", page=page, per_page=per_page, q=q))




"""Integrar Ordem Auvo"""




import re
from typing import Any

_RE_ID_INICIO = re.compile(r"^\s*(\d+)\s*(\|.*)?$")


def extrair_id_do_select_digitavel(valor: str | None) -> int | None:
 
    v = (valor or "").strip()
    if not v:
        return None
    m = _RE_ID_INICIO.match(v)
    if not m:
        return None
    return int(m.group(1))


def carregar_listas_para_template() -> dict[str, list[dict[str, Any]]]:
   

    empresas = db.session.execute(text("""
        SELECT
            IDEmpresa,
            RazaoSocial,
            NomeFantasia
        FROM [Integracao].[Silver].[DimEmpresas]
        ORDER BY RazaoSocial
    """)).mappings().all()

    projetos = db.session.execute(text("""
        SELECT
            IDDimProjeto,
            NomeProjeto,
            IDEmpresa,
            BitAtivo
        FROM [Integracao].[Silver].[DimProjeto]
        WHERE BitAtivo = 1
        ORDER BY NomeProjeto
    """)).mappings().all()


    contratos = db.session.execute(text("""
        SELECT
            IDDimContrato,
            NomeContrato,
            IDEmpresa,
            BitAtivo
        FROM [Integracao].[Silver].[DimContrato]
        WHERE BitAtivo = 1
        ORDER BY NomeContrato
    """)).mappings().all()

    produtos = db.session.execute(text("""
        SELECT
            IDDimProdutoAuvo,
            Nome,
            Codigo,
            BitAtivo
        FROM [Integracao].[Silver].[DimProdutoAuvo]
        WHERE BitAtivo = 1
        ORDER BY Nome
    """)).mappings().all()

    return {
        "empresas": [dict(x) for x in empresas],
        "projetos": [dict(x) for x in projetos],
        "contratos": [dict(x) for x in contratos],
        "produtos": [dict(x) for x in produtos],
    }



@admin.route("/api/os_auvo/proximo_id", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def api_os_auvo_proximo_id():
   
    row = db.session.execute(text("""
        SELECT
            CAST(ISNULL(ic.last_value, 0) AS BIGINT) AS last_value
        FROM sys.identity_columns ic
        WHERE ic.[object_id] = OBJECT_ID('dbo.FatoOrdemServicoAuvo')
          AND ic.[name] = 'IDFatoOrdemServicoAuvo'
    """)).mappings().first()

    last_val = int(row["last_value"]) if row and row.get("last_value") is not None else 0
    return jsonify({"proximo_id_estimado": last_val + 1})




@admin.route("/criar_os_auvo", methods=["GET", "POST"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def criar_os_auvo():
    
    if request.method == "GET":
        listas = carregar_listas_para_template()
        return render_template("admin/criar_os_auvo.html", listas=listas)

    payload = request.get_json(silent=True) or {}
    ordens = payload.get("ordens") or []

    if not isinstance(ordens, list) or len(ordens) == 0:
        return jsonify({"ok": False, "erro": "Nenhuma OS recebida."}), 400

    ids_criados: list[int] = []

    try:
        with db.session.begin():
            for idx, ordem in enumerate(ordens, start=1):
                empresa_input = (ordem.get("empresa_input") or "").strip()
                projeto_input = (ordem.get("projeto_input") or "").strip()
                contrato_input = (ordem.get("contrato_input") or "").strip()

                id_empresa = extrair_id_do_select_digitavel(empresa_input)
                id_projeto = extrair_id_do_select_digitavel(projeto_input)
                id_contrato = extrair_id_do_select_digitavel(contrato_input)

                endereco = (ordem.get("endereco") or "").strip()
                observacoes = (ordem.get("observacoes") or "").strip()
                data_servico = (ordem.get("data_servico") or "").strip()

                itens = ordem.get("itens") or []

                if not id_empresa:
                    raise ValueError(f"OS {idx}: Empresa inválida.")
                if not id_projeto:
                    raise ValueError(f"OS {idx}: Projeto inválido.")
                if not id_contrato:
                    raise ValueError(f"OS {idx}: Contrato inválido.")
                if not data_servico:
                    raise ValueError(f"OS {idx}: Data do serviço vazia.")
                if not isinstance(itens, list) or len(itens) == 0:
                    raise ValueError(f"OS {idx}: Adicione ao menos 1 item.")

                # ✅ (recomendado) valida Projeto pertence à Empresa
                chk_proj = db.session.execute(text("""
                    SELECT 1
                    FROM [Integracao].[Silver].[DimProjeto]
                    WHERE IDDimProjeto = :id_projeto
                      AND IDEmpresa = :id_empresa
                """), {"id_projeto": id_projeto, "id_empresa": id_empresa}).first()
                if not chk_proj:
                    raise ValueError(f"OS {idx}: Projeto não pertence à empresa selecionada.")

                # ✅ (recomendado) valida Contrato pertence à Empresa
                chk_ctr = db.session.execute(text("""
                    SELECT 1
                    FROM [Integracao].[Silver].[DimContrato]
                    WHERE IDDimContrato = :id_contrato
                      AND IDEmpresa = :id_empresa
                """), {"id_contrato": id_contrato, "id_empresa": id_empresa}).first()
                if not chk_ctr:
                    raise ValueError(f"OS {idx}: Contrato não pertence à empresa selecionada.")

                # ✅ Insert OS e pega ID real gerado (IDENTITY)
                row_os = db.session.execute(text("""
                    INSERT INTO dbo.FatoOrdemServicoAuvo
                        (IDEmpresa, IDDimProjeto, IDDimContrato, Endereco, Observacoes, DataServico)
                    OUTPUT INSERTED.IDFatoOrdemServicoAuvo AS id_os
                    VALUES
                        (:id_empresa, :id_projeto, :id_contrato, :endereco, :observacoes, :data_servico)
                """), {
                    "id_empresa": id_empresa,
                    "id_projeto": id_projeto,
                    "id_contrato": id_contrato,
                    "endereco": endereco if endereco else None,
                    "observacoes": observacoes if observacoes else None,
                    "data_servico": data_servico,
                }).mappings().first()

                if not row_os or not row_os.get("id_os"):
                    raise RuntimeError(f"OS {idx}: Falha ao gerar ID da OS (verifique IDENTITY).")

                id_os = int(row_os["id_os"])
                ids_criados.append(id_os)

                # ✅ Insert itens (amarrados no ID da OS)
                for jdx, item in enumerate(itens, start=1):
                    produto_input = (item.get("produto_input") or "").strip()
                    id_produto_auvo = extrair_id_do_select_digitavel(produto_input)

                    qtd_raw = item.get("quantidade")
                    try:
                        qtd = int(qtd_raw)
                    except Exception:
                        qtd = 0

                    if not id_produto_auvo:
                        raise ValueError(f"OS {idx} / Item {jdx}: Produto inválido.")
                    if qtd <= 0:
                        raise ValueError(f"OS {idx} / Item {jdx}: Quantidade inválida.")

                    nome_prod = db.session.execute(text("""
                        SELECT TOP 1 Nome
                        FROM [Integracao].[Silver].[DimProdutoAuvo]
                        WHERE IDDimProdutoAuvo = :id_prod
                    """), {"id_prod": id_produto_auvo}).scalar()

                    if not nome_prod:
                        raise ValueError(f"OS {idx} / Item {jdx}: Produto não encontrado no DimProdutoAuvo.")

                    db.session.execute(text("""
                        INSERT INTO dbo.FatoOrdemServicoItensAuvo
                            (IDDimProdutoAuvo, IDFatoOrdemServicoAuvo, NomeProduto, Quantidade)
                        VALUES
                            (:id_prod, :id_os, :nome, :qtd)
                    """), {
                        "id_prod": id_produto_auvo,
                        "id_os": id_os,
                        "nome": nome_prod,
                        "qtd": qtd,
                    })

        return jsonify({"ok": True, "ids_criados": ids_criados})

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "erro": str(e)}), 400








@admin.route("/tickets/auvo", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def lista_tickets_auvo():
    

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    prioridade = (request.args.get("prioridade") or "").strip()
    tipo = (request.args.get("tipo") or "").strip()

    try:
        page = int(request.args.get("page") or "1")
    except:
        page = 1

    try:
        per_page = int(request.args.get("per_page") or "20")
    except:
        per_page = 20

    per_page = max(5, min(per_page, 100))
    page = max(1, page)

    filtros_sql = []
    params = {}

    if status:
        filtros_sql.append("ft.StatusDescricao = :status")
        params["status"] = status

    if prioridade:
        filtros_sql.append("ft.Prioridade = :prioridade")
        params["prioridade"] = prioridade

    if tipo:
        filtros_sql.append("ft.TipoSolicitacao = :tipo")
        params["tipo"] = tipo

    if q:
        filtros_sql.append("""
            (
                CAST(ft.IdTicketAuvo AS varchar(50)) LIKE :q_like
                OR ISNULL(ft.Titulo,'') LIKE :q_like
                OR ISNULL(ft.NomeCliente,'') LIKE :q_like
            )
        """)
        params["q_like"] = f"%{q}%"

    where_sql = " AND ".join([f"({x})" for x in filtros_sql]) if filtros_sql else "1=1"


    sql_total = text(f"""
        SELECT COUNT(1) AS total
        FROM [Integracao].[Silver].[FatoTicketsAuvo] AS ft
        WHERE {where_sql}
    """)
    row_total = db.session.execute(sql_total, params).fetchone()
    total = int(row_total[0] or 0) if row_total else 0

    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    inicio = (page - 1) * per_page + 1 if total > 0 else 0
    fim = min(page * per_page, total)

    start_rn = (page - 1) * per_page + 1
    end_rn = page * per_page

    params_page = dict(params)
    params_page.update({"start_rn": start_rn, "end_rn": end_rn})


    sql_rows = text(f"""
        ;WITH ranked AS (
            SELECT
                ft.IdTicketAuvo,
                ft.DataCriacao,
                ft.DataUltimaAtualizacao,
                ft.Titulo,
                ft.NomeCliente,
                ft.TipoSolicitacao,
                ft.Prioridade,
                ft.StatusDescricao,
                ft.DataEncerramento,

                ROW_NUMBER() OVER (
                    ORDER BY
                        ISNULL(ft.DataUltimaAtualizacao, ft.DataCriacao) DESC,
                        ISNULL(ft.IdTicketAuvo, 0) DESC
                ) AS rn
            FROM [Integracao].[Silver].[FatoTicketsAuvo] AS ft
            WHERE {where_sql}
        )
        SELECT
            IdTicketAuvo,
            DataCriacao,
            DataUltimaAtualizacao,
            Titulo,
            NomeCliente,
            TipoSolicitacao,
            Prioridade,
            StatusDescricao,
            DataEncerramento
        FROM ranked
        WHERE rn BETWEEN :start_rn AND :end_rn
        ORDER BY rn
    """)

    rows = db.session.execute(sql_rows, params_page).mappings().all()

    itens = []
    for r in rows:
        id_ticket = r.get("IdTicketAuvo")

        itens.append({
            "IdTicketAuvo": id_ticket,
            "DataCriacao": r.get("DataCriacao"),
            "DataUltimaAtualizacao": r.get("DataUltimaAtualizacao"),
            "Titulo": (r.get("Titulo") or "").strip(),
            "NomeCliente": (r.get("NomeCliente") or "").strip(),
            "TipoSolicitacao": (r.get("TipoSolicitacao") or "").strip(),
            "Prioridade": (r.get("Prioridade") or "").strip(),
            "StatusDescricao": (r.get("StatusDescricao") or "").strip(),
            "DataEncerramento": r.get("DataEncerramento"),

            
            "url_detalhe": url_for("admin.ticket_auvo_detalhes", id_ticket=int(id_ticket)),
        })

  
    sql_status = text("""
        SELECT DISTINCT StatusDescricao
        FROM [Integracao].[Silver].[FatoTicketsAuvo]
        WHERE StatusDescricao IS NOT NULL AND LTRIM(RTRIM(StatusDescricao)) <> ''
        ORDER BY StatusDescricao ASC
    """)
    status_opcoes = [x[0] for x in db.session.execute(sql_status).fetchall()]

    sql_prioridade = text("""
        SELECT DISTINCT Prioridade
        FROM [Integracao].[Silver].[FatoTicketsAuvo]
        WHERE Prioridade IS NOT NULL AND LTRIM(RTRIM(Prioridade)) <> ''
        ORDER BY Prioridade ASC
    """)
    prioridade_opcoes = [x[0] for x in db.session.execute(sql_prioridade).fetchall()]

    sql_tipo = text("""
        SELECT DISTINCT TipoSolicitacao
        FROM [Integracao].[Silver].[FatoTicketsAuvo]
        WHERE TipoSolicitacao IS NOT NULL AND LTRIM(RTRIM(TipoSolicitacao)) <> ''
        ORDER BY TipoSolicitacao ASC
    """)
    tipo_opcoes = [x[0] for x in db.session.execute(sql_tipo).fetchall()]

    return render_template(
        "admin/tickets_auvo_lista.html",
        itens=itens,
        status_opcoes=status_opcoes,
        prioridade_opcoes=prioridade_opcoes,
        tipo_opcoes=tipo_opcoes,
        filtros={
            "q": q,
            "status": status,
            "prioridade": prioridade,
            "tipo": tipo,
            "per_page": per_page,
        },
        paginacao={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "inicio": inicio,
            "fim": fim,
        },
    )









@admin.route("/tickets/auvo/<int:id_ticket>", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def ticket_auvo_detalhes(id_ticket: int):
    """ticket_auvo_detalhes
    - Eu busco o ticket pelo IdTicketAuvo
    - Eu busco o histórico de tasks ligadas ao ticket (FatoTicketsAuvoTask -> FatoTaskAuvo)
    - Eu monto dicts simples para o Jinja renderizar sem dor
    """

    sql_ticket = text("""
        SELECT TOP 1
            ft.IdTicketAuvo,
            ft.DataCriacao,
            ft.DataUltimaAtualizacao,
            ft.Titulo,
            ft.Descricao,

            ft.StatusDescricao,
            ft.StatusTipo,
            ft.DataStatus,
            ft.DataEncerramento,

            ft.Prioridade,
            ft.TipoSolicitacao,
            ft.Sla,

            ft.NomeEquipe,
            ft.NomeUsuarioCriador,
            ft.NomeUsuarioResponsavel,

            ft.NomeCliente,
            ft.EmailCliente,
            ft.TelefoneCliente,

            ft.NomeSolicitante,
            ft.EmailSolicitante
        FROM [Integracao].[Silver].[FatoTicketsAuvo] AS ft
        WHERE ft.IdTicketAuvo = :id_ticket
    """)

    ticket = db.session.execute(sql_ticket, {"id_ticket": id_ticket}).mappings().first()
    if not ticket:
        abort(404)

    sql_tasks = text("""
        SELECT
            t.IdTarefaAuvo,
            d.IDFatoTaskAuvo,
            d.DescricaoTipoTarefa,
            d.StatusTarefa,
            d.Finalizada,
            d.DataCriacao,
            d.DataTarefa,
            d.DataUltimaAtualizacao,

            d.NomeUsuarioDe,
            d.NomeUsuarioPara,

            d.FezCheckIn,
            d.DataCheckIn,
            d.FezCheckOut,
            d.DataCheckOut,

            d.Endereco,
            d.Relatorio,
            d.UrlTarefa,
            d.Pendencia
        FROM [Integracao].[Silver].[FatoTicketsAuvoTask] AS t
        LEFT JOIN [Integracao].[Silver].[FatoTaskAuvo] AS d
            ON d.IdTarefaAuvo = t.IdTarefaAuvo
        WHERE t.IdTicketAuvo = :id_ticket
        ORDER BY
            ISNULL(d.DataTarefa, d.DataCriacao) DESC,
            t.IdTarefaAuvo DESC
    """)

    rows_tasks = db.session.execute(sql_tasks, {"id_ticket": id_ticket}).mappings().all()

    tasks = []
    for r in rows_tasks:
        tasks.append({
            "IdTarefaAuvo": r.get("IdTarefaAuvo"),
            "DescricaoTipoTarefa": (r.get("DescricaoTipoTarefa") or "").strip(),
            "StatusTarefa": r.get("StatusTarefa"),
            "Finalizada": r.get("Finalizada"),
            "DataCriacao": r.get("DataCriacao"),
            "DataTarefa": r.get("DataTarefa"),
            "DataUltimaAtualizacao": r.get("DataUltimaAtualizacao"),
            "NomeUsuarioDe": (r.get("NomeUsuarioDe") or "").strip(),
            "NomeUsuarioPara": (r.get("NomeUsuarioPara") or "").strip(),
            "FezCheckIn": r.get("FezCheckIn"),
            "DataCheckIn": r.get("DataCheckIn"),
            "FezCheckOut": r.get("FezCheckOut"),
            "DataCheckOut": r.get("DataCheckOut"),
            "Endereco": (r.get("Endereco") or "").strip(),
            "Relatorio": (r.get("Relatorio") or "").strip(),
            "UrlTarefa": (r.get("UrlTarefa") or "").strip(),
            "Pendencia": (r.get("Pendencia") or "").strip(),
        })

    return render_template(
        "admin/ticket_auvo_detalhes.html",
        ticket=ticket,
        tasks=tasks,
    )




"""ATIVOS"""





@admin.route("/ativos", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def ativos():
    termo = (request.args.get("q") or "").strip()
    termo_limpo = termo[:120]
    # A página carrega “vazia”; KPIs e séries vêm via AJAX quando selecionar o ativo.
    return render_template("admin/ativos.html", q=termo_limpo)


# -----------------------------------------------------------------------------
# /ativos/sugestoes (autocomplete)
# -----------------------------------------------------------------------------
@admin.route("/ativos/sugestoes", methods=["GET"])
@login_required
def ativos_sugestoes():
    termo = (request.args.get("q") or "").strip()
    termo_limpo = termo[:80]
    if len(termo_limpo) < 2:
        return jsonify([])

    sql = """
    SET NOCOUNT ON;

    DECLARE @q NVARCHAR(200) = :q;
    DECLARE @q_like NVARCHAR(210) = N'%' + @q + N'%';
    DECLARE @q_prefix NVARCHAR(210) = @q + N'%';

    ;WITH base AS (
        SELECT TOP (30)
            IDDimAtivos,
            NomeAtivo,
            Tipo,
            SubTipo,
            Descricao,
            CodPonto,
            NumeroSerie,
            ReferenciaExterna,
            Cidade,
            UF,
            Bairro,
            Logradouro,
            CEP,
            BitAtivo
        FROM Integracao.Silver.DimAtivos WITH (NOLOCK)
        WHERE
            (
                NomeAtivo   COLLATE Latin1_General_CI_AI LIKE @q_like OR
                CAST(ReferenciaExterna AS NVARCHAR(50))          LIKE @q_like OR
                NumeroSerie  COLLATE Latin1_General_CI_AI LIKE @q_like OR
                Bairro   COLLATE Latin1_General_CI_AI LIKE @q_like OR
                Logradouro   COLLATE Latin1_General_CI_AI LIKE @q_like OR
                Cidade  COLLATE Latin1_General_CI_AI LIKE @q_like OR
                CEP                                LIKE @q_like OR
                UF   COLLATE Latin1_General_CI_AI LIKE @q_like
            )
    )
    SELECT *
    FROM base
    ORDER BY
        CASE
            WHEN NomeAtivo COLLATE Latin1_General_CI_AI = @q THEN 0
            WHEN NumeroSerie COLLATE Latin1_General_CI_AI = @q THEN 0
            WHEN CAST(ReferenciaExterna AS NVARCHAR(50)) = @q THEN 0
            WHEN NomeAtivo COLLATE Latin1_General_CI_AI LIKE @q_prefix THEN 1
            WHEN NumeroSerie COLLATE Latin1_General_CI_AI LIKE @q_prefix THEN 1
            WHEN Cidade COLLATE Latin1_General_CI_AI LIKE @q_prefix THEN 2
            WHEN Bairro COLLATE Latin1_General_CI_AI LIKE @q_prefix THEN 2
            ELSE 3
        END,
        ISNULL(NomeAtivo, N'') ASC,
        IDDimAtivos DESC;
    """

    with db.engine.connect() as conn:
        rs = conn.execute(text(sql), {"q": termo_limpo})
        colunas = list(rs.keys())

        itens = []
        for row in (rs.fetchall() or []):
            d = dict(zip(colunas, row))

            nome = (d.get("NomeAtivo") or "").strip()
            tipo = (d.get("Tipo") or "").strip()
            subtipo = (d.get("SubTipo") or "").strip()
            descricao = (d.get("Descricao") or "").strip()
            cidade = (d.get("Cidade") or "").strip()
            uf = (d.get("UF") or "").strip()
            ns = (d.get("NumeroSerie") or "").strip()
            refext = d.get("ReferenciaExterna")
            codponto = d.get("CodPonto")
            bit_ativo = d.get("BitAtivo")

            if descricao and len(descricao) > 90:
                descricao_curta = descricao[:90].rstrip() + "…"
            else:
                descricao_curta = descricao

            label = nome or (f"CodPonto {codponto}" if codponto else f"Ativo {d.get('IDDimAtivos')}")

            pedacos = []
            if tipo:
                pedacos.append(tipo)
            if subtipo:
                pedacos.append(subtipo)
            if descricao_curta:
                pedacos.append(descricao_curta)
            if ns:
                pedacos.append(f"NS: {ns}")
            if refext is not None:
                pedacos.append(f"RefExt: {refext}")
            if codponto is not None:
                pedacos.append(f"CodPonto: {codponto}")
            if cidade or uf:
                pedacos.append(f"{cidade}/{uf}".strip("/"))
            if bit_ativo is not None:
                pedacos.append("ATIVO" if bit_ativo else "INATIVO")

            itens.append(
                {
                    "id": d.get("IDDimAtivos"),
                    "label": label,
                    "sublabel": " • ".join([p for p in pedacos if p]),
                    "valor": label,
                    "tipo": tipo,
                    "subtipo": subtipo,
                }
            )

    return jsonify(itens)




@admin.route("/ativos/detalhe", methods=["GET"])
@login_required
def ativos_detalhe():
    id_str = (request.args.get("id") or "").strip()
    if not id_str.isdigit():
        return jsonify({"ok": False, "msg": "id inválido"}), 400

   
    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()

    sql_ativo = """
    SET NOCOUNT ON;

    DECLARE @id INT = :id;

    SELECT TOP (1)
        IDDimAtivos,
        ReferenciaExterna,
        CodPonto,
        IDDimProduto,
        Tipo,
        SubTipo,
        NomeAtivo,
        Descricao,
        NumeroSerie,
        CEP,
        Cidade,
        UF,
        Logradouro,
        Bairro,
        BitAtivo,
        DataAtualizacao
    FROM Integracao.Silver.DimAtivos WITH (NOLOCK)
    WHERE IDDimAtivos = @id;
    """

    sql_kpis_periodo = """
    SET NOCOUNT ON;

    DECLARE @id INT = :id;

  
    DECLARE @dt_fim_in DATE = TRY_CONVERT(date, :dt_fim);
    DECLARE @dt_ini_in DATE = TRY_CONVERT(date, :dt_ini);

    DECLARE @dt_fim DATE;
    DECLARE @dt_ini DATE;

    IF @dt_fim_in IS NULL
        SET @dt_fim = EOMONTH(CONVERT(date, GETDATE()));
    ELSE
        SET @dt_fim = EOMONTH(@dt_fim_in);

    IF @dt_ini_in IS NULL
        SET @dt_ini = DATEADD(MONTH, -11, DATEFROMPARTS(YEAR(@dt_fim), MONTH(@dt_fim), 1));
    ELSE
        SET @dt_ini = DATEFROMPARTS(YEAR(@dt_ini_in), MONTH(@dt_ini_in), 1);

    IF @dt_fim < @dt_ini
    BEGIN
        SELECT
            CAST(0.0 AS decimal(18,2)) AS receita_brl,
            CAST(0.0 AS decimal(18,6)) AS ocupacao_pct,
            CAST(0.0 AS decimal(18,6)) AS rentabilidade_pct;
        RETURN;
    END;

 
    DECLARE @CodPonto INT;

    SELECT TOP (1)
        @CodPonto = COALESCE(
            a.CodPonto,
            TRY_CONVERT(INT, NULLIF(LTRIM(RTRIM(CAST(a.ReferenciaExterna AS NVARCHAR(80)))), ''))
        )
    FROM Integracao.Silver.DimAtivos a WITH (NOLOCK)
    WHERE a.IDDimAtivos = @id;

    IF @CodPonto IS NULL
    BEGIN
        SELECT
            CAST(0.0 AS decimal(18,2)) AS receita_brl,
            CAST(0.0 AS decimal(18,6)) AS ocupacao_pct,
            CAST(0.0 AS decimal(18,6)) AS rentabilidade_pct;
        RETURN;
    END;

  
    ;WITH meses_ref AS (
        SELECT DATEFROMPARTS(c.Ano, c.Mes, 1) AS MesRef
        FROM Integracao.Silver.DimCalendario c WITH (NOLOCK)
        WHERE c.[Data] >= @dt_ini AND c.[Data] <= @dt_fim
        GROUP BY c.Ano, c.Mes
    ),
    contratos_base AS (
        SELECT
            f.CodPonto,
            DATEFROMPARTS(YEAR(f.DataInicioPrevisto),  MONTH(f.DataInicioPrevisto),  1) AS IniMes,
            DATEFROMPARTS(YEAR(f.DataTerminoPrevisto), MONTH(f.DataTerminoPrevisto), 1) AS FimMes,
            TRY_CONVERT(DECIMAL(18,2), f.TotalLiquidoContratoAGBRCTACORDO) AS TotalContrato
        FROM Integracao.Silver.FatoControleContratosItensEuromidia f WITH (NOLOCK)
        WHERE
            f.CodPonto = @CodPonto
            AND f.DataInicioPrevisto IS NOT NULL
            AND f.DataTerminoPrevisto IS NOT NULL
            AND f.DataTerminoPrevisto >= f.DataInicioPrevisto
            AND TRY_CONVERT(DECIMAL(18,2), f.TotalLiquidoContratoAGBRCTACORDO) IS NOT NULL
            AND ISNULL(TRY_CONVERT(INT, f.AtivoCancelamento), 0) = 0
            AND f.DataTerminoPrevisto >= @dt_ini
            AND f.DataInicioPrevisto  <= @dt_fim
    ),
    contratos_norm AS (
        SELECT
            CodPonto,
            CASE WHEN IniMes < @dt_ini THEN @dt_ini ELSE IniMes END AS IniMes2,
            CASE WHEN FimMes > DATEFROMPARTS(YEAR(@dt_fim), MONTH(@dt_fim), 1)
                 THEN DATEFROMPARTS(YEAR(@dt_fim), MONTH(@dt_fim), 1)
                 ELSE FimMes
            END AS FimMes2,
            TotalContrato
        FROM contratos_base
    ),
    contratos_exp AS (
        SELECT
            CodPonto,
            IniMes2,
            FimMes2,
            TotalContrato,
            DATEDIFF(MONTH, IniMes2, FimMes2) + 1 AS QtMeses
        FROM contratos_norm
        WHERE FimMes2 >= IniMes2
    ),
    receita_m AS (
        SELECT
            mr.MesRef,
            COALESCE(SUM(CASE WHEN e.QtMeses > 0 THEN (e.TotalContrato / e.QtMeses) ELSE 0 END), 0) AS Receita
        FROM meses_ref mr
        LEFT JOIN contratos_exp e
            ON mr.MesRef >= e.IniMes2
           AND mr.MesRef <= e.FimMes2
        GROUP BY mr.MesRef
    ),

 
    qtd_faces AS (
        SELECT TOP (1)
            TRY_CONVERT(int, p.QuantidadeFaces) AS QuantidadeFaces
        FROM Integracao.Silver.DimPaineisEuromidia p WITH (NOLOCK)
        WHERE TRY_CONVERT(int, p.CodPonto) = @CodPonto
        ORDER BY p.IDDimPaineisEuromidia DESC
    ),
    custo_face_m AS (
        SELECT
            mr.MesRef,
            CAST(
                COALESCE((
                    SELECT TOP (1)
                        TRY_CONVERT(decimal(18,6), cmp.ValorMensal)
                    FROM Integracao.Silver.DimCustoMensalPainel cmp WITH (NOLOCK)
                    WHERE cmp.CodPonto = @CodPonto
                      AND ((cmp.Ano * 100) + cmp.Mes) <= ((YEAR(mr.MesRef) * 100) + MONTH(mr.MesRef))
                    ORDER BY ((cmp.Ano * 100) + cmp.Mes) DESC
                ), 0.0)
                /
                NULLIF(CAST(ISNULL((SELECT TOP (1) QuantidadeFaces FROM qtd_faces), 1) AS decimal(18,6)), 0)
                AS decimal(18,6)
            ) AS CustoPorFace
        FROM meses_ref mr
    ),


    kp AS (
        SELECT TOP (1)
            UPPER(LTRIM(RTRIM(COALESCE(p.Tipo,'')))) AS TipoPainel,
            TRY_CONVERT(int, p.QuantidadeFaces)      AS QuantidadeFaces
        FROM Integracao.Silver.DimPaineisEuromidia p WITH (NOLOCK)
        WHERE TRY_CONVERT(int, p.CodPonto) = @CodPonto
        ORDER BY p.IDDimPaineisEuromidia DESC
    ),
    k_calc AS (
        SELECT
            CASE WHEN TipoPainel LIKE '%DIGITAL%' THEN 1 ELSE 0 END AS EhDigital,
            CASE
                WHEN TipoPainel LIKE '%DIGITAL%' THEN ISNULL(NULLIF(QuantidadeFaces, 0), 16)
                ELSE 1
            END AS K_fisico
        FROM kp
    ),
    base_ocup AS (
        SELECT
            TRY_CONVERT(int, ftci.CodPonto) AS CodPonto,
            TRY_CONVERT(int, ftci.Cota) AS Cota,
            TRY_CONVERT(date, ftci.DataInicioPrevisto)  AS DtIni,
            TRY_CONVERT(date, ftci.DataTerminoPrevisto) AS DtFim
        FROM Integracao.Silver.FatoControleContratosItensEuromidia ftci WITH (NOLOCK)
        WHERE
            TRY_CONVERT(int, ftci.CodPonto) = @CodPonto
            AND ftci.DataInicioPrevisto IS NOT NULL
            AND ftci.DataTerminoPrevisto IS NOT NULL
            AND TRY_CONVERT(date, ftci.DataInicioPrevisto) <= TRY_CONVERT(date, ftci.DataTerminoPrevisto)
            AND TRY_CONVERT(date, ftci.DataTerminoPrevisto) >= @dt_ini
            AND TRY_CONVERT(date, ftci.DataInicioPrevisto)  <= @dt_fim
            AND ISNULL(TRY_CONVERT(INT, ftci.AtivoCancelamento), 0) = 0
    ),
    contratos_ocup AS (
        SELECT
            b.CodPonto,
            CASE WHEN b.DtIni < @dt_ini THEN @dt_ini ELSE b.DtIni END AS DtIni2,
            CASE WHEN b.DtFim > @dt_fim THEN @dt_fim ELSE b.DtFim END AS DtFim2,
            CAST(
                CASE
                    WHEN (SELECT TOP 1 EhDigital FROM k_calc) = 1 THEN
                        CASE
                            WHEN b.Cota IS NULL OR b.Cota <= 0 THEN 0.0
                            ELSE 1080.0 / CAST(b.Cota AS float)
                        END
                    ELSE 1.0
                END
                AS float
            ) AS SlotsContrato
        FROM base_ocup b
        WHERE (CASE WHEN b.DtFim > @dt_fim THEN @dt_fim ELSE b.DtFim END)
            >= (CASE WHEN b.DtIni < @dt_ini THEN @dt_ini ELSE b.DtIni END)
    ),
    eventos AS (
        SELECT
            e.CodPonto,
            e.DiaEvento,
            SUM(e.DeltaSlots) AS DeltaSlots
        FROM (
            SELECT CodPonto, DtIni2 AS DiaEvento,  SlotsContrato AS DeltaSlots FROM contratos_ocup
            UNION ALL
            SELECT CodPonto, DATEADD(DAY, 1, DtFim2) AS DiaEvento, -SlotsContrato AS DeltaSlots FROM contratos_ocup
            UNION ALL
            SELECT @CodPonto, @dt_ini AS DiaEvento, 0.0 AS DeltaSlots
            UNION ALL
            SELECT @CodPonto, DATEADD(DAY, 1, @dt_fim) AS DiaEvento, 0.0 AS DeltaSlots
        ) e
        GROUP BY e.CodPonto, e.DiaEvento
    ),
    eventos_ordenados AS (
        SELECT
            e.CodPonto,
            e.DiaEvento,
            SUM(e.DeltaSlots) OVER (
                PARTITION BY e.CodPonto
                ORDER BY e.DiaEvento
                ROWS UNBOUNDED PRECEDING
            ) AS SlotsAtivos,
            LEAD(e.DiaEvento) OVER (
                PARTITION BY e.CodPonto
                ORDER BY e.DiaEvento
            ) AS ProxDiaEvento
        FROM eventos e
    ),
    segmentos AS (
        SELECT
            eo.CodPonto,
            eo.DiaEvento,
            eo.ProxDiaEvento,
            CAST(
                CASE
                    WHEN eo.SlotsAtivos > (SELECT TOP 1 K_fisico FROM k_calc) THEN CAST((SELECT TOP 1 K_fisico FROM k_calc) AS float)
                    WHEN eo.SlotsAtivos < 0 THEN 0.0
                    ELSE CAST(eo.SlotsAtivos AS float)
                END
                AS float
            ) AS SlotsCap
        FROM eventos_ordenados eo
        WHERE eo.ProxDiaEvento IS NOT NULL
          AND eo.DiaEvento < eo.ProxDiaEvento
    ),
    segmentos_periodo AS (
        SELECT
            s.SlotsCap,
            CASE WHEN s.DiaEvento > @dt_ini THEN s.DiaEvento ELSE @dt_ini END AS IniInt,
            CASE
                WHEN DATEADD(DAY, -1, s.ProxDiaEvento) < @dt_fim
                    THEN DATEADD(DAY, -1, s.ProxDiaEvento)
                ELSE @dt_fim
            END AS FimInt
        FROM segmentos s
        WHERE s.DiaEvento <= @dt_fim
          AND DATEADD(DAY, -1, s.ProxDiaEvento) >= @dt_ini
    ),
    soma_ocup AS (
        SELECT
            SUM(
                CASE
                    WHEN IniInt <= FimInt
                        THEN SlotsCap * CAST(DATEDIFF(DAY, IniInt, DATEADD(DAY, 1, FimInt)) AS float)
                    ELSE 0.0
                END
            ) AS OcupadoSlotDiasTotal
        FROM segmentos_periodo
    )
    SELECT
        CAST((SELECT COALESCE(SUM(Receita), 0) FROM receita_m) AS decimal(18,2)) AS receita_brl,

        CAST(
            CASE
                WHEN (CAST((SELECT TOP 1 K_fisico FROM k_calc) AS float) * CAST(DATEDIFF(DAY, @dt_ini, DATEADD(DAY, 1, @dt_fim)) AS float)) = 0
                    THEN 0.0
                ELSE
                    (ISNULL((SELECT TOP 1 OcupadoSlotDiasTotal FROM soma_ocup), 0.0)
                     /
                     (CAST((SELECT TOP 1 K_fisico FROM k_calc) AS float) * CAST(DATEDIFF(DAY, @dt_ini, DATEADD(DAY, 1, @dt_fim)) AS float))
                    ) * 100.0
            END
            AS decimal(18,6)
        ) AS ocupacao_pct,

        CAST(
            CASE
                WHEN (SELECT COALESCE(SUM(CustoPorFace), 0.0) FROM custo_face_m) = 0 THEN 0.0
                ELSE (
                    ((SELECT COALESCE(SUM(Receita), 0.0) FROM receita_m) - (SELECT COALESCE(SUM(CustoPorFace), 0.0) FROM custo_face_m))
                    /
                    NULLIF((SELECT COALESCE(SUM(CustoPorFace), 0.0) FROM custo_face_m), 0.0)
                ) * 100.0
            END
            AS decimal(18,6)
        ) AS rentabilidade_pct;
    """

    params_kpi = {
        "id": int(id_str),
        "dt_ini": dt_ini if dt_ini else None,
        "dt_fim": dt_fim if dt_fim else None,
    }

    with db.engine.connect() as conn:
        rs = conn.execute(text(sql_ativo), {"id": int(id_str)})
        row = rs.fetchone()
        if not row:
            return jsonify({"ok": False, "msg": "não encontrado"}), 404

        colunas = list(rs.keys())
        ativo = dict(zip(colunas, row))

        # formata data
        if ativo.get("DataAtualizacao") is not None:
            try:
                ativo["DataAtualizacao"] = ativo["DataAtualizacao"].strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ativo["DataAtualizacao"] = str(ativo["DataAtualizacao"])

        rs2 = conn.execute(text(sql_kpis_periodo), params_kpi)
        k = rs2.fetchone()

        if not k:
            kpis = {"receita_brl": 0.0, "ocupacao_pct": 0.0, "rentabilidade_pct": 0.0}
        else:
            kpis = {
                "receita_brl": float(k[0] or 0.0),
                "ocupacao_pct": float(k[1] or 0.0),
                "rentabilidade_pct": float(k[2] or 0.0),
            }

    # ✅ compat extra (se o front ainda usa nomes antigos em algum lugar)
    kpis["ocupacao"] = kpis["ocupacao_pct"]
    kpis["rentabilidade"] = kpis["rentabilidade_pct"]

    # ✅ NOVO: regra de exibição do gráfico de custos (somente Tipo == 'Painel')
    tipo_ativo = (ativo.get("Tipo") or "").strip().upper()
    mostrar_custos = (tipo_ativo == "PAINEL")

    return jsonify({"ok": True, "ativo": ativo, "kpis": kpis, "kpis_12m": kpis, "mostrar_custos": mostrar_custos})








@admin.route("/ativos/serie_receita", methods=["GET"])
@login_required
def ativos_serie_receita():
    id_str = (request.args.get("id") or "").strip()
    if not id_str.isdigit():
        return jsonify({"ok": False, "msg": "id inválido"}), 400

    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()


    caminho_sql = Path(current_app.root_path) / "admin" / "querys" / "ativos_serie_receita_query.sql"

    if not caminho_sql.exists():
        raise FileNotFoundError(f"Arquivo SQL não encontrado em: {caminho_sql}")

    sql = caminho_sql.read_text(encoding="utf-8")

    params = {
        "id": int(id_str),
        "dt_ini": dt_ini if dt_ini else None,
        "dt_fim": dt_fim if dt_fim else None,
    }

    with db.engine.connect() as conn:
        rs = conn.execute(text(sql), params)
        rows = rs.fetchall() or []

    serie = []
    for r in rows:
        mes = r[0]
        receita = float(r[1] or 0.0)
        ocupacao_pct = None if r[2] is None else float(r[2])
        rentab_pct = None if r[3] is None else float(r[3])

        serie.append({
            "mes": mes,
            "receita": receita,
            "ocupacao_pct": ocupacao_pct,
            "rentabilidade_pct": rentab_pct,
        })

    return jsonify({"ok": True, "serie": serie})






@admin.route("/ativos/serie_rentabilidade", methods=["GET"])
@login_required
def ativos_serie_rentabilidade():
    """
    Rentabilidade % mensal do Ativo (CodPonto):

    - ReceitaMes: rateio mensal do TotalLiquidoContratoAGBRCTACORDO
    - CustoMes: custo vindo de Integracao.Silver.DimCustoPainel (Valor = ValorMensal),
               aplicado por dia do mês SOMENTE nos dias em que existe contrato ativo no painel:
               CustoMes = (ValorMensal / DiasNoMes) * DiasOcupadosNoMes

    Fórmula (igual sua query "correta"):
      margem_mes = ReceitaMes - CustoMes
      rentabilidade_pct = (margem_mes / ReceitaMes) * 100

    Retorna por mês:
      mes, receita_brl, custo_brl, lucro_brl, rentabilidade_pct
    """
    id_str = (request.args.get("id") or "").strip()
    if not id_str.isdigit():
        return jsonify({"ok": False, "msg": "id inválido"}), 400

    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()
    if not dt_ini or not dt_fim:
        return jsonify({"ok": False, "msg": "dt_ini e dt_fim são obrigatórios"}), 400

 
    caminho_sql = Path(current_app.root_path) / "admin" / "querys" / "ativos_serie_rentabilidade_query.sql"

    if not caminho_sql.exists():
        raise FileNotFoundError(f"Arquivo SQL não encontrado em: {caminho_sql}")

    sql = caminho_sql.read_text(encoding="utf-8")

    with db.engine.connect() as conn:
        rs = conn.execute(text(sql), {"id": int(id_str), "dt_ini": dt_ini, "dt_fim": dt_fim})
        rows = rs.fetchall() or []

    serie = []
    for r in rows:
        serie.append({
            "mes": r[0],
            "receita_brl": float(r[1] or 0.0),
            "custo_brl": float(r[2] or 0.0),
            "lucro_brl": float(r[3] or 0.0),
            "rentabilidade_pct": float(r[4] or 0.0),
            "rentabilidade": float(r[4] or 0.0),
        })

    print(serie)
    return jsonify({"ok": True, "serie": serie})






@admin.route("/ativos/serie_indicadores", methods=["GET"])
@login_required
def ativos_serie_indicadores():
    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()

    if not dt_ini or not dt_fim:
        return jsonify({"ok": False, "msg": "dt_ini e dt_fim são obrigatórios"}), 400

    caminho_sql = Path(current_app.root_path) / "admin" / "querys" / "ativos_serie_indicadores_query.sql"

    if not caminho_sql.exists():
        raise FileNotFoundError(f"Arquivo SQL não encontrado em: {caminho_sql}")

    sql = caminho_sql.read_text(encoding="utf-8")

    with db.engine.connect() as conn:
        resultado = conn.execute(text(sql), {"dt_ini": dt_ini, "dt_fim": dt_fim})
        linhas = resultado.mappings().all() or []

    serie = []
    for linha in linhas:
        serie.append({
            "mes": linha.get("Mes"),

            "cdi": float(linha.get("cdi") or 0.0),
            "dolar": float(linha.get("dolar") or 0.0),
            "sp500_brl": float(linha.get("sp500_brl") or 0.0),

            "cco": float(linha.get("cco") or 0.0),
            "icon": float(linha.get("icon") or 0.0),
            "iimob": float(linha.get("iimob") or 0.0),
            "iind": float(linha.get("iind") or 0.0),

            "lamr": float(linha.get("lamr") or 0.0),
            "out": float(linha.get("out") or 0.0),
            "ifin": float(linha.get("ifin") or 0.0),

            "ouro": float(linha.get("ouro") or 0.0),
            "petroleo": float(linha.get("petroleo") or 0.0),

            "ooh": float(linha.get("ooh") or 0.0),
            "ooh_global": float(linha.get("ooh_global") or 0.0),
        })

    return jsonify({
        "ok": True,
        "dt_ini": dt_ini,
        "dt_fim": dt_fim,
        "serie": serie,
    })





@admin.route("/ativos/serie_ocupacao", methods=["GET"])
@login_required
def ativos_serie_ocupacao():
    """
    Ocupação % mensal por CodPonto (Ativo = Ponto, sem face).

    PercentOcupadoMes = (OcupadoSlotDiasMes / CapacidadeSlotDiasMes) * 100

    Digital: SlotsContrato = 1080 / Cota
    Não digital: SlotsContrato = 1
    """
    id_str = (request.args.get("id") or "").strip()
    if not id_str.isdigit():
        return jsonify({"ok": False, "msg": "id inválido"}), 400

    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()
    if not dt_ini or not dt_fim:
        return jsonify({"ok": False, "msg": "dt_ini e dt_fim são obrigatórios"}), 400

  
    caminho_sql = Path(current_app.root_path) / "admin" / "querys" / "ativos_serie_ocupacao_query.sql"

    if not caminho_sql.exists():
        raise FileNotFoundError(f"Arquivo SQL não encontrado em: {caminho_sql}")

    sql = caminho_sql.read_text(encoding="utf-8")

    with db.engine.connect() as conn:
        rs = conn.execute(text(sql), {"id": int(id_str), "dt_ini": dt_ini, "dt_fim": dt_fim})
        rows = rs.fetchall() or []

    serie = [{"mes": r[0], "ocupacao_pct": float(r[1] or 0.0), "ocupacao": float(r[1] or 0.0)} for r in rows]
    print(serie)
    return jsonify({"ok": True, "serie": serie})











@admin.route("/ativos/matriz_composicao", methods=["GET"])
@login_required
def ativos_matriz_composicao():
    id_str = (request.args.get("id") or "").strip()
    if not id_str.isdigit():
        return jsonify({"ok": False, "msg": "id inválido"}), 400

    dt_ini_str = (request.args.get("dt_ini") or "").strip()
    dt_fim_str = (request.args.get("dt_fim") or "").strip()

    if not dt_ini_str:
        dt_ini_str = "2024-01-01"
    if not dt_fim_str:
        dt_fim_str = datetime.today().strftime("%Y-%m-%d")

    def _primeiro_dia_mes(iso: str, fallback: str) -> str:
        try:
            d = datetime.strptime(iso, "%Y-%m-%d")
            return f"{d.year:04d}-{d.month:02d}-01"
        except Exception:
            return fallback

    dt_ini_mes = _primeiro_dia_mes(dt_ini_str, "2024-01-01")
    dt_fim_mes = _primeiro_dia_mes(dt_fim_str, datetime.today().strftime("%Y-%m-01"))


    caminho_sql = Path(current_app.root_path)/ "admin"/ "querys"/"ativos_matriz_composicao_query.sql"

    if not caminho_sql.exists():
        raise FileNotFoundError(f"Arquivo SQL não encontrado em: {caminho_sql}")

    sql = caminho_sql.read_text(encoding="utf-8")

    with db.engine.connect() as conn:
        rs = conn.execute(
            text(sql),
            {"id": int(id_str), "dt_ini": dt_ini_mes, "dt_fim": dt_fim_mes}
        )
        rows = rs.fetchall() or []
        cols = list(rs.keys())

    if rows and rows[0][0] == "EMPTY":
        return jsonify({
            "ok": True,
            "dt_ini": dt_ini_str,
            "dt_fim": dt_fim_str,
            "serie": [],
            "matriz": [],
            "composicao": []
        })

    full = []
    periodo = []
    composicao = []

    for r in rows:
        d = dict(zip(cols, r))
        tipo = d.get("Tipo")

        if tipo == "FULL":
            full.append({
                "ano": int(d.get("Ano") or 0),
                "mes": int(d.get("Mes") or 0),
                "mes_str": d.get("MesStr"),
                "receita": float(d.get("Receita") or 0.0),
                "custo": float(d.get("Custo") or 0.0),
                "margem_pct": float(d.get("MargemPct") or 0.0),
            })
        elif tipo == "PERIODO":
            periodo.append({
                "ano": int(d.get("Ano") or 0),
                "mes": int(d.get("Mes") or 0),
                "mes_str": d.get("MesStr"),
                "receita": float(d.get("Receita") or 0.0),
                "custo": float(d.get("Custo") or 0.0),
                "margem_pct": float(d.get("MargemPct") or 0.0),
            })
        elif tipo == "COMP":
            nome = (d.get("Categoria") or "").strip() or "Sem Categoria"
            composicao.append({
                "nome": nome,
                "valor": float(d.get("Valor") or 0.0),
            })

    serie = [{
        "mes": x["mes_str"],
        "ano": x["ano"],
        "mes_num": x["mes"],
        "receita": x["receita"],
        "custo": x["custo"],
        "margem_pct": x["margem_pct"],
    } for x in periodo]

    anos_asc = sorted({x["ano"] for x in full if x["ano"] > 0})

    matriz_tmp = []
    acumulado = 0.0

    for ano in anos_asc:
        itens_ano = [x for x in full if x["ano"] == ano and 1 <= x["mes"] <= 12]

        meses_dict = {m: {"receita": None, "custo": None, "margem_pct": None} for m in range(1, 13)}

        soma_receita = 0.0
        soma_lucro = 0.0
        soma_receita_margem = 0.0

        for it in itens_ano:
            m = int(it["mes"])
            rec = float(it["receita"] or 0.0)
            cus = float(it["custo"] or 0.0)

            meses_dict[m]["receita"] = rec
            meses_dict[m]["custo"] = cus
            meses_dict[m]["margem_pct"] = float(it["margem_pct"] or 0.0)

            soma_receita += rec
            soma_lucro += (rec - cus)
            if rec > 0:
                soma_receita_margem += rec

        margem_media = None
        if soma_receita_margem > 0:
            margem_media = (soma_lucro / soma_receita_margem) * 100.0

        acumulado += soma_receita

        matriz_tmp.append({
            "ano": ano,
            "meses": meses_dict,
            "total_ano": soma_receita,
            "margem_media_ano": margem_media,
            "acumulado": acumulado,
        })

    
    matriz = matriz_tmp

    return jsonify({
        "ok": True,
        "dt_ini": dt_ini_str,
        "dt_fim": dt_fim_str,
        "serie": serie,
        "matriz": matriz,
        "composicao": composicao,
    })


























def _obter_id_usuario_logado_admin() -> int | None:
    """Eu tento descobrir o ID do usuário logado de forma defensiva."""
    candidatos = [
        getattr(current_user, "IDDimUsuarios", None),
        getattr(current_user, "id", None),
    ]

    for valor in candidatos:
        if valor is None or valor == "":
            continue
        try:
            return int(valor)
        except Exception:
            continue

    return None


def _normalizar_float_brasil(valor_texto: str) -> float:
    """Eu converto texto como 12, 12,5 ou 12.5 para float."""
    texto = (valor_texto or "").strip().replace("%", "").strip()

    if not texto:
        raise ValueError("Informe o desconto máximo.")

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    valor = float(texto)

    if valor < 0:
        raise ValueError("O desconto máximo não pode ser negativo.")

    return valor





@admin.route("/permissao-desconto", methods=["GET", "POST"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET", "POST"])
def permissao_desconto():
    q = (request.args.get("q") or "").strip()

    try:
        page = int(request.args.get("page") or "1")
    except Exception:
        page = 1

    per_page = 10
    page = max(1, page)
    offset = (page - 1) * per_page

    filtros_sql = [
        "u.BitAtivo = 1"
    ]
    params = {}

    if q:
        filtros_sql.append("""
            (
                CAST(u.IDDimUsuarios AS varchar(50)) LIKE :q_like
                OR ISNULL(u.NomeUsuario, '') LIKE :q_like
                OR ISNULL(u.Email, '') LIKE :q_like
            )
        """)
        params["q_like"] = f"%{q}%"

    where_sql = " AND ".join([f"({x})" for x in filtros_sql]) if filtros_sql else "1=1"

    if request.method == "POST":
        id_usuario_logado = _obter_id_usuario_logado_admin()

        try:
            id_dim_usuarios = int(request.form.get("id_dim_usuarios") or 0)
        except Exception:
            id_dim_usuarios = 0

        desconto_maximo_raw = request.form.get("desconto_maximo") or ""

        try:
            if not id_usuario_logado:
                raise ValueError("Não foi possível identificar o usuário logado.")

            if id_dim_usuarios <= 0:
                raise ValueError("Selecione um usuário.")

            desconto_maximo = _normalizar_float_brasil(desconto_maximo_raw)

            usuario_existe = db.session.execute(
                text("""
                    SELECT TOP 1
                        u.IDDimUsuarios,
                        u.NomeUsuario
                    FROM [Integracao].[Silver].[DimUsuarios] u
                    WHERE u.IDDimUsuarios = :id_dim_usuarios
                      AND ISNULL(u.BitAtivo, 0) = 1
                """),
                {
                    "id_dim_usuarios": id_dim_usuarios,
                },
            ).mappings().first()

            if not usuario_existe:
                raise ValueError("Usuário não encontrado ou inativo.")

            db.session.execute(
                text("""
                    UPDATE [Kanban].[Silver].[DimKanbanPermissaoDesconto]
                    SET
                        BitAtivo = 0,
                        DataAtualizado = GETDATE(),
                        IDUsuarioAprovado = :id_usuario_aprovado
                    WHERE IDDimUsuarios = :id_dim_usuarios
                      AND ISNULL(BitAtivo, 0) = 1
                """),
                {
                    "id_dim_usuarios": id_dim_usuarios,
                    "id_usuario_aprovado": id_usuario_logado,
                },
            )

            db.session.execute(
                text("""
                    INSERT INTO [Kanban].[Silver].[DimKanbanPermissaoDesconto]
                    (
                        IDDimUsuarios,
                        DescontoMaximo,
                        DataAtualizado,
                        IDUsuarioAprovado,
                        BitAtivo
                    )
                    VALUES
                    (
                        :id_dim_usuarios,
                        :desconto_maximo,
                        GETDATE(),
                        :id_usuario_aprovado,
                        1
                    )
                """),
                {
                    "id_dim_usuarios": id_dim_usuarios,
                    "desconto_maximo": desconto_maximo,
                    "id_usuario_aprovado": id_usuario_logado,
                },
            )

            db.session.commit()
            flash("Permissão de desconto salva com sucesso.", "success")

            return redirect(
                url_for(
                    "admin.permissao_desconto",
                    page=page,
                    q=q,
                )
            )

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("Erro ao salvar permissão de desconto: %s", e)
            flash(f"Erro ao salvar permissão de desconto: {e}", "danger")

    sql_total = text(f"""
        SELECT COUNT(1) AS total
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE {where_sql}
    """)

    row_total = db.session.execute(sql_total, params).fetchone()
    total = int(row_total[0] or 0) if row_total else 0
    total_pages = max(1, (total + per_page - 1) // per_page)

    if page > total_pages:
        page = total_pages
        offset = (page - 1) * per_page

    params_lista = dict(params)
    params_lista["offset"] = offset
    params_lista["limit"] = per_page

    sql_lista = text(f"""
        SELECT
            u.IDDimUsuarios,
            u.NomeUsuario,
            u.Email,
            pd.DescontoMaximo AS DescontoPermitido,
            ua.NomeUsuario AS UsuarioAprovadoNome
        FROM [Integracao].[Silver].[DimUsuarios] u
        OUTER APPLY (
            SELECT TOP 1
                p.DescontoMaximo,
                p.IDUsuarioAprovado,
                p.DataAtualizado
            FROM [Kanban].[Silver].[DimKanbanPermissaoDesconto] p
            WHERE p.IDDimUsuarios = u.IDDimUsuarios
              AND ISNULL(p.BitAtivo, 0) = 1
            ORDER BY
                ISNULL(p.DataAtualizado, '1900-01-01') DESC,
                p.IDDimKanbanPermissaoDesconto DESC
        ) pd
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] ua
            ON ua.IDDimUsuarios = pd.IDUsuarioAprovado
        WHERE {where_sql}
        ORDER BY
            u.NomeUsuario ASC,
            u.IDDimUsuarios ASC
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """)

    rows = db.session.execute(sql_lista, params_lista).mappings().all()

    usuarios = []
    for r in rows:
        usuarios.append(
            {
                "IDDimUsuarios": r.get("IDDimUsuarios"),
                "NomeUsuario": (r.get("NomeUsuario") or "").strip(),
                "Email": (r.get("Email") or "").strip(),
                "DescontoPermitido": r.get("DescontoPermitido"),
                "UsuarioAprovadoNome": (r.get("UsuarioAprovadoNome") or "").strip(),
            }
        )

    inicio = 0 if total == 0 else offset + 1
    fim = min(offset + per_page, total)

    return render_template(
        "admin/permissao_desconto.html",
        usuarios=usuarios,
        filtros={
            "q": q,
            "per_page": per_page,
        },
        paginacao={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "inicio": inicio,
            "fim": fim,
        },
    )










def _resolver_url_logo_empresa_proprietaria(valor_logo) -> str:
    valor = str(valor_logo or "").strip()
    if not valor:
        return ""

    valor = valor.replace("\\", "/").strip()

    if valor.startswith("http://") or valor.startswith("https://") or valor.startswith("data:image/"):
        return valor

    if valor.startswith("/static/"):
        return valor

    if valor.startswith("static/"):
        return f"/{valor}"

    if "LogoEmpresaProprietaria/" in valor:
        sufixo = valor.split("LogoEmpresaProprietaria/", 1)[1].lstrip("/")
        if sufixo:
            return url_for("static", filename=f"LogoEmpresaProprietaria/{sufixo}")

    nome_arquivo = Path(valor).name.strip()
    if not nome_arquivo:
        return ""

    return url_for("static", filename=f"LogoEmpresaProprietaria/{nome_arquivo}")


def _texto_ou_none(valor):
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor if valor != "" else None


def _texto_ou_vazio(valor):
    return str(valor or "").strip()


def _int_ou_none(valor):
    valor = _texto_ou_none(valor)
    if valor is None:
        return None
    try:
        return int(valor)
    except Exception:
        return None

def _normalizar_ativo_cancelamento_aprovacao(valor):
    """
    Eu garanto que item aprovado entre como ocupação ativa na grade.

    A grade de ocupação filtra FatoControleContratosItensEuromidia.AtivoCancelamento = 'A'.
    Quando a solicitação vem do Kanban, esse campo pode chegar nulo; se ficar nulo,
    o item aprovado não aparece na grade depois que a reserva sai de RESERVADO.
    """
    texto = str(valor or "").strip().upper()

    if texto in {"C", "CANCELADO", "CANCELADA", "CANCELAMENTO", "I", "INATIVO", "INATIVA", "0", "FALSE", "N", "NAO", "NÃO"}:
        return "C"

    return "A"


def _normalizar_status_item_aprovacao(valor):
    """Eu preencho Status do item aprovado sem deixar nulo quando veio do Kanban."""
    texto = str(valor or "").strip()
    return texto if texto else "ATIVO"



def _gerar_hash_sha256_hex(*partes) -> str:
    texto_base = '|'.join(_texto_ou_vazio(parte) for parte in partes)
    return hashlib.sha256(texto_base.encode('utf-8')).hexdigest().upper()


def _gerar_referencia_contrato_hash(*, id_fato_controle_contratos: int | None, cnpj: str | None, marca_exibida: str | None, id_empresa: int | None) -> str:
    return _gerar_hash_sha256_hex(
        int(id_fato_controle_contratos) if id_fato_controle_contratos not in (None, '', 0) else '',
        _texto_ou_vazio(cnpj),
        _texto_ou_vazio(marca_exibida),
        int(id_empresa) if id_empresa not in (None, '', 0) else '',
    )


def _gerar_referencia_contrato_temporaria(*, id_fato_solicitacao: int | None, cnpj: str | None, marca_exibida: str | None, id_empresa: int | None) -> str:
    return _gerar_hash_sha256_hex(
        'PENDENTE_CONTRATO',
        int(id_fato_solicitacao) if id_fato_solicitacao not in (None, '', 0) else '',
        _texto_ou_vazio(cnpj),
        _texto_ou_vazio(marca_exibida),
        int(id_empresa) if id_empresa not in (None, '', 0) else '',
    )


def _gerar_referencia_item_contrato_hash(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_solicitacao_item: int | None,
    cod_ponto: str | int | None,
    cod_face: str | None,
    id_painel: int | None,
    id_face: int | None,
    cnpj: str | None,
    tentativa: int = 0,
) -> str:
    """
    Eu gero uma Referencia própria para o item do contrato.

    Motivo:
    - a tabela de itens tem índice único em Referencia;
    - o cabeçalho do contrato também tem Referencia;
    - vários itens não podem herdar a mesma Referencia do cabeçalho.
    """
    return _gerar_hash_sha256_hex(
        'ITEM_CONTRATO',
        int(id_fato_controle_contratos) if id_fato_controle_contratos not in (None, '', 0) else '',
        int(id_fato_solicitacao_item) if id_fato_solicitacao_item not in (None, '', 0) else '',
        _texto_ou_vazio(cod_ponto),
        _texto_ou_vazio(cod_face).upper(),
        int(id_painel) if id_painel not in (None, '', 0) else '',
        int(id_face) if id_face not in (None, '', 0) else '',
        _texto_ou_vazio(cnpj),
        int(tentativa or 0),
    )


def _referencia_item_controle_esta_livre(
    *,
    referencia: str | None,
    id_item_controle_atual: int | None = None,
) -> bool:
    """Eu verifico se a Referencia do item não está sendo usada por outro item."""
    referencia_limpa = _texto_ou_none(referencia)
    if not referencia_limpa:
        return False

    id_item_atual_int = _int_ou_none(id_item_controle_atual)

    row = db.session.execute(
        text("""
            SELECT TOP 1
                   i.IDFatoControleContratosItensEuromidia
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
            WHERE i.Referencia = :referencia
              AND (
                    :id_item_controle_atual IS NULL
                    OR i.IDFatoControleContratosItensEuromidia <> :id_item_controle_atual
                  )
        """),
        {
            "referencia": referencia_limpa,
            "id_item_controle_atual": id_item_atual_int,
        },
    ).mappings().first()

    return row is None


def _resolver_referencia_item_controle(
    *,
    id_fato_controle_contratos: int,
    id_item_controle_atual: int | None,
    id_item_solicitacao: int | None,
    referencia_informada: str | None,
    referencia_contrato: str | None,
    referencia_atual: str | None,
    cod_ponto: str | int | None,
    cod_face: str | None,
    id_painel: int | None,
    id_face: int | None,
    cnpj: str | None,
) -> str:
    """
    Eu resolvo uma Referencia segura para o item antes do INSERT/UPDATE.

    Regra principal:
    - se o item já existe, preservo a Referencia atual dele;
    - se veio uma Referencia própria e livre, uso ela;
    - se veio a mesma Referencia do cabeçalho, gero uma Referencia exclusiva do item;
    - se a Referencia informada já estiver em outro item, gero uma nova.
    """
    id_item_controle_atual_int = _int_ou_none(id_item_controle_atual)
    referencia_atual_limpa = _texto_ou_none(referencia_atual)
    referencia_informada_limpa = _texto_ou_none(referencia_informada)
    referencia_contrato_limpa = _texto_ou_none(referencia_contrato)

    if (
        referencia_atual_limpa
        and _referencia_item_controle_esta_livre(
            referencia=referencia_atual_limpa,
            id_item_controle_atual=id_item_controle_atual_int,
        )
    ):
        return referencia_atual_limpa

    referencia_informada_eh_do_cabecalho = (
        bool(referencia_informada_limpa)
        and bool(referencia_contrato_limpa)
        and referencia_informada_limpa == referencia_contrato_limpa
    )

    if (
        referencia_informada_limpa
        and not referencia_informada_eh_do_cabecalho
        and _referencia_item_controle_esta_livre(
            referencia=referencia_informada_limpa,
            id_item_controle_atual=id_item_controle_atual_int,
        )
    ):
        return referencia_informada_limpa

    for tentativa in range(0, 25):
        referencia_gerada = _gerar_referencia_item_contrato_hash(
            id_fato_controle_contratos=id_fato_controle_contratos,
            id_fato_solicitacao_item=id_item_solicitacao,
            cod_ponto=cod_ponto,
            cod_face=cod_face,
            id_painel=id_painel,
            id_face=id_face,
            cnpj=cnpj,
            tentativa=tentativa,
        )

        if _referencia_item_controle_esta_livre(
            referencia=referencia_gerada,
            id_item_controle_atual=id_item_controle_atual_int,
        ):
            return referencia_gerada

    raise RuntimeError(
        "Não foi possível gerar uma Referencia única para o item do contrato. "
        f"Contrato={id_fato_controle_contratos}, ItemSolicitacao={id_item_solicitacao}, "
        f"CodPonto={cod_ponto}, CodFace={cod_face}."
    )


def _decimal_ou_none(valor):
    valor = _texto_ou_none(valor)
    if valor is None:
        return None

    valor = valor.replace("R$", "").replace(" ", "")

    if "," in valor and "." in valor:
        valor = valor.replace(".", "").replace(",", ".")
    elif "," in valor:
        valor = valor.replace(",", ".")

    try:
        return float(valor)
    except Exception:
        return None


def _data_ou_none(valor):
    valor = _texto_ou_none(valor)
    if valor is None:
        return None

    formatos = (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M",
    )

    for fmt in formatos:
        try:
            return datetime.strptime(valor, fmt)
        except Exception:
            pass

    return None


def _data_para_input_date(valor):
    if not valor:
        return ""
    try:
        return valor.strftime("%Y-%m-%d")
    except Exception:
        return str(valor)[:10]


def _tipo_solicitacao_normalizado(valor):
    valor = _texto_ou_vazio(valor).upper().replace("_", " ")
    if valor == "NOVO CONTRATO":
        return "NOVO CONTRATO"
    if valor == "ADITIVO":
        return "ADITIVO"
    return "ADITIVO"


def _id_usuario_logado():
    candidatos = [
        getattr(current_user, "IDDimUsuarios", None),
        getattr(current_user, "id", None),
        getattr(current_user, "Id", None),
        getattr(current_user, "ID", None),
    ]
    for c in candidatos:
        try:
            if c is not None:
                return int(c)
        except Exception:
            pass
    return None


def _resolver_face_e_painel_por_codigos(cod_ponto: str | None, cod_face: str | None):
    cod_ponto = _texto_ou_none(cod_ponto)
    cod_face = _texto_ou_none(cod_face)

    if not cod_ponto or not cod_face:
        return None

    sql = text("""
        SELECT TOP 1
               df.[IDDimFacesPaineis],
               COALESCE(df.[IDDimPaineisEuromidia], dp.[IDDimPaineisEuromidia]) AS [IDDimPaineisEuromidia],
               df.[Face],
               df.[CodFace],
               df.[CodPonto],
               COALESCE(df.[Tipo], dp.[Tipo]) AS [TipoPainel],
               dp.[Cidade],
               dp.[UF]
        FROM [Integracao].[Silver].[DimFacesPaineis] df
        LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] dp
               ON dp.[IDDimPaineisEuromidia] = df.[IDDimPaineisEuromidia]
        WHERE LTRIM(RTRIM(ISNULL(df.[CodPonto], ''))) = :cod_ponto
          AND LTRIM(RTRIM(ISNULL(df.[CodFace], ''))) = :cod_face
    """)

    row = db.session.execute(
        sql,
        {
            "cod_ponto": cod_ponto,
            "cod_face": cod_face,
        }
    ).mappings().first()

    return dict(row) if row else None





def _obter_id_acao_solicitacao_contrato(nome_acao: str, fallback: int | None = None) -> int | None:
    nome_limpo = _texto_ou_vazio(nome_acao).upper()
    if not nome_limpo:
        return fallback

    row = db.session.execute(
        text("""
            SELECT TOP 1 IDDimAcaoSolicitacaoContrato
            FROM [Integracao].[Silver].[DimAcaoSolicitacaoContrato]
            WHERE UPPER(LTRIM(RTRIM(ISNULL(NomeAcaoContrato, '')))) = :nome_acao
        """),
        {"nome_acao": nome_limpo},
    ).mappings().first()

    if row and row.get("IDDimAcaoSolicitacaoContrato") is not None:
        try:
            return int(row["IDDimAcaoSolicitacaoContrato"])
        except Exception:
            pass

    return fallback



def _obter_id_dim_acao_solicitacao_contrato(nome_acao: str, fallback: int | None = None) -> int | None:
    return _obter_id_acao_solicitacao_contrato(nome_acao=nome_acao, fallback=fallback)


def _resolver_id_fato_controle_contratos_para_historico(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_solicitacao: int | None,
) -> int | None:
    id_contrato_informado = _int_ou_none(id_fato_controle_contratos)
    if id_contrato_informado not in (None, '', 0):
        return int(id_contrato_informado)

    id_solicitacao_int = _int_ou_none(id_fato_solicitacao)
    if id_solicitacao_int in (None, '', 0):
        return None

    row = db.session.execute(
        text("""
            SELECT TOP 1 IDFatoControleContratosEuromidia
            FROM [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
            WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
        """),
        {"id_solicitacao": int(id_solicitacao_int)},
    ).mappings().first()

    if row and row.get("IDFatoControleContratosEuromidia") not in (None, '', 0):
        try:
            return int(row["IDFatoControleContratosEuromidia"])
        except Exception:
            return None

    return None


def _registrar_historico_contrato_euromidia(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_solicitacao: int | None,
    id_dim_acao: int | None,
    id_empresa: int | None,
    id_empresa_proprietaria: int | None,
    id_fato_kanban_card: int | None,
    tipo_evento: str | None,
    tipo_solicitacao: str | None,
    descricao_evento: str | None,
    id_dim_usuario_acao: int | None,
) -> None:
    id_fato_controle_contratos_resolvido = _resolver_id_fato_controle_contratos_para_historico(
        id_fato_controle_contratos=id_fato_controle_contratos,
        id_fato_solicitacao=id_fato_solicitacao,
    )

    tabela_historico_existe = db.session.execute(
        text("""
            SELECT CASE WHEN OBJECT_ID(N'[Integracao].[Silver].[FatoHistoricoContratoEuromidia]', N'U') IS NOT NULL THEN 1 ELSE 0 END AS Existe
        """)
    ).scalar()

    if not tabela_historico_existe:
        return

    db.session.execute(
        text("""
            INSERT INTO [Integracao].[Silver].[FatoHistoricoContratoEuromidia]
            (
                IDFatoControleContratosEuromidia,
                IDFatoSolicitacaoContratoEuromidia,
                IDDimAcaoSolicitacaoContrato,
                IDEmpresa,
                IDEmpresaProprietaria,
                IDFatoKanbanCard,
                TipoEvento,
                TipoSolicitacao,
                DescricaoEvento,
                IDDimUsuarioAcao,
                DataEvento
            )
            VALUES
            (
                :id_fato_controle_contratos,
                :id_fato_solicitacao,
                :id_dim_acao,
                :id_empresa,
                :id_empresa_proprietaria,
                :id_fato_kanban_card,
                :tipo_evento,
                :tipo_solicitacao,
                :descricao_evento,
                :id_dim_usuario_acao,
                GETDATE()
            )
        """),
        {
            "id_fato_controle_contratos": int(id_fato_controle_contratos_resolvido) if id_fato_controle_contratos_resolvido not in (None, '', 0) else None,
            "id_fato_solicitacao": int(id_fato_solicitacao) if id_fato_solicitacao not in (None, '', 0) else None,
            "id_dim_acao": int(id_dim_acao) if id_dim_acao not in (None, '', 0) else None,
            "id_empresa": int(id_empresa) if id_empresa not in (None, '', 0) else None,
            "id_empresa_proprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, '', 0) else None,
            "id_fato_kanban_card": int(id_fato_kanban_card) if id_fato_kanban_card not in (None, '', 0) else None,
            "tipo_evento": _texto_ou_none(tipo_evento),
            "tipo_solicitacao": _texto_ou_none(tipo_solicitacao),
            "descricao_evento": _texto_ou_none(descricao_evento),
            "id_dim_usuario_acao": int(id_dim_usuario_acao) if id_dim_usuario_acao not in (None, '', 0) else None,
        },
    )


def _card_possui_tag_ativa_admin(id_card: int | None, id_tag: int | None) -> bool:
    if id_card in (None, '', 0) or id_tag in (None, '', 0):
        return False

    filtros = [
        "IDFatoKanbanCard = :id_card",
        "IDDimKanbanTag = :id_tag",
        "RemovidoEm IS NULL",
    ]

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_TAG_RENOVACAO, "Ativo"):
        filtros.append("ISNULL(Ativo, 1) = 1")

    existe = db.session.execute(
        text(f"""
            SELECT TOP 1 1
            FROM {TABELA_KANBAN_CARD_TAG_RENOVACAO}
            WHERE {' AND '.join(filtros)}
        """),
        {"id_card": int(id_card), "id_tag": int(id_tag)},
    ).scalar()

    return bool(existe)


def _card_eh_renovacao_admin(id_card: int | None) -> bool:
    """Regra forte no admin: card com tag 17 Renovação nunca pode ser aprovado como Novo Contrato."""
    id_card_int = _int_ou_none(id_card)
    if id_card_int in (None, "", 0):
        return False

    filtros = [
        "ct.IDFatoKanbanCard = :id_card",
        "ct.RemovidoEm IS NULL",
        "(ct.IDDimKanbanTag = :id_tag_renovacao OR UPPER(LTRIM(RTRIM(ISNULL(tg.NomeTag, '')))) COLLATE Latin1_General_CI_AI LIKE '%RENOVA%')",
    ]

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_TAG_RENOVACAO, "Ativo"):
        filtros.append("ISNULL(ct.Ativo, 1) = 1")

    existe = db.session.execute(
        text(f"""
            SELECT TOP 1 1
            FROM {TABELA_KANBAN_CARD_TAG_RENOVACAO} ct
            LEFT JOIN {TABELA_KANBAN_TAG_RENOVACAO} tg
                   ON tg.IDDimKanbanTag = ct.IDDimKanbanTag
            WHERE {' AND '.join(filtros)}
        """),
        {
            "id_card": int(id_card_int),
            "id_tag_renovacao": int(ID_TAG_RENOVACAO_CAMPANHA),
        },
    ).scalar()

    return bool(existe)


def _aplicar_tag_no_card_admin(*, id_card: int | None, id_tag: int | None, id_usuario: int | None, id_empresa_proprietaria: int | None) -> bool:
    if id_card in (None, '', 0) or id_tag in (None, '', 0):
        return False

    id_card_int = int(id_card)
    id_tag_int = int(id_tag)

    # Regra de segurança: card com tag 17 Renovação nunca deve receber tag 9 Novo Contrato.
    # Renovação é continuação/substituição de item existente, não abertura de contrato novo.
    if id_tag_int == int(ID_TAG_TIPO_CONTRATO_NOVO_ADMIN) and _card_eh_renovacao_admin(id_card_int):
        current_app.logger.warning(
            "KANBAN_TAG | bloqueada aplicação da tag Novo Contrato em card de Renovação | id_card=%s | id_usuario=%s",
            id_card_int,
            id_usuario,
        )
        return False

    # Se a tag 17 for aplicada depois, removo a tag 9 imediatamente para não contaminar o card.
    if id_tag_int == int(ID_TAG_RENOVACAO_CAMPANHA):
        try:
            _remover_tag_do_card_admin(
                id_card=id_card_int,
                id_tag=int(ID_TAG_TIPO_CONTRATO_NOVO_ADMIN),
                id_usuario=id_usuario,
            )
        except Exception:
            current_app.logger.exception(
                "KANBAN_TAG | falha ao remover tag Novo Contrato ao aplicar Renovação no admin | id_card=%s",
                id_card_int,
            )

    if _card_possui_tag_ativa_admin(id_card_int, id_tag_int):
        return False

    db.session.execute(
        text("""
            INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
                (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
            VALUES
                (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa_proprietaria)
        """),
        {
            "id_card": int(id_card),
            "id_tag": int(id_tag),
            "id_usuario": int(id_usuario) if id_usuario not in (None, '', 0) else None,
            "id_empresa_proprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, '', 0) else None,
        },
    )
    return True


def _remover_tag_do_card_admin(*, id_card: int | None, id_tag: int | None, id_usuario: int | None) -> bool:
    if id_card in (None, '', 0) or id_tag in (None, '', 0):
        return False

    resultado = db.session.execute(
        text("""
            UPDATE [Kanban].[Silver].[FatoKanbanCardTag]
               SET RemovidoEm = GETDATE(),
                   RemovidoPor = :id_usuario
             WHERE IDFatoKanbanCard = :id_card
               AND IDDimKanbanTag = :id_tag
               AND RemovidoEm IS NULL
        """),
        {"id_card": int(id_card), "id_tag": int(id_tag), "id_usuario": int(id_usuario) if id_usuario not in (None, '', 0) else None},
    )
    return bool(getattr(resultado, 'rowcount', 0) or 0)


def _normalizar_card_renovacao_admin(
    *,
    id_card: int | None,
    id_usuario: int | None,
    tipo_solicitacao: str | None = None,
    id_solicitacao: int | None = None,
) -> dict:
    """Normaliza a regra operacional: ADITIVO + tag 17 = RENOVAÇÃO.

    Renovação continua sendo gravada como TipoSolicitacao = ADITIVO, mas o fluxo
    operacional passa a ser RENOVAÇÃO quando a tag 17 está ativa. Nessa condição:
    - garanto tag 8 Aditivo;
    - removo tag 9 Novo Contrato, se existir;
    - deixo BitAditivo=1 e BitContratoNovo=0 no card;
    - devolvo inicio_renovacao='R' para a aprovação inserir nova linha e inativar a antiga.
    """

    id_card_int = _int_ou_none(id_card)
    id_usuario_int = _int_ou_none(id_usuario)
    id_solicitacao_int = _int_ou_none(id_solicitacao)
    tipo_norm = _tipo_solicitacao_normalizado(tipo_solicitacao) if tipo_solicitacao not in (None, "") else None

    retorno = {
        "id_card": id_card_int,
        "tipo_solicitacao": tipo_norm,
        "tipo_operacional": tipo_norm,
        "tem_tag_renovacao": False,
        "tem_tag_aditivo": False,
        "tem_tag_novo_contrato": False,
        "tag_aditivo_adicionada": False,
        "tag_novo_removida": False,
        "solicitacao_normalizada": False,
        "inicio_renovacao": None,
    }

    if id_card_int in (None, "", 0):
        return retorno

    tem_tag_renovacao = _card_eh_renovacao_admin(id_card_int)
    tem_tag_aditivo = _card_possui_tag_ativa_admin(id_card_int, ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN)
    tem_tag_novo_contrato = _card_possui_tag_ativa_admin(id_card_int, ID_TAG_TIPO_CONTRATO_NOVO_ADMIN)

    retorno["tem_tag_renovacao"] = bool(tem_tag_renovacao)
    retorno["tem_tag_aditivo"] = bool(tem_tag_aditivo)
    retorno["tem_tag_novo_contrato"] = bool(tem_tag_novo_contrato)

    if tem_tag_renovacao:
        retorno["tipo_solicitacao"] = "ADITIVO"
        retorno["tipo_operacional"] = "RENOVACAO"
        retorno["inicio_renovacao"] = "R"

        if not tem_tag_aditivo:
            retorno["tag_aditivo_adicionada"] = _aplicar_tag_no_card_admin(
                id_card=id_card_int,
                id_tag=ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN,
                id_usuario=id_usuario_int,
                id_empresa_proprietaria=ID_EMPRESA_PROPRIETARIA_EUROMIDIA_RENOVACAO,
            )
            retorno["tem_tag_aditivo"] = True

        if tem_tag_novo_contrato:
            retorno["tag_novo_removida"] = _remover_tag_do_card_admin(
                id_card=id_card_int,
                id_tag=ID_TAG_TIPO_CONTRATO_NOVO_ADMIN,
                id_usuario=id_usuario_int,
            )

        sets = []
        params = {"id_card": int(id_card_int)}

        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "BitAditivo"):
            sets.append("BitAditivo = 1")

        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "BitContratoNovo"):
            sets.append("BitContratoNovo = 0")

        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "AtualizadoEm"):
            sets.append("AtualizadoEm = GETDATE()")

        if sets:
            db.session.execute(
                text(f"""
                    UPDATE [Kanban].[Silver].[FatoKanbanCard]
                       SET {', '.join(sets)}
                     WHERE IDFatoKanbanCard = :id_card
                """),
                params,
            )

        if id_solicitacao_int not in (None, "", 0):
            db.session.execute(
                text("""
                    UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
                       SET TipoSolicitacao = 'ADITIVO',
                           DataAtualizacao = GETDATE()
                     WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
                """),
                {"id_solicitacao": int(id_solicitacao_int)},
            )
            retorno["solicitacao_normalizada"] = True

        return retorno

    if tem_tag_novo_contrato:
        retorno["inicio_renovacao"] = "I"

    return retorno


def _obter_data_aprovacao_sql_server_admin():
    """Uso a data do SQL Server como data oficial da aprovação do contrato."""

    return db.session.execute(text("SELECT CAST(GETDATE() AS date) AS DataAprovacao")).scalar()


def _normalizar_data_aprovacao_admin(valor) -> date | None:
    """Converte date/datetime/string para date, sem inventar data quando o valor vem vazio."""

    if valor in (None, ""):
        return None

    if isinstance(valor, datetime):
        return valor.date()

    if isinstance(valor, date):
        return valor

    valor_parseado = _data_ou_none(valor)
    if isinstance(valor_parseado, datetime):
        return valor_parseado.date()

    if isinstance(valor_parseado, date):
        return valor_parseado

    return None


def _resolver_periodo_item_aprovacao_admin(
    *,
    item_solicitacao: dict,
    data_aprovacao_sql_server,
    eh_renovacao: bool,
    id_solicitacao: int | None = None,
    id_item_solicitacao: int | None = None,
) -> dict:
    """Resolve as datas que vão para FatoControleContratosItensEuromidia na aprovação.

    Regras:
    - DataAssinaturaRenovacao sempre recebe a data real da aprovação.
    - Contrato novo usa o período gravado na solicitação.
    - Renovação começa na data da aprovação e termina na data informada pelo usuário.
    - Renovação sem DataTerminoPrevisto informada não pode copiar a data do item antigo em silêncio.
    """

    data_aprovacao = _normalizar_data_aprovacao_admin(data_aprovacao_sql_server) or date.today()

    if eh_renovacao:
        data_termino_informada = item_solicitacao.get("_DataTerminoPrevistoInformadaUsuario")
        data_termino = _normalizar_data_aprovacao_admin(data_termino_informada)

        if data_termino is None:
            data_termino = _normalizar_data_aprovacao_admin(item_solicitacao.get("DataTerminoPrevisto"))

        if data_termino is None:
            raise RuntimeError(
                "Não foi possível aprovar a renovação porque DataTerminoPrevisto não foi informada no item da solicitação. "
                f"IDSolicitacao={id_solicitacao}; IDItemSolicitacao={id_item_solicitacao}. "
                "Informe a data de término da renovação antes de aprovar."
            )

        if data_termino < data_aprovacao:
            raise RuntimeError(
                "Não foi possível aprovar a renovação porque DataTerminoPrevisto ficou menor que a data da aprovação. "
                f"IDSolicitacao={id_solicitacao}; IDItemSolicitacao={id_item_solicitacao}; "
                f"DataAprovacao={data_aprovacao}; DataTerminoPrevisto={data_termino}."
            )

        return {
            "DataAssinaturaRenovacao": data_aprovacao,
            "DataInicioPrevisto": data_aprovacao,
            "DataTerminoPrevisto": data_termino,
        }

    return {
        "DataAssinaturaRenovacao": data_aprovacao,
        "DataInicioPrevisto": item_solicitacao.get("DataInicioPrevisto"),
        "DataTerminoPrevisto": item_solicitacao.get("DataTerminoPrevisto"),
    }


def _resolver_id_card_aprovacao_solicitacao_admin(cabecalho_solicitacao: dict | None, itens_solicitacao: list[dict] | None = None) -> int | None:
    """Resolvo o card real da aprovação olhando cabeçalho e itens.

    Motivo:
    - em alguns fluxos o IDFatoKanbanCard fica no item da solicitação, não no cabeçalho;
    - a regra da renovação depende da tag 17 no card;
    - se eu olhar apenas o cabeçalho e ele vier vazio, o sistema trata renovação como contrato comum,
      não inativa o item antigo e não cria uma nova linha.
    """

    candidatos: list[int] = []

    def adicionar(valor):
        id_card = _int_ou_none(valor)
        if id_card not in (None, "", 0) and int(id_card) not in candidatos:
            candidatos.append(int(id_card))

    cab = cabecalho_solicitacao or {}
    adicionar(cab.get("IDFatoKanbanCard"))

    for item in itens_solicitacao or []:
        adicionar((item or {}).get("IDFatoKanbanCard"))

    if not candidatos:
        return None

    for id_card in candidatos:
        if _card_possui_tag_ativa_admin(id_card, ID_TAG_RENOVACAO_CAMPANHA):
            return int(id_card)

    return int(candidatos[0])




def _limitar_texto_aprovacao_admin(valor, tamanho: int | None) -> str | None:
    """Limita texto antes de gravar em colunas curtas do contrato.

    Evita erro SQL Server 2628 (String or binary data would be truncated),
    principalmente na coluna OBS de FatoControleContratosItensEuromidia.
    """
    if valor is None:
        return None

    texto = str(valor).replace("\x00", "").strip()
    if not texto:
        return None

    if tamanho in (None, "", 0):
        return texto

    try:
        tamanho_int = int(tamanho)
    except Exception:
        return texto

    if tamanho_int <= 0:
        return None

    return texto[:tamanho_int]


def _obs_item_controle_aprovacao_admin(valor) -> str | None:
    """OBS segura para FatoControleContratosItensEuromidia.

    No banco atual a coluna OBS é curta. Para renovação, preservo o identificador
    técnico principal e removo texto narrativo longo que quebra a aprovação.
    """
    texto = str(valor or "").replace("\x00", "").strip()
    if not texto:
        return None

    match_vencimento = re.search(r"RENOVACAO_CAMPANHA_ID_VENCIMENTO\s*=\s*(\d+)", texto, flags=re.IGNORECASE)
    if match_vencimento:
        id_vencimento = match_vencimento.group(1)
        return _limitar_texto_aprovacao_admin(f"RENOVACAO_CAMPANHA_ID_VENCIMENTO={id_vencimento}", 100)

    return _limitar_texto_aprovacao_admin(texto, 100)


def _extrair_ids_renovacao_texto_admin(texto: str | None) -> dict:
    """Extrai IDs técnicos do texto do card/OBS de renovação."""
    conteudo = str(texto or "")
    retorno = {
        "id_vencimento": None,
        "id_contrato_origem": None,
        "id_item_origem": None,
    }

    match_vencimento = re.search(r"RENOVACAO_CAMPANHA_ID_VENCIMENTO\s*=\s*(\d+)", conteudo, flags=re.IGNORECASE)
    if match_vencimento:
        retorno["id_vencimento"] = int(match_vencimento.group(1))

    match_contrato = re.search(r"Contrato\s+origem\s*:\s*(\d+)", conteudo, flags=re.IGNORECASE)
    if match_contrato:
        retorno["id_contrato_origem"] = int(match_contrato.group(1))

    match_item = re.search(r"Item\s+origem\s*:\s*(\d+)", conteudo, flags=re.IGNORECASE)
    if match_item:
        retorno["id_item_origem"] = int(match_item.group(1))

    return retorno


def _buscar_origem_renovacao_por_vencimento_admin(id_vencimento: int | None) -> dict | None:
    """Busca contrato/item original da renovação pela tabela oficial de vencimentos."""
    id_vencimento_int = _int_ou_none(id_vencimento)
    if id_vencimento_int in (None, "", 0):
        return None

    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   venc.IDFatoVencimentoCampanhaEuromidia,
                   venc.IDFatoControleContratosEuromidia,
                   venc.IDFatoControleContratosItensEuromidia,
                   item.CodPonto,
                   item.CodFace,
                   item.BitAtivo AS BitAtivoItemOrigem,
                   item.IDDimOrigemAtendimento AS IDDimOrigemAtendimentoItem,
                   emp.IDDimOrigemAtendimento AS IDDimOrigemAtendimentoEmpresa
            FROM {TABELA_VENCIMENTO_CAMPANHA} AS venc
            LEFT JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item
                ON item.IDFatoControleContratosItensEuromidia = venc.IDFatoControleContratosItensEuromidia
            LEFT JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
                ON ctr.IDFatoControleContratosEuromidia = venc.IDFatoControleContratosEuromidia
            LEFT JOIN [Integracao].[Silver].[DimEmpresas] AS emp
                ON emp.IDEmpresa = COALESCE(venc.IDEmpresa, ctr.IDEmpresa)
            WHERE venc.IDFatoVencimentoCampanhaEuromidia = :id_vencimento
            ORDER BY venc.IDFatoVencimentoCampanhaEuromidia DESC;
        """),
        {"id_vencimento": int(id_vencimento_int)},
    ).mappings().first()

    return dict(row) if row else None


def _buscar_origem_renovacao_por_item_admin(id_item_controle: int | None) -> dict | None:
    """Busca contrato/item original diretamente pelo ID do item de controle."""
    id_item_int = _int_ou_none(id_item_controle)
    if id_item_int in (None, "", 0):
        return None

    row = db.session.execute(
        text("""
            SELECT TOP (1)
                   CAST(NULL AS int) AS IDFatoVencimentoCampanhaEuromidia,
                   item.IDFatoControleContratoEuromidia AS IDFatoControleContratosEuromidia,
                   item.IDFatoControleContratosItensEuromidia,
                   item.CodPonto,
                   item.CodFace,
                   item.BitAtivo AS BitAtivoItemOrigem,
                   item.IDDimOrigemAtendimento AS IDDimOrigemAtendimentoItem
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item WITH (UPDLOCK, HOLDLOCK)
            WHERE item.IDFatoControleContratosItensEuromidia = :id_item_controle
            ORDER BY item.IDFatoControleContratosItensEuromidia DESC;
        """),
        {"id_item_controle": int(id_item_int)},
    ).mappings().first()

    return dict(row) if row else None


def _buscar_origem_renovacao_por_card_painel_face_admin(id_card: int | None, cod_ponto=None, cod_face=None) -> dict | None:
    """Busca IDs originais gravados na linha operacional de painel/face do card."""
    id_card_int = _int_ou_none(id_card)
    if id_card_int in (None, "", 0):
        return None

    tabela_pf = TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO

    expr_id_contrato = "CAST(NULL AS int)"
    if _campanhas_vencimentos_coluna_existe(tabela_pf, "IDFatoControleContratosEuromidia"):
        expr_id_contrato = "TRY_CONVERT(int, pf.IDFatoControleContratosEuromidia)"
    elif _campanhas_vencimentos_coluna_existe(tabela_pf, "IDFatoControleContratoEuromidia"):
        expr_id_contrato = "TRY_CONVERT(int, pf.IDFatoControleContratoEuromidia)"

    expr_id_item = "CAST(NULL AS int)"
    if _campanhas_vencimentos_coluna_existe(tabela_pf, "IDFatoControleContratosItensEuromidia"):
        expr_id_item = "TRY_CONVERT(int, pf.IDFatoControleContratosItensEuromidia)"
    elif _campanhas_vencimentos_coluna_existe(tabela_pf, "IDFatoControleContratoItemEuromidia"):
        expr_id_item = "TRY_CONVERT(int, pf.IDFatoControleContratoItemEuromidia)"

    expr_cod_ponto = "CAST(NULL AS varchar(80))"
    if _campanhas_vencimentos_coluna_existe(tabela_pf, "CodPonto"):
        expr_cod_ponto = "pf.CodPonto"

    expr_cod_face = "CAST(NULL AS varchar(80))"
    if _campanhas_vencimentos_coluna_existe(tabela_pf, "CodFace"):
        expr_cod_face = "pf.CodFace"

    filtros = ["pf.IDFatoKanbanCard = :id_card"]
    params = {
        "id_card": int(id_card_int),
        "cod_ponto": _texto_ou_none(cod_ponto),
        "cod_face": str(cod_face or "").strip().upper() if cod_face not in (None, "") else None,
    }

    if _campanhas_vencimentos_coluna_existe(tabela_pf, "Ativo"):
        filtros.append("ISNULL(pf.Ativo, 1) = 1")

    filtro_face = ""
    if params["cod_face"]:
        filtro_face = f"""
            AND (
                UPPER(LTRIM(RTRIM(ISNULL(CONVERT(varchar(80), {expr_cod_face}), '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
                OR {expr_id_item} IS NOT NULL
            )
        """

    ordem_prioridade_item = ""
    if not expr_id_item.upper().startswith("CAST(NULL"):
        ordem_prioridade_item = f"CASE WHEN {expr_id_item} IS NOT NULL THEN 0 ELSE 1 END,"

    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   CAST(NULL AS int) AS IDFatoVencimentoCampanhaEuromidia,
                   {expr_id_contrato} AS IDFatoControleContratosEuromidia,
                   {expr_id_item} AS IDFatoControleContratosItensEuromidia,
                   {expr_cod_ponto} AS CodPonto,
                   {expr_cod_face} AS CodFace,
                   CAST(NULL AS int) AS BitAtivoItemOrigem
            FROM {tabela_pf} AS pf
            WHERE {' AND '.join(filtros)}
            {filtro_face}
            ORDER BY
                {ordem_prioridade_item}
                pf.IDFatoKanbanCardPainelFace DESC;
        """),
        params,
    ).mappings().first()

    if not row:
        return None

    dados = dict(row)
    if _int_ou_none(dados.get("IDFatoControleContratosItensEuromidia")):
        return dados

    return None


def _buscar_origem_renovacao_por_contrato_face_admin(
    *,
    id_contrato_controle: int | None,
    cod_ponto,
    cod_face,
) -> dict | None:
    """Último fallback: acha item ativo do contrato pela face."""
    id_contrato_int = _int_ou_none(id_contrato_controle)
    cod_face_norm = str(cod_face or "").strip().upper()
    if id_contrato_int in (None, "", 0) or not cod_face_norm:
        return None

    row = db.session.execute(
        text("""
            SELECT TOP (1)
                   CAST(NULL AS int) AS IDFatoVencimentoCampanhaEuromidia,
                   item.IDFatoControleContratoEuromidia AS IDFatoControleContratosEuromidia,
                   item.IDFatoControleContratosItensEuromidia,
                   item.CodPonto,
                   item.CodFace,
                   item.BitAtivo AS BitAtivoItemOrigem,
                   item.IDDimOrigemAtendimento AS IDDimOrigemAtendimentoItem
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item WITH (UPDLOCK, HOLDLOCK)
            WHERE item.IDFatoControleContratoEuromidia = :id_contrato_controle
              AND ISNULL(item.BitAtivo, 1) = 1
              AND UPPER(LTRIM(RTRIM(CONVERT(varchar(80), item.CodFace)))) = UPPER(LTRIM(RTRIM(:cod_face)))
              AND (
                    :cod_ponto IS NULL
                    OR LTRIM(RTRIM(CONVERT(varchar(80), item.CodPonto))) = LTRIM(RTRIM(CONVERT(varchar(80), :cod_ponto)))
                  )
            ORDER BY item.IDFatoControleContratosItensEuromidia DESC;
        """),
        {
            "id_contrato_controle": int(id_contrato_int),
            "cod_ponto": _texto_ou_none(cod_ponto),
            "cod_face": cod_face_norm,
        },
    ).mappings().first()

    return dict(row) if row else None


def _resolver_origem_renovacao_aprovacao_admin(
    *,
    id_card: int | None,
    cabecalho_solicitacao: dict | None = None,
    item_solicitacao: dict | None = None,
    id_contrato_controle: int | None = None,
    cod_ponto=None,
    cod_face=None,
) -> dict:
    """Resolve a origem oficial da renovação antes de aprovar.

    Prioridade:
    1) marcador RENOVACAO_CAMPANHA_ID_VENCIMENTO no card/OBS;
    2) IDFatoControleContratosItensEuromidia já gravado na solicitação;
    3) vínculo KanbanCardPainelFace;
    4) item ativo do contrato pela face.
    """
    cab = cabecalho_solicitacao or {}
    item = item_solicitacao or {}

    textos = [
        cab.get("Descricao"),
        cab.get("OBS"),
        item.get("Descricao"),
        item.get("OBS"),
    ]

    id_card_int = _int_ou_none(id_card)
    if id_card_int not in (None, "", 0):
        row_card = db.session.execute(
            text(f"""
                SELECT TOP (1)
                       Descricao
                FROM {TABELA_KANBAN_CARD_RENOVACAO}
                WHERE IDFatoKanbanCard = :id_card
            """),
            {"id_card": int(id_card_int)},
        ).mappings().first()
        if row_card:
            textos.append(row_card.get("Descricao"))

    ids_texto = {"id_vencimento": None, "id_contrato_origem": None, "id_item_origem": None}
    for texto_livre in textos:
        ids_extraidos = _extrair_ids_renovacao_texto_admin(texto_livre)
        for chave, valor in ids_extraidos.items():
            if ids_texto.get(chave) in (None, "", 0) and valor not in (None, "", 0):
                ids_texto[chave] = int(valor)

    origem = _buscar_origem_renovacao_por_vencimento_admin(ids_texto.get("id_vencimento"))
    if origem:
        origem["fonte_origem_renovacao"] = "FatoVencimentoCampanhaEuromidia"
        return origem

    origem = _buscar_origem_renovacao_por_item_admin(
        ids_texto.get("id_item_origem")
        or item.get("IDFatoControleContratosItensEuromidia")
    )
    if origem:
        origem["fonte_origem_renovacao"] = "ItemOrigem"
        return origem

    origem = _buscar_origem_renovacao_por_card_painel_face_admin(
        id_card=id_card_int,
        cod_ponto=cod_ponto or item.get("CodPonto"),
        cod_face=cod_face or item.get("CodFace"),
    )
    if origem:
        origem["fonte_origem_renovacao"] = "KanbanCardPainelFace"
        return origem

    origem = _buscar_origem_renovacao_por_contrato_face_admin(
        id_contrato_controle=ids_texto.get("id_contrato_origem") or id_contrato_controle or cab.get("IDFatoControleContratosEuromidia"),
        cod_ponto=cod_ponto or item.get("CodPonto"),
        cod_face=cod_face or item.get("CodFace"),
    )
    if origem:
        origem["fonte_origem_renovacao"] = "ContratoFace"
        return origem

    return {}


def _resolver_id_origem_atendimento_aprovacao_admin(
    *,
    cabecalho_solicitacao: dict | None = None,
    item_solicitacao: dict | None = None,
    origem_renovacao: dict | None = None,
    id_card: int | None = None,
) -> int | None:
    """Resolve o IDDimOrigemAtendimento que deve ir para o item/ocupação aprovado.

    Ordem de confiança:
    1. item da solicitação;
    2. cabeçalho da solicitação;
    3. item/empresa de origem da renovação;
    4. cadastro do card;
    5. cadastro da empresa.
    """

    cab = cabecalho_solicitacao or {}
    item = item_solicitacao or {}
    origem = origem_renovacao or {}

    candidatos = (
        item.get("IDDimOrigemAtendimento"),
        item.get("IDOrigemAtendimento"),
        cab.get("IDDimOrigemAtendimento"),
        cab.get("IDOrigemAtendimento"),
        origem.get("IDDimOrigemAtendimentoItem"),
        origem.get("IDDimOrigemAtendimentoEmpresa"),
        origem.get("IDOrigemAtendimento"),
    )

    for valor in candidatos:
        inteiro = _int_ou_none(valor)
        if inteiro not in (None, "", 0):
            return int(inteiro)

    id_card_int = _int_ou_none(id_card or item.get("IDFatoKanbanCard") or cab.get("IDFatoKanbanCard"))
    if id_card_int not in (None, "", 0) and _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDDimOrigemAtendimento"):
        valor_card = db.session.execute(
            text(f"""
                SELECT TOP (1) IDDimOrigemAtendimento
                FROM {TABELA_KANBAN_CARD_RENOVACAO}
                WHERE IDFatoKanbanCard = :id_card;
            """),
            {"id_card": int(id_card_int)},
        ).scalar()

        inteiro = _int_ou_none(valor_card)
        if inteiro not in (None, "", 0):
            return int(inteiro)

    id_empresa = _int_ou_none(item.get("IDEmpresa") or cab.get("IDEmpresa") or origem.get("IDEmpresa"))
    if id_empresa not in (None, "", 0) and _campanhas_vencimentos_coluna_existe("[Integracao].[Silver].[DimEmpresas]", "IDDimOrigemAtendimento"):
        valor_empresa = db.session.execute(
            text("""
                SELECT TOP (1) IDDimOrigemAtendimento
                FROM [Integracao].[Silver].[DimEmpresas]
                WHERE IDEmpresa = :id_empresa;
            """),
            {"id_empresa": int(id_empresa)},
        ).scalar()

        inteiro = _int_ou_none(valor_empresa)
        if inteiro not in (None, "", 0):
            return int(inteiro)

    return None


def _buscar_item_controle_existente_aprovacao_admin(
    *,
    id_contrato_controle: int,
    id_item_controle_origem: int | None,
    cod_ponto,
    cod_face,
    somente_ativos: bool = False,
) -> dict | None:
    """Busca o item oficial existente do contrato para atualizar ou substituir.

    Para renovação, primeiro procuro item ativo. Assim eu inativo a linha atualmente válida
    e depois insiro a nova linha aprovada, sem reaproveitar item antigo inativo por engano.
    """

    id_item_controle_origem_int = _int_ou_none(id_item_controle_origem)
    ordem_prioridade_item = ""
    if id_item_controle_origem_int not in (None, "", 0):
        ordem_prioridade_item = "CASE WHEN i.IDFatoControleContratosItensEuromidia = :id_item_controle_origem THEN 0 ELSE 1 END,"

    sql = text(f"""
        SELECT TOP 1
               i.IDFatoControleContratosItensEuromidia,
               i.Referencia AS ReferenciaAtual,
               ISNULL(i.BitAtivo, 1) AS BitAtivoAtual
        FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i WITH (UPDLOCK, HOLDLOCK)
        WHERE i.IDFatoControleContratoEuromidia = :id_contrato_controle
          AND (:somente_ativos = 0 OR ISNULL(i.BitAtivo, 1) = 1)
          AND (
                (:id_item_controle_origem IS NOT NULL AND i.IDFatoControleContratosItensEuromidia = :id_item_controle_origem)
                OR
                (
                    ISNULL(LTRIM(RTRIM(CAST(i.CodPonto AS varchar(60)))), '') = ISNULL(LTRIM(RTRIM(CAST(:cod_ponto AS varchar(60)))), '')
                    AND ISNULL(UPPER(LTRIM(RTRIM(CAST(i.CodFace AS varchar(60))))), '') = ISNULL(UPPER(LTRIM(RTRIM(CAST(:cod_face AS varchar(60))))), '')
                )
              )
        ORDER BY
            {ordem_prioridade_item}
            CASE WHEN ISNULL(i.BitAtivo, 1) = 1 THEN 0 ELSE 1 END,
            i.IDFatoControleContratosItensEuromidia DESC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_contrato_controle": int(id_contrato_controle),
            "id_item_controle_origem": int(id_item_controle_origem_int) if id_item_controle_origem_int not in (None, "", 0) else None,
            "cod_ponto": cod_ponto,
            "cod_face": cod_face,
            "somente_ativos": 1 if somente_ativos else 0,
        },
    ).mappings().first()

    if row:
        return dict(row)

    if somente_ativos:
        return _buscar_item_controle_existente_aprovacao_admin(
            id_contrato_controle=id_contrato_controle,
            id_item_controle_origem=id_item_controle_origem,
            cod_ponto=cod_ponto,
            cod_face=cod_face,
            somente_ativos=False,
        )

    return None


def _obter_cabecalho_solicitacao_bruta(id_solicitacao: int) -> dict | None:
    row = db.session.execute(
        text("""
            SELECT TOP 1 *
            FROM [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
            WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
        """),
        {"id_solicitacao": int(id_solicitacao)},
    ).mappings().first()
    return dict(row) if row else None





def _obter_itens_solicitacao_brutos(id_solicitacao: int) -> list[dict]:
    rows = db.session.execute(
        text("""
            SELECT *
            FROM [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]
            WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
            ORDER BY IDFatoSolicitacaoContratoItemEuromidia ASC
        """),
        {"id_solicitacao": int(id_solicitacao)},
    ).mappings().all()

    cab = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao)) or {}
    itens: list[dict] = []
    for row in rows:
        item = dict(row)
        item["_DataAssinaturaRenovacaoInformadaUsuario"] = item.get("DataAssinaturaRenovacao")
        item["_DataInicioPrevistoInformadaUsuario"] = item.get("DataInicioPrevisto")
        item["_DataTerminoPrevistoInformadaUsuario"] = item.get("DataTerminoPrevisto")
        item = _aplicar_fallback_layout_item_solicitacao(item, cab)
        item["CodPonto"] = _texto_ou_none(item.get("CodPonto") or item.get("CodPontoOriginal"))
        item["CodFace"] = _texto_ou_none(item.get("CodFace") or item.get("CodFaceOriginal"))
        if item.get("CodFace"):
            item["CodFace"] = str(item["CodFace"]).strip().upper()
        if _valor_esta_vazio_para_fallback(item.get("Status")):
            item["Status"] = "ATIVO"
        itens.append(item)

    return itens






def _upsert_item_controle_a_partir_item_solicitacao(
    *,
    id_fato_controle_contratos: int,
    item_solicitacao: dict,
    referencia_padrao: str | None,
) -> int:
    """
    Eu insiro ou atualizo o item do contrato na tabela de controle.
    A lógica é:
    - se já existir item de controle para o mesmo contrato + CodPonto + CodFace, eu atualizo
    - se não existir, eu insiro
    - se o item da solicitação já vier com IDFatoControleContratosItensEuromidia, eu priorizo esse vínculo
    """

    id_item_controle_origem = _int_ou_none(item_solicitacao.get("IDFatoControleContratosItensEuromidia"))
    cod_ponto = item_solicitacao.get("CodPonto")
    cod_face = item_solicitacao.get("CodFace")
    eh_renovacao_item = str(item_solicitacao.get("InicioRenovacao") or "").strip().upper() == "R"
    data_aprovacao_sql_server = _obter_data_aprovacao_sql_server_admin()
    periodo_item_aprovacao = _resolver_periodo_item_aprovacao_admin(
        item_solicitacao=item_solicitacao,
        data_aprovacao_sql_server=data_aprovacao_sql_server,
        eh_renovacao=eh_renovacao_item,
        id_solicitacao=item_solicitacao.get("IDFatoSolicitacaoContratoEuromidia"),
        id_item_solicitacao=item_solicitacao.get("IDFatoSolicitacaoContratoItemEuromidia"),
    )

    row_existente = db.session.execute(
        text("""
            SELECT TOP 1
                   i.IDFatoControleContratosItensEuromidia,
                   i.Referencia AS ReferenciaAtual
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
            WHERE i.IDFatoControleContratoEuromidia = :id_fato_controle_contratos
              AND (
                    i.IDFatoControleContratosItensEuromidia = :id_item_controle_origem
                    OR
                    (
                        ISNULL(LTRIM(RTRIM(CAST(i.CodPonto AS varchar(60)))), '') = ISNULL(LTRIM(RTRIM(CAST(:cod_ponto AS varchar(60)))), '')
                        AND ISNULL(UPPER(LTRIM(RTRIM(CAST(i.CodFace AS varchar(60))))), '') = ISNULL(UPPER(LTRIM(RTRIM(CAST(:cod_face AS varchar(60))))), '')
                    )
                  )
            ORDER BY i.IDFatoControleContratosItensEuromidia DESC
        """),
        {
            "id_fato_controle_contratos": int(id_fato_controle_contratos),
            "id_item_controle_origem": int(id_item_controle_origem) if id_item_controle_origem not in (None, "", 0) else None,
            "cod_ponto": cod_ponto,
            "cod_face": cod_face,
        },
    ).mappings().first()

    id_item_controle_existente = (
        int(row_existente["IDFatoControleContratosItensEuromidia"])
        if row_existente and row_existente.get("IDFatoControleContratosItensEuromidia") is not None
        else None
    )

    referencia_item_resolvida = _resolver_referencia_item_controle(
        id_fato_controle_contratos=int(id_fato_controle_contratos),
        id_item_controle_atual=id_item_controle_existente,
        id_item_solicitacao=item_solicitacao.get("IDFatoSolicitacaoContratoItemEuromidia"),
        referencia_informada=item_solicitacao.get("Referencia"),
        referencia_contrato=referencia_padrao,
        referencia_atual=(row_existente or {}).get("ReferenciaAtual"),
        cod_ponto=cod_ponto,
        cod_face=cod_face,
        id_painel=item_solicitacao.get("IDPainelEuromidia"),
        id_face=item_solicitacao.get("IDDimFacesPaineis"),
        cnpj=item_solicitacao.get("CNPJ"),
    )

    params_item = {
        "IDFatoControleContratoEuromidia": int(id_fato_controle_contratos),
        "Referencia": referencia_item_resolvida,
        "NumeroContrato": item_solicitacao.get("NumeroContrato"),
        "NumeroPrevia": item_solicitacao.get("NumeroPrevia"),
        "CNPJ": item_solicitacao.get("CNPJ"),
        "CodPonto": item_solicitacao.get("CodPonto"),
        "CodFace": item_solicitacao.get("CodFace"),
        "DataLancamento": item_solicitacao.get("DataLancamento"),
        "Cota": item_solicitacao.get("Cota") or item_solicitacao.get("ExibicoesDia") or item_solicitacao.get("exibicoes_dia"),
        "CidadeExibicao": item_solicitacao.get("CidadeExibicao"),
        "Tipo": item_solicitacao.get("Tipo"),
        "Origem": item_solicitacao.get("Origem"),
        "EmpresaEuro": item_solicitacao.get("EmpresaEuro"),
        "CnpjExibibora": item_solicitacao.get("CnpjExibibora"),
        "TipoDocumento": item_solicitacao.get("TipoDocumento"),
        "RazaoSocial": item_solicitacao.get("RazaoSocial"),
        "CPF": item_solicitacao.get("CPF"),
        "MarcaExibida": item_solicitacao.get("MarcaExibida"),
        "Vendedor": item_solicitacao.get("Vendedor"),
        "SDR": item_solicitacao.get("SDR"),
        "Agencia": item_solicitacao.get("Agencia"),
        "CnpjAgencia": item_solicitacao.get("CnpjAgencia"),
        "Bureau": item_solicitacao.get("Bureau"),
        "CnpjBureau": item_solicitacao.get("CnpjBureau"),
        "Intermediario": item_solicitacao.get("Intermediario"),
        "CnpjIntermediario": item_solicitacao.get("CnpjIntermediario"),
        "DataAssinaturaRenovacao": periodo_item_aprovacao["DataAssinaturaRenovacao"],
        "IDTrimestre": item_solicitacao.get("IDTrimestre"),
        "TexmpoExposicao": item_solicitacao.get("TexmpoExposicao"),
        "DataInicioPrevisto": periodo_item_aprovacao["DataInicioPrevisto"],
        "DataTerminoPrevisto": periodo_item_aprovacao["DataTerminoPrevisto"],
        "InicioRenovacao": item_solicitacao.get("InicioRenovacao"),
        "FaturamentoBrutoMensal": item_solicitacao.get("FaturamentoBrutoMensal"),
        "PercentualPermuta": item_solicitacao.get("PercentualPermuta"),
        "CotaOportunidade": item_solicitacao.get("CotaOportunidade"),
        "ValorPermuta": item_solicitacao.get("ValorPermuta"),
        "FaturamentoLiquidoPermuta": item_solicitacao.get("FaturamentoLiquidoPermuta"),
        "NumeroParcelas": item_solicitacao.get("NumeroParcelas"),
        "DataInicioVencimento": item_solicitacao.get("DataInicioVencimento"),
        "TotalBrutoContrato": item_solicitacao.get("TotalBrutoContrato"),
        "TotalLiquidoContratoAGBRCTACORDO": item_solicitacao.get("TotalLiquidoContratoAGBRCTACORDO"),
        "TotalLiquidoContratoAGBRVENDGERCOOR": item_solicitacao.get("TotalLiquidoContratoAGBRVENDGERCOOR"),
        "PercentualAgencia": item_solicitacao.get("PercentualAgencia"),
        "ValorMensalAgencia": item_solicitacao.get("ValorMensalAgencia"),
        "PercentualBureau": item_solicitacao.get("PercentualBureau"),
        "ValorBureauMensal": item_solicitacao.get("ValorBureauMensal"),
        "PercentualCartaAcordo": item_solicitacao.get("PercentualCartaAcordo"),
        "ValorCartaAcordoMensal": item_solicitacao.get("ValorCartaAcordoMensal"),
        "ValorOutrasComissoes": item_solicitacao.get("ValorOutrasComissoes"),
        "FaturamentoLiquidoMensal": item_solicitacao.get("FaturamentoLiquidoMensal"),
        "PercentualComissaoVendedor": item_solicitacao.get("PercentualComissaoVendedor"),
        "ValorVendedor": item_solicitacao.get("ValorVendedor"),
        "ValorVendedorTotal": item_solicitacao.get("ValorVendedorTotal"),
        "PercentualComissaoCoordenacao": item_solicitacao.get("PercentualComissaoCoordenacao"),
        "ValorCoordenador": item_solicitacao.get("ValorCoordenador"),
        "ValorCoordenadorTotal": item_solicitacao.get("ValorCoordenadorTotal"),
        "PercentualComissaoGerencia": item_solicitacao.get("PercentualComissaoGerencia"),
        "ValorGerencia": item_solicitacao.get("ValorGerencia"),
        "ValorGerenciaTotal": item_solicitacao.get("ValorGerenciaTotal"),
        "AtivoCancelamento": _normalizar_ativo_cancelamento_aprovacao(item_solicitacao.get("AtivoCancelamento")),
        "FaturamentoLiquidoFinalMensal": item_solicitacao.get("FaturamentoLiquidoFinalMensal"),
        "ComissaoGerenciaNordeste": item_solicitacao.get("ComissaoGerenciaNordeste"),
        "Faturamento": item_solicitacao.get("Faturamento"),
        "DataCancelamento": item_solicitacao.get("DataCancelamento"),
        "OBS": _obs_item_controle_aprovacao_admin(item_solicitacao.get("OBS")),
        "IDVendedor": item_solicitacao.get("IDVendedor"),
        "IDPainelEuromidia": item_solicitacao.get("IDPainelEuromidia"),
        "IDDimFacesPaineis": item_solicitacao.get("IDDimFacesPaineis"),
        "DataFimEfetiva": item_solicitacao.get("DataFimEfetiva"),
        "Status": _normalizar_status_item_aprovacao(item_solicitacao.get("Status")),
        "IDDimCheckinHistorico": item_solicitacao.get("IDDimCheckinHistorico"),
        "IDFatoKanbanCard": item_solicitacao.get("IDFatoKanbanCard"),
        "BitAtivo": 1,
        "IDEmpresaAgencia": item_solicitacao.get("IDEmpresaAgencia"),
    }

    if row_existente and row_existente.get("IDFatoControleContratosItensEuromidia") is not None:
        id_item_controle = int(row_existente["IDFatoControleContratosItensEuromidia"])

        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                   SET IDFatoControleContratoEuromidia = :IDFatoControleContratoEuromidia,
                       DataAtualizacao = GETDATE(),
                       Referencia = :Referencia,
                       NumeroContrato = :NumeroContrato,
                       NumeroPrevia = :NumeroPrevia,
                       CNPJ = :CNPJ,
                       CodPonto = :CodPonto,
                       CodFace = :CodFace,
                       DataLancamento = :DataLancamento,
                       Cota = :Cota,
                       CidadeExibicao = :CidadeExibicao,
                       Tipo = :Tipo,
                       Origem = :Origem,
                       EmpresaEuro = :EmpresaEuro,
                       CnpjExibibora = :CnpjExibibora,
                       TipoDocumento = :TipoDocumento,
                       RazaoSocial = :RazaoSocial,
                       CPF = :CPF,
                       MarcaExibida = :MarcaExibida,
                       Vendedor = :Vendedor,
                       SDR = :SDR,
                       Agencia = :Agencia,
                       CnpjAgencia = :CnpjAgencia,
                       Bureau = :Bureau,
                       CnpjBureau = :CnpjBureau,
                       Intermediario = :Intermediario,
                       CnpjIntermediario = :CnpjIntermediario,
                       DataAssinaturaRenovacao = :DataAssinaturaRenovacao,
                       IDTrimestre = :IDTrimestre,
                       TexmpoExposicao = :TexmpoExposicao,
                       DataInicioPrevisto = :DataInicioPrevisto,
                       DataTerminoPrevisto = :DataTerminoPrevisto,
                       InicioRenovacao = :InicioRenovacao,
                       FaturamentoBrutoMensal = :FaturamentoBrutoMensal,
                       PercentualPermuta = :PercentualPermuta,
                       CotaOportunidade = :CotaOportunidade,
                       ValorPermuta = :ValorPermuta,
                       FaturamentoLiquidoPermuta = :FaturamentoLiquidoPermuta,
                       NumeroParcelas = :NumeroParcelas,
                       DataInicioVencimento = :DataInicioVencimento,
                       TotalBrutoContrato = :TotalBrutoContrato,
                       TotalLiquidoContratoAGBRCTACORDO = :TotalLiquidoContratoAGBRCTACORDO,
                       TotalLiquidoContratoAGBRVENDGERCOOR = :TotalLiquidoContratoAGBRVENDGERCOOR,
                       PercentualAgencia = :PercentualAgencia,
                       ValorMensalAgencia = :ValorMensalAgencia,
                       PercentualBureau = :PercentualBureau,
                       ValorBureauMensal = :ValorBureauMensal,
                       PercentualCartaAcordo = :PercentualCartaAcordo,
                       ValorCartaAcordoMensal = :ValorCartaAcordoMensal,
                       ValorOutrasComissoes = :ValorOutrasComissoes,
                       FaturamentoLiquidoMensal = :FaturamentoLiquidoMensal,
                       PercentualComissaoVendedor = :PercentualComissaoVendedor,
                       ValorVendedor = :ValorVendedor,
                       ValorVendedorTotal = :ValorVendedorTotal,
                       PercentualComissaoCoordenacao = :PercentualComissaoCoordenacao,
                       ValorCoordenador = :ValorCoordenador,
                       ValorCoordenadorTotal = :ValorCoordenadorTotal,
                       PercentualComissaoGerencia = :PercentualComissaoGerencia,
                       ValorGerencia = :ValorGerencia,
                       ValorGerenciaTotal = :ValorGerenciaTotal,
                       AtivoCancelamento = :AtivoCancelamento,
                       FaturamentoLiquidoFinalMensal = :FaturamentoLiquidoFinalMensal,
                       ComissaoGerenciaNordeste = :ComissaoGerenciaNordeste,
                       Faturamento = :Faturamento,
                       DataCancelamento = :DataCancelamento,
                       OBS = :OBS,
                       IDVendedor = :IDVendedor,
                       IDPainelEuromidia = :IDPainelEuromidia,
                       IDDimFacesPaineis = :IDDimFacesPaineis,
                       Status = :Status,
                       IDDimCheckinHistorico = :IDDimCheckinHistorico,
                       IDFatoKanbanCard = :IDFatoKanbanCard,
                       BitAtivo = :BitAtivo,
                       IDEmpresaAgencia = :IDEmpresaAgencia
                 WHERE IDFatoControleContratosItensEuromidia = :id_item_controle
            """),
            {**params_item, "id_item_controle": id_item_controle},
        )

        return id_item_controle

    row_novo = db.session.execute(
        text("""
            INSERT INTO [Integracao].[Silver].[FatoControleContratosItensEuromidia]
            (
                IDFatoControleContratoEuromidia,
                DataAtualizacao,
                Referencia,
                NumeroContrato,
                NumeroPrevia,
                CNPJ,
                CodPonto,
                CodFace,
                DataLancamento,
                Cota,
                CidadeExibicao,
                Tipo,
                Origem,
                EmpresaEuro,
                CnpjExibibora,
                TipoDocumento,
                RazaoSocial,
                CPF,
                MarcaExibida,
                Vendedor,
                SDR,
                Agencia,
                CnpjAgencia,
                Bureau,
                CnpjBureau,
                Intermediario,
                CnpjIntermediario,
                DataAssinaturaRenovacao,
                IDTrimestre,
                TexmpoExposicao,
                DataInicioPrevisto,
                DataTerminoPrevisto,
                InicioRenovacao,
                FaturamentoBrutoMensal,
                PercentualPermuta,
                CotaOportunidade,
                ValorPermuta,
                FaturamentoLiquidoPermuta,
                NumeroParcelas,
                DataInicioVencimento,
                TotalBrutoContrato,
                TotalLiquidoContratoAGBRCTACORDO,
                TotalLiquidoContratoAGBRVENDGERCOOR,
                PercentualAgencia,
                ValorMensalAgencia,
                PercentualBureau,
                ValorBureauMensal,
                PercentualCartaAcordo,
                ValorCartaAcordoMensal,
                ValorOutrasComissoes,
                FaturamentoLiquidoMensal,
                PercentualComissaoVendedor,
                ValorVendedor,
                ValorVendedorTotal,
                PercentualComissaoCoordenacao,
                ValorCoordenador,
                ValorCoordenadorTotal,
                PercentualComissaoGerencia,
                ValorGerencia,
                ValorGerenciaTotal,
                AtivoCancelamento,
                FaturamentoLiquidoFinalMensal,
                ComissaoGerenciaNordeste,
                Faturamento,
                DataCancelamento,
                OBS,
                IDVendedor,
                IDPainelEuromidia,
                IDDimFacesPaineis,
                Status,
                IDDimCheckinHistorico,
                IDFatoKanbanCard,
                BitAtivo,
                IDEmpresaAgencia
            )
            OUTPUT INSERTED.IDFatoControleContratosItensEuromidia AS id_item_controle
            VALUES
            (
                :IDFatoControleContratoEuromidia,
                GETDATE(),
                :Referencia,
                :NumeroContrato,
                :NumeroPrevia,
                :CNPJ,
                :CodPonto,
                :CodFace,
                :DataLancamento,
                :Cota,
                :CidadeExibicao,
                :Tipo,
                :Origem,
                :EmpresaEuro,
                :CnpjExibibora,
                :TipoDocumento,
                :RazaoSocial,
                :CPF,
                :MarcaExibida,
                :Vendedor,
                :SDR,
                :Agencia,
                :CnpjAgencia,
                :Bureau,
                :CnpjBureau,
                :Intermediario,
                :CnpjIntermediario,
                :DataAssinaturaRenovacao,
                :IDTrimestre,
                :TexmpoExposicao,
                :DataInicioPrevisto,
                :DataTerminoPrevisto,
                :InicioRenovacao,
                :FaturamentoBrutoMensal,
                :PercentualPermuta,
                :CotaOportunidade,
                :ValorPermuta,
                :FaturamentoLiquidoPermuta,
                :NumeroParcelas,
                :DataInicioVencimento,
                :TotalBrutoContrato,
                :TotalLiquidoContratoAGBRCTACORDO,
                :TotalLiquidoContratoAGBRVENDGERCOOR,
                :PercentualAgencia,
                :ValorMensalAgencia,
                :PercentualBureau,
                :ValorBureauMensal,
                :PercentualCartaAcordo,
                :ValorCartaAcordoMensal,
                :ValorOutrasComissoes,
                :FaturamentoLiquidoMensal,
                :PercentualComissaoVendedor,
                :ValorVendedor,
                :ValorVendedorTotal,
                :PercentualComissaoCoordenacao,
                :ValorCoordenador,
                :ValorCoordenadorTotal,
                :PercentualComissaoGerencia,
                :ValorGerencia,
                :ValorGerenciaTotal,
                :AtivoCancelamento,
                :FaturamentoLiquidoFinalMensal,
                :ComissaoGerenciaNordeste,
                :Faturamento,
                :DataCancelamento,
                :OBS,
                :IDVendedor,
                :IDPainelEuromidia,
                :IDDimFacesPaineis,
                :Status,
                :IDDimCheckinHistorico,
                :IDFatoKanbanCard,
                :BitAtivo,
                :IDEmpresaAgencia
            )
        """),
        params_item,
    ).mappings().first()

    if not row_novo or row_novo.get("id_item_controle") is None:
        raise RuntimeError("Não foi possível inserir item do contrato no controle.")

    return int(row_novo["id_item_controle"])





def _obter_dados_card_para_contato_contrato(id_card: int | None) -> dict | None:
    if id_card in (None, '', 0):
        return None

    row = db.session.execute(
        text("""
            SELECT TOP 1
                c.IDFatoKanbanCard,
                c.IDEmpresa,
                c.IDEmpresaProprietaria,
                c.Telefone,
                c.Email
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            WHERE c.IDFatoKanbanCard = :id_card
        """),
        {"id_card": int(id_card)},
    ).mappings().first()

    return dict(row) if row else None








def _upsert_dim_contatos_contrato(*, id_fato_kanban_card: int | None, id_empresa: int | None, id_empresa_proprietaria: int | None, telefone: str | None, email: str | None, id_fato_controle_contratos: int | None) -> int | None:
    if id_fato_kanban_card in (None, '', 0):
        return None

    telefone_limpo = _texto_ou_none(telefone)
    email_limpo = _texto_ou_none(email)
    id_empresa_int = int(id_empresa) if id_empresa not in (None, '', 0) else None
    id_empresa_prop_int = int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, '', 0) else None
    id_contrato_int = int(id_fato_controle_contratos) if id_fato_controle_contratos not in (None, '', 0) else None

    if id_empresa_int is None and not telefone_limpo and not email_limpo:
        return None

    row_existente = db.session.execute(
        text("""
            SELECT TOP 1 IDDimContatosContrato
            FROM [Integracao].[Silver].[DimContatosContrato]
            WHERE IDFatoKanbanCard = :id_fato_kanban_card
            ORDER BY IDDimContatosContrato DESC
        """),
        {"id_fato_kanban_card": int(id_fato_kanban_card)},
    ).mappings().first()

    if row_existente and row_existente.get("IDDimContatosContrato") is not None:
        id_contato = int(row_existente["IDDimContatosContrato"])
        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[DimContatosContrato]
                   SET Telefone = :telefone,
                       Email = :email,
                       IDFatoControleContratosEuromidia = COALESCE(:id_fato_controle_contratos, IDFatoControleContratosEuromidia),
                       IDEmpresa = COALESCE(:id_empresa, IDEmpresa),
                       IDEmpresaProprietaria = COALESCE(:id_empresa_proprietaria, IDEmpresaProprietaria),
                       IDFatoKanbanCard = :id_fato_kanban_card
                 WHERE IDDimContatosContrato = :id_contato
            """),
            {
                "telefone": telefone_limpo,
                "email": email_limpo,
                "id_fato_controle_contratos": id_contrato_int,
                "id_empresa": id_empresa_int,
                "id_empresa_proprietaria": id_empresa_prop_int,
                "id_fato_kanban_card": int(id_fato_kanban_card),
                "id_contato": id_contato,
            },
        )
        return id_contato

    row_novo = db.session.execute(
        text("""
            INSERT INTO [Integracao].[Silver].[DimContatosContrato]
            (Telefone, Email, IDFatoControleContratosEuromidia, IDEmpresa, IDEmpresaProprietaria, IDFatoKanbanCard)
            OUTPUT INSERTED.IDDimContatosContrato AS id_contato
            VALUES (:telefone, :email, :id_fato_controle_contratos, :id_empresa, :id_empresa_proprietaria, :id_fato_kanban_card)
        """),
        {
            "telefone": telefone_limpo,
            "email": email_limpo,
            "id_fato_controle_contratos": id_contrato_int,
            "id_empresa": id_empresa_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
            "id_fato_kanban_card": int(id_fato_kanban_card),
        },
    ).mappings().first()

    if not row_novo:
        return None

    return int(row_novo.get('id_contato') or 0) or None







def _obter_id_fase_atual_card(id_fato_kanban_card: int | None) -> int | None:
    if id_fato_kanban_card in (None, "", 0):
        return None

    row_coluna_fase = db.session.execute(
        text("""
            SELECT
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM [Kanban].sys.columns c
                        INNER JOIN [Kanban].sys.objects o
                                ON o.object_id = c.object_id
                        INNER JOIN [Kanban].sys.schemas s
                                ON s.schema_id = o.schema_id
                        WHERE s.name = 'Silver'
                          AND o.name = 'FatoKanbanCard'
                          AND o.type = 'U'
                          AND c.name = 'IDDimKanbanFaseAtual'
                    ) THEN 'IDDimKanbanFaseAtual'
                    WHEN EXISTS (
                        SELECT 1
                        FROM [Kanban].sys.columns c
                        INNER JOIN [Kanban].sys.objects o
                                ON o.object_id = c.object_id
                        INNER JOIN [Kanban].sys.schemas s
                                ON s.schema_id = o.schema_id
                        WHERE s.name = 'Silver'
                          AND o.name = 'FatoKanbanCard'
                          AND o.type = 'U'
                          AND c.name = 'IDDimKanbanFase'
                    ) THEN 'IDDimKanbanFase'
                    ELSE NULL
                END AS NomeColunaFase
        """),
    ).mappings().first()

    nome_coluna_fase = (row_coluna_fase or {}).get("NomeColunaFase")
    if not nome_coluna_fase:
        return None

    sql_fase = text(f"""
        SELECT TOP 1
               TRY_CONVERT(int, c.[{nome_coluna_fase}]) AS IDDimKanbanFaseAtual
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        WHERE c.IDFatoKanbanCard = :id_card
    """)

    row_fase = db.session.execute(
        sql_fase,
        {"id_card": int(id_fato_kanban_card)},
    ).mappings().first()

    if not row_fase or row_fase.get("IDDimKanbanFaseAtual") is None:
        return None

    return int(row_fase["IDDimKanbanFaseAtual"])



def _sincronizar_contato_contrato_se_fase_4(
    *,
    id_fato_kanban_card: int | None,
    id_empresa: int | None,
    id_empresa_proprietaria: int | None,
    id_fato_controle_contratos: int | None = None,
) -> int | None:
    """
    Eu salvo o contato somente se o card estiver na fase 4.
    Se ainda não houver contrato aprovado, salvo o contato só com o card.
    Se já houver contrato aprovado, também preencho o IDFatoControleContratosEuromidia.
    """
    if id_fato_kanban_card in (None, "", 0):
        return None

    id_fase = _obter_id_fase_atual_card(int(id_fato_kanban_card))
    if id_fase != 4:
        return None

    dados_card = _obter_dados_card_para_contato_contrato(int(id_fato_kanban_card))
    if not dados_card:
        return None

    return _upsert_dim_contatos_contrato(
        id_fato_kanban_card=int(id_fato_kanban_card),
        id_empresa=id_empresa or _int_ou_none(dados_card.get("IDEmpresa")),
        id_empresa_proprietaria=id_empresa_proprietaria or _int_ou_none(dados_card.get("IDEmpresaProprietaria")),
        telefone=_texto_ou_none(dados_card.get("Telefone")),
        email=_texto_ou_none(dados_card.get("Email")),
        id_fato_controle_contratos=id_fato_controle_contratos,
    )











def _campo_form_ou_none(form, *nomes: str) -> str | None:
    if form is None:
        return None

    for nome in nomes:
        valor = _texto_ou_none(form.get(nome))
        if valor:
            return valor

    return None



def _resolver_id_dim_tipo_cliente_para_contato_cliente_direto(
    *,
    id_fato_kanban_card: int | None,
    form=None,
    cabecalho_solicitacao: dict | None = None,
) -> int | None:
    id_tipo_form = _int_ou_none(_campo_form_ou_none(form, "IDDimTipoCliente", "id_dim_tipo_cliente", "TipoCliente"))
    if id_tipo_form not in (None, "", 0):
        return int(id_tipo_form)

    if cabecalho_solicitacao:
        id_tipo_cab = _int_ou_none(cabecalho_solicitacao.get("IDDimTipoCliente"))
        if id_tipo_cab not in (None, "", 0):
            return int(id_tipo_cab)

    if id_fato_kanban_card in (None, "", 0):
        return None

    row_coluna_tipo = db.session.execute(
        text("""
            SELECT TOP 1 c.name AS NomeColunaTipoCliente
            FROM [Kanban].sys.columns c
            INNER JOIN [Kanban].sys.objects o
                    ON o.object_id = c.object_id
            INNER JOIN [Kanban].sys.schemas s
                    ON s.schema_id = o.schema_id
            WHERE s.name = 'Silver'
              AND o.name = 'FatoKanbanCard'
              AND c.name = 'IDDimTipoCliente'
        """),
    ).mappings().first()

    if not row_coluna_tipo:
        return None

    row = db.session.execute(
        text("""
            SELECT TOP 1
                   TRY_CONVERT(int, c.[IDDimTipoCliente]) AS IDDimTipoCliente
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            WHERE c.[IDFatoKanbanCard] = :id_card
        """),
        {"id_card": int(id_fato_kanban_card)},
    ).mappings().first()

    return _int_ou_none((row or {}).get("IDDimTipoCliente"))



def _upsert_contato_cliente_direto_euromidia(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_kanban_card: int | None,
    form=None,
    cabecalho_solicitacao: dict | None = None,
) -> int | None:
    """
    Eu amarro o contato de Cliente Direto ao contrato aprovado.
    A regra é:
    - primeiro tento achar registro existente pelo card ou pelo contrato;
    - se existir, atualizo o IDFatoControleContratosEuromidia e preservo dados já preenchidos;
    - se não existir, crio o registro com o contrato, card, tipo de cliente e campos recebidos do formulário.
    """
    id_contrato_int = _int_ou_none(id_fato_controle_contratos)
    id_card_int = _int_ou_none(id_fato_kanban_card)

    if id_contrato_int in (None, "", 0):
        return None

    id_tipo_cliente = _resolver_id_dim_tipo_cliente_para_contato_cliente_direto(
        id_fato_kanban_card=id_card_int,
        form=form,
        cabecalho_solicitacao=cabecalho_solicitacao,
    )

    params = {
        "id_contrato": int(id_contrato_int),
        "id_card": int(id_card_int) if id_card_int not in (None, "", 0) else None,
        "id_tipo_cliente": int(id_tipo_cliente) if id_tipo_cliente not in (None, "", 0) else None,
        "nome_responsavel": _campo_form_ou_none(
            form,
            "NomeResponsavelLegalProcuradorEmpresa",
            "NomeCompletoResponsavelLegalProcuradorEmpresa",
            "NomeCompletoResolvavelLegalProcuradorEmpresa",
            "NomeCompletoResposavelLegalProcuradorEmpresa",
        ),
        "whatsapp_empresa": _campo_form_ou_none(form, "WhatsappEmpresa", "WhatsAppEmpresa", "TelefoneEmpresa"),
        "nome_testemunha": _campo_form_ou_none(form, "NomeTestemunha"),
        "email": _campo_form_ou_none(form, "Email", "EmailTestemunha", "EmailEmpresa"),
        "telefone": _campo_form_ou_none(form, "Telefone", "TelefoneTestemunha"),
        "nome_financeiro": _campo_form_ou_none(form, "NomeFinanceiro"),
        "email_financeiro": _campo_form_ou_none(form, "EmailFinanceiro"),
        "telefone_financeiro": _campo_form_ou_none(form, "TelefoneFinanceiro"),
    }

    ordem_prioridade_card = ""
    if params.get("id_card") not in (None, "", 0):
        ordem_prioridade_card = "CASE WHEN f.IDFatoKanbanCard = :id_card THEN 0 ELSE 1 END,"

    row_existente = db.session.execute(
        text(f"""
            SELECT TOP 1
                   f.IDFatoContatoClienteDiretoEuromidia
            FROM [Integracao].[Silver].[FatoContatoClienteDiretoEuromidia] f
            WHERE f.IDFatoControleContratosEuromidia = :id_contrato
               OR (:id_card IS NOT NULL AND f.IDFatoKanbanCard = :id_card)
            ORDER BY
                {ordem_prioridade_card}
                f.IDFatoContatoClienteDiretoEuromidia DESC
        """),
        params,
    ).mappings().first()

    if row_existente and row_existente.get("IDFatoContatoClienteDiretoEuromidia") not in (None, "", 0):
        id_contato = int(row_existente["IDFatoContatoClienteDiretoEuromidia"])
        params_update = dict(params)
        params_update["id_contato"] = id_contato

        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoContatoClienteDiretoEuromidia]
                   SET IDFatoControleContratosEuromidia = :id_contrato,
                       IDFatoKanbanCard = COALESCE(:id_card, IDFatoKanbanCard),
                       IDDimTipoCliente = COALESCE(:id_tipo_cliente, IDDimTipoCliente),
                       NomeResponsavelLegalProcuradorEmpresa = COALESCE(:nome_responsavel, NomeResponsavelLegalProcuradorEmpresa),
                       WhatsappEmpresa = COALESCE(:whatsapp_empresa, WhatsappEmpresa),
                       NomeTestemunha = COALESCE(:nome_testemunha, NomeTestemunha),
                       Email = COALESCE(:email, Email),
                       Telefone = COALESCE(:telefone, Telefone),
                       NomeFinanceiro = COALESCE(:nome_financeiro, NomeFinanceiro),
                       EmailFinanceiro = COALESCE(:email_financeiro, EmailFinanceiro),
                       TelefoneFinanceiro = COALESCE(:telefone_financeiro, TelefoneFinanceiro)
                 WHERE IDFatoContatoClienteDiretoEuromidia = :id_contato
            """),
            params_update,
        )
        return id_contato

    row_novo = db.session.execute(
        text("""
            INSERT INTO [Integracao].[Silver].[FatoContatoClienteDiretoEuromidia]
            (
                IDFatoControleContratosEuromidia,
                IDFatoKanbanCard,
                IDDimTipoCliente,
                NomeResponsavelLegalProcuradorEmpresa,
                WhatsappEmpresa,
                NomeTestemunha,
                Email,
                Telefone,
                NomeFinanceiro,
                EmailFinanceiro,
                TelefoneFinanceiro
            )
            OUTPUT INSERTED.IDFatoContatoClienteDiretoEuromidia AS id_contato
            VALUES
            (
                :id_contrato,
                :id_card,
                :id_tipo_cliente,
                :nome_responsavel,
                :whatsapp_empresa,
                :nome_testemunha,
                :email,
                :telefone,
                :nome_financeiro,
                :email_financeiro,
                :telefone_financeiro
            )
        """),
        params,
    ).mappings().first()

    return int(row_novo.get("id_contato") or 0) if row_novo else None

def _upsert_destinatarios_externos_contrato(*, id_fato_controle_contratos: int | None, id_empresa_destinatario: int | None, id_empresa: int | None, ids_itens_controle: list[int] | None) -> int | None:
    if id_fato_controle_contratos in (None, '', 0) or id_empresa_destinatario in (None, '', 0):
        return None

    id_contrato_int = int(id_fato_controle_contratos)
    id_empresa_destinatario_int = int(id_empresa_destinatario)
    id_empresa_int = int(id_empresa) if id_empresa not in (None, '', 0) else id_empresa_destinatario_int
    ids_itens_validos = [int(x) for x in (ids_itens_controle or []) if x not in (None, '', 0)]

    row_existente = db.session.execute(
        text("""
            SELECT TOP 1 IDFatoContratoDestinatarioExterno
            FROM [Integracao].[Silver].[FatoContratoDestinatarioExterno]
            WHERE IDFatoControleContratosEuromidia = :id_fato_controle_contratos
              AND IDEmpresaDestinatario = :id_empresa_destinatario
            ORDER BY IDFatoContratoDestinatarioExterno DESC
        """),
        {"id_fato_controle_contratos": id_contrato_int, "id_empresa_destinatario": id_empresa_destinatario_int},
    ).mappings().first()

    if row_existente and row_existente.get('IDFatoContratoDestinatarioExterno') is not None:
        id_destinatario_externo = int(row_existente['IDFatoContratoDestinatarioExterno'])
        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoContratoDestinatarioExterno]
                   SET IDEmpresa = :id_empresa,
                       BitAtivo = 1,
                       DataAtualizado = GETDATE()
                 WHERE IDFatoContratoDestinatarioExterno = :id_destinatario_externo
            """),
            {"id_empresa": id_empresa_int, "id_destinatario_externo": id_destinatario_externo},
        )
    else:
        row_novo = db.session.execute(
            text("""
                INSERT INTO [Integracao].[Silver].[FatoContratoDestinatarioExterno]
                (IDEmpresaDestinatario, IDEmpresa, IDFatoControleContratosEuromidia, BitAtivo, DataAtualizado)
                OUTPUT INSERTED.IDFatoContratoDestinatarioExterno AS id_destinatario_externo
                VALUES (:id_empresa_destinatario, :id_empresa, :id_fato_controle_contratos, 1, GETDATE())
            """),
            {
                "id_empresa_destinatario": id_empresa_destinatario_int,
                "id_empresa": id_empresa_int,
                "id_fato_controle_contratos": id_contrato_int,
            },
        ).mappings().first()
        id_destinatario_externo = int(row_novo.get('id_destinatario_externo') or 0) if row_novo else None

    if id_destinatario_externo is None:
        return None

    if ids_itens_validos:
        placeholders = ', '.join(str(x) for x in ids_itens_validos)
        db.session.execute(
            text(f"""
                UPDATE [Integracao].[Silver].[FatoContratoDestinatarioExternoItens]
                   SET BitAtivo = CASE
                                    WHEN IDFatoControleContratosItensEuromidia IN ({placeholders}) THEN 1
                                    ELSE 0
                                  END,
                       DataAtualizado = GETDATE()
                 WHERE IDFatoContratoDestinatarioExterno = :id_destinatario_externo
            """),
            {"id_destinatario_externo": int(id_destinatario_externo)},
        )
    else:
        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoContratoDestinatarioExternoItens]
                   SET BitAtivo = 0,
                       DataAtualizado = GETDATE()
                 WHERE IDFatoContratoDestinatarioExterno = :id_destinatario_externo
            """),
            {"id_destinatario_externo": int(id_destinatario_externo)},
        )

    for id_item_controle in ids_itens_validos:
        row_item_existente = db.session.execute(
            text("""
                SELECT TOP 1 IDFatoContratoDestinatarioExternoItens
                FROM [Integracao].[Silver].[FatoContratoDestinatarioExternoItens]
                WHERE IDFatoContratoDestinatarioExterno = :id_destinatario_externo
                  AND IDFatoControleContratosItensEuromidia = :id_item_controle
                ORDER BY IDFatoContratoDestinatarioExternoItens DESC
            """),
            {"id_destinatario_externo": int(id_destinatario_externo), "id_item_controle": int(id_item_controle)},
        ).mappings().first()

        if row_item_existente and row_item_existente.get('IDFatoContratoDestinatarioExternoItens') is not None:
            db.session.execute(
                text("""
                    UPDATE [Integracao].[Silver].[FatoContratoDestinatarioExternoItens]
                       SET IDEmpresaDestinatario = :id_empresa_destinatario,
                           IDEmpresa = :id_empresa,
                           BitAtivo = 1,
                           DataAtualizado = GETDATE()
                     WHERE IDFatoContratoDestinatarioExternoItens = :id_destinatario_item
                """),
                {
                    "id_empresa_destinatario": id_empresa_destinatario_int,
                    "id_empresa": id_empresa_int,
                    "id_destinatario_item": int(row_item_existente['IDFatoContratoDestinatarioExternoItens']),
                },
            )
            continue

        db.session.execute(
            text("""
                INSERT INTO [Integracao].[Silver].[FatoContratoDestinatarioExternoItens]
                (IDEmpresaDestinatario, IDEmpresa, IDFatoContratoDestinatarioExterno, IDFatoControleContratosItensEuromidia, BitAtivo, DataAtualizado)
                VALUES (:id_empresa_destinatario, :id_empresa, :id_destinatario_externo, :id_item_controle, 1, GETDATE())
            """),
            {
                "id_empresa_destinatario": id_empresa_destinatario_int,
                "id_empresa": id_empresa_int,
                "id_destinatario_externo": int(id_destinatario_externo),
                "id_item_controle": int(id_item_controle),
            },
        )

    return id_destinatario_externo














def _resolver_ids_painel_face_preco_praticado(item_solicitacao: dict) -> dict:
    """Eu resolvo ID do painel e da face para gravar o preço praticado por item."""

    id_painel = _int_ou_none(item_solicitacao.get("IDPainelEuromidia"))
    id_face = _int_ou_none(item_solicitacao.get("IDDimFacesPaineis"))
    cod_ponto = _texto_ou_none(item_solicitacao.get("CodPonto"))
    cod_face = _texto_ou_none(item_solicitacao.get("CodFace"))

    if id_face is not None:
        row_face = db.session.execute(
            text("""
                SELECT TOP (1)
                       IDDimFacesPaineis,
                       IDDimPaineisEuromidia,
                       CodPonto,
                       CodFace
                FROM [Integracao].[Silver].[DimFacesPaineis]
                WHERE IDDimFacesPaineis = :id_face
                ORDER BY IDDimFacesPaineis DESC
            """),
            {"id_face": int(id_face)},
        ).mappings().first()

        if row_face:
            id_face = _int_ou_none(row_face.get("IDDimFacesPaineis")) or id_face
            id_painel = id_painel or _int_ou_none(row_face.get("IDDimPaineisEuromidia"))
            cod_ponto = cod_ponto or _texto_ou_none(row_face.get("CodPonto"))
            cod_face = cod_face or _texto_ou_none(row_face.get("CodFace"))

    if (id_painel is None or id_face is None) and cod_ponto and cod_face:
        row_face = db.session.execute(
            text("""
                SELECT TOP (1)
                       f.IDDimFacesPaineis,
                       f.IDDimPaineisEuromidia,
                       f.CodPonto,
                       f.CodFace
                FROM [Integracao].[Silver].[DimFacesPaineis] f
                WHERE ISNULL(LTRIM(RTRIM(CAST(f.CodPonto AS varchar(60)))), '') = ISNULL(LTRIM(RTRIM(CAST(:cod_ponto AS varchar(60)))), '')
                  AND ISNULL(UPPER(LTRIM(RTRIM(CAST(f.CodFace AS varchar(60))))), '') = ISNULL(UPPER(LTRIM(RTRIM(CAST(:cod_face AS varchar(60))))), '')
                ORDER BY f.IDDimFacesPaineis DESC
            """),
            {"cod_ponto": cod_ponto, "cod_face": cod_face},
        ).mappings().first()

        if row_face:
            id_face = id_face or _int_ou_none(row_face.get("IDDimFacesPaineis"))
            id_painel = id_painel or _int_ou_none(row_face.get("IDDimPaineisEuromidia"))
            cod_ponto = cod_ponto or _texto_ou_none(row_face.get("CodPonto"))
            cod_face = cod_face or _texto_ou_none(row_face.get("CodFace"))

    return {
        "id_painel": id_painel,
        "id_face": id_face,
        "cod_ponto": cod_ponto,
        "cod_face": cod_face,
    }


def _buscar_relacionamento_empresa_preco_praticado(*, id_empresa: int | None, id_empresa_proprietaria: int | None) -> int | None:
    """Eu busco o relacionamento da empresa para preencher DimRelacionamentoEmpresa no preço praticado."""

    if id_empresa in (None, "", 0) or id_empresa_proprietaria in (None, "", 0):
        return None

    row = db.session.execute(
        text("""
            SELECT TOP (1)
                   DimRelacionamentoEmpresa
            FROM [Integracao].[Silver].[DimRelacionamentoEmpresa]
            WHERE IDEmpresa = :id_empresa
              AND IDEmpresaProprietaria = :id_empresa_proprietaria
            ORDER BY DimRelacionamentoEmpresa DESC
        """),
        {
            "id_empresa": int(id_empresa),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    return _int_ou_none(row.get("DimRelacionamentoEmpresa")) if row else None


def _buscar_ultima_negociacao_preco_para_preco_praticado(
    *,
    id_card: int | None,
    id_empresa_proprietaria: int | None,
    id_painel: int | None,
    id_face: int | None,
) -> dict | None:
    """Eu busco a última negociação aprovada/proposta do mesmo card + painel + face."""

    if id_card in (None, "", 0) or id_painel in (None, "", 0) or id_face in (None, "", 0):
        return None

    filtros_empresa = ""
    params = {
        "id_card": int(id_card),
        "id_painel": int(id_painel),
        "id_face": int(id_face),
    }

    if id_empresa_proprietaria not in (None, "", 0):
        filtros_empresa = "AND np.IDEmpresaProprietaria = :id_empresa_proprietaria"
        params["id_empresa_proprietaria"] = int(id_empresa_proprietaria)

    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   np.*
            FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco] np
            WHERE np.IDFatoKanbanCard = :id_card
              {filtros_empresa}
              AND ISNULL(TRY_CONVERT(int, np.IDDimPaineisEuromidia), 0) = ISNULL(TRY_CONVERT(int, :id_painel), 0)
              AND ISNULL(TRY_CONVERT(int, np.IDDimFacesPaineis), 0) = ISNULL(TRY_CONVERT(int, :id_face), 0)
            ORDER BY
                CASE WHEN np.PrecoAprovado IS NULL THEN 1 ELSE 0 END,
                COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino) DESC,
                np.IDFatoKanbanNegociacaoPreco DESC
        """),
        params,
    ).mappings().first()

    return dict(row) if row else None


def _buscar_operacional_painel_face_preco_praticado(*, id_card: int | None, id_painel: int | None, id_face: int | None) -> dict | None:
    """Eu busco o snapshot operacional do card para usar como fallback do preço praticado."""

    if id_card in (None, "", 0) or id_painel in (None, "", 0) or id_face in (None, "", 0):
        return None

    row = db.session.execute(
        text("""
            SELECT TOP (1)
                   pf.IDDimTabelaPrecosEuromidia,
                   pf.ExibicoesDia,
                   pf.CustoTabela,
                   pf.ValorTabela,
                   pf.NovoValor,
                   pf.PercentualDesconto,
                   pf.ValorVendaFinal,
                   pf.MargemPercentual,
                   pf.DataInicio,
                   pf.DataFim
            FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
            WHERE pf.IDFatoKanbanCard = :id_card
              AND ISNULL(pf.Ativo, 1) = 1
              AND ISNULL(TRY_CONVERT(int, pf.IDDimPaineisEuromidia), 0) = ISNULL(TRY_CONVERT(int, :id_painel), 0)
              AND ISNULL(TRY_CONVERT(int, pf.IDDimFacesPaineis), 0) = ISNULL(TRY_CONVERT(int, :id_face), 0)
            ORDER BY ISNULL(pf.Ordem, 0), pf.IDFatoKanbanCardPainelFace DESC
        """),
        {
            "id_card": int(id_card),
            "id_painel": int(id_painel),
            "id_face": int(id_face),
        },
    ).mappings().first()

    return dict(row) if row else None


def _primeiro_decimal_nao_nulo(*valores):
    """Eu devolvo o primeiro número decimal válido da lista."""
    for valor in valores:
        numero = _decimal_ou_none(valor)
        if numero is not None:
            return numero
    return None


def _primeiro_valor_nao_vazio(*valores):
    """Eu devolvo o primeiro valor realmente preenchido."""
    for valor in valores:
        if valor not in (None, ""):
            return valor
    return None


def _calcular_percentual_preco_praticado(*, numerador: float | None, denominador: float | None) -> float | None:
    """Eu calculo percentual somente quando os valores permitem uma divisão segura."""
    try:
        if numerador is None or denominador in (None, 0):
            return None
        return float(numerador) / float(denominador) * 100
    except Exception:
        return None


def _upsert_preco_praticado_item_contrato_euromidia(
    *,
    cabecalho_solicitacao: dict,
    item_solicitacao: dict,
    id_contrato_controle: int,
    id_item_controle: int,
    id_usuario_logado: int | None,
) -> dict:
    """
    Eu gravo o preço aplicado de fato no item oficial do contrato.

    Regra importante:
    - Kanban.Silver.FatoKanbanNegociacaoPreco continua sendo o histórico da negociação;
    - Integracao.Silver.FatoContratoItemPrecoPraticadoEuromidia guarda o preço final aplicado no contrato/item;
    - a chave principal de consolidação aqui é IDFatoControleContratosItensEuromidia.
    """

    id_contrato = _int_ou_none(id_contrato_controle)
    id_item = _int_ou_none(id_item_controle)
    id_card = _int_ou_none(item_solicitacao.get("IDFatoKanbanCard")) or _int_ou_none(cabecalho_solicitacao.get("IDFatoKanbanCard"))
    id_empresa = _int_ou_none(item_solicitacao.get("IDEmpresa")) or _int_ou_none(cabecalho_solicitacao.get("IDEmpresa"))
    id_empresa_proprietaria = _int_ou_none(item_solicitacao.get("IDEmpresaProprietaria")) or _int_ou_none(cabecalho_solicitacao.get("IDEmpresaProprietaria"))
    id_usuario = _int_ou_none(id_usuario_logado)

    if id_contrato in (None, "", 0) or id_item in (None, "", 0):
        print(
            "APROVACAO_CONTRATO | preco praticado ignorado por falta de contrato/item | "
            f"id_contrato={id_contrato} | id_item={id_item} | id_card={id_card}",
            flush=True,
        )
        return {"ok": False, "motivo": "contrato_ou_item_invalido", "id_contrato": id_contrato, "id_item": id_item}

    ids_painel_face = _resolver_ids_painel_face_preco_praticado(item_solicitacao)
    id_painel = ids_painel_face.get("id_painel")
    id_face = ids_painel_face.get("id_face")

    negociacao = _buscar_ultima_negociacao_preco_para_preco_praticado(
        id_card=id_card,
        id_empresa_proprietaria=id_empresa_proprietaria,
        id_painel=id_painel,
        id_face=id_face,
    )
    operacional = _buscar_operacional_painel_face_preco_praticado(
        id_card=id_card,
        id_painel=id_painel,
        id_face=id_face,
    )

    id_tabela_preco = _int_ou_none((negociacao or {}).get("IDDimTabelaPrecosEuromidia")) or _int_ou_none((operacional or {}).get("IDDimTabelaPrecosEuromidia"))
    id_usuario_autorizacao_preco = _int_ou_none((negociacao or {}).get("IDDimUsuariosAprovacaoPreco"))
    dim_relacionamento = _buscar_relacionamento_empresa_preco_praticado(
        id_empresa=id_empresa,
        id_empresa_proprietaria=id_empresa_proprietaria,
    )

    custo_painel = _primeiro_decimal_nao_nulo(
        (negociacao or {}).get("CustoProposto"),
        (negociacao or {}).get("CustoAtual"),
        (operacional or {}).get("CustoTabela"),
    )
    preco_proposto = _primeiro_decimal_nao_nulo(
        (negociacao or {}).get("PrecoProposto"),
        (operacional or {}).get("NovoValor"),
        (operacional or {}).get("ValorVendaFinal"),
        item_solicitacao.get("FaturamentoLiquidoFinalMensal"),
        item_solicitacao.get("FaturamentoLiquidoMensal"),
        item_solicitacao.get("TotalLiquidoContratoAGBRVENDGERCOOR"),
        item_solicitacao.get("TotalLiquidoContratoAGBRCTACORDO"),
        item_solicitacao.get("FaturamentoBrutoMensal"),
    )
    preco_praticado = _primeiro_decimal_nao_nulo(
        (negociacao or {}).get("PrecoAprovado"),
        (operacional or {}).get("ValorVendaFinal"),
        (negociacao or {}).get("PrecoProposto"),
        (operacional or {}).get("NovoValor"),
        item_solicitacao.get("FaturamentoLiquidoFinalMensal"),
        item_solicitacao.get("FaturamentoLiquidoMensal"),
        item_solicitacao.get("TotalLiquidoContratoAGBRVENDGERCOOR"),
        item_solicitacao.get("TotalLiquidoContratoAGBRCTACORDO"),
        item_solicitacao.get("FaturamentoBrutoMensal"),
    )
    desconto_percentual = _primeiro_decimal_nao_nulo(
        (negociacao or {}).get("DescontoAprovado"),
        (negociacao or {}).get("DescontoProposto"),
        (operacional or {}).get("PercentualDesconto"),
    )

    if desconto_percentual is None and preco_proposto not in (None, 0) and preco_praticado is not None:
        desconto_percentual = _calcular_percentual_preco_praticado(
            numerador=float(preco_proposto) - float(preco_praticado),
            denominador=float(preco_proposto),
        )

    margem_percentual = _primeiro_decimal_nao_nulo(
        (negociacao or {}).get("MargemProposta"),
        (operacional or {}).get("MargemPercentual"),
    )

    if margem_percentual is None and preco_praticado not in (None, 0) and custo_painel is not None:
        margem_percentual = _calcular_percentual_preco_praticado(
            numerador=float(preco_praticado) - float(custo_painel),
            denominador=float(preco_praticado),
        )

    data_inicio = _primeiro_valor_nao_vazio(
        item_solicitacao.get("DataInicioPrevisto"),
        (operacional or {}).get("DataInicio"),
        (negociacao or {}).get("PeriodoInicio"),
    )
    data_termino = _primeiro_valor_nao_vazio(
        item_solicitacao.get("DataTerminoPrevisto"),
        item_solicitacao.get("DataFimEfetiva"),
        (operacional or {}).get("DataFim"),
        (negociacao or {}).get("PeriodoTermino"),
    )

    exibicoes = _int_ou_none((operacional or {}).get("ExibicoesDia"))
    custo_medio_painel = custo_painel

    payload = {
        "IDEmpresa": id_empresa,
        "DimRelacionamentoEmpresa": dim_relacionamento,
        "IDFatoKanbanCard": id_card,
        "IDDimUsuarios": id_usuario,
        "IDDimUsuariosAutorizacaoPreco": id_usuario_autorizacao_preco,
        "IDDimUsuariosAprovacaoContrato": id_usuario,
        "IDFatoControleContratosEuromidia": id_contrato,
        "IDFatoControleContratosItensEuromidia": id_item,
        "IDDimPaineisEuromidia": id_painel,
        "IDDimFacesPaineis": id_face,
        "IDDimTabelaPrecosEuromidia": id_tabela_preco,
        "Exibicoes": exibicoes,
        "CustoPainel": custo_painel,
        "PrecoProposto": preco_proposto,
        "CustoMedioPainel": custo_medio_painel,
        "PrecoPraticado": preco_praticado,
        "DescontoPercentual": desconto_percentual,
        "MargemPercentual": margem_percentual,
        "DataInicio": data_inicio,
        "DataTermino": data_termino,
    }

    row_existente = db.session.execute(
        text("""
            SELECT TOP (1)
                   IDFatoContratoItemPrecoPraticadoEuromidia
            FROM [Integracao].[Silver].[FatoContratoItemPrecoPraticadoEuromidia] WITH (UPDLOCK, HOLDLOCK)
            WHERE IDFatoControleContratosItensEuromidia = :id_item
            ORDER BY IDFatoContratoItemPrecoPraticadoEuromidia DESC
        """),
        {"id_item": int(id_item)},
    ).mappings().first()

    print(
        "APROVACAO_CONTRATO | sincronizando preco praticado | "
        f"id_contrato={id_contrato} | id_item={id_item} | id_card={id_card} | "
        f"id_painel={id_painel} | id_face={id_face} | preco_praticado={preco_praticado} | "
        f"id_negociacao={(negociacao or {}).get('IDFatoKanbanNegociacaoPreco')}",
        flush=True,
    )

    if row_existente:
        id_preco_praticado = int(row_existente.get("IDFatoContratoItemPrecoPraticadoEuromidia") or 0)
        resultado_update = db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoContratoItemPrecoPraticadoEuromidia]
                   SET IDEmpresa = :IDEmpresa,
                       DimRelacionamentoEmpresa = :DimRelacionamentoEmpresa,
                       IDFatoKanbanCard = :IDFatoKanbanCard,
                       IDDimUsuarios = :IDDimUsuarios,
                       IDDimUsuariosAutorizacaoPreco = :IDDimUsuariosAutorizacaoPreco,
                       IDDimUsuariosAprovacaoContrato = :IDDimUsuariosAprovacaoContrato,
                       IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia,
                       IDFatoControleContratosItensEuromidia = :IDFatoControleContratosItensEuromidia,
                       IDDimPaineisEuromidia = :IDDimPaineisEuromidia,
                       IDDimFacesPaineis = :IDDimFacesPaineis,
                       IDDimTabelaPrecosEuromidia = :IDDimTabelaPrecosEuromidia,
                       Exibicoes = :Exibicoes,
                       CustoPainel = :CustoPainel,
                       PrecoProposto = :PrecoProposto,
                       CustoMedioPainel = :CustoMedioPainel,
                       PrecoPraticado = :PrecoPraticado,
                       DescontoPercentual = :DescontoPercentual,
                       MargemPercentual = :MargemPercentual,
                       DataInicio = :DataInicio,
                       DataTermino = :DataTermino,
                       DataAprovacaoContrato = GETDATE(),
                       DataCadastro = GETDATE()
                 WHERE IDFatoContratoItemPrecoPraticadoEuromidia = :id_preco_praticado
            """),
            {**payload, "id_preco_praticado": int(id_preco_praticado)},
        )

        print(
            "APROVACAO_CONTRATO | preco praticado atualizado | "
            f"id_preco_praticado={id_preco_praticado} | linhas={int(resultado_update.rowcount or 0)}",
            flush=True,
        )

        return {
            "ok": True,
            "acao": "update",
            "id_preco_praticado": int(id_preco_praticado),
            "linhas": int(resultado_update.rowcount or 0),
            "id_item": int(id_item),
            "preco_praticado": preco_praticado,
        }

    row_insert = db.session.execute(
        text("""
            INSERT INTO [Integracao].[Silver].[FatoContratoItemPrecoPraticadoEuromidia]
            (
                IDEmpresa,
                DimRelacionamentoEmpresa,
                IDFatoKanbanCard,
                IDDimUsuarios,
                IDDimUsuariosAutorizacaoPreco,
                IDDimUsuariosAprovacaoContrato,
                IDFatoControleContratosEuromidia,
                IDFatoControleContratosItensEuromidia,
                IDDimPaineisEuromidia,
                IDDimFacesPaineis,
                IDDimTabelaPrecosEuromidia,
                Exibicoes,
                CustoPainel,
                PrecoProposto,
                CustoMedioPainel,
                PrecoPraticado,
                DescontoPercentual,
                MargemPercentual,
                DataInicio,
                DataTermino,
                DataAprovacaoContrato,
                DataCadastro
            )
            OUTPUT INSERTED.IDFatoContratoItemPrecoPraticadoEuromidia AS id_preco_praticado
            VALUES
            (
                :IDEmpresa,
                :DimRelacionamentoEmpresa,
                :IDFatoKanbanCard,
                :IDDimUsuarios,
                :IDDimUsuariosAutorizacaoPreco,
                :IDDimUsuariosAprovacaoContrato,
                :IDFatoControleContratosEuromidia,
                :IDFatoControleContratosItensEuromidia,
                :IDDimPaineisEuromidia,
                :IDDimFacesPaineis,
                :IDDimTabelaPrecosEuromidia,
                :Exibicoes,
                :CustoPainel,
                :PrecoProposto,
                :CustoMedioPainel,
                :PrecoPraticado,
                :DescontoPercentual,
                :MargemPercentual,
                :DataInicio,
                :DataTermino,
                GETDATE(),
                GETDATE()
            )
        """),
        payload,
    ).mappings().first()

    id_preco_praticado = int(row_insert.get("id_preco_praticado") or 0) if row_insert else None

    print(
        "APROVACAO_CONTRATO | preco praticado inserido | "
        f"id_preco_praticado={id_preco_praticado} | id_item={id_item}",
        flush=True,
    )

    return {
        "ok": True,
        "acao": "insert",
        "id_preco_praticado": id_preco_praticado,
        "linhas": 1,
        "id_item": int(id_item),
        "preco_praticado": preco_praticado,
    }


def _tabela_silver_existe_admin(*, banco: str, nome_tabela: str) -> bool:
    """Confiro se uma tabela Silver existe antes de tentar gravar eventos auxiliares."""
    banco_limpo = _texto_ou_vazio(banco)
    nome_tabela_limpo = _texto_ou_vazio(nome_tabela)

    if banco_limpo not in {"Integracao", "Kanban"} or not nome_tabela_limpo:
        return False

    sql = text(f"""
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM [{banco_limpo}].sys.tables t
            INNER JOIN [{banco_limpo}].sys.schemas s
                    ON s.schema_id = t.schema_id
            WHERE s.name = 'Silver'
              AND t.name = :nome_tabela
        ) THEN 1 ELSE 0 END AS Existe;
    """)

    return bool(db.session.execute(sql, {"nome_tabela": nome_tabela_limpo}).scalar() or 0)


def _coluna_silver_existe_admin(*, banco: str, nome_tabela: str, nome_coluna: str) -> bool:
    """Confiro se uma coluna existe antes de montar SQL que depende dela."""
    banco_limpo = _texto_ou_vazio(banco)
    nome_tabela_limpo = _texto_ou_vazio(nome_tabela)
    nome_coluna_limpo = _texto_ou_vazio(nome_coluna)

    if banco_limpo not in {"Integracao", "Kanban"} or not nome_tabela_limpo or not nome_coluna_limpo:
        return False

    sql = text(f"""
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM [{banco_limpo}].sys.columns c
            INNER JOIN [{banco_limpo}].sys.tables t
                    ON t.object_id = c.object_id
            INNER JOIN [{banco_limpo}].sys.schemas s
                    ON s.schema_id = t.schema_id
            WHERE s.name = 'Silver'
              AND t.name = :nome_tabela
              AND c.name = :nome_coluna
        ) THEN 1 ELSE 0 END AS Existe;
    """)

    return bool(db.session.execute(sql, {"nome_tabela": nome_tabela_limpo, "nome_coluna": nome_coluna_limpo}).scalar() or 0)


def _obter_metadados_card_empresa_relacionada_admin(id_fato_kanban_card: int | None) -> dict:
    """Busco dados do card que ajudam a preencher FatoContratoEmpresaRelacionada."""
    id_card = _int_ou_none(id_fato_kanban_card)
    if id_card in (None, "", 0):
        return {}

    campos = []
    for nome_coluna in (
        "IDEmpresa",
        "IDEmpresaAgencia",
        "IDEmpresaBureau",
        "IDEmpresaIntermediario",
        "IDDimTipoCliente",
        "IDDimCnaes",
        "MarcaExibida",
    ):
        if _coluna_silver_existe_admin(banco="Kanban", nome_tabela="FatoKanbanCard", nome_coluna=nome_coluna):
            campos.append(f"c.[{nome_coluna}] AS [{nome_coluna}]")
        else:
            campos.append(f"CAST(NULL AS nvarchar(255)) AS [{nome_coluna}]")

    sql = text(f"""
        SELECT TOP (1)
               {', '.join(campos)}
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        WHERE c.IDFatoKanbanCard = :id_card;
    """)

    row = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()
    return dict(row) if row else {}


def _upsert_linha_empresa_relacionada_contrato_euromidia(
    *,
    id_fato_contrato_empresa_relacionada: int | None,
    id_contrato_controle: int,
    id_empresa: int | None,
    id_dim_tipo_cliente: int | None,
    id_dim_cnaes: int | None,
    marca_exibida: str | None,
    bit_principal: int,
) -> int | None:
    """Faço upsert de uma linha em FatoContratoEmpresaRelacionada e devolvo o ID."""
    id_contrato = _int_ou_none(id_contrato_controle)
    id_empresa_int = _int_ou_none(id_empresa)
    id_rel = _int_ou_none(id_fato_contrato_empresa_relacionada)

    if id_contrato in (None, "", 0) or id_empresa_int in (None, "", 0):
        return None

    if not _tabela_silver_existe_admin(banco="Integracao", nome_tabela="FatoContratoEmpresaRelacionada"):
        current_app.logger.warning(
            "APROVACAO_CONTRATO | tabela FatoContratoEmpresaRelacionada não existe; sincronização ignorada | contrato=%s | empresa=%s",
            id_contrato,
            id_empresa_int,
        )
        return None

    params = {
        "id_rel": int(id_rel) if id_rel not in (None, "", 0) else None,
        "id_contrato": int(id_contrato),
        "id_empresa": int(id_empresa_int),
        "id_dim_tipo_cliente": int(id_dim_tipo_cliente) if id_dim_tipo_cliente not in (None, "", 0) else None,
        "id_dim_cnaes": int(id_dim_cnaes) if id_dim_cnaes not in (None, "", 0) else None,
        "marca_exibida": _texto_ou_none(marca_exibida),
        "bit_principal": 1 if int(bit_principal or 0) == 1 else 0,
    }

    ordem_prioridade_relacao = ""
    if params.get("id_rel") not in (None, "", 0):
        ordem_prioridade_relacao = "CASE WHEN IDFatoContratoEmpresaRelacionada = :id_rel THEN 0 ELSE 1 END,"

    row_existente = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   IDFatoContratoEmpresaRelacionada
            FROM [Integracao].[Silver].[FatoContratoEmpresaRelacionada] WITH (UPDLOCK, HOLDLOCK)
            WHERE
                (:id_rel IS NOT NULL AND IDFatoContratoEmpresaRelacionada = :id_rel)
                OR
                (
                    IDFatoControleContratosEuromidia = :id_contrato
                    AND IDEmpresa = :id_empresa
                    AND ISNULL(BitPrincipal, 0) = :bit_principal
                )
            ORDER BY
                {ordem_prioridade_relacao}
                IDFatoContratoEmpresaRelacionada DESC;
        """),
        params,
    ).mappings().first()

    if row_existente and row_existente.get("IDFatoContratoEmpresaRelacionada") not in (None, "", 0):
        id_rel_final = int(row_existente["IDFatoContratoEmpresaRelacionada"])
        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoContratoEmpresaRelacionada]
                   SET IDFatoControleContratosEuromidia = :id_contrato,
                       IDEmpresa = :id_empresa,
                       IDDimTipoCliente = COALESCE(:id_dim_tipo_cliente, IDDimTipoCliente),
                       IDDimCnaes = COALESCE(:id_dim_cnaes, IDDimCnaes),
                       MarcaExibida = COALESCE(:marca_exibida, MarcaExibida),
                       BitPrincipal = :bit_principal,
                       BitAtivo = 1,
                       DataAtualizacao = GETDATE()
                 WHERE IDFatoContratoEmpresaRelacionada = :id_rel_final;
            """),
            {**params, "id_rel_final": id_rel_final},
        )
        return id_rel_final

    row_insert = db.session.execute(
        text("""
            INSERT INTO [Integracao].[Silver].[FatoContratoEmpresaRelacionada]
            (
                IDFatoControleContratosEuromidia,
                IDEmpresa,
                IDDimTipoCliente,
                IDDimCnaes,
                MarcaExibida,
                BitPrincipal,
                BitAtivo,
                DataAtualizacao
            )
            OUTPUT INSERTED.IDFatoContratoEmpresaRelacionada AS id_rel
            VALUES
            (
                :id_contrato,
                :id_empresa,
                :id_dim_tipo_cliente,
                :id_dim_cnaes,
                :marca_exibida,
                :bit_principal,
                1,
                GETDATE()
            );
        """),
        params,
    ).mappings().first()

    return int(row_insert.get("id_rel") or 0) if row_insert and row_insert.get("id_rel") not in (None, "", 0) else None


def _sincronizar_empresa_relacionada_item_contrato_euromidia(
    *,
    cabecalho_solicitacao: dict,
    item_solicitacao: dict,
    id_contrato_controle: int,
    id_item_controle: int,
    id_usuario_logado: int | None = None,
) -> dict:
    """Restaura a gravação de empresas relacionadas no momento da aprovação.

    A tabela FatoContratoEmpresaRelacionada é parte do rastro do contrato:
    - grava a empresa principal do contrato;
    - grava agência/bureau/intermediário quando houver ID disponível;
    - amarra o item aprovado ao IDFatoContratoEmpresaRelacionada principal.
    """
    cab = cabecalho_solicitacao or {}
    item = item_solicitacao or {}
    id_contrato = _int_ou_none(id_contrato_controle)
    id_item = _int_ou_none(id_item_controle)
    id_card = _int_ou_none(item.get("IDFatoKanbanCard")) or _int_ou_none(cab.get("IDFatoKanbanCard"))

    if id_contrato in (None, "", 0) or id_item in (None, "", 0):
        return {"ok": False, "motivo": "contrato_ou_item_invalido", "id_contrato": id_contrato, "id_item": id_item}

    metadados_card = _obter_metadados_card_empresa_relacionada_admin(id_card)

    id_tipo_cliente_principal = (
        _int_ou_none(item.get("IDDimTipoCliente"))
        or _int_ou_none(cab.get("IDDimTipoCliente"))
        or _int_ou_none(metadados_card.get("IDDimTipoCliente"))
    )
    id_dim_cnaes = (
        _int_ou_none(item.get("IDDimCnaes"))
        or _int_ou_none(cab.get("IDDimCnaes"))
        or _int_ou_none(metadados_card.get("IDDimCnaes"))
    )
    marca_exibida = _primeiro_valor_nao_vazio(
        item.get("MarcaExibida"),
        cab.get("MarcaExibida"),
        metadados_card.get("MarcaExibida"),
    )

    empresas_para_sincronizar: list[dict] = []
    id_empresa_principal = (
        _int_ou_none(item.get("IDEmpresa"))
        or _int_ou_none(cab.get("IDEmpresa"))
        or _int_ou_none(metadados_card.get("IDEmpresa"))
    )

    if id_empresa_principal not in (None, "", 0):
        empresas_para_sincronizar.append({
            "papel": "PRINCIPAL",
            "id_rel_existente": _int_ou_none(item.get("IDFatoContratoEmpresaRelacionada")),
            "id_empresa": int(id_empresa_principal),
            "id_dim_tipo_cliente": id_tipo_cliente_principal,
            "id_dim_cnaes": id_dim_cnaes,
            "marca_exibida": marca_exibida,
            "bit_principal": 1,
        })

    # IDs conhecidos pelo cadastro atual: Cliente direto=2, Agência=3, Bureau=4.
    # Intermediário pode variar por cadastro; por isso só gravo a empresa e deixo o tipo nulo quando não houver ID explícito.
    relacionados = [
        ("AGENCIA", _int_ou_none(item.get("IDEmpresaAgencia")) or _int_ou_none(cab.get("IDEmpresaAgencia")) or _int_ou_none(metadados_card.get("IDEmpresaAgencia")), 3),
        ("BUREAU", _int_ou_none(item.get("IDEmpresaBureau")) or _int_ou_none(cab.get("IDEmpresaBureau")) or _int_ou_none(metadados_card.get("IDEmpresaBureau")), 4),
        ("INTERMEDIARIO", _int_ou_none(item.get("IDEmpresaIntermediario")) or _int_ou_none(cab.get("IDEmpresaIntermediario")) or _int_ou_none(metadados_card.get("IDEmpresaIntermediario")), _int_ou_none(item.get("IDDimTipoClienteIntermediario")) or _int_ou_none(cab.get("IDDimTipoClienteIntermediario"))),
    ]

    empresas_vistas: set[tuple[int, int]] = set()
    for empresa in list(empresas_para_sincronizar):
        chave = (int(empresa["id_empresa"]), int(empresa["bit_principal"]))
        empresas_vistas.add(chave)

    for papel, id_empresa_relacionada, id_tipo_relacionada in relacionados:
        if id_empresa_relacionada in (None, "", 0):
            continue
        chave = (int(id_empresa_relacionada), 0)
        if chave in empresas_vistas:
            continue
        empresas_vistas.add(chave)
        empresas_para_sincronizar.append({
            "papel": papel,
            "id_rel_existente": None,
            "id_empresa": int(id_empresa_relacionada),
            "id_dim_tipo_cliente": id_tipo_relacionada,
            "id_dim_cnaes": id_dim_cnaes,
            "marca_exibida": marca_exibida,
            "bit_principal": 0,
        })

    registros: list[dict] = []
    id_rel_principal = None

    for empresa in empresas_para_sincronizar:
        id_rel = _upsert_linha_empresa_relacionada_contrato_euromidia(
            id_fato_contrato_empresa_relacionada=empresa.get("id_rel_existente"),
            id_contrato_controle=int(id_contrato),
            id_empresa=empresa.get("id_empresa"),
            id_dim_tipo_cliente=empresa.get("id_dim_tipo_cliente"),
            id_dim_cnaes=empresa.get("id_dim_cnaes"),
            marca_exibida=empresa.get("marca_exibida"),
            bit_principal=int(empresa.get("bit_principal") or 0),
        )
        registros.append({
            "papel": empresa.get("papel"),
            "id_empresa": empresa.get("id_empresa"),
            "id_fato_contrato_empresa_relacionada": id_rel,
            "bit_principal": int(empresa.get("bit_principal") or 0),
        })
        if int(empresa.get("bit_principal") or 0) == 1 and id_rel not in (None, "", 0):
            id_rel_principal = int(id_rel)

    if id_rel_principal not in (None, "", 0) and _coluna_silver_existe_admin(
        banco="Integracao",
        nome_tabela="FatoControleContratosItensEuromidia",
        nome_coluna="IDFatoContratoEmpresaRelacionada",
    ):
        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                   SET IDFatoContratoEmpresaRelacionada = :id_rel_principal,
                       DataAtualizacao = GETDATE()
                 WHERE IDFatoControleContratosItensEuromidia = :id_item;
            """),
            {"id_rel_principal": int(id_rel_principal), "id_item": int(id_item)},
        )

    if id_rel_principal not in (None, "", 0) and _coluna_silver_existe_admin(
        banco="Integracao",
        nome_tabela="FatoSolicitacaoContratoItemEuromidia",
        nome_coluna="IDFatoContratoEmpresaRelacionada",
    ) and item.get("IDFatoSolicitacaoContratoItemEuromidia") not in (None, "", 0):
        db.session.execute(
            text("""
                UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]
                   SET IDFatoContratoEmpresaRelacionada = :id_rel_principal,
                       DataAtualizacao = GETDATE()
                 WHERE IDFatoSolicitacaoContratoItemEuromidia = :id_item_solicitacao;
            """),
            {
                "id_rel_principal": int(id_rel_principal),
                "id_item_solicitacao": int(item.get("IDFatoSolicitacaoContratoItemEuromidia")),
            },
        )

    print(
        "APROVACAO_CONTRATO | empresas relacionadas sincronizadas | "
        f"contrato={id_contrato} | item={id_item} | card={id_card} | "
        f"principal={id_rel_principal} | registros={registros}",
        flush=True,
    )

    return {
        "ok": True,
        "id_contrato": int(id_contrato),
        "id_item": int(id_item),
        "id_card": int(id_card) if id_card not in (None, "", 0) else None,
        "id_fato_contrato_empresa_relacionada_principal": id_rel_principal,
        "registros": registros,
    }


def _upsert_vinculo_contrato_card_euromidia(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_controle_contratos_item: int | None,
    id_fato_kanban_card: int | None,
    id_usuario_logado: int | None,
) -> bool:
    """Eu garanto o vínculo entre contrato, item do contrato e card do Kanban."""

    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_item = _int_ou_none(id_fato_controle_contratos_item)
    id_card = _int_ou_none(id_fato_kanban_card)
    id_usuario = _int_ou_none(id_usuario_logado)

    if id_contrato in (None, "", 0) or id_item in (None, "", 0) or id_card in (None, "", 0):
        print(
            "APROVACAO_CONTRATO | vinculo contrato-card ignorado | "
            f"id_contrato={id_contrato} | id_item={id_item} | id_card={id_card}",
            flush=True,
        )
        return False

    print(
        "APROVACAO_CONTRATO | garantindo vinculo contrato-card | "
        f"id_contrato={id_contrato} | id_item={id_item} | id_card={id_card} | id_usuario={id_usuario}",
        flush=True,
    )

    resultado_update = db.session.execute(
        text("""
            UPDATE [Integracao].[Silver].[FatoContratoCardEuromidia]
               SET DataAtualizacao = GETDATE(),
                   IDDimUsuarios = COALESCE(:id_usuario, IDDimUsuarios),
                   IDFatoKanbanCard = :id_card
             WHERE IDFatoControleContratosEuromidia = :id_contrato
               AND IDFatoControleContratosItensEuromidia = :id_item
               AND (
                    IDFatoKanbanCard = :id_card
                    OR IDFatoKanbanCard IS NULL
               )
        """),
        {
            "id_contrato": int(id_contrato),
            "id_item": int(id_item),
            "id_card": int(id_card),
            "id_usuario": int(id_usuario) if id_usuario not in (None, "", 0) else None,
        },
    )

    linhas_update = int(getattr(resultado_update, "rowcount", 0) or 0)
    if linhas_update > 0:
        print(
            "APROVACAO_CONTRATO | vinculo contrato-card atualizado | "
            f"id_contrato={id_contrato} | id_item={id_item} | id_card={id_card} | linhas={linhas_update}",
            flush=True,
        )
        return True

    db.session.execute(
        text("""
            IF NOT EXISTS (
                SELECT 1
                FROM [Integracao].[Silver].[FatoContratoCardEuromidia] WITH (UPDLOCK, HOLDLOCK)
                WHERE IDFatoControleContratosEuromidia = :id_contrato
                  AND IDFatoControleContratosItensEuromidia = :id_item
                  AND IDFatoKanbanCard = :id_card
            )
            BEGIN
                INSERT INTO [Integracao].[Silver].[FatoContratoCardEuromidia]
                (
                    IDFatoControleContratosEuromidia,
                    IDFatoControleContratosItensEuromidia,
                    DataAtualizacao,
                    IDDimUsuarios,
                    IDFatoKanbanCard
                )
                VALUES
                (
                    :id_contrato,
                    :id_item,
                    GETDATE(),
                    :id_usuario,
                    :id_card
                );
            END
        """),
        {
            "id_contrato": int(id_contrato),
            "id_item": int(id_item),
            "id_card": int(id_card),
            "id_usuario": int(id_usuario) if id_usuario not in (None, "", 0) else None,
        },
    )

    row_check = db.session.execute(
        text("""
            SELECT TOP (1)
                   IDFatoContratoCardEuromidia,
                   IDFatoControleContratosEuromidia,
                   IDFatoControleContratosItensEuromidia,
                   IDFatoKanbanCard,
                   DataAtualizacao
            FROM [Integracao].[Silver].[FatoContratoCardEuromidia]
            WHERE IDFatoControleContratosEuromidia = :id_contrato
              AND IDFatoControleContratosItensEuromidia = :id_item
              AND IDFatoKanbanCard = :id_card
            ORDER BY IDFatoContratoCardEuromidia DESC
        """),
        {
            "id_contrato": int(id_contrato),
            "id_item": int(id_item),
            "id_card": int(id_card),
        },
    ).mappings().first()

    if row_check:
        print(
            "APROVACAO_CONTRATO | vinculo contrato-card confirmado | "
            f"IDFatoContratoCardEuromidia={row_check.get('IDFatoContratoCardEuromidia')} | "
            f"id_contrato={row_check.get('IDFatoControleContratosEuromidia')} | "
            f"id_item={row_check.get('IDFatoControleContratosItensEuromidia')} | "
            f"id_card={row_check.get('IDFatoKanbanCard')} | "
            f"DataAtualizacao={row_check.get('DataAtualizacao')}",
            flush=True,
        )
        return True

    print(
        "APROVACAO_CONTRATO | ATENCAO vinculo contrato-card nao confirmado apos upsert | "
        f"id_contrato={id_contrato} | id_item={id_item} | id_card={id_card}",
        flush=True,
    )
    return False


def _bit_ativo_campanha_admin(valor) -> int:
    """Normaliza BitAtivo para 1 ou 0, aceitando int, bool e texto."""
    if isinstance(valor, bool):
        return 1 if valor else 0

    texto = _texto_ou_vazio(valor).lower()
    if texto in {"0", "false", "falso", "não", "nao", "no", "n"}:
        return 0

    inteiro = _int_ou_none(valor)
    if inteiro is not None:
        return 1 if inteiro != 0 else 0

    return 1


def _resolver_id_status_campanha_aprovada_admin(
    *,
    data_inicio_campanha,
    data_termino_previsto,
    bit_ativo,
) -> int:
    """Resolve o status inicial da campanha no momento da aprovação do contrato."""

    if _bit_ativo_campanha_admin(bit_ativo) == 0:
        return ID_STATUS_CAMPANHA_CANCELADA

    data_inicio = _data_ou_none(data_inicio_campanha) if isinstance(data_inicio_campanha, str) else data_inicio_campanha
    data_termino = _data_ou_none(data_termino_previsto) if isinstance(data_termino_previsto, str) else data_termino_previsto

    if hasattr(data_inicio, "date"):
        data_inicio = data_inicio.date()
    if hasattr(data_termino, "date"):
        data_termino = data_termino.date()

    hoje = date.today()

    if data_termino in (None, ""):
        return ID_STATUS_CAMPANHA_SEM_DATA_TERMINO

    if data_inicio not in (None, "") and data_inicio > hoje:
        return ID_STATUS_CAMPANHA_FUTURA

    if data_termino < hoje:
        return ID_STATUS_CAMPANHA_VENCIDA

    return ID_STATUS_CAMPANHA_ATIVA


def _upsert_vencimento_campanha_aprovada_admin(
    *,
    cabecalho_solicitacao: dict,
    item_solicitacao: dict,
    id_contrato_controle: int | None,
    id_item_controle: int | None,
    id_fato_kanban_card: int | None,
    id_dim_tipo_documento: int | None,
) -> dict:
    """Cria/atualiza o vencimento da campanha quando contrato/aditivo é aprovado na fase 4."""

    id_contrato = _int_ou_none(id_contrato_controle)
    id_item = _int_ou_none(id_item_controle)
    id_card = _int_ou_none(id_fato_kanban_card)
    id_tipo_documento = _int_ou_none(id_dim_tipo_documento)

    retorno_ignorado = {
        "ok": True,
        "acao": "ignorado",
        "id_contrato": id_contrato,
        "id_item": id_item,
        "id_card": id_card,
        "id_dim_tipo_documento": id_tipo_documento,
    }

    if id_contrato in (None, "", 0) or id_item in (None, "", 0):
        retorno_ignorado["motivo"] = "contrato_ou_item_nao_resolvido"
        return retorno_ignorado

    if id_tipo_documento not in ID_TIPOS_DOCUMENTO_GERAM_CAMPANHA:
        retorno_ignorado["motivo"] = "tipo_documento_nao_gera_campanha"
        return retorno_ignorado

    if not _card_admin_esta_na_fase_formulario_contrato(id_card):
        retorno_ignorado["motivo"] = "card_nao_esta_na_fase_4"
        return retorno_ignorado

    data_inicio = item_solicitacao.get("DataInicioPrevisto")
    data_termino = item_solicitacao.get("DataTerminoPrevisto")
    bit_ativo = item_solicitacao.get("BitAtivo") if item_solicitacao.get("BitAtivo") is not None else 1

    id_status_campanha = _resolver_id_status_campanha_aprovada_admin(
        data_inicio_campanha=data_inicio,
        data_termino_previsto=data_termino,
        bit_ativo=bit_ativo,
    )

    id_vendedor = _int_ou_none(item_solicitacao.get("IDVendedor"))
    id_empresa = _int_ou_none(item_solicitacao.get("IDEmpresa")) or _int_ou_none(cabecalho_solicitacao.get("IDEmpresa"))
    marca_exibida = (
        _texto_ou_none(item_solicitacao.get("MarcaExibida"))
        or _texto_ou_none(cabecalho_solicitacao.get("MarcaExibida"))
        or _texto_ou_none(item_solicitacao.get("RazaoSocial"))
        or _texto_ou_none(cabecalho_solicitacao.get("RazaoSocial"))
    )

    params = {
        "id_contrato": int(id_contrato),
        "id_item": int(id_item),
        "id_status_campanha": int(id_status_campanha),
        "id_vendedor": int(id_vendedor) if id_vendedor not in (None, "", 0) else None,
        "id_empresa": int(id_empresa) if id_empresa not in (None, "", 0) else None,
        "marca_exibida": marca_exibida,
        "data_inicio": data_inicio,
        "data_termino": data_termino,
        "bit_ativo": _bit_ativo_campanha_admin(bit_ativo),
    }

    db.session.execute(
        text(f"""
            UPDATE vc
               SET vc.IDDimStatusCampanha = :id_status_campanha,
                   vc.IDVendedor = :id_vendedor,
                   vc.IDEmpresa = :id_empresa,
                   vc.MarcaExibida = :marca_exibida,
                   vc.DataInicioCampanha = :data_inicio,
                   vc.DataTerminoPrevisto = :data_termino,
                   vc.DiasParaVencer = CASE
                                           WHEN :data_termino IS NULL THEN NULL
                                           ELSE DATEDIFF(DAY, CONVERT(date, GETDATE()), CONVERT(date, :data_termino))
                                        END,
                   vc.BitAtivo = :bit_ativo,
                   vc.DataAtualizacao = SYSDATETIME()
              FROM {TABELA_VENCIMENTO_CAMPANHA} vc
             WHERE vc.IDFatoControleContratosEuromidia = :id_contrato
               AND vc.IDFatoControleContratosItensEuromidia = :id_item;

            IF @@ROWCOUNT = 0
            BEGIN
                INSERT INTO {TABELA_VENCIMENTO_CAMPANHA}
                (
                    IDFatoControleContratosEuromidia,
                    IDFatoControleContratosItensEuromidia,
                    IDDimStatusCampanha,
                    IDVendedor,
                    IDEmpresa,
                    MarcaExibida,
                    DataInicioCampanha,
                    DataTerminoPrevisto,
                    DiasParaVencer,
                    BitAtivo,
                    DataCriacao,
                    DataAtualizacao
                )
                SELECT
                    :id_contrato,
                    :id_item,
                    :id_status_campanha,
                    :id_vendedor,
                    :id_empresa,
                    :marca_exibida,
                    :data_inicio,
                    :data_termino,
                    CASE
                        WHEN :data_termino IS NULL THEN NULL
                        ELSE DATEDIFF(DAY, CONVERT(date, GETDATE()), CONVERT(date, :data_termino))
                    END,
                    :bit_ativo,
                    SYSDATETIME(),
                    SYSDATETIME()
                WHERE NOT EXISTS
                (
                    SELECT 1
                    FROM {TABELA_VENCIMENTO_CAMPANHA} WITH (UPDLOCK, HOLDLOCK)
                    WHERE IDFatoControleContratosEuromidia = :id_contrato
                      AND IDFatoControleContratosItensEuromidia = :id_item
                );
            END;
        """),
        params,
    )

    row_check = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   IDFatoVencimentoCampanhaEuromidia,
                   IDDimStatusCampanha,
                   DiasParaVencer
            FROM {TABELA_VENCIMENTO_CAMPANHA}
            WHERE IDFatoControleContratosEuromidia = :id_contrato
              AND IDFatoControleContratosItensEuromidia = :id_item
            ORDER BY IDFatoVencimentoCampanhaEuromidia DESC;
        """),
        {
            "id_contrato": int(id_contrato),
            "id_item": int(id_item),
        },
    ).mappings().first()

    print(
        "APROVACAO_CONTRATO | vencimento campanha sincronizado | "
        f"id_contrato={id_contrato} | id_item={id_item} | "
        f"id_status_campanha={id_status_campanha} | id_vencimento={(row_check or {}).get('IDFatoVencimentoCampanhaEuromidia')}",
        flush=True,
    )

    return {
        "ok": True,
        "acao": "upsert",
        "id_contrato": int(id_contrato),
        "id_item": int(id_item),
        "id_card": id_card,
        "id_dim_tipo_documento": id_tipo_documento,
        "id_status_campanha": int(id_status_campanha),
        "id_vencimento_campanha": _int_ou_none((row_check or {}).get("IDFatoVencimentoCampanhaEuromidia")),
        "dias_para_vencer": _int_ou_none((row_check or {}).get("DiasParaVencer")),
    }


def _marcar_vencimento_campanha_origem_renovada_admin(
    *,
    id_vencimento_origem: int | None,
    id_contrato_controle: int | None,
    id_item_controle_origem: int | None,
    id_fato_kanban_card: int | None = None,
) -> dict:
    """Marca a campanha original como renovada/inativa.

    Regra pedida:
    - a campanha antiga não deve aparecer na tela de vencimentos;
    - BitAtivo = 0;
    - IDDimStatusCampanha = 8.
    """

    id_vencimento = _int_ou_none(id_vencimento_origem)
    id_contrato = _int_ou_none(id_contrato_controle)
    id_item_origem = _int_ou_none(id_item_controle_origem)
    id_card = _int_ou_none(id_fato_kanban_card)

    if id_vencimento in (None, "", 0) and (id_contrato in (None, "", 0) or id_item_origem in (None, "", 0)):
        return {
            "ok": True,
            "acao": "ignorado",
            "motivo": "origem_nao_resolvida",
            "id_vencimento_origem": id_vencimento,
            "id_contrato": id_contrato,
            "id_item_origem": id_item_origem,
            "id_card": id_card,
        }

    resultado = db.session.execute(
        text(f"""
            UPDATE vc
               SET vc.IDDimStatusCampanha = :id_status_renovada,
                   vc.BitAtivo = 0,
                   vc.DataAtualizacao = SYSDATETIME()
              FROM {TABELA_VENCIMENTO_CAMPANHA} AS vc
             WHERE
                (
                    :id_vencimento IS NOT NULL
                    AND vc.IDFatoVencimentoCampanhaEuromidia = :id_vencimento
                )
                OR
                (
                    :id_vencimento IS NULL
                    AND :id_contrato IS NOT NULL
                    AND :id_item_origem IS NOT NULL
                    AND vc.IDFatoControleContratosEuromidia = :id_contrato
                    AND vc.IDFatoControleContratosItensEuromidia = :id_item_origem
                );
        """),
        {
            "id_status_renovada": int(ID_STATUS_CAMPANHA_RENOVADA),
            "id_vencimento": int(id_vencimento) if id_vencimento not in (None, "", 0) else None,
            "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
            "id_item_origem": int(id_item_origem) if id_item_origem not in (None, "", 0) else None,
        },
    )

    linhas = int(resultado.rowcount or 0)

    print(
        "APROVACAO_CONTRATO | renovacao: vencimento origem marcado como renovado | "
        f"id_vencimento={id_vencimento} | contrato={id_contrato} | item_origem={id_item_origem} | "
        f"status={ID_STATUS_CAMPANHA_RENOVADA} | linhas={linhas}",
        flush=True,
    )

    return {
        "ok": True,
        "acao": "marcado_renovado" if linhas > 0 else "nao_encontrado",
        "linhas": linhas,
        "id_vencimento_origem": id_vencimento,
        "id_contrato": id_contrato,
        "id_item_origem": id_item_origem,
        "id_card": id_card,
        "id_status_campanha": int(ID_STATUS_CAMPANHA_RENOVADA),
        "bit_ativo": 0,
    }


def _sincronizar_origem_atendimento_ocupacao_admin(
    *,
    id_ocupacao: int | None,
    id_item_controle: int | None,
) -> dict:
    """Preenche IDOrigemAtendimento/IDDimOrigemAtendimento da ocupação quando a coluna existir."""

    id_ocupacao_int = _int_ou_none(id_ocupacao)
    id_item_int = _int_ou_none(id_item_controle)

    if id_ocupacao_int in (None, "", 0) or id_item_int in (None, "", 0):
        return {"ok": True, "acao": "ignorado", "motivo": "ids_invalidos"}

    coluna_destino = None
    if _campanhas_vencimentos_coluna_existe(TABELA_OCUPACAO_PAINEIS_EUROMIDIA_ADMIN, "IDOrigemAtendimento"):
        coluna_destino = "IDOrigemAtendimento"
    elif _campanhas_vencimentos_coluna_existe(TABELA_OCUPACAO_PAINEIS_EUROMIDIA_ADMIN, "IDDimOrigemAtendimento"):
        coluna_destino = "IDDimOrigemAtendimento"

    if not coluna_destino:
        return {
            "ok": True,
            "acao": "ignorado",
            "motivo": "coluna_origem_atendimento_nao_existe_na_ocupacao",
        }

    expr_item_origem = "i.IDDimOrigemAtendimento"
    expr_card_origem = (
        "card.IDDimOrigemAtendimento"
        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDDimOrigemAtendimento")
        else "CAST(NULL AS int)"
    )
    expr_empresa_origem = (
        "emp.IDDimOrigemAtendimento"
        if _campanhas_vencimentos_coluna_existe("[Integracao].[Silver].[DimEmpresas]", "IDDimOrigemAtendimento")
        else "CAST(NULL AS int)"
    )

    valor_origem = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   COALESCE({expr_item_origem}, {expr_card_origem}, {expr_empresa_origem}) AS IDOrigemAtendimentoResolvido
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS i
            LEFT JOIN [Kanban].[Silver].[FatoKanbanCard] AS card
                ON card.IDFatoKanbanCard = i.IDFatoKanbanCard
            LEFT JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
                ON ctr.IDFatoControleContratosEuromidia = i.IDFatoControleContratoEuromidia
            LEFT JOIN [Integracao].[Silver].[DimEmpresas] AS emp
                ON emp.IDEmpresa = ctr.IDEmpresa
            WHERE i.IDFatoControleContratosItensEuromidia = :id_item;
        """),
        {"id_item": int(id_item_int)},
    ).scalar()

    id_origem = _int_ou_none(valor_origem)
    if id_origem in (None, "", 0):
        return {
            "ok": True,
            "acao": "ignorado",
            "motivo": "origem_atendimento_nao_resolvida",
            "coluna": coluna_destino,
        }

    resultado = db.session.execute(
        text(f"""
            UPDATE {TABELA_OCUPACAO_PAINEIS_EUROMIDIA_ADMIN}
               SET {coluna_destino} = :id_origem_atendimento,
                   DataAtualizacao = SYSDATETIME()
             WHERE IDFatoOcupacaoPaineisEuromidia = :id_ocupacao
               AND (
                    {coluna_destino} IS NULL
                    OR {coluna_destino} <> :id_origem_atendimento
               );
        """),
        {
            "id_origem_atendimento": int(id_origem),
            "id_ocupacao": int(id_ocupacao_int),
        },
    )

    return {
        "ok": True,
        "acao": "atualizado" if int(resultado.rowcount or 0) > 0 else "sem_alteracao",
        "id_ocupacao": int(id_ocupacao_int),
        "id_item": int(id_item_int),
        "coluna": coluna_destino,
        "id_origem_atendimento": int(id_origem),
        "linhas": int(resultado.rowcount or 0),
    }




def _upsert_ocupacao_contrato_aprovado_admin(
    *,
    id_contrato_controle: int | None,
    id_item_controle: int | None,
    id_usuario_logado: int | None = None,
) -> dict:
    """
    Registro obrigatório da ocupação quando um contrato é aprovado.

    Regra de negócio:
    - aprovou contrato;
    - o item tem painel/face;
    - o item tem DataInicioPrevisto e DataTerminoPrevisto/DataCancelamento;
    então o item precisa existir em Integracao.Silver.FatoOcupacaoPaineisEuromidia.

    Observação:
    - uso Origem = 'CONTRATO' porque a linha representa ocupação contratual aprovada;
    - não dependo da DAG de prioridade/reservas para gravar esta ocupação;
    - a DAG pode usar esta linha depois para regras futuras, mas a ocupação nasce aqui.
    """

    id_contrato = _int_ou_none(id_contrato_controle)
    id_item = _int_ou_none(id_item_controle)
    id_usuario = _int_ou_none(id_usuario_logado)

    retorno_ignorado = {
        "ok": True,
        "acao": "ignorado",
        "id_contrato": id_contrato,
        "id_item": id_item,
    }

    if id_contrato in (None, "", 0) or id_item in (None, "", 0):
        retorno_ignorado["motivo"] = "contrato_ou_item_nao_resolvido"
        return retorno_ignorado

    sql = text("""
        SET NOCOUNT ON;

        IF OBJECT_ID('tempdb..#FonteOcupacaoContratoAprovado') IS NOT NULL
            DROP TABLE #FonteOcupacaoContratoAprovado;

        SELECT TOP (1)
            DataAtualizacao = CAST(GETDATE() AS datetime2(0)),
            Referencia = COALESCE(
                NULLIF(LTRIM(RTRIM(i.Referencia)), ''),
                CONVERT(varchar(64), HASHBYTES(
                    'SHA2_256',
                    CONCAT(
                        'OCUPACAO_CONTRATO_APROVADO|',
                        COALESCE(CONVERT(varchar(30), i.IDFatoControleContratoEuromidia), ''), '|',
                        COALESCE(CONVERT(varchar(30), i.IDFatoControleContratosItensEuromidia), ''), '|',
                        COALESCE(CONVERT(varchar(30), COALESCE(TRY_CONVERT(int, i.CodPonto), TRY_CONVERT(int, face.CodPonto))), ''), '|',
                        COALESCE(NULLIF(LTRIM(RTRIM(i.CodFace)), ''), NULLIF(LTRIM(RTRIM(face.CodFace)), ''), ''), '|',
                        COALESCE(CONVERT(varchar(10), i.DataInicioPrevisto, 120), ''), '|',
                        COALESCE(CONVERT(varchar(10), COALESCE(i.DataCancelamento, i.DataTerminoPrevisto), 120), '')
                    )
                ), 2)
            ),
            CodPonto = COALESCE(TRY_CONVERT(int, i.CodPonto), TRY_CONVERT(int, face.CodPonto)),
            CodFace = LEFT(COALESCE(NULLIF(LTRIM(RTRIM(i.CodFace)), ''), NULLIF(LTRIM(RTRIM(face.CodFace)), '')), 100),
            IDPainelEuromidia = COALESCE(i.IDPainelEuromidia, face.IDDimPaineisEuromidia),
            Origem = CAST('CONTRATO' AS varchar(20)),
            Status = CAST(
                CASE
                    WHEN i.DataCancelamento IS NULL THEN 'ATIVO'
                    ELSE 'CANCELADO'
                END AS varchar(20)
            ),
            DataInicio = CONVERT(date, i.DataInicioPrevisto),
            DataFim = CONVERT(date, COALESCE(i.DataCancelamento, i.DataTerminoPrevisto)),
            LoopInicio = CAST(NULL AS int),
            LoopFim = CAST(NULL AS int),
            SpanQtd = CAST(NULL AS int),
            Cota = TRY_CONVERT(int, i.Cota),
            MarcaExibida = LEFT(COALESCE(NULLIF(LTRIM(RTRIM(i.MarcaExibida)), ''), NULLIF(LTRIM(RTRIM(c.MarcaExibida)), '')), 200),
            Vendedor = LEFT(NULLIF(LTRIM(RTRIM(i.Vendedor)), ''), 200),
            IDVendedor = i.IDVendedor,
            IDCliente = c.IDEmpresa,
            IDFatoControleContratos = i.IDFatoControleContratoEuromidia,
            NumeroContrato = LEFT(NULLIF(LTRIM(RTRIM(i.NumeroContrato)), ''), 150),
            NumeroPrevia = LEFT(NULLIF(LTRIM(RTRIM(i.NumeroPrevia)), ''), 150),
            TextoOriginal = LEFT(CONCAT(
                'CONTRATO:', COALESCE(i.NumeroContrato,''),
                ' | PREVIA:', COALESCE(i.NumeroPrevia,''),
                ' | PONTO:', COALESCE(CONVERT(varchar(30), COALESCE(TRY_CONVERT(int, i.CodPonto), TRY_CONVERT(int, face.CodPonto))), ''),
                ' | FACE:', COALESCE(NULLIF(LTRIM(RTRIM(i.CodFace)), ''), NULLIF(LTRIM(RTRIM(face.CodFace)), ''), ''),
                ' | ITEM:', COALESCE(CONVERT(varchar(30), i.IDFatoControleContratosItensEuromidia), '')
            ), 1000),
            CriadoEm = CAST(COALESCE(i.DataLancamento, CAST(i.DataAtualizacao AS date), GETDATE()) AS datetime2(0)),
            CriadoPorIDUsuario = COALESCE(:id_usuario_logado, i.IDVendedor, 0),
            ExpiraEm = CAST(NULL AS datetime2(0)),
            CanceladoEm = CASE
                            WHEN i.DataCancelamento IS NULL THEN NULL
                            ELSE CAST(GETDATE() AS datetime2(0))
                          END,
            CanceladoPorIDUsuario = CAST(NULL AS int),
            Observacao = LEFT(COALESCE(NULLIF(LTRIM(RTRIM(i.OBS)), ''), 'Ocupação gerada automaticamente na aprovação do contrato.'), 500),
            Dias = CASE
                     WHEN i.DataInicioPrevisto IS NULL THEN NULL
                     WHEN COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) IS NULL THEN NULL
                     WHEN COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) < i.DataInicioPrevisto THEN NULL
                     ELSE DATEDIFF(day, i.DataInicioPrevisto, COALESCE(i.DataCancelamento, i.DataTerminoPrevisto)) + 1
                   END,
            ReservaOrdemPrioridade = CAST(1 AS int),
            IDFatoOcupacaoOrigem = CAST(NULL AS int),
            IDFatoControleContratosItemOrigem = i.IDFatoControleContratosItensEuromidia,
            TipoVinculoOrigem = CAST('CONTRATO_APROVADO' AS nvarchar(80))
        INTO #FonteOcupacaoContratoAprovado
        FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS i
        LEFT JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS c
            ON c.IDFatoControleContratosEuromidia = i.IDFatoControleContratoEuromidia
        OUTER APPLY
        (
            SELECT TOP (1)
                f.IDDimFacesPaineis,
                f.IDDimPaineisEuromidia,
                f.CodPonto,
                f.CodFace
            FROM [Integracao].[Silver].[DimFacesPaineis] AS f
            WHERE
                (
                    i.IDDimFacesPaineis IS NOT NULL
                    AND f.IDDimFacesPaineis = i.IDDimFacesPaineis
                )
                OR
                (
                    i.IDPainelEuromidia IS NOT NULL
                    AND f.IDDimPaineisEuromidia = i.IDPainelEuromidia
                    AND (
                        NULLIF(LTRIM(RTRIM(i.CodFace)), '') IS NULL
                        OR UPPER(LTRIM(RTRIM(f.CodFace))) = UPPER(LTRIM(RTRIM(i.CodFace)))
                    )
                )
                OR
                (
                    TRY_CONVERT(int, i.CodPonto) IS NOT NULL
                    AND NULLIF(LTRIM(RTRIM(i.CodFace)), '') IS NOT NULL
                    AND TRY_CONVERT(int, f.CodPonto) = TRY_CONVERT(int, i.CodPonto)
                    AND UPPER(LTRIM(RTRIM(f.CodFace))) = UPPER(LTRIM(RTRIM(i.CodFace)))
                )
            ORDER BY
                CASE WHEN i.IDDimFacesPaineis IS NOT NULL AND f.IDDimFacesPaineis = i.IDDimFacesPaineis THEN 0 ELSE 1 END,
                CASE WHEN i.IDPainelEuromidia IS NOT NULL AND f.IDDimPaineisEuromidia = i.IDPainelEuromidia THEN 0 ELSE 1 END,
                f.IDDimFacesPaineis DESC
        ) AS face
        WHERE
            i.IDFatoControleContratosItensEuromidia = :id_item
            AND i.IDFatoControleContratoEuromidia = :id_contrato
            AND i.DataInicioPrevisto IS NOT NULL
            AND COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) IS NOT NULL
            AND COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) >= i.DataInicioPrevisto
            AND COALESCE(i.IDPainelEuromidia, face.IDDimPaineisEuromidia) IS NOT NULL
            AND COALESCE(TRY_CONVERT(int, i.CodPonto), TRY_CONVERT(int, face.CodPonto)) IS NOT NULL
            AND COALESCE(NULLIF(LTRIM(RTRIM(i.CodFace)), ''), NULLIF(LTRIM(RTRIM(face.CodFace)), '')) IS NOT NULL
        ORDER BY
            i.IDFatoControleContratosItensEuromidia DESC;

        UPDATE T
           SET T.DataAtualizacao = S.DataAtualizacao,
               T.Referencia = S.Referencia,
               T.CodPonto = S.CodPonto,
               T.CodFace = S.CodFace,
               T.IDPainelEuromidia = S.IDPainelEuromidia,
               T.Origem = S.Origem,
               T.Status = S.Status,
               T.DataInicio = S.DataInicio,
               T.DataFim = S.DataFim,
               T.LoopInicio = S.LoopInicio,
               T.LoopFim = S.LoopFim,
               T.SpanQtd = S.SpanQtd,
               T.Cota = S.Cota,
               T.MarcaExibida = S.MarcaExibida,
               T.Vendedor = S.Vendedor,
               T.IDVendedor = S.IDVendedor,
               T.IDCliente = S.IDCliente,
               T.IDFatoControleContratos = S.IDFatoControleContratos,
               T.NumeroContrato = S.NumeroContrato,
               T.NumeroPrevia = S.NumeroPrevia,
               T.TextoOriginal = S.TextoOriginal,
               T.ExpiraEm = S.ExpiraEm,
               T.CanceladoEm = S.CanceladoEm,
               T.CanceladoPorIDUsuario = S.CanceladoPorIDUsuario,
               T.Observacao = S.Observacao,
               T.Dias = S.Dias,
               T.ReservaOrdemPrioridade = COALESCE(T.ReservaOrdemPrioridade, S.ReservaOrdemPrioridade),
               T.IDFatoOcupacaoOrigem = S.IDFatoOcupacaoOrigem,
               T.IDFatoControleContratosItemOrigem = S.IDFatoControleContratosItemOrigem,
               T.TipoVinculoOrigem = S.TipoVinculoOrigem
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS T WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN #FonteOcupacaoContratoAprovado AS S
            ON T.Origem = S.Origem
           AND (
                    (
                        T.IDFatoControleContratosItemOrigem IS NOT NULL
                        AND T.IDFatoControleContratosItemOrigem = S.IDFatoControleContratosItemOrigem
                    )
                    OR T.Referencia = S.Referencia
               );

        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
            (
                DataAtualizacao,
                Referencia,
                CodPonto,
                CodFace,
                IDPainelEuromidia,
                Origem,
                Status,
                DataInicio,
                DataFim,
                LoopInicio,
                LoopFim,
                SpanQtd,
                Cota,
                MarcaExibida,
                Vendedor,
                IDVendedor,
                IDCliente,
                IDFatoControleContratos,
                NumeroContrato,
                NumeroPrevia,
                TextoOriginal,
                CriadoEm,
                CriadoPorIDUsuario,
                ExpiraEm,
                CanceladoEm,
                CanceladoPorIDUsuario,
                Observacao,
                Dias,
                ReservaOrdemPrioridade,
                IDFatoOcupacaoOrigem,
                IDFatoControleContratosItemOrigem,
                TipoVinculoOrigem
            )
            SELECT
                S.DataAtualizacao,
                S.Referencia,
                S.CodPonto,
                S.CodFace,
                S.IDPainelEuromidia,
                S.Origem,
                S.Status,
                S.DataInicio,
                S.DataFim,
                S.LoopInicio,
                S.LoopFim,
                S.SpanQtd,
                S.Cota,
                S.MarcaExibida,
                S.Vendedor,
                S.IDVendedor,
                S.IDCliente,
                S.IDFatoControleContratos,
                S.NumeroContrato,
                S.NumeroPrevia,
                S.TextoOriginal,
                S.CriadoEm,
                S.CriadoPorIDUsuario,
                S.ExpiraEm,
                S.CanceladoEm,
                S.CanceladoPorIDUsuario,
                S.Observacao,
                S.Dias,
                S.ReservaOrdemPrioridade,
                S.IDFatoOcupacaoOrigem,
                S.IDFatoControleContratosItemOrigem,
                S.TipoVinculoOrigem
            FROM #FonteOcupacaoContratoAprovado AS S
            WHERE NOT EXISTS
            (
                SELECT 1
                FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS T WITH (UPDLOCK, HOLDLOCK)
                WHERE T.Origem = S.Origem
                  AND (
                        (
                            T.IDFatoControleContratosItemOrigem IS NOT NULL
                            AND T.IDFatoControleContratosItemOrigem = S.IDFatoControleContratosItemOrigem
                        )
                        OR T.Referencia = S.Referencia
                  )
            );
        END;

        SELECT TOP (1)
            T.IDFatoOcupacaoPaineisEuromidia,
            T.Referencia,
            T.Origem,
            T.Status,
            T.CodPonto,
            T.CodFace,
            T.DataInicio,
            T.DataFim,
            T.IDFatoControleContratosItemOrigem
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS T
        INNER JOIN #FonteOcupacaoContratoAprovado AS S
            ON T.Origem = S.Origem
           AND (
                    T.IDFatoControleContratosItemOrigem = S.IDFatoControleContratosItemOrigem
                    OR T.Referencia = S.Referencia
               )
        ORDER BY T.IDFatoOcupacaoPaineisEuromidia DESC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_contrato": int(id_contrato),
            "id_item": int(id_item),
            "id_usuario_logado": int(id_usuario) if id_usuario not in (None, "", 0) else None,
        },
    ).mappings().first()

    if not row:
        diagnostico = db.session.execute(
            text("""
                SELECT TOP (1)
                    i.IDFatoControleContratoEuromidia,
                    i.IDFatoControleContratosItensEuromidia,
                    i.CodPonto,
                    i.CodFace,
                    i.IDPainelEuromidia,
                    i.IDDimFacesPaineis,
                    i.DataInicioPrevisto,
                    i.DataTerminoPrevisto,
                    i.DataCancelamento
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS i
                WHERE i.IDFatoControleContratosItensEuromidia = :id_item
                  AND i.IDFatoControleContratoEuromidia = :id_contrato
            """),
            {"id_contrato": int(id_contrato), "id_item": int(id_item)},
        ).mappings().first()

        current_app.logger.warning(
            "APROVACAO_CONTRATO | ocupação não sincronizada | id_contrato=%s | id_item=%s | diagnostico=%s",
            id_contrato,
            id_item,
            dict(diagnostico or {}),
        )

        return {
            "ok": True,
            "acao": "ignorado",
            "motivo": "item_sem_dados_minimos_para_ocupacao",
            "id_contrato": int(id_contrato),
            "id_item": int(id_item),
            "diagnostico": dict(diagnostico or {}),
        }

    id_ocupacao = _int_ou_none(row.get("IDFatoOcupacaoPaineisEuromidia"))
    status_ocupacao = str(row.get("Status") or "").strip()
    resultado_origem_atendimento_ocupacao = _sincronizar_origem_atendimento_ocupacao_admin(
        id_ocupacao=id_ocupacao,
        id_item_controle=id_item,
    )

    current_app.logger.info(
        "APROVACAO_CONTRATO | ocupação contratual registrada | id_contrato=%s | id_item=%s | id_ocupacao=%s | status=%s | cod_ponto=%s | cod_face=%s | data_inicio=%s | data_fim=%s",
        id_contrato,
        id_item,
        id_ocupacao,
        status_ocupacao,
        row.get("CodPonto"),
        row.get("CodFace"),
        row.get("DataInicio"),
        row.get("DataFim"),
    )

    return {
        "ok": True,
        "acao": "upsert",
        "id_contrato": int(id_contrato),
        "id_item": int(id_item),
        "id_ocupacao": id_ocupacao,
        "referencia": str(row.get("Referencia") or "").strip() or None,
        "origem": str(row.get("Origem") or "").strip() or None,
        "status": status_ocupacao or None,
        "cod_ponto": row.get("CodPonto"),
        "cod_face": row.get("CodFace"),
        "data_inicio": row.get("DataInicio"),
        "data_fim": row.get("DataFim"),
        "origem_atendimento_ocupacao": resultado_origem_atendimento_ocupacao,
    }

def _efetivar_reservas_card_kanban_admin(
    *,
    id_card: int | None,
    id_contrato_controle: int | None,
    id_usuario_logado: int | None,
) -> int:
    """Efetiva reservas vinculadas ao card quando a solicitação é aprovada.

    Regra segura:
    - NÃO usa LIKE com colchetes, porque no SQL Server [] é padrão de busca.
    - Localiza a reserva pelo IDReserva do card ou pelo texto literal usando CHARINDEX.
    - Atualiza Status/Origem/Contrato da reserva alvo.
    - Só altera Observacao quando a observação é técnica da reserva ou está vazia.
    - Não tenta concatenar em observação comercial grande, evitando erro 2628.
    """
    id_card_int = _int_ou_none(id_card)
    if not id_card_int:
        return 0

    id_contrato_int = _int_ou_none(id_contrato_controle)
    id_usuario_int = _int_ou_none(id_usuario_logado)

    marcador_reserva = f"[RESERVA_CARD_ATIVO={int(id_card_int)}]"
    observacao_reserva = f"[RESERVA_CARD_ATIVO={int(id_card_int)}] Reserva informada no Card {int(id_card_int)}."
    observacao_efetivacao = f"Reserva Efetivada pelo Card IDFatoKanbanCard={int(id_card_int)}"
    marcador_efetivado = f"Reserva Efetivada pelo Card IDFatoKanbanCard={int(id_card_int)}"

    resultado = db.session.execute(
        text("""
            ;WITH ReservaCard AS (
                SELECT TOP (1)
                    TRY_CONVERT(int, c.IDReserva) AS IDReserva
                FROM [Kanban].[Silver].[FatoKanbanCard] AS c
                WHERE c.IDFatoKanbanCard = :id_card
            ),
            ReservasAlvo AS (
                SELECT
                    fo.IDFatoOcupacaoPaineisEuromidia
                FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS fo
                WHERE fo.CanceladoEm IS NULL
                  AND UPPER(LTRIM(RTRIM(COALESCE(fo.Status, '')))) <> 'CANCELADO'
                  AND (
                        EXISTS (
                            SELECT 1
                            FROM ReservaCard rc
                            WHERE rc.IDReserva IS NOT NULL
                              AND rc.IDReserva = fo.IDFatoOcupacaoPaineisEuromidia
                        )
                        OR CHARINDEX(:marcador_reserva, COALESCE(fo.Observacao, '')) > 0
                  )
            )
            UPDATE fo
               SET fo.Status = 'ATIVO',
                   fo.Origem = CASE
                       WHEN UPPER(LTRIM(RTRIM(COALESCE(fo.Origem, '')))) = 'RESERVA' THEN 'OCUPACAO'
                       ELSE fo.Origem
                   END,
                   fo.IDFatoControleContratos = COALESCE(fo.IDFatoControleContratos, :id_contrato_controle),
                   fo.NumeroContrato = COALESCE(NULLIF(LTRIM(RTRIM(fo.NumeroContrato)), ''), ctr.NumeroContrato),
                   fo.NumeroPrevia = COALESCE(NULLIF(LTRIM(RTRIM(fo.NumeroPrevia)), ''), ctr.NumeroPrevia),
                   fo.ExpiraEm = NULL,
                   fo.DataAtualizacao = SYSDATETIME(),
                   fo.Observacao = CASE
                       WHEN CHARINDEX(:marcador_efetivado, COALESCE(fo.Observacao, '')) > 0
                           THEN fo.Observacao

                       WHEN CHARINDEX(:marcador_reserva, COALESCE(fo.Observacao, '')) > 0
                           THEN CONCAT(fo.Observacao, ' | ', :observacao_efetivacao)

                       WHEN NULLIF(LTRIM(RTRIM(COALESCE(fo.Observacao, ''))), '') IS NULL
                           THEN CONCAT(:observacao_reserva, ' | ', :observacao_efetivacao)

                       ELSE fo.Observacao
                   END
              FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS fo
              INNER JOIN ReservasAlvo AS alvo
                ON alvo.IDFatoOcupacaoPaineisEuromidia = fo.IDFatoOcupacaoPaineisEuromidia
              LEFT JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
                ON ctr.IDFatoControleContratosEuromidia = :id_contrato_controle;
        """),
        {
            "id_card": int(id_card_int),
            "id_contrato_controle": id_contrato_int,
            "id_usuario_logado": id_usuario_int,
            "marcador_reserva": marcador_reserva,
            "observacao_reserva": observacao_reserva,
            "observacao_efetivacao": observacao_efetivacao,
            "marcador_efetivado": marcador_efetivado,
        },
    )

    return int(resultado.rowcount or 0)

def _mover_solicitacao_aprovada_para_controle(*, id_solicitacao: int, id_usuario_logado: int | None) -> dict:
    cab = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao))
    if not cab:
        raise ValueError("Solicitação não encontrada para aprovação.")

    itens_solicitacao = _obter_itens_solicitacao_brutos(int(id_solicitacao))

    id_contrato_controle = _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
    referencia_informada = _texto_ou_none(cab.get("Referencia"))
    ids_itens_controle: list[int] = []
    precos_praticados: list[dict] = []
    empresas_relacionadas_sincronizadas: list[dict] = []
    vencimentos_campanha: list[dict] = []
    vencimentos_origem_renovados: list[dict] = []
    ocupacoes_sincronizadas: list[dict] = []

    id_card_cabecalho = _resolver_id_card_aprovacao_solicitacao_admin(cab, itens_solicitacao)
    if id_card_cabecalho not in (None, "", 0) and _int_ou_none(cab.get("IDFatoKanbanCard")) in (None, "", 0):
        cab["IDFatoKanbanCard"] = int(id_card_cabecalho)

    # Correção defensiva: solicitações antigas ou criadas antes da correção podem estar
    # sem MarcaExibida. Antes de aprovar, busco a marca salva no card e uso como fallback
    # para cabeçalho e itens. Assim a marca chega em Controle de Contratos e Ocupação.
    marca_card_aprovacao = None
    if id_card_cabecalho not in (None, "", 0):
        try:
            marca_card_aprovacao = db.session.execute(
                text("""
                    SELECT TOP (1)
                        NULLIF(LTRIM(RTRIM(Marca)), '') AS Marca
                    FROM [Kanban].[Silver].[FatoKanbanCard]
                    WHERE IDFatoKanbanCard = :id_card;
                """),
                {"id_card": int(id_card_cabecalho)},
            ).scalar()
        except Exception:
            marca_card_aprovacao = None

    marca_fallback_aprovacao = _texto_ou_none(
        _primeiro_valor_nao_vazio(cab.get("MarcaExibida"), marca_card_aprovacao)
    )
    if marca_fallback_aprovacao:
        marca_fallback_aprovacao = marca_fallback_aprovacao[:200]

    if marca_fallback_aprovacao and not _texto_ou_none(cab.get("MarcaExibida")):
        cab["MarcaExibida"] = marca_fallback_aprovacao

    for item_solicitacao in itens_solicitacao:
        if marca_fallback_aprovacao and not _texto_ou_none(item_solicitacao.get("MarcaExibida")):
            item_solicitacao["MarcaExibida"] = marca_fallback_aprovacao

    contexto_tipo_card = _normalizar_card_renovacao_admin(
        id_card=id_card_cabecalho,
        id_usuario=id_usuario_logado,
        tipo_solicitacao=cab.get("TipoSolicitacao"),
        id_solicitacao=int(id_solicitacao),
    )
    card_tem_tag_renovacao = bool(contexto_tipo_card.get("tem_tag_renovacao"))

    origem_renovacao_cabecalho = _resolver_origem_renovacao_aprovacao_admin(
        id_card=id_card_cabecalho,
        cabecalho_solicitacao=cab,
        item_solicitacao=(itens_solicitacao[0] if itens_solicitacao else None),
        id_contrato_controle=id_contrato_controle,
        cod_ponto=(itens_solicitacao[0].get("CodPonto") if itens_solicitacao else None),
        cod_face=(itens_solicitacao[0].get("CodFace") if itens_solicitacao else None),
    )

    # Segurança extra: se a origem oficial de renovação foi encontrada por marcador/linha de
    # vencimento, trato como RENOVAÇÃO mesmo que o IDFatoKanbanCard não tenha vindo na solicitação.
    if origem_renovacao_cabecalho:
        card_tem_tag_renovacao = True

    card_tem_tag_novo_contrato = bool(contexto_tipo_card.get("tem_tag_novo_contrato")) and not card_tem_tag_renovacao
    inicio_renovacao_padrao = contexto_tipo_card.get("inicio_renovacao")
    tipo_operacional_card = contexto_tipo_card.get("tipo_operacional")

    if card_tem_tag_renovacao:
        inicio_renovacao_padrao = "R"
        tipo_operacional_card = "RENOVACAO"
        cab["TipoSolicitacao"] = "ADITIVO"

        id_contrato_origem_renovacao = _int_ou_none(
            origem_renovacao_cabecalho.get("IDFatoControleContratosEuromidia")
        )
        if id_contrato_origem_renovacao not in (None, "", 0):
            # Renovação não abre contrato novo. Ela reaproveita o contrato de origem e
            # substitui o item antigo por uma nova linha ativa.
            id_contrato_controle = int(id_contrato_origem_renovacao)
            cab["IDFatoControleContratosEuromidia"] = int(id_contrato_origem_renovacao)

    data_aprovacao_sql_server = _obter_data_aprovacao_sql_server_admin()

    referencia_resolvida = referencia_informada
    if not referencia_resolvida:
        if id_contrato_controle not in (None, "", 0):
            referencia_resolvida = _gerar_referencia_contrato_hash(
                id_fato_controle_contratos=int(id_contrato_controle),
                cnpj=cab.get("CNPJ"),
                marca_exibida=cab.get("MarcaExibida"),
                id_empresa=cab.get("IDEmpresa"),
            )
        else:
            referencia_resolvida = _gerar_referencia_contrato_temporaria(
                id_fato_solicitacao=int(id_solicitacao),
                cnpj=cab.get("CNPJ"),
                marca_exibida=cab.get("MarcaExibida"),
                id_empresa=cab.get("IDEmpresa"),
            )

    params_cab = {
        "Referencia": referencia_resolvida,
        "NumeroContrato": cab.get("NumeroContrato"),
        "NumeroPrevia": cab.get("NumeroPrevia"),
        "CNPJ": cab.get("CNPJ"),
        "DataAssinaturaRenovacao": data_aprovacao_sql_server,
        "IDTrimestre": cab.get("IDTrimestre"),
        "DataLancamento": cab.get("DataLancamento"),
        "RazaoSocial": cab.get("RazaoSocial"),
        "CPF": cab.get("CPF"),
        "MarcaExibida": cab.get("MarcaExibida"),
        "Vendedor": cab.get("Vendedor"),
        "TipoDocumento": cab.get("TipoDocumento"),
        "Origem": cab.get("Origem"),
        "SDR": cab.get("SDR"),
        "Agencia": cab.get("Agencia"),
        "CnpjAgencia": cab.get("CnpjAgencia"),
        "Bureau": cab.get("Bureau"),
        "CnpjBureau": cab.get("CnpjBureau"),
        "Intermediario": cab.get("Intermediario"),
        "CnpjIntermediario": cab.get("CnpjIntermediario"),
        "QuantidadePontos": cab.get("QuantidadePontos"),
        "QuantidadeFaces": cab.get("QuantidadeFaces"),
        "TotalFaturamentoBrutoMensal": cab.get("TotalFaturamentoBrutoMensal"),
        "TotalPercentualPermuta": cab.get("TotalPercentualPermuta"),
        "TotalCotaOportunidade": cab.get("TotalCotaOportunidade"),
        "TotalValorPermuta": cab.get("TotalValorPermuta"),
        "TotalFaturamentoLiquidoPermuta": cab.get("TotalFaturamentoLiquidoPermuta"),
        "TotalBrutoContrato": cab.get("TotalBrutoContrato"),
        "TotalLiquidoContratoAGBRCTACORDO": cab.get("TotalLiquidoContratoAGBRCTACORDO"),
        "TotalLiquidoContratoAGBRVENDGERCOOR": cab.get("TotalLiquidoContratoAGBRVENDGERCOOR"),
        "TotalPercentualAgencia": cab.get("TotalPercentualAgencia"),
        "TotalValorMensalAgencia": cab.get("TotalValorMensalAgencia"),
        "TotalPercentualBureau": cab.get("TotalPercentualBureau"),
        "TotalValorBureauMensal": cab.get("TotalValorBureauMensal"),
        "TotalPercentualCartaAcordo": cab.get("TotalPercentualCartaAcordo"),
        "TotalValorCartaAcordoMensal": cab.get("TotalValorCartaAcordoMensal"),
        "TotalValorOutrasComissoes": cab.get("TotalValorOutrasComissoes"),
        "TotalFaturamentoLiquidoMensal": cab.get("TotalFaturamentoLiquidoMensal"),
        "TotalPercentualComissaoVendedor": cab.get("TotalPercentualComissaoVendedor"),
        "TotalValorVendedor": cab.get("TotalValorVendedor"),
        "ValorVendedorTotal": cab.get("ValorVendedorTotal"),
        "TotalPercentualComissaoCoordenacao": cab.get("TotalPercentualComissaoCoordenacao"),
        "IDEmpresa": cab.get("IDEmpresa"),
        "IDCategoriaMarca": cab.get("IDCategoriaMarca"),
        "BitAtivo": 1,
        "IDEmpresaAgencia": cab.get("IDEmpresaAgencia"),
        "IDEmpresaBureau": cab.get("IDEmpresaBureau"),
    }

    if id_contrato_controle not in (None, "", 0):
        params_update_cab = dict(params_cab)
        params_update_cab["id_contrato_controle"] = int(id_contrato_controle)

        db.session.execute(text("""
            UPDATE [Integracao].[Silver].[FatoControleContratosEuromidia]
               SET DataAtualizacao = GETDATE(),
                   Referencia = :Referencia,
                   NumeroContrato = :NumeroContrato,
                   NumeroPrevia = :NumeroPrevia,
                   CNPJ = :CNPJ,
                   DataAssinaturaRenovacao = :DataAssinaturaRenovacao,
                   IDTrimestre = :IDTrimestre,
                   DataLancamento = GETDATE(),
                   RazaoSocial = :RazaoSocial,
                   CPF = :CPF,
                   MarcaExibida = :MarcaExibida,
                   Vendedor = :Vendedor,
                   TipoDocumento = :TipoDocumento,
                   Origem = :Origem,
                   SDR = :SDR,
                   Agencia = :Agencia,
                   CnpjAgencia = :CnpjAgencia,
                   Bureau = :Bureau,
                   CnpjBureau = :CnpjBureau,
                   Intermediario = :Intermediario,
                   CnpjIntermediario = :CnpjIntermediario,
                   QuantidadePontos = :QuantidadePontos,
                   QuantidadeFaces = :QuantidadeFaces,
                   TotalFaturamentoBrutoMensal = :TotalFaturamentoBrutoMensal,
                   TotalPercentualPermuta = :TotalPercentualPermuta,
                   TotalCotaOportunidade = :TotalCotaOportunidade,
                   TotalValorPermuta = :TotalValorPermuta,
                   TotalFaturamentoLiquidoPermuta = :TotalFaturamentoLiquidoPermuta,
                   TotalBrutoContrato = :TotalBrutoContrato,
                   TotalLiquidoContratoAGBRCTACORDO = :TotalLiquidoContratoAGBRCTACORDO,
                   TotalLiquidoContratoAGBRVENDGERCOOR = :TotalLiquidoContratoAGBRVENDGERCOOR,
                   TotalPercentualAgencia = :TotalPercentualAgencia,
                   TotalValorMensalAgencia = :TotalValorMensalAgencia,
                   TotalPercentualBureau = :TotalPercentualBureau,
                   TotalValorBureauMensal = :TotalValorBureauMensal,
                   TotalPercentualCartaAcordo = :TotalPercentualCartaAcordo,
                   TotalValorCartaAcordoMensal = :TotalValorCartaAcordoMensal,
                   TotalValorOutrasComissoes = :TotalValorOutrasComissoes,
                   TotalFaturamentoLiquidoMensal = :TotalFaturamentoLiquidoMensal,
                   TotalPercentualComissaoVendedor = :TotalPercentualComissaoVendedor,
                   TotalValorVendedor = :TotalValorVendedor,
                   ValorVendedorTotal = :ValorVendedorTotal,
                   TotalPercentualComissaoCoordenacao = :TotalPercentualComissaoCoordenacao,
                   IDEmpresa = :IDEmpresa,
                   IDCategoriaMarca = :IDCategoriaMarca,
                   IDDimStatusContratos = 2,
                   BitAtivo = :BitAtivo,
                   IDEmpresaAgencia = :IDEmpresaAgencia,
                   IDEmpresaBureau = :IDEmpresaBureau
             WHERE IDFatoControleContratosEuromidia = :id_contrato_controle
        """), params_update_cab)
    else:
        row_novo = db.session.execute(text("""
            INSERT INTO [Integracao].[Silver].[FatoControleContratosEuromidia]
            (
                DataAtualizacao, Referencia, NumeroContrato, NumeroPrevia, CNPJ, DataAssinaturaRenovacao,
                IDTrimestre, DataLancamento, RazaoSocial, CPF, MarcaExibida, Vendedor, TipoDocumento,
                Origem, SDR, Agencia, CnpjAgencia, Bureau, CnpjBureau, Intermediario, CnpjIntermediario,
                QuantidadePontos, QuantidadeFaces, TotalFaturamentoBrutoMensal, TotalPercentualPermuta,
                TotalCotaOportunidade, TotalValorPermuta, TotalFaturamentoLiquidoPermuta, TotalBrutoContrato,
                TotalLiquidoContratoAGBRCTACORDO, TotalLiquidoContratoAGBRVENDGERCOOR, TotalPercentualAgencia,
                TotalValorMensalAgencia, TotalPercentualBureau, TotalValorBureauMensal, TotalPercentualCartaAcordo,
                TotalValorCartaAcordoMensal, TotalValorOutrasComissoes, TotalFaturamentoLiquidoMensal,
                TotalPercentualComissaoVendedor, TotalValorVendedor, ValorVendedorTotal, TotalPercentualComissaoCoordenacao,
                IDEmpresa, IDCategoriaMarca, IDDimStatusContratos, BitAtivo, IDEmpresaAgencia, IDEmpresaBureau
            )
            OUTPUT INSERTED.IDFatoControleContratosEuromidia AS id_contrato_controle
            VALUES
            (
                GETDATE(), :Referencia, :NumeroContrato, :NumeroPrevia, :CNPJ, :DataAssinaturaRenovacao,
                :IDTrimestre, GETDATE(), :RazaoSocial, :CPF, :MarcaExibida, :Vendedor, :TipoDocumento,
                :Origem, :SDR, :Agencia, :CnpjAgencia, :Bureau, :CnpjBureau, :Intermediario, :CnpjIntermediario,
                :QuantidadePontos, :QuantidadeFaces, :TotalFaturamentoBrutoMensal, :TotalPercentualPermuta,
                :TotalCotaOportunidade, :TotalValorPermuta, :TotalFaturamentoLiquidoPermuta, :TotalBrutoContrato,
                :TotalLiquidoContratoAGBRCTACORDO, :TotalLiquidoContratoAGBRVENDGERCOOR, :TotalPercentualAgencia,
                :TotalValorMensalAgencia, :TotalPercentualBureau, :TotalValorBureauMensal, :TotalPercentualCartaAcordo,
                :TotalValorCartaAcordoMensal, :TotalValorOutrasComissoes, :TotalFaturamentoLiquidoMensal,
                :TotalPercentualComissaoVendedor, :TotalValorVendedor, :ValorVendedorTotal, :TotalPercentualComissaoCoordenacao,
                :IDEmpresa, :IDCategoriaMarca, 2, :BitAtivo, :IDEmpresaAgencia, :IDEmpresaBureau
            )
        """), params_cab).mappings().first()

        id_contrato_controle = int(row_novo.get("id_contrato_controle") or 0) if row_novo else None

    if id_contrato_controle in (None, "", 0):
        raise RuntimeError("Não foi possível resolver o ID do contrato de controle.")

    referencia_final = referencia_informada or _gerar_referencia_contrato_hash(
        id_fato_controle_contratos=int(id_contrato_controle),
        cnpj=cab.get("CNPJ"),
        marca_exibida=cab.get("MarcaExibida"),
        id_empresa=cab.get("IDEmpresa"),
    )

    db.session.execute(
        text("""
            UPDATE [Integracao].[Silver].[FatoControleContratosEuromidia]
               SET Referencia = :referencia,
                   DataLancamento = GETDATE(),
                   DataAtualizacao = GETDATE(),
                   IDDimStatusContratos = 2
             WHERE IDFatoControleContratosEuromidia = :id_contrato_controle
        """),
        {
            "referencia": referencia_final,
            "id_contrato_controle": int(id_contrato_controle),
        },
    )

    for item in itens_solicitacao:
        item = _aplicar_fallback_layout_item_solicitacao(dict(item), cab)
        if _valor_esta_vazio_para_fallback(item.get("CodPonto")):
            item["CodPonto"] = _texto_ou_none(item.get("CodPontoOriginal"))
        if _valor_esta_vazio_para_fallback(item.get("CodFace")):
            item["CodFace"] = _texto_ou_none(item.get("CodFaceOriginal"))
        if item.get("CodFace"):
            item["CodFace"] = str(item.get("CodFace")).strip().upper()

        id_item_solicitacao = _int_ou_none(item.get("IDFatoSolicitacaoContratoItemEuromidia"))
        id_item_controle_origem = _int_ou_none(item.get("IDFatoControleContratosItensEuromidia"))

        if _valor_esta_vazio_para_fallback(item.get("CodPonto")) or _valor_esta_vazio_para_fallback(item.get("CodFace")):
            raise RuntimeError(
                "Não foi possível aprovar a solicitação porque um item está sem CodPonto/CodFace. "
                f"IDSolicitacao={id_solicitacao}; IDItemSolicitacao={id_item_solicitacao}; "
                f"IDCard={item.get('IDFatoKanbanCard') or cab.get('IDFatoKanbanCard')}. "
                "Reabra o card, confirme o painel/face e salve novamente."
            )

        origem_renovacao_item = {}
        if card_tem_tag_renovacao:
            origem_renovacao_item = _resolver_origem_renovacao_aprovacao_admin(
                id_card=(item.get("IDFatoKanbanCard") or cab.get("IDFatoKanbanCard") or id_card_cabecalho),
                cabecalho_solicitacao=cab,
                item_solicitacao=item,
                id_contrato_controle=id_contrato_controle,
                cod_ponto=item.get("CodPonto"),
                cod_face=item.get("CodFace"),
            ) or origem_renovacao_cabecalho or {}

            id_contrato_origem_item = _int_ou_none(origem_renovacao_item.get("IDFatoControleContratosEuromidia"))
            id_item_origem_item = _int_ou_none(origem_renovacao_item.get("IDFatoControleContratosItensEuromidia"))

            if id_contrato_origem_item not in (None, "", 0):
                id_contrato_controle = int(id_contrato_origem_item)
                item["IDFatoControleContratosEuromidia"] = int(id_contrato_origem_item)

            if id_item_origem_item not in (None, "", 0):
                id_item_controle_origem = int(id_item_origem_item)
                item["IDFatoControleContratosItensEuromidia"] = int(id_item_origem_item)

            item["InicioRenovacao"] = "R"
            item["BitAtivo"] = 1
            item["DataAssinaturaRenovacao"] = data_aprovacao_sql_server
            item["DataInicioPrevisto"] = data_aprovacao_sql_server

            current_app.logger.warning(
                "APROVACAO_CONTRATO | origem renovacao resolvida | solicitacao=%s | item_solicitacao=%s | "
                "contrato_origem=%s | item_origem=%s | cod_ponto=%s | cod_face=%s | fonte=%s",
                id_solicitacao,
                id_item_solicitacao,
                id_contrato_controle,
                id_item_controle_origem,
                item.get("CodPonto"),
                item.get("CodFace"),
                origem_renovacao_item.get("fonte_origem_renovacao"),
            )

        row_item_existente = _buscar_item_controle_existente_aprovacao_admin(
            id_contrato_controle=int(id_contrato_controle),
            id_item_controle_origem=id_item_controle_origem,
            cod_ponto=item.get("CodPonto"),
            cod_face=item.get("CodFace"),
            somente_ativos=bool(card_tem_tag_renovacao),
        )

        id_item_controle_existente = (
            int(row_item_existente["IDFatoControleContratosItensEuromidia"])
            if row_item_existente and row_item_existente.get("IDFatoControleContratosItensEuromidia") is not None
            else None
        )

        id_item_referencia_atual = None if card_tem_tag_renovacao else id_item_controle_existente
        referencia_atual_item = None if card_tem_tag_renovacao else (row_item_existente or {}).get("ReferenciaAtual")

        referencia_item_resolvida = _resolver_referencia_item_controle(
            id_fato_controle_contratos=int(id_contrato_controle),
            id_item_controle_atual=id_item_referencia_atual,
            id_item_solicitacao=id_item_solicitacao,
            referencia_informada=item.get("Referencia"),
            referencia_contrato=referencia_final,
            referencia_atual=referencia_atual_item,
            cod_ponto=item.get("CodPonto"),
            cod_face=item.get("CodFace"),
            id_painel=item.get("IDPainelEuromidia"),
            id_face=item.get("IDDimFacesPaineis"),
            cnpj=item.get("CNPJ") or cab.get("CNPJ"),
        )

        print(
            "APROVACAO_CONTRATO | item controle | "
            f"solicitacao={id_solicitacao} | "
            f"item_solicitacao={id_item_solicitacao} | "
            f"contrato={id_contrato_controle} | "
            f"item_existente={id_item_controle_existente} | "
            f"cod_ponto={item.get('CodPonto')} | "
            f"cod_face={item.get('CodFace')} | "
            f"referencia_item={referencia_item_resolvida}",
            flush=True,
        )

        id_dim_tipo_documento_item = _resolver_id_dim_tipo_documento_solicitacao_admin(
            id_solicitacao=int(id_solicitacao),
            cabecalho_solicitacao=cab,
            item_solicitacao=item,
        )

        id_dim_origem_atendimento_item = _resolver_id_origem_atendimento_aprovacao_admin(
            cabecalho_solicitacao=cab,
            item_solicitacao=item,
            origem_renovacao=origem_renovacao_item if card_tem_tag_renovacao else origem_renovacao_cabecalho,
            id_card=(item.get("IDFatoKanbanCard") or cab.get("IDFatoKanbanCard") or id_card_cabecalho),
        )

        periodo_item_aprovacao = _resolver_periodo_item_aprovacao_admin(
            item_solicitacao=item,
            data_aprovacao_sql_server=data_aprovacao_sql_server,
            eh_renovacao=bool(card_tem_tag_renovacao),
            id_solicitacao=int(id_solicitacao),
            id_item_solicitacao=id_item_solicitacao,
        )

        params_item = {
            "IDFatoControleContratoEuromidia": int(id_contrato_controle),
            "Referencia": referencia_item_resolvida,
            "NumeroContrato": item.get("NumeroContrato") or cab.get("NumeroContrato"),
            "NumeroPrevia": item.get("NumeroPrevia") or cab.get("NumeroPrevia"),
            "CNPJ": item.get("CNPJ") or cab.get("CNPJ"),
            "CodPonto": item.get("CodPonto"),
            "CodFace": item.get("CodFace"),
            "DataLancamento": item.get("DataLancamento") or cab.get("DataLancamento"),
            "Cota": item.get("Cota") or item.get("ExibicoesDia") or item.get("exibicoes_dia"),
            "CidadeExibicao": item.get("CidadeExibicao"),
            "Tipo": item.get("Tipo"),
            "Origem": item.get("Origem") or cab.get("Origem"),
            "EmpresaEuro": item.get("EmpresaEuro"),
            "CnpjExibibora": item.get("CnpjExibibora"),
            "TipoDocumento": item.get("TipoDocumento") or cab.get("TipoDocumento"),
            "RazaoSocial": item.get("RazaoSocial") or cab.get("RazaoSocial"),
            "CPF": item.get("CPF") or cab.get("CPF"),
            "MarcaExibida": item.get("MarcaExibida") or cab.get("MarcaExibida"),
            "Vendedor": item.get("Vendedor") or cab.get("Vendedor"),
            "SDR": item.get("SDR") or cab.get("SDR"),
            "Agencia": item.get("Agencia") or cab.get("Agencia"),
            "CnpjAgencia": item.get("CnpjAgencia") or cab.get("CnpjAgencia"),
            "Bureau": item.get("Bureau") or cab.get("Bureau"),
            "CnpjBureau": item.get("CnpjBureau") or cab.get("CnpjBureau"),
            "Intermediario": item.get("Intermediario") or cab.get("Intermediario"),
            "CnpjIntermediario": item.get("CnpjIntermediario") or cab.get("CnpjIntermediario"),
            "DataAssinaturaRenovacao": periodo_item_aprovacao["DataAssinaturaRenovacao"],
            "IDTrimestre": item.get("IDTrimestre") or cab.get("IDTrimestre"),
            "TexmpoExposicao": item.get("TexmpoExposicao"),
            "DataInicioPrevisto": periodo_item_aprovacao["DataInicioPrevisto"],
            "DataTerminoPrevisto": periodo_item_aprovacao["DataTerminoPrevisto"],
            "InicioRenovacao": inicio_renovacao_padrao or item.get("InicioRenovacao"),
            "FaturamentoBrutoMensal": item.get("FaturamentoBrutoMensal"),
            "PercentualPermuta": item.get("PercentualPermuta"),
            "CotaOportunidade": item.get("CotaOportunidade"),
            "ValorPermuta": item.get("ValorPermuta"),
            "FaturamentoLiquidoPermuta": item.get("FaturamentoLiquidoPermuta"),
            "NumeroParcelas": item.get("NumeroParcelas"),
            "DataInicioVencimento": item.get("DataInicioVencimento"),
            "TotalBrutoContrato": item.get("TotalBrutoContrato"),
            "TotalLiquidoContratoAGBRCTACORDO": item.get("TotalLiquidoContratoAGBRCTACORDO"),
            "TotalLiquidoContratoAGBRVENDGERCOOR": item.get("TotalLiquidoContratoAGBRVENDGERCOOR"),
            "PercentualAgencia": item.get("PercentualAgencia"),
            "ValorMensalAgencia": item.get("ValorMensalAgencia"),
            "PercentualBureau": item.get("PercentualBureau"),
            "ValorBureauMensal": item.get("ValorBureauMensal"),
            "PercentualCartaAcordo": item.get("PercentualCartaAcordo"),
            "ValorCartaAcordoMensal": item.get("ValorCartaAcordoMensal"),
            "ValorOutrasComissoes": item.get("ValorOutrasComissoes"),
            "FaturamentoLiquidoMensal": item.get("FaturamentoLiquidoMensal"),
            "PercentualComissaoVendedor": item.get("PercentualComissaoVendedor"),
            "ValorVendedor": item.get("ValorVendedor"),
            "ValorVendedorTotal": item.get("ValorVendedorTotal"),
            "PercentualComissaoCoordenacao": item.get("PercentualComissaoCoordenacao"),
            "ValorCoordenador": item.get("ValorCoordenador"),
            "ValorCoordenadorTotal": item.get("ValorCoordenadorTotal"),
            "PercentualComissaoGerencia": item.get("PercentualComissaoGerencia"),
            "ValorGerencia": item.get("ValorGerencia"),
            "ValorGerenciaTotal": item.get("ValorGerenciaTotal"),
            "AtivoCancelamento": _normalizar_ativo_cancelamento_aprovacao(item.get("AtivoCancelamento")),
            "FaturamentoLiquidoFinalMensal": item.get("FaturamentoLiquidoFinalMensal"),
            "ComissaoGerenciaNordeste": item.get("ComissaoGerenciaNordeste"),
            "Faturamento": item.get("Faturamento"),
            "DataCancelamento": item.get("DataCancelamento"),
            "OBS": _obs_item_controle_aprovacao_admin(item.get("OBS")),
            "IDVendedor": item.get("IDVendedor"),
            "IDPainelEuromidia": item.get("IDPainelEuromidia"),
            "IDDimFacesPaineis": item.get("IDDimFacesPaineis"),
            "DataFimEfetiva": item.get("DataFimEfetiva"),
            "Status": _normalizar_status_item_aprovacao(item.get("Status")),
            "IDDimCheckinHistorico": item.get("IDDimCheckinHistorico"),
            "IDFatoKanbanCard": item.get("IDFatoKanbanCard") or cab.get("IDFatoKanbanCard"),
            "IDDimTipoDocumento": id_dim_tipo_documento_item,
            "IDDimOrigemAtendimento": id_dim_origem_atendimento_item,
            "BitAtivo": 1,
            "IDEmpresaAgencia": item.get("IDEmpresaAgencia") or cab.get("IDEmpresaAgencia"),
        }

        if card_tem_tag_renovacao and id_item_controle_existente not in (None, "", 0):
            db.session.execute(
                text("""
                    UPDATE [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                       SET BitAtivo = 0,
                           DataAtualizacao = GETDATE()
                     WHERE IDFatoControleContratosItensEuromidia = :id_item_controle_existente
                """),
                {"id_item_controle_existente": int(id_item_controle_existente)},
            )

            resultado_vencimento_origem = _marcar_vencimento_campanha_origem_renovada_admin(
                id_vencimento_origem=origem_renovacao_item.get("IDFatoVencimentoCampanhaEuromidia"),
                id_contrato_controle=id_contrato_controle,
                id_item_controle_origem=id_item_controle_existente or id_item_controle_origem,
                id_fato_kanban_card=(item.get("IDFatoKanbanCard") or cab.get("IDFatoKanbanCard") or id_card_cabecalho),
            )
            vencimentos_origem_renovados.append(resultado_vencimento_origem)

            print(
                "APROVACAO_CONTRATO | renovacao: item antigo inativado antes de inserir nova linha | "
                f"contrato={id_contrato_controle} | item_antigo={id_item_controle_existente} | "
                f"cod_ponto={item.get('CodPonto')} | cod_face={item.get('CodFace')}",
                flush=True,
            )

        if (not card_tem_tag_renovacao) and row_item_existente and row_item_existente.get("IDFatoControleContratosItensEuromidia") is not None:
            id_item_controle = int(row_item_existente["IDFatoControleContratosItensEuromidia"])

            db.session.execute(
                text("""
                    UPDATE [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                       SET IDFatoControleContratoEuromidia = :IDFatoControleContratoEuromidia,
                           DataAtualizacao = GETDATE(),
                           Referencia = :Referencia,
                           NumeroContrato = :NumeroContrato,
                           NumeroPrevia = :NumeroPrevia,
                           CNPJ = :CNPJ,
                           CodPonto = :CodPonto,
                           CodFace = :CodFace,
                           DataLancamento = :DataLancamento,
                           Cota = :Cota,
                           CidadeExibicao = :CidadeExibicao,
                           Tipo = :Tipo,
                           Origem = :Origem,
                           EmpresaEuro = :EmpresaEuro,
                           CnpjExibibora = :CnpjExibibora,
                           TipoDocumento = :TipoDocumento,
                           RazaoSocial = :RazaoSocial,
                           CPF = :CPF,
                           MarcaExibida = :MarcaExibida,
                           Vendedor = :Vendedor,
                           SDR = :SDR,
                           Agencia = :Agencia,
                           CnpjAgencia = :CnpjAgencia,
                           Bureau = :Bureau,
                           CnpjBureau = :CnpjBureau,
                           Intermediario = :Intermediario,
                           CnpjIntermediario = :CnpjIntermediario,
                           DataAssinaturaRenovacao = :DataAssinaturaRenovacao,
                           IDTrimestre = :IDTrimestre,
                           TexmpoExposicao = :TexmpoExposicao,
                           DataInicioPrevisto = :DataInicioPrevisto,
                           DataTerminoPrevisto = :DataTerminoPrevisto,
                           InicioRenovacao = :InicioRenovacao,
                           FaturamentoBrutoMensal = :FaturamentoBrutoMensal,
                           PercentualPermuta = :PercentualPermuta,
                           CotaOportunidade = :CotaOportunidade,
                           ValorPermuta = :ValorPermuta,
                           FaturamentoLiquidoPermuta = :FaturamentoLiquidoPermuta,
                           NumeroParcelas = :NumeroParcelas,
                           DataInicioVencimento = :DataInicioVencimento,
                           TotalBrutoContrato = :TotalBrutoContrato,
                           TotalLiquidoContratoAGBRCTACORDO = :TotalLiquidoContratoAGBRCTACORDO,
                           TotalLiquidoContratoAGBRVENDGERCOOR = :TotalLiquidoContratoAGBRVENDGERCOOR,
                           PercentualAgencia = :PercentualAgencia,
                           ValorMensalAgencia = :ValorMensalAgencia,
                           PercentualBureau = :PercentualBureau,
                           ValorBureauMensal = :ValorBureauMensal,
                           PercentualCartaAcordo = :PercentualCartaAcordo,
                           ValorCartaAcordoMensal = :ValorCartaAcordoMensal,
                           ValorOutrasComissoes = :ValorOutrasComissoes,
                           FaturamentoLiquidoMensal = :FaturamentoLiquidoMensal,
                           PercentualComissaoVendedor = :PercentualComissaoVendedor,
                           ValorVendedor = :ValorVendedor,
                           ValorVendedorTotal = :ValorVendedorTotal,
                           PercentualComissaoCoordenacao = :PercentualComissaoCoordenacao,
                           ValorCoordenador = :ValorCoordenador,
                           ValorCoordenadorTotal = :ValorCoordenadorTotal,
                           PercentualComissaoGerencia = :PercentualComissaoGerencia,
                           ValorGerencia = :ValorGerencia,
                           ValorGerenciaTotal = :ValorGerenciaTotal,
                           AtivoCancelamento = :AtivoCancelamento,
                           FaturamentoLiquidoFinalMensal = :FaturamentoLiquidoFinalMensal,
                           ComissaoGerenciaNordeste = :ComissaoGerenciaNordeste,
                           Faturamento = :Faturamento,
                           DataCancelamento = :DataCancelamento,
                           OBS = :OBS,
                           IDVendedor = :IDVendedor,
                           IDPainelEuromidia = :IDPainelEuromidia,
                           IDDimFacesPaineis = :IDDimFacesPaineis,
                               Status = :Status,
                           IDDimCheckinHistorico = :IDDimCheckinHistorico,
                           IDFatoKanbanCard = :IDFatoKanbanCard,
                           IDDimTipoDocumento = :IDDimTipoDocumento,
                           IDDimOrigemAtendimento = :IDDimOrigemAtendimento,
                           BitAtivo = :BitAtivo,
                           IDEmpresaAgencia = :IDEmpresaAgencia
                     WHERE IDFatoControleContratosItensEuromidia = :id_item_controle
                """),
                {
                    **params_item,
                    "id_item_controle": int(id_item_controle),
                },
            )
        else:
            row_item_novo = db.session.execute(
                text("""
                    INSERT INTO [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                    (
                        IDFatoControleContratoEuromidia,
                        DataAtualizacao,
                        Referencia,
                        NumeroContrato,
                        NumeroPrevia,
                        CNPJ,
                        CodPonto,
                        CodFace,
                        DataLancamento,
                        Cota,
                        CidadeExibicao,
                        Tipo,
                        Origem,
                        EmpresaEuro,
                        CnpjExibibora,
                        TipoDocumento,
                        RazaoSocial,
                        CPF,
                        MarcaExibida,
                        Vendedor,
                        SDR,
                        Agencia,
                        CnpjAgencia,
                        Bureau,
                        CnpjBureau,
                        Intermediario,
                        CnpjIntermediario,
                        DataAssinaturaRenovacao,
                        IDTrimestre,
                        TexmpoExposicao,
                        DataInicioPrevisto,
                        DataTerminoPrevisto,
                        InicioRenovacao,
                        FaturamentoBrutoMensal,
                        PercentualPermuta,
                        CotaOportunidade,
                        ValorPermuta,
                        FaturamentoLiquidoPermuta,
                        NumeroParcelas,
                        DataInicioVencimento,
                        TotalBrutoContrato,
                        TotalLiquidoContratoAGBRCTACORDO,
                        TotalLiquidoContratoAGBRVENDGERCOOR,
                        PercentualAgencia,
                        ValorMensalAgencia,
                        PercentualBureau,
                        ValorBureauMensal,
                        PercentualCartaAcordo,
                        ValorCartaAcordoMensal,
                        ValorOutrasComissoes,
                        FaturamentoLiquidoMensal,
                        PercentualComissaoVendedor,
                        ValorVendedor,
                        ValorVendedorTotal,
                        PercentualComissaoCoordenacao,
                        ValorCoordenador,
                        ValorCoordenadorTotal,
                        PercentualComissaoGerencia,
                        ValorGerencia,
                        ValorGerenciaTotal,
                        AtivoCancelamento,
                        FaturamentoLiquidoFinalMensal,
                        ComissaoGerenciaNordeste,
                        Faturamento,
                        DataCancelamento,
                        OBS,
                        IDVendedor,
                        IDPainelEuromidia,
                        IDDimFacesPaineis,
                                Status,
                        IDDimCheckinHistorico,
                        IDFatoKanbanCard,
                        IDDimTipoDocumento,
                        IDDimOrigemAtendimento,
                        BitAtivo,
                        IDEmpresaAgencia
                    )
                    OUTPUT INSERTED.IDFatoControleContratosItensEuromidia AS id_item_controle
                    VALUES
                    (
                        :IDFatoControleContratoEuromidia,
                        GETDATE(),
                        :Referencia,
                        :NumeroContrato,
                        :NumeroPrevia,
                        :CNPJ,
                        :CodPonto,
                        :CodFace,
                        :DataLancamento,
                        :Cota,
                        :CidadeExibicao,
                        :Tipo,
                        :Origem,
                        :EmpresaEuro,
                        :CnpjExibibora,
                        :TipoDocumento,
                        :RazaoSocial,
                        :CPF,
                        :MarcaExibida,
                        :Vendedor,
                        :SDR,
                        :Agencia,
                        :CnpjAgencia,
                        :Bureau,
                        :CnpjBureau,
                        :Intermediario,
                        :CnpjIntermediario,
                        :DataAssinaturaRenovacao,
                        :IDTrimestre,
                        :TexmpoExposicao,
                        :DataInicioPrevisto,
                        :DataTerminoPrevisto,
                        :InicioRenovacao,
                        :FaturamentoBrutoMensal,
                        :PercentualPermuta,
                        :CotaOportunidade,
                        :ValorPermuta,
                        :FaturamentoLiquidoPermuta,
                        :NumeroParcelas,
                        :DataInicioVencimento,
                        :TotalBrutoContrato,
                        :TotalLiquidoContratoAGBRCTACORDO,
                        :TotalLiquidoContratoAGBRVENDGERCOOR,
                        :PercentualAgencia,
                        :ValorMensalAgencia,
                        :PercentualBureau,
                        :ValorBureauMensal,
                        :PercentualCartaAcordo,
                        :ValorCartaAcordoMensal,
                        :ValorOutrasComissoes,
                        :FaturamentoLiquidoMensal,
                        :PercentualComissaoVendedor,
                        :ValorVendedor,
                        :ValorVendedorTotal,
                        :PercentualComissaoCoordenacao,
                        :ValorCoordenador,
                        :ValorCoordenadorTotal,
                        :PercentualComissaoGerencia,
                        :ValorGerencia,
                        :ValorGerenciaTotal,
                        :AtivoCancelamento,
                        :FaturamentoLiquidoFinalMensal,
                        :ComissaoGerenciaNordeste,
                        :Faturamento,
                        :DataCancelamento,
                        :OBS,
                        :IDVendedor,
                        :IDPainelEuromidia,
                        :IDDimFacesPaineis,
                                :Status,
                        :IDDimCheckinHistorico,
                        :IDFatoKanbanCard,
                        :IDDimTipoDocumento,
                        :IDDimOrigemAtendimento,
                        :BitAtivo,
                        :IDEmpresaAgencia
                    )
                """),
                params_item,
            ).mappings().first()

            id_item_controle = int(row_item_novo.get("id_item_controle") or 0) if row_item_novo else None

        if id_item_controle in (None, "", 0):
            raise RuntimeError("Não foi possível inserir/atualizar um item do contrato no controle.")

        ids_itens_controle.append(int(id_item_controle))

        resultado_empresa_relacionada = _sincronizar_empresa_relacionada_item_contrato_euromidia(
            cabecalho_solicitacao=cab,
            item_solicitacao=item,
            id_contrato_controle=int(id_contrato_controle),
            id_item_controle=int(id_item_controle),
            id_usuario_logado=id_usuario_logado,
        )
        empresas_relacionadas_sincronizadas.append(resultado_empresa_relacionada)

        resultado_ocupacao_contrato = _upsert_ocupacao_contrato_aprovado_admin(
            id_contrato_controle=int(id_contrato_controle),
            id_item_controle=int(id_item_controle),
            id_usuario_logado=id_usuario_logado,
        )
        ocupacoes_sincronizadas.append(resultado_ocupacao_contrato)

        id_card_vinculo = _int_ou_none(item.get("IDFatoKanbanCard")) or _int_ou_none(cab.get("IDFatoKanbanCard"))
        _upsert_vinculo_contrato_card_euromidia(
            id_fato_controle_contratos=int(id_contrato_controle),
            id_fato_controle_contratos_item=int(id_item_controle),
            id_fato_kanban_card=id_card_vinculo,
            id_usuario_logado=id_usuario_logado,
        )

        resultado_preco_praticado = _upsert_preco_praticado_item_contrato_euromidia(
            cabecalho_solicitacao=cab,
            item_solicitacao=item,
            id_contrato_controle=int(id_contrato_controle),
            id_item_controle=int(id_item_controle),
            id_usuario_logado=id_usuario_logado,
        )
        precos_praticados.append(resultado_preco_praticado)

        id_dim_tipo_documento_campanha = _resolver_id_dim_tipo_documento_solicitacao_admin(
            id_solicitacao=int(id_solicitacao),
            cabecalho_solicitacao=cab,
            item_solicitacao=item,
        )

        resultado_vencimento_campanha = _upsert_vencimento_campanha_aprovada_admin(
            cabecalho_solicitacao=cab,
            item_solicitacao=item,
            id_contrato_controle=int(id_contrato_controle),
            id_item_controle=int(id_item_controle),
            id_fato_kanban_card=id_card_vinculo,
            id_dim_tipo_documento=id_dim_tipo_documento_campanha,
        )
        vencimentos_campanha.append(resultado_vencimento_campanha)

        if id_item_solicitacao not in (None, "", 0):
            db.session.execute(
                text("""
                    UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]
                       SET IDFatoControleContratosEuromidia = :id_contrato_controle,
                           IDFatoControleContratosItensEuromidia = :id_item_controle,
                           BitSolicitacaoAtiva = 0,
                           DataAtualizacao = GETDATE()
                     WHERE IDFatoSolicitacaoContratoItemEuromidia = :id_item_solicitacao
                """),
                {
                    "id_contrato_controle": int(id_contrato_controle),
                    "id_item_controle": int(id_item_controle),
                    "id_item_solicitacao": int(id_item_solicitacao),
                },
            )

    reservas_efetivadas = _efetivar_reservas_card_kanban_admin(
        id_card=_int_ou_none(cab.get("IDFatoKanbanCard")) or _int_ou_none(id_card_cabecalho),
        id_contrato_controle=int(id_contrato_controle),
        id_usuario_logado=id_usuario_logado,
    )

    db.session.execute(text("""
        UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
           SET IDFatoControleContratosEuromidia = :id_contrato_controle,
               IDDimUsuariosAprovacao = :id_usuario_logado,
               IDDimStatusContratos = 2,
               DataAprovacao = GETDATE(),
               StatusSolicitacao = 'APROVADO',
               BitAtivo = 0,
               DataAtualizacao = GETDATE()
         WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
    """), {
        "id_contrato_controle": int(id_contrato_controle),
        "id_usuario_logado": int(id_usuario_logado) if id_usuario_logado not in (None, "", 0) else None,
        "id_solicitacao": int(id_solicitacao),
    })

    return {
        "id_contrato_controle": int(id_contrato_controle),
        "ids_itens_controle": ids_itens_controle,
        "precos_praticados": precos_praticados,
        "empresas_relacionadas_sincronizadas": empresas_relacionadas_sincronizadas,
        "vencimentos_campanha": vencimentos_campanha,
        "vencimentos_origem_renovados": vencimentos_origem_renovados,
        "ocupacoes_sincronizadas": ocupacoes_sincronizadas,
        "reservas_efetivadas": int(reservas_efetivadas or 0),
        "id_card": _int_ou_none(cab.get("IDFatoKanbanCard")) or _int_ou_none(id_card_cabecalho),
        "id_empresa": _int_ou_none(cab.get("IDEmpresa")),
        "id_empresa_proprietaria": _int_ou_none(cab.get("IDEmpresaProprietaria")),
        "tipo_solicitacao": "ADITIVO" if card_tem_tag_renovacao else _tipo_solicitacao_normalizado(cab.get("TipoSolicitacao")),
        "tipo_operacional": "RENOVACAO" if card_tem_tag_renovacao else _tipo_solicitacao_normalizado(cab.get("TipoSolicitacao")),
    }









def _resolver_id_dim_tipo_documento_admin(nome_tipo_documento: str | None, id_empresa_proprietaria: int | None = None) -> int | None:
    """Resolve o IDDimTipoDocumento pelo nome salvo na solicitação."""
    nome = _texto_ou_none(nome_tipo_documento)
    if not nome:
        return None

    id_empresa_proprietaria_int = _int_ou_none(id_empresa_proprietaria)
    ordem_prioridade_empresa = ""
    if id_empresa_proprietaria_int not in (None, "", 0):
        ordem_prioridade_empresa = "CASE WHEN td.IDEmpresaProprietaria = :id_empresa_proprietaria THEN 0 ELSE 1 END,"

    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   td.IDDimTipoDocumento
            FROM [Integracao].[Silver].[DimTipoDocumento] td
            WHERE UPPER(LTRIM(RTRIM(CONVERT(nvarchar(200), td.NomeTipoDocumento)))) COLLATE Latin1_General_CI_AI
                = UPPER(LTRIM(RTRIM(CONVERT(nvarchar(200), :nome_tipo_documento)))) COLLATE Latin1_General_CI_AI
              AND ISNULL(td.BitAtivo, 1) = 1
              AND (
                    :id_empresa_proprietaria IS NULL
                    OR td.IDEmpresaProprietaria = :id_empresa_proprietaria
                    OR td.IDEmpresaProprietaria IS NULL
                  )
            ORDER BY
                {ordem_prioridade_empresa}
                td.IDDimTipoDocumento ASC;
        """),
        {
            "nome_tipo_documento": nome,
            "id_empresa_proprietaria": int(id_empresa_proprietaria_int) if id_empresa_proprietaria_int not in (None, "", 0) else None,
        },
    ).mappings().first()

    return _int_ou_none(row.get("IDDimTipoDocumento")) if row else None


def _resolver_id_dim_tipo_documento_por_fragmento_admin(
    fragmento_nome_tipo_documento: str | None,
    id_empresa_proprietaria: int | None = None,
) -> int | None:
    """Resolve IDDimTipoDocumento por trecho do nome, usado só como fallback controlado.

    Exemplo prático:
    - se o card é Renovação/tag 17, o tipo documental esperado é um tipo com
      nome parecido com "Aditivo";
    - se o card é Novo Contrato/tag 9, o tipo documental esperado é um tipo com
      nome parecido com "Contrato".

    Não uso ID fixo aqui. Eu ainda busco na DimTipoDocumento, respeitando
    IDEmpresaProprietaria e BitAtivo.
    """

    fragmento = _texto_ou_none(fragmento_nome_tipo_documento)
    if not fragmento:
        return None

    id_empresa_proprietaria_int = _int_ou_none(id_empresa_proprietaria)
    ordem_prioridade_empresa = ""
    if id_empresa_proprietaria_int not in (None, "", 0):
        ordem_prioridade_empresa = "CASE WHEN td.IDEmpresaProprietaria = :id_empresa_proprietaria THEN 0 ELSE 1 END,"

    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   td.IDDimTipoDocumento
            FROM [Integracao].[Silver].[DimTipoDocumento] td
            WHERE UPPER(LTRIM(RTRIM(CONVERT(nvarchar(200), td.NomeTipoDocumento)))) COLLATE Latin1_General_CI_AI
                    LIKE ('%' + UPPER(LTRIM(RTRIM(CONVERT(nvarchar(200), :fragmento)))) + '%') COLLATE Latin1_General_CI_AI
              AND ISNULL(td.BitAtivo, 1) = 1
              AND (
                    :id_empresa_proprietaria IS NULL
                    OR td.IDEmpresaProprietaria = :id_empresa_proprietaria
                    OR td.IDEmpresaProprietaria IS NULL
                  )
            ORDER BY
                {ordem_prioridade_empresa}
                LEN(CONVERT(nvarchar(200), td.NomeTipoDocumento)) ASC,
                td.IDDimTipoDocumento ASC;
        """),
        {
            "fragmento": fragmento,
            "id_empresa_proprietaria": int(id_empresa_proprietaria_int) if id_empresa_proprietaria_int not in (None, "", 0) else None,
        },
    ).mappings().first()

    return _int_ou_none(row.get("IDDimTipoDocumento")) if row else None


def _resolver_id_dim_tipo_documento_card_admin(id_fato_kanban_card: int | None) -> int | None:
    """Busca IDDimTipoDocumento direto no card, quando a coluna existe."""

    id_card = _int_ou_none(id_fato_kanban_card)
    if id_card in (None, "", 0):
        return None

    if not _fato_kanban_card_tem_coluna_tipo_documento_admin():
        return None

    row = db.session.execute(
        text("""
            SELECT TOP (1)
                   IDDimTipoDocumento
            FROM [Kanban].[Silver].[FatoKanbanCard]
            WHERE IDFatoKanbanCard = :id_card;
        """),
        {"id_card": int(id_card)},
    ).mappings().first()

    return _int_ou_none(row.get("IDDimTipoDocumento")) if row else None


def _resolver_id_dim_tipo_documento_solicitacao_admin(
    *,
    id_solicitacao: int | None,
    cabecalho_solicitacao: dict | None = None,
    item_solicitacao: dict | None = None,
    id_fato_kanban_card: int | None = None,
    id_fato_controle_contratos: int | None = None,
    ids_itens_controle: list[int] | tuple[int, ...] | None = None,
    tipo_solicitacao: str | None = None,
) -> int | None:
    """Resolve o IDDimTipoDocumento da aprovação com fallback forte e rastreável.

    Ordem de resolução:
    1. item da solicitação;
    2. cabeçalho da solicitação;
    3. itens da solicitação no banco;
    4. itens já gravados no controle de contratos;
    5. card Kanban;
    6. nome textual do tipo de documento;
    7. regra operacional: Renovação/tag 17 => tipo parecido com Aditivo;
       Novo Contrato/tag 9 => tipo parecido com Contrato.

    Importante:
    - não uso ID fixo de DimTipoDocumento;
    - quando uso fallback por "Aditivo"/"Contrato", busco na própria dimensão;
    - isso evita derrubar a aprovação só porque o campo veio vazio na solicitação.
    """

    cab = cabecalho_solicitacao or {}
    item = item_solicitacao or {}

    candidatos_nomes: list[str] = []
    candidatos_ids_itens_controle: list[int] = []

    def _registrar_nome(valor) -> None:
        nome = _texto_ou_none(valor)
        if nome and nome not in candidatos_nomes:
            candidatos_nomes.append(nome)

    def _registrar_id_item_controle(valor) -> None:
        id_item = _int_ou_none(valor)
        if id_item not in (None, "", 0) and int(id_item) not in candidatos_ids_itens_controle:
            candidatos_ids_itens_controle.append(int(id_item))

    def _retornar(id_tipo_documento, fonte: str) -> int | None:
        id_resolvido = _int_ou_none(id_tipo_documento)
        if id_resolvido in (None, "", 0):
            return None

        try:
            current_app.logger.info(
                "APROVACAO_CONTRATO | IDDimTipoDocumento resolvido | fonte=%s | "
                "id_solicitacao=%s | id_card=%s | id_contrato=%s | id_tipo_documento=%s",
                fonte,
                id_solicitacao,
                id_fato_kanban_card,
                id_fato_controle_contratos,
                id_resolvido,
            )
        except Exception:
            pass

        print(
            "APROVACAO_CONTRATO | IDDimTipoDocumento resolvido | "
            f"fonte={fonte} | solicitacao={id_solicitacao} | "
            f"card={id_fato_kanban_card} | contrato={id_fato_controle_contratos} | "
            f"id_tipo_documento={id_resolvido}",
            flush=True,
        )

        return int(id_resolvido)

    # 1) Valor já presente no item em memória.
    id_tipo_documento = _int_ou_none(item.get("IDDimTipoDocumento"))
    if id_tipo_documento not in (None, "", 0):
        return _retornar(id_tipo_documento, "item_memoria.IDDimTipoDocumento")

    # 2) Valor já presente no cabeçalho em memória.
    id_tipo_documento = _int_ou_none(cab.get("IDDimTipoDocumento"))
    if id_tipo_documento not in (None, "", 0):
        return _retornar(id_tipo_documento, "cabecalho_memoria.IDDimTipoDocumento")

    _registrar_nome(item.get("TipoDocumento"))
    _registrar_nome(cab.get("TipoDocumento"))
    _registrar_nome(item.get("TipoSolicitacao"))
    _registrar_nome(cab.get("TipoSolicitacao"))
    _registrar_nome(tipo_solicitacao)

    _registrar_id_item_controle(item.get("IDFatoControleContratosItensEuromidia"))
    for id_item in ids_itens_controle or []:
        _registrar_id_item_controle(id_item)

    id_solicitacao_int = _int_ou_none(id_solicitacao)
    id_card_int = _int_ou_none(id_fato_kanban_card) or _int_ou_none(item.get("IDFatoKanbanCard")) or _int_ou_none(cab.get("IDFatoKanbanCard"))
    id_contrato_int = (
        _int_ou_none(id_fato_controle_contratos)
        or _int_ou_none(item.get("IDFatoControleContratosEuromidia"))
        or _int_ou_none(item.get("IDFatoControleContratoEuromidia"))
        or _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
    )

    id_empresa_proprietaria = (
        _int_ou_none(item.get("IDEmpresaProprietaria"))
        or _int_ou_none(cab.get("IDEmpresaProprietaria"))
    )

    # 3) Itens da solicitação salvos no banco.
    if id_solicitacao_int not in (None, "", 0):
        rows_tipo = db.session.execute(
            text("""
                SELECT
                       fsci.IDDimTipoDocumento,
                       fsci.TipoDocumento,
                       fsci.IDFatoControleContratosItensEuromidia,
                       fsci.IDFatoKanbanCard,
                       fsci.IDEmpresaProprietaria
                FROM [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia] fsci
                WHERE fsci.IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
                ORDER BY
                    CASE
                        WHEN fsci.IDDimTipoDocumento IS NOT NULL AND fsci.IDDimTipoDocumento > 0 THEN 0
                        ELSE 1
                    END,
                    fsci.IDFatoSolicitacaoContratoItemEuromidia ASC;
            """),
            {"id_solicitacao": int(id_solicitacao_int)},
        ).mappings().all()

        for row_tipo in rows_tipo:
            id_tipo_documento = _int_ou_none(row_tipo.get("IDDimTipoDocumento"))
            if id_tipo_documento not in (None, "", 0):
                return _retornar(id_tipo_documento, "solicitacao_item_banco.IDDimTipoDocumento")

            _registrar_nome(row_tipo.get("TipoDocumento"))
            _registrar_id_item_controle(row_tipo.get("IDFatoControleContratosItensEuromidia"))

            if id_card_int in (None, "", 0):
                id_card_int = _int_ou_none(row_tipo.get("IDFatoKanbanCard"))

            if id_empresa_proprietaria in (None, "", 0):
                id_empresa_proprietaria = _int_ou_none(row_tipo.get("IDEmpresaProprietaria"))

    # 4) Itens já gravados/atualizados no controle de contratos.
    ids_csv = ",".join(str(int(x)) for x in candidatos_ids_itens_controle if _int_ou_none(x) not in (None, "", 0))
    if ids_csv or id_contrato_int not in (None, "", 0) or id_card_int not in (None, "", 0):
        rows_controle = db.session.execute(
            text("""
                SELECT TOP (20)
                       i.IDDimTipoDocumento,
                       i.TipoDocumento,
                       i.IDFatoControleContratosItensEuromidia
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
                WHERE
                    (
                        :ids_itens_csv IS NOT NULL
                        AND CHARINDEX(
                            ',' + CONVERT(varchar(30), i.IDFatoControleContratosItensEuromidia) + ',',
                            ',' + :ids_itens_csv + ','
                        ) > 0
                    )
                    OR (
                        :id_contrato IS NOT NULL
                        AND i.IDFatoControleContratoEuromidia = :id_contrato
                    )
                    OR (
                        :id_card IS NOT NULL
                        AND i.IDFatoKanbanCard = :id_card
                    )
                ORDER BY
                    CASE
                        WHEN i.IDDimTipoDocumento IS NOT NULL AND i.IDDimTipoDocumento > 0 THEN 0
                        ELSE 1
                    END,
                    i.IDFatoControleContratosItensEuromidia DESC;
            """),
            {
                "ids_itens_csv": ids_csv or None,
                "id_contrato": int(id_contrato_int) if id_contrato_int not in (None, "", 0) else None,
                "id_card": int(id_card_int) if id_card_int not in (None, "", 0) else None,
            },
        ).mappings().all()

        for row_controle in rows_controle:
            id_tipo_documento = _int_ou_none(row_controle.get("IDDimTipoDocumento"))
            if id_tipo_documento not in (None, "", 0):
                return _retornar(id_tipo_documento, "controle_item.IDDimTipoDocumento")

            _registrar_nome(row_controle.get("TipoDocumento"))

    # 5) Card Kanban.
    id_tipo_documento = _resolver_id_dim_tipo_documento_card_admin(id_card_int)
    if id_tipo_documento not in (None, "", 0):
        return _retornar(id_tipo_documento, "kanban_card.IDDimTipoDocumento")

    # 6) Nomes textuais encontrados no fluxo.
    for nome_tipo_documento in candidatos_nomes:
        id_tipo_documento = _resolver_id_dim_tipo_documento_admin(
            nome_tipo_documento,
            id_empresa_proprietaria,
        )
        if id_tipo_documento not in (None, "", 0):
            return _retornar(id_tipo_documento, f"DimTipoDocumento.nome_exato:{nome_tipo_documento}")

    # 7) Fallback operacional por tag/tipo de solicitação, sem usar ID fixo.
    tipo_solicitacao_norm = _tipo_solicitacao_normalizado(tipo_solicitacao or cab.get("TipoSolicitacao") or item.get("TipoSolicitacao"))
    card_eh_renovacao = _card_eh_renovacao_admin(id_card_int)
    card_tem_tag_aditivo = _card_possui_tag_ativa_admin(id_card_int, ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN)
    card_tem_tag_novo = _card_possui_tag_ativa_admin(id_card_int, ID_TAG_TIPO_CONTRATO_NOVO_ADMIN)

    if card_eh_renovacao or card_tem_tag_aditivo or tipo_solicitacao_norm in ("ADITIVO", "RENOVACAO", "RENOVAÇÃO"):
        for fragmento in ("ADITIVO", "ADITIV", "RENOVA"):
            id_tipo_documento = _resolver_id_dim_tipo_documento_por_fragmento_admin(
                fragmento,
                id_empresa_proprietaria,
            )
            if id_tipo_documento not in (None, "", 0):
                return _retornar(id_tipo_documento, f"DimTipoDocumento.fragmento:{fragmento}")

    if card_tem_tag_novo or tipo_solicitacao_norm in ("NOVO", "NOVO CONTRATO", "CONTRATO NOVO", "CONTRATO"):
        for fragmento in ("CONTRATO",):
            id_tipo_documento = _resolver_id_dim_tipo_documento_por_fragmento_admin(
                fragmento,
                id_empresa_proprietaria,
            )
            if id_tipo_documento not in (None, "", 0):
                return _retornar(id_tipo_documento, f"DimTipoDocumento.fragmento:{fragmento}")

    try:
        current_app.logger.error(
            "APROVACAO_CONTRATO | IDDimTipoDocumento não resolvido | "
            "id_solicitacao=%s | id_card=%s | id_contrato=%s | ids_itens_controle=%s | "
            "tipo_solicitacao=%s | nomes_candidatos=%s",
            id_solicitacao,
            id_card_int,
            id_contrato_int,
            ids_csv,
            tipo_solicitacao_norm,
            candidatos_nomes,
        )
    except Exception:
        pass

    print(
        "APROVACAO_CONTRATO | IDDimTipoDocumento NÃO resolvido | "
        f"solicitacao={id_solicitacao} | card={id_card_int} | contrato={id_contrato_int} | "
        f"ids_itens_controle={ids_csv or '-'} | tipo_solicitacao={tipo_solicitacao_norm} | "
        f"nomes_candidatos={candidatos_nomes}",
        flush=True,
    )

    return None



def _fato_kanban_card_tem_coluna_tipo_documento_admin() -> bool:
    """Confirma se FatoKanbanCard possui a coluna IDDimTipoDocumento antes de atualizar."""
    existe = db.session.execute(
        text("""
            SELECT TOP (1) 1
            FROM [Kanban].sys.columns c
            INNER JOIN [Kanban].sys.objects o
                    ON o.object_id = c.object_id
            INNER JOIN [Kanban].sys.schemas s
                    ON s.schema_id = o.schema_id
            WHERE s.name = 'Silver'
              AND o.name = 'FatoKanbanCard'
              AND c.name = 'IDDimTipoDocumento';
        """)
    ).scalar()
    return bool(existe)


def _atualizar_id_tipo_documento_card_admin(
    *,
    id_fato_kanban_card: int | None,
    id_dim_tipo_documento: int | None,
) -> None:
    """Atualiza o IDDimTipoDocumento direto no card quando a coluna existe."""
    id_card = _int_ou_none(id_fato_kanban_card)
    id_tipo_documento = _int_ou_none(id_dim_tipo_documento)

    if id_card in (None, "", 0) or id_tipo_documento in (None, "", 0):
        return

    if not _fato_kanban_card_tem_coluna_tipo_documento_admin():
        current_app.logger.warning(
            "APROVACAO_CONTRATO | FatoKanbanCard sem coluna IDDimTipoDocumento | id_card=%s | id_tipo_documento=%s",
            id_card,
            id_tipo_documento,
        )
        return

    db.session.execute(
        text("""
            UPDATE [Kanban].[Silver].[FatoKanbanCard]
               SET IDDimTipoDocumento = :id_tipo_documento,
                   AtualizadoEm = GETDATE()
             WHERE IDFatoKanbanCard = :id_card
               AND (
                    IDDimTipoDocumento IS NULL
                    OR IDDimTipoDocumento <> :id_tipo_documento
               );
        """),
        {
            "id_card": int(id_card),
            "id_tipo_documento": int(id_tipo_documento),
        },
    )


def _card_admin_esta_na_fase_formulario_contrato(id_fato_kanban_card: int | None) -> bool:
    """Confirma que o card ainda está na fase 4 antes de registrar a ocorrência."""
    id_card = _int_ou_none(id_fato_kanban_card)
    if id_card in (None, "", 0):
        return False

    fase_atual = db.session.execute(
        text("""
            SELECT TOP (1) IDDimKanbanFaseAtual
            FROM [Kanban].[Silver].[FatoKanbanCard]
            WHERE IDFatoKanbanCard = :id_card;
        """),
        {"id_card": int(id_card)},
    ).scalar()

    return _int_ou_none(fase_atual) == ID_FASE_FORMULARIO_CONTRATO


def _registrar_ocorrencia_card_tipo_documento_admin(
    *,
    id_fato_kanban_card: int | None,
    id_dim_tipo_documento: int | None,
    id_usuario_logado: int | None = None,
    id_empresa_proprietaria: int | None = None,
    id_fato_solicitacao: int | None = None,
    id_fato_controle_contratos: int | None = None,
    tipo_ocorrencia: str = "APROVADO",
    observacao: str | None = None,
) -> None:
    """Registra a ocorrência do card com o tipo de documento aprovado/removido."""
    id_card = _int_ou_none(id_fato_kanban_card)
    if id_card in (None, "", 0):
        return

    tipo = (tipo_ocorrencia or "").strip().upper() or "APROVADO"
    id_tipo_documento = _int_ou_none(id_dim_tipo_documento)

    if tipo == "APROVADO" and id_tipo_documento in (None, "", 0):
        current_app.logger.error(
            "APROVACAO_CONTRATO | ocorrência do card não registrada porque IDDimTipoDocumento não foi resolvido | "
            "id_card=%s | id_solicitacao=%s | id_contrato=%s",
            id_card,
            id_fato_solicitacao,
            id_fato_controle_contratos,
        )
        print(
            "APROVACAO_CONTRATO | ocorrência do card não registrada porque IDDimTipoDocumento não foi resolvido | "
            f"id_card={id_card} | id_solicitacao={id_fato_solicitacao} | id_contrato={id_fato_controle_contratos}",
            flush=True,
        )
        return

    db.session.execute(
        text(f"""
            INSERT INTO {TABELA_CARD_OCORRENCIA}
            (
                IDFatoKanbanCard,
                IDDimTipoDocumento,
                TipoOcorrencia,
                IDEmpresaProprietaria,
                IDDimUsuarios,
                IDFatoSolicitacaoContratoEuromidia,
                IDFatoControleContratosEuromidia,
                Observacao,
                DataOcorrencia
            )
            VALUES
            (
                :id_card,
                :id_tipo_documento,
                :tipo_ocorrencia,
                :id_empresa_proprietaria,
                :id_usuario,
                :id_solicitacao,
                :id_contrato,
                :observacao,
                SYSDATETIME()
            );
        """),
        {
            "id_card": int(id_card),
            "id_tipo_documento": int(id_tipo_documento) if id_tipo_documento not in (None, "", 0) else None,
            "tipo_ocorrencia": tipo[:30],
            "id_empresa_proprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, "", 0) else None,
            "id_usuario": int(id_usuario_logado) if id_usuario_logado not in (None, "", 0) else None,
            "id_solicitacao": int(id_fato_solicitacao) if id_fato_solicitacao not in (None, "", 0) else None,
            "id_contrato": int(id_fato_controle_contratos) if id_fato_controle_contratos not in (None, "", 0) else None,
            "observacao": (observacao or "")[:500] if observacao else None,
        },
    )


def _aplicar_resultado_aprovacao_no_card(*, id_fato_kanban_card: int | None, id_usuario_logado: int | None, id_empresa_proprietaria: int | None, aprovar: bool) -> None:
    if id_fato_kanban_card in (None, '', 0):
        return

    id_tag_aprovado = 13
    id_tag_reprovado = 16
    id_tag_em_avaliacao = 14

    if aprovar:
        _remover_tag_do_card_admin(id_card=id_fato_kanban_card, id_tag=id_tag_reprovado, id_usuario=id_usuario_logado)
        _remover_tag_do_card_admin(id_card=id_fato_kanban_card, id_tag=id_tag_em_avaliacao, id_usuario=id_usuario_logado)
        _aplicar_tag_no_card_admin(id_card=id_fato_kanban_card, id_tag=id_tag_aprovado, id_usuario=id_usuario_logado, id_empresa_proprietaria=id_empresa_proprietaria)
        return

    _remover_tag_do_card_admin(id_card=id_fato_kanban_card, id_tag=id_tag_aprovado, id_usuario=id_usuario_logado)
    _remover_tag_do_card_admin(id_card=id_fato_kanban_card, id_tag=id_tag_em_avaliacao, id_usuario=id_usuario_logado)
    _aplicar_tag_no_card_admin(id_card=id_fato_kanban_card, id_tag=id_tag_reprovado, id_usuario=id_usuario_logado, id_empresa_proprietaria=id_empresa_proprietaria)




def _form_valor_unico(form, chave: str, padrao=None):
    """Leio um campo vindo do request.form ou do dict serializado enviado ao Celery."""
    if form is None:
        return padrao

    try:
        valor = form.get(chave, padrao)
    except Exception:
        valor = padrao

    if isinstance(valor, (list, tuple)):
        if not valor:
            return padrao
        return valor[0]

    return valor


def _form_lista_valores(form, chave: str) -> list:
    """Leio uma lista de valores tanto do MultiDict do Flask quanto do dict do Celery."""
    if form is None:
        return []

    try:
        if hasattr(form, "getlist"):
            return list(form.getlist(chave))
    except Exception:
        pass

    try:
        valor = form.get(chave, [])
    except Exception:
        return []

    if isinstance(valor, (list, tuple)):
        return list(valor)

    if valor in (None, ""):
        return []

    return [valor]


def _gerar_referencia_agendamento_face_contrato(
    *,
    id_contrato: int,
    id_item_contrato: int,
    sequencia: int,
    data_inicio,
    data_termino,
    referencia_informada: str | None = None,
) -> str | None:
    """Retorno somente a referência informada pelo usuário.

    Regra de negócio: o campo Referencia da tabela
    Integracao.Silver.FatoAgendamentoFaceContrato é um campo livre do usuário.
    Ele não é obrigatório e não deve receber chave técnica inventada pelo sistema.
    """
    ref = _texto_ou_none(referencia_informada)
    return ref[:80] if ref else None



def _referencia_agendamento_pendente_eh_tecnica(valor: str | None) -> bool:
    """Identifico referências técnicas antigas apenas para não exibir ao usuário."""
    v = str(valor or "").strip()
    return v.startswith("SOL_AG_FACE|") or v.startswith("AG_FACE|")


def _desativar_agendamentos_face_pendentes_solicitacao_item(
    *,
    id_solicitacao: int,
    id_item_solicitacao: int,
) -> int:
    """Não uso mais Referencia como chave técnica temporária."""
    return 0


def _sincronizar_agendamentos_face_pendentes_solicitacao_item(
    *,
    id_solicitacao: int,
    id_item_solicitacao: int,
    config: dict,
) -> dict:
    """Não gravo pendência usando o campo Referencia.

    A tabela FatoAgendamentoFaceContrato não tem coluna própria para amarrar
    solicitação/item antes de existir IDFatoControleContratosItensEuromidia.
    Por isso, para respeitar a regra de negócio, eu não uso Referencia como
    chave técnica. A gravação definitiva acontece na aprovação, usando o
    formulário enviado ao Celery depois que o item real do contrato é criado.
    """
    return {
        "inseridos": 0,
        "atualizados": 0,
        "desativados": 0,
        "pendente_sem_gravacao": 1 if config.get("ativo") else 0,
    }


def _migrar_agendamentos_face_pendentes_solicitacao_para_contrato(
    *,
    id_solicitacao: int,
    id_contrato_controle: int | None,
) -> dict:
    """Não migro pendência técnica porque Referencia não é chave de sistema.

    A gravação correta ocorre durante a aprovação usando o form_data enviado ao
    Celery, depois que o item real do contrato já existe.
    """
    return {"migrados": 0, "desativados_pendentes": 0, "itens_processados": 0}


def _extrair_periodos_agendamento_face_contrato_form(form, item_id: int) -> dict:
    """Extraio do formulário os períodos gerados na tela de aprovação do contrato.

    Eu não dependo apenas do campo QuantidadePeriodosOcupacao, porque a tela
    pode gerar períodos mensais + um período restante. Exemplo: usuário pede 3
    períodos mensais em um range de 5 meses; a tela manda 4 linhas: 3 mensais
    + 1 restante. Então eu varro as chaves Agendamento_N__ para não perder a
    linha restante.
    """
    prefixo = f"item_{int(item_id)}__"
    ativo = _texto_ou_vazio(_form_valor_unico(form, f"{prefixo}DividirOcupacaoPeriodos", "0")).strip() == "1"
    quantidade_informada = _int_ou_none(_form_valor_unico(form, f"{prefixo}QuantidadePeriodosOcupacao")) or 0

    if quantidade_informada < 0:
        quantidade_informada = 0

    if quantidade_informada > 120:
        raise ValueError(f"Quantidade de períodos inválida no item {item_id}. O limite é 120.")

    periodos = []

    if not ativo:
        return {
            "ativo": False,
            "quantidade": 0,
            "periodos": periodos,
        }

    sequencias_encontradas: set[int] = set()
    try:
        chaves_form = list(form.keys())
    except Exception:
        chaves_form = []

    padrao_chave = re.compile(rf"^{re.escape(prefixo)}Agendamento_(\d+)__")
    for chave in chaves_form:
        m = padrao_chave.match(str(chave))
        if not m:
            continue
        seq = _int_ou_none(m.group(1))
        if seq not in (None, "", 0):
            sequencias_encontradas.add(int(seq))

    limite = max([quantidade_informada] + list(sequencias_encontradas or [0]))
    if limite > 120:
        raise ValueError(f"Quantidade de períodos inválida no item {item_id}. O limite é 120.")

    for sequencia in range(1, limite + 1):
        base = f"{prefixo}Agendamento_{sequencia}__"
        ordem = _int_ou_none(_form_valor_unico(form, f"{base}Ordem")) or sequencia
        data_inicio = _data_ou_none(_form_valor_unico(form, f"{base}DataInicio"))
        data_termino = _data_ou_none(_form_valor_unico(form, f"{base}DataTermino"))

        if not data_inicio and not data_termino:
            continue

        if not data_inicio or not data_termino:
            raise ValueError(f"Agendamento incompleto no item {item_id}, sequência {sequencia}.")

        if data_termino < data_inicio:
            raise ValueError(f"Data final menor que a inicial no item {item_id}, sequência {sequencia}.")

        situacao = (_texto_ou_none(_form_valor_unico(form, f"{base}Situacao")) or "PROGRAMADO").upper().strip()
        if situacao == "CONCLUÍDO":
            situacao = "CONCLUIDO"

        if situacao not in ("PROGRAMADO", "ATIVO", "CONCLUIDO", "CANCELADO"):
            situacao = "PROGRAMADO"

        periodos.append(
            {
                "sequencia": int(ordem),
                "data_inicio": data_inicio,
                "data_termino": data_termino,
                "situacao": situacao,
                "referencia": _texto_ou_none(_form_valor_unico(form, f"{base}Referencia")),
            }
        )

    return {
        "ativo": True,
        "quantidade": len(periodos),
        "periodos": periodos,
    }


def _sincronizar_agendamentos_face_contrato_por_formulario(
    *,
    id_solicitacao: int,
    form,
    id_usuario_logado: int | None = None,
    id_contrato_controle_resolvido: int | None = None,
) -> dict:
    """Gravo/atualizo a divisão da ocupação em períodos na tabela FatoAgendamentoFaceContrato.

    Regra importante:
    - se ainda não existir IDFatoControleContratosItensEuromidia, eu não tenho relação segura
      com o item final do contrato; nesse caso o item fica pendente e será gravado após a aprovação,
      quando a aprovação resolver o item de controle.
    """
    item_ids = [_int_ou_none(x) for x in _form_lista_valores(form, "item_id")]
    item_ids = [int(x) for x in item_ids if x not in (None, "", 0)]

    if not item_ids:
        return {
            "inseridos": 0,
            "atualizados": 0,
            "desativados": 0,
            "ignorados_sem_item_controle": 0,
            "itens_processados": 0,
        }

    cab = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao)) or {}
    itens_solicitacao = _obter_itens_solicitacao_brutos(int(id_solicitacao))
    mapa_itens = {
        int(item.get("IDFatoSolicitacaoContratoItemEuromidia")): item
        for item in itens_solicitacao
        if _int_ou_none(item.get("IDFatoSolicitacaoContratoItemEuromidia")) not in (None, "", 0)
    }

    sql_desativar_item = text("""
        UPDATE [Integracao].[Silver].[FatoAgendamentoFaceContrato]
           SET BitAtivo = 0,
               DataAtualizado = GETDATE()
         WHERE IDFatoControleContratosEuromidia = :id_contrato
           AND IDFatoControleContratosItensEuromidia = :id_item_contrato
           AND ISNULL(BitAtivo, 1) = 1
    """)

    sql_desativar_fora_form = text("""
        UPDATE [Integracao].[Silver].[FatoAgendamentoFaceContrato]
           SET BitAtivo = 0,
               DataAtualizado = GETDATE()
         WHERE IDFatoControleContratosEuromidia = :id_contrato
           AND IDFatoControleContratosItensEuromidia = :id_item_contrato
           AND ISNULL(BitAtivo, 1) = 1
           AND Sequencia NOT IN :sequencias
    """)

    sql_buscar_existente = text("""
        SELECT TOP 1 IDFatoAgendamentoFaceContrato
        FROM [Integracao].[Silver].[FatoAgendamentoFaceContrato]
        WHERE IDFatoControleContratosEuromidia = :id_contrato
          AND IDFatoControleContratosItensEuromidia = :id_item_contrato
          AND Sequencia = :sequencia
        ORDER BY IDFatoAgendamentoFaceContrato DESC
    """)

    sql_update = text("""
        UPDATE [Integracao].[Silver].[FatoAgendamentoFaceContrato]
           SET DataInicio = :data_inicio,
               DataTermino = :data_termino,
               Situacao = :situacao,
               Referencia = :referencia,
               BitAtivo = 1,
               DataAtualizado = GETDATE()
         WHERE IDFatoAgendamentoFaceContrato = :id_agendamento
    """)

    sql_insert = text("""
        INSERT INTO [Integracao].[Silver].[FatoAgendamentoFaceContrato]
        (
            IDFatoControleContratosEuromidia,
            IDFatoControleContratosItensEuromidia,
            Sequencia,
            DataInicio,
            DataTermino,
            Situacao,
            Referencia,
            BitAtivo,
            DataAtualizado
        )
        VALUES
        (
            :id_contrato,
            :id_item_contrato,
            :sequencia,
            :data_inicio,
            :data_termino,
            :situacao,
            :referencia,
            1,
            GETDATE()
        )
    """)

    total_inseridos = 0
    total_atualizados = 0
    total_desativados = 0
    total_ignorados_sem_item_controle = 0
    total_itens_processados = 0

    for item_id in item_ids:
        item = mapa_itens.get(int(item_id)) or {}
        config = _extrair_periodos_agendamento_face_contrato_form(form, int(item_id))

        id_contrato = (
            _int_ou_none(id_contrato_controle_resolvido)
            or _int_ou_none(item.get("IDFatoControleContratosEuromidia"))
            or _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
        )
        id_item_contrato = _int_ou_none(item.get("IDFatoControleContratosItensEuromidia"))

        if id_contrato in (None, "", 0) or id_item_contrato in (None, "", 0):
            # Não gravo na FatoAgendamentoFaceContrato sem o item real do contrato.
            # Motivo: o campo Referencia é livre do usuário e não pode ser usado
            # como chave técnica temporária. Na aprovação, o form completo é enviado
            # ao Celery e a gravação definitiva ocorre depois que o ContratoItem existe.
            if config.get("ativo"):
                total_ignorados_sem_item_controle += 1
            continue

        total_itens_processados += 1
        total_desativados += _desativar_agendamentos_face_pendentes_solicitacao_item(
            id_solicitacao=int(id_solicitacao),
            id_item_solicitacao=int(item_id),
        )

        if not config.get("ativo"):
            resultado = db.session.execute(
                sql_desativar_item,
                {
                    "id_contrato": int(id_contrato),
                    "id_item_contrato": int(id_item_contrato),
                },
            )
            total_desativados += int(getattr(resultado, "rowcount", 0) or 0)
            continue

        periodos = config.get("periodos") or []

        if not periodos:
            resultado = db.session.execute(
                sql_desativar_item,
                {
                    "id_contrato": int(id_contrato),
                    "id_item_contrato": int(id_item_contrato),
                },
            )
            total_desativados += int(getattr(resultado, "rowcount", 0) or 0)
            continue

        sequencias = []

        for periodo in periodos:
            sequencia = int(periodo["sequencia"])
            sequencias.append(sequencia)
            referencia = _gerar_referencia_agendamento_face_contrato(
                id_contrato=int(id_contrato),
                id_item_contrato=int(id_item_contrato),
                sequencia=sequencia,
                data_inicio=periodo["data_inicio"],
                data_termino=periodo["data_termino"],
                referencia_informada=periodo.get("referencia"),
            )

            existente = db.session.execute(
                sql_buscar_existente,
                {
                    "id_contrato": int(id_contrato),
                    "id_item_contrato": int(id_item_contrato),
                    "sequencia": sequencia,
                },
            ).mappings().first()

            params_agendamento = {
                "id_contrato": int(id_contrato),
                "id_item_contrato": int(id_item_contrato),
                "sequencia": sequencia,
                "data_inicio": periodo["data_inicio"],
                "data_termino": periodo["data_termino"],
                "situacao": periodo["situacao"],
                "referencia": referencia,
            }

            if existente and existente.get("IDFatoAgendamentoFaceContrato") is not None:
                db.session.execute(
                    sql_update,
                    {
                        **params_agendamento,
                        "id_agendamento": int(existente["IDFatoAgendamentoFaceContrato"]),
                    },
                )
                total_atualizados += 1
            else:
                db.session.execute(sql_insert, params_agendamento)
                total_inseridos += 1

        if sequencias:
            placeholders = ",".join(f":seq_{idx}" for idx, _ in enumerate(sequencias))
            params_desativar = {
                "id_contrato": int(id_contrato),
                "id_item_contrato": int(id_item_contrato),
            }
            for idx, seq in enumerate(sequencias):
                params_desativar[f"seq_{idx}"] = int(seq)

            sql_desativar_fora = text(f"""
                UPDATE [Integracao].[Silver].[FatoAgendamentoFaceContrato]
                   SET BitAtivo = 0,
                       DataAtualizado = GETDATE()
                 WHERE IDFatoControleContratosEuromidia = :id_contrato
                   AND IDFatoControleContratosItensEuromidia = :id_item_contrato
                   AND ISNULL(BitAtivo, 1) = 1
                   AND Sequencia NOT IN ({placeholders})
            """)
            resultado = db.session.execute(sql_desativar_fora, params_desativar)
            total_desativados += int(getattr(resultado, "rowcount", 0) or 0)

    return {
        "inseridos": total_inseridos,
        "atualizados": total_atualizados,
        "desativados": total_desativados,
        "ignorados_sem_item_controle": total_ignorados_sem_item_controle,
        "itens_processados": total_itens_processados,
    }


def _adicionar_um_mes_data(dt: date) -> date:
    """Somar um mês sem depender de bibliotecas externas."""
    if not isinstance(dt, date):
        return dt

    ano = dt.year
    mes = dt.month + 1
    if mes > 12:
        mes = 1
        ano += 1

    ultimo_dia_mes_destino = (date(ano + (1 if mes == 12 else 0), 1 if mes == 12 else mes + 1, 1) - timedelta(days=1)).day
    return date(ano, mes, min(dt.day, ultimo_dia_mes_destino))


def _marcar_periodo_restante_visual(item: dict) -> None:
    """Marco visualmente o último período quando ele representa sobra do range.

    Essa marcação é apenas para o layout. A tabela física não tem coluna para
    guardar o tipo do período, então a persistência continua sendo por datas,
    situação e referência do usuário.
    """
    ags = item.get("AgendamentosFaceContrato") or []
    if len(ags) <= 1:
        return

    ultimo = ags[-1]
    data_inicio = ultimo.get("DataInicio")
    data_termino = ultimo.get("DataTermino")
    data_termino_item = item.get("DataTerminoPrevisto")

    if not isinstance(data_inicio, date) or not isinstance(data_termino, date):
        return
    if not isinstance(data_termino_item, date) or data_termino != data_termino_item:
        return

    termino_mensal_teorico = _adicionar_um_mes_data(data_inicio) - timedelta(days=1)
    if data_termino != termino_mensal_teorico:
        ultimo["EhPeriodoRestante"] = True
        ultimo["RotuloPeriodo"] = "RESTANTE"


def _atualizar_bit_fracionado_itens_contrato_por_agendamentos(
    *,
    id_contrato_controle: int | None,
) -> dict:
    """Sincronizo a flag BitFracionado dos itens do contrato com a tabela de divisão por períodos.

    Regra aplicada:
    - item com pelo menos um agendamento ativo em FatoAgendamentoFaceContrato => BitFracionado = 1;
    - item sem agendamento ativo em FatoAgendamentoFaceContrato => BitFracionado = 0.

    Essa rotina roda depois da aprovação resolver os IDs definitivos do contrato e dos itens,
    porque antes disso não existe relação segura com FatoControleContratosItensEuromidia.
    """
    id_contrato = _int_ou_none(id_contrato_controle)
    if id_contrato in (None, "", 0):
        return {
            "itens_atualizados": 0,
            "fracionados": 0,
            "nao_fracionados": 0,
        }

    resultado_update = db.session.execute(
        text("""
            UPDATE item
               SET BitFracionado =
                   CASE
                       WHEN EXISTS
                       (
                           SELECT 1
                           FROM [Integracao].[Silver].[FatoAgendamentoFaceContrato] ag
                           WHERE ag.IDFatoControleContratosEuromidia = item.IDFatoControleContratoEuromidia
                             AND ag.IDFatoControleContratosItensEuromidia = item.IDFatoControleContratosItensEuromidia
                             AND ISNULL(ag.BitAtivo, 1) = 1
                       )
                       THEN 1
                       ELSE 0
                   END,
                   DataAtualizacao = GETDATE()
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] item
            WHERE item.IDFatoControleContratoEuromidia = :id_contrato
        """),
        {"id_contrato": int(id_contrato)},
    )

    resumo = db.session.execute(
        text("""
            SELECT
                SUM(CASE WHEN ISNULL(BitFracionado, 0) = 1 THEN 1 ELSE 0 END) AS fracionados,
                SUM(CASE WHEN ISNULL(BitFracionado, 0) = 0 THEN 1 ELSE 0 END) AS nao_fracionados
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia]
            WHERE IDFatoControleContratoEuromidia = :id_contrato
        """),
        {"id_contrato": int(id_contrato)},
    ).mappings().first()

    return {
        "itens_atualizados": int(getattr(resultado_update, "rowcount", 0) or 0),
        "fracionados": int((resumo or {}).get("fracionados") or 0),
        "nao_fracionados": int((resumo or {}).get("nao_fracionados") or 0),
    }


def _atualizar_solicitacao_contrato_por_formulario(*, id_solicitacao: int, form, id_usuario_logado: int | None) -> None:
    tipo_solicitacao = _tipo_solicitacao_normalizado(form.get("TipoSolicitacao"))
    params_cab = {
        "id_solicitacao": int(id_solicitacao), "TipoSolicitacao": tipo_solicitacao, "Referencia": _texto_ou_none(form.get("Referencia")), "NumeroContrato": _texto_ou_none(form.get("NumeroContrato")),
        "NumeroPrevia": _texto_ou_none(form.get("NumeroPrevia")), "CNPJ": _texto_ou_none(form.get("CNPJ")), "DataAssinaturaRenovacao": _data_ou_none(form.get("DataAssinaturaRenovacao")),
        "IDTrimestre": _texto_ou_none(form.get("IDTrimestre")), "DataLancamento": _data_ou_none(form.get("DataLancamento")), "RazaoSocial": _texto_ou_none(form.get("RazaoSocial")),
        "CPF": _texto_ou_none(form.get("CPF")), "MarcaExibida": _texto_ou_none(form.get("MarcaExibida")), "Vendedor": _texto_ou_none(form.get("Vendedor")),
        "TipoDocumento": _texto_ou_none(form.get("TipoDocumento")), "Origem": _texto_ou_none(form.get("Origem")), "SDR": _texto_ou_none(form.get("SDR")), "Agencia": _texto_ou_none(form.get("Agencia")),
        "CnpjAgencia": _texto_ou_none(form.get("CnpjAgencia")), "Bureau": _texto_ou_none(form.get("Bureau")), "CnpjBureau": _texto_ou_none(form.get("CnpjBureau")),
        "Intermediario": _texto_ou_none(form.get("Intermediario")), "CnpjIntermediario": _texto_ou_none(form.get("CnpjIntermediario")), "QuantidadePontos": _int_ou_none(form.get("QuantidadePontos")),
        "QuantidadeFaces": _int_ou_none(form.get("QuantidadeFaces")), "TotalFaturamentoBrutoMensal": _decimal_ou_none(form.get("TotalFaturamentoBrutoMensal")), "TotalPercentualPermuta": _decimal_ou_none(form.get("TotalPercentualPermuta")),
        "TotalCotaOportunidade": _decimal_ou_none(form.get("TotalCotaOportunidade")), "TotalValorPermuta": _decimal_ou_none(form.get("TotalValorPermuta")), "TotalFaturamentoLiquidoPermuta": _decimal_ou_none(form.get("TotalFaturamentoLiquidoPermuta")),
        "TotalBrutoContrato": _decimal_ou_none(form.get("TotalBrutoContrato")), "TotalLiquidoContratoAGBRCTACORDO": _decimal_ou_none(form.get("TotalLiquidoContratoAGBRCTACORDO")),
        "TotalLiquidoContratoAGBRVENDGERCOOR": _decimal_ou_none(form.get("TotalLiquidoContratoAGBRVENDGERCOOR")), "TotalPercentualAgencia": _decimal_ou_none(form.get("TotalPercentualAgencia")),
        "TotalValorMensalAgencia": _decimal_ou_none(form.get("TotalValorMensalAgencia")), "TotalPercentualBureau": _decimal_ou_none(form.get("TotalPercentualBureau")), "TotalValorBureauMensal": _decimal_ou_none(form.get("TotalValorBureauMensal")),
        "TotalPercentualCartaAcordo": _decimal_ou_none(form.get("TotalPercentualCartaAcordo")), "TotalValorCartaAcordoMensal": _decimal_ou_none(form.get("TotalValorCartaAcordoMensal")),
        "TotalValorOutrasComissoes": _decimal_ou_none(form.get("TotalValorOutrasComissoes")), "TotalFaturamentoLiquidoMensal": _decimal_ou_none(form.get("TotalFaturamentoLiquidoMensal")),
        "TotalPercentualComissaoVendedor": _decimal_ou_none(form.get("TotalPercentualComissaoVendedor")), "TotalValorVendedor": _decimal_ou_none(form.get("TotalValorVendedor")),
        "ValorVendedorTotal": _decimal_ou_none(form.get("ValorVendedorTotal")), "TotalPercentualComissaoCoordenacao": _decimal_ou_none(form.get("TotalPercentualComissaoCoordenacao")), "Observacao": _texto_ou_none(form.get("Observacao")),
        "MotivoRejeicao": _texto_ou_none(form.get("MotivoRejeicao")), "MotivoCancelamento": _texto_ou_none(form.get("MotivoCancelamento")),
    }

    db.session.execute(text("""
        UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
           SET [TipoSolicitacao] = :TipoSolicitacao, [Referencia] = :Referencia, [NumeroContrato] = :NumeroContrato, [NumeroPrevia] = :NumeroPrevia, [CNPJ] = :CNPJ,
               [DataAssinaturaRenovacao] = :DataAssinaturaRenovacao, [IDTrimestre] = :IDTrimestre, [DataLancamento] = :DataLancamento, [RazaoSocial] = :RazaoSocial, [CPF] = :CPF,
               [MarcaExibida] = :MarcaExibida, [Vendedor] = :Vendedor, [TipoDocumento] = :TipoDocumento, [Origem] = :Origem, [SDR] = :SDR, [Agencia] = :Agencia, [CnpjAgencia] = :CnpjAgencia,
               [Bureau] = :Bureau, [CnpjBureau] = :CnpjBureau, [Intermediario] = :Intermediario, [CnpjIntermediario] = :CnpjIntermediario, [QuantidadePontos] = :QuantidadePontos, [QuantidadeFaces] = :QuantidadeFaces,
               [TotalFaturamentoBrutoMensal] = :TotalFaturamentoBrutoMensal, [TotalPercentualPermuta] = :TotalPercentualPermuta, [TotalCotaOportunidade] = :TotalCotaOportunidade, [TotalValorPermuta] = :TotalValorPermuta,
               [TotalFaturamentoLiquidoPermuta] = :TotalFaturamentoLiquidoPermuta, [TotalBrutoContrato] = :TotalBrutoContrato, [TotalLiquidoContratoAGBRCTACORDO] = :TotalLiquidoContratoAGBRCTACORDO,
               [TotalLiquidoContratoAGBRVENDGERCOOR] = :TotalLiquidoContratoAGBRVENDGERCOOR, [TotalPercentualAgencia] = :TotalPercentualAgencia, [TotalValorMensalAgencia] = :TotalValorMensalAgencia,
               [TotalPercentualBureau] = :TotalPercentualBureau, [TotalValorBureauMensal] = :TotalValorBureauMensal, [TotalPercentualCartaAcordo] = :TotalPercentualCartaAcordo, [TotalValorCartaAcordoMensal] = :TotalValorCartaAcordoMensal,
               [TotalValorOutrasComissoes] = :TotalValorOutrasComissoes, [TotalFaturamentoLiquidoMensal] = :TotalFaturamentoLiquidoMensal, [TotalPercentualComissaoVendedor] = :TotalPercentualComissaoVendedor,
               [TotalValorVendedor] = :TotalValorVendedor, [ValorVendedorTotal] = :ValorVendedorTotal, [TotalPercentualComissaoCoordenacao] = :TotalPercentualComissaoCoordenacao, [Observacao] = :Observacao,
               [MotivoRejeicao] = :MotivoRejeicao, [MotivoCancelamento] = :MotivoCancelamento, [DataAtualizacao] = GETDATE()
         WHERE [IDFatoSolicitacaoContratoEuromidia] = :id_solicitacao
    """), params_cab)

    cab_atual_para_fallback = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao)) or {}

    item_ids = form.getlist("item_id")
    itens_atuais_por_id = {
        int(x.get("IDFatoSolicitacaoContratoItemEuromidia")): x
        for x in _obter_itens_solicitacao_brutos(int(id_solicitacao))
        if _int_ou_none(x.get("IDFatoSolicitacaoContratoItemEuromidia")) not in (None, "", 0)
    }

    sql_update_item = text("""
        UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]
           SET [IDFatoControleContratosEuromidia] = COALESCE([IDFatoControleContratosEuromidia], :IDFatoControleContratosEuromidia),
               [IDFatoControleContratosItensEuromidia] = COALESCE([IDFatoControleContratosItensEuromidia], :IDFatoControleContratosItensEuromidia),
               [IDPainelEuromidia] = :IDPainelEuromidia, [IDDimFacesPaineis] = :IDDimFacesPaineis, [CodPonto] = :CodPonto, [CodFace] = :CodFace, [CidadeExibicao] = :CidadeExibicao,
               [Tipo] = :Tipo, [Cota] = :Cota, [DataInicioPrevisto] = :DataInicioPrevisto, [DataTerminoPrevisto] = :DataTerminoPrevisto, [NumeroParcelas] = :NumeroParcelas,
               [DataInicioVencimento] = :DataInicioVencimento, [FaturamentoBrutoMensal] = :FaturamentoBrutoMensal, [PercentualPermuta] = :PercentualPermuta, [ValorPermuta] = :ValorPermuta,
               [FaturamentoLiquidoPermuta] = :FaturamentoLiquidoPermuta, [FaturamentoLiquidoMensal] = :FaturamentoLiquidoMensal, [FaturamentoLiquidoFinalMensal] = :FaturamentoLiquidoFinalMensal,
               [PercentualComissaoVendedor] = :PercentualComissaoVendedor, [ValorVendedor] = :ValorVendedor, [ValorVendedorTotal] = :ValorVendedorTotal, [Status] = :Status, [OBS] = :OBS, [BitAtivo] = :BitAtivo,
               [DataAtualizacao] = GETDATE(), [IDDimUsuariosAtualizacao] = :IDDimUsuariosAtualizacao
         WHERE [IDFatoSolicitacaoContratoItemEuromidia] = :IDFatoSolicitacaoContratoItemEuromidia
           AND [IDFatoSolicitacaoContratoEuromidia] = :IDFatoSolicitacaoContratoEuromidia
    """)

    for item_id_raw in item_ids:
        item_id = _int_ou_none(item_id_raw)
        if item_id is None:
            continue
        prefixo = f"item_{item_id}__"
        item_atual = itens_atuais_por_id.get(int(item_id)) or {}
        fallback_item = _buscar_item_controle_fallback_layout(
            {
                **item_atual,
                "IDFatoKanbanCard": item_atual.get("IDFatoKanbanCard") or cab_atual_para_fallback.get("IDFatoKanbanCard"),
                "IDFatoControleContratosEuromidia": item_atual.get("IDFatoControleContratosEuromidia") or cab_atual_para_fallback.get("IDFatoControleContratosEuromidia"),
                "CodPonto": form.get(f"{prefixo}CodPonto") or item_atual.get("CodPonto") or item_atual.get("CodPontoOriginal"),
                "CodFace": form.get(f"{prefixo}CodFace") or item_atual.get("CodFace") or item_atual.get("CodFaceOriginal"),
            },
            cab_atual_para_fallback,
        )

        def vf(campo_form: str, parser, campo_banco: str):
            return _valor_form_ou_fallback(
                form,
                f"{prefixo}{campo_form}",
                parser,
                item_atual,
                fallback_item,
                campo_banco,
            )

        cod_ponto = vf("CodPonto", _texto_ou_none, "CodPonto")
        cod_face = vf("CodFace", _texto_ou_none, "CodFace")
        info_face = None
        if cod_ponto and cod_face:
            info_face = _resolver_face_e_painel_por_codigos(cod_ponto, cod_face)
            if not info_face:
                raise ValueError(f"Não encontrei CodPonto/CodFace válidos para o item {item_id}: {cod_ponto} / {cod_face}.")

        id_painel_resolvido = (
            (info_face.get("IDDimPaineisEuromidia") if info_face else None)
            or item_atual.get("IDPainelEuromidia")
            or fallback_item.get("IDPainelEuromidia")
            or fallback_item.get("IDDimPaineisEuromidia")
        )
        id_face_resolvida = (
            (info_face.get("IDDimFacesPaineis") if info_face else None)
            or item_atual.get("IDDimFacesPaineis")
            or fallback_item.get("IDDimFacesPaineis")
        )

        id_contrato_item_resolvido = (
            _int_ou_none(item_atual.get("IDFatoControleContratosEuromidia"))
            or _int_ou_none(fallback_item.get("IDFatoControleContratosEuromidia"))
        )
        id_item_controle_resolvido = (
            _int_ou_none(item_atual.get("IDFatoControleContratosItensEuromidia"))
            or _int_ou_none(fallback_item.get("IDFatoControleContratosItensEuromidia"))
        )

        params_item = {
            "IDFatoSolicitacaoContratoItemEuromidia": item_id, "IDFatoSolicitacaoContratoEuromidia": int(id_solicitacao),
            "IDFatoControleContratosEuromidia": id_contrato_item_resolvido,
            "IDFatoControleContratosItensEuromidia": id_item_controle_resolvido,
            "IDPainelEuromidia": id_painel_resolvido, "IDDimFacesPaineis": id_face_resolvida,
            "CodPonto": cod_ponto, "CodFace": cod_face, "CidadeExibicao": vf("CidadeExibicao", _texto_ou_none, "CidadeExibicao"), "Tipo": vf("Tipo", _texto_ou_none, "Tipo"),
            "Cota": vf("Cota", _decimal_ou_none, "Cota"), "DataInicioPrevisto": vf("DataInicioPrevisto", _data_ou_none, "DataInicioPrevisto"), "DataTerminoPrevisto": vf("DataTerminoPrevisto", _data_ou_none, "DataTerminoPrevisto"),
            "NumeroParcelas": vf("NumeroParcelas", _int_ou_none, "NumeroParcelas"), "DataInicioVencimento": vf("DataInicioVencimento", _data_ou_none, "DataInicioVencimento"),
            "FaturamentoBrutoMensal": vf("FaturamentoBrutoMensal", _decimal_ou_none, "FaturamentoBrutoMensal"), "PercentualPermuta": vf("PercentualPermuta", _decimal_ou_none, "PercentualPermuta"),
            "ValorPermuta": vf("ValorPermuta", _decimal_ou_none, "ValorPermuta"), "FaturamentoLiquidoPermuta": vf("FaturamentoLiquidoPermuta", _decimal_ou_none, "FaturamentoLiquidoPermuta"),
            "FaturamentoLiquidoMensal": vf("FaturamentoLiquidoMensal", _decimal_ou_none, "FaturamentoLiquidoMensal"), "FaturamentoLiquidoFinalMensal": vf("FaturamentoLiquidoFinalMensal", _decimal_ou_none, "FaturamentoLiquidoFinalMensal"),
            "PercentualComissaoVendedor": vf("PercentualComissaoVendedor", _decimal_ou_none, "PercentualComissaoVendedor"), "ValorVendedor": vf("ValorVendedor", _decimal_ou_none, "ValorVendedor"),
            "ValorVendedorTotal": vf("ValorVendedorTotal", _decimal_ou_none, "ValorVendedorTotal"), "Status": vf("Status", _texto_ou_none, "Status"), "OBS": vf("OBS", _texto_ou_none, "OBS"),
            "BitAtivo": 1 if form.get(f"{prefixo}BitAtivo") == "1" else 0, "IDDimUsuariosAtualizacao": id_usuario_logado,
        }
        db.session.execute(sql_update_item, params_item)


def _obter_status_contratos_empresa(id_empresa_proprietaria: int | None):
    mapa_padrao = {
        1: "Em Digitação",
        2: "Pendente Geração",
        3: "Documento Gerado",
        4: "Pendente Envio",
        5: "Enviado Assinatura",
        6: "Em Assinatura",
        7: "Ativo",
        8: "Concluido",
        9: "Cancelado",
        10: "ERRO",
    }

    try:
        id_empresa = int(id_empresa_proprietaria or 0)
    except Exception:
        id_empresa = 0

    if id_empresa <= 0:
        return [
            {
                "IDDimStatusContratos": id_status,
                "Status": nome,
            }
            for id_status, nome in mapa_padrao.items()
        ]

    sql = text("""
        SELECT
             [IDDimStatusContratos]
            ,[Status]
        FROM [Integracao].[Silver].[DimStatusContratos]
        WHERE [IDEmpresaProprietaria] = :id_empresa_proprietaria
          AND [IDDimStatusContratos] BETWEEN 1 AND 10
        ORDER BY [IDDimStatusContratos] ASC
    """)

    rows = db.session.execute(
        sql,
        {"id_empresa_proprietaria": id_empresa},
    ).mappings().all()

    if not rows:
        return [
            {
                "IDDimStatusContratos": id_status,
                "Status": nome,
            }
            for id_status, nome in mapa_padrao.items()
        ]

    retorno = []
    ids_existentes = set()
    for row in rows:
        id_status = int(row.get("IDDimStatusContratos") or 0)
        if id_status <= 0:
            continue
        ids_existentes.add(id_status)
        retorno.append(
            {
                "IDDimStatusContratos": id_status,
                "Status": (row.get("Status") or mapa_padrao.get(id_status) or f"Status {id_status}").strip(),
            }
        )

    for id_status, nome in mapa_padrao.items():
        if id_status not in ids_existentes:
            retorno.append(
                {
                    "IDDimStatusContratos": id_status,
                    "Status": nome,
                }
            )

    retorno.sort(key=lambda x: int(x.get("IDDimStatusContratos") or 0))
    return retorno



def _montar_diagrama_status_contrato(
    id_empresa_proprietaria: int | None,
    id_status_atual: int | None,
    nome_status_atual: str | None = None,
):
    status_rows = _obter_status_contratos_empresa(id_empresa_proprietaria)
    mapa_status = {
        int(row.get("IDDimStatusContratos") or 0): (row.get("Status") or "").strip()
        for row in status_rows
        if int(row.get("IDDimStatusContratos") or 0) > 0
    }

    try:
        id_status_corrente = int(id_status_atual or 0)
    except Exception:
        id_status_corrente = 0

    nome_status_corrente = (nome_status_atual or mapa_status.get(id_status_corrente) or "").strip()

    etapas_principais = []
    for id_status in range(1, 8):
        nome = mapa_status.get(id_status) or f"Status {id_status}"
        concluido = False
        atual = False

        if id_status_corrente in range(1, 8):
            concluido = id_status < id_status_corrente
            atual = id_status == id_status_corrente
        elif id_status_corrente == 8:
            concluido = True
        else:
            concluido = False
            atual = False

        etapas_principais.append(
            {
                "id": id_status,
                "nome": nome,
                "concluido": concluido,
                "atual": atual,
                "pendente": (not concluido) and (not atual),
                "mostra_d4sign": 2 <= id_status <= 6,
            }
        )

    terminal_atual = None
    if id_status_corrente in (8, 9, 10):
        terminal_atual = {
            "id": id_status_corrente,
            "nome": mapa_status.get(id_status_corrente) or nome_status_corrente or f"Status {id_status_corrente}",
            "classe": "sucesso" if id_status_corrente == 8 else "erro",
            "icone": "✓" if id_status_corrente == 8 else "!",
        }

    return {
        "status_atual_id": id_status_corrente,
        "status_atual_nome": nome_status_corrente or "Sem status definido",
        "etapas": etapas_principais,
        "terminal_atual": terminal_atual,
    }






def _valor_esta_vazio_para_fallback(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str) and valor.strip() == "":
        return True
    return False


def _buscar_item_controle_fallback_layout(item: dict, cab: dict | None = None) -> dict:
    """Busco o item atual do contrato para preencher o layout da tela de aprovação.

    Na alteração/aditivo, a solicitação pode trazer apenas CodPonto/CodFace e datas,
    deixando cota, parcelas e valores vazios. Se eu renderizar só a solicitação, a tela
    parece que perdeu as informações. Por isso eu uso o item atual do contrato como
    fallback visual e também como fallback de salvamento.
    """
    cab = cab or {}
    id_item_controle = _int_ou_none(item.get("IDFatoControleContratosItensEuromidia"))
    id_contrato = (
        _int_ou_none(item.get("IDFatoControleContratosEuromidia"))
        or _int_ou_none(item.get("IDFatoControleContratoEuromidia"))
        or _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
    )
    cod_ponto = _texto_ou_none(item.get("CodPonto") or item.get("CodPontoOriginal"))
    cod_face = _texto_ou_none(item.get("CodFace") or item.get("CodFaceOriginal"))

    if id_contrato in (None, "", 0) and id_item_controle in (None, "", 0):
        return {}

    id_item_controle_int = _int_ou_none(id_item_controle)
    ordem_prioridade_item = ""
    if id_item_controle_int not in (None, "", 0):
        ordem_prioridade_item = "CASE WHEN i.IDFatoControleContratosItensEuromidia = :id_item_controle THEN 0 ELSE 1 END,"

    row = db.session.execute(
        text(f"""
            SELECT TOP 1
                 i.IDFatoControleContratoEuromidia AS IDFatoControleContratosEuromidia
                ,i.IDFatoControleContratosItensEuromidia
                ,i.Referencia
                ,i.NumeroContrato
                ,i.NumeroPrevia
                ,i.CNPJ
                ,i.CodPonto
                ,i.CodFace
                ,i.DataLancamento
                ,i.Cota
                ,i.CidadeExibicao
                ,i.Tipo
                ,i.Origem
                ,i.EmpresaEuro
                ,i.CnpjExibibora
                ,i.TipoDocumento
                ,i.RazaoSocial
                ,i.CPF
                ,i.MarcaExibida
                ,i.Vendedor
                ,i.SDR
                ,i.Agencia
                ,i.CnpjAgencia
                ,i.Bureau
                ,i.CnpjBureau
                ,i.Intermediario
                ,i.CnpjIntermediario
                ,i.DataAssinaturaRenovacao
                ,i.IDTrimestre
                ,i.TexmpoExposicao
                ,i.DataInicioPrevisto
                ,i.DataTerminoPrevisto
                ,i.InicioRenovacao
                ,i.FaturamentoBrutoMensal
                ,i.PercentualPermuta
                ,i.CotaOportunidade
                ,i.ValorPermuta
                ,i.FaturamentoLiquidoPermuta
                ,i.NumeroParcelas
                ,i.DataInicioVencimento
                ,i.TotalBrutoContrato
                ,i.TotalLiquidoContratoAGBRCTACORDO
                ,i.TotalLiquidoContratoAGBRVENDGERCOOR
                ,i.PercentualAgencia
                ,i.ValorMensalAgencia
                ,i.PercentualBureau
                ,i.ValorBureauMensal
                ,i.PercentualCartaAcordo
                ,i.ValorCartaAcordoMensal
                ,i.ValorOutrasComissoes
                ,i.FaturamentoLiquidoMensal
                ,i.PercentualComissaoVendedor
                ,i.ValorVendedor
                ,i.ValorVendedorTotal
                ,i.PercentualComissaoCoordenacao
                ,i.ValorCoordenador
                ,i.ValorCoordenadorTotal
                ,i.PercentualComissaoGerencia
                ,i.ValorGerencia
                ,i.ValorGerenciaTotal
                ,i.AtivoCancelamento
                ,i.FaturamentoLiquidoFinalMensal
                ,i.ComissaoGerenciaNordeste
                ,i.Faturamento
                ,i.DataCancelamento
                ,i.OBS
                ,i.IDVendedor
                ,i.IDPainelEuromidia
                ,i.IDDimFacesPaineis
                ,i.DataFimEfetiva
                ,i.Status
                ,i.IDDimCheckinHistorico
                ,i.IDFatoKanbanCard
                ,i.BitAtivo
                ,i.IDEmpresaAgencia
                ,i.IDDimTipoDocumento
                ,i.BitPreferencia
                ,i.BitFracionado
                ,df.Face AS FaceDescricaoCadastro
                ,df.CodFace AS CodFaceCadastro
                ,df.Tipo AS TipoFaceCadastro
                ,dp.Cidade AS CidadePainelCadastro
                ,dp.UF AS UFPainelCadastro
                ,dp.Tipo AS TipoPainelCadastro
                ,dp.Logradouro AS LogradouroPainelCadastro
                ,dp.Bairro AS BairroPainelCadastro
                ,dp.Referencia AS ReferenciaPainelCadastro
                ,COALESCE(i.IDPainelEuromidia, df.IDDimPaineisEuromidia) AS IDDimPaineisEuromidia
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
            LEFT JOIN [Integracao].[Silver].[DimFacesPaineis] df
                   ON df.IDDimFacesPaineis = i.IDDimFacesPaineis
            LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] dp
                   ON dp.IDDimPaineisEuromidia = COALESCE(i.IDPainelEuromidia, df.IDDimPaineisEuromidia)
            WHERE
                (
                    :id_item_controle IS NOT NULL
                    AND i.IDFatoControleContratosItensEuromidia = :id_item_controle
                )
                OR
                (
                    :id_contrato IS NOT NULL
                    AND i.IDFatoControleContratoEuromidia = :id_contrato
                    AND ISNULL(LTRIM(RTRIM(CAST(i.CodPonto AS varchar(60)))), '') = ISNULL(LTRIM(RTRIM(CAST(:cod_ponto AS varchar(60)))), '')
                    AND ISNULL(UPPER(LTRIM(RTRIM(CAST(i.CodFace AS varchar(60))))), '') = ISNULL(UPPER(LTRIM(RTRIM(CAST(:cod_face AS varchar(60))))), '')
                )
            ORDER BY
                {ordem_prioridade_item}
                i.IDFatoControleContratosItensEuromidia DESC
        """),
        {
            "id_item_controle": int(id_item_controle) if id_item_controle not in (None, "", 0) else None,
            "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
            "cod_ponto": cod_ponto,
            "cod_face": cod_face,
        },
    ).mappings().first()

    if row:
        return dict(row)

    return _buscar_fallback_item_solicitacao_por_card_e_contrato(item, cab)


def _buscar_fallback_item_solicitacao_por_card_e_contrato(item: dict, cab: dict | None = None) -> dict:
    """Resolve CodPonto/CodFace/IDs do item quando a solicitação veio incompleta.

    Situação que esta função corrige:
    - renovação/aditivo criado a partir do Kanban com tag 17;
    - item da solicitação sem CodPonto/CodFace;
    - item da solicitação sem IDDimFacesPaineis/IDPainelEuromidia;
    - tela de aprovação mostrando CodPonto —, CodFace — e SEM STATUS;
    - aprovação falhando porque o backend não consegue localizar a face do contrato.

    Ordem de fallback:
    1. item oficial do contrato, quando há ID do item;
    2. item oficial do contrato por contrato + CodPonto/CodFace;
    3. item oficial do contrato vinculado ao card;
    4. FatoKanbanCardPainelFace do card;
    5. CodPontoContrato/CodFaceContrato do próprio card;
    6. DimFacesPaineis/DimPaineisEuromidia.
    """
    item = item or {}
    cab = cab or {}

    id_item_solicitacao = _int_ou_none(item.get("IDFatoSolicitacaoContratoItemEuromidia"))
    id_item_controle = _int_ou_none(item.get("IDFatoControleContratosItensEuromidia"))
    id_contrato = (
        _int_ou_none(item.get("IDFatoControleContratosEuromidia"))
        or _int_ou_none(item.get("IDFatoControleContratoEuromidia"))
        or _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
        or _int_ou_none(cab.get("IDFatoControleContratoEuromidia"))
    )
    id_card = _int_ou_none(item.get("IDFatoKanbanCard")) or _int_ou_none(cab.get("IDFatoKanbanCard"))
    id_painel = _int_ou_none(item.get("IDPainelEuromidia")) or _int_ou_none(item.get("IDDimPaineisEuromidia"))
    id_face = _int_ou_none(item.get("IDDimFacesPaineis"))
    cod_ponto = _texto_ou_none(item.get("CodPonto") or item.get("CodPontoOriginal"))
    cod_face = _texto_ou_none(item.get("CodFace") or item.get("CodFaceOriginal"))

    if all(valor in (None, "", 0) for valor in (id_item_controle, id_contrato, id_card, id_painel, id_face, cod_ponto, cod_face)):
        return {}

    try:
        row = db.session.execute(
            text("""
                ;WITH base AS (
                    SELECT
                        :id_item_solicitacao AS IDFatoSolicitacaoContratoItemEuromidia,
                        :id_item_controle AS IDItemControleParametro,
                        :id_contrato AS IDContratoParametro,
                        :id_card AS IDCardParametro,
                        :id_painel AS IDPainelParametro,
                        :id_face AS IDFaceParametro,
                        NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), :cod_ponto))), '') AS CodPontoParametro,
                        NULLIF(UPPER(LTRIM(RTRIM(CONVERT(varchar(80), :cod_face)))), '') AS CodFaceParametro
                )
                SELECT TOP (1)
                     COALESCE(i.IDFatoControleContratoEuromidia, base.IDContratoParametro) AS IDFatoControleContratosEuromidia
                    ,i.IDFatoControleContratosItensEuromidia
                    ,i.Referencia AS Referencia
                    ,COALESCE(NULLIF(i.NumeroContrato, ''), NULLIF(ctr.NumeroContrato, '')) AS NumeroContrato
                    ,COALESCE(NULLIF(i.NumeroPrevia, ''), NULLIF(ctr.NumeroPrevia, '')) AS NumeroPrevia
                    ,COALESCE(NULLIF(i.CNPJ, ''), NULLIF(ctr.CNPJ, ''), NULLIF(emp.CNPJ, '')) AS CNPJ
                    ,COALESCE(
                        NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), i.CodPonto))), ''),
                        NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), pf.CodPonto))), ''),
                        CAST(NULL AS varchar(80)),
                        NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), df.CodPonto))), ''),
                        NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), dp.CodPonto))), ''),
                        base.CodPontoParametro
                     ) AS CodPonto
                    ,COALESCE(
                        NULLIF(UPPER(LTRIM(RTRIM(CONVERT(varchar(80), i.CodFace)))), ''),
                        NULLIF(UPPER(LTRIM(RTRIM(CONVERT(varchar(80), pf.CodFace)))), ''),
                        CAST(NULL AS varchar(80)),
                        NULLIF(UPPER(LTRIM(RTRIM(CONVERT(varchar(80), df.CodFace)))), ''),
                        base.CodFaceParametro
                     ) AS CodFace
                    ,COALESCE(i.DataLancamento, ctr.DataLancamento) AS DataLancamento
                    ,COALESCE(i.Cota, TRY_CONVERT(decimal(18,2), pf.ExibicoesDia)) AS Cota
                    ,COALESCE(NULLIF(i.CidadeExibicao, ''), NULLIF(dp.Cidade, '')) AS CidadeExibicao
                    ,COALESCE(NULLIF(i.Tipo, ''), NULLIF(pf.TipoPainel, ''), NULLIF(df.Tipo, ''), NULLIF(dp.Tipo, '')) AS Tipo
                    ,COALESCE(NULLIF(i.Origem, ''), NULLIF(ctr.Origem, '')) AS Origem
                    ,i.EmpresaEuro
                    ,i.CnpjExibibora
                    ,COALESCE(NULLIF(i.TipoDocumento, ''), NULLIF(ctr.TipoDocumento, '')) AS TipoDocumento
                    ,COALESCE(NULLIF(i.RazaoSocial, ''), NULLIF(ctr.RazaoSocial, ''), NULLIF(emp.RazaoSocial, '')) AS RazaoSocial
                    ,COALESCE(NULLIF(i.CPF, ''), NULLIF(ctr.CPF, '')) AS CPF
                    ,COALESCE(NULLIF(i.MarcaExibida, ''), NULLIF(ctr.MarcaExibida, '')) AS MarcaExibida
                    ,COALESCE(NULLIF(i.Vendedor, ''), NULLIF(ctr.Vendedor, '')) AS Vendedor
                    ,COALESCE(NULLIF(i.SDR, ''), NULLIF(ctr.SDR, '')) AS SDR
                    ,COALESCE(NULLIF(i.Agencia, ''), NULLIF(ctr.Agencia, '')) AS Agencia
                    ,COALESCE(NULLIF(i.CnpjAgencia, ''), NULLIF(ctr.CnpjAgencia, '')) AS CnpjAgencia
                    ,COALESCE(NULLIF(i.Bureau, ''), NULLIF(ctr.Bureau, '')) AS Bureau
                    ,COALESCE(NULLIF(i.CnpjBureau, ''), NULLIF(ctr.CnpjBureau, '')) AS CnpjBureau
                    ,COALESCE(NULLIF(i.Intermediario, ''), NULLIF(ctr.Intermediario, '')) AS Intermediario
                    ,COALESCE(NULLIF(i.CnpjIntermediario, ''), NULLIF(ctr.CnpjIntermediario, '')) AS CnpjIntermediario
                    ,COALESCE(i.DataAssinaturaRenovacao, ctr.DataAssinaturaRenovacao) AS DataAssinaturaRenovacao
                    ,COALESCE(i.IDTrimestre, ctr.IDTrimestre) AS IDTrimestre
                    ,i.TexmpoExposicao
                    ,COALESCE(i.DataInicioPrevisto, TRY_CONVERT(date, pf.DataInicio)) AS DataInicioPrevisto
                    ,COALESCE(i.DataTerminoPrevisto, TRY_CONVERT(date, pf.DataFim)) AS DataTerminoPrevisto
                    ,i.InicioRenovacao
                    ,i.FaturamentoBrutoMensal
                    ,i.PercentualPermuta
                    ,i.CotaOportunidade
                    ,i.ValorPermuta
                    ,i.FaturamentoLiquidoPermuta
                    ,i.NumeroParcelas
                    ,i.DataInicioVencimento
                    ,i.TotalBrutoContrato
                    ,i.TotalLiquidoContratoAGBRCTACORDO
                    ,i.TotalLiquidoContratoAGBRVENDGERCOOR
                    ,i.PercentualAgencia
                    ,i.ValorMensalAgencia
                    ,i.PercentualBureau
                    ,i.ValorBureauMensal
                    ,i.PercentualCartaAcordo
                    ,i.ValorCartaAcordoMensal
                    ,i.ValorOutrasComissoes
                    ,i.FaturamentoLiquidoMensal
                    ,i.PercentualComissaoVendedor
                    ,i.ValorVendedor
                    ,i.ValorVendedorTotal
                    ,i.PercentualComissaoCoordenacao
                    ,i.ValorCoordenador
                    ,i.ValorCoordenadorTotal
                    ,i.PercentualComissaoGerencia
                    ,i.ValorGerencia
                    ,i.ValorGerenciaTotal
                    ,i.AtivoCancelamento
                    ,i.FaturamentoLiquidoFinalMensal
                    ,i.ComissaoGerenciaNordeste
                    ,i.Faturamento
                    ,i.DataCancelamento
                    ,i.OBS AS OBS
                    ,i.IDVendedor
                    ,COALESCE(i.IDPainelEuromidia, pf.IDDimPaineisEuromidia, df.IDDimPaineisEuromidia, dp.IDDimPaineisEuromidia, base.IDPainelParametro) AS IDPainelEuromidia
                    ,COALESCE(i.IDDimFacesPaineis, pf.IDDimFacesPaineis, df.IDDimFacesPaineis, base.IDFaceParametro) AS IDDimFacesPaineis
                    ,COALESCE(i.DataFimEfetiva, TRY_CONVERT(date, pf.DataFim)) AS DataFimEfetiva
                    ,COALESCE(NULLIF(i.Status, ''), 'ATIVO') AS Status
                    ,i.IDDimCheckinHistorico
                    ,COALESCE(i.IDFatoKanbanCard, base.IDCardParametro) AS IDFatoKanbanCard
                    ,COALESCE(i.BitAtivo, CAST(1 AS bit)) AS BitAtivo
                    ,i.IDEmpresaAgencia
                    ,i.IDDimTipoDocumento
                    ,i.BitPreferencia
                    ,i.BitFracionado
                    ,df.Face AS FaceDescricaoCadastro
                    ,df.CodFace AS CodFaceCadastro
                    ,df.Tipo AS TipoFaceCadastro
                    ,dp.Cidade AS CidadePainelCadastro
                    ,dp.UF AS UFPainelCadastro
                    ,dp.Tipo AS TipoPainelCadastro
                    ,dp.Logradouro AS LogradouroPainelCadastro
                    ,dp.Bairro AS BairroPainelCadastro
                    ,dp.Referencia AS ReferenciaPainelCadastro
                    ,COALESCE(i.IDPainelEuromidia, pf.IDDimPaineisEuromidia, df.IDDimPaineisEuromidia, dp.IDDimPaineisEuromidia, base.IDPainelParametro) AS IDDimPaineisEuromidia
                FROM base
                LEFT JOIN [Kanban].[Silver].[FatoKanbanCard] card
                       ON card.IDFatoKanbanCard = base.IDCardParametro
                LEFT JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] ctr
                       ON ctr.IDFatoControleContratosEuromidia = base.IDContratoParametro
                LEFT JOIN [Integracao].[Silver].[DimEmpresas] emp
                       ON emp.IDEmpresa = ctr.IDEmpresa
                OUTER APPLY (
                    SELECT TOP (1)
                        pf.*
                    FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
                    WHERE base.IDCardParametro IS NOT NULL
                      AND pf.IDFatoKanbanCard = base.IDCardParametro
                      AND (
                            base.IDFaceParametro IS NULL
                            OR pf.IDDimFacesPaineis = base.IDFaceParametro
                          )
                    ORDER BY
                        ISNULL(pf.Ordem, 999999) ASC,
                        pf.IDFatoKanbanCardPainelFace DESC
                ) pf
                OUTER APPLY (
                    SELECT TOP (1)
                        i.*
                    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
                    WHERE
                        (
                            base.IDItemControleParametro IS NOT NULL
                            AND i.IDFatoControleContratosItensEuromidia = base.IDItemControleParametro
                        )
                        OR
                        (
                            base.IDContratoParametro IS NOT NULL
                            AND i.IDFatoControleContratoEuromidia = base.IDContratoParametro
                            AND COALESCE(base.CodPontoParametro, NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), pf.CodPonto))), '')) IS NOT NULL
                            AND LTRIM(RTRIM(CONVERT(varchar(80), i.CodPonto))) = COALESCE(base.CodPontoParametro, NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), pf.CodPonto))), ''))
                            AND (
                                COALESCE(base.CodFaceParametro, NULLIF(UPPER(LTRIM(RTRIM(CONVERT(varchar(80), pf.CodFace)))), '')) IS NULL
                                OR UPPER(LTRIM(RTRIM(CONVERT(varchar(80), i.CodFace)))) = COALESCE(base.CodFaceParametro, NULLIF(UPPER(LTRIM(RTRIM(CONVERT(varchar(80), pf.CodFace)))), ''))
                            )
                        )
                        OR
                        (
                            base.IDCardParametro IS NOT NULL
                            AND i.IDFatoKanbanCard = base.IDCardParametro
                        )
                    ORDER BY
                        ISNULL(i.BitAtivo, 1) DESC,
                        i.IDFatoControleContratosItensEuromidia DESC
                ) i
                LEFT JOIN [Integracao].[Silver].[DimFacesPaineis] df
                       ON df.IDDimFacesPaineis = COALESCE(base.IDFaceParametro, i.IDDimFacesPaineis, pf.IDDimFacesPaineis)
                LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] dp
                       ON dp.IDDimPaineisEuromidia = COALESCE(base.IDPainelParametro, i.IDPainelEuromidia, pf.IDDimPaineisEuromidia, df.IDDimPaineisEuromidia)
            """),
            {
                "id_item_solicitacao": int(id_item_solicitacao) if id_item_solicitacao not in (None, "", 0) else None,
                "id_item_controle": int(id_item_controle) if id_item_controle not in (None, "", 0) else None,
                "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
                "id_card": int(id_card) if id_card not in (None, "", 0) else None,
                "id_painel": int(id_painel) if id_painel not in (None, "", 0) else None,
                "id_face": int(id_face) if id_face not in (None, "", 0) else None,
                "cod_ponto": cod_ponto,
                "cod_face": cod_face,
            },
        ).mappings().first()

        return dict(row) if row else {}
    except Exception:
        current_app.logger.exception(
            "APROVACAO_CONTRATO | falha ao resolver fallback de item por card/contrato | id_solicitacao=%s | id_card=%s | id_contrato=%s",
            id_item_solicitacao,
            id_card,
            id_contrato,
        )
        return {}


def _aplicar_fallback_layout_item_solicitacao(item: dict, cab: dict | None = None) -> dict:
    fallback = _buscar_item_controle_fallback_layout(item, cab)
    if not fallback:
        return item

    campos = [
        "IDFatoControleContratosEuromidia", "IDFatoControleContratosItensEuromidia",
        "Referencia", "NumeroContrato", "NumeroPrevia", "CNPJ", "CodPonto", "CodFace",
        "DataLancamento", "Cota", "CidadeExibicao", "Tipo", "Origem", "EmpresaEuro",
        "CnpjExibibora", "TipoDocumento", "RazaoSocial", "CPF", "MarcaExibida", "Vendedor",
        "SDR", "Agencia", "CnpjAgencia", "Bureau", "CnpjBureau", "Intermediario",
        "CnpjIntermediario", "DataAssinaturaRenovacao", "IDTrimestre", "TexmpoExposicao",
        "DataInicioPrevisto", "DataTerminoPrevisto", "InicioRenovacao", "FaturamentoBrutoMensal",
        "PercentualPermuta", "CotaOportunidade", "ValorPermuta", "FaturamentoLiquidoPermuta",
        "NumeroParcelas", "DataInicioVencimento", "TotalBrutoContrato",
        "TotalLiquidoContratoAGBRCTACORDO", "TotalLiquidoContratoAGBRVENDGERCOOR",
        "PercentualAgencia", "ValorMensalAgencia", "PercentualBureau", "ValorBureauMensal",
        "PercentualCartaAcordo", "ValorCartaAcordoMensal", "ValorOutrasComissoes",
        "FaturamentoLiquidoMensal", "PercentualComissaoVendedor", "ValorVendedor",
        "ValorVendedorTotal", "PercentualComissaoCoordenacao", "ValorCoordenador",
        "ValorCoordenadorTotal", "PercentualComissaoGerencia", "ValorGerencia",
        "ValorGerenciaTotal", "AtivoCancelamento", "FaturamentoLiquidoFinalMensal",
        "ComissaoGerenciaNordeste", "Faturamento", "DataCancelamento", "OBS", "IDVendedor",
        "IDPainelEuromidia", "IDDimFacesPaineis", "DataFimEfetiva", "Status",
        "IDDimCheckinHistorico", "IDFatoKanbanCard", "BitAtivo", "IDEmpresaAgencia",
        "IDDimTipoDocumento", "BitPreferencia", "BitFracionado",
        "FaceDescricaoCadastro", "CodFaceCadastro", "TipoFaceCadastro", "CidadePainelCadastro",
        "UFPainelCadastro", "TipoPainelCadastro", "LogradouroPainelCadastro",
        "BairroPainelCadastro", "ReferenciaPainelCadastro", "IDDimPaineisEuromidia",
    ]

    for campo in campos:
        if _valor_esta_vazio_para_fallback(item.get(campo)) and not _valor_esta_vazio_para_fallback(fallback.get(campo)):
            item[campo] = fallback.get(campo)

    return item


def _valor_form_ou_fallback(form, nome_campo: str, parser, item_atual: dict, fallback: dict, campo_banco: str):
    valor_bruto = form.get(nome_campo)
    if valor_bruto is not None and str(valor_bruto).strip() != "":
        return parser(valor_bruto)

    if not _valor_esta_vazio_para_fallback(item_atual.get(campo_banco)):
        return item_atual.get(campo_banco)

    return fallback.get(campo_banco)


def _form_getlist_admin(form, nome_campo: str) -> list:
    """Lê campos múltiplos tanto do request.form quanto do dicionário vindo do Celery."""
    if form is None:
        return []

    try:
        if hasattr(form, "getlist"):
            return list(form.getlist(nome_campo) or [])
    except Exception:
        pass

    try:
        valor = form.get(nome_campo)
    except Exception:
        valor = None

    if valor is None:
        return []

    if isinstance(valor, (list, tuple, set)):
        return list(valor)

    return [valor]


def _form_get_first_admin(form, nome_campo: str, padrao: str = ""):
    valores = _form_getlist_admin(form, nome_campo)
    if not valores:
        return padrao
    return valores[0]


def _bit_form_admin(valor) -> int:
    texto = str(valor or "").strip().lower()
    return 1 if texto in ("1", "true", "t", "sim", "s", "yes", "y", "on") else 0


def _buscar_dim_email_contrato_admin(
    *,
    id_fato_controle_contratos: int | None = None,
    id_fato_kanban_card: int | None = None,
) -> list[dict]:
    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_card = _int_ou_none(id_fato_kanban_card)

    if id_contrato in (None, "", 0) and id_card in (None, "", 0):
        return []

    rows = db.session.execute(
        text(f"""
            SELECT
                 IDDimEmailContratoEmailContrato
                ,IDFatoControleContratosEuromidia
                ,IDFatoKanbanCard
                ,EmailContrato
                ,TelefoneContrato
                ,CpfContrato
                ,BitResponsavelContrato
            FROM {TABELA_EMAIL_CONTRATO_ADMIN}
            WHERE
                (
                    :id_contrato IS NOT NULL
                    AND IDFatoControleContratosEuromidia = :id_contrato
                )
                OR
                (
                    :id_card IS NOT NULL
                    AND IDFatoKanbanCard = :id_card
                )
            ORDER BY
                CASE WHEN ISNULL(BitResponsavelContrato, 0) = 1 THEN 0 ELSE 1 END,
                IDDimEmailContratoEmailContrato ASC
        """),
        {
            "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
            "id_card": int(id_card) if id_card not in (None, "", 0) else None,
        },
    ).mappings().all()

    return [dict(row) for row in rows]


def _extrair_contatos_contrato_formulario_admin(form) -> tuple[bool, list[dict]]:
    """Extrai as linhas dinâmicas do bloco Adicionar Contatos Contrato."""
    indices = [str(x or "").strip() for x in _form_getlist_admin(form, "contato_contrato_idx")]
    indices = [idx for idx in indices if idx != ""]

    if not indices:
        return False, []

    contatos = []
    for idx in indices:
        email = _texto_ou_vazio(_form_get_first_admin(form, f"contato_contrato_{idx}__EmailContrato"))[:200]
        telefone = _texto_ou_vazio(_form_get_first_admin(form, f"contato_contrato_{idx}__TelefoneContrato"))[:20]
        cpf = _texto_ou_vazio(_form_get_first_admin(form, f"contato_contrato_{idx}__CpfContrato"))[:20]
        principal = _bit_form_admin(_form_get_first_admin(form, f"contato_contrato_{idx}__ContatoPrincipal"))

        if not email and not telefone and not cpf:
            continue

        contatos.append(
            {
                "EmailContrato": email or None,
                "TelefoneContrato": telefone or None,
                "CpfContrato": cpf or None,
                "BitResponsavelContrato": principal,
            }
        )

    return True, contatos


def _sincronizar_dim_email_contrato_por_formulario_admin(
    *,
    id_fato_controle_contratos: int | None = None,
    id_fato_kanban_card: int | None = None,
    form=None,
) -> dict:
    """
    Sincroniza o bloco Adicionar Contatos Contrato com Silver.DimEmailContrato.

    Regra aplicada:
    - salvar antes da aprovação grava pelo IDFatoKanbanCard;
    - aprovar depois da criação do contrato grava/atualiza com IDFatoKanbanCard e IDFatoControleContratosEuromidia;
    - quando não houver form, apenas completa o ID do contrato em registros já salvos pelo card.
    """
    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_card = _int_ou_none(id_fato_kanban_card)

    if id_contrato in (None, "", 0) and id_card in (None, "", 0):
        return {
            "ok": False,
            "status": "sem_referencia",
            "mensagem": "Não sincronizei contatos porque não veio IDFatoKanbanCard nem IDFatoControleContratosEuromidia.",
        }

    if form is None:
        if id_contrato not in (None, "", 0) and id_card not in (None, "", 0):
            db.session.execute(
                text(f"""
                    UPDATE {TABELA_EMAIL_CONTRATO_ADMIN}
                       SET IDFatoControleContratosEuromidia = :id_contrato,
                           IDFatoKanbanCard = COALESCE(IDFatoKanbanCard, :id_card)
                     WHERE IDFatoKanbanCard = :id_card
                       AND (
                            IDFatoControleContratosEuromidia IS NULL
                            OR IDFatoControleContratosEuromidia = :id_contrato
                       )
                """),
                {
                    "id_contrato": int(id_contrato),
                    "id_card": int(id_card),
                },
            )

        return {
            "ok": True,
            "status": "referencia_atualizada_sem_formulario",
            "id_contrato": int(id_contrato) if id_contrato else None,
            "id_card": int(id_card) if id_card else None,
        }

    campos_presentes, contatos = _extrair_contatos_contrato_formulario_admin(form)

    if not campos_presentes:
        return {
            "ok": True,
            "status": "sem_campos_no_formulario",
            "id_contrato": int(id_contrato) if id_contrato else None,
            "id_card": int(id_card) if id_card else None,
        }

    db.session.execute(
        text(f"""
            DELETE FROM {TABELA_EMAIL_CONTRATO_ADMIN}
            WHERE
                (
                    :id_contrato IS NOT NULL
                    AND IDFatoControleContratosEuromidia = :id_contrato
                )
                OR
                (
                    :id_card IS NOT NULL
                    AND IDFatoKanbanCard = :id_card
                )
        """),
        {
            "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
            "id_card": int(id_card) if id_card not in (None, "", 0) else None,
        },
    )

    for contato in contatos:
        db.session.execute(
            text(f"""
                INSERT INTO {TABELA_EMAIL_CONTRATO_ADMIN} (
                     IDFatoControleContratosEuromidia
                    ,IDFatoKanbanCard
                    ,EmailContrato
                    ,TelefoneContrato
                    ,CpfContrato
                    ,BitResponsavelContrato
                )
                VALUES (
                     :id_contrato
                    ,:id_card
                    ,:email
                    ,:telefone
                    ,:cpf
                    ,:principal
                )
            """),
            {
                "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
                "id_card": int(id_card) if id_card not in (None, "", 0) else None,
                "email": contato.get("EmailContrato"),
                "telefone": contato.get("TelefoneContrato"),
                "cpf": contato.get("CpfContrato"),
                "principal": int(contato.get("BitResponsavelContrato") or 0),
            },
        )

    return {
        "ok": True,
        "status": "sincronizado",
        "id_contrato": int(id_contrato) if id_contrato else None,
        "id_card": int(id_card) if id_card else None,
        "quantidade": len(contatos),
    }


# ==========================================================
# ANEXOS DO CONTRATO - UPLOAD ASSÍNCRONO COM CELERY
# ==========================================================

ANEXOS_CONTRATOS_PASTA_PADRAO_ADMIN = "/home/guilherme_correa/PythonJobs/pipelines/FlaskApp/Contratos/Euromidia/Anexos/Contrato"
EXTENSOES_PERMITIDAS_ANEXOS_CONTRATOS_ADMIN = {
    "xlsm", "csv", "xlsx", "pdf",
    "jpg", "jpeg", "png", "img", "gif", "bmp", "webp", "tif", "tiff", "heic", "heif", "svg",
}


def _anexos_contrato_extensoes_permitidas_admin() -> set[str]:
    configuradas = None
    try:
        configuradas = current_app.config.get("EXTENSOES_PERMITIDAS_ANEXOS_CONTRATOS")
    except Exception:
        configuradas = None

    configuradas = _texto_ou_vazio(configuradas or os.getenv("EXTENSOES_PERMITIDAS_ANEXOS_CONTRATOS"))
    if configuradas:
        valores = {
            str(ext).strip().lower().lstrip(".")
            for ext in configuradas.replace(";", ",").split(",")
            if str(ext).strip()
        }
        if valores:
            return valores

    return set(EXTENSOES_PERMITIDAS_ANEXOS_CONTRATOS_ADMIN)


def _anexos_contrato_pasta_base_admin() -> Path:
    valor = None
    try:
        valor = current_app.config.get("PASTA_ANEXOS_CONTRATOS_EUROMIDIA")
    except Exception:
        valor = None

    valor = _texto_ou_vazio(valor or os.getenv("PASTA_ANEXOS_CONTRATOS_EUROMIDIA") or ANEXOS_CONTRATOS_PASTA_PADRAO_ADMIN)
    return Path(valor).expanduser()


def _anexos_contrato_pasta_temp_admin() -> Path:
    valor = None
    try:
        valor = current_app.config.get("PASTA_TEMP_ANEXOS_CONTRATOS_EUROMIDIA")
    except Exception:
        valor = None

    if valor:
        return Path(str(valor)).expanduser()

    return _anexos_contrato_pasta_base_admin().parent / "_temp"


def _anexos_contrato_url_relativa_admin(nome_arquivo: str) -> str:
    return f"Contrato/{Path(str(nome_arquivo or '')).name}"


def _anexos_contrato_resolver_caminho_admin(url_anexo: str | None) -> Path | None:
    valor = _texto_ou_vazio(url_anexo).replace("\\", "/").lstrip("/")
    if not valor:
        return None

    pasta_base = _anexos_contrato_pasta_base_admin().resolve()
    pasta_raiz_anexos = pasta_base.parent.resolve()

    if valor.startswith("/home/") or valor.startswith("/mnt/") or valor.startswith("/app/"):
        caminho = Path(valor).expanduser().resolve()
    elif valor.lower().startswith("contratos/euromidia/anexos/"):
        rel = valor.split("Anexos/", 1)[1] if "Anexos/" in valor else Path(valor).name
        caminho = (pasta_raiz_anexos / rel).resolve()
    elif valor.lower().startswith("contrato/"):
        caminho = (pasta_raiz_anexos / valor).resolve()
    else:
        caminho = (pasta_base / Path(valor).name).resolve()

    if caminho != pasta_base and pasta_base not in caminho.parents:
        if caminho != pasta_raiz_anexos and pasta_raiz_anexos not in caminho.parents:
            raise RuntimeError("Caminho de anexo fora da pasta permitida.")

    return caminho


def _anexos_contrato_extensao_admin(nome_arquivo: str | None) -> str:
    extensao = Path(_texto_ou_vazio(nome_arquivo)).suffix.lower().lstrip(".")
    return extensao


def _anexos_contrato_validar_extensao_admin(nome_arquivo: str | None) -> str:
    extensao = _anexos_contrato_extensao_admin(nome_arquivo)
    if not extensao:
        raise ValueError("Arquivo sem extensão.")

    permitidas = _anexos_contrato_extensoes_permitidas_admin()
    if extensao not in permitidas:
        permitidas_txt = ", ".join(sorted(permitidas))
        raise ValueError(f"Extensão .{extensao} não permitida. Permitidas: {permitidas_txt}.")

    return extensao


def _anexos_contrato_limpar_nome_base_admin(nome_arquivo: str | None) -> str:
    nome_seguro = secure_filename(_texto_ou_vazio(nome_arquivo))
    base = Path(nome_seguro).stem if nome_seguro else "arquivo"
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")
    return (base or "arquivo")[:180]


def _anexos_contrato_montar_prefixo_admin(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_kanban_card: int | None,
    id_solicitacao: int | None = None,
) -> str:
    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_card = _int_ou_none(id_fato_kanban_card)
    id_solic = _int_ou_none(id_solicitacao)

    if id_contrato not in (None, "", 0):
        return str(int(id_contrato))
    if id_card not in (None, "", 0):
        return f"CARD{int(id_card)}"
    if id_solic not in (None, "", 0):
        return f"SOLICITACAO{int(id_solic)}"
    return "SEM_REFERENCIA"


def _anexos_contrato_nome_unico_admin(pasta: Path, nome_arquivo: str) -> str:
    pasta.mkdir(parents=True, exist_ok=True)
    candidato = Path(nome_arquivo).name
    destino = pasta / candidato
    if not destino.exists():
        return candidato

    stem = Path(candidato).stem
    suffix = Path(candidato).suffix
    for indice in range(2, 10000):
        novo_nome = f"{stem}_{indice}{suffix}"
        if not (pasta / novo_nome).exists():
            return novo_nome

    return f"{stem}_{uuid.uuid4().hex[:10]}{suffix}"


def _anexos_contrato_montar_nome_final_admin(
    *,
    nome_original: str,
    id_fato_controle_contratos: int | None,
    id_fato_kanban_card: int | None,
    id_solicitacao: int | None = None,
    data_referencia: datetime | None = None,
) -> str:
    extensao = _anexos_contrato_validar_extensao_admin(nome_original)
    base_limpa = _anexos_contrato_limpar_nome_base_admin(nome_original)
    prefixo = _anexos_contrato_montar_prefixo_admin(
        id_fato_controle_contratos=id_fato_controle_contratos,
        id_fato_kanban_card=id_fato_kanban_card,
        id_solicitacao=id_solicitacao,
    )
    data_ref = data_referencia or datetime.now()
    carimbo = data_ref.strftime("%Y%m%d%H%M%S")
    nome = f"{prefixo}_{base_limpa}_{carimbo}.{extensao}"
    return _anexos_contrato_nome_unico_admin(_anexos_contrato_pasta_base_admin(), nome)


def _anexos_contrato_flags_tipo_admin(
    *,
    tipo_solicitacao: str | None,
    id_fato_kanban_card: int | None = None,
) -> dict:
    tipo_normalizado = _texto_ou_vazio(tipo_solicitacao).upper().replace("_", " ")
    id_card = _int_ou_none(id_fato_kanban_card)

    eh_renovacao = "RENOVA" in tipo_normalizado
    if not eh_renovacao and id_card not in (None, "", 0):
        try:
            eh_renovacao = bool(_card_eh_renovacao_admin(int(id_card)))
        except Exception:
            eh_renovacao = False

    eh_novo = (not eh_renovacao) and tipo_normalizado == "NOVO CONTRATO"
    eh_aditivo = (not eh_renovacao) and ("ADITIV" in tipo_normalizado or not eh_novo)

    return {
        "BitNovoContrato": 1 if eh_novo else 0,
        "BitRenovacao": 1 if eh_renovacao else 0,
        "BitAditivo": 1 if eh_aditivo else 0,
    }


def _anexos_contrato_mes_ano_admin(data_referencia: datetime | None = None) -> str:
    data_ref = data_referencia or datetime.now()
    return data_ref.strftime("%m-%Y")


def _anexos_contrato_proximo_numero_admin(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_kanban_card: int | None,
) -> int:
    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_card = _int_ou_none(id_fato_kanban_card)

    row = db.session.execute(
        text(f"""
            SELECT ISNULL(MAX(NumeroAnexo), 0) AS MaiorNumero
            FROM {TABELA_ANEXOS_CONTRATOS_ADMIN}
            WHERE
                (
                    :id_contrato IS NOT NULL
                    AND IDFatoControleContratosEuromidia = :id_contrato
                )
                OR
                (
                    :id_card IS NOT NULL
                    AND IDFatoKanbanCard = :id_card
                )
        """),
        {
            "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
            "id_card": int(id_card) if id_card not in (None, "", 0) else None,
        },
    ).mappings().first()

    return int((row or {}).get("MaiorNumero") or 0) + 1


def _buscar_anexos_contrato_admin(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_kanban_card: int | None,
) -> list[dict]:
    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_card = _int_ou_none(id_fato_kanban_card)

    if id_contrato in (None, "", 0) and id_card in (None, "", 0):
        return []

    rows = db.session.execute(
        text(f"""
            SELECT
                 IDFatoAnexosContratosEuromidia
                ,IDFatoControleContratosEuromidia
                ,IDFatoContratoD4
                ,IDFatoKanbanCard
                ,NomeArquivo
                ,UrlAnexo
                ,Extensao
                ,TamanhoArquivo
                ,NumeroAnexo
                ,MesAno
                ,BitNovoContrato
                ,BitRenovacao
                ,BitAditivo
                ,DataAtualizado
            FROM {TABELA_ANEXOS_CONTRATOS_ADMIN}
            WHERE
                (
                    :id_contrato IS NOT NULL
                    AND IDFatoControleContratosEuromidia = :id_contrato
                )
                OR
                (
                    :id_card IS NOT NULL
                    AND IDFatoKanbanCard = :id_card
                )
            ORDER BY
                ISNULL(NumeroAnexo, 999999) ASC,
                DataAtualizado ASC,
                IDFatoAnexosContratosEuromidia ASC
        """),
        {
            "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
            "id_card": int(id_card) if id_card not in (None, "", 0) else None,
        },
    ).mappings().all()

    anexos = []
    for row in rows:
        anexo = dict(row)
        tamanho = anexo.get("TamanhoArquivo") or 0
        try:
            tamanho_bytes = float(tamanho)
        except Exception:
            tamanho_bytes = 0.0

        if tamanho_bytes >= 1024 * 1024:
            anexo["TamanhoArquivoFormatado"] = f"{tamanho_bytes / (1024 * 1024):.2f} MB"
        elif tamanho_bytes >= 1024:
            anexo["TamanhoArquivoFormatado"] = f"{tamanho_bytes / 1024:.2f} KB"
        else:
            anexo["TamanhoArquivoFormatado"] = f"{tamanho_bytes:.0f} bytes"

        anexos.append(anexo)

    return anexos


def _anexos_contrato_linha_para_json_admin(row: dict) -> dict:
    id_anexo = _int_ou_none(row.get("IDFatoAnexosContratosEuromidia"))
    return {
        "id": id_anexo,
        "numero": row.get("NumeroAnexo"),
        "nome_arquivo": row.get("NomeArquivo") or "",
        "extensao": row.get("Extensao") or "",
        "tamanho": row.get("TamanhoArquivoFormatado") or "",
        "mes_ano": row.get("MesAno") or "",
        "data_atualizado": row.get("DataAtualizado").strftime("%d/%m/%Y %H:%M") if hasattr(row.get("DataAtualizado"), "strftime") else _texto_ou_vazio(row.get("DataAtualizado")),
        "download_url": url_for("admin.download_anexo_contrato", id_anexo=id_anexo) if id_anexo else "",
        "remover_url": url_for("admin.remover_anexo_contrato", id_anexo=id_anexo) if id_anexo else "",
    }


def _processar_upload_anexos_contrato_admin(
    *,
    arquivos: list[dict],
    id_solicitacao: int | None,
    id_fato_controle_contratos: int | None,
    id_fato_contrato_d4: int | None,
    id_fato_kanban_card: int | None,
    tipo_solicitacao: str | None,
) -> dict:
    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_d4 = _int_ou_none(id_fato_contrato_d4)
    id_card = _int_ou_none(id_fato_kanban_card)
    id_solic = _int_ou_none(id_solicitacao)

    # O upload pode ter sido enfileirado antes da aprovação e processado depois.
    # Por isso releio a solicitação aqui no worker e aproveito o ID do contrato se ele já existir.
    if id_solic not in (None, "", 0):
        try:
            cab_atual = _obter_cabecalho_solicitacao_bruta(int(id_solic)) or {}
            id_contrato = _int_ou_none(id_contrato) or _int_ou_none(cab_atual.get("IDFatoControleContratosEuromidia"))
            id_card = _int_ou_none(id_card) or _int_ou_none(cab_atual.get("IDFatoKanbanCard"))
            tipo_solicitacao = tipo_solicitacao or cab_atual.get("TipoSolicitacao")
        except Exception:
            current_app.logger.exception(
                "ANEXOS_CONTRATO | não consegui reler solicitação no worker | id_solicitacao=%s",
                id_solic,
            )

    if id_contrato in (None, "", 0) and id_card in (None, "", 0):
        raise RuntimeError("Não é possível anexar arquivo sem IDFatoControleContratosEuromidia nem IDFatoKanbanCard.")

    pasta_base = _anexos_contrato_pasta_base_admin()
    pasta_temp = _anexos_contrato_pasta_temp_admin().resolve()
    pasta_base.mkdir(parents=True, exist_ok=True)

    flags = _anexos_contrato_flags_tipo_admin(
        tipo_solicitacao=tipo_solicitacao,
        id_fato_kanban_card=id_card,
    )

    numero_anexo = _anexos_contrato_proximo_numero_admin(
        id_fato_controle_contratos=id_contrato,
        id_fato_kanban_card=id_card,
    )

    inseridos = []
    erros = []
    data_referencia = datetime.now()
    mes_ano = _anexos_contrato_mes_ano_admin(data_referencia)

    for arquivo in arquivos or []:
        nome_original = _texto_ou_vazio(arquivo.get("nome_original"))
        caminho_temp_txt = _texto_ou_vazio(arquivo.get("caminho_temp"))
        tamanho_bytes = float(arquivo.get("tamanho_bytes") or 0)

        try:
            _anexos_contrato_validar_extensao_admin(nome_original)
            caminho_temp = Path(caminho_temp_txt).expanduser().resolve()

            if not caminho_temp.exists() or not caminho_temp.is_file():
                raise RuntimeError("Arquivo temporário não encontrado pelo worker.")

            if pasta_temp != caminho_temp.parent and pasta_temp not in caminho_temp.parents:
                raise RuntimeError("Arquivo temporário fora da pasta permitida.")

            nome_final = _anexos_contrato_montar_nome_final_admin(
                nome_original=nome_original,
                id_fato_controle_contratos=id_contrato,
                id_fato_kanban_card=id_card,
                id_solicitacao=id_solic,
                data_referencia=data_referencia,
            )
            extensao = _anexos_contrato_extensao_admin(nome_final)
            destino = pasta_base / nome_final
            shutil.move(str(caminho_temp), str(destino))

            url_relativa = _anexos_contrato_url_relativa_admin(nome_final)

            db.session.execute(
                text(f"""
                    INSERT INTO {TABELA_ANEXOS_CONTRATOS_ADMIN} (
                         IDFatoControleContratosEuromidia
                        ,IDFatoContratoD4
                        ,IDFatoKanbanCard
                        ,NomeArquivo
                        ,UrlAnexo
                        ,Extensao
                        ,TamanhoArquivo
                        ,NumeroAnexo
                        ,MesAno
                        ,BitNovoContrato
                        ,BitRenovacao
                        ,BitAditivo
                        ,DataAtualizado
                    )
                    VALUES (
                         :id_contrato
                        ,:id_d4
                        ,:id_card
                        ,:nome_arquivo
                        ,:url_anexo
                        ,:extensao
                        ,:tamanho
                        ,:numero_anexo
                        ,:mes_ano
                        ,:bit_novo
                        ,:bit_renovacao
                        ,:bit_aditivo
                        ,GETDATE()
                    )
                """),
                {
                    "id_contrato": int(id_contrato) if id_contrato not in (None, "", 0) else None,
                    "id_d4": int(id_d4) if id_d4 not in (None, "", 0) else None,
                    "id_card": int(id_card) if id_card not in (None, "", 0) else None,
                    "nome_arquivo": nome_final,
                    "url_anexo": url_relativa,
                    "extensao": extensao,
                    "tamanho": tamanho_bytes,
                    "numero_anexo": int(numero_anexo),
                    "mes_ano": mes_ano,
                    "bit_novo": int(flags["BitNovoContrato"]),
                    "bit_renovacao": int(flags["BitRenovacao"]),
                    "bit_aditivo": int(flags["BitAditivo"]),
                },
            )

            inseridos.append({
                "numero_anexo": int(numero_anexo),
                "nome_arquivo": nome_final,
                "url_anexo": url_relativa,
            })
            numero_anexo += 1

        except Exception as exc:
            erros.append({
                "nome_original": nome_original,
                "erro": str(exc),
            })
            current_app.logger.exception(
                "ANEXOS_CONTRATO | falha ao processar arquivo | nome=%s | id_solicitacao=%s | id_contrato=%s | id_card=%s",
                nome_original,
                id_solic,
                id_contrato,
                id_card,
            )

    db.session.commit()

    return {
        "ok": len(inseridos) > 0 and not erros,
        "status": "processado_com_erros" if erros else "processado",
        "id_solicitacao": int(id_solic) if id_solic else None,
        "id_contrato": int(id_contrato) if id_contrato else None,
        "id_card": int(id_card) if id_card else None,
        "inseridos": inseridos,
        "erros": erros,
    }


def _sincronizar_anexos_contrato_apos_aprovacao_admin(
    *,
    id_solicitacao: int | None,
    id_fato_controle_contratos: int | None,
    id_fato_kanban_card: int | None,
    tipo_solicitacao: str | None,
    id_fato_contrato_d4: int | None = None,
) -> dict:
    id_contrato = _int_ou_none(id_fato_controle_contratos)
    id_card = _int_ou_none(id_fato_kanban_card)
    id_solic = _int_ou_none(id_solicitacao)
    id_d4 = _int_ou_none(id_fato_contrato_d4)

    if id_contrato in (None, "", 0):
        return {"ok": False, "status": "sem_id_contrato", "atualizados": 0}

    if id_card in (None, "", 0):
        return {"ok": False, "status": "sem_id_card", "atualizados": 0}

    flags = _anexos_contrato_flags_tipo_admin(
        tipo_solicitacao=tipo_solicitacao,
        id_fato_kanban_card=id_card,
    )

    rows = db.session.execute(
        text(f"""
            SELECT
                 IDFatoAnexosContratosEuromidia
                ,NomeArquivo
                ,UrlAnexo
            FROM {TABELA_ANEXOS_CONTRATOS_ADMIN}
            WHERE IDFatoKanbanCard = :id_card
              AND (
                    IDFatoControleContratosEuromidia IS NULL
                    OR IDFatoControleContratosEuromidia = :id_contrato
                  )
            ORDER BY NumeroAnexo ASC, IDFatoAnexosContratosEuromidia ASC
        """),
        {
            "id_card": int(id_card),
            "id_contrato": int(id_contrato),
        },
    ).mappings().all()

    atualizados = 0
    renomeados = 0
    erros_rename = []

    pasta_base = _anexos_contrato_pasta_base_admin()
    pasta_base.mkdir(parents=True, exist_ok=True)

    for row in rows:
        anexo = dict(row)
        id_anexo = int(anexo["IDFatoAnexosContratosEuromidia"])
        nome_atual = _texto_ou_vazio(anexo.get("NomeArquivo"))
        url_atual = _texto_ou_vazio(anexo.get("UrlAnexo"))
        nome_novo = nome_atual
        url_nova = url_atual

        try:
            if nome_atual and not nome_atual.startswith(f"{int(id_contrato)}_"):
                resto_nome = nome_atual.split("_", 1)[1] if "_" in nome_atual else nome_atual
                candidato = f"{int(id_contrato)}_{resto_nome}"
                caminho_atual = _anexos_contrato_resolver_caminho_admin(url_atual)
                nome_unico = _anexos_contrato_nome_unico_admin(pasta_base, candidato)
                caminho_novo = pasta_base / nome_unico

                if caminho_atual and caminho_atual.exists() and caminho_atual.resolve() != caminho_novo.resolve():
                    caminho_atual.rename(caminho_novo)
                    renomeados += 1

                nome_novo = nome_unico
                url_nova = _anexos_contrato_url_relativa_admin(nome_unico)

        except Exception as exc:
            erros_rename.append({"id_anexo": id_anexo, "erro": str(exc)})
            current_app.logger.exception(
                "ANEXOS_CONTRATO | falha ao renomear anexo após aprovação | id_anexo=%s | id_contrato=%s | id_card=%s",
                id_anexo,
                id_contrato,
                id_card,
            )

        db.session.execute(
            text(f"""
                UPDATE {TABELA_ANEXOS_CONTRATOS_ADMIN}
                   SET IDFatoControleContratosEuromidia = :id_contrato,
                       IDFatoContratoD4 = COALESCE(:id_d4, IDFatoContratoD4),
                       IDFatoKanbanCard = COALESCE(IDFatoKanbanCard, :id_card),
                       NomeArquivo = :nome_arquivo,
                       UrlAnexo = :url_anexo,
                       BitNovoContrato = :bit_novo,
                       BitRenovacao = :bit_renovacao,
                       BitAditivo = :bit_aditivo
                 WHERE IDFatoAnexosContratosEuromidia = :id_anexo
            """),
            {
                "id_contrato": int(id_contrato),
                "id_d4": int(id_d4) if id_d4 not in (None, "", 0) else None,
                "id_card": int(id_card),
                "nome_arquivo": nome_novo,
                "url_anexo": url_nova,
                "bit_novo": int(flags["BitNovoContrato"]),
                "bit_renovacao": int(flags["BitRenovacao"]),
                "bit_aditivo": int(flags["BitAditivo"]),
                "id_anexo": id_anexo,
            },
        )
        atualizados += 1

    return {
        "ok": True,
        "status": "sincronizado",
        "id_solicitacao": int(id_solic) if id_solic else None,
        "id_contrato": int(id_contrato),
        "id_card": int(id_card),
        "atualizados": atualizados,
        "renomeados": renomeados,
        "erros_rename": erros_rename,
    }

def _obter_solicitacao_contrato_detalhe(id_solicitacao: int):
    sql_cabecalho = text("""
        SELECT TOP 1
               fsce.[IDFatoSolicitacaoContratoEuromidia]
              ,fsce.[IDFatoKanbanCard]
              ,fsce.[IDFatoControleContratosEuromidia]
              ,fsce.[IDDimStatusContratos]
              ,fsce.[IDDimUsuariosCriacao]
              ,fsce.[IDDimUsuariosEnvioAvaliacao]
              ,fsce.[IDDimUsuariosAprovacao]
              ,fsce.[IDDimUsuariosRejeicao]
              ,fsce.[IDDimUsuariosCancelamento]
              ,fsce.[IDEmpresa]
              ,fsce.[IDCategoriaMarca]
              ,fsce.[IDEmpresaProprietaria]
              ,fsce.[TipoSolicitacao]
              ,fsce.[Referencia]
              ,fsce.[NumeroContrato]
              ,fsce.[NumeroPrevia]
              ,fsce.[CNPJ]
              ,fsce.[DataAssinaturaRenovacao]
              ,fsce.[IDTrimestre]
              ,fsce.[DataLancamento]
              ,fsce.[RazaoSocial]
              ,fsce.[CPF]
              ,fsce.[MarcaExibida]
              ,fsce.[Vendedor]
              ,fsce.[TipoDocumento]
              ,fsce.[Origem]
              ,fsce.[SDR]
              ,fsce.[Agencia]
              ,fsce.[CnpjAgencia]
              ,fsce.[Bureau]
              ,fsce.[CnpjBureau]
              ,fsce.[Intermediario]
              ,fsce.[CnpjIntermediario]
              ,fsce.[QuantidadePontos]
              ,fsce.[QuantidadeFaces]
              ,fsce.[TotalFaturamentoBrutoMensal]
              ,fsce.[TotalPercentualPermuta]
              ,fsce.[TotalCotaOportunidade]
              ,fsce.[TotalValorPermuta]
              ,fsce.[TotalFaturamentoLiquidoPermuta]
              ,fsce.[TotalBrutoContrato]
              ,fsce.[TotalLiquidoContratoAGBRCTACORDO]
              ,fsce.[TotalLiquidoContratoAGBRVENDGERCOOR]
              ,fsce.[TotalPercentualAgencia]
              ,fsce.[TotalValorMensalAgencia]
              ,fsce.[TotalPercentualBureau]
              ,fsce.[TotalValorBureauMensal]
              ,fsce.[TotalPercentualCartaAcordo]
              ,fsce.[TotalValorCartaAcordoMensal]
              ,fsce.[TotalValorOutrasComissoes]
              ,fsce.[TotalFaturamentoLiquidoMensal]
              ,fsce.[TotalPercentualComissaoVendedor]
              ,fsce.[TotalValorVendedor]
              ,fsce.[ValorVendedorTotal]
              ,fsce.[TotalPercentualComissaoCoordenacao]
              ,fsce.[Observacao]
              ,fsce.[MotivoRejeicao]
              ,fsce.[MotivoCancelamento]
              ,fsce.[BitAtivo]
              ,fsce.[DataCriacao]
              ,fsce.[DataAtualizacao]
              ,fsce.[DataEnvioAvaliacao]
              ,fsce.[DataAprovacao]
              ,fsce.[DataRejeicao]
              ,fsce.[DataCancelamento]
              ,du.[NomeUsuario] AS [NomeUsuarioCriacao]
              ,de.[RazaoSocial] AS [RazaoSocialEmpresa]
              ,de.[NomeFantasia] AS [NomeFantasiaEmpresa]
              ,de.[CNPJ] AS [CNPJEmpresa]
              ,de.[Municipio] AS [MunicipioEmpresa]
              ,de.[UF] AS [UFEmpresa]
              ,de.[Email] AS [EmailEmpresa]
              ,ep.[Logo] AS [LogoEmpresaProprietaria]
              ,ep.[RazaoSocial] AS [RazaoSocialEmpresaProprietaria]
              ,dsc.[Status] AS [StatusContrato]
        FROM [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia] fsce
        INNER JOIN [Integracao].[Silver].[DimUsuarios] du
                ON du.[IDDimUsuarios] = fsce.[IDDimUsuariosCriacao]
        INNER JOIN [Integracao].[Silver].[DimEmpresas] de
                ON de.[IDEmpresa] = fsce.[IDEmpresa]
        INNER JOIN [Integracao].[dbo].[EmpresaProprietaria] ep
                ON ep.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        LEFT JOIN [Integracao].[Silver].[DimStatusContratos] dsc
                ON dsc.[IDDimStatusContratos] = fsce.[IDDimStatusContratos]
               AND dsc.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        WHERE fsce.[IDFatoSolicitacaoContratoEuromidia] = :id_solicitacao
    """)

    sql_itens = text("""
        WITH itens_base AS (
            SELECT
                   fsci.[IDFatoSolicitacaoContratoItemEuromidia]
                  ,fsci.[IDFatoSolicitacaoContratoEuromidia]
                  ,fsci.[IDFatoControleContratosEuromidia]
                  ,fsci.[IDFatoControleContratosItensEuromidia]
                  ,fsci.[IDFatoKanbanCard]
                  ,fsci.[IDDimUsuariosCriacao]
                  ,fsci.[IDDimUsuariosAtualizacao]
                  ,fsci.[IDVendedor]
                  ,fsci.[IDPainelEuromidia]
                  ,fsci.[IDDimFacesPaineis]
                  ,fsci.[IDDimCheckingHistorico] AS [IDDimCheckinHistorico]
                  ,fsci.[IDEmpresaProprietaria]
                  ,fsci.[Referencia]
                  ,fsci.[NumeroContrato]
                  ,fsci.[NumeroPrevia]
                  ,fsci.[CNPJ]
                  ,fsci.[CodPonto] AS [CodPontoOriginal]
                  ,fsci.[CodFace] AS [CodFaceOriginal]
                  ,CAST(
                        COALESCE(
                            NULLIF(LTRIM(RTRIM(CAST(fsci.[CodPonto] AS varchar(50)))), ''),
                            NULLIF(LTRIM(RTRIM(CAST(df.[CodPonto] AS varchar(50)))), ''),
                            NULLIF(LTRIM(RTRIM(CAST(dp.[CodPonto] AS varchar(50)))), '')
                        ) AS varchar(50)
                   ) AS [CodPonto]
                  ,CAST(
                        COALESCE(
                            NULLIF(UPPER(LTRIM(RTRIM(CAST(fsci.[CodFace] AS varchar(50))))), ''),
                            NULLIF(UPPER(LTRIM(RTRIM(CAST(df.[CodFace] AS varchar(50))))), '')
                        ) AS varchar(50)
                   ) AS [CodFace]
                  ,fsci.[DataLancamento]
                  ,fsci.[Cota]
                  ,fsci.[CidadeExibicao] AS [CidadeExibicaoOriginal]
                  ,COALESCE(
                        NULLIF(LTRIM(RTRIM(fsci.[CidadeExibicao])), ''),
                        NULLIF(LTRIM(RTRIM(dp.[Cidade])), '')
                   ) AS [CidadeExibicao]
                  ,fsci.[Tipo] AS [TipoOriginal]
                  ,COALESCE(
                        NULLIF(LTRIM(RTRIM(fsci.[Tipo])), ''),
                        NULLIF(LTRIM(RTRIM(df.[Tipo])), ''),
                        NULLIF(LTRIM(RTRIM(dp.[Tipo])), '')
                   ) AS [Tipo]
                  ,fsci.[Origem]
                  ,fsci.[EmpresaEuro]
                  ,fsci.[CnpjExibibora]
                  ,fsci.[TipoDocumento]
                  ,fsci.[RazaoSocial]
                  ,fsci.[CPF]
                  ,fsci.[MarcaExibida]
                  ,fsci.[Vendedor]
                  ,fsci.[SDR]
                  ,fsci.[Agencia]
                  ,fsci.[CnpjAgencia]
                  ,fsci.[Bureau]
                  ,fsci.[CnpjBureau]
                  ,fsci.[Intermediario]
                  ,fsci.[CnpjIntermediario]
                  ,fsci.[DataAssinaturaRenovacao]
                  ,fsci.[IDTrimestre]
                  ,fsci.[TexmpoExposicao]
                  ,fsci.[DataInicioPrevisto]
                  ,fsci.[DataTerminoPrevisto]
                  ,fsci.[InicioRenovacao]
                  ,fsci.[FaturamentoBrutoMensal]
                  ,fsci.[PercentualPermuta]
                  ,fsci.[CotaOportunidade]
                  ,fsci.[ValorPermuta]
                  ,fsci.[FaturamentoLiquidoPermuta]
                  ,fsci.[NumeroParcelas]
                  ,fsci.[DataInicioVencimento]
                  ,fsci.[TotalBrutoContrato]
                  ,fsci.[TotalLiquidoContratoAGBRCTACORDO]
                  ,fsci.[TotalLiquidoContratoAGBRVENDGERCOOR]
                  ,fsci.[PercentualAgencia]
                  ,fsci.[ValorMensalAgencia]
                  ,fsci.[PercentualBureau]
                  ,fsci.[ValorBureauMensal]
                  ,fsci.[PercentualCartaAcordo]
                  ,fsci.[ValorCartaAcordoMensal]
                  ,fsci.[ValorOutrasComissoes]
                  ,fsci.[FaturamentoLiquidoMensal]
                  ,fsci.[PercentualComissaoVendedor]
                  ,fsci.[ValorVendedor]
                  ,fsci.[ValorVendedorTotal]
                  ,fsci.[PercentualComissaoCoordenacao]
                  ,fsci.[ValorCoordenador]
                  ,fsci.[ValorCoordenadorTotal]
                  ,fsci.[PercentualComissaoGerencia]
                  ,fsci.[ValorGerencia]
                  ,fsci.[ValorGerenciaTotal]
                  ,fsci.[AtivoCancelamento]
                  ,fsci.[FaturamentoLiquidoFinalMensal]
                  ,fsci.[ComissaoGerenciaNordeste]
                  ,fsci.[Faturamento]
                  ,fsci.[DataCancelamento]
                  ,fsci.[OBS]
                  ,fsci.[DataFimEfetiva]
                  ,fsci.[Status]
                  ,fsci.[BitAtivo]
                  ,fsci.[DataCriacao]
                  ,fsci.[DataAtualizacao]
                  ,fsci.[BitSolicitacaoAtiva]
                  ,fsci.[IDDimTipoDocumento]
                  ,df.[Face] AS [FaceDescricaoCadastro]
                  ,df.[CodFace] AS [CodFaceCadastro]
                  ,df.[Tipo] AS [TipoFaceCadastro]
                  ,dp.[Cidade] AS [CidadePainelCadastro]
                  ,dp.[UF] AS [UFPainelCadastro]
                  ,dp.[Tipo] AS [TipoPainelCadastro]
                  ,dp.[Logradouro] AS [LogradouroPainelCadastro]
                  ,dp.[Bairro] AS [BairroPainelCadastro]
                  ,dp.[Referencia] AS [ReferenciaPainelCadastro]
                  ,COALESCE(fsci.[IDPainelEuromidia], df.[IDDimPaineisEuromidia]) AS [IDDimPaineisEuromidia]
                  ,fknp.[IDFatoKanbanNegociacaoPreco] AS [IDNegociacaoPreco]
                  ,fknp.[CustoAtual] AS [CustoAtualNegociacao]
                  ,fknp.[PrecoAtual] AS [PrecoAtualNegociacao]
                  ,fknp.[PrecoProposto] AS [PrecoPropostoNegociacao]
                  ,fknp.[DescontoProposto] AS [DescontoPropostoNegociacao]
                  ,fknp.[MargemProposta] AS [MargemPropostaNegociacao]
                  ,fknp.[PeriodoInicio] AS [PeriodoInicioNegociacao]
                  ,fknp.[PeriodoTermino] AS [PeriodoTerminoNegociacao]
                  ,fcp.[IDFatoContratoItemPrecoPraticadoEuromidia] AS [IDPrecoPraticado]
                  ,fcp.[PrecoPraticado] AS [PrecoPraticadoOficial]
                  ,fcp.[PrecoProposto] AS [PrecoPropostoOficial]
                  ,fcp.[CustoPainel] AS [CustoPainelOficial]
                  ,fcp.[DescontoPercentual] AS [DescontoPercentualOficial]
                  ,fcp.[MargemPercentual] AS [MargemPercentualOficial]
                  ,ROW_NUMBER() OVER (
                        PARTITION BY
                            fsci.[IDFatoSolicitacaoContratoEuromidia],
                            CASE
                                WHEN ISNULL(fsci.[IDFatoControleContratosItensEuromidia], 0) > 0 THEN
                                    CONCAT('ITEM|', CAST(fsci.[IDFatoControleContratosItensEuromidia] AS varchar(50)))
                                ELSE
                                    CONCAT(
                                        'LOGICO|',
                                        CAST(ISNULL(fsci.[IDFatoControleContratosEuromidia], 0) AS varchar(50)),
                                        '|', LTRIM(RTRIM(ISNULL(CAST(COALESCE(NULLIF(fsci.[CodPonto], ''), CAST(df.[CodPonto] AS varchar(50)), CAST(dp.[CodPonto] AS varchar(50))) AS varchar(50)), ''))),
                                        '|', UPPER(LTRIM(RTRIM(ISNULL(CAST(COALESCE(NULLIF(fsci.[CodFace], ''), df.[CodFace]) AS varchar(50)), ''))))
                                    )
                            END
                        ORDER BY
                            CASE WHEN fsci.[DataAtualizacao] IS NULL THEN 1 ELSE 0 END,
                            fsci.[DataAtualizacao] DESC,
                            CASE WHEN fsci.[DataCriacao] IS NULL THEN 1 ELSE 0 END,
                            fsci.[DataCriacao] DESC,
                            fsci.[IDFatoSolicitacaoContratoItemEuromidia] DESC
                    ) AS rn
            FROM [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia] fsci
            LEFT JOIN [Integracao].[Silver].[DimFacesPaineis] df
                   ON df.[IDDimFacesPaineis] = fsci.[IDDimFacesPaineis]
            LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] dp
                   ON dp.[IDDimPaineisEuromidia] = COALESCE(fsci.[IDPainelEuromidia], df.[IDDimPaineisEuromidia])
            LEFT JOIN [Kanban].[Silver].[FatoKanbanNegociacaoPreco] fknp
                   ON fknp.[IDFatoKanbanCard] = fsci.[IDFatoKanbanCard]
                  AND ISNULL(fknp.[IDDimPaineisEuromidia], 0) = ISNULL(COALESCE(fsci.[IDPainelEuromidia], df.[IDDimPaineisEuromidia]), 0)
                  AND ISNULL(fknp.[IDDimFacesPaineis], 0) = ISNULL(fsci.[IDDimFacesPaineis], 0)
            LEFT JOIN [Integracao].[Silver].[FatoContratoItemPrecoPraticadoEuromidia] fcp
                   ON fcp.[IDFatoKanbanCard] = fsci.[IDFatoKanbanCard]
                  AND ISNULL(fcp.[IDDimPaineisEuromidia], 0) = ISNULL(COALESCE(fsci.[IDPainelEuromidia], df.[IDDimPaineisEuromidia]), 0)
                  AND ISNULL(fcp.[IDDimFacesPaineis], 0) = ISNULL(fsci.[IDDimFacesPaineis], 0)
            WHERE fsci.[IDFatoSolicitacaoContratoEuromidia] = :id_solicitacao
        )
        SELECT *
        FROM itens_base
        WHERE rn = 1
        ORDER BY
            LTRIM(RTRIM(ISNULL(CAST([CodPonto] AS varchar(50)), ''))) ASC,
            UPPER(LTRIM(RTRIM(ISNULL(CAST([CodFace] AS varchar(50)), '')))) ASC,
            [IDFatoSolicitacaoContratoItemEuromidia] ASC
    """)

    cab = db.session.execute(
        sql_cabecalho,
        {"id_solicitacao": int(id_solicitacao)}
    ).mappings().first()

    if not cab:
        return None

    cab = dict(cab)
    cab["LogoEmpresaProprietariaUrl"] = _resolver_url_logo_empresa_proprietaria(cab.get("LogoEmpresaProprietaria"))
    cab["TipoSolicitacaoExibicao"] = _tipo_solicitacao_normalizado(cab.get("TipoSolicitacao"))
    cab["StatusContrato"] = (cab.get("StatusContrato") or "").strip()
    cab["DataAssinaturaRenovacaoInput"] = _data_para_input_date(cab.get("DataAssinaturaRenovacao"))
    cab["DataLancamentoInput"] = _data_para_input_date(cab.get("DataLancamento"))
    cab["DataCriacaoInput"] = _data_para_input_date(cab.get("DataCriacao"))
    cab["DataEnvioAvaliacaoInput"] = _data_para_input_date(cab.get("DataEnvioAvaliacao"))

    itens_rows = db.session.execute(
        sql_itens,
        {"id_solicitacao": int(id_solicitacao)}
    ).mappings().all()

    itens = []
    for row in itens_rows:
        item = dict(row)
        item = _aplicar_fallback_layout_item_solicitacao(item, cab)
        item["CodPonto"] = _texto_ou_none(item.get("CodPonto") or item.get("CodPontoOriginal"))
        item["CodFace"] = _texto_ou_none(item.get("CodFace") or item.get("CodFaceOriginal"))
        if item.get("CodFace"):
            item["CodFace"] = str(item["CodFace"]).strip().upper()
        status_item = _texto_ou_none(item.get("Status"))
        status_cabecalho = _texto_ou_none(cab.get("StatusContrato"))
        item["StatusExibicao"] = status_item or status_cabecalho or "EM AVALIAÇÃO"
        item["DataLancamentoInput"] = _data_para_input_date(item.get("DataLancamento"))
        item["DataAssinaturaRenovacaoInput"] = _data_para_input_date(item.get("DataAssinaturaRenovacao"))
        item["DataInicioPrevistoInput"] = _data_para_input_date(item.get("DataInicioPrevisto"))
        item["DataTerminoPrevistoInput"] = _data_para_input_date(item.get("DataTerminoPrevisto"))
        item["DataInicioVencimentoInput"] = _data_para_input_date(item.get("DataInicioVencimento"))
        item["DataCancelamentoInput"] = _data_para_input_date(item.get("DataCancelamento"))
        item["DataFimEfetivaInput"] = _data_para_input_date(item.get("DataFimEfetiva"))
        item["AgendamentosFaceContrato"] = []
        item["TemAgendamentoFaceContrato"] = False
        item["QuantidadeAgendamentosFaceContrato"] = 0
        itens.append(item)

    ag_rows_final = db.session.execute(
        text("""
            SELECT
                 fsci.IDFatoSolicitacaoContratoItemEuromidia
                ,ag.IDFatoAgendamentoFaceContrato
                ,ag.IDFatoControleContratosEuromidia
                ,ag.IDFatoControleContratosItensEuromidia
                ,ag.Sequencia
                ,ag.DataInicio
                ,ag.DataTermino
                ,DATEDIFF(DAY, ag.DataInicio, ag.DataTermino) + 1 AS QuantidadeDias
                ,ag.Situacao
                ,ag.Referencia
                ,ag.BitAtivo
                ,ag.DataAtualizado
            FROM [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia] fsci
            INNER JOIN [Integracao].[Silver].[FatoAgendamentoFaceContrato] ag
                    ON ag.IDFatoControleContratosItensEuromidia = fsci.IDFatoControleContratosItensEuromidia
                   AND ag.IDFatoControleContratosEuromidia = COALESCE(fsci.IDFatoControleContratosEuromidia, :id_contrato_controle)
            WHERE fsci.IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
              AND ISNULL(ag.BitAtivo, 1) = 1
            ORDER BY
                fsci.IDFatoSolicitacaoContratoItemEuromidia ASC,
                ag.Sequencia ASC,
                ag.DataInicio ASC
        """),
        {
            "id_solicitacao": int(id_solicitacao),
            "id_contrato_controle": _int_ou_none(cab.get("IDFatoControleContratosEuromidia")),
        },
    ).mappings().all()

    ag_rows = list(ag_rows_final)

    mapa_item_por_id = {
        int(item["IDFatoSolicitacaoContratoItemEuromidia"]): item
        for item in itens
        if _int_ou_none(item.get("IDFatoSolicitacaoContratoItemEuromidia")) not in (None, "", 0)
    }

    for row in ag_rows:
        id_item_solic = _int_ou_none(row.get("IDFatoSolicitacaoContratoItemEuromidia"))
        item = mapa_item_por_id.get(int(id_item_solic)) if id_item_solic not in (None, "", 0) else None
        if not item:
            continue

        ag = dict(row)
        ag["DataInicioInput"] = _data_para_input_date(ag.get("DataInicio"))
        ag["DataTerminoInput"] = _data_para_input_date(ag.get("DataTermino"))
        ag["QuantidadeDias"] = int(ag.get("QuantidadeDias") or 0)
        ag["Situacao"] = (ag.get("Situacao") or "PROGRAMADO").strip().upper()
        ag["Referencia"] = (ag.get("Referencia") or "").strip()
        if _referencia_agendamento_pendente_eh_tecnica(ag.get("Referencia")):
            ag["Referencia"] = ""
        item["AgendamentosFaceContrato"].append(ag)

    for item in itens:
        _marcar_periodo_restante_visual(item)
        qtd_ag = len(item.get("AgendamentosFaceContrato") or [])
        item["TemAgendamentoFaceContrato"] = qtd_ag > 0
        item["QuantidadeAgendamentosFaceContrato"] = qtd_ag

    contatos_contrato = _buscar_dim_email_contrato_admin(
        id_fato_controle_contratos=cab.get("IDFatoControleContratosEuromidia"),
        id_fato_kanban_card=cab.get("IDFatoKanbanCard"),
    )

    anexos_contrato = _buscar_anexos_contrato_admin(
        id_fato_controle_contratos=cab.get("IDFatoControleContratosEuromidia"),
        id_fato_kanban_card=cab.get("IDFatoKanbanCard"),
    )

    return {
        "solicitacao": cab,
        "itens": itens,
        "contatos_contrato": contatos_contrato,
        "anexos_contrato": anexos_contrato,
        "diagrama_status": _montar_diagrama_status_contrato(
            id_empresa_proprietaria=cab.get("IDEmpresaProprietaria"),
            id_status_atual=cab.get("IDDimStatusContratos"),
            nome_status_atual=cab.get("StatusContrato"),
        ),
    }



@admin.route("/aprovacao/contratos", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def lista_aprovacao_contratos():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    q = (request.args.get("q") or "").strip()

    if per_page not in (10, 20, 30, 50, 100):
        per_page = 10

    if page < 1:
        page = 1

    sql_total = text("""
        SELECT COUNT(1)
        FROM [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia] fsce
        INNER JOIN [Integracao].[Silver].[DimUsuarios] du
            ON du.[IDDimUsuarios] = fsce.[IDDimUsuariosCriacao]
        INNER JOIN [Integracao].[Silver].[DimEmpresas] de
            ON de.[IDEmpresa] = fsce.[IDEmpresa]
        INNER JOIN [Integracao].[dbo].[EmpresaProprietaria] ep
            ON ep.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        LEFT JOIN [Integracao].[Silver].[DimStatusContratos] dsc
            ON dsc.[IDDimStatusContratos] = fsce.[IDDimStatusContratos]
           AND dsc.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        WHERE ISNULL(fsce.[BitAtivo], 1) = 1
          AND (
                :q = ''
                OR CAST(fsce.[IDFatoSolicitacaoContratoEuromidia] AS varchar(50)) LIKE '%' + :q + '%'
                OR CAST(fsce.[IDFatoKanbanCard] AS varchar(50)) LIKE '%' + :q + '%'
                OR ISNULL(fsce.[CNPJ], '') LIKE '%' + :q + '%'
                OR ISNULL(de.[RazaoSocial], '') LIKE '%' + :q + '%'
                OR ISNULL(du.[NomeUsuario], '') LIKE '%' + :q + '%'
                OR ISNULL(fsce.[TipoSolicitacao], '') LIKE '%' + :q + '%'
                OR ISNULL(dsc.[Status], '') LIKE '%' + :q + '%'
            )
    """)

    total = int(db.session.execute(sql_total, {"q": q}).scalar() or 0)

    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    sql_itens = text("""
        SELECT
             fsce.[IDFatoSolicitacaoContratoEuromidia]
            ,fsce.[IDFatoKanbanCard]
            ,fsce.[IDFatoControleContratosEuromidia]
            ,fsce.[IDDimStatusContratos]
            ,fsce.[IDDimUsuariosCriacao]
            ,fsce.[IDDimUsuariosEnvioAvaliacao]
            ,fsce.[IDDimUsuariosAprovacao]
            ,fsce.[IDDimUsuariosRejeicao]
            ,fsce.[IDDimUsuariosCancelamento]
            ,fsce.[IDEmpresa]
            ,fsce.[IDCategoriaMarca]
            ,fsce.[IDEmpresaProprietaria]
            ,fsce.[TipoSolicitacao]
            ,fsce.[Referencia]
            ,fsce.[NumeroContrato]
            ,fsce.[NumeroPrevia]
            ,fsce.[CNPJ]
            ,fsce.[DataAssinaturaRenovacao]
            ,fsce.[IDTrimestre]
            ,fsce.[DataLancamento]
            ,fsce.[RazaoSocial] AS [RazaoSocialSolicitacao]
            ,fsce.[CPF]
            ,fsce.[MarcaExibida]
            ,fsce.[Vendedor]
            ,fsce.[TipoDocumento]
            ,fsce.[Origem]
            ,fsce.[SDR]
            ,fsce.[Agencia]
            ,fsce.[CnpjAgencia]
            ,fsce.[Bureau]
            ,fsce.[CnpjBureau]
            ,fsce.[Intermediario]
            ,fsce.[CnpjIntermediario]
            ,fsce.[QuantidadePontos]
            ,fsce.[QuantidadeFaces]
            ,fsce.[TotalFaturamentoBrutoMensal]
            ,fsce.[TotalPercentualPermuta]
            ,fsce.[TotalCotaOportunidade]
            ,fsce.[TotalValorPermuta]
            ,fsce.[TotalFaturamentoLiquidoPermuta]
            ,fsce.[TotalBrutoContrato]
            ,fsce.[TotalLiquidoContratoAGBRCTACORDO]
            ,fsce.[TotalLiquidoContratoAGBRVENDGERCOOR]
            ,fsce.[TotalPercentualAgencia]
            ,fsce.[TotalValorMensalAgencia]
            ,fsce.[TotalPercentualBureau]
            ,fsce.[TotalValorBureauMensal]
            ,fsce.[TotalPercentualCartaAcordo]
            ,fsce.[TotalValorCartaAcordoMensal]
            ,fsce.[TotalValorOutrasComissoes]
            ,fsce.[TotalFaturamentoLiquidoMensal]
            ,fsce.[TotalPercentualComissaoVendedor]
            ,fsce.[TotalValorVendedor]
            ,fsce.[ValorVendedorTotal]
            ,fsce.[TotalPercentualComissaoCoordenacao]
            ,fsce.[Observacao]
            ,fsce.[MotivoRejeicao]
            ,fsce.[MotivoCancelamento]
            ,fsce.[BitAtivo]
            ,fsce.[DataCriacao]
            ,fsce.[DataAtualizacao]
            ,fsce.[DataEnvioAvaliacao]
            ,fsce.[DataAprovacao]
            ,fsce.[DataRejeicao]
            ,fsce.[DataCancelamento]
            ,du.[NomeUsuario] AS [NomeUsuarioCriacao]
            ,de.[RazaoSocial] AS [RazaoSocialEmpresa]
            ,ep.[Logo] AS [LogoEmpresaProprietaria]
            ,ep.[RazaoSocial] AS [RazaoSocialEmpresaProprietaria]
            ,dsc.[Status] AS [StatusContrato]
        FROM [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia] fsce
        INNER JOIN [Integracao].[Silver].[DimUsuarios] du
            ON du.[IDDimUsuarios] = fsce.[IDDimUsuariosCriacao]
        INNER JOIN [Integracao].[Silver].[DimEmpresas] de
            ON de.[IDEmpresa] = fsce.[IDEmpresa]
        INNER JOIN [Integracao].[dbo].[EmpresaProprietaria] ep
            ON ep.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        LEFT JOIN [Integracao].[Silver].[DimStatusContratos] dsc
            ON dsc.[IDDimStatusContratos] = fsce.[IDDimStatusContratos]
           AND dsc.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        WHERE ISNULL(fsce.[BitAtivo], 1) = 1
          AND (
                :q = ''
                OR CAST(fsce.[IDFatoSolicitacaoContratoEuromidia] AS varchar(50)) LIKE '%' + :q + '%'
                OR CAST(fsce.[IDFatoKanbanCard] AS varchar(50)) LIKE '%' + :q + '%'
                OR ISNULL(fsce.[CNPJ], '') LIKE '%' + :q + '%'
                OR ISNULL(de.[RazaoSocial], '') LIKE '%' + :q + '%'
                OR ISNULL(du.[NomeUsuario], '') LIKE '%' + :q + '%'
                OR ISNULL(fsce.[TipoSolicitacao], '') LIKE '%' + :q + '%'
                OR ISNULL(dsc.[Status], '') LIKE '%' + :q + '%'
            )
        ORDER BY
            CASE WHEN fsce.[DataEnvioAvaliacao] IS NULL THEN 1 ELSE 0 END ASC,
            fsce.[DataEnvioAvaliacao] DESC,
            fsce.[IDFatoSolicitacaoContratoEuromidia] DESC
        OFFSET :offset ROWS
        FETCH NEXT :per_page ROWS ONLY
    """)

    rows = db.session.execute(
        sql_itens,
        {
            "q": q,
            "offset": offset,
            "per_page": per_page,
        }
    ).mappings().all()

    itens = []
    for r in rows:
        logo_url = _resolver_url_logo_empresa_proprietaria(r.get("LogoEmpresaProprietaria"))
        id_solic = int(r.get("IDFatoSolicitacaoContratoEuromidia"))

        itens.append(
            {
                "IDFatoSolicitacaoContratoEuromidia": id_solic,
                "IDFatoKanbanCard": r.get("IDFatoKanbanCard"),
                "IDFatoControleContratosEuromidia": r.get("IDFatoControleContratosEuromidia"),
                "DataEnvioAvaliacao": r.get("DataEnvioAvaliacao"),
                "NomeUsuarioCriacao": r.get("NomeUsuarioCriacao") or "—",
                "CNPJ": r.get("CNPJ") or "—",
                "RazaoSocialEmpresa": r.get("RazaoSocialEmpresa") or "—",
                "LogoEmpresaProprietaria": logo_url,
                "StatusContrato": r.get("StatusContrato") or "—",
                "TipoSolicitacao": _tipo_solicitacao_normalizado(r.get("TipoSolicitacao") or "—"),
                "url_detalhe": url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solic),
            }
        )

    inicio = 0 if total == 0 else (offset + 1)
    fim = min(offset + per_page, total)

    paginacao = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "inicio": inicio,
        "fim": fim,
    }

    filtros = {
        "q": q,
        "per_page": per_page,
    }

    return render_template(
        "admin/lista_aprovacao_contratos.html",
        itens=itens,
        filtros=filtros,
        paginacao=paginacao,
    )













# ======================================================================================
# Integração D4Sign - criação de contrato na aprovação Admin
# ======================================================================================

URL_BASE_D4SIGN_ADMIN = "https://secure.d4sign.com.br/api/v1"
NOME_COFRE_D4_CONTRATOS_ADMIN = "Contratos"
NOME_PASTA_RAIZ_D4_CONTRATOS_ADMIN = (os.getenv("D4SIGN_NOME_PASTA_RAIZ_CONTRATOS") or "Euromidia").strip() or "Euromidia"

MAPA_FASE_D4SIGN_ADMIN = {
    "1": "Processando",
    "2": "Aguardando Signatários",
    "3": "Aguardando Assinaturas",
    "4": "Finalizado",
    "5": "Arquivado",
    "6": "Cancelado",
    "7": "Editando",
}


def _d4sign_timeout_segundos_admin() -> int:
    """_d4sign_timeout_segundos_admin: eu defino timeout seguro para chamada externa na D4Sign."""

    try:
        return max(5, int(os.getenv("D4SIGN_TIMEOUT_SEGUNDOS", "30") or "30"))
    except Exception:
        return 30


def _d4sign_obter_credenciais_admin() -> tuple[str, str]:
    """_d4sign_obter_credenciais_admin: eu busco TOKEN_D4SIGN e CRYPTKEY_D4SIGN no .env do Flask."""

    token_api = (os.getenv("TOKEN_D4SIGN") or "").strip()
    crypt_key = (os.getenv("CRYPTKEY_D4SIGN") or "").strip()

    if not token_api:
        raise RuntimeError("TOKEN_D4SIGN não encontrado no .env do Flask.")

    if not crypt_key:
        raise RuntimeError("CRYPTKEY_D4SIGN não encontrado no .env do Flask.")

    return token_api, crypt_key


def _d4sign_montar_parametros_autenticacao_admin(parametros_extras: dict | None = None) -> dict:
    """_d4sign_montar_parametros_autenticacao_admin: eu monto tokenAPI e cryptKey para a D4Sign."""

    token_api, crypt_key = _d4sign_obter_credenciais_admin()

    parametros = {
        "tokenAPI": token_api,
        "cryptKey": crypt_key,
    }

    if parametros_extras:
        parametros.update(parametros_extras)

    return parametros


def _d4sign_executar_get_admin(caminho: str, parametros_extras: dict | None = None) -> dict:
    """_d4sign_executar_get_admin: eu faço GET na API da D4Sign e trato erro de forma explícita."""

    if requests is None:
        raise RuntimeError(
            "A biblioteca requests não está instalada no container Flask. "
            "Adicione requests no requirements.txt ou instale a dependência na imagem."
        )

    url = f"{URL_BASE_D4SIGN_ADMIN}{caminho}"

    resposta = requests.get(
        url,
        params=_d4sign_montar_parametros_autenticacao_admin(parametros_extras),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=_d4sign_timeout_segundos_admin(),
    )

    try:
        dados = resposta.json()
    except Exception:
        dados = {"resposta_texto": resposta.text}

    if not resposta.ok:
        raise RuntimeError(
            f"Erro GET D4Sign. Caminho={caminho}. "
            f"Status={resposta.status_code}. Resposta={dados}"
        )

    return dados if isinstance(dados, dict) else {"resposta": dados}


def _d4sign_executar_post_admin(caminho: str, payload: dict | None = None) -> dict:
    """_d4sign_executar_post_admin: eu faço POST JSON na API da D4Sign e trato erro de forma explícita."""

    if requests is None:
        raise RuntimeError(
            "A biblioteca requests não está instalada no container Flask. "
            "Adicione requests no requirements.txt ou instale a dependência na imagem."
        )

    url = f"{URL_BASE_D4SIGN_ADMIN}{caminho}"

    resposta = requests.post(
        url,
        params=_d4sign_montar_parametros_autenticacao_admin(),
        json=payload or {},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=_d4sign_timeout_segundos_admin(),
    )

    try:
        dados = resposta.json()
    except Exception:
        dados = {"resposta_texto": resposta.text}

    if not resposta.ok:
        raise RuntimeError(
            f"Erro POST D4Sign. Caminho={caminho}. "
            f"Status={resposta.status_code}. Resposta={dados}"
        )

    return dados if isinstance(dados, dict) else {"resposta": dados}


def _d4sign_primeiro_objeto_admin(resposta) -> dict:
    """_d4sign_primeiro_objeto_admin: eu normalizo respostas variadas da D4Sign para um dicionário."""

    if isinstance(resposta, dict):
        for chave in (
            "data",
            "document",
            "documents",
            "doc",
            "docs",
            "result",
            "results",
            "resposta",
            "message",
        ):
            valor = resposta.get(chave)

            if isinstance(valor, list) and valor:
                primeiro = valor[0]
                return primeiro if isinstance(primeiro, dict) else {}

            if isinstance(valor, dict):
                return valor

        return resposta

    if isinstance(resposta, list) and resposta:
        primeiro = resposta[0]
        return primeiro if isinstance(primeiro, dict) else {}

    return {}


def _d4sign_buscar_valor_recursivo_admin(objeto, nomes_chaves: set[str]):
    """_d4sign_buscar_valor_recursivo_admin: eu procuro uma chave em respostas aninhadas da D4Sign."""

    if isinstance(objeto, dict):
        for chave, valor in objeto.items():
            if str(chave or "").strip() in nomes_chaves and valor not in (None, ""):
                return valor

        for valor in objeto.values():
            encontrado = _d4sign_buscar_valor_recursivo_admin(valor, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    if isinstance(objeto, list):
        for item in objeto:
            encontrado = _d4sign_buscar_valor_recursivo_admin(item, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    return None


def _d4sign_obter_uuid_documento_admin(dados: dict) -> str:
    """_d4sign_obter_uuid_documento_admin: eu capturo o UUID do documento em respostas diferentes."""

    valor = _d4sign_buscar_valor_recursivo_admin(
        dados,
        {
            "uuidDoc",
            "uuid_doc",
            "uuid-document",
            "uuid_document",
            "uuidDocument",
            "uuid_documento",
            "UUIDDocumentoD4",
        },
    )

    if valor not in (None, ""):
        return str(valor).strip()

    texto = str(dados or "")
    try:
        match = re.search(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            texto,
        )
        if match:
            return match.group(0).strip()
    except Exception:
        pass

    return ""


def _d4sign_para_int_ou_none_admin(valor):
    """_d4sign_para_int_ou_none_admin: eu converto valores da D4Sign para inteiro quando possível."""

    try:
        if valor in (None, ""):
            return None

        texto = str(valor).strip()
        if not texto:
            return None

        return int(float(texto))
    except Exception:
        return None


def _d4sign_formatar_data_br_admin(valor) -> str:
    """_d4sign_formatar_data_br_admin: eu converto data para dd/mm/aaaa antes de enviar ao template."""

    if valor in (None, ""):
        return ""

    try:
        if hasattr(valor, "strftime"):
            return valor.strftime("%d/%m/%Y")
    except Exception:
        pass

    texto = str(valor).strip()
    if not texto:
        return ""

    for formato in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto[:19], formato).strftime("%d/%m/%Y")
        except Exception:
            pass

    return texto


def _d4sign_formatar_moeda_br_admin(valor) -> str:
    """_d4sign_formatar_moeda_br_admin: eu converto número para texto de moeda brasileira."""

    if valor in (None, ""):
        return ""

    try:
        numero = float(valor)
        texto = f"{numero:,.2f}"
        texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {texto}"
    except Exception:
        return str(valor).strip()


def _d4sign_formatar_valor_token_admin(valor, formato: str | None = None) -> str:
    """_d4sign_formatar_valor_token_admin: eu aplica formatação simples nos tokens do template."""

    formato_normalizado = _texto_ou_vazio(formato).strip().lower()

    if formato_normalizado in ("data", "date", "data_br", "dd/mm/yyyy"):
        return _d4sign_formatar_data_br_admin(valor)

    if formato_normalizado in ("moeda", "moeda_br", "money", "currency", "brl"):
        return _d4sign_formatar_moeda_br_admin(valor)

    if valor in (None, ""):
        return ""

    return str(valor).strip()


def _d4sign_obter_cofre_contratos_admin(id_dim_cofre_preferencial: int | None = None) -> dict:
    """_d4sign_obter_cofre_contratos_admin: eu busco o cofre Contratos cadastrado localmente."""

    id_cofre = _int_ou_none(id_dim_cofre_preferencial)

    row = db.session.execute(
        text("""
            SELECT TOP 1
                IDDimCofreD4,
                CAST(UUIDCofreD4 AS varchar(36)) AS UUIDCofreD4,
                NomeCofreD4
            FROM [Integracao].[Silver].[DimCofreD4]
            WHERE BitAtivo = 1
              AND (
                    (:id_cofre IS NOT NULL AND IDDimCofreD4 = :id_cofre)
                    OR (:id_cofre IS NULL AND NomeCofreD4 = :nome_cofre)
                  )
            ORDER BY
                CASE WHEN :id_cofre IS NOT NULL AND IDDimCofreD4 = :id_cofre THEN 0 ELSE 1 END,
                IDDimCofreD4 ASC
        """),
        {
            "id_cofre": int(id_cofre) if id_cofre not in (None, "", 0) else None,
            "nome_cofre": NOME_COFRE_D4_CONTRATOS_ADMIN,
        },
    ).mappings().first()

    if not row:
        raise RuntimeError(
            "Nenhum cofre D4Sign ativo encontrado em [Integracao].[Silver].[DimCofreD4]. "
            "Cadastre o cofre 'Contratos' antes de aprovar contratos com integração D4Sign."
        )

    return dict(row)


def _d4sign_resolver_modelo_contrato_admin(
    *,
    id_dim_cofre_d4: int | None,
    tipo_solicitacao: str | None,
    tipo_documento: str | None,
) -> dict:
    """_d4sign_resolver_modelo_contrato_admin: eu escolho o modelo ativo da sua DimModeloContratoD4."""

    tipo_solicitacao_norm = _texto_ou_vazio(tipo_solicitacao).upper().replace("_", " ").strip()
    tipo_documento_norm = _texto_ou_vazio(tipo_documento).upper().replace("_", " ").strip()

    sql = text("""
        SELECT TOP 1
            m.IDDimModeloContratoD4,
            m.IDDimCofreD4,
            LTRIM(RTRIM(CONVERT(varchar(100), m.IDTemplateD4))) AS IDTemplateD4,
            m.NomeModeloContratoD4,
            m.TipoModeloD4,
            m.JsonVariaveis,
            c.NomeCofreD4,
            CAST(c.UUIDCofreD4 AS varchar(36)) AS UUIDCofreD4
        FROM [Integracao].[Silver].[DimModeloContratoD4] AS m
        INNER JOIN [Integracao].[Silver].[DimCofreD4] AS c
            ON c.IDDimCofreD4 = m.IDDimCofreD4
        WHERE ISNULL(m.BitAtivo, 1) = 1
          AND ISNULL(c.BitAtivo, 1) = 1
          AND (
                (:id_cofre IS NULL AND UPPER(LTRIM(RTRIM(c.NomeCofreD4))) = UPPER(LTRIM(RTRIM(:nome_cofre))))
                OR (:id_cofre IS NOT NULL AND m.IDDimCofreD4 = :id_cofre)
              )
        ORDER BY
            CASE
                WHEN UPPER(REPLACE(LTRIM(RTRIM(ISNULL(m.TipoModeloD4, ''))), '_', ' ')) COLLATE Latin1_General_CI_AI = :tipo_solicitacao THEN 0
                WHEN UPPER(REPLACE(LTRIM(RTRIM(ISNULL(m.TipoModeloD4, ''))), '_', ' ')) COLLATE Latin1_General_CI_AI = :tipo_documento THEN 1
                WHEN UPPER(REPLACE(LTRIM(RTRIM(ISNULL(m.TipoModeloD4, ''))), '_', ' ')) COLLATE Latin1_General_CI_AI IN ('CONTRATO', 'CONTRATOS', 'PADRAO', 'PADRÃO', 'PADRAO CONTRATO', 'PADRÃO CONTRATO') THEN 2
                WHEN LTRIM(RTRIM(ISNULL(m.TipoModeloD4, ''))) = '' THEN 3
                ELSE 4
            END,
            m.IDDimModeloContratoD4 DESC
    """)

    row = db.session.execute(
        sql,
        {
            "id_cofre": int(id_dim_cofre_d4) if id_dim_cofre_d4 not in (None, "", 0) else None,
            "nome_cofre": NOME_COFRE_D4_CONTRATOS_ADMIN,
            "tipo_solicitacao": tipo_solicitacao_norm,
            "tipo_documento": tipo_documento_norm,
        },
    ).mappings().first()

    if not row:
        raise RuntimeError(
            "Nenhum modelo D4Sign ativo encontrado em [Integracao].[Silver].[DimModeloContratoD4] "
            "para o cofre Contratos."
        )

    modelo = dict(row)
    if not _texto_ou_vazio(modelo.get("IDTemplateD4")):
        raise RuntimeError(
            f"Modelo D4Sign {modelo.get('IDDimModeloContratoD4')} está sem IDTemplateD4."
        )

    return modelo



_D4SIGN_CACHE_COLUNAS_TABELA_ADMIN: dict[tuple[str, str, str], set[str]] = {}


def _d4sign_obter_colunas_tabela_admin(
    banco: str,
    esquema: str,
    tabela: str,
) -> set[str]:
    """_d4sign_obter_colunas_tabela_admin: eu leio o schema real da tabela para evitar SELECT em coluna inexistente."""

    banco_limpo = str(banco or "").strip()
    esquema_limpo = str(esquema or "").strip()
    tabela_limpa = str(tabela or "").strip()

    if not banco_limpo or not esquema_limpo or not tabela_limpa:
        return set()

    if not re.match(r"^[A-Za-z0-9_]+$", banco_limpo):
        raise RuntimeError(f"Nome de banco inválido para leitura de colunas: {banco_limpo}")

    chave_cache = (banco_limpo.lower(), esquema_limpo.lower(), tabela_limpa.lower())
    if chave_cache in _D4SIGN_CACHE_COLUNAS_TABELA_ADMIN:
        return _D4SIGN_CACHE_COLUNAS_TABELA_ADMIN[chave_cache]

    try:
        rows = db.session.execute(
            text(f"""
                SELECT c.name AS NomeColuna
                FROM [{banco_limpo}].sys.columns AS c
                INNER JOIN [{banco_limpo}].sys.tables AS t
                    ON t.object_id = c.object_id
                INNER JOIN [{banco_limpo}].sys.schemas AS s
                    ON s.schema_id = t.schema_id
                WHERE s.name = :esquema
                  AND t.name = :tabela
            """),
            {
                "esquema": esquema_limpo,
                "tabela": tabela_limpa,
            },
        ).mappings().all()

        colunas = {str(r.get("NomeColuna") or "").strip().lower() for r in rows if r.get("NomeColuna")}
        _D4SIGN_CACHE_COLUNAS_TABELA_ADMIN[chave_cache] = colunas
        return colunas

    except Exception as exc:
        current_app.logger.warning(
            "D4SIGN | não consegui ler colunas de %s.%s.%s; usando fallback seguro | erro=%s",
            banco_limpo,
            esquema_limpo,
            tabela_limpa,
            exc,
        )
        _D4SIGN_CACHE_COLUNAS_TABELA_ADMIN[chave_cache] = set()
        return set()


def _d4sign_tem_coluna_admin(colunas: set[str], nome_coluna: str) -> bool:
    """_d4sign_tem_coluna_admin: eu comparo coluna de forma case-insensitive."""

    return str(nome_coluna or "").strip().lower() in (colunas or set())


def _d4sign_coluna_existente_admin(colunas: set[str], candidatos: list[str] | tuple[str, ...]) -> str | None:
    """_d4sign_coluna_existente_admin: eu escolho a primeira coluna existente entre vários nomes possíveis."""

    colunas_normalizadas = colunas or set()
    for candidato in candidatos or []:
        nome = str(candidato or "").strip()
        if nome and nome.lower() in colunas_normalizadas:
            return nome
    return None


def _d4sign_expr_coluna_ou_null_admin(
    alias_tabela: str,
    alias_saida: str,
    colunas: set[str],
    candidatos: list[str] | tuple[str, ...],
    tipo_null: str = "nvarchar(4000)",
) -> str:
    """_d4sign_expr_coluna_ou_null_admin: eu monto SELECT seguro; se a coluna não existir, retorno NULL com o mesmo alias."""

    coluna = _d4sign_coluna_existente_admin(colunas, candidatos)
    if coluna:
        return f"{alias_tabela}.[{coluna}] AS [{alias_saida}]"
    return f"CAST(NULL AS {tipo_null}) AS [{alias_saida}]"


def _d4sign_carregar_dados_contrato_admin(id_fato_controle_contratos: int) -> dict:
    """_d4sign_carregar_dados_contrato_admin: eu busco contrato, empresa e itens ativos para preencher o template."""

    colunas_empresa = _d4sign_obter_colunas_tabela_admin("Integracao", "Silver", "DimEmpresas")

    campos_empresa_sql = ",\n                ".join(
        [
            _d4sign_expr_coluna_ou_null_admin("emp", "RazaoSocialEmpresa", colunas_empresa, ("RazaoSocial",)),
            _d4sign_expr_coluna_ou_null_admin("emp", "NomeFantasiaEmpresa", colunas_empresa, ("NomeFantasia",)),
            _d4sign_expr_coluna_ou_null_admin("emp", "CNPJEmpresa", colunas_empresa, ("CNPJ", "Cnpj", "Documento", "DocumentoFederal")),
            _d4sign_expr_coluna_ou_null_admin("emp", "EmailEmpresa", colunas_empresa, ("Email", "EmailEmpresa", "E-mail")),
            _d4sign_expr_coluna_ou_null_admin(
                "emp",
                "TelefoneEmpresa",
                colunas_empresa,
                ("Telefones", "Telefone", "TelefoneEmpresa", "Telefone1", "Fone", "Celular", "Whatsapp", "WhatsApp"),
            ),
            _d4sign_expr_coluna_ou_null_admin("emp", "MunicipioEmpresa", colunas_empresa, ("Municipio", "Município", "Cidade", "CidadeEmpresa")),
            _d4sign_expr_coluna_ou_null_admin("emp", "UFEmpresa", colunas_empresa, ("UF", "Uf", "Estado")),
            _d4sign_expr_coluna_ou_null_admin("emp", "CEPEmpresa", colunas_empresa, ("CEP", "Cep", "CodigoPostal")),
            _d4sign_expr_coluna_ou_null_admin("emp", "LogradouroEmpresa", colunas_empresa, ("Logradouro", "Endereco", "Endereço")),
            _d4sign_expr_coluna_ou_null_admin("emp", "NumeroEmpresa", colunas_empresa, ("Numero", "Número", "NumeroEndereco", "NumeroLogradouro")),
            _d4sign_expr_coluna_ou_null_admin("emp", "BairroEmpresa", colunas_empresa, ("Bairro",)),
            _d4sign_expr_coluna_ou_null_admin("emp", "ComplementoEmpresa", colunas_empresa, ("Complemento",)),
        ]
    )

    cab = db.session.execute(
        text(f"""
            SELECT TOP 1
                ctr.*,
                {campos_empresa_sql}
            FROM [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
            LEFT JOIN [Integracao].[Silver].[DimEmpresas] AS emp
                ON emp.IDEmpresa = ctr.IDEmpresa
            WHERE ctr.IDFatoControleContratosEuromidia = :id_contrato
        """),
        {"id_contrato": int(id_fato_controle_contratos)},
    ).mappings().first()

    if not cab:
        raise RuntimeError(
            f"Contrato {id_fato_controle_contratos} não encontrado em "
            "[Integracao].[Silver].[FatoControleContratosEuromidia]."
        )

    itens = db.session.execute(
        text("""
            SELECT *
            FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia]
            WHERE IDFatoControleContratoEuromidia = :id_contrato
              AND ISNULL(BitAtivo, 1) = 1
            ORDER BY IDFatoControleContratosItensEuromidia ASC
        """),
        {"id_contrato": int(id_fato_controle_contratos)},
    ).mappings().all()

    dados = dict(cab)
    itens_dict = [dict(item) for item in itens]
    primeiro_item = itens_dict[0] if itens_dict else {}

    def _soma(campo: str):
        total = 0.0
        encontrou = False
        for item in itens_dict:
            valor = item.get(campo)
            if valor in (None, ""):
                continue
            try:
                total += float(valor)
                encontrou = True
            except Exception:
                pass
        return total if encontrou else None

    def _min_data(campo: str):
        valores = [item.get(campo) for item in itens_dict if item.get(campo) not in (None, "")]
        return min(valores) if valores else None

    def _max_data(campo: str):
        valores = [item.get(campo) for item in itens_dict if item.get(campo) not in (None, "")]
        return max(valores) if valores else None

    dados.update(
        {
            "Resumo_QuantidadeItens": len(itens_dict),
            "Resumo_DataInicioContrato": _min_data("DataInicioPrevisto"),
            "Resumo_DataTerminoContrato": _max_data("DataTerminoPrevisto"),
            "Resumo_SomaFaturamentoBrutoMensal": _soma("FaturamentoBrutoMensal"),
            "Resumo_SomaFaturamentoLiquidoMensal": _soma("FaturamentoLiquidoMensal"),
            "Resumo_SomaTotalBrutoContrato": _soma("TotalBrutoContrato"),
            "PrimeiroItem_CodPonto": primeiro_item.get("CodPonto"),
            "PrimeiroItem_CodFace": primeiro_item.get("CodFace"),
            "PrimeiroItem_CidadeExibicao": primeiro_item.get("CidadeExibicao"),
            "PrimeiroItem_Tipo": primeiro_item.get("Tipo"),
            "PrimeiroItem_DataInicioPrevisto": primeiro_item.get("DataInicioPrevisto"),
            "PrimeiroItem_DataTerminoPrevisto": primeiro_item.get("DataTerminoPrevisto"),
            "PrimeiroItem_FaturamentoBrutoMensal": primeiro_item.get("FaturamentoBrutoMensal"),
            "PrimeiroItem_FaturamentoLiquidoMensal": primeiro_item.get("FaturamentoLiquidoMensal"),
        }
    )

    return dados


def _d4sign_montar_endereco_empresa_admin(dados: dict) -> str:
    """_d4sign_montar_endereco_empresa_admin: eu junto os campos de endereço da empresa."""

    partes = [
        dados.get("LogradouroEmpresa"),
        dados.get("NumeroEmpresa"),
        dados.get("ComplementoEmpresa"),
        dados.get("BairroEmpresa"),
        dados.get("MunicipioEmpresa"),
        dados.get("UFEmpresa"),
        dados.get("CEPEmpresa"),
    ]

    return " ".join(str(p).strip() for p in partes if str(p or "").strip())


def _d4sign_montar_nome_documento_admin(
    dados_contrato: dict,
    *,
    tipo_solicitacao: str | None = None,
    id_fato_kanban_card: int | None = None,
    id_dim_tipo_documento: int | None = None,
) -> str:
    """_d4sign_montar_nome_documento_admin: eu gero o nome oficial do contrato no D4Sign.

    Padrão operacional:
    - Contrato-IDFatoControleContratosEuromidia-CNPJ-RazaoSocial-Dia-Mes-Ano
    - Contrato-Renovacao-IDFatoControleContratosEuromidia-CNPJ-RazaoSocial-Dia-Mes-Ano
    - Contrato-Aditivo-IDFatoControleContratosEuromidia-CNPJ-RazaoSocial-Dia-Mes-Ano
    """

    id_contrato = _int_ou_none(dados_contrato.get("IDFatoControleContratosEuromidia"))
    if id_contrato in (None, "", 0):
        id_contrato = _int_ou_none(dados_contrato.get("IDFatoControleContratoEuromidia"))

    cnpj = _d4sign_obter_cnpj_ou_identificador_empresa_admin(dados_contrato)
    cnpj_limpo = re.sub(r"\D+", "", str(cnpj or "")) or "sem-cnpj"

    razao_social = (
        _texto_ou_vazio(dados_contrato.get("RazaoSocialEmpresa"))
        or _texto_ou_vazio(dados_contrato.get("RazaoSocial"))
        or _texto_ou_vazio(dados_contrato.get("NomeFantasiaEmpresa"))
        or "Empresa"
    )
    razao_social = " ".join(razao_social.split())
    razao_social = _d4sign_limpar_nome_pasta_arquivo_admin(razao_social)[:120] or "Empresa"

    textos_tipo = [
        tipo_solicitacao,
        dados_contrato.get("TipoSolicitacao"),
        dados_contrato.get("TipoOperacional"),
        dados_contrato.get("TipoDocumento"),
        dados_contrato.get("NomeTipoDocumento"),
    ]
    textos_tipo_normalizados = [
        _texto_ou_vazio(valor).upper().replace("_", " ")
        for valor in textos_tipo
        if _texto_ou_vazio(valor)
    ]

    inicio_renovacao = _texto_ou_vazio(dados_contrato.get("InicioRenovacao")).upper().strip()

    eh_renovacao = False
    if inicio_renovacao == "R":
        eh_renovacao = True
    elif any("RENOVA" in valor for valor in textos_tipo_normalizados):
        eh_renovacao = True
    elif id_fato_kanban_card not in (None, "", 0):
        try:
            eh_renovacao = _card_eh_renovacao_admin(int(id_fato_kanban_card))
        except Exception as exc:
            current_app.logger.warning(
                "D4SIGN | não consegui validar tag de renovação para nome do documento | id_card=%s | erro=%s",
                id_fato_kanban_card,
                exc,
            )

    eh_aditivo = False
    if not eh_renovacao:
        if any("ADITIV" in valor for valor in textos_tipo_normalizados):
            eh_aditivo = True
        elif id_fato_kanban_card not in (None, "", 0):
            try:
                eh_aditivo = _card_possui_tag_ativa_admin(
                    int(id_fato_kanban_card),
                    int(ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN),
                )
            except Exception as exc:
                current_app.logger.warning(
                    "D4SIGN | não consegui validar tag de aditivo para nome do documento | id_card=%s | erro=%s",
                    id_fato_kanban_card,
                    exc,
                )

    partes_nome = ["Contrato"]
    if eh_renovacao:
        partes_nome.append("Renovacao")
    elif eh_aditivo:
        partes_nome.append("Aditivo")

    partes_nome.extend(
        [
            str(id_contrato or "sem-id"),
            cnpj_limpo,
            razao_social,
            datetime.now().strftime("%d-%m-%Y"),
        ]
    )

    nome_documento = "-".join(partes_nome)
    return _d4sign_limpar_nome_pasta_arquivo_admin(nome_documento)


def _d4sign_montar_tokens_padrao_admin(dados: dict, tipo_solicitacao: str | None = None) -> dict:
    """_d4sign_montar_tokens_padrao_admin: eu monto os tokens padrão enviados ao template Word."""

    total_bruto = (
        dados.get("TotalBrutoContrato")
        or dados.get("Resumo_SomaTotalBrutoContrato")
        or dados.get("TotalContrato")
    )

    total_liquido_mensal = (
        dados.get("TotalFaturamentoLiquidoMensal")
        or dados.get("Resumo_SomaFaturamentoLiquidoMensal")
        or dados.get("FaturamentoLiquidoMensal")
    )

    cnpj_empresa = (
        dados.get("CNPJEmpresa")
        or dados.get("CnpjEmpresa")
        or dados.get("CNPJ")
        or dados.get("Cnpj")
        or ""
    )
    cnpj_empresa_limpo = re.sub(r"\D+", "", str(cnpj_empresa or ""))

    return {
        "ID_CONTRATO": _d4sign_formatar_valor_token_admin(dados.get("IDFatoControleContratosEuromidia")),
        "NUMERO_CONTRATO": _d4sign_formatar_valor_token_admin(dados.get("NumeroContrato")),
        "NUMERO_PREVIA": _d4sign_formatar_valor_token_admin(dados.get("NumeroPrevia")),
        "REFERENCIA": _d4sign_formatar_valor_token_admin(dados.get("Referencia")),
        "TIPO_SOLICITACAO": _d4sign_formatar_valor_token_admin(tipo_solicitacao),
        "TIPO_DOCUMENTO": _d4sign_formatar_valor_token_admin(dados.get("TipoDocumento")),
        "RAZAO_SOCIAL": _d4sign_formatar_valor_token_admin(dados.get("RazaoSocialEmpresa") or dados.get("RazaoSocial")),
        "NOME_FANTASIA": _d4sign_formatar_valor_token_admin(dados.get("NomeFantasiaEmpresa")),
        "CNPJ": _d4sign_formatar_valor_token_admin(cnpj_empresa),
        "CNPJ_LIMPO": _d4sign_formatar_valor_token_admin(cnpj_empresa_limpo),
        "CPF": _d4sign_formatar_valor_token_admin(dados.get("CPF")),
        "EMAIL_EMPRESA": _d4sign_formatar_valor_token_admin(dados.get("EmailEmpresa")),
        "TELEFONE_EMPRESA": _d4sign_formatar_valor_token_admin(dados.get("TelefoneEmpresa")),
        "ENDERECO_EMPRESA": _d4sign_montar_endereco_empresa_admin(dados),
        "CIDADE_EMPRESA": _d4sign_formatar_valor_token_admin(dados.get("MunicipioEmpresa")),
        "UF_EMPRESA": _d4sign_formatar_valor_token_admin(dados.get("UFEmpresa")),
        "CEP_EMPRESA": _d4sign_formatar_valor_token_admin(dados.get("CEPEmpresa")),
        "MARCA_EXIBIDA": _d4sign_formatar_valor_token_admin(dados.get("MarcaExibida")),
        "VENDEDOR": _d4sign_formatar_valor_token_admin(dados.get("Vendedor")),
        "ORIGEM": _d4sign_formatar_valor_token_admin(dados.get("Origem")),
        "AGENCIA": _d4sign_formatar_valor_token_admin(dados.get("Agencia")),
        "CNPJ_AGENCIA": _d4sign_formatar_valor_token_admin(dados.get("CnpjAgencia")),
        "BUREAU": _d4sign_formatar_valor_token_admin(dados.get("Bureau")),
        "CNPJ_BUREAU": _d4sign_formatar_valor_token_admin(dados.get("CnpjBureau")),
        "INTERMEDIARIO": _d4sign_formatar_valor_token_admin(dados.get("Intermediario")),
        "CNPJ_INTERMEDIARIO": _d4sign_formatar_valor_token_admin(dados.get("CnpjIntermediario")),
        "DATA_LANCAMENTO": _d4sign_formatar_data_br_admin(dados.get("DataLancamento")),
        "DATA_ASSINATURA": _d4sign_formatar_data_br_admin(dados.get("DataAssinaturaRenovacao")),
        "DATA_INICIO": _d4sign_formatar_data_br_admin(dados.get("Resumo_DataInicioContrato")),
        "DATA_TERMINO": _d4sign_formatar_data_br_admin(dados.get("Resumo_DataTerminoContrato")),
        "QUANTIDADE_ITENS": _d4sign_formatar_valor_token_admin(dados.get("Resumo_QuantidadeItens")),
        "QUANTIDADE_PONTOS": _d4sign_formatar_valor_token_admin(dados.get("QuantidadePontos")),
        "QUANTIDADE_FACES": _d4sign_formatar_valor_token_admin(dados.get("QuantidadeFaces")),
        "COD_PONTO": _d4sign_formatar_valor_token_admin(dados.get("PrimeiroItem_CodPonto")),
        "COD_FACE": _d4sign_formatar_valor_token_admin(dados.get("PrimeiroItem_CodFace")),
        "CIDADE_EXIBICAO": _d4sign_formatar_valor_token_admin(dados.get("PrimeiroItem_CidadeExibicao")),
        "TIPO_FACE": _d4sign_formatar_valor_token_admin(dados.get("PrimeiroItem_Tipo")),
        "TOTAL_BRUTO_CONTRATO": _d4sign_formatar_moeda_br_admin(total_bruto),
        "TOTAL_FATURAMENTO_LIQUIDO_MENSAL": _d4sign_formatar_moeda_br_admin(total_liquido_mensal),
        "TOTAL_LIQUIDO_AGENCIA_BUREAU_CARTA_ACORDO": _d4sign_formatar_moeda_br_admin(dados.get("TotalLiquidoContratoAGBRCTACORDO")),
        "TOTAL_LIQUIDO_AGENCIA_BUREAU_VENDEDOR_GERENCIA": _d4sign_formatar_moeda_br_admin(dados.get("TotalLiquidoContratoAGBRVENDGERCOOR")),
        "DATA_GERACAO_DOCUMENTO": datetime.now().strftime("%d/%m/%Y"),
    }


def _d4sign_parse_json_variaveis_admin(valor_json) -> dict | list | None:
    """_d4sign_parse_json_variaveis_admin: eu leio o JsonVariaveis da DimModeloContratoD4."""

    texto = _texto_ou_vazio(valor_json)
    if not texto:
        return None

    try:
        dados = json.loads(texto)
    except Exception as exc:
        raise RuntimeError(f"JsonVariaveis inválido na DimModeloContratoD4: {exc}") from exc

    if isinstance(dados, dict):
        for chave in ("variaveis", "variables", "tokens", "campos"):
            if isinstance(dados.get(chave), (dict, list)):
                return dados.get(chave)

    return dados if isinstance(dados, (dict, list)) else None


def _d4sign_resolver_valor_json_variavel_admin(nome_variavel: str, regra, dados: dict, tokens_padrao: dict) -> str:
    """_d4sign_resolver_valor_json_variavel_admin: eu resolvo uma variável configurada no JsonVariaveis."""

    if isinstance(regra, dict):
        if "valor_fixo" in regra:
            return _d4sign_formatar_valor_token_admin(regra.get("valor_fixo"), regra.get("formato"))

        if "fixo" in regra:
            return _d4sign_formatar_valor_token_admin(regra.get("fixo"), regra.get("formato"))

        if "valor" in regra and not any(k in regra for k in ("campo", "coluna", "token", "origem")):
            return _d4sign_formatar_valor_token_admin(regra.get("valor"), regra.get("formato"))

        origem = (
            regra.get("campo")
            or regra.get("coluna")
            or regra.get("token")
            or regra.get("origem")
            or regra.get("valor")
            or nome_variavel
        )

        if origem in tokens_padrao:
            return _d4sign_formatar_valor_token_admin(tokens_padrao.get(origem), regra.get("formato"))

        return _d4sign_formatar_valor_token_admin(dados.get(str(origem)), regra.get("formato"))

    if isinstance(regra, str):
        origem = regra.strip()

        if origem in tokens_padrao:
            return _d4sign_formatar_valor_token_admin(tokens_padrao.get(origem))

        if origem in dados:
            return _d4sign_formatar_valor_token_admin(dados.get(origem))

        return _d4sign_formatar_valor_token_admin(origem)

    if regra in (None, ""):
        return _d4sign_formatar_valor_token_admin(tokens_padrao.get(nome_variavel) or dados.get(nome_variavel))

    return _d4sign_formatar_valor_token_admin(regra)


def _d4sign_montar_tokens_template_admin(modelo: dict, dados_contrato: dict, tipo_solicitacao: str | None) -> dict:
    """_d4sign_montar_tokens_template_admin: eu monto tokens padrão e aplico JsonVariaveis quando configurado."""

    tokens_padrao = _d4sign_montar_tokens_padrao_admin(dados_contrato, tipo_solicitacao=tipo_solicitacao)
    configuracao = _d4sign_parse_json_variaveis_admin(modelo.get("JsonVariaveis"))

    if not configuracao:
        return tokens_padrao

    if isinstance(configuracao, list):
        tokens = {}
        for item in configuracao:
            nome_variavel = str(item or "").strip()
            if nome_variavel:
                tokens[nome_variavel] = _d4sign_formatar_valor_token_admin(
                    tokens_padrao.get(nome_variavel) or dados_contrato.get(nome_variavel)
                )
        return tokens or tokens_padrao

    if isinstance(configuracao, dict):
        tokens = {}
        for nome_variavel, regra in configuracao.items():
            nome = str(nome_variavel or "").strip()
            if not nome:
                continue
            tokens[nome] = _d4sign_resolver_valor_json_variavel_admin(
                nome,
                regra,
                dados_contrato,
                tokens_padrao,
            )
        return tokens or tokens_padrao

    return tokens_padrao




def _d4sign_extrair_lista_objetos_admin(resposta) -> list[dict]:
    """_d4sign_extrair_lista_objetos_admin: eu transformo respostas variadas da D4Sign em lista."""

    if resposta is None:
        return []

    if isinstance(resposta, list):
        return [item for item in resposta if isinstance(item, dict)]

    if isinstance(resposta, dict):
        for chave in (
            "data",
            "safes",
            "folders",
            "documents",
            "docs",
            "result",
            "results",
            "resposta",
            "list",
        ):
            valor = resposta.get(chave)

            if isinstance(valor, list):
                return [item for item in valor if isinstance(item, dict)]

        if resposta and all(isinstance(valor, dict) for valor in resposta.values()):
            return [valor for valor in resposta.values() if isinstance(valor, dict)]

        return [resposta]

    return []


def _d4sign_normalizar_texto_admin(valor) -> str:
    """_d4sign_normalizar_texto_admin: eu padronizo texto para comparação segura."""

    return str(valor or "").strip().casefold()


def _d4sign_limpar_nome_pasta_arquivo_admin(valor) -> str:
    """_d4sign_limpar_nome_pasta_arquivo_admin: eu limpo caracteres inválidos de pasta/documento."""

    texto = str(valor or "").strip()
    texto = re.sub(r'[\\/:*?"<>|]+', "-", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    if not texto:
        return "sem_nome"

    return texto[:180]


def _d4sign_obter_nome_pasta_admin(pasta: dict) -> str:
    """_d4sign_obter_nome_pasta_admin: eu capturo o nome da pasta em formatos diferentes da D4Sign."""

    return str(
        pasta.get("folder_name")
        or pasta.get("name-folder")
        or pasta.get("name_folder")
        or pasta.get("folderName")
        or pasta.get("name")
        or pasta.get("nome")
        or ""
    ).strip()


def _d4sign_obter_uuid_pasta_admin(pasta: dict) -> str:
    """_d4sign_obter_uuid_pasta_admin: eu capturo o UUID da pasta em formatos diferentes da D4Sign."""

    return str(
        pasta.get("uuid_folder")
        or pasta.get("uuid-folder")
        or pasta.get("uuidFolder")
        or pasta.get("uuid_folder_current")
        or pasta.get("uuid")
        or pasta.get("id")
        or ""
    ).strip()


def _d4sign_obter_uuid_pasta_pai_admin(pasta: dict) -> str:
    """_d4sign_obter_uuid_pasta_pai_admin: eu capturo o UUID da pasta pai em formatos diferentes."""

    return str(
        pasta.get("uuid_folder_parent")
        or pasta.get("uuid-folder-parent")
        or pasta.get("uuidParent")
        or pasta.get("parent_uuid")
        or pasta.get("uuid_parent")
        or pasta.get("id_parent")
        or ""
    ).strip()


def _d4sign_listar_pastas_do_cofre_admin(uuid_cofre: str) -> list[dict]:
    """_d4sign_listar_pastas_do_cofre_admin: eu listo as pastas do cofre na D4Sign."""

    resposta = _d4sign_executar_get_admin(f"/folders/{uuid_cofre}/find")
    return _d4sign_extrair_lista_objetos_admin(resposta)


def _d4sign_buscar_pasta_por_nome_admin(
    *,
    uuid_cofre: str,
    nome_pasta: str,
    uuid_pasta_pai: str | None = None,
    permitir_candidata_sem_pai: bool = False,
) -> dict | None:
    """_d4sign_buscar_pasta_por_nome_admin: eu busco pasta pelo nome com cuidado de hierarquia.

    Observação importante:
    - o endpoint /folders/{UUID-SAFE}/find da D4Sign lista as pastas do cofre;
    - a documentação/resposta desse endpoint normalmente não informa claramente o pai da pasta;
    - por isso, quando uuid_pasta_pai vem preenchido, eu só reaproveito automaticamente se o pai bater;
    - se a API não informar o pai e permitir_candidata_sem_pai=True, eu posso reaproveitar a candidata como fallback.
    """

    pastas = _d4sign_listar_pastas_do_cofre_admin(uuid_cofre)
    nome_procurado = _d4sign_normalizar_texto_admin(nome_pasta)
    uuid_pai_procurado = _d4sign_normalizar_texto_admin(uuid_pasta_pai) if uuid_pasta_pai else ""

    candidatas: list[dict] = []
    for pasta in pastas:
        nome_encontrado = _d4sign_obter_nome_pasta_admin(pasta)
        if _d4sign_normalizar_texto_admin(nome_encontrado) == nome_procurado:
            candidatas.append(pasta)

    if not candidatas:
        return None

    if uuid_pai_procurado:
        candidatas_sem_pai: list[dict] = []

        for pasta in candidatas:
            uuid_pai_encontrado = _d4sign_normalizar_texto_admin(
                _d4sign_obter_uuid_pasta_pai_admin(pasta)
            )

            if uuid_pai_encontrado and uuid_pai_encontrado == uuid_pai_procurado:
                return pasta

            if not uuid_pai_encontrado:
                candidatas_sem_pai.append(pasta)

        current_app.logger.warning(
            "D4SIGN | pasta com mesmo nome encontrada, mas a API não confirmou o pai correto | "
            "nome=%s | uuid_pasta_pai_esperado=%s | permitir_candidata_sem_pai=%s | candidatas=%s",
            nome_pasta,
            uuid_pasta_pai,
            permitir_candidata_sem_pai,
            [
                {
                    "uuid": _d4sign_obter_uuid_pasta_admin(p),
                    "nome": _d4sign_obter_nome_pasta_admin(p),
                    "uuid_pai": _d4sign_obter_uuid_pasta_pai_admin(p),
                }
                for p in candidatas[:10]
            ],
        )

        if permitir_candidata_sem_pai and candidatas_sem_pai:
            return candidatas_sem_pai[0]

        return None

    candidatas_raiz = [p for p in candidatas if not _d4sign_obter_uuid_pasta_pai_admin(p)]
    if candidatas_raiz:
        return candidatas_raiz[0]

    return candidatas[0]


def _d4sign_criar_pasta_admin(
    *,
    uuid_cofre: str,
    nome_pasta: str,
    uuid_pasta_pai: str | None = None,
) -> dict:
    """_d4sign_criar_pasta_admin: eu crio pasta ou subpasta dentro do cofre."""

    payload = {
        "folder_name": nome_pasta,
    }

    if uuid_pasta_pai:
        payload["uuid_folder"] = uuid_pasta_pai

    resposta = _d4sign_executar_post_admin(
        caminho=f"/folders/{uuid_cofre}/create",
        payload=payload,
    )

    return resposta if isinstance(resposta, dict) else {"resposta": resposta}


def _d4sign_erro_nome_pasta_ja_existe_admin(erro: Exception) -> bool:
    """_d4sign_erro_nome_pasta_ja_existe_admin: eu identifico erro de nome de pasta duplicado."""

    texto_erro = str(erro or "").casefold()
    return (
        "already taken" in texto_erro
        or "name" in texto_erro and "taken" in texto_erro
        or "nome" in texto_erro and "existe" in texto_erro
        or "já existe" in texto_erro
        or "ja existe" in texto_erro
    )


def _d4sign_obter_ou_criar_pasta_admin(
    *,
    uuid_cofre: str,
    nome_pasta: str,
    uuid_pasta_pai: str | None = None,
    nome_pasta_alternativo_se_duplicar: str | None = None,
) -> dict:
    """_d4sign_obter_ou_criar_pasta_admin: eu reaproveito ou crio pasta sem jogar documento no cliente errado.

    O ponto crítico é a pasta mensal, por exemplo "05-2026".
    A API da D4Sign aceita uuid_folder para criar subpasta, mas a listagem /find geralmente
    não devolve o pai da pasta. Se a D4Sign recusar a criação dizendo que o nome já existe,
    eu NÃO reaproveito cegamente uma pasta global de mesmo nome, porque isso pode jogar o
    contrato dentro de outro cliente.

    Quando houver nome_pasta_alternativo_se_duplicar, eu crio uma pasta alternativa e única
    dentro do pai correto. Isso é mais seguro do que usar uma pasta mensal de outro cliente.
    """

    pasta_existente = _d4sign_buscar_pasta_por_nome_admin(
        uuid_cofre=uuid_cofre,
        nome_pasta=nome_pasta,
        uuid_pasta_pai=uuid_pasta_pai,
        permitir_candidata_sem_pai=False,
    )

    if pasta_existente:
        current_app.logger.info(
            "D4SIGN | pasta reaproveitada | nome=%s | uuid=%s | uuid_pasta_pai=%s",
            nome_pasta,
            _d4sign_obter_uuid_pasta_admin(pasta_existente),
            uuid_pasta_pai,
        )
        return pasta_existente

    current_app.logger.info(
        "D4SIGN | criando pasta | nome=%s | uuid_pasta_pai=%s",
        nome_pasta,
        uuid_pasta_pai,
    )

    try:
        resposta_criacao = _d4sign_criar_pasta_admin(
            uuid_cofre=uuid_cofre,
            nome_pasta=nome_pasta,
            uuid_pasta_pai=uuid_pasta_pai,
        )
    except Exception as exc:
        if not _d4sign_erro_nome_pasta_ja_existe_admin(exc):
            raise

        if not nome_pasta_alternativo_se_duplicar:
            pasta_sem_pai = _d4sign_buscar_pasta_por_nome_admin(
                uuid_cofre=uuid_cofre,
                nome_pasta=nome_pasta,
                uuid_pasta_pai=uuid_pasta_pai,
                permitir_candidata_sem_pai=True,
            )
            if pasta_sem_pai:
                current_app.logger.warning(
                    "D4SIGN | reaproveitando pasta duplicada porque a API não informou o pai | "
                    "nome=%s | uuid=%s | uuid_pasta_pai_esperado=%s",
                    nome_pasta,
                    _d4sign_obter_uuid_pasta_admin(pasta_sem_pai),
                    uuid_pasta_pai,
                )
                return pasta_sem_pai
            raise

        nome_alternativo = _d4sign_limpar_nome_pasta_arquivo_admin(nome_pasta_alternativo_se_duplicar)

        current_app.logger.warning(
            "D4SIGN | nome de pasta já existe no cofre e a API não informou pai suficiente; "
            "vou criar pasta alternativa dentro do pai correto | nome_original=%s | nome_alternativo=%s | uuid_pasta_pai=%s | erro=%s",
            nome_pasta,
            nome_alternativo,
            uuid_pasta_pai,
            exc,
        )

        pasta_alternativa_existente = _d4sign_buscar_pasta_por_nome_admin(
            uuid_cofre=uuid_cofre,
            nome_pasta=nome_alternativo,
            uuid_pasta_pai=uuid_pasta_pai,
            permitir_candidata_sem_pai=True,
        )
        if pasta_alternativa_existente:
            return pasta_alternativa_existente

        resposta_criacao = _d4sign_criar_pasta_admin(
            uuid_cofre=uuid_cofre,
            nome_pasta=nome_alternativo,
            uuid_pasta_pai=uuid_pasta_pai,
        )

    uuid_pasta_criada = _d4sign_obter_uuid_pasta_admin(resposta_criacao)
    if uuid_pasta_criada:
        return resposta_criacao

    pasta_criada = _d4sign_buscar_pasta_por_nome_admin(
        uuid_cofre=uuid_cofre,
        nome_pasta=nome_pasta,
        uuid_pasta_pai=uuid_pasta_pai,
        permitir_candidata_sem_pai=True,
    )

    if not pasta_criada:
        raise RuntimeError(
            f"Solicitei a criação da pasta D4Sign '{nome_pasta}', "
            f"mas não consegui encontrá-la depois. Resposta={resposta_criacao}"
        )

    return pasta_criada


def _d4sign_obter_cnpj_ou_identificador_empresa_admin(dados_contrato: dict) -> str:
    """_d4sign_obter_cnpj_ou_identificador_empresa_admin: eu defino a pasta da empresa.

    Eu priorizo o CNPJ carregado da DimEmpresas como CNPJEmpresa, porque ele representa
    a empresa do contrato. Só uso CNPJ genérico como fallback para não quebrar legado.
    """

    cnpj = (
        dados_contrato.get("CNPJEmpresa")
        or dados_contrato.get("CnpjEmpresa")
        or dados_contrato.get("CNPJ")
        or dados_contrato.get("Cnpj")
        or ""
    )

    cnpj_limpo = re.sub(r"\D+", "", str(cnpj or ""))
    if cnpj_limpo:
        return cnpj_limpo

    id_empresa = dados_contrato.get("IDEmpresa")
    if id_empresa not in (None, "", 0):
        return f"empresa-{id_empresa}"

    razao_social = (
        dados_contrato.get("RazaoSocialEmpresa")
        or dados_contrato.get("RazaoSocial")
        or dados_contrato.get("NomeFantasiaEmpresa")
        or "empresa-sem-cnpj"
    )

    return _d4sign_limpar_nome_pasta_arquivo_admin(razao_social)


def _d4sign_resolver_pasta_destino_contrato_admin(
    *,
    uuid_cofre: str,
    dados_contrato: dict,
) -> dict:
    """_d4sign_resolver_pasta_destino_contrato_admin: eu salvo direto na pasta Euromidia.

    Regra nova:
    - mantenho somente a pasta raiz configurada em D4SIGN_NOME_PASTA_RAIZ_CONTRATOS;
    - por padrão essa pasta é "Euromidia";
    - não crio mais subpasta por CNPJ;
    - não crio mais subpasta por mês/ano.
    """

    nome_pasta_raiz = _d4sign_limpar_nome_pasta_arquivo_admin(NOME_PASTA_RAIZ_D4_CONTRATOS_ADMIN)

    pasta_raiz = _d4sign_obter_ou_criar_pasta_admin(
        uuid_cofre=uuid_cofre,
        nome_pasta=nome_pasta_raiz,
    )

    uuid_pasta_raiz = _d4sign_obter_uuid_pasta_admin(pasta_raiz)
    if not uuid_pasta_raiz:
        raise RuntimeError(f"Não consegui obter UUID da pasta raiz D4Sign: {pasta_raiz}")

    nome_pasta_raiz_real = _d4sign_obter_nome_pasta_admin(pasta_raiz) or nome_pasta_raiz

    return {
        "nome_pasta_raiz": nome_pasta_raiz_real,
        "uuid_pasta_raiz": uuid_pasta_raiz,
        "nome_pasta_destino": nome_pasta_raiz_real,
        "uuid_pasta_destino": uuid_pasta_raiz,

        # Compatibilidade com retornos/logs antigos: agora o destino final é a própria Euromidia.
        "nome_pasta_empresa": None,
        "uuid_pasta_empresa": None,
        "nome_pasta_mes_ano": nome_pasta_raiz_real,
        "nome_pasta_mes_ano_solicitada": None,
        "uuid_pasta_mes_ano": uuid_pasta_raiz,
    }




def _d4sign_documento_existente_ativo_admin(
    *,
    id_fato_controle_contratos: int,
    id_fato_kanban_card: int | None,
    id_dim_tipo_documento: int | None,
) -> dict | None:
    """_d4sign_documento_existente_ativo_admin: eu evito criar documento duplicado para o mesmo contrato/card."""

    colunas = _d4sign_obter_colunas_tabela_admin("Integracao", "Silver", "FatoContratoD4")

    if not _d4sign_tem_coluna_admin(colunas, "IDFatoContratoD4"):
        return None

    if not _d4sign_tem_coluna_admin(colunas, "IDFatoControleContratosEuromidia"):
        return None

    if not _d4sign_tem_coluna_admin(colunas, "UUIDDocumentoD4"):
        return None

    campos_select = [
        "IDFatoContratoD4",
        "CAST(UUIDDocumentoD4 AS varchar(36)) AS UUIDDocumentoD4",
    ]

    if _d4sign_tem_coluna_admin(colunas, "NomeDocumentoD4"):
        campos_select.append("NomeDocumentoD4")
    else:
        campos_select.append("CAST(NULL AS nvarchar(255)) AS NomeDocumentoD4")

    if _d4sign_tem_coluna_admin(colunas, "IDDimStatusD4"):
        campos_select.append("IDDimStatusD4")
    else:
        campos_select.append("CAST(NULL AS int) AS IDDimStatusD4")

    if _d4sign_tem_coluna_admin(colunas, "NomeFaseD4"):
        campos_select.append("NomeFaseD4")
    else:
        campos_select.append("CAST(NULL AS nvarchar(90)) AS NomeFaseD4")

    filtros = ["IDFatoControleContratosEuromidia = :id_contrato"]
    params = {
        "id_contrato": int(id_fato_controle_contratos),
        "id_card": int(id_fato_kanban_card) if id_fato_kanban_card not in (None, "", 0) else None,
        "id_tipo_documento": int(id_dim_tipo_documento) if id_dim_tipo_documento not in (None, "", 0) else None,
    }

    if _d4sign_tem_coluna_admin(colunas, "IDFatoKanbanCard"):
        filtros.append("ISNULL(IDFatoKanbanCard, 0) = ISNULL(:id_card, 0)")

    if _d4sign_tem_coluna_admin(colunas, "IDDimTipoDocumento"):
        filtros.append("(:id_tipo_documento IS NULL OR IDDimTipoDocumento = :id_tipo_documento OR IDDimTipoDocumento IS NULL)")

    if _d4sign_tem_coluna_admin(colunas, "BitAtivo"):
        filtros.append("ISNULL(BitAtivo, 1) = 1")

    if _d4sign_tem_coluna_admin(colunas, "IDDimStatusD4"):
        filtros.append("ISNULL(IDDimStatusD4, 0) <> 6")

    sql = f"""
        SELECT TOP 1
            {", ".join(campos_select)}
        FROM [Integracao].[Silver].[FatoContratoD4]
        WHERE {" AND ".join(filtros)}
        ORDER BY IDFatoContratoD4 DESC
    """

    row = db.session.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _d4sign_criar_documento_template_word_admin(
    *,
    uuid_cofre: str,
    nome_documento: str,
    id_template_d4: str,
    tokens: dict,
    uuid_pasta_destino: str | None = None,
) -> dict:
    """_d4sign_criar_documento_template_word_admin: eu crio documento no cofre usando template Word."""

    id_template_limpo = str(id_template_d4 or "").strip()
    if not id_template_limpo:
        raise RuntimeError("IDTemplateD4 veio vazio. Confira o cadastro em DimModeloContratoD4.")

    payload = {
        "name_document": str(nome_documento or "Contrato").strip()[:255],
        "templates": {
            id_template_limpo: tokens or {}
        },
    }

    if uuid_pasta_destino:
        payload["uuid_folder"] = str(uuid_pasta_destino).strip()

    current_app.logger.info(
        "D4SIGN | criando documento por template Word | cofre=%s | pasta=%s | template=%s | nome=%s",
        uuid_cofre,
        uuid_pasta_destino,
        id_template_limpo,
        payload["name_document"],
    )

    return _d4sign_executar_post_admin(
        caminho=f"/documents/{uuid_cofre}/makedocumentbytemplateword",
        payload=payload,
    )



def _d4sign_limpar_tag_documento_admin(tag: str | None) -> str:
    """_d4sign_limpar_tag_documento_admin: eu limpo espaços da TAG sem remover acentos nem palavras."""

    return re.sub(r"\s+", " ", str(tag or "")).strip()


def _d4sign_resolver_tags_documento_contrato_admin(
    dados_contrato: dict,
    *,
    tipo_solicitacao: str | None = None,
    id_fato_kanban_card: int | None = None,
    id_dim_tipo_documento: int | None = None,
) -> list[str]:
    """_d4sign_resolver_tags_documento_contrato_admin
    - Eu defino as TAGs oficiais do documento no D4Sign.
    - Todo documento recebe a TAG base "Contratos".
    - Novo contrato recebe "Novo Contrato".
    - Renovação recebe "Renovação".
    - Aditivo recebe "Aditivo de Contrato".
    """

    textos_tipo = [
        tipo_solicitacao,
        dados_contrato.get("TipoSolicitacao"),
        dados_contrato.get("TipoOperacional"),
        dados_contrato.get("TipoDocumento"),
        dados_contrato.get("NomeTipoDocumento"),
        id_dim_tipo_documento,
    ]
    textos_tipo_normalizados = [
        _texto_ou_vazio(valor).upper().replace("_", " ")
        for valor in textos_tipo
        if _texto_ou_vazio(valor)
    ]

    inicio_renovacao = _texto_ou_vazio(dados_contrato.get("InicioRenovacao")).upper().strip()

    eh_renovacao = False
    if inicio_renovacao == "R":
        eh_renovacao = True
    elif any("RENOVA" in valor for valor in textos_tipo_normalizados):
        eh_renovacao = True
    elif id_fato_kanban_card not in (None, "", 0):
        try:
            eh_renovacao = _card_eh_renovacao_admin(int(id_fato_kanban_card))
        except Exception as exc:
            current_app.logger.warning(
                "D4SIGN_TAG | não consegui validar tag de renovação do card | id_card=%s | erro=%s",
                id_fato_kanban_card,
                exc,
            )

    eh_aditivo = False
    if not eh_renovacao:
        if any("ADITIV" in valor for valor in textos_tipo_normalizados):
            eh_aditivo = True
        elif id_fato_kanban_card not in (None, "", 0):
            try:
                eh_aditivo = _card_possui_tag_ativa_admin(
                    int(id_fato_kanban_card),
                    int(ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN),
                )
            except Exception as exc:
                current_app.logger.warning(
                    "D4SIGN_TAG | não consegui validar tag de aditivo do card | id_card=%s | erro=%s",
                    id_fato_kanban_card,
                    exc,
                )

    tags = ["Contratos"]
    if eh_renovacao:
        tags.append("Renovação")
    elif eh_aditivo:
        tags.append("Aditivo de Contrato")
    else:
        tags.append("Novo Contrato")

    tags_limpas = []
    for tag in tags:
        tag_limpa = _d4sign_limpar_tag_documento_admin(tag)
        if tag_limpa and tag_limpa not in tags_limpas:
            tags_limpas.append(tag_limpa)

    return tags_limpas


def _d4sign_normalizar_tag_para_comparacao_admin(tag: str | None) -> str:
    """_d4sign_normalizar_tag_para_comparacao_admin: eu normalizo TAG para evitar cadastro duplicado por diferença de caixa/espaço."""

    return _d4sign_limpar_tag_documento_admin(tag).casefold()


def _d4sign_extrair_tags_resposta_admin(resposta) -> set[str]:
    """_d4sign_extrair_tags_resposta_admin: eu tento extrair nomes de TAGs mesmo se a D4Sign mudar o formato da resposta."""

    tags: set[str] = set()
    chaves_possiveis = {"tag", "tags", "name", "nome", "label", "descricao", "description"}

    def caminhar(objeto):
        if isinstance(objeto, dict):
            for chave, valor in objeto.items():
                chave_normalizada = str(chave or "").strip().lower()

                if chave_normalizada in chaves_possiveis:
                    if isinstance(valor, str):
                        tag_limpa = _d4sign_limpar_tag_documento_admin(valor)
                        if tag_limpa:
                            tags.add(tag_limpa)
                    elif isinstance(valor, list):
                        for item in valor:
                            if isinstance(item, str):
                                tag_limpa = _d4sign_limpar_tag_documento_admin(item)
                                if tag_limpa:
                                    tags.add(tag_limpa)
                            else:
                                caminhar(item)
                    elif isinstance(valor, dict):
                        caminhar(valor)
                elif isinstance(valor, (dict, list)):
                    caminhar(valor)

        elif isinstance(objeto, list):
            for item in objeto:
                caminhar(item)

    caminhar(resposta)
    return tags


def _d4sign_listar_tags_documento_admin(uuid_documento: str) -> set[str]:
    """_d4sign_listar_tags_documento_admin: eu consulto as TAGs atuais do documento no D4Sign."""

    uuid_documento_limpo = str(uuid_documento or "").strip()
    if not uuid_documento_limpo:
        return set()

    resposta = _d4sign_executar_get_admin(f"/tags/{uuid_documento_limpo}")
    return _d4sign_extrair_tags_resposta_admin(resposta)


def _d4sign_adicionar_tag_documento_admin(*, uuid_documento: str, tag: str) -> dict:
    """_d4sign_adicionar_tag_documento_admin: eu chamo POST /tags/{UUID-DOCUMENTO}/add com o campo obrigatório tag."""

    uuid_documento_limpo = str(uuid_documento or "").strip()
    tag_limpa = _d4sign_limpar_tag_documento_admin(tag)

    if not uuid_documento_limpo:
        raise RuntimeError("Não adicionei TAG D4Sign porque UUID do documento veio vazio.")

    if not tag_limpa:
        raise RuntimeError("Não adicionei TAG D4Sign porque o nome da TAG veio vazio.")

    return _d4sign_executar_post_admin(
        caminho=f"/tags/{uuid_documento_limpo}/add",
        payload={"tag": tag_limpa},
    )


def _d4sign_adicionar_tags_documento_contrato_admin(
    *,
    uuid_documento: str,
    dados_contrato: dict,
    tipo_solicitacao: str | None = None,
    id_fato_kanban_card: int | None = None,
    id_dim_tipo_documento: int | None = None,
) -> dict:
    """_d4sign_adicionar_tags_documento_contrato_admin
    - Eu aplico as TAGs oficiais do contrato no documento D4Sign.
    - Eu tento listar as TAGs existentes antes para evitar duplicidade.
    - Se a listagem falhar, eu sigo tentando adicionar as TAGs, porque o objetivo principal é classificar o documento.
    """

    tags_planejadas = _d4sign_resolver_tags_documento_contrato_admin(
        dados_contrato,
        tipo_solicitacao=tipo_solicitacao,
        id_fato_kanban_card=id_fato_kanban_card,
        id_dim_tipo_documento=id_dim_tipo_documento,
    )

    tags_existentes: set[str] = set()
    try:
        tags_existentes = _d4sign_listar_tags_documento_admin(uuid_documento)
    except Exception as exc:
        current_app.logger.warning(
            "D4SIGN_TAG | não consegui listar TAGs atuais; vou tentar adicionar mesmo assim | uuid=%s | erro=%s",
            uuid_documento,
            exc,
        )

    tags_existentes_normalizadas = {
        _d4sign_normalizar_tag_para_comparacao_admin(tag)
        for tag in tags_existentes
    }

    resultados = []
    ok_geral = True

    for tag in tags_planejadas:
        tag_normalizada = _d4sign_normalizar_tag_para_comparacao_admin(tag)

        if tag_normalizada in tags_existentes_normalizadas:
            resultados.append({
                "tag": tag,
                "status": "ja_existia",
            })
            continue

        try:
            resposta = _d4sign_adicionar_tag_documento_admin(
                uuid_documento=uuid_documento,
                tag=tag,
            )
            resultados.append({
                "tag": tag,
                "status": "adicionada",
                "resposta": resposta,
            })
            tags_existentes_normalizadas.add(tag_normalizada)
        except Exception as exc:
            ok_geral = False
            current_app.logger.exception(
                "D4SIGN_TAG | falha ao adicionar TAG no documento | uuid=%s | tag=%s",
                uuid_documento,
                tag,
            )
            resultados.append({
                "tag": tag,
                "status": "erro",
                "erro": str(exc),
            })

    return {
        "ok": ok_geral,
        "uuid_documento_d4": str(uuid_documento or "").strip(),
        "tags_planejadas": tags_planejadas,
        "tags_existentes": sorted(tags_existentes),
        "resultados": resultados,
    }



def _d4sign_buscar_detalhe_documento_admin(uuid_documento: str) -> dict:
    """_d4sign_buscar_detalhe_documento_admin: eu busco detalhes/status do documento criado."""

    resposta = _d4sign_executar_get_admin(f"/documents/{uuid_documento}")
    return _d4sign_primeiro_objeto_admin(resposta)


def _d4sign_normalizar_nome_status_admin(valor: str | None) -> str:
    """Normaliza o nome do status D4 para comparar com DimStatusD4."""

    texto = _texto_ou_vazio(valor).upper().strip()
    if not texto:
        return ""

    substituicoes = {
        "Á": "A", "À": "A", "Â": "A", "Ã": "A", "Ä": "A",
        "É": "E", "È": "E", "Ê": "E", "Ë": "E",
        "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
        "Ó": "O", "Ò": "O", "Ô": "O", "Õ": "O", "Ö": "O",
        "Ú": "U", "Ù": "U", "Û": "U", "Ü": "U",
        "Ç": "C",
    }
    for origem, destino in substituicoes.items():
        texto = texto.replace(origem, destino)

    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def _d4sign_id_status_por_nome_admin(nome_status: str | None) -> int | None:
    """Converte NomeStatus do D4 para o IDDimStatusD4 cadastrado localmente."""

    nome = _d4sign_normalizar_nome_status_admin(nome_status)
    if not nome:
        return None

    mapa = {
        "PROCESSANDO": 1,
        "AGUARDANDO SIGNATARIOS": 2,
        "AGUARDANDO SIGNATARIO": 2,
        "AGUARDANDO ASSINATURAS": 3,
        "AGUARDANDO ASSINATURA": 3,
        "FINALIZADO": 4,
        "ARQUIVADO": 5,
        "CANCELADO": 6,
        "EDITANDO": 7,
    }

    if nome in mapa:
        return mapa[nome]

    for chave, id_status in mapa.items():
        if chave in nome:
            return id_status

    return None


def _d4sign_extrair_id_status_d4_admin(detalhe: dict | None, fallback: int = ID_STATUS_D4_PROCESSANDO_ADMIN) -> int:
    """Extrai o IDDimStatusD4 de respostas variadas da API D4Sign."""

    if not isinstance(detalhe, dict):
        return int(fallback or ID_STATUS_D4_PROCESSANDO_ADMIN)

    valor_id = (
        detalhe.get("IDDimStatusD4")
        or detalhe.get("id_dim_status_d4")
        or detalhe.get("id_fase_d4")
        or detalhe.get("IDFaseD4")
        or detalhe.get("statusId")
        or detalhe.get("status_id")
        or detalhe.get("id_status")
        or detalhe.get("idStatus")
    )

    id_status = _d4sign_para_int_ou_none_admin(valor_id)
    if id_status in (1, 2, 3, 4, 5, 6, 7):
        return int(id_status)

    nome_status = (
        detalhe.get("NomeStatus")
        or detalhe.get("nome_status")
        or detalhe.get("nome_fase_d4")
        or detalhe.get("NomeFaseD4")
        or detalhe.get("statusName")
        or detalhe.get("status_name")
        or detalhe.get("status")
        or detalhe.get("phase")
        or detalhe.get("fase")
    )

    id_por_nome = _d4sign_id_status_por_nome_admin(nome_status)
    if id_por_nome in (1, 2, 3, 4, 5, 6, 7):
        return int(id_por_nome)

    return int(fallback or ID_STATUS_D4_PROCESSANDO_ADMIN)


def _d4sign_inserir_historico_contratos_d4_admin(
    *,
    id_fato_controle_contratos: int,
    id_dim_status_contratos: int | None,
    id_dim_status_d4: int | None = None,
) -> int:
    """Insere a linha inicial do histórico D4 do contrato aprovado."""

    id_contrato = _int_ou_none(id_fato_controle_contratos)
    if id_contrato in (None, "", 0):
        raise RuntimeError("Não inseri histórico D4 porque IDFatoControleContratosEuromidia veio vazio.")

    id_status_contrato = _int_ou_none(id_dim_status_contratos) or ID_STATUS_CONTRATO_APROVADO
    id_status_d4 = _int_ou_none(id_dim_status_d4) or ID_STATUS_D4_PROCESSANDO_ADMIN

    row = db.session.execute(
        text(f"""
            INSERT INTO {TABELA_HISTORICO_CONTRATOS_D4_ADMIN}
            (
                 IDEmpresaProprietaria
                ,IDFatoControleContratosEuromidia
                ,IDDimStatusContratos
                ,IDDimStatusD4
                ,DataStatus
            )
            OUTPUT INSERTED.IDDimHistoricoContratos AS IDDimHistoricoContratos
            VALUES
            (
                 :id_empresa_proprietaria
                ,:id_contrato
                ,:id_status_contrato
                ,:id_status_d4
                ,GETDATE()
            )
        """),
        {
            "id_empresa_proprietaria": int(ID_EMPRESA_PROPRIETARIA_HISTORICO_D4_ADMIN),
            "id_contrato": int(id_contrato),
            "id_status_contrato": int(id_status_contrato) if id_status_contrato not in (None, "", 0) else None,
            "id_status_d4": int(id_status_d4) if id_status_d4 not in (None, "", 0) else None,
        },
    ).mappings().first()

    if not row or row.get("IDDimHistoricoContratos") is None:
        raise RuntimeError("Histórico D4 inserido, mas não consegui recuperar IDDimHistoricoContratos.")

    return int(row["IDDimHistoricoContratos"])


def _d4sign_atualizar_status_historico_contratos_d4_admin(
    *,
    id_dim_historico_contratos: int,
    id_dim_status_d4: int,
) -> None:
    """Atualiza a linha recém-inserida com o status atual consultado na D4Sign."""

    id_historico = _int_ou_none(id_dim_historico_contratos)
    id_status = _int_ou_none(id_dim_status_d4)

    if id_historico in (None, "", 0) or id_status in (None, "", 0):
        return

    db.session.execute(
        text(f"""
            UPDATE {TABELA_HISTORICO_CONTRATOS_D4_ADMIN}
               SET IDDimStatusD4 = :id_status_d4,
                   DataStatus = GETDATE()
             WHERE IDDimHistoricoContratos = :id_historico
        """),
        {
            "id_status_d4": int(id_status),
            "id_historico": int(id_historico),
        },
    )


def _d4sign_registrar_historico_status_contrato_d4_admin(
    *,
    id_fato_controle_contratos: int | None,
    id_dim_status_contratos: int | None,
    resultado_d4sign: dict | None,
) -> dict:
    """Registra o histórico D4 depois que o documento foi enviado/criado na D4Sign."""

    id_contrato = _int_ou_none(id_fato_controle_contratos)
    if id_contrato in (None, "", 0):
        return {
            "ok": False,
            "status": "sem_contrato",
            "mensagem": "Histórico D4 não inserido porque o contrato veio vazio.",
        }

    resultado = resultado_d4sign if isinstance(resultado_d4sign, dict) else {}
    uuid_documento = _texto_ou_vazio(resultado.get("uuid_documento_d4")).strip()

    if not resultado.get("ok") or not uuid_documento:
        return {
            "ok": False,
            "status": "sem_documento_d4",
            "mensagem": "Histórico D4 não inserido porque o documento D4Sign ainda não foi criado/localizado.",
            "id_contrato": int(id_contrato),
        }

    id_status_inicial = _d4sign_extrair_id_status_d4_admin(
        resultado,
        fallback=ID_STATUS_D4_PROCESSANDO_ADMIN,
    )

    id_historico = _d4sign_inserir_historico_contratos_d4_admin(
        id_fato_controle_contratos=int(id_contrato),
        id_dim_status_contratos=id_dim_status_contratos or ID_STATUS_CONTRATO_APROVADO,
        id_dim_status_d4=id_status_inicial,
    )

    detalhe_d4 = None
    id_status_final = id_status_inicial

    try:
        detalhe_d4 = _d4sign_buscar_detalhe_documento_admin(uuid_documento)
        id_status_final = _d4sign_extrair_id_status_d4_admin(
            detalhe_d4,
            fallback=id_status_inicial,
        )

        _d4sign_atualizar_status_historico_contratos_d4_admin(
            id_dim_historico_contratos=int(id_historico),
            id_dim_status_d4=int(id_status_final),
        )

    except Exception as exc:
        current_app.logger.exception(
            "D4SIGN_HISTORICO | histórico inserido, mas falhou consulta/atualização do status D4 | id_contrato=%s | uuid=%s",
            id_contrato,
            uuid_documento,
        )
        return {
            "ok": False,
            "status": "historico_inserido_sem_atualizacao_d4",
            "id_historico_d4": int(id_historico),
            "id_contrato": int(id_contrato),
            "uuid_documento_d4": uuid_documento,
            "id_dim_status_d4": int(id_status_inicial),
            "erro": str(exc),
        }

    return {
        "ok": True,
        "status": "registrado",
        "id_historico_d4": int(id_historico),
        "id_contrato": int(id_contrato),
        "uuid_documento_d4": uuid_documento,
        "id_dim_status_d4": int(id_status_final),
        "detalhe_d4": detalhe_d4,
    }


def _d4sign_inserir_fato_contrato_admin(
    *,
    id_dim_status_d4: int,
    id_empresa: int | None,
    id_dim_cofre_d4: int | None,
    id_fato_controle_contratos: int,
    id_fato_kanban_card: int | None,
    id_dim_status_contratos: int | None,
    id_dim_modelo_contrato_d4: int | None,
    id_dim_tipo_documento: int | None,
    uuid_documento_d4: str,
    uuid_cofre_d4: str | None,
    nome_documento_d4: str,
    nome_cofre_d4: str | None,
    id_fase_d4: int | None,
    nome_fase_d4: str | None,
    tipo_arquivo_d4: str | None,
    quantidade_paginas: int | None,
    tamanho_arquivo_d4: int | None,
    status_comentario_d4: str | None,
    cancelado_por_d4: str | None,
) -> int:
    """_d4sign_inserir_fato_contrato_admin: eu registro localmente o documento criado na D4Sign."""

    colunas = _d4sign_obter_colunas_tabela_admin("Integracao", "Silver", "FatoContratoD4")

    if not _d4sign_tem_coluna_admin(colunas, "IDFatoContratoD4"):
        raise RuntimeError(
            "A tabela [Integracao].[Silver].[FatoContratoD4] não possui IDFatoContratoD4. "
            "Não consigo gravar o vínculo local do documento D4Sign."
        )

    valores_por_coluna = {
        "IDDimStatusD4": (":id_dim_status_d4", int(id_dim_status_d4)),
        "IDEmpresa": (":id_empresa", int(id_empresa) if id_empresa not in (None, "", 0) else None),
        "IDDimCofreD4": (":id_dim_cofre_d4", int(id_dim_cofre_d4) if id_dim_cofre_d4 not in (None, "", 0) else None),
        "IDFatoControleContratosEuromidia": (":id_contrato", int(id_fato_controle_contratos)),
        "IDFatoKanbanCard": (":id_card", int(id_fato_kanban_card) if id_fato_kanban_card not in (None, "", 0) else None),
        "IDDimStatusContratos": (":id_dim_status_contratos", int(id_dim_status_contratos) if id_dim_status_contratos not in (None, "", 0) else None),
        "IDDimModeloContratoD4": (":id_dim_modelo", int(id_dim_modelo_contrato_d4) if id_dim_modelo_contrato_d4 not in (None, "", 0) else None),
        "IDDimTipoDocumento": (":id_dim_tipo_documento", int(id_dim_tipo_documento) if id_dim_tipo_documento not in (None, "", 0) else None),
        "UUIDDocumentoD4": (":uuid_documento", uuid_documento_d4),
        "UUIDCofreD4": (":uuid_cofre", uuid_cofre_d4),
        "NomeDocumentoD4": (":nome_documento", nome_documento_d4),
        "NomeCofreD4": (":nome_cofre", nome_cofre_d4),
        "IDFaseD4": (":id_fase", int(id_fase_d4) if id_fase_d4 not in (None, "", 0) else None),
        "NomeFaseD4": (":nome_fase", nome_fase_d4),
        "TipoArquivoD4": (":tipo_arquivo", tipo_arquivo_d4),
        "QuantidadePaginas": (":paginas", int(quantidade_paginas) if quantidade_paginas not in (None, "", 0) else None),
        "TamanhoArquivoD4": (":tamanho", int(tamanho_arquivo_d4) if tamanho_arquivo_d4 not in (None, "", 0) else None),
        "StatusComentarioD4": (":status_comentario", status_comentario_d4),
        "CanceladoPorD4": (":cancelado_por", cancelado_por_d4),
        "DataCriacao": ("SYSDATETIME()", None),
        "DataAtualizacao": ("SYSDATETIME()", None),
        "BitAtivo": ("1", None),
    }

    nomes_colunas_insert = []
    valores_insert = []
    parametros = {}

    for nome_coluna, (expressao_valor, valor_parametro) in valores_por_coluna.items():
        if not _d4sign_tem_coluna_admin(colunas, nome_coluna):
            continue

        nomes_colunas_insert.append(f"[{nome_coluna}]")
        valores_insert.append(expressao_valor)

        if expressao_valor.startswith(":"):
            parametros[expressao_valor[1:]] = valor_parametro

    if not nomes_colunas_insert:
        raise RuntimeError(
            "A tabela [Integracao].[Silver].[FatoContratoD4] existe, mas nenhuma coluna esperada foi encontrada para INSERT."
        )

    sql = f"""
        INSERT INTO [Integracao].[Silver].[FatoContratoD4]
        (
            {", ".join(nomes_colunas_insert)}
        )
        OUTPUT INSERTED.IDFatoContratoD4 AS IDFatoContratoD4
        VALUES
        (
            {", ".join(valores_insert)}
        )
    """

    row = db.session.execute(text(sql), parametros).mappings().first()

    if not row or row.get("IDFatoContratoD4") is None:
        raise RuntimeError("Documento criado na D4Sign, mas não consegui gravar em FatoContratoD4.")

    return int(row["IDFatoContratoD4"])


def _d4sign_criar_contrato_por_aprovacao_admin(
    *,
    id_fato_controle_contratos: int | None,
    id_fato_kanban_card: int | None,
    id_empresa: int | None,
    id_dim_status_contratos: int | None,
    id_dim_tipo_documento: int | None,
    tipo_solicitacao: str | None,
) -> dict:
    """_d4sign_criar_contrato_por_aprovacao_admin: eu crio o documento D4Sign depois da aprovação."""

    if not _env_bool("D4SIGN_CRIAR_CONTRATO_NA_APROVACAO_HABILITADO", "1"):
        return {
            "ok": False,
            "status": "desabilitado",
            "mensagem": "Criação de contrato D4Sign na aprovação está desabilitada por env.",
        }

    id_contrato = _int_ou_none(id_fato_controle_contratos)
    if id_contrato in (None, "", 0):
        raise RuntimeError("Não criei D4Sign porque IDFatoControleContratosEuromidia veio vazio.")

    id_tipo_documento = _int_ou_none(id_dim_tipo_documento)

    existente = _d4sign_documento_existente_ativo_admin(
        id_fato_controle_contratos=int(id_contrato),
        id_fato_kanban_card=id_fato_kanban_card,
        id_dim_tipo_documento=id_tipo_documento,
    )

    if existente:
        resultado_tags_d4sign = None
        uuid_documento_existente = existente.get("UUIDDocumentoD4")

        if uuid_documento_existente:
            try:
                dados_contrato_existente = _d4sign_carregar_dados_contrato_admin(int(id_contrato))
                resultado_tags_d4sign = _d4sign_adicionar_tags_documento_contrato_admin(
                    uuid_documento=str(uuid_documento_existente),
                    dados_contrato=dados_contrato_existente,
                    tipo_solicitacao=tipo_solicitacao,
                    id_fato_kanban_card=id_fato_kanban_card,
                    id_dim_tipo_documento=id_tipo_documento,
                )
            except Exception as exc:
                current_app.logger.exception(
                    "D4SIGN_TAG | falha ao garantir TAGs em documento D4Sign já existente | uuid=%s | id_contrato=%s",
                    uuid_documento_existente,
                    id_contrato,
                )
                resultado_tags_d4sign = {
                    "ok": False,
                    "uuid_documento_d4": str(uuid_documento_existente),
                    "erro": str(exc),
                }

        return {
            "ok": True,
            "status": "ja_existia",
            "id_fato_contrato_d4": existente.get("IDFatoContratoD4"),
            "uuid_documento_d4": existente.get("UUIDDocumentoD4"),
            "nome_documento_d4": existente.get("NomeDocumentoD4"),
            "nome_fase_d4": existente.get("NomeFaseD4"),
            "tags_d4sign": resultado_tags_d4sign,
        }

    dados_contrato = _d4sign_carregar_dados_contrato_admin(int(id_contrato))
    tipo_documento = dados_contrato.get("TipoDocumento")

    modelo = _d4sign_resolver_modelo_contrato_admin(
        id_dim_cofre_d4=None,
        tipo_solicitacao=tipo_solicitacao,
        tipo_documento=tipo_documento,
    )

    cofre = _d4sign_obter_cofre_contratos_admin(modelo.get("IDDimCofreD4"))

    nome_documento = _d4sign_montar_nome_documento_admin(
        dados_contrato,
        tipo_solicitacao=tipo_solicitacao,
        id_fato_kanban_card=id_fato_kanban_card,
        id_dim_tipo_documento=id_tipo_documento,
    )
    tokens = _d4sign_montar_tokens_template_admin(
        modelo=modelo,
        dados_contrato=dados_contrato,
        tipo_solicitacao=tipo_solicitacao,
    )

    pasta_destino = _d4sign_resolver_pasta_destino_contrato_admin(
        uuid_cofre=str(cofre["UUIDCofreD4"]),
        dados_contrato=dados_contrato,
    )

    resposta_criacao = _d4sign_criar_documento_template_word_admin(
        uuid_cofre=str(cofre["UUIDCofreD4"]),
        nome_documento=nome_documento,
        id_template_d4=str(modelo["IDTemplateD4"]),
        tokens=tokens,
        uuid_pasta_destino=pasta_destino.get("uuid_pasta_destino") or pasta_destino.get("uuid_pasta_raiz"),
    )

    uuid_documento = _d4sign_obter_uuid_documento_admin(resposta_criacao)
    if not uuid_documento:
        raise RuntimeError(f"D4Sign respondeu sem UUID do documento criado: {resposta_criacao}")

    resultado_tags_d4sign = _d4sign_adicionar_tags_documento_contrato_admin(
        uuid_documento=uuid_documento,
        dados_contrato=dados_contrato,
        tipo_solicitacao=tipo_solicitacao,
        id_fato_kanban_card=id_fato_kanban_card,
        id_dim_tipo_documento=id_tipo_documento,
    )

    detalhe = _d4sign_primeiro_objeto_admin(resposta_criacao)
    try:
        detalhe_api = _d4sign_buscar_detalhe_documento_admin(uuid_documento)
        if detalhe_api:
            detalhe = detalhe_api
    except Exception as exc:
        current_app.logger.warning(
            "D4SIGN | documento criado, mas detalhe não foi carregado | uuid=%s | erro=%s",
            uuid_documento,
            exc,
        )

    id_fase_texto = str(
        detalhe.get("statusId")
        or detalhe.get("status_id")
        or detalhe.get("id_status")
        or detalhe.get("idStatus")
        or "1"
    ).strip()

    id_dim_status_d4 = _d4sign_para_int_ou_none_admin(id_fase_texto) or 1

    nome_fase = _texto_ou_vazio(
        detalhe.get("statusName")
        or detalhe.get("status_name")
        or detalhe.get("status")
        or MAPA_FASE_D4SIGN_ADMIN.get(str(id_dim_status_d4), "Processando")
    )

    id_fato_contrato_d4 = _d4sign_inserir_fato_contrato_admin(
        id_dim_status_d4=int(id_dim_status_d4),
        id_empresa=id_empresa or dados_contrato.get("IDEmpresa"),
        id_dim_cofre_d4=cofre.get("IDDimCofreD4"),
        id_fato_controle_contratos=int(id_contrato),
        id_fato_kanban_card=id_fato_kanban_card,
        id_dim_status_contratos=id_dim_status_contratos or dados_contrato.get("IDDimStatusContratos"),
        id_dim_modelo_contrato_d4=modelo.get("IDDimModeloContratoD4"),
        id_dim_tipo_documento=id_tipo_documento,
        uuid_documento_d4=uuid_documento,
        uuid_cofre_d4=detalhe.get("uuidSafe") or cofre.get("UUIDCofreD4"),
        nome_documento_d4=detalhe.get("nameDoc") or nome_documento,
        nome_cofre_d4=detalhe.get("safeName") or cofre.get("NomeCofreD4"),
        id_fase_d4=int(id_dim_status_d4),
        nome_fase_d4=nome_fase,
        tipo_arquivo_d4=detalhe.get("type"),
        quantidade_paginas=_d4sign_para_int_ou_none_admin(detalhe.get("pages")),
        tamanho_arquivo_d4=_d4sign_para_int_ou_none_admin(detalhe.get("size")),
        status_comentario_d4=detalhe.get("statusComment"),
        cancelado_por_d4=detalhe.get("whoCanceled"),
    )

    return {
        "ok": True,
        "status": "criado",
        "id_fato_contrato_d4": id_fato_contrato_d4,
        "uuid_documento_d4": uuid_documento,
        "nome_documento_d4": detalhe.get("nameDoc") or nome_documento,
        "id_fase_d4": int(id_dim_status_d4),
        "nome_fase_d4": nome_fase,
        "id_dim_modelo_contrato_d4": modelo.get("IDDimModeloContratoD4"),
        "id_dim_cofre_d4": cofre.get("IDDimCofreD4"),
        "pasta_d4sign": pasta_destino,
        "tags_d4sign": resultado_tags_d4sign,
    }

def _d4sign_criar_para_solicitacao_aprovada_ou_retorno_admin(
    *,
    id_solicitacao: int,
    id_usuario_logado: int | None = None,
    cabecalho_solicitacao: dict | None = None,
) -> dict:
    """_d4sign_criar_para_solicitacao_aprovada_ou_retorno_admin: eu crio D4 mesmo quando a solicitação já está aprovada.

    Motivo prático:
    - o POST da tela de aprovação usa Celery;
    - se a solicitação já ficou APROVADO antes da correção da D4Sign, o fluxo antigo retornava antes de tentar criar o documento;
    - esta função permite reaproveitar o contrato já aprovado e criar/identificar o documento na D4Sign sem reaprovar tudo.
    """

    id_solicitacao_int = int(id_solicitacao)
    cab = cabecalho_solicitacao or _obter_cabecalho_solicitacao_bruta(id_solicitacao_int) or {}

    id_fato_controle = _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
    id_card = _int_ou_none(cab.get("IDFatoKanbanCard"))
    id_empresa = _int_ou_none(cab.get("IDEmpresa"))
    tipo_solicitacao = _tipo_solicitacao_normalizado(cab.get("TipoSolicitacao"))

    if id_fato_controle in (None, "", 0):
        return {
            "ok": False,
            "status": "sem_contrato_controle",
            "erro": "A solicitação está aprovada, mas não possui IDFatoControleContratosEuromidia para criar o documento D4Sign.",
            "id_solicitacao": id_solicitacao_int,
        }

    try:
        id_dim_tipo_documento = _resolver_id_dim_tipo_documento_solicitacao_admin(
            id_solicitacao=id_solicitacao_int,
            cabecalho_solicitacao=cab,
            id_fato_kanban_card=id_card,
            id_fato_controle_contratos=id_fato_controle,
            ids_itens_controle=None,
            tipo_solicitacao=tipo_solicitacao,
        )

        resultado_d4sign = _d4sign_criar_contrato_por_aprovacao_admin(
            id_fato_controle_contratos=id_fato_controle,
            id_fato_kanban_card=id_card,
            id_empresa=id_empresa,
            id_dim_status_contratos=cab.get("IDDimStatusContratos") or ID_STATUS_CONTRATO_APROVADO,
            id_dim_tipo_documento=id_dim_tipo_documento,
            tipo_solicitacao=tipo_solicitacao,
        )

        db.session.commit()

        current_app.logger.info(
            "D4SIGN | criação/identificação executada para solicitação já aprovada | "
            "id_solicitacao=%s | id_contrato=%s | id_card=%s | usuario=%s | resultado=%s",
            id_solicitacao_int,
            id_fato_controle,
            id_card,
            id_usuario_logado,
            resultado_d4sign,
        )

        return resultado_d4sign

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "D4SIGN | falha ao criar contrato D4Sign para solicitação já aprovada | "
            "id_solicitacao=%s | id_contrato=%s | id_card=%s",
            id_solicitacao_int,
            id_fato_controle,
            id_card,
        )
        return {
            "ok": False,
            "status": "erro",
            "erro": str(exc),
            "id_solicitacao": id_solicitacao_int,
            "id_contrato": int(id_fato_controle) if id_fato_controle else None,
            "id_card": int(id_card) if id_card else None,
        }

def _processar_aprovacao_contrato_admin(
    *,
    id_solicitacao: int,
    id_usuario_logado: int | None,
    form=None,
    enfileirar_airflow: bool = True,
) -> dict:
    """
    Eu executo a aprovação completa do contrato fora da requisição HTTP.

    Motivo:
    - a aprovação cria/atualiza contrato, itens, ocupação, preço praticado,
      vencimento de campanha, vínculos, contatos, destinatários, card e histórico;
    - rodar tudo isso dentro do POST da tela pode estourar timeout no Nginx/Gunicorn;
    - esta função foi separada para ser chamada pelo Celery.
    """

    id_solicitacao_int = int(id_solicitacao)
    id_usuario_int = int(id_usuario_logado) if id_usuario_logado not in (None, "", 0) else None

    cab_inicial = _obter_cabecalho_solicitacao_bruta(id_solicitacao_int)
    if not cab_inicial:
        raise ValueError("Não encontrei a solicitação para aprovação.")

    status_atual = _texto_ou_vazio(cab_inicial.get("StatusSolicitacao")).upper().strip()
    if status_atual == "APROVADO":
        resultado_d4sign = _d4sign_criar_para_solicitacao_aprovada_ou_retorno_admin(
            id_solicitacao=id_solicitacao_int,
            id_usuario_logado=id_usuario_int,
            cabecalho_solicitacao=cab_inicial,
        )

        return {
            "ok": True,
            "status": "ja_aprovado",
            "id_solicitacao": id_solicitacao_int,
            "id_contrato": _int_ou_none(cab_inicial.get("IDFatoControleContratosEuromidia")),
            "id_card": _int_ou_none(cab_inicial.get("IDFatoKanbanCard")),
            "resultado_d4sign": resultado_d4sign,
        }

    id_card = _int_ou_none(cab_inicial.get("IDFatoKanbanCard"))
    id_empresa = _int_ou_none(cab_inicial.get("IDEmpresa"))
    id_empresa_proprietaria = _int_ou_none(cab_inicial.get("IDEmpresaProprietaria"))
    tipo_solicitacao = _tipo_solicitacao_normalizado(cab_inicial.get("TipoSolicitacao"))

    current_app.logger.info(
        "APROVACAO_CONTRATO | inicio processamento assíncrono | id_solicitacao=%s | id_card=%s | usuario=%s",
        id_solicitacao_int,
        id_card,
        id_usuario_int,
    )

    resultado_aprovacao = _mover_solicitacao_aprovada_para_controle(
        id_solicitacao=id_solicitacao_int,
        id_usuario_logado=id_usuario_int,
    )

    id_fato_controle = _int_ou_none(resultado_aprovacao.get("id_contrato_controle"))
    ids_itens_controle = resultado_aprovacao.get("ids_itens_controle") or []
    id_card = _int_ou_none(resultado_aprovacao.get("id_card")) or id_card
    id_empresa = _int_ou_none(resultado_aprovacao.get("id_empresa")) or id_empresa
    id_empresa_proprietaria = _int_ou_none(resultado_aprovacao.get("id_empresa_proprietaria")) or id_empresa_proprietaria
    tipo_solicitacao = resultado_aprovacao.get("tipo_solicitacao") or tipo_solicitacao

    cab_aprovada = _obter_cabecalho_solicitacao_bruta(id_solicitacao_int) or {}
    id_fato_controle = _int_ou_none(id_fato_controle) or _int_ou_none(cab_aprovada.get("IDFatoControleContratosEuromidia"))
    id_card = _int_ou_none(id_card) or _int_ou_none(cab_aprovada.get("IDFatoKanbanCard"))
    id_empresa = _int_ou_none(id_empresa) or _int_ou_none(cab_aprovada.get("IDEmpresa"))
    id_empresa_proprietaria = _int_ou_none(id_empresa_proprietaria) or _int_ou_none(cab_aprovada.get("IDEmpresaProprietaria"))
    tipo_solicitacao = _tipo_solicitacao_normalizado(cab_aprovada.get("TipoSolicitacao")) or tipo_solicitacao

    _sincronizar_contato_contrato_se_fase_4(
        id_fato_kanban_card=id_card,
        id_empresa=id_empresa,
        id_empresa_proprietaria=id_empresa_proprietaria,
        id_fato_controle_contratos=id_fato_controle,
    )

    _upsert_contato_cliente_direto_euromidia(
        id_fato_controle_contratos=id_fato_controle,
        id_fato_kanban_card=id_card,
        form=form,
        cabecalho_solicitacao=cab_aprovada,
    )

    resultado_emails_contrato = _sincronizar_dim_email_contrato_por_formulario_admin(
        id_fato_controle_contratos=id_fato_controle,
        id_fato_kanban_card=id_card,
        form=form,
    )

    _upsert_destinatarios_externos_contrato(
        id_fato_controle_contratos=id_fato_controle,
        id_empresa_destinatario=id_empresa,
        id_empresa=id_empresa,
        ids_itens_controle=ids_itens_controle,
    )

    _aplicar_resultado_aprovacao_no_card(
        id_fato_kanban_card=id_card,
        id_usuario_logado=id_usuario_int,
        id_empresa_proprietaria=id_empresa_proprietaria,
        aprovar=True,
    )

    id_dim_tipo_documento = _resolver_id_dim_tipo_documento_solicitacao_admin(
        id_solicitacao=id_solicitacao_int,
        cabecalho_solicitacao=cab_aprovada,
        id_fato_kanban_card=id_card,
        id_fato_controle_contratos=id_fato_controle,
        ids_itens_controle=ids_itens_controle,
        tipo_solicitacao=tipo_solicitacao,
    )

    _atualizar_id_tipo_documento_card_admin(
        id_fato_kanban_card=id_card,
        id_dim_tipo_documento=id_dim_tipo_documento,
    )

    if _card_admin_esta_na_fase_formulario_contrato(id_card):
        _registrar_ocorrencia_card_tipo_documento_admin(
            id_fato_kanban_card=id_card,
            id_dim_tipo_documento=id_dim_tipo_documento,
            id_usuario_logado=id_usuario_int,
            id_empresa_proprietaria=id_empresa_proprietaria,
            id_fato_solicitacao=id_solicitacao_int,
            id_fato_controle_contratos=id_fato_controle,
            tipo_ocorrencia="APROVADO",
            observacao="Card aprovado na fase 4 pela tela admin/aprovacao/contratos.",
        )

    _registrar_historico_contrato_euromidia(
        id_fato_controle_contratos=id_fato_controle,
        id_fato_solicitacao=id_solicitacao_int,
        id_dim_acao=_obter_id_dim_acao_solicitacao_contrato("APROVADO", fallback=1),
        id_empresa=id_empresa,
        id_empresa_proprietaria=id_empresa_proprietaria,
        id_fato_kanban_card=id_card,
        tipo_evento="APROVADO",
        tipo_solicitacao=tipo_solicitacao,
        descricao_evento="Solicitação aprovada e movida para Controle de Contratos Euromídia.",
        id_dim_usuario_acao=id_usuario_int,
    )

    resultado_agendamentos_face = {}
    if form is not None:
        resultado_agendamentos_face = _sincronizar_agendamentos_face_contrato_por_formulario(
            id_solicitacao=int(id_solicitacao_int),
            form=form,
            id_usuario_logado=id_usuario_int,
            id_contrato_controle_resolvido=id_fato_controle,
        )

    resultado_agendamentos_pendentes = _migrar_agendamentos_face_pendentes_solicitacao_para_contrato(
        id_solicitacao=int(id_solicitacao_int),
        id_contrato_controle=id_fato_controle,
    )

    resultado_bit_fracionado = _atualizar_bit_fracionado_itens_contrato_por_agendamentos(
        id_contrato_controle=id_fato_controle,
    )

    db.session.commit()

    resultado_d4sign = {}

    try:
        resultado_d4sign = _d4sign_criar_contrato_por_aprovacao_admin(
            id_fato_controle_contratos=id_fato_controle,
            id_fato_kanban_card=id_card,
            id_empresa=id_empresa,
            id_dim_status_contratos=cab_aprovada.get("IDDimStatusContratos") or ID_STATUS_CONTRATO_APROVADO,
            id_dim_tipo_documento=id_dim_tipo_documento,
            tipo_solicitacao=tipo_solicitacao,
        )

        db.session.commit()

        current_app.logger.info(
            "D4SIGN | contrato criado/identificado após aprovação | id_contrato=%s | id_solicitacao=%s | id_card=%s | resultado=%s",
            id_fato_controle,
            id_solicitacao_int,
            id_card,
            resultado_d4sign,
        )

    except Exception as exc:
        db.session.rollback()

        resultado_d4sign = {
            "ok": False,
            "status": "erro",
            "erro": str(exc),
        }

        current_app.logger.exception(
            "D4SIGN | falha ao criar contrato D4Sign após aprovação | id_contrato=%s | id_solicitacao=%s | id_card=%s",
            id_fato_controle,
            id_solicitacao_int,
            id_card,
        )

    resultado_historico_d4 = {}
    if resultado_d4sign.get("ok"):
        try:
            resultado_historico_d4 = _d4sign_registrar_historico_status_contrato_d4_admin(
                id_fato_controle_contratos=id_fato_controle,
                id_dim_status_contratos=cab_aprovada.get("IDDimStatusContratos") or ID_STATUS_CONTRATO_APROVADO,
                resultado_d4sign=resultado_d4sign,
            )
            db.session.commit()

            current_app.logger.info(
                "D4SIGN_HISTORICO | histórico registrado após aprovação | id_contrato=%s | id_solicitacao=%s | resultado=%s",
                id_fato_controle,
                id_solicitacao_int,
                resultado_historico_d4,
            )

        except Exception as exc:
            db.session.rollback()
            resultado_historico_d4 = {
                "ok": False,
                "status": "erro",
                "erro": str(exc),
            }
            current_app.logger.exception(
                "D4SIGN_HISTORICO | contrato aprovado/D4 criado, mas falhou ao registrar histórico | id_contrato=%s | id_solicitacao=%s",
                id_fato_controle,
                id_solicitacao_int,
            )
    else:
        resultado_historico_d4 = {
            "ok": False,
            "status": "d4sign_nao_criado",
            "mensagem": "Histórico D4 não inserido porque o documento D4Sign não foi criado/localizado.",
        }

    resultado_anexos_contrato = {}
    try:
        resultado_anexos_contrato = _sincronizar_anexos_contrato_apos_aprovacao_admin(
            id_solicitacao=id_solicitacao_int,
            id_fato_controle_contratos=id_fato_controle,
            id_fato_kanban_card=id_card,
            tipo_solicitacao=tipo_solicitacao,
            id_fato_contrato_d4=resultado_d4sign.get("id_fato_contrato_d4") or resultado_d4sign.get("id_fato_contrato_d4sign") or resultado_d4sign.get("id_fato_contrato"),
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        resultado_anexos_contrato = {
            "ok": False,
            "status": "erro",
            "erro": str(exc),
        }
        current_app.logger.exception(
            "ANEXOS_CONTRATO | contrato aprovado, mas falhou ao sincronizar anexos | id_contrato=%s | id_solicitacao=%s | id_card=%s",
            id_fato_controle,
            id_solicitacao_int,
            id_card,
        )

    task_airflow_id = None
    if enfileirar_airflow:
        try:
            from app.tasks.airflow_admin_tasks import tarefa_disparar_airflow_aprovacao_contrato

            tarefa = tarefa_disparar_airflow_aprovacao_contrato.apply_async(
                kwargs={
                    "id_contrato": int(id_fato_controle) if id_fato_controle else None,
                    "id_solicitacao": int(id_solicitacao_int),
                    "id_card": int(id_card) if id_card else None,
                    "id_usuario_logado": int(id_usuario_int) if id_usuario_int else None,
                },
                queue=os.getenv("CELERY_QUEUE_AIRFLOW_ADMIN", "airflow_admin"),
            )
            task_airflow_id = getattr(tarefa, "id", None)

            current_app.logger.info(
                "APROVACAO_CONTRATO | task Celery enfileirada para disparar Airflow | "
                "task_id=%s | id_contrato=%s | id_solicitacao=%s | id_card=%s",
                task_airflow_id,
                id_fato_controle,
                id_solicitacao_int,
                id_card,
            )

        except Exception:
            current_app.logger.exception(
                "APROVACAO_CONTRATO | contrato aprovado, mas falhou ao enfileirar task Celery do Airflow | "
                "id_contrato=%s | id_solicitacao=%s | id_card=%s",
                id_fato_controle,
                id_solicitacao_int,
                id_card,
            )

    current_app.logger.info(
        "APROVACAO_CONTRATO | processamento assíncrono concluído | id_solicitacao=%s | id_contrato=%s | id_card=%s",
        id_solicitacao_int,
        id_fato_controle,
        id_card,
    )

    return {
        "ok": True,
        "status": "aprovado",
        "id_solicitacao": id_solicitacao_int,
        "id_contrato": int(id_fato_controle) if id_fato_controle else None,
        "id_card": int(id_card) if id_card else None,
        "ids_itens_controle": ids_itens_controle,
        "task_airflow_id": task_airflow_id,
        "resultado_d4sign": resultado_d4sign,
        "resultado_historico_d4": resultado_historico_d4,
        "resultado_aprovacao": resultado_aprovacao,
        "precos_praticados": resultado_aprovacao.get("precos_praticados") or [],
        "empresas_relacionadas_sincronizadas": resultado_aprovacao.get("empresas_relacionadas_sincronizadas") or [],
        "resultado_agendamentos_face": resultado_agendamentos_face,
        "resultado_agendamentos_pendentes": resultado_agendamentos_pendentes,
        "resultado_bit_fracionado": resultado_bit_fracionado,
        "resultado_emails_contrato": resultado_emails_contrato,
        "resultado_anexos_contrato": resultado_anexos_contrato,
    }



@admin.route("/aprovacao/contratos/<int:id_solicitacao>/anexos", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("120 per minute", methods=["GET"])
def listar_anexos_contrato(id_solicitacao: int):
    cab = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao))
    if not cab:
        return jsonify({"ok": False, "mensagem": "Solicitação não encontrada."}), 404

    anexos = _buscar_anexos_contrato_admin(
        id_fato_controle_contratos=cab.get("IDFatoControleContratosEuromidia"),
        id_fato_kanban_card=cab.get("IDFatoKanbanCard"),
    )

    return jsonify({
        "ok": True,
        "anexos": [_anexos_contrato_linha_para_json_admin(anexo) for anexo in anexos],
    })


@admin.route("/aprovacao/contratos/<int:id_solicitacao>/anexos/upload", methods=["POST"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("30 per minute", methods=["POST"])
def upload_anexos_contrato(id_solicitacao: int):
    cab = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao))
    if not cab:
        return jsonify({"ok": False, "mensagem": "Solicitação não encontrada."}), 404

    arquivos_recebidos = request.files.getlist("arquivos")
    if not arquivos_recebidos:
        return jsonify({"ok": False, "mensagem": "Nenhum arquivo foi enviado."}), 400

    id_contrato = _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
    id_card = _int_ou_none(cab.get("IDFatoKanbanCard"))
    tipo_solicitacao = _tipo_solicitacao_normalizado(cab.get("TipoSolicitacao"))

    if id_contrato in (None, "", 0) and id_card in (None, "", 0):
        return jsonify({"ok": False, "mensagem": "Não existe contrato nem card para vincular o anexo."}), 400

    pasta_temp = _anexos_contrato_pasta_temp_admin()
    pasta_temp.mkdir(parents=True, exist_ok=True)

    arquivos_para_processar = []
    avisos = []

    for arquivo in arquivos_recebidos:
        nome_original = _texto_ou_vazio(getattr(arquivo, "filename", ""))
        if not nome_original:
            continue

        try:
            extensao = _anexos_contrato_validar_extensao_admin(nome_original)
        except Exception as exc:
            avisos.append(f"{nome_original}: {exc}")
            continue

        nome_temp = f"{uuid.uuid4().hex}.{extensao}"
        caminho_temp = pasta_temp / nome_temp
        arquivo.save(str(caminho_temp))

        try:
            tamanho_bytes = float(caminho_temp.stat().st_size)
        except Exception:
            tamanho_bytes = 0.0

        arquivos_para_processar.append({
            "nome_original": nome_original,
            "caminho_temp": str(caminho_temp),
            "tamanho_bytes": tamanho_bytes,
        })

    if not arquivos_para_processar:
        return jsonify({
            "ok": False,
            "mensagem": "Nenhum arquivo válido para anexar.",
            "avisos": avisos,
        }), 400

    try:
        from app.tasks.contratos_anexos_tasks import tarefa_processar_upload_anexos_contrato

        tarefa = tarefa_processar_upload_anexos_contrato.apply_async(
            kwargs={
                "arquivos": arquivos_para_processar,
                "id_solicitacao": int(id_solicitacao),
                "id_fato_controle_contratos": int(id_contrato) if id_contrato not in (None, "", 0) else None,
                "id_fato_contrato_d4": None,
                "id_fato_kanban_card": int(id_card) if id_card not in (None, "", 0) else None,
                "tipo_solicitacao": tipo_solicitacao,
            },
            queue=os.getenv("CELERY_QUEUE_CONTRATOS_ANEXOS", "contratos_anexos"),
        )

        return jsonify({
            "ok": True,
            "mensagem": "Arquivos anexados.",
            "task_id": getattr(tarefa, "id", None),
            "avisos": avisos,
        })

    except Exception as exc:
        current_app.logger.exception(
            "ANEXOS_CONTRATO | falha ao enfileirar upload | id_solicitacao=%s",
            id_solicitacao,
        )
        return jsonify({"ok": False, "mensagem": f"Erro ao enviar anexos para processamento: {exc}"}), 500


@admin.route("/aprovacao/contratos/anexos/<int:id_anexo>/download", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("120 per minute", methods=["GET"])
def download_anexo_contrato(id_anexo: int):
    row = db.session.execute(
        text(f"""
            SELECT TOP 1
                 IDFatoAnexosContratosEuromidia
                ,NomeArquivo
                ,UrlAnexo
            FROM {TABELA_ANEXOS_CONTRATOS_ADMIN}
            WHERE IDFatoAnexosContratosEuromidia = :id_anexo
        """),
        {"id_anexo": int(id_anexo)},
    ).mappings().first()

    if not row:
        abort(404)

    anexo = dict(row)
    caminho = _anexos_contrato_resolver_caminho_admin(anexo.get("UrlAnexo"))
    if not caminho or not caminho.exists() or not caminho.is_file():
        abort(404)

    return send_file(
        str(caminho),
        as_attachment=True,
        download_name=anexo.get("NomeArquivo") or caminho.name,
    )


@admin.route("/aprovacao/contratos/anexos/<int:id_anexo>/remover", methods=["POST", "DELETE"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("60 per minute", methods=["POST", "DELETE"])
def remover_anexo_contrato(id_anexo: int):
    row = db.session.execute(
        text(f"""
            SELECT TOP 1
                 IDFatoAnexosContratosEuromidia
                ,NomeArquivo
                ,UrlAnexo
            FROM {TABELA_ANEXOS_CONTRATOS_ADMIN}
            WHERE IDFatoAnexosContratosEuromidia = :id_anexo
        """),
        {"id_anexo": int(id_anexo)},
    ).mappings().first()

    if not row:
        return jsonify({"ok": False, "mensagem": "Anexo não encontrado."}), 404

    anexo = dict(row)
    caminho = None
    try:
        caminho = _anexos_contrato_resolver_caminho_admin(anexo.get("UrlAnexo"))
    except Exception:
        caminho = None

    db.session.execute(
        text(f"""
            DELETE FROM {TABELA_ANEXOS_CONTRATOS_ADMIN}
            WHERE IDFatoAnexosContratosEuromidia = :id_anexo
        """),
        {"id_anexo": int(id_anexo)},
    )
    db.session.commit()

    if caminho and caminho.exists() and caminho.is_file():
        try:
            caminho.unlink()
        except Exception:
            current_app.logger.exception(
                "ANEXOS_CONTRATO | removi registro mas falhei ao apagar arquivo físico | id_anexo=%s | caminho=%s",
                id_anexo,
                caminho,
            )

    return jsonify({"ok": True, "mensagem": "Anexo removido."})


@admin.route("/aprovacao/contratos/<int:id_solicitacao>", methods=["GET", "POST"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute")
def detalhe_aprovacao_contrato(id_solicitacao: int):
    if request.method == "POST":
        try:
            id_usuario_logado = _id_usuario_logado()
            acao = _texto_ou_vazio(request.form.get("acao")).lower().strip() or "salvar"

            _atualizar_solicitacao_contrato_por_formulario(
                id_solicitacao=int(id_solicitacao),
                form=request.form,
                id_usuario_logado=id_usuario_logado,
            )

            cab_atualizada = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao))
            if not cab_atualizada:
                raise ValueError("Não encontrei a solicitação após salvar as alterações.")

            id_card = _int_ou_none(cab_atualizada.get("IDFatoKanbanCard"))
            id_empresa = _int_ou_none(cab_atualizada.get("IDEmpresa"))
            id_empresa_proprietaria = _int_ou_none(cab_atualizada.get("IDEmpresaProprietaria"))
            tipo_solicitacao = _tipo_solicitacao_normalizado(cab_atualizada.get("TipoSolicitacao"))
            id_fato_controle = _int_ou_none(cab_atualizada.get("IDFatoControleContratosEuromidia"))

            resultado_agendamentos_face = _sincronizar_agendamentos_face_contrato_por_formulario(
                id_solicitacao=int(id_solicitacao),
                form=request.form,
                id_usuario_logado=id_usuario_logado,
                id_contrato_controle_resolvido=id_fato_controle,
            )

            if acao == "salvar":
                _sincronizar_contato_contrato_se_fase_4(
                    id_fato_kanban_card=id_card,
                    id_empresa=id_empresa,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    id_fato_controle_contratos=id_fato_controle,
                )

                _sincronizar_dim_email_contrato_por_formulario_admin(
                    id_fato_controle_contratos=id_fato_controle,
                    id_fato_kanban_card=id_card,
                    form=request.form,
                )

                _registrar_historico_contrato_euromidia(
                    id_fato_controle_contratos=id_fato_controle,
                    id_fato_solicitacao=int(id_solicitacao),
                    id_dim_acao=_obter_id_dim_acao_solicitacao_contrato("ALTERAÇÕES SALVAS", fallback=4),
                    id_empresa=id_empresa,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    id_fato_kanban_card=id_card,
                    tipo_evento="ALTERAÇÕES SALVAS",
                    tipo_solicitacao=tipo_solicitacao,
                    descricao_evento="Solicitação salva/atualizada na tela de aprovação.",
                    id_dim_usuario_acao=id_usuario_logado,
                )

                resultado_bit_fracionado_salvar = {}
                if id_fato_controle not in (None, "", 0):
                    resultado_bit_fracionado_salvar = _atualizar_bit_fracionado_itens_contrato_por_agendamentos(
                        id_contrato_controle=id_fato_controle,
                    )

                db.session.commit()
                if int(resultado_agendamentos_face.get("inseridos") or 0) > 0 or int(resultado_agendamentos_face.get("atualizados") or 0) > 0:
                    flash("Alterações salvas com sucesso. A divisão em períodos ficou preservada para a aprovação.", "success")
                elif resultado_agendamentos_face.get("ignorados_sem_item_controle"):
                    flash("Alterações salvas, mas a divisão em períodos ainda não foi gravada porque o item não possui ContratoItem definitivo. Ela será gravada quando você aprovar com a grade preenchida.", "info")
                else:
                    flash("Alterações salvas com sucesso.", "success")
                return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

            if acao == "aprovar":
                status_atual = _texto_ou_vazio(cab_atualizada.get("StatusSolicitacao")).upper().strip()
                if status_atual == "APROVADO":
                    db.session.rollback()

                    resultado_d4sign = _d4sign_criar_para_solicitacao_aprovada_ou_retorno_admin(
                        id_solicitacao=int(id_solicitacao),
                        id_usuario_logado=id_usuario_logado,
                        cabecalho_solicitacao=cab_atualizada,
                    )

                    if resultado_d4sign.get("ok"):
                        status_d4 = resultado_d4sign.get("status") or "processado"
                        flash(f"Essa solicitação já estava aprovada. D4Sign {status_d4} com sucesso.", "success")
                    else:
                        erro_d4 = resultado_d4sign.get("erro") or resultado_d4sign.get("mensagem") or "erro não informado"
                        flash(f"Essa solicitação já está aprovada, mas não consegui criar o D4Sign: {erro_d4}", "warning")

                    return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

                if status_atual == "PROCESSANDO_APROVACAO":
                    db.session.rollback()
                    flash("Essa solicitação já está em processamento. Aguarde o worker concluir a aprovação.", "info")
                    return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

                status_anterior = cab_atualizada.get("StatusSolicitacao") or "PENDENTE_APROVACAO"
                form_data_aprovacao = {
                    chave: request.form.getlist(chave)
                    for chave in request.form.keys()
                }

                _sincronizar_dim_email_contrato_por_formulario_admin(
                    id_fato_controle_contratos=id_fato_controle,
                    id_fato_kanban_card=id_card,
                    form=request.form,
                )

                db.session.execute(
                    text("""
                        UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
                           SET StatusSolicitacao = 'PROCESSANDO_APROVACAO',
                               IDDimStatusContratos = 2,
                               DataAtualizacao = GETDATE()
                         WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
                    """),
                    {"id_solicitacao": int(id_solicitacao)},
                )

                _registrar_historico_contrato_euromidia(
                    id_fato_controle_contratos=id_fato_controle,
                    id_fato_solicitacao=int(id_solicitacao),
                    id_dim_acao=_obter_id_dim_acao_solicitacao_contrato("APROVAÇÃO SOLICITADA", fallback=4),
                    id_empresa=id_empresa,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    id_fato_kanban_card=id_card,
                    tipo_evento="APROVAÇÃO SOLICITADA",
                    tipo_solicitacao=tipo_solicitacao,
                    descricao_evento="Usuário clicou em Aprovar Contrato; processamento enviado para a fila assíncrona.",
                    id_dim_usuario_acao=id_usuario_logado,
                )
                db.session.commit()

                try:
                    from app.tasks.airflow_admin_tasks import tarefa_processar_aprovacao_contrato

                    tarefa = tarefa_processar_aprovacao_contrato.apply_async(
                        kwargs={
                            "id_solicitacao": int(id_solicitacao),
                            "id_usuario_logado": int(id_usuario_logado) if id_usuario_logado not in (None, "", 0) else None,
                            "form_data": form_data_aprovacao,
                        },
                        queue=os.getenv("CELERY_QUEUE_AIRFLOW_ADMIN", "airflow_admin"),
                    )

                    current_app.logger.info(
                        "APROVACAO_CONTRATO | aprovação enviada para Celery | task_id=%s | id_solicitacao=%s | id_card=%s",
                        getattr(tarefa, "id", None),
                        id_solicitacao,
                        id_card,
                    )

                    flash("Contrato aprovado.", "success")
                    return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

                except Exception as exc:
                    current_app.logger.exception(
                        "APROVACAO_CONTRATO | falha ao enfileirar aprovação no Celery | id_solicitacao=%s",
                        id_solicitacao,
                    )
                    db.session.rollback()
                    db.session.execute(
                        text("""
                            UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
                               SET StatusSolicitacao = :status_anterior,
                                   DataAtualizacao = GETDATE()
                             WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
                        """),
                        {
                            "status_anterior": status_anterior,
                            "id_solicitacao": int(id_solicitacao),
                        },
                    )
                    db.session.commit()
                    flash(f"Erro ao enviar aprovação para o Celery: {exc}", "danger")
                    return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

            if acao == "reprovar":
                db.session.execute(
                    text("""
                        UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
                           SET IDDimUsuariosRejeicao = :id_usuario_logado,
                               DataRejeicao = GETDATE(),
                               StatusSolicitacao = 'REPROVADO',
                               DataAtualizacao = GETDATE()
                         WHERE IDFatoSolicitacaoContratoEuromidia = :id_solicitacao
                    """),
                    {
                        "id_usuario_logado": int(id_usuario_logado) if id_usuario_logado not in (None, "", 0) else None,
                        "id_solicitacao": int(id_solicitacao),
                    },
                )

                _aplicar_resultado_aprovacao_no_card(
                    id_fato_kanban_card=id_card,
                    id_usuario_logado=id_usuario_logado,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    aprovar=False,
                )

                _registrar_historico_contrato_euromidia(
                    id_fato_controle_contratos=id_fato_controle,
                    id_fato_solicitacao=int(id_solicitacao),
                    id_dim_acao=_obter_id_dim_acao_solicitacao_contrato("REPROVADO", fallback=2),
                    id_empresa=id_empresa,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    id_fato_kanban_card=id_card,
                    tipo_evento="REPROVADO",
                    tipo_solicitacao=tipo_solicitacao,
                    descricao_evento="Solicitação reprovada na tela de aprovação.",
                    id_dim_usuario_acao=id_usuario_logado,
                )

                db.session.commit()
                flash("Solicitação reprovada com sucesso.", "warning")
                return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

            raise ValueError(f"Ação inválida: {acao}")

        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao processar a solicitação: {e}", "danger")

    dados = _obter_solicitacao_contrato_detalhe(int(id_solicitacao))
    if not dados:
        abort(404)

    return render_template(
        "admin/aprovacao_contrato_detalhe.html",
        solicitacao=dados["solicitacao"],
        itens=dados["itens"],
        contatos_contrato=dados.get("contatos_contrato") or [],
        anexos_contrato=dados.get("anexos_contrato") or [],
        diagrama_status=dados["diagrama_status"],
    )













































# ==========================================================
# MENSAGENS DO USUÁRIO - DESTINO SEGURO PARA CONTRATOS
# ==========================================================

def _mensagens_normalizar_texto(valor) -> str:
    """Eu normalizo nomes para comparar vendedor sem depender de acento/caixa."""
    try:
        import unicodedata
        texto = str(valor or "").strip().casefold()
        texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
        return " ".join(texto.split())
    except Exception:
        return ""


def _mensagens_usuario_tem_admin_tudo() -> bool:
    """Eu libero rotas administrativas quando o usuário possui ADMIN_TUDO."""
    try:
        metodo = getattr(current_user, "has_permission", None)
        if not metodo:
            return False
        return bool(metodo("ADMIN_TUDO"))
    except Exception:
        return False


def _mensagens_usuario_eh_vendedor() -> bool:
    """Eu identifico se o usuário logado é perfil VENDEDOR."""
    try:
        return bool(_campanhas_vencimentos_usuario_eh_vendedor())
    except Exception:
        return False


def _mensagens_resolver_vendedor_logado(id_usuario_logado: int | None) -> dict:
    """Eu encontro o vendedor vinculado ao IDDimUsuarios do usuário logado."""
    try:
        id_usuario = int(id_usuario_logado or 0)
    except Exception:
        id_usuario = 0

    if id_usuario <= 0:
        return {"id_vendedor": 0, "nome_vendedor": ""}

    try:
        id_empresa = int(getattr(current_user, "IDEmpresaProprietaria", 0) or 0)
    except Exception:
        id_empresa = 0

    try:
        row = db.session.execute(
            text("""
                SELECT TOP (1)
                     v.IDVendedor
                    ,v.NomeVendedor
                FROM [Integracao].[dbo].[Vendedores] v WITH (NOLOCK)
                WHERE v.IDDimUsuarios = :id_usuario
                  AND ISNULL(v.BitAtivo, 1) = 1
                  AND (
                        :id_empresa = 0
                        OR ISNULL(v.IDEmpresaProprietaria, 0) = :id_empresa
                      )
                ORDER BY
                    CASE WHEN ISNULL(v.IDEmpresaProprietaria, 0) = :id_empresa THEN 0 ELSE 1 END,
                    v.IDVendedor ASC;
            """),
            {
                "id_usuario": int(id_usuario),
                "id_empresa": int(id_empresa),
            },
        ).mappings().first()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "MENSAGENS | Falha ao resolver vendedor logado | id_usuario=%s",
            id_usuario,
        )
        return {"id_vendedor": 0, "nome_vendedor": ""}

    if not row:
        return {"id_vendedor": 0, "nome_vendedor": ""}

    try:
        id_vendedor = int(row.get("IDVendedor") or 0)
    except Exception:
        id_vendedor = 0

    return {
        "id_vendedor": id_vendedor,
        "nome_vendedor": str(row.get("NomeVendedor") or "").strip(),
    }


def _mensagens_contrato_pertence_ao_vendedor(
    id_contrato: int | None,
    id_usuario_logado: int | None,
) -> bool:
    """Eu valido se o contrato possui item ou cabeçalho vinculado ao vendedor logado."""
    try:
        id_contrato_int = int(id_contrato or 0)
    except Exception:
        id_contrato_int = 0

    if id_contrato_int <= 0:
        return False

    vendedor = _mensagens_resolver_vendedor_logado(id_usuario_logado)
    id_vendedor_logado = int(vendedor.get("id_vendedor") or 0)
    nome_vendedor_logado_norm = _mensagens_normalizar_texto(vendedor.get("nome_vendedor"))

    if id_vendedor_logado <= 0 and not nome_vendedor_logado_norm:
        return False

    try:
        rows = db.session.execute(
            text("""
                SELECT
                     ctr.Vendedor AS VendedorContrato
                    ,i.IDVendedor AS IDVendedorItem
                    ,i.Vendedor AS VendedorItem
                FROM [Integracao].[Silver].[FatoControleContratosEuromidia] ctr WITH (NOLOCK)
                LEFT JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] i WITH (NOLOCK)
                    ON i.IDFatoControleContratoEuromidia = ctr.IDFatoControleContratosEuromidia
                WHERE ctr.IDFatoControleContratosEuromidia = :id_contrato;
            """),
            {"id_contrato": int(id_contrato_int)},
        ).mappings().all()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "MENSAGENS | Falha ao validar contrato do vendedor | id_contrato=%s | id_usuario=%s",
            id_contrato_int,
            id_usuario_logado,
        )
        return False

    for row in rows or []:
        try:
            id_vendedor_item = int(row.get("IDVendedorItem") or 0)
        except Exception:
            id_vendedor_item = 0

        if id_vendedor_logado > 0 and id_vendedor_item == id_vendedor_logado:
            return True

        for campo in ("VendedorItem", "VendedorContrato"):
            nome_norm = _mensagens_normalizar_texto(row.get(campo))
            if nome_vendedor_logado_norm and nome_norm and nome_norm == nome_vendedor_logado_norm:
                return True

    return False


def _mensagens_montar_destino_seguro(row_mensagem, id_usuario_logado: int | None) -> dict:
    """Eu monto o botão Abrir destino apontando para o detalhe do contrato."""
    id_contrato = _int_ou_none(row_mensagem.get("IDFatoControleContratosEuromidia"))
    link_original = str(row_mensagem.get("LinkDestino") or "").strip()

    usuario_eh_vendedor = _mensagens_usuario_eh_vendedor()
    usuario_eh_admin = _mensagens_usuario_tem_admin_tudo()

    if id_contrato:
        pode_abrir = True
        motivo_bloqueio = ""

        if usuario_eh_vendedor and not usuario_eh_admin:
            pode_abrir = _mensagens_contrato_pertence_ao_vendedor(
                id_contrato=int(id_contrato),
                id_usuario_logado=id_usuario_logado,
            )
            if not pode_abrir:
                motivo_bloqueio = "Contrato não pertence ao vendedor logado."

        if not pode_abrir:
            return {
                "link": "",
                "pode_abrir_destino": False,
                "motivo_destino": motivo_bloqueio,
            }

        return {
            "link": url_for(
                "Paineis.contratos_detalhe",
                id_contrato=int(id_contrato),
                return_to=url_for("Paineis.contratos_lista", id_contrato=int(id_contrato)),
            ),
            "pode_abrir_destino": True,
            "motivo_destino": "",
        }

    if link_original and not usuario_eh_vendedor:
        return {
            "link": link_original,
            "pode_abrir_destino": True,
            "motivo_destino": "",
        }

    return {
        "link": "",
        "pode_abrir_destino": False,
        "motivo_destino": "Mensagem sem contrato de destino liberado.",
    }



def _mensagens_param_int(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(request.args.get(nome) or padrao)
    except Exception:
        valor = padrao

    if valor < minimo:
        valor = minimo
    if valor > maximo:
        valor = maximo

    return valor


def _mensagens_formatar_data(valor) -> str:
    if not valor:
        return ""

    try:
        return valor.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(valor or "")


def _mensagens_payload_lista(row_mensagem, id_usuario_logado: int) -> dict:
    destino = _mensagens_montar_destino_seguro(row_mensagem, id_usuario_logado)
    data_criacao = row_mensagem.get("DataCriacao")
    data_leitura = row_mensagem.get("DataLeitura")

    return {
        "id": int(row_mensagem["IDFatoMensagemUsuario"]),
        "id_tipo": row_mensagem.get("IDDimTipoMensagem"),
        "tipo": row_mensagem.get("NomeTipoMensagem") or "Mensagem",
        "titulo": row_mensagem.get("TituloMensagem") or "Sem título",
        "resumo": row_mensagem.get("ResumoMensagem") or "",
        "link": destino.get("link") or "",
        "pode_abrir_destino": bool(destino.get("pode_abrir_destino")),
        "motivo_destino": destino.get("motivo_destino") or "",
        "bit_lida": bool(row_mensagem.get("BitLida")),
        "data_criacao": _mensagens_formatar_data(data_criacao),
        "data_leitura": _mensagens_formatar_data(data_leitura),
        "id_vencimento": row_mensagem.get("IDFatoVencimentoCampanhaEuromidia"),
        "id_contrato": row_mensagem.get("IDFatoControleContratosEuromidia"),
        "id_item": row_mensagem.get("IDFatoControleContratosItensEuromidia"),
    }


def _mensagens_payload_detalhe(row_mensagem, id_usuario_logado: int) -> dict:
    destino = _mensagens_montar_destino_seguro(row_mensagem, id_usuario_logado)
    data_criacao = row_mensagem.get("DataCriacao")
    data_leitura = row_mensagem.get("DataLeitura")

    return {
        "id": int(row_mensagem["IDFatoMensagemUsuario"]),
        "id_tipo": row_mensagem.get("IDDimTipoMensagem"),
        "tipo": row_mensagem.get("NomeTipoMensagem") or "Mensagem",
        "titulo": row_mensagem.get("TituloMensagem") or "Sem título",
        "texto": row_mensagem.get("TextoMensagem") or "",
        "link": destino.get("link") or "",
        "pode_abrir_destino": bool(destino.get("pode_abrir_destino")),
        "motivo_destino": destino.get("motivo_destino") or "",
        "bit_lida": bool(row_mensagem.get("BitLida")),
        "data_criacao": _mensagens_formatar_data(data_criacao),
        "data_leitura": _mensagens_formatar_data(data_leitura),
        "id_vencimento": row_mensagem.get("IDFatoVencimentoCampanhaEuromidia"),
        "id_contrato": row_mensagem.get("IDFatoControleContratosEuromidia"),
        "id_item": row_mensagem.get("IDFatoControleContratosItensEuromidia"),
    }


@admin.route("/mensagens", methods=["GET"])
@login_required
@limiter.limit("80 per minute", methods=["GET"])
def mensagens_usuario():
    return render_template("admin/mensagens_usuario.html")


@admin.route("/api/mensagens/resumo", methods=["GET"])
@login_required
@limiter.limit("120 per minute", methods=["GET"])
def api_mensagens_resumo():
    id_usuario = _id_usuario_logado()

    if not id_usuario:
        return jsonify({"ok": False, "erro": "Usuário logado não identificado."}), 401

    row = db.session.execute(
        text("""
            SELECT
                COUNT(1) AS Total,
                SUM(CASE WHEN ISNULL(BitLida, 0) = 0 THEN 1 ELSE 0 END) AS NaoLidas,
                SUM(CASE WHEN ISNULL(BitLida, 0) = 1 THEN 1 ELSE 0 END) AS Lidas
            FROM [Integracao].[Silver].[FatoMensagemUsuario] WITH (NOLOCK)
            WHERE IDDimUsuariosDestinatario = :id_usuario
              AND ISNULL(BitAtivo, 1) = 1
        """),
        {"id_usuario": int(id_usuario)},
    ).mappings().first()

    total = int(row.get("Total") or 0) if row else 0
    nao_lidas = int(row.get("NaoLidas") or 0) if row else 0
    lidas = int(row.get("Lidas") or 0) if row else 0

    return jsonify({
        "ok": True,
        "total": total,
        "nao_lidas": nao_lidas,
        "lidas": lidas,
    })


@admin.route("/api/mensagens", methods=["GET"])
@login_required
@limiter.limit("120 per minute", methods=["GET"])
def api_mensagens_lista():
    id_usuario = _id_usuario_logado()

    if not id_usuario:
        return jsonify({"ok": False, "erro": "Usuário logado não identificado."}), 401

    pagina = _mensagens_param_int("page", 1, 1, 100000)
    por_pagina = 10
    offset = (pagina - 1) * por_pagina

    filtro = (request.args.get("filtro") or "todas").strip().lower()
    if filtro not in {"todas", "nao_lidas", "lidas"}:
        filtro = "todas"

    termo = (request.args.get("q") or "").strip()

    where_sql = [
        "m.IDDimUsuariosDestinatario = :id_usuario",
        "ISNULL(m.BitAtivo, 1) = 1",
    ]
    parametros = {
        "id_usuario": int(id_usuario),
        "offset": int(offset),
        "por_pagina": int(por_pagina),
    }

    if filtro == "nao_lidas":
        where_sql.append("ISNULL(m.BitLida, 0) = 0")
    elif filtro == "lidas":
        where_sql.append("ISNULL(m.BitLida, 0) = 1")

    if termo:
        parametros["termo_like"] = f"%{termo}%"
        where_sql.append("""
            (
                ISNULL(m.TituloMensagem, '') LIKE :termo_like
                OR ISNULL(m.TextoMensagem, '') LIKE :termo_like
                OR ISNULL(tm.NomeTipoMensagem, '') LIKE :termo_like
                OR CONVERT(varchar(30), ISNULL(m.IDFatoControleContratosEuromidia, 0)) LIKE :termo_like
                OR CONVERT(varchar(30), ISNULL(m.IDFatoControleContratosItensEuromidia, 0)) LIKE :termo_like
                OR CONVERT(varchar(30), ISNULL(m.IDFatoVencimentoCampanhaEuromidia, 0)) LIKE :termo_like
            )
        """)

    where_final = " AND ".join(where_sql)

    row_total = db.session.execute(
        text(f"""
            SELECT COUNT(1) AS Total
            FROM [Integracao].[Silver].[FatoMensagemUsuario] m WITH (NOLOCK)
            LEFT JOIN [Integracao].[Silver].[DimTipoMensagem] tm WITH (NOLOCK)
                ON tm.IDDimTipoMensagem = m.IDDimTipoMensagem
            WHERE {where_final}
        """),
        parametros,
    ).mappings().first()

    total_filtrado = int(row_total.get("Total") or 0) if row_total else 0
    total_paginas = max(1, (total_filtrado + por_pagina - 1) // por_pagina)

    if pagina > total_paginas:
        pagina = total_paginas
        offset = (pagina - 1) * por_pagina
        parametros["offset"] = int(offset)

    rows = db.session.execute(
        text(f"""
            SELECT
                 m.IDFatoMensagemUsuario
                ,m.IDDimTipoMensagem
                ,tm.NomeTipoMensagem
                ,m.IDFatoVencimentoCampanhaEuromidia
                ,m.IDFatoControleContratosEuromidia
                ,m.IDFatoControleContratosItensEuromidia
                ,m.TituloMensagem
                ,LEFT(ISNULL(m.TextoMensagem, ''), 260) AS ResumoMensagem
                ,m.LinkDestino
                ,ISNULL(m.BitLida, 0) AS BitLida
                ,m.DataLeitura
                ,m.DataCriacao
                ,m.DataAtualizacao
            FROM [Integracao].[Silver].[FatoMensagemUsuario] m WITH (NOLOCK)
            LEFT JOIN [Integracao].[Silver].[DimTipoMensagem] tm WITH (NOLOCK)
                ON tm.IDDimTipoMensagem = m.IDDimTipoMensagem
            WHERE {where_final}
            ORDER BY
                CASE WHEN ISNULL(m.BitLida, 0) = 0 THEN 0 ELSE 1 END,
                m.DataCriacao DESC,
                m.IDFatoMensagemUsuario DESC
            OFFSET :offset ROWS FETCH NEXT :por_pagina ROWS ONLY;
        """),
        parametros,
    ).mappings().all()

    row_resumo = db.session.execute(
        text("""
            SELECT
                COUNT(1) AS Total,
                SUM(CASE WHEN ISNULL(BitLida, 0) = 0 THEN 1 ELSE 0 END) AS NaoLidas,
                SUM(CASE WHEN ISNULL(BitLida, 0) = 1 THEN 1 ELSE 0 END) AS Lidas
            FROM [Integracao].[Silver].[FatoMensagemUsuario] WITH (NOLOCK)
            WHERE IDDimUsuariosDestinatario = :id_usuario
              AND ISNULL(BitAtivo, 1) = 1
        """),
        {"id_usuario": int(id_usuario)},
    ).mappings().first()

    itens = [_mensagens_payload_lista(r, int(id_usuario)) for r in rows]

    inicio = offset + 1 if total_filtrado > 0 else 0
    fim = min(offset + por_pagina, total_filtrado) if total_filtrado > 0 else 0

    return jsonify({
        "ok": True,
        "itens": itens,
        "resumo": {
            "total": int(row_resumo.get("Total") or 0) if row_resumo else 0,
            "nao_lidas": int(row_resumo.get("NaoLidas") or 0) if row_resumo else 0,
            "lidas": int(row_resumo.get("Lidas") or 0) if row_resumo else 0,
        },
        "paginacao": {
            "page": int(pagina),
            "per_page": int(por_pagina),
            "total": int(total_filtrado),
            "total_pages": int(total_paginas),
            "inicio": int(inicio),
            "fim": int(fim),
        },
    })


@admin.route("/api/mensagens/<int:id_mensagem>", methods=["GET"])
@login_required
@limiter.limit("120 per minute", methods=["GET"])
def api_mensagens_detalhe(id_mensagem: int):
    id_usuario = _id_usuario_logado()

    if not id_usuario:
        return jsonify({"ok": False, "erro": "Usuário logado não identificado."}), 401

    r = db.session.execute(
        text("""
            SELECT TOP (1)
                 m.IDFatoMensagemUsuario
                ,m.IDDimTipoMensagem
                ,tm.NomeTipoMensagem
                ,m.IDFatoVencimentoCampanhaEuromidia
                ,m.IDFatoControleContratosEuromidia
                ,m.IDFatoControleContratosItensEuromidia
                ,m.TituloMensagem
                ,m.TextoMensagem
                ,m.LinkDestino
                ,ISNULL(m.BitLida, 0) AS BitLida
                ,m.DataLeitura
                ,m.DataCriacao
                ,m.DataAtualizacao
            FROM [Integracao].[Silver].[FatoMensagemUsuario] m WITH (NOLOCK)
            LEFT JOIN [Integracao].[Silver].[DimTipoMensagem] tm WITH (NOLOCK)
                ON tm.IDDimTipoMensagem = m.IDDimTipoMensagem
            WHERE m.IDFatoMensagemUsuario = :id_mensagem
              AND m.IDDimUsuariosDestinatario = :id_usuario
              AND ISNULL(m.BitAtivo, 1) = 1
        """),
        {
            "id_mensagem": int(id_mensagem),
            "id_usuario": int(id_usuario),
        },
    ).mappings().first()

    if not r:
        return jsonify({"ok": False, "erro": "Mensagem não encontrada."}), 404

    return jsonify({
        "ok": True,
        "mensagem": _mensagens_payload_detalhe(r, int(id_usuario)),
    })


@admin.route("/api/mensagens/<int:id_mensagem>/marcar-lida", methods=["POST"])
@login_required
@limiter.limit("120 per minute", methods=["POST"])
def api_mensagens_marcar_lida(id_mensagem: int):
    id_usuario = _id_usuario_logado()

    if not id_usuario:
        return jsonify({"ok": False, "erro": "Usuário logado não identificado."}), 401

    resultado = db.session.execute(
        text("""
            UPDATE [Integracao].[Silver].[FatoMensagemUsuario]
               SET BitLida = 1,
                   DataLeitura = COALESCE(DataLeitura, GETDATE()),
                   DataAtualizacao = GETDATE()
             WHERE IDFatoMensagemUsuario = :id_mensagem
               AND IDDimUsuariosDestinatario = :id_usuario
               AND ISNULL(BitAtivo, 1) = 1
        """),
        {
            "id_mensagem": int(id_mensagem),
            "id_usuario": int(id_usuario),
        },
    )

    db.session.commit()
    emitir_resumo_mensagens_usuario(int(id_usuario), evento="mensagens:lida")

    return jsonify({
        "ok": True,
        "linhas": int(resultado.rowcount or 0),
    })


@admin.route("/api/mensagens/marcar-todas-lidas", methods=["POST"])
@login_required
@limiter.limit("60 per minute", methods=["POST"])
def api_mensagens_marcar_todas_lidas():
    id_usuario = _id_usuario_logado()

    if not id_usuario:
        return jsonify({"ok": False, "erro": "Usuário logado não identificado."}), 401

    resultado = db.session.execute(
        text("""
            UPDATE [Integracao].[Silver].[FatoMensagemUsuario]
               SET BitLida = 1,
                   DataLeitura = COALESCE(DataLeitura, GETDATE()),
                   DataAtualizacao = GETDATE()
             WHERE IDDimUsuariosDestinatario = :id_usuario
               AND ISNULL(BitAtivo, 1) = 1
               AND ISNULL(BitLida, 0) = 0
        """),
        {"id_usuario": int(id_usuario)},
    )

    db.session.commit()
    emitir_resumo_mensagens_usuario(int(id_usuario), evento="mensagens:todas_lidas")

    return jsonify({
        "ok": True,
        "linhas": int(resultado.rowcount or 0),
    })


@admin.route("/api/mensagens/<int:id_mensagem>/excluir", methods=["POST", "DELETE"])
@login_required
@limiter.limit("120 per minute", methods=["POST", "DELETE"])
def api_mensagens_excluir(id_mensagem: int):
    id_usuario = _id_usuario_logado()

    if not id_usuario:
        return jsonify({"ok": False, "erro": "Usuário logado não identificado."}), 401

    resultado = db.session.execute(
        text("""
            UPDATE [Integracao].[Silver].[FatoMensagemUsuario]
               SET BitAtivo = 0,
                   DataAtualizacao = GETDATE()
             WHERE IDFatoMensagemUsuario = :id_mensagem
               AND IDDimUsuariosDestinatario = :id_usuario
               AND ISNULL(BitAtivo, 1) = 1
        """),
        {
            "id_mensagem": int(id_mensagem),
            "id_usuario": int(id_usuario),
        },
    )

    db.session.commit()
    emitir_resumo_mensagens_usuario(int(id_usuario), evento="mensagens:excluida")

    return jsonify({
        "ok": True,
        "linhas": int(resultado.rowcount or 0),
    })


@admin.route("/api/mensagens/notificar", methods=["POST"])
@csrf.exempt
@limiter.limit("300 per minute", methods=["POST"])
def api_mensagens_notificar():
    token_esperado = (
        current_app.config.get("MENSAGERIA_SOCKET_TOKEN")
        or os.getenv("MENSAGERIA_SOCKET_TOKEN")
        or ""
    )

    token_recebido = (
        request.headers.get("X-Mensageria-Token")
        or request.headers.get("X-Internal-Token")
        or ""
    )

    if not token_esperado or token_recebido != token_esperado:
        return jsonify({"ok": False, "erro": "Token inválido."}), 401

    payload = request.get_json(silent=True) or {}

    usuarios_raw = (
        payload.get("usuarios")
        or payload.get("ids_usuarios")
        or payload.get("id_usuario")
        or []
    )

    if isinstance(usuarios_raw, (int, str)):
        usuarios_raw = [usuarios_raw]

    usuarios = []

    for item in usuarios_raw:
        try:
            id_usuario = int(item)
            if id_usuario > 0 and id_usuario not in usuarios:
                usuarios.append(id_usuario)
        except Exception:
            pass

    if not usuarios:
        return jsonify({"ok": False, "erro": "Nenhum usuário informado."}), 400

    resumos = []

    for id_usuario in usuarios:
        payload_socket = emitir_nova_mensagem_usuario(int(id_usuario))
        resumos.append({
            "id_usuario": int(id_usuario),
            "nao_lidas": int(payload_socket.get("nao_lidas") or 0),
        })

    return jsonify({
        "ok": True,
        "usuarios_notificados": usuarios,
        "resumos": resumos,
    })

# ==========================================================
# LISTA DE PREÇOS EUROMÍDIA
# ==========================================================


def _id_usuario_logado_admin_ou_none():
    """Retorna um ID de usuário possível para gravar em AlteradoPor, sem depender de um único nome de atributo."""

    for nome_atributo in (
        "IDDimUsuarios",
        "IDDimUsuario",
        "IDUsuario",
        "id_usuario",
        "id_dim_usuarios",
        "id",
    ):
        valor = getattr(current_user, nome_atributo, None)
        try:
            if valor is not None and str(valor).strip() != "":
                return int(valor)
        except Exception:
            continue

    return None


def _normalizar_texto_perfil_lista_precos(valor) -> str:
    """Normaliza o texto do perfil para comparar com segurança."""

    import unicodedata

    texto = str(valor or "").strip().upper()
    if not texto:
        return ""

    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(texto.split())


def _usuario_logado_tem_perfil_vendedor_lista_precos() -> bool:
    """
    Bloqueio explícito para Perfil = VENDEDOR.

    Regra principal:
    - IDDimPerfilUsuario = 3 é VENDEDOR.

    Por segurança, também confiro textos de perfil no current_user, na relação
    current_user.perfil e, em último caso, no banco. Assim o endpoint não depende
    de um único nome de atributo carregado no login.
    """

    if not getattr(current_user, "is_authenticated", False):
        return False

    def _eh_vendedor_por_texto(valor) -> bool:
        return _normalizar_texto_perfil_lista_precos(valor) == "VENDEDOR"

    def _eh_vendedor_por_id(valor) -> bool:
        try:
            return int(valor or 0) == 3
        except Exception:
            return False

    # 1) Regra mais confiável: ID do perfil carregado no usuário logado.
    for nome_atributo in (
        "IDDimPerfilUsuario",
        "id_dim_perfil_usuario",
        "id_perfil_usuario",
        "IDPerfilUsuario",
        "IDPerfil",
        "PerfilID",
        "perfil_id",
    ):
        if _eh_vendedor_por_id(getattr(current_user, nome_atributo, None)):
            return True

    # 2) Campos textuais diretamente no usuário.
    for nome_atributo in (
        "Perfil",
        "NomePerfil",
        "nome_perfil",
        "DescricaoPerfil",
        "descricao_perfil",
        "TipoPerfil",
        "tipo_perfil",
        "PerfilUsuario",
        "perfil_usuario",
        "Role",
        "role",
        "Papel",
        "papel",
    ):
        valor = getattr(current_user, nome_atributo, None)
        if _eh_vendedor_por_texto(valor):
            return True

    # 3) Relação current_user.perfil usada no módulo de autenticação.
    perfil_rel = getattr(current_user, "perfil", None)
    if perfil_rel is not None:
        for nome_atributo in (
            "IDDimPerfilUsuario",
            "id_dim_perfil_usuario",
            "IDPerfilUsuario",
            "IDPerfil",
            "id",
        ):
            if _eh_vendedor_por_id(getattr(perfil_rel, nome_atributo, None)):
                return True

        for nome_atributo in (
            "Perfil",
            "NomePerfil",
            "nome_perfil",
            "Descricao",
            "descricao",
            "DescricaoPerfil",
            "descricao_perfil",
            "NomePerfilUsuario",
            "nome",
        ):
            if _eh_vendedor_por_texto(getattr(perfil_rel, nome_atributo, None)):
                return True

    # 4) Fallback no banco: primeiro tenta pela própria DimUsuarios.
    id_usuario = _id_usuario_logado_admin_ou_none()
    if not id_usuario:
        return False

    try:
        row_usuario = db.session.execute(
            text("""
                SELECT TOP (1)
                       u.*
                FROM [Integracao].[Silver].[DimUsuarios] AS u
                WHERE u.IDDimUsuarios = :id_usuario
            """),
            {"id_usuario": int(id_usuario)},
        ).mappings().first()

        if row_usuario:
            if _eh_vendedor_por_id(row_usuario.get("IDDimPerfilUsuario")):
                return True

            for nome_coluna in (
                "Perfil",
                "NomePerfil",
                "DescricaoPerfil",
                "TipoPerfil",
                "PerfilUsuario",
                "Role",
                "Papel",
            ):
                if _eh_vendedor_por_texto(row_usuario.get(nome_coluna)):
                    return True

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao verificar DimUsuarios para bloqueio da lista de preços."
        )

    # 5) Fallback extra: tenta resolver o nome do perfil pela dimensão de perfil.
    #    Mantive try/except porque o nome físico das colunas pode variar no banco.
    try:
        row_perfil = db.session.execute(
            text("""
                SELECT TOP (1)
                       p.*
                FROM [Integracao].[Silver].[DimUsuarios] AS u
                INNER JOIN [Integracao].[Silver].[DimPerfilUsuario] AS p
                    ON p.IDDimPerfilUsuario = u.IDDimPerfilUsuario
                WHERE u.IDDimUsuarios = :id_usuario
            """),
            {"id_usuario": int(id_usuario)},
        ).mappings().first()

        if row_perfil:
            if _eh_vendedor_por_id(row_perfil.get("IDDimPerfilUsuario")):
                return True

            for nome_coluna in (
                "Perfil",
                "NomePerfil",
                "Descricao",
                "DescricaoPerfil",
                "NomePerfilUsuario",
                "TipoPerfil",
            ):
                if _eh_vendedor_por_texto(row_perfil.get(nome_coluna)):
                    return True

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao verificar DimPerfilUsuario para bloqueio da lista de preços."
        )

    return False


def _bloquear_vendedor_lista_precos():
    """Interrompe a requisição da lista de preços quando o perfil logado é VENDEDOR."""

    if _usuario_logado_tem_perfil_vendedor_lista_precos():
        current_app.logger.warning(
            "Acesso bloqueado ao endpoint de lista de preços para usuário com perfil VENDEDOR."
        )
        abort(403, description="Usuário com perfil VENDEDOR não pode acessar a lista de preços.")


def _normalizar_lista_filtro_lista_precos(valores) -> list[str]:
    """Remove vazios, duplicados e preserva a ordem dos filtros múltiplos."""

    resultado: list[str] = []
    vistos: set[str] = set()

    for valor in valores or []:
        texto = str(valor or "").strip()
        if not texto:
            continue
        chave = texto.upper()
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append(texto)

    return resultado


def _adicionar_filtro_in_lista_precos(where: list[str], params: dict, coluna_sql: str, nome_base: str, valores: list[str]):
    """Cria um filtro IN com parâmetros nomeados, sem concatenar valores do usuário no SQL."""

    if not valores:
        return

    placeholders = []
    for indice, valor in enumerate(valores):
        chave = f"{nome_base}_{indice}"
        placeholders.append(f":{chave}")
        params[chave] = valor

    where.append(f"{coluna_sql} IN ({', '.join(placeholders)})")


def _url_lista_precos_euromidia(page: int, q: str, ativo: str, tipos: list[str], tabelas: list[str]) -> str:
    """Monta URL preservando filtros múltiplos de tipo e tabela."""

    from urllib.parse import urlencode

    parametros = []
    parametros.append(("page", max(1, int(page or 1))))

    if q:
        parametros.append(("q", q))

    if ativo and ativo != "todos":
        parametros.append(("ativo", ativo))
    elif ativo == "todos":
        parametros.append(("ativo", "todos"))

    for tipo in tipos or []:
        parametros.append(("tipo", tipo))

    for tabela in tabelas or []:
        parametros.append(("tabela", tabela))

    query_string = urlencode(parametros, doseq=True)
    url_base = url_for("admin.lista_precos_euromidia")
    return f"{url_base}?{query_string}" if query_string else url_base


def _query_string_sem_page_lista_precos(q: str, ativo: str, tipos: list[str], tabelas: list[str]) -> str:
    """Retorna a querystring dos filtros atuais, sem o parâmetro page."""

    from urllib.parse import urlencode

    parametros = []

    if q:
        parametros.append(("q", q))

    if ativo:
        parametros.append(("ativo", ativo))

    for tipo in tipos or []:
        parametros.append(("tipo", tipo))

    for tabela in tabelas or []:
        parametros.append(("tabela", tabela))

    return urlencode(parametros, doseq=True)


def _formatar_moeda_brasil_lista_precos(valor) -> str:
    """Formata valor monetário no padrão brasileiro para retorno JSON das sugestões."""

    if valor is None:
        return "—"

    try:
        numero = float(valor)
    except Exception:
        return "—"

    return "R$ " + f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@admin.route("/lista-precos", methods=["GET"])
@admin.route("/listas-precos", methods=["GET"])
@admin.route("/precos/euromidia", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("80 per minute", methods=["GET"])
def lista_precos_euromidia():
    _bloquear_vendedor_lista_precos()

    q = (request.args.get("q") or "").strip()
    ativo = (request.args.get("ativo") or "todos").strip().lower()
    tipos_selecionados = _normalizar_lista_filtro_lista_precos(request.args.getlist("tipo"))
    tabelas_selecionadas = _normalizar_lista_filtro_lista_precos(request.args.getlist("tabela"))

    try:
        page = int(request.args.get("page") or "1")
    except Exception:
        page = 1

    page = max(1, page)
    per_page = 10

    where = ["1 = 1"]
    params = {}

    if q:
        where.append("""
            (
                COALESCE(pn.CodPonto, '') LIKE '%' + :q + '%'
                OR COALESCE(fp.CodFace, '') LIKE '%' + :q + '%'
                OR COALESCE(tp.Tipo, '') LIKE '%' + :q + '%'
                OR COALESCE(tp.PeriodoExibicao, '') LIKE '%' + :q + '%'
                OR COALESCE(CAST(tp.ExibicoesDia AS varchar(50)), '') LIKE '%' + :q + '%'
                OR COALESCE(CAST(tp.Valor AS varchar(50)), '') LIKE '%' + :q + '%'
                OR COALESCE(tp.PoliticaTrocas, '') LIKE '%' + :q + '%'
                OR COALESCE(tp.Tabela, '') LIKE '%' + :q + '%'
                OR COALESCE(pn.Cidade, '') LIKE '%' + :q + '%'
                OR COALESCE(pn.UF, '') LIKE '%' + :q + '%'
                OR COALESCE(pn.Referencia, '') LIKE '%' + :q + '%'
            )
        """)
        params["q"] = q

    if ativo in ("1", "ativo", "ativos"):
        where.append("ISNULL(tp.BitAtivo, 0) = 1")
        ativo = "1"
    elif ativo in ("0", "inativo", "inativos"):
        where.append("ISNULL(tp.BitAtivo, 0) = 0")
        ativo = "0"
    else:
        ativo = "todos"

    _adicionar_filtro_in_lista_precos(where, params, "tp.Tipo", "tipo", tipos_selecionados)
    _adicionar_filtro_in_lista_precos(where, params, "tp.Tabela", "tabela", tabelas_selecionadas)

    where_sql = "\n        AND ".join(where)

    sql_count = text(f"""
        SELECT COUNT(1) AS Total
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] AS tp
        INNER JOIN [Integracao].[Silver].[DimPaineisEuromidia] AS pn
            ON pn.IDDimPaineisEuromidia = tp.IDDimPaineisEuromidia
        INNER JOIN [Integracao].[Silver].[DimFacesPaineis] AS fp
            ON fp.IDDimFacesPaineis = tp.IDDimFacesPaineis
        WHERE {where_sql}
    """)

    total = int(db.session.execute(sql_count, params).scalar() or 0)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    params_lista = dict(params)
    params_lista["offset"] = offset
    params_lista["per_page"] = per_page

    sql_itens = text(f"""
        SELECT
             tp.IDDimTabelaPrecosEuromidia
            ,tp.IDDimPaineisEuromidia
            ,tp.IDDimFacesPaineis
            ,tp.Tipo
            ,tp.PeriodoExibicao
            ,tp.ExibicoesDia
            ,tp.Valor
            ,tp.PoliticaTrocas
            ,tp.Tabela
            ,tp.DataPublicacao
            ,tp.DataValidade
            ,tp.DataAtualizacao
            ,tp.BitAtivo
            ,tp.AlteradoPor
            ,tp.ValorTroca
            ,pn.CodPonto
            ,pn.QuantidadeFaces
            ,pn.Cidade
            ,pn.UF
            ,pn.Logradouro
            ,pn.Bairro
            ,pn.Referencia AS ReferenciaPainel
            ,pn.Exibidora
            ,fp.CodFace
            ,fp.Face
            ,fp.Tipo AS TipoFace
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] AS tp
        INNER JOIN [Integracao].[Silver].[DimPaineisEuromidia] AS pn
            ON pn.IDDimPaineisEuromidia = tp.IDDimPaineisEuromidia
        INNER JOIN [Integracao].[Silver].[DimFacesPaineis] AS fp
            ON fp.IDDimFacesPaineis = tp.IDDimFacesPaineis
        WHERE {where_sql}
        ORDER BY
            ISNULL(tp.DataAtualizacao, CONVERT(datetime2, '19000101')) DESC,
            tp.IDDimTabelaPrecosEuromidia DESC
        OFFSET :offset ROWS
        FETCH NEXT :per_page ROWS ONLY
    """)

    rows = db.session.execute(sql_itens, params_lista).mappings().all()
    itens = [dict(row) for row in rows]

    tipos_rows = db.session.execute(text("""
        SELECT DISTINCT tp.Tipo
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] AS tp
        WHERE tp.Tipo IS NOT NULL
          AND LTRIM(RTRIM(tp.Tipo)) <> ''
        ORDER BY tp.Tipo ASC
    """)).scalars().all()

    tabelas_rows = db.session.execute(text("""
        SELECT DISTINCT tp.Tabela
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] AS tp
        WHERE tp.Tabela IS NOT NULL
          AND LTRIM(RTRIM(tp.Tabela)) <> ''
        ORDER BY tp.Tabela ASC
    """)).scalars().all()

    pagina_inicio = max(1, page - 3)
    pagina_fim = min(total_pages, page + 3)
    paginas_visiveis = list(range(pagina_inicio, pagina_fim + 1))
    query_string_sem_page = _query_string_sem_page_lista_precos(
        q=q,
        ativo=ativo,
        tipos=tipos_selecionados,
        tabelas=tabelas_selecionadas,
    )

    return render_template(
        "admin/lista_precos_euromidia.html",
        itens=itens,
        tipos=[x for x in tipos_rows if x],
        tabelas=[x for x in tabelas_rows if x],
        filtros={
            "q": q,
            "ativo": ativo,
            "tipos": tipos_selecionados,
            "tabelas": tabelas_selecionadas,
        },
        paginacao={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "inicio": (offset + 1) if total > 0 else 0,
            "fim": min(offset + per_page, total),
            "paginas_visiveis": paginas_visiveis,
            "query_string_sem_page": query_string_sem_page,
        },
    )


@admin.route("/lista-precos/api/sugestoes", methods=["GET"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("120 per minute", methods=["GET"])
def api_lista_precos_sugestoes():
    _bloquear_vendedor_lista_precos()

    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"ok": True, "itens": []})

    sql = text("""
        SELECT TOP (12)
             pn.CodPonto
            ,fp.CodFace
            ,fp.Face
            ,COALESCE(tp.Tipo, fp.Tipo) AS Tipo
            ,tp.PeriodoExibicao
            ,tp.ExibicoesDia
            ,tp.Valor
            ,tp.ValorTroca
            ,tp.Tabela
            ,pn.Cidade
            ,pn.UF
            ,pn.Referencia AS ReferenciaPainel
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] AS tp
        INNER JOIN [Integracao].[Silver].[DimPaineisEuromidia] AS pn
            ON pn.IDDimPaineisEuromidia = tp.IDDimPaineisEuromidia
        INNER JOIN [Integracao].[Silver].[DimFacesPaineis] AS fp
            ON fp.IDDimFacesPaineis = tp.IDDimFacesPaineis
        WHERE
            COALESCE(pn.CodPonto, '') LIKE '%' + :q + '%'
            OR COALESCE(fp.CodFace, '') LIKE '%' + :q + '%'
            OR COALESCE(tp.Tipo, '') LIKE '%' + :q + '%'
            OR COALESCE(tp.PeriodoExibicao, '') LIKE '%' + :q + '%'
            OR COALESCE(CAST(tp.ExibicoesDia AS varchar(50)), '') LIKE '%' + :q + '%'
            OR COALESCE(CAST(tp.Valor AS varchar(50)), '') LIKE '%' + :q + '%'
            OR COALESCE(tp.Tabela, '') LIKE '%' + :q + '%'
            OR COALESCE(pn.Cidade, '') LIKE '%' + :q + '%'
            OR COALESCE(pn.UF, '') LIKE '%' + :q + '%'
            OR COALESCE(pn.Referencia, '') LIKE '%' + :q + '%'
        ORDER BY
            CASE
                WHEN fp.CodFace = :q THEN 0
                WHEN pn.CodPonto = :q THEN 1
                WHEN fp.CodFace LIKE :q_prefixo THEN 2
                WHEN pn.CodPonto LIKE :q_prefixo THEN 3
                ELSE 9
            END,
            fp.CodFace ASC,
            tp.Tabela DESC,
            tp.ExibicoesDia DESC
    """)

    rows = db.session.execute(
        sql,
        {
            "q": q,
            "q_prefixo": f"{q}%",
        },
    ).mappings().all()

    itens = []
    for r in rows:
        valor = r.get("Valor")
        itens.append({
            "CodPonto": r.get("CodPonto") or "",
            "CodFace": r.get("CodFace") or "",
            "Face": r.get("Face") or "",
            "Tipo": r.get("Tipo") or "",
            "PeriodoExibicao": r.get("PeriodoExibicao") or "",
            "ExibicoesDia": r.get("ExibicoesDia"),
            "Valor": float(valor) if valor is not None else None,
            "ValorFormatado": _formatar_moeda_brasil_lista_precos(valor),
            "ValorTrocaFormatado": _formatar_moeda_brasil_lista_precos(r.get("ValorTroca")),
            "Tabela": r.get("Tabela") or "",
            "Cidade": r.get("Cidade") or "",
            "UF": r.get("UF") or "",
            "ReferenciaPainel": r.get("ReferenciaPainel") or "",
            "TermoBusca": r.get("CodFace") or r.get("CodPonto") or q,
        })

    return jsonify({"ok": True, "itens": itens})


@admin.route("/lista-precos/<int:id_preco>/bitativo", methods=["POST"])
@login_required
@requer_permissao("ADMIN_TUDO")
@limiter.limit("120 per minute", methods=["POST"])
def lista_precos_euromidia_alterar_bitativo(id_preco: int):
    _bloquear_vendedor_lista_precos()

    q = (request.form.get("q") or request.args.get("q") or "").strip()
    ativo = (request.form.get("ativo") or request.args.get("ativo") or "todos").strip()
    tipos_selecionados = _normalizar_lista_filtro_lista_precos(
        request.form.getlist("tipo") or request.args.getlist("tipo")
    )
    tabelas_selecionadas = _normalizar_lista_filtro_lista_precos(
        request.form.getlist("tabela") or request.args.getlist("tabela")
    )

    try:
        page = int(request.form.get("page") or request.args.get("page") or "1")
    except Exception:
        page = 1

    bit_ativo_raw = (request.form.get("bit_ativo") or "0").strip().lower()
    bit_ativo = 1 if bit_ativo_raw in ("1", "true", "on", "sim", "s") else 0

    params_update = {
        "id_preco": int(id_preco),
        "bit_ativo": int(bit_ativo),
        "alterado_por": _id_usuario_logado_admin_ou_none(),
    }

    try:
        resultado = db.session.execute(text("""
            UPDATE [Integracao].[Silver].[FatoTabelaPrecosEuromidia]
               SET BitAtivo = :bit_ativo,
                   DataAtualizacao = SYSDATETIME(),
                   AlteradoPor = :alterado_por
             WHERE IDDimTabelaPrecosEuromidia = :id_preco
        """), params_update)

        db.session.commit()

        if int(resultado.rowcount or 0) <= 0:
            flash("Preço não encontrado para atualizar o status ativo/inativo.", "warning")
        else:
            flash("Status do preço atualizado com sucesso.", "success")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar BitAtivo da lista de preços Euromídia.")
        flash(f"Erro ao atualizar o status do preço: {exc}", "danger")

    return redirect(_url_lista_precos_euromidia(
        page=page,
        q=q,
        ativo=ativo,
        tipos=tipos_selecionados,
        tabelas=tabelas_selecionadas,
    ))


# ==========================================================
# VENCIMENTOS DE CAMPANHAS EUROMÍDIA
# ==========================================================


def _campanhas_vencimentos_usuario_logado_id() -> int | None:
    """Retorna o IDDimUsuarios do usuário logado com fallback para nomes comuns usados no projeto."""

    try:
        id_usuario = _id_usuario_logado_admin_ou_none()
        if id_usuario:
            return int(id_usuario)
    except Exception:
        pass

    for nome_atributo in (
        "IDDimUsuarios",
        "IDDimUsuario",
        "IDUsuario",
        "id_usuario",
        "id_dim_usuarios",
        "id",
    ):
        valor = getattr(current_user, nome_atributo, None)
        try:
            if valor is not None and str(valor).strip() != "":
                return int(valor)
        except Exception:
            continue

    return None


def _campanhas_vencimentos_usuario_eh_vendedor() -> bool:
    """Identifica Perfil = VENDEDOR reaproveitando a regra robusta já usada na lista de preços."""

    try:
        return bool(_usuario_logado_tem_perfil_vendedor_lista_precos())
    except Exception:
        current_app.logger.exception(
            "Falha ao identificar perfil VENDEDOR na tela de vencimentos de campanha."
        )
        return False


def _campanhas_vencimentos_usuario_eh_admin() -> bool:
    """Identifica se o usuário logado tem ADMIN_TUDO para exibir filtros administrativos."""

    try:
        metodo = getattr(current_user, "has_permission", None)
        if not metodo:
            return False
        return bool(metodo("ADMIN_TUDO"))
    except Exception:
        current_app.logger.exception(
            "Falha ao identificar perfil ADMIN na tela de vencimentos de campanha."
        )
        return False


def _campanhas_vencimentos_classe_status(nome_status: str | None) -> str:
    """Converte o nome do status em classe CSS segura para o badge."""

    nome = (nome_status or "").strip().upper()

    if nome == "CAMPANHA ATIVA":
        return "ativa"
    if nome == "CAMPANHA VENCENDO":
        return "vencendo"
    if nome == "CAMPANHA VENCIDA":
        return "vencida"
    if nome == "CANCELADA":
        return "cancelada"
    if nome == "SEM DATA TERMINO":
        return "sem-data"
    if nome == "CAMPANHA FUTURA":
        return "futura"

    return "neutro"


def _campanhas_vencimentos_normalizar_lista_int(valores) -> list[int]:
    """Remove vazios, valores inválidos e duplicados de filtros múltiplos inteiros."""

    resultado: list[int] = []
    vistos: set[int] = set()

    for valor in valores or []:
        numero = _parse_int(valor)
        if numero is None:
            continue
        if numero in vistos:
            continue
        vistos.add(numero)
        resultado.append(int(numero))

    return resultado


def _campanhas_vencimentos_normalizar_lista_texto(valores) -> list[str]:
    """Remove vazios e duplicados de filtros múltiplos de texto preservando a ordem."""

    resultado: list[str] = []
    vistos: set[str] = set()

    for valor in valores or []:
        texto = str(valor or "").strip()
        if not texto:
            continue

        chave = texto.upper()
        if chave in vistos:
            continue

        vistos.add(chave)
        resultado.append(texto)

    return resultado


def _campanhas_vencimentos_marca_sql() -> str:
    """Expressão SQL única para a marca exibida usada em SELECT, filtro e sugestões."""

    return "COALESCE(NULLIF(LTRIM(RTRIM(ctr.MarcaExibida)), ''), NULLIF(LTRIM(RTRIM(venc.MarcaExibida)), ''))"


def _campanhas_vencimentos_razao_social_sql() -> str:
    """Expressão SQL da razão social exibida, ignorando vazios e valores placeholder como 0."""

    return """COALESCE(
        NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), emp_venc.RazaoSocial))), ''), '0'),
        NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), emp_ctr.RazaoSocial))), ''), '0'),
        NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), ctr.RazaoSocial))), ''), '0'),
        NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), emp_venc.NomeFantasia))), ''), '0'),
        NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), emp_ctr.NomeFantasia))), ''), '0'),
        NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), ctr.MarcaExibida))), ''), '0')
    )"""


def _campanhas_vencimentos_painel_sql() -> str:
    """Expressão SQL do painel exibido na tela: CodFace resolvido pelo item do contrato."""

    return "NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), item.CodFace))), '')"


def _campanhas_vencimentos_adicionar_filtro_in(
    filtros_sql: list[str],
    params: dict,
    coluna_sql: str,
    prefixo: str,
    valores,
) -> None:
    """Monta filtro IN com parâmetros nomeados para evitar SQL Injection."""

    valores_limpos = list(valores or [])
    if not valores_limpos:
        return

    nomes_parametros = []
    for idx, valor in enumerate(valores_limpos):
        nome_parametro = f"{prefixo}_{idx}"
        nomes_parametros.append(f":{nome_parametro}")
        params[nome_parametro] = valor

    filtros_sql.append(f"{coluna_sql} IN ({', '.join(nomes_parametros)})")


def _campanhas_vencimentos_montar_filtros_sql(
    *,
    q: str,
    status_ids_selecionados: list[int],
    marcas_selecionadas: list[str],
    vendedores_ids_selecionados: list[int],
    usuario_logado_eh_vendedor: bool,
    usuario_logado_eh_admin: bool,
    id_usuario_logado: int | None,
    data_inicio_filtro: date | None = None,
    data_fim_filtro: date | None = None,
) -> tuple[str, dict]:
    """Centraliza os filtros usados pela lista e pela busca de sugestões."""

    marca_sql = _campanhas_vencimentos_marca_sql()
    razao_social_sql = _campanhas_vencimentos_razao_social_sql()
    painel_sql = _campanhas_vencimentos_painel_sql()

    filtros_sql: list[str] = []
    params = {
        "usuario_logado_eh_vendedor": 1 if usuario_logado_eh_vendedor else 0,
        "id_usuario_logado": int(id_usuario_logado or 0),
    }

    if usuario_logado_eh_vendedor:
        filtros_sql.append("ISNULL(vend.IDDimUsuarios, 0) = :id_usuario_logado")

    if q:
        filtros_sql.append(f"""
            (
                CAST(venc.IDFatoControleContratosEuromidia AS varchar(50)) LIKE :q_like
                OR COALESCE(CONVERT(varchar(80), ctr.NumeroContrato), '') COLLATE Latin1_General_CI_AI LIKE :q_like
                OR COALESCE(CONVERT(varchar(80), ctr.NumeroPrevia), '') COLLATE Latin1_General_CI_AI LIKE :q_like
                OR ISNULL({razao_social_sql}, '') COLLATE Latin1_General_CI_AI LIKE :q_like
                OR ISNULL(ctr.MarcaExibida, '') COLLATE Latin1_General_CI_AI LIKE :q_like
                OR ISNULL(venc.MarcaExibida, '') COLLATE Latin1_General_CI_AI LIKE :q_like
                OR ISNULL({painel_sql}, '') COLLATE Latin1_General_CI_AI LIKE :q_like
                OR ISNULL(st.NomeStatus, '') COLLATE Latin1_General_CI_AI LIKE :q_like
            )
        """)
        params["q_like"] = f"%{q}%"

    if data_inicio_filtro is not None:
        filtros_sql.append("venc.DataTerminoPrevisto IS NOT NULL AND CAST(venc.DataTerminoPrevisto AS date) >= :data_inicio_filtro")
        params["data_inicio_filtro"] = data_inicio_filtro

    if data_fim_filtro is not None:
        filtros_sql.append("venc.DataTerminoPrevisto IS NOT NULL AND CAST(venc.DataTerminoPrevisto AS date) <= :data_fim_filtro")
        params["data_fim_filtro"] = data_fim_filtro

    _campanhas_vencimentos_adicionar_filtro_in(
        filtros_sql,
        params,
        "venc.IDDimStatusCampanha",
        "status_id",
        status_ids_selecionados,
    )

    _campanhas_vencimentos_adicionar_filtro_in(
        filtros_sql,
        params,
        f"{marca_sql} COLLATE Latin1_General_CI_AI",
        "marca",
        marcas_selecionadas,
    )

    if usuario_logado_eh_admin:
        _campanhas_vencimentos_adicionar_filtro_in(
            filtros_sql,
            params,
            "venc.IDVendedor",
            "vendedor_id",
            vendedores_ids_selecionados,
        )

    where_sql = " AND ".join(f"({item})" for item in filtros_sql) if filtros_sql else "1=1"
    return where_sql, params

def _campanhas_vencimentos_sql_from_where(where_sql: str) -> str:
    """FROM/JOIN padrão para a tela de vencimentos de campanhas.

    Regra oficial da tela:
    - a origem da linha é Integracao.Silver.FatoVencimentoCampanhaEuromidia;
    - IDFatoControleContratosEuromidia aponta para o cabeçalho do contrato;
    - IDFatoControleContratosItensEuromidia aponta para o item do contrato;
    - CodFace vem diretamente do item do contrato;
    - somente campanhas com venc.BitAtivo = 1 aparecem;
    - somente campanhas cujo item oficial esteja com item.BitAtivo = 1 aparecem;
    - se houver mais de uma linha ativa para o mesmo item, fica somente a mais recente.

    Observação importante:
    Eu não faço fallback por contrato + marca + período aqui. Esse fallback conseguia
    preencher CodFace em alguns dados quebrados, mas também podia escolher outro item
    do mesmo contrato e gerar duplicidade/associação errada.
    """

    return f"""
        FROM
        (
            SELECT *
            FROM
            (
                SELECT
                    venc_base.*,
                    ROW_NUMBER() OVER
                    (
                        PARTITION BY
                            venc_base.IDFatoControleContratosItensEuromidia
                        ORDER BY
                            ISNULL(venc_base.DataAtualizacao, '19000101') DESC,
                            ISNULL(venc_base.DataCriacao, '19000101') DESC,
                            venc_base.IDFatoVencimentoCampanhaEuromidia DESC
                    ) AS LinhaMaisRecente
                FROM [Integracao].[Silver].[FatoVencimentoCampanhaEuromidia] AS venc_base
                WHERE ISNULL(venc_base.BitAtivo, 1) = 1
                  AND ISNULL(venc_base.IDFatoControleContratosEuromidia, 0) > 0
                  AND ISNULL(venc_base.IDFatoControleContratosItensEuromidia, 0) > 0
            ) AS venc_filtrado
            WHERE venc_filtrado.LinhaMaisRecente = 1
        ) AS venc
        INNER JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
            ON ctr.IDFatoControleContratosEuromidia = venc.IDFatoControleContratosEuromidia
        INNER JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item
            ON item.IDFatoControleContratosItensEuromidia = venc.IDFatoControleContratosItensEuromidia
           AND item.IDFatoControleContratoEuromidia = venc.IDFatoControleContratosEuromidia
           AND ISNULL(item.BitAtivo, 0) = 1
        INNER JOIN [Integracao].[Silver].[DimStatusCampanha] AS st
            ON st.IDDimStatusCampanha = venc.IDDimStatusCampanha
        LEFT JOIN [Integracao].[dbo].[Vendedores] AS vend
            ON vend.IDVendedor = venc.IDVendedor
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] AS emp_venc
            ON emp_venc.IDEmpresa = venc.IDEmpresa
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] AS emp_ctr
            ON emp_ctr.IDEmpresa = ctr.IDEmpresa
        WHERE {where_sql}
    """

def _campanhas_vencimentos_enriquecer_item(d: dict) -> dict:
    """Adiciona classes CSS e textos auxiliares usados na tabela e nas sugestões."""

    razao_social = str(d.get("RazaoSocial") or "").strip()
    if not razao_social or razao_social == "0":
        d["RazaoSocial"] = "—"

    painel = str(d.get("Painel") or "").strip()
    if not painel or painel == "0":
        d["Painel"] = "—"

    d["ClasseStatus"] = _campanhas_vencimentos_classe_status(d.get("NomeStatus"))

    dias = d.get("DiasParaVencer")
    try:
        dias_int = int(dias) if dias is not None else None
    except Exception:
        dias_int = None

    if dias_int is None:
        d["ClasseDias"] = "sem-data"
        d["DiasTexto"] = "—"
    elif dias_int < 0:
        d["ClasseDias"] = "vencido"
        d["DiasTexto"] = str(dias_int)
    elif dias_int <= 45:
        d["ClasseDias"] = "perto"
        d["DiasTexto"] = str(dias_int)
    else:
        d["ClasseDias"] = "normal"
        d["DiasTexto"] = str(dias_int)

    d["BitAtivoTexto"] = "Ativo" if int(d.get("BitAtivo") or 0) == 1 else "Inativo"
    d["ClasseBitAtivo"] = "ativo" if int(d.get("BitAtivo") or 0) == 1 else "inativo"

    id_contrato_texto = str(d.get("IDFatoControleContratosEuromidia") or "").strip()
    d["IDFatoControleContratosExibicao"] = id_contrato_texto or "—"

    numero_contrato = str(d.get("NumeroContrato") or "").strip()
    if not numero_contrato:
        numero_contrato = id_contrato_texto

    d["NumeroContratoExibicao"] = numero_contrato or "—"

    valor_total_liquido = d.get("TotalLiquidoContrato")
    if valor_total_liquido is None:
        d["TotalLiquidoContratoTexto"] = "—"
    else:
        try:
            valor_float = float(valor_total_liquido)
            texto = f"R$ {valor_float:,.2f}"
            d["TotalLiquidoContratoTexto"] = texto.replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            d["TotalLiquidoContratoTexto"] = str(valor_total_liquido)

    return d


def _campanhas_vencimentos_opcoes_marca(
    usuario_logado_eh_vendedor: bool,
    id_usuario_logado: int | None,
) -> list[str]:
    """Busca as marcas disponíveis respeitando a restrição do perfil VENDEDOR."""

    marca_sql = _campanhas_vencimentos_marca_sql()
    filtros_sql: list[str] = [
        f"NULLIF(LTRIM(RTRIM({marca_sql})), '') IS NOT NULL",
        "ISNULL(venc.BitAtivo, 1) = 1",
    ]
    params = {"id_usuario_logado": int(id_usuario_logado or 0)}

    if usuario_logado_eh_vendedor:
        filtros_sql.append("ISNULL(vend.IDDimUsuarios, 0) = :id_usuario_logado")

    where_sql = " AND ".join(f"({item})" for item in filtros_sql)

    sql = text(f"""
        SELECT DISTINCT
            MarcaExibida = {marca_sql}
        FROM [Integracao].[Silver].[FatoVencimentoCampanhaEuromidia] AS venc
        INNER JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
            ON ctr.IDFatoControleContratosEuromidia = venc.IDFatoControleContratosEuromidia
        INNER JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item
            ON item.IDFatoControleContratosItensEuromidia = venc.IDFatoControleContratosItensEuromidia
           AND item.IDFatoControleContratoEuromidia = venc.IDFatoControleContratosEuromidia
           AND ISNULL(item.BitAtivo, 0) = 1
        LEFT JOIN [Integracao].[dbo].[Vendedores] AS vend
            ON vend.IDVendedor = venc.IDVendedor
        WHERE {where_sql}
        ORDER BY MarcaExibida ASC
    """)

    rows = db.session.execute(sql, params).mappings().all()
    return [str(row.get("MarcaExibida") or "").strip() for row in rows if str(row.get("MarcaExibida") or "").strip()]




def _campanhas_vencimentos_opcoes_vendedor(
    usuario_logado_eh_admin: bool,
) -> list[dict]:
    """Busca os vendedores disponíveis para o filtro visível somente para Admin."""

    if not usuario_logado_eh_admin:
        return []

    sql = text("""
        SELECT DISTINCT
            vend.IDVendedor,
            vend.NomeVendedor
        FROM [Integracao].[Silver].[FatoVencimentoCampanhaEuromidia] AS venc
        INNER JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
            ON ctr.IDFatoControleContratosEuromidia = venc.IDFatoControleContratosEuromidia
        INNER JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item
            ON item.IDFatoControleContratosItensEuromidia = venc.IDFatoControleContratosItensEuromidia
           AND item.IDFatoControleContratoEuromidia = venc.IDFatoControleContratosEuromidia
           AND ISNULL(item.BitAtivo, 0) = 1
        LEFT JOIN [Integracao].[dbo].[Vendedores] AS vend
            ON vend.IDVendedor = venc.IDVendedor
        WHERE vend.IDVendedor IS NOT NULL
          AND NULLIF(LTRIM(RTRIM(vend.NomeVendedor)), '') IS NOT NULL
          AND ISNULL(venc.BitAtivo, 1) = 1
        ORDER BY vend.NomeVendedor ASC
    """)

    rows = db.session.execute(sql).mappings().all()
    vendedores = []

    for row in rows:
        try:
            id_vendedor = int(row.get("IDVendedor") or 0)
        except Exception:
            id_vendedor = 0

        nome_vendedor = str(row.get("NomeVendedor") or "").strip()
        if id_vendedor <= 0 or not nome_vendedor:
            continue

        vendedores.append({
            "IDVendedor": id_vendedor,
            "NomeVendedor": nome_vendedor,
        })

    return vendedores


def _campanhas_vencimentos_data_json(valor):
    """Formata datas para o JSON de sugestões."""

    if not valor:
        return None
    try:
        return valor.strftime("%d/%m/%Y")
    except Exception:
        return str(valor)


def _campanhas_vencimentos_status_dias_cache_key() -> str:
    """Chave diária para não recalcular status/dias em todo carregamento da tela."""

    return f"vencimentos_campanhas_status_dias_{date.today().isoformat()}"


def _campanhas_vencimentos_cache_get_seguro(chave: str):
    """Lê o cache sem derrubar a tela caso o backend de cache falhe."""

    try:
        return cache.get(chave)
    except Exception:
        current_app.logger.warning(
            "VENCIMENTOS_CAMPANHAS | falha ao ler cache | chave=%s",
            chave,
            exc_info=True,
        )
        return None


def _campanhas_vencimentos_cache_set_seguro(chave: str, valor, timeout_segundos: int) -> None:
    """Grava o cache sem derrubar a tela caso o backend de cache falhe."""

    try:
        cache.set(chave, valor, timeout=timeout_segundos)
    except Exception:
        current_app.logger.warning(
            "VENCIMENTOS_CAMPANHAS | falha ao gravar cache | chave=%s",
            chave,
            exc_info=True,
        )


def _campanhas_vencimentos_atualizar_status_e_dias(forcar: bool = False) -> None:
    """
    Atualiza automaticamente os campos de acompanhamento da campanha.

    Importante para performance:
    - antes esta atualização rodava em todo GET da tela;
    - agora ela roda uma vez por dia por processo/cache, porque a própria tela informa
      que os prazos são atualizados diariamente;
    - se precisar recalcular manualmente, chame com forcar=True.

    Regras:
    - DiasParaVencer corre conforme a data atual.
    - IDDimStatusCampanha acompanha a situação real.
    - BitAtivo vira 0 quando a campanha já terminou por data.
    - Linhas canceladas manualmente, com BitAtivo = 0 antes do vencimento, ficam como CANCELADA.
    - DataAtualizacao só muda quando algum valor realmente precisar mudar.
    """

    chave_cache = _campanhas_vencimentos_status_dias_cache_key()
    if not forcar and _campanhas_vencimentos_cache_get_seguro(chave_cache):
        return

    sql = text("""
        SET NOCOUNT ON;

        DECLARE @Hoje DATE = CAST(SYSDATETIME() AS DATE);

        DECLARE @IDStatusFutura INT;
        DECLARE @IDStatusAtiva INT;
        DECLARE @IDStatusVencendo INT;
        DECLARE @IDStatusVencida INT;
        DECLARE @IDStatusCancelada INT;
        DECLARE @IDStatusSemDataTermino INT;
        DECLARE @IDStatusRenovada INT = 8;

        SELECT @IDStatusFutura = IDDimStatusCampanha
        FROM [Integracao].[Silver].[DimStatusCampanha]
        WHERE NomeStatus = N'CAMPANHA FUTURA';

        SELECT @IDStatusAtiva = IDDimStatusCampanha
        FROM [Integracao].[Silver].[DimStatusCampanha]
        WHERE NomeStatus = N'CAMPANHA ATIVA';

        SELECT @IDStatusVencendo = IDDimStatusCampanha
        FROM [Integracao].[Silver].[DimStatusCampanha]
        WHERE NomeStatus = N'CAMPANHA VENCENDO';

        SELECT @IDStatusVencida = IDDimStatusCampanha
        FROM [Integracao].[Silver].[DimStatusCampanha]
        WHERE NomeStatus = N'CAMPANHA VENCIDA';

        SELECT @IDStatusCancelada = IDDimStatusCampanha
        FROM [Integracao].[Silver].[DimStatusCampanha]
        WHERE NomeStatus = N'CANCELADA';

        SELECT @IDStatusSemDataTermino = IDDimStatusCampanha
        FROM [Integracao].[Silver].[DimStatusCampanha]
        WHERE NomeStatus = N'SEM DATA TERMINO';

        ;WITH StatusCalculado AS
        (
            SELECT
                f.IDFatoVencimentoCampanhaEuromidia,

                DiasParaVencerCalculado =
                    CASE
                        WHEN f.DataTerminoPrevisto IS NULL THEN NULL
                        ELSE DATEDIFF(DAY, @Hoje, CAST(f.DataTerminoPrevisto AS DATE))
                    END,

                BitAtivoCalculado =
                    CASE
                        WHEN ISNULL(f.BitAtivo, 1) = 0 THEN 0
                        WHEN f.DataTerminoPrevisto IS NOT NULL
                             AND CAST(f.DataTerminoPrevisto AS DATE) < @Hoje THEN 0
                        ELSE 1
                    END,

                IDDimStatusCampanhaCalculado =
                    CASE
                        WHEN ISNULL(f.BitAtivo, 1) = 0
                             AND f.IDDimStatusCampanha = @IDStatusRenovada THEN @IDStatusRenovada

                        WHEN ISNULL(f.BitAtivo, 1) = 0
                             AND (
                                    f.DataTerminoPrevisto IS NULL
                                    OR CAST(f.DataTerminoPrevisto AS DATE) >= @Hoje
                                 ) THEN @IDStatusCancelada

                        WHEN f.DataTerminoPrevisto IS NULL THEN @IDStatusSemDataTermino

                        WHEN f.DataInicioCampanha IS NOT NULL
                             AND CAST(f.DataInicioCampanha AS DATE) > @Hoje THEN @IDStatusFutura

                        WHEN CAST(f.DataTerminoPrevisto AS DATE) < @Hoje THEN @IDStatusVencida

                        WHEN DATEDIFF(DAY, @Hoje, CAST(f.DataTerminoPrevisto AS DATE)) BETWEEN 0 AND 45 THEN @IDStatusVencendo

                        ELSE @IDStatusAtiva
                    END
            FROM [Integracao].[Silver].[FatoVencimentoCampanhaEuromidia] AS f
        )
        UPDATE f
        SET
            f.DiasParaVencer = sc.DiasParaVencerCalculado,
            f.IDDimStatusCampanha = COALESCE(sc.IDDimStatusCampanhaCalculado, f.IDDimStatusCampanha),
            f.BitAtivo = sc.BitAtivoCalculado,
            f.DataAtualizacao = SYSDATETIME()
        FROM [Integracao].[Silver].[FatoVencimentoCampanhaEuromidia] AS f
        INNER JOIN StatusCalculado AS sc
            ON sc.IDFatoVencimentoCampanhaEuromidia = f.IDFatoVencimentoCampanhaEuromidia
        WHERE
            ISNULL(f.DiasParaVencer, -999999) <> ISNULL(sc.DiasParaVencerCalculado, -999999)
            OR ISNULL(f.IDDimStatusCampanha, -1) <> ISNULL(sc.IDDimStatusCampanhaCalculado, -1)
            OR ISNULL(f.BitAtivo, 1) <> ISNULL(sc.BitAtivoCalculado, 1);
    """)

    try:
        db.session.execute(sql)
        db.session.commit()
        _campanhas_vencimentos_cache_set_seguro(
            chave_cache,
            True,
            timeout_segundos=60 * 60 * 30,
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao atualizar status/dias da tela de vencimentos de campanha."
        )
        raise



def _campanhas_vencimentos_bisemanas_select(
    *,
    dt_ini_base: date | None,
    dt_fim_base: date | None,
) -> list[dict]:
    """Monta as opções de bi-semana para o seletor de período da tela."""

    hoje = date.today()
    inicio_base = dt_ini_base or date(hoje.year, hoje.month, 1)
    fim_base = dt_fim_base or hoje

    if inicio_base and fim_base and inicio_base > fim_base:
        inicio_base, fim_base = fim_base, inicio_base

    try:
        rows = db.session.execute(
            text("""
                SELECT
                    c.bi_semana_numero,
                    c.inicio_bi_semana,
                    c.fim_bi_semana
                FROM [Integracao].[Silver].[DimCalendario] AS c
                WHERE c.bi_semana_numero IS NOT NULL
                  AND c.inicio_bi_semana IS NOT NULL
                  AND c.fim_bi_semana IS NOT NULL
                  AND c.inicio_bi_semana <= :dt_fim_base
                  AND c.fim_bi_semana >= :dt_ini_base
                GROUP BY
                    c.bi_semana_numero,
                    c.inicio_bi_semana,
                    c.fim_bi_semana
                ORDER BY c.inicio_bi_semana ASC
            """),
            {
                "dt_ini_base": inicio_base,
                "dt_fim_base": fim_base,
            },
        ).mappings().all()
    except Exception:
        current_app.logger.exception("Falha ao carregar bi-semanas da tela de vencimentos de campanhas.")
        return []

    retorno: list[dict] = []
    vistos: set[tuple] = set()

    for row in rows:
        numero = row.get("bi_semana_numero")
        inicio = row.get("inicio_bi_semana")
        fim = row.get("fim_bi_semana")

        if numero is None or inicio is None or fim is None:
            continue

        try:
            value = str(int(numero))
        except Exception:
            value = str(numero).strip()

        if not value:
            continue

        chave = (value, inicio, fim)
        if chave in vistos:
            continue
        vistos.add(chave)

        try:
            inicio_iso = inicio.strftime("%Y-%m-%d")
            fim_iso = fim.strftime("%Y-%m-%d")
            label = f"{value} — {inicio.strftime('%d/%m')} a {fim.strftime('%d/%m')}"
        except Exception:
            inicio_iso = str(inicio)[:10]
            fim_iso = str(fim)[:10]
            label = f"{value} — {inicio_iso} a {fim_iso}"

        retorno.append({
            "value": value,
            "label": label,
            "inicio": inicio_iso,
            "fim": fim_iso,
        })

    return retorno





def _campanhas_vencimentos_partes_objeto_sql(tabela: str) -> tuple[str, str, str]:
    """Converte [Banco].[Schema].[Tabela] em partes seguras para consultar catálogo do SQL Server."""

    nome = str(tabela or "").replace("[", "").replace("]", "").strip()
    partes = [p.strip() for p in nome.split(".") if p.strip()]

    if len(partes) == 3:
        return partes[0], partes[1], partes[2]

    if len(partes) == 2:
        return "", partes[0], partes[1]

    if len(partes) == 1:
        return "", "dbo", partes[0]

    return "", "", ""


def _campanhas_vencimentos_coluna_existe(tabela: str, coluna: str) -> bool:
    """Verifica coluna em tabelas cross-database sem depender do blueprint do Kanban."""

    banco, schema, nome_tabela = _campanhas_vencimentos_partes_objeto_sql(tabela)
    if banco not in {"Kanban", "Integracao"}:
        return False

    sql = text(f"""
        SELECT TOP (1) 1
        FROM [{banco}].sys.columns AS c
        INNER JOIN [{banco}].sys.objects AS o
            ON o.object_id = c.object_id
        INNER JOIN [{banco}].sys.schemas AS s
            ON s.schema_id = o.schema_id
        WHERE s.name = :schema
          AND o.name = :nome_tabela
          AND c.name = :nome_coluna;
    """)

    try:
        return bool(db.session.execute(
            sql,
            {
                "schema": schema,
                "nome_tabela": nome_tabela,
                "nome_coluna": str(coluna or "").strip(),
            },
        ).scalar())
    except Exception:
        current_app.logger.exception(
            "Falha ao verificar coluna. tabela=%s coluna=%s",
            tabela,
            coluna,
        )
        return False


def _campanhas_vencimentos_primeira_fase_kanban_renovacao() -> int:
    """Busca a primeira fase ativa do Kanban 1 para receber o card de renovação."""

    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                IDDimKanbanFase
            FROM {TABELA_KANBAN_FASE_RENOVACAO}
            WHERE IDDimKanban = :id_kanban
              AND ISNULL(Ativo, 1) = 1
            ORDER BY
                ISNULL(OrdemFase, 999999) ASC,
                IDDimKanbanFase ASC;
        """),
        {"id_kanban": int(ID_KANBAN_RENOVACAO_CAMPANHA)},
    ).mappings().first()

    id_fase = int((row or {}).get("IDDimKanbanFase") or 0)
    if id_fase <= 0:
        raise RuntimeError("Não encontrei fase ativa no Kanban 1 para criar o card de renovação.")

    return id_fase


def _campanhas_vencimentos_id_status_card_ativo_ou_none() -> int | None:
    """Resolve o IDDimKanbanStatusCard para o código ATIVO quando a coluna existir."""

    if not _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDDimKanbanStatusCard"):
        return None

    try:
        row = db.session.execute(
            text(f"""
                SELECT TOP (1)
                    IDDimKanbanStatusCard
                FROM {TABELA_KANBAN_STATUS_CARD_RENOVACAO}
                WHERE UPPER(LTRIM(RTRIM(ISNULL(CodigoStatus, '')))) = 'ATIVO'
                  AND ISNULL(Ativo, 1) = 1
                ORDER BY IDDimKanbanStatusCard ASC;
            """),
        ).mappings().first()
    except Exception:
        current_app.logger.exception("Falha ao buscar IDDimKanbanStatusCard ATIVO para renovação de campanha.")
        return None

    id_status = int((row or {}).get("IDDimKanbanStatusCard") or 0)
    return id_status or None


def _campanhas_vencimentos_coluna_empresa_card_ou_none() -> str | None:
    """Mantém a mesma regra do Kanban: IDEmpresa é a coluna oficial do cliente no card."""

    for nome_coluna in ("IDEmpresa", "IDCliente", "IDEmpresaRelacionada"):
        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, nome_coluna):
            return nome_coluna
    return None



def _campanhas_vencimentos_buscar_base_renovacao(id_vencimento: int) -> dict | None:
    """
    Busca a base oficial da renovação seguindo a cadeia correta:

    1) FatoVencimentoCampanhaEuromidia
    2) FatoControleContratosEuromidia pelo IDFatoControleContratosEuromidia
    3) FatoControleContratosItensEuromidia pelo IDFatoControleContratosItensEuromidia
    4) DimEmpresas pelo IDEmpresa
    5) DimCnaes pelo CNAE = cnaepadrao

    Observação:
    - O CodPonto/CodFace vêm do item oficial do contrato.
    - Inserções/dia vem primeiro da Cota do item.
    - Período de campanha vem primeiro do TexmpoExposicao do item.
    - A tabela de preço é usada apenas como complemento, sem substituir a origem oficial.
    """

    sql = text("""
        SELECT TOP (1)
            venc.IDFatoVencimentoCampanhaEuromidia,
            venc.IDFatoControleContratosEuromidia,
            venc.IDFatoControleContratosItensEuromidia,
            venc.IDDimStatusCampanha,
            venc.IDVendedor,
            vend.NomeVendedor,
            vend.IDDimUsuarios AS IDDimUsuariosVendedor,

            IDEmpresa = COALESCE(venc.IDEmpresa, ctr.IDEmpresa),
            MarcaExibida = COALESCE(
                NULLIF(LTRIM(RTRIM(venc.MarcaExibida)), ''),
                NULLIF(LTRIM(RTRIM(item.MarcaExibida)), ''),
                NULLIF(LTRIM(RTRIM(ctr.MarcaExibida)), ''),
                NULLIF(LTRIM(RTRIM(emp.NomeFantasia)), ''),
                NULLIF(LTRIM(RTRIM(emp.RazaoSocial)), '')
            ),

            DataInicioCampanha = COALESCE(
                CAST(venc.DataInicioCampanha AS date),
                CAST(item.DataInicioPrevisto AS date)
            ),
            DataTerminoPrevisto = COALESCE(
                CAST(venc.DataTerminoPrevisto AS date),
                CAST(item.DataTerminoPrevisto AS date),
                CAST(item.DataFimEfetiva AS date)
            ),
            venc.DiasParaVencer,
            venc.BitAtivo,

            ctr.NumeroContrato,
            ctr.NumeroPrevia,
            RazaoSocial = COALESCE(
                NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), emp.RazaoSocial))), ''), '0'),
                NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), ctr.RazaoSocial))), ''), '0'),
                NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), emp.NomeFantasia))), ''), '0'),
                NULLIF(NULLIF(LTRIM(RTRIM(CONVERT(varchar(300), ctr.MarcaExibida))), ''), '0')
            ),
            ctr.IDEmpresaAgencia,
            ctr.IDEmpresaBureau,
            ctr.IDEmpresaIntermediario,

            emp.NomeFantasia,
            emp.CNPJ,
            emp.Email,
            Telefone = COALESCE(NULLIF(LTRIM(RTRIM(emp.TelefoneContato1)), ''), NULLIF(LTRIM(RTRIM(emp.TelefoneContato2)), '')),
            emp.CNAE,
            emp.IDDimOrigemAtendimento AS IDDimOrigemAtendimentoEmpresa,

            cnae.IDDimCnaes,
            cnae.Classe AS ClasseCnae,
            cnae.Setor AS SetorCnae,
            cnae.MacroSetor AS MacroSetorCnae,
            cnae.SubClasse AS SubClasseCnae,

            CodPonto = NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), item.CodPonto))), ''),
            CodFace = NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), item.CodFace))), ''),
            item.IDPainelEuromidia,
            item.IDDimFacesPaineis,
            item.Tipo AS TipoPainelItem,
            item.Cota,
            item.TexmpoExposicao,
            item.CidadeExibicao,
            item.IDFatoKanbanCard AS IDFatoKanbanCardOrigem,
            item.IDDimOrigemAtendimento AS IDDimOrigemAtendimentoItem,
            item.IDDimTipoDocumento,
            item.FaturamentoBrutoMensal,
            item.FaturamentoLiquidoMensal,
            item.FaturamentoLiquidoFinalMensal,
            item.TotalBrutoContrato,
            item.TotalLiquidoContratoAGBRCTACORDO,
            item.TotalLiquidoContratoAGBRVENDGERCOOR,
            item.NumeroParcelas,
            item.DataInicioVencimento,

            painel.Tipo AS TipoPainelCadastro,
            face.Tipo AS TipoFaceCadastro,

            tp.IDDimTabelaPrecosEuromidia AS IDDimTabelaPrecosEuromidiaTabela,
            tp.PeriodoExibicao AS PeriodoExibicaoTabela,
            tp.ExibicoesDia AS ExibicoesDiaTabela,
            tp.Valor AS ValorTabelaPreco,
            tp.Tabela AS TabelaPreco,
            tp.PoliticaTrocas AS PoliticaTrocasTabela,
            tp.ValorTroca AS ValorTrocaTabela,

            PeriodoExibicaoContrato = NULLIF(LTRIM(RTRIM(CONVERT(varchar(120), item.TexmpoExposicao))), ''),
            ExibicoesDiaContrato = TRY_CONVERT(int, item.Cota)

        FROM [Integracao].[Silver].[FatoVencimentoCampanhaEuromidia] AS venc

        INNER JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS ctr
            ON ctr.IDFatoControleContratosEuromidia = venc.IDFatoControleContratosEuromidia

        INNER JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item
            ON item.IDFatoControleContratosItensEuromidia = venc.IDFatoControleContratosItensEuromidia
           AND item.IDFatoControleContratoEuromidia = venc.IDFatoControleContratosEuromidia

        LEFT JOIN [Integracao].[Silver].[DimEmpresas] AS emp
            ON emp.IDEmpresa = COALESCE(venc.IDEmpresa, ctr.IDEmpresa)

        LEFT JOIN [Integracao].[Silver].[DimCnaes] AS cnae
            ON LTRIM(RTRIM(CONVERT(varchar(30), emp.CNAE))) COLLATE Latin1_General_CI_AI
             = LTRIM(RTRIM(CONVERT(varchar(30), cnae.cnaepadrao))) COLLATE Latin1_General_CI_AI

        LEFT JOIN [Integracao].[dbo].[Vendedores] AS vend
            ON vend.IDVendedor = venc.IDVendedor

        LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] AS painel
            ON painel.IDDimPaineisEuromidia = item.IDPainelEuromidia

        LEFT JOIN [Integracao].[Silver].[DimFacesPaineis] AS face
            ON face.IDDimFacesPaineis = item.IDDimFacesPaineis

        OUTER APPLY (
            SELECT TOP (1)
                tabela_preco.IDDimTabelaPrecosEuromidia,
                tabela_preco.PeriodoExibicao,
                tabela_preco.ExibicoesDia,
                tabela_preco.Valor,
                tabela_preco.Tabela,
                tabela_preco.PoliticaTrocas,
                tabela_preco.ValorTroca
            FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] AS tabela_preco
            WHERE ISNULL(tabela_preco.BitAtivo, 1) = 1
              AND (
                    item.IDPainelEuromidia IS NULL
                    OR tabela_preco.IDDimPaineisEuromidia = item.IDPainelEuromidia
                  )
              AND (
                    item.IDDimFacesPaineis IS NULL
                    OR tabela_preco.IDDimFacesPaineis = item.IDDimFacesPaineis
                  )
            ORDER BY
                CASE
                    WHEN item.Cota IS NOT NULL
                     AND TRY_CONVERT(varchar(80), tabela_preco.ExibicoesDia) = TRY_CONVERT(varchar(80), item.Cota)
                    THEN 0 ELSE 1
                END,
                CASE
                    WHEN NULLIF(LTRIM(RTRIM(CONVERT(varchar(120), item.TexmpoExposicao))), '') IS NOT NULL
                     AND LTRIM(RTRIM(CONVERT(varchar(120), tabela_preco.PeriodoExibicao))) COLLATE Latin1_General_CI_AI
                       = LTRIM(RTRIM(CONVERT(varchar(120), item.TexmpoExposicao))) COLLATE Latin1_General_CI_AI
                    THEN 0 ELSE 1
                END,
                ISNULL(tabela_preco.DataPublicacao, tabela_preco.DataAtualizacao) DESC,
                tabela_preco.IDDimTabelaPrecosEuromidia DESC
        ) AS tp

        WHERE venc.IDFatoVencimentoCampanhaEuromidia = :id_vencimento
          AND ISNULL(item.BitAtivo, 0) = 1;
    """)

    row = db.session.execute(sql, {"id_vencimento": int(id_vencimento)}).mappings().first()
    return dict(row) if row else None

def _campanhas_vencimentos_formatar_data_pt(valor) -> str:
    if not valor:
        return "—"
    try:
        return valor.strftime("%d/%m/%Y")
    except Exception:
        return str(valor)


def _campanhas_vencimentos_primeiro_valor_preenchido(*valores):
    """Devolve o primeiro valor realmente preenchido, preservando zero quando ele for válido."""

    for valor in valores:
        if valor is None:
            continue

        if isinstance(valor, str):
            texto = valor.strip()
            if texto == "":
                continue
            return texto

        return valor

    return None


def _campanhas_vencimentos_titulo_card_renovacao(campanha: dict) -> str:
    id_contrato = int(campanha.get("IDFatoControleContratosEuromidia") or 0)
    nome_campanha = (
        str(campanha.get("MarcaExibida") or "").strip()
        or str(campanha.get("NomeFantasia") or "").strip()
        or str(campanha.get("RazaoSocial") or "").strip()
        or f"Contrato {id_contrato}"
    )

    titulo = f"Renovação Campanha {nome_campanha} - {id_contrato}"
    return titulo[:200]



def _campanhas_vencimentos_descricao_card_renovacao(
    *,
    campanha: dict,
    data_inicio_renovacao: date,
    data_fim_renovacao: date,
    prazo_dias: int,
) -> str:
    id_vencimento = int(campanha.get("IDFatoVencimentoCampanhaEuromidia") or 0)
    id_contrato = int(campanha.get("IDFatoControleContratosEuromidia") or 0)
    id_item = int(campanha.get("IDFatoControleContratosItensEuromidia") or 0)
    cod_ponto = str(campanha.get("CodPonto") or "").strip()
    cod_face = str(campanha.get("CodFace") or "").strip().upper()
    periodo_exibicao = _campanhas_vencimentos_primeiro_valor_preenchido(
        campanha.get("PeriodoExibicaoContrato"),
        campanha.get("PeriodoExibicaoTabela"),
    )
    exibicoes_dia = _campanhas_vencimentos_primeiro_valor_preenchido(
        campanha.get("ExibicoesDiaContrato"),
        campanha.get("ExibicoesDiaTabela"),
        campanha.get("Cota"),
    )

    linhas = [
        "RENOVAÇÃO DE CAMPANHA GERADA PELA TELA DE VENCIMENTOS",
        f"Origem técnica: RENOVACAO_CAMPANHA_ID_VENCIMENTO={id_vencimento}",
        "",
        f"Cliente: {str(campanha.get('RazaoSocial') or campanha.get('NomeFantasia') or '—').strip()}",
        f"CNPJ: {str(campanha.get('CNPJ') or '—').strip()}",
        f"Segmento: {str(campanha.get('ClasseCnae') or campanha.get('SetorCnae') or '—').strip()}",
        f"IDDimCnaes: {campanha.get('IDDimCnaes') or '—'}",
        f"Marca/Campanha: {str(campanha.get('MarcaExibida') or '—').strip()}",
        f"Contrato origem: {id_contrato}",
        f"Item origem: {id_item or '—'}",
        f"Número contrato: {str(campanha.get('NumeroContrato') or '—').strip()}",
        f"Número prévia: {str(campanha.get('NumeroPrevia') or '—').strip()}",
        f"CodPonto: {cod_ponto or '—'}",
        f"CodFace: {cod_face or '—'}",
        f"Inserções/dia: {exibicoes_dia or '—'}",
        f"Período de campanha: {periodo_exibicao or '—'}",
        f"Vendedor: {str(campanha.get('NomeVendedor') or '—').strip()}",
        "",
        "Período original:",
        f"- Início: {_campanhas_vencimentos_formatar_data_pt(campanha.get('DataInicioCampanha'))}",
        f"- Término: {_campanhas_vencimentos_formatar_data_pt(campanha.get('DataTerminoPrevisto'))}",
        f"- Prazo: {int(prazo_dias)} dia(s)",
        "",
        "Período sugerido para renovação:",
        f"- Data de início: {_campanhas_vencimentos_formatar_data_pt(data_inicio_renovacao)}",
        f"- Data até: {_campanhas_vencimentos_formatar_data_pt(data_fim_renovacao)}",
        f"- Regra aplicada: começa no primeiro dia após o término e mantém exatamente {int(prazo_dias)} dia(s).",
    ]

    id_reserva_preferencia = _parse_int(campanha.get("IDReservaPreferenciaRenovacao"))
    reserva_preferencia = campanha.get("ReservaPreferenciaRenovacao") or {}
    if id_reserva_preferencia:
        linhas.extend([
            "",
            "Reserva de preferência vinculada:",
            f"- IDReserva: {id_reserva_preferencia}",
            f"- Período da reserva: {_campanhas_vencimentos_formatar_data_pt(reserva_preferencia.get('DataInicio'))} até {_campanhas_vencimentos_formatar_data_pt(reserva_preferencia.get('DataFim'))}",
            "- Origem: PREFERENCIA RENOVAÇÃO CONTRATO",
        ])

    return "\n".join(linhas)


def _campanhas_vencimentos_buscar_reserva_preferencia_renovacao(campanha: dict) -> dict | None:
    """Busca a reserva automática de preferência vinculada ao item original da campanha.

    Regra oficial:
    - a reserva precisa ser da mesma linha de item do contrato;
    - Origem = RESERVA;
    - Status = RESERVADO;
    - CanceladoEm IS NULL;
    - TipoVinculoOrigem = PREFERENCIA RENOVAÇÃO CONTRATO.

    O campo chave é:
    FatoOcupacaoPaineisEuromidia.IDFatoControleContratosItemOrigem
    = FatoControleContratosItensEuromidia.IDFatoControleContratosItensEuromidia.
    """

    id_item_origem = _parse_int(campanha.get("IDFatoControleContratosItensEuromidia"))
    if not id_item_origem:
        return None

    id_contrato = _parse_int(campanha.get("IDFatoControleContratosEuromidia"))
    cod_ponto = str(campanha.get("CodPonto") or "").strip()
    cod_face = str(campanha.get("CodFace") or "").strip().upper()
    data_termino_original = campanha.get("DataTerminoPrevisto")

    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                   reserva.IDFatoOcupacaoPaineisEuromidia,
                   reserva.DataAtualizacao,
                   reserva.Referencia,
                   reserva.CodPonto,
                   reserva.CodFace,
                   reserva.IDPainelEuromidia,
                   reserva.Origem,
                   reserva.Status,
                   CAST(reserva.DataInicio AS date) AS DataInicio,
                   CAST(reserva.DataFim AS date) AS DataFim,
                   reserva.LoopInicio,
                   reserva.LoopFim,
                   reserva.SpanQtd,
                   reserva.Cota,
                   reserva.MarcaExibida,
                   reserva.Vendedor,
                   reserva.IDVendedor,
                   reserva.IDCliente,
                   reserva.IDFatoControleContratos,
                   reserva.NumeroContrato,
                   reserva.NumeroPrevia,
                   reserva.TextoOriginal,
                   reserva.CriadoEm,
                   reserva.CriadoPorIDUsuario,
                   reserva.ExpiraEm,
                   reserva.CanceladoEm,
                   reserva.CanceladoPorIDUsuario,
                   reserva.Observacao,
                   reserva.Dias,
                   reserva.ReservaOrdemPrioridade,
                   reserva.IDFatoOcupacaoOrigem,
                   reserva.IDFatoControleContratosItemOrigem,
                   reserva.TipoVinculoOrigem,
                   reserva.BitEmpresasRelacionadas
            FROM {TABELA_OCUPACAO_PAINEIS_EUROMIDIA_ADMIN} AS reserva WITH (NOLOCK)
            WHERE reserva.IDFatoControleContratosItemOrigem = :id_item_origem
              AND reserva.CanceladoEm IS NULL
              AND UPPER(LTRIM(RTRIM(ISNULL(reserva.Origem, '')))) COLLATE Latin1_General_CI_AI = 'RESERVA'
              AND UPPER(LTRIM(RTRIM(ISNULL(reserva.Status, '')))) COLLATE Latin1_General_CI_AI = 'RESERVADO'
              AND UPPER(LTRIM(RTRIM(ISNULL(reserva.TipoVinculoOrigem, '')))) COLLATE Latin1_General_CI_AI = 'PREFERENCIA RENOVAÇÃO CONTRATO'
              AND (
                    :id_contrato IS NULL
                    OR reserva.IDFatoControleContratos = :id_contrato
                    OR reserva.IDFatoControleContratos IS NULL
                  )
              AND (
                    :cod_ponto = ''
                    OR LTRIM(RTRIM(CONVERT(varchar(80), reserva.CodPonto))) = :cod_ponto
                  )
              AND (
                    :cod_face = ''
                    OR UPPER(LTRIM(RTRIM(ISNULL(reserva.CodFace, '')))) = :cod_face
                  )
            ORDER BY
                CASE
                    WHEN :data_termino_original IS NOT NULL
                     AND CAST(reserva.DataInicio AS date) >= DATEADD(DAY, 1, CAST(:data_termino_original AS date))
                    THEN 0 ELSE 1
                END,
                ISNULL(reserva.ReservaOrdemPrioridade, 999999),
                CAST(reserva.DataInicio AS date) ASC,
                reserva.IDFatoOcupacaoPaineisEuromidia DESC;
        """),
        {
            "id_item_origem": int(id_item_origem),
            "id_contrato": int(id_contrato) if id_contrato else None,
            "cod_ponto": cod_ponto,
            "cod_face": cod_face,
            "data_termino_original": data_termino_original,
        },
    ).mappings().first()

    return dict(row) if row else None


def _campanhas_vencimentos_preparar_campanha_com_reserva_preferencia(campanha: dict) -> dict | None:
    """Enriquece o dicionário da campanha com a reserva de preferência, quando existir."""

    reserva = _campanhas_vencimentos_buscar_reserva_preferencia_renovacao(campanha)
    if not reserva:
        campanha["IDReservaPreferenciaRenovacao"] = None
        campanha["ReservaPreferenciaRenovacao"] = None
        return None

    id_reserva = _parse_int(reserva.get("IDFatoOcupacaoPaineisEuromidia"))
    campanha["IDReservaPreferenciaRenovacao"] = id_reserva
    campanha["ReservaPreferenciaRenovacao"] = reserva
    campanha["DataInicioReservaPreferenciaRenovacao"] = reserva.get("DataInicio")
    campanha["DataFimReservaPreferenciaRenovacao"] = reserva.get("DataFim")

    return reserva


def _campanhas_vencimentos_vincular_reserva_preferencia_card_renovacao(
    *,
    id_card: int,
    campanha: dict,
    reserva: dict | None = None,
) -> dict:
    """Vincula a reserva de preferência ao card de renovação.

    Efeitos:
    1. grava FatoKanbanCard.IDReserva = IDFatoOcupacaoPaineisEuromidia da reserva;
    2. marca a reserva com [RESERVA_CARD_ATIVO=<id_card>] na Observacao.

    Essa marca é importante porque a API do Kanban usa o IDReserva do card + o marcador
    na reserva para preencher o campo Reserva no bloco Painel / Face do modal.
    """

    id_card_int = int(id_card or 0)
    if id_card_int <= 0:
        return {"ok": False, "motivo": "id_card_invalido"}

    reserva_final = reserva or campanha.get("ReservaPreferenciaRenovacao")
    if not reserva_final:
        reserva_final = _campanhas_vencimentos_buscar_reserva_preferencia_renovacao(campanha)

    id_reserva = _parse_int(
        (reserva_final or {}).get("IDFatoOcupacaoPaineisEuromidia")
        or campanha.get("IDReservaPreferenciaRenovacao")
    )

    if not id_reserva:
        return {"ok": True, "vinculada": False, "motivo": "sem_reserva_preferencia", "id_card": id_card_int}

    atualizou_card = False
    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDReserva"):
        campos_set = ["IDReserva = :id_reserva"]
        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "AtualizadoEm"):
            campos_set.append("AtualizadoEm = GETDATE()")

        db.session.execute(
            text(f"""
                UPDATE {TABELA_KANBAN_CARD_RENOVACAO}
                   SET {', '.join(campos_set)}
                 WHERE IDFatoKanbanCard = :id_card;
            """),
            {
                "id_card": int(id_card_int),
                "id_reserva": int(id_reserva),
            },
        )
        atualizou_card = True

    marcador_ativo = f"[RESERVA_CARD_ATIVO={int(id_card_int)}]"
    texto_vinculo = (
        f"{marcador_ativo} Reserva de preferência de renovação vinculada automaticamente "
        f"ao Card {int(id_card_int)} pela tela de vencimentos de campanhas."
    )

    db.session.execute(
        text(f"""
            UPDATE {TABELA_OCUPACAO_PAINEIS_EUROMIDIA_ADMIN}
               SET Observacao = CASE
                    WHEN CHARINDEX(:marcador_ativo, COALESCE(CONVERT(varchar(max), Observacao), '')) > 0
                        THEN Observacao
                    WHEN NULLIF(LTRIM(RTRIM(COALESCE(CONVERT(varchar(max), Observacao), ''))), '') IS NULL
                        THEN :texto_vinculo
                    ELSE CONCAT(CONVERT(varchar(max), Observacao), ' | ', :texto_vinculo)
                   END,
                   DataAtualizacao = GETDATE()
             WHERE IDFatoOcupacaoPaineisEuromidia = :id_reserva
               AND CanceladoEm IS NULL
               AND UPPER(LTRIM(RTRIM(ISNULL(Origem, '')))) COLLATE Latin1_General_CI_AI = 'RESERVA'
               AND UPPER(LTRIM(RTRIM(ISNULL(Status, '')))) COLLATE Latin1_General_CI_AI = 'RESERVADO'
               AND UPPER(LTRIM(RTRIM(ISNULL(TipoVinculoOrigem, '')))) COLLATE Latin1_General_CI_AI = 'PREFERENCIA RENOVAÇÃO CONTRATO';
        """),
        {
            "id_reserva": int(id_reserva),
            "marcador_ativo": marcador_ativo,
            "texto_vinculo": texto_vinculo,
        },
    )

    current_app.logger.info(
        "RENOVACAO_CAMPANHA_RESERVA | card=%s | reserva=%s | item_origem=%s | contrato=%s | atualizou_card=%s",
        id_card_int,
        id_reserva,
        campanha.get("IDFatoControleContratosItensEuromidia"),
        campanha.get("IDFatoControleContratosEuromidia"),
        atualizou_card,
    )

    return {
        "ok": True,
        "vinculada": True,
        "id_card": id_card_int,
        "id_reserva": int(id_reserva),
        "atualizou_card": atualizou_card,
    }

def _campanhas_vencimentos_card_renovacao_existente(campanha: dict) -> int | None:
    """Evita criar duplicidade se o usuário clicar em Renovar mais de uma vez."""

    id_vencimento = int(campanha.get("IDFatoVencimentoCampanhaEuromidia") or 0)
    if id_vencimento <= 0:
        return None

    marcador = f"%RENOVACAO_CAMPANHA_ID_VENCIMENTO={id_vencimento}%"
    row = db.session.execute(
        text(f"""
            SELECT TOP (1)
                c.IDFatoKanbanCard
            FROM {TABELA_KANBAN_CARD_RENOVACAO} AS c
            INNER JOIN {TABELA_KANBAN_CARD_TAG_RENOVACAO} AS ct
                ON ct.IDFatoKanbanCard = c.IDFatoKanbanCard
               AND ct.IDDimKanbanTag = :id_tag
               AND ct.RemovidoEm IS NULL
            WHERE c.IDDimKanban = :id_kanban
              AND ISNULL(c.Ativo, 1) = 1
              AND ISNULL(c.Descricao, '') LIKE :marcador
            ORDER BY c.IDFatoKanbanCard DESC;
        """),
        {
            "id_tag": int(ID_TAG_RENOVACAO_CAMPANHA),
            "id_kanban": int(ID_KANBAN_RENOVACAO_CAMPANHA),
            "marcador": marcador,
        },
    ).mappings().first()

    id_card = int((row or {}).get("IDFatoKanbanCard") or 0)
    return id_card or None


def _campanhas_vencimentos_invalidar_cache_kanban_renovacao(*, id_kanban: int, id_empresa_proprietaria: int, id_card: int) -> None:
    """Bumpa as mesmas chaves de versão usadas pelo Kanban para não carregar cache antigo."""

    def _inc(chave: str) -> None:
        try:
            valor_atual = cache.get(chave)
            try:
                valor_atual = int(valor_atual)
            except Exception:
                valor_atual = 1
            cache.set(chave, valor_atual + 1, timeout=30000)
        except Exception:
            current_app.logger.exception("Falha ao invalidar cache do Kanban. chave=%s", chave)

    _inc(f"kanban:versao:empresa:{int(id_empresa_proprietaria)}")
    _inc(f"kanban:versao:kanban:{int(id_kanban)}")
    _inc(f"kanban:versao:card:{int(id_card)}")



def _campanhas_vencimentos_criar_card_renovacao(
    *,
    campanha: dict,
    data_inicio_renovacao: date,
    data_fim_renovacao: date,
    prazo_dias: int,
) -> int:
    """Cria o card de renovação no Kanban 1 e aplica a tag 17 Renovação."""

    id_usuario = int(_campanhas_vencimentos_usuario_logado_id() or 0)
    if id_usuario <= 0:
        raise RuntimeError("Não consegui identificar o usuário logado para criar o card de renovação.")

    id_usuario_responsavel = _parse_int(campanha.get("IDDimUsuariosVendedor")) or id_usuario

    tag_renovacao = db.session.execute(
        text(f"""
            SELECT TOP (1)
                IDDimKanbanTag,
                NomeTag
            FROM {TABELA_KANBAN_TAG_RENOVACAO}
            WHERE IDDimKanbanTag = :id_tag
              AND IDDimKanban = :id_kanban
              AND ISNULL(Ativo, 1) = 1;
        """),
        {
            "id_tag": int(ID_TAG_RENOVACAO_CAMPANHA),
            "id_kanban": int(ID_KANBAN_RENOVACAO_CAMPANHA),
        },
    ).mappings().first()

    if not tag_renovacao:
        raise RuntimeError("Tag 17 Renovação não encontrada ou inativa no Kanban 1.")

    id_fase_inicial = _campanhas_vencimentos_primeira_fase_kanban_renovacao()
    id_status_card = _campanhas_vencimentos_id_status_card_ativo_ou_none()
    id_empresa_proprietaria = int(ID_EMPRESA_PROPRIETARIA_EUROMIDIA_RENOVACAO)

    id_empresa = _parse_int(campanha.get("IDEmpresa"))
    id_vendedor = _parse_int(campanha.get("IDVendedor"))
    id_contrato = _parse_int(campanha.get("IDFatoControleContratosEuromidia"))
    id_dim_cnaes = _parse_int(campanha.get("IDDimCnaes"))
    id_origem_atendimento = _parse_int(
        _campanhas_vencimentos_primeiro_valor_preenchido(
            campanha.get("IDDimOrigemAtendimentoItem"),
            campanha.get("IDDimOrigemAtendimentoEmpresa"),
        )
    )
    id_tipo_documento = _parse_int(campanha.get("IDDimTipoDocumento"))
    id_reserva_preferencia = _parse_int(campanha.get("IDReservaPreferenciaRenovacao"))

    cod_ponto = str(campanha.get("CodPonto") or "").strip()
    cod_face = str(campanha.get("CodFace") or "").strip().upper()
    marca = str(campanha.get("MarcaExibida") or "").strip() or None
    nome_empresa = str(campanha.get("RazaoSocial") or campanha.get("NomeFantasia") or "").strip() or None
    telefone = str(campanha.get("Telefone") or "").strip() or None
    email = str(campanha.get("Email") or "").strip() or None

    titulo = _campanhas_vencimentos_titulo_card_renovacao(campanha)
    descricao = _campanhas_vencimentos_descricao_card_renovacao(
        campanha=campanha,
        data_inicio_renovacao=data_inicio_renovacao,
        data_fim_renovacao=data_fim_renovacao,
        prazo_dias=int(prazo_dias),
    )

    colunas = [
        "IDDimKanban",
        "IDDimKanbanFaseAtual",
        "Titulo",
        "Descricao",
        "StatusCard",
        "CriadoEm",
        "Ativo",
        "IDEmpresaProprietaria",
    ]
    valores = [
        ":id_kanban",
        ":id_fase",
        ":titulo",
        ":descricao",
        ":status_card",
        "GETDATE()",
        "1",
        ":id_empresa_proprietaria",
    ]
    params = {
        "id_kanban": int(ID_KANBAN_RENOVACAO_CAMPANHA),
        "id_fase": int(id_fase_inicial),
        "titulo": titulo,
        "descricao": descricao,
        "status_card": STATUS_CARD_RENOVACAO_PADRAO,
        "id_empresa_proprietaria": id_empresa_proprietaria,
    }

    def adicionar_coluna_se_existir(nome_coluna: str, nome_parametro: str, valor) -> None:
        if not _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, nome_coluna):
            return
        colunas.append(nome_coluna)
        valores.append(f":{nome_parametro}")
        params[nome_parametro] = valor

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "AtualizadoEm"):
        colunas.append("AtualizadoEm")
        valores.append("GETDATE()")

    adicionar_coluna_se_existir("IDVendedorUsuario", "id_usuario_responsavel", id_usuario_responsavel)
    adicionar_coluna_se_existir("IDDimUsuarios", "id_usuario_responsavel", id_usuario_responsavel)
    adicionar_coluna_se_existir("IDUsuarioCriacao", "id_usuario", id_usuario)
    adicionar_coluna_se_existir("IDVendedor", "id_vendedor", id_vendedor)

    coluna_empresa = _campanhas_vencimentos_coluna_empresa_card_ou_none()
    if coluna_empresa:
        colunas.append(coluna_empresa)
        valores.append(":id_empresa")
        params["id_empresa"] = id_empresa

    adicionar_coluna_se_existir("IDDimCnaes", "id_dim_cnaes", id_dim_cnaes)
    adicionar_coluna_se_existir("IDDimOrigemAtendimento", "id_origem_atendimento", id_origem_atendimento)
    adicionar_coluna_se_existir("IDDimTipoDocumento", "id_tipo_documento", id_tipo_documento)

    adicionar_coluna_se_existir("IDEmpresaAgencia", "id_empresa_agencia", _parse_int(campanha.get("IDEmpresaAgencia")))
    adicionar_coluna_se_existir("IDEmpresaBureau", "id_empresa_bureau", _parse_int(campanha.get("IDEmpresaBureau")))
    adicionar_coluna_se_existir("IDEmpresaIntermediario", "id_empresa_intermediario", _parse_int(campanha.get("IDEmpresaIntermediario")))

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "BitAditivo"):
        colunas.append("BitAditivo")
        valores.append("1")

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "BitContratoNovo"):
        colunas.append("BitContratoNovo")
        valores.append("0")

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDFatoControleContratosEuromidia"):
        adicionar_coluna_se_existir("IDFatoControleContratosEuromidia", "id_contrato", id_contrato)
    elif _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDFatoControleContratoEuromidia"):
        adicionar_coluna_se_existir("IDFatoControleContratoEuromidia", "id_contrato", id_contrato)

    adicionar_coluna_se_existir("CodPontoContrato", "cod_ponto", cod_ponto or None)
    adicionar_coluna_se_existir("CodFaceContrato", "cod_face", cod_face or None)
    adicionar_coluna_se_existir("IDReserva", "id_reserva_preferencia", id_reserva_preferencia)
    adicionar_coluna_se_existir("Marca", "marca", marca)
    adicionar_coluna_se_existir("NomeEmpresa", "nome_empresa", nome_empresa)
    adicionar_coluna_se_existir("Telefone", "telefone", telefone)
    adicionar_coluna_se_existir("Email", "email", email)

    if id_status_card is not None:
        adicionar_coluna_se_existir("IDDimKanbanStatusCard", "id_status_card", int(id_status_card))

    sql_insert = text(f"""
        INSERT INTO {TABELA_KANBAN_CARD_RENOVACAO}
            ({', '.join(colunas)})
        OUTPUT INSERTED.IDFatoKanbanCard
        VALUES
            ({', '.join(valores)});
    """)

    id_card = int(db.session.execute(sql_insert, params).scalar() or 0)
    if id_card <= 0:
        raise RuntimeError("O INSERT do card de renovação não retornou IDFatoKanbanCard.")

    _campanhas_vencimentos_vincular_reserva_preferencia_card_renovacao(
        id_card=int(id_card),
        campanha=campanha,
    )

    _aplicar_tag_no_card_admin(
        id_card=int(id_card),
        id_tag=int(ID_TAG_RENOVACAO_CAMPANHA),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )
    _aplicar_tag_no_card_admin(
        id_card=int(id_card),
        id_tag=int(ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )

    if cod_face:
        _campanhas_vencimentos_inserir_painel_face_card_renovacao(
            id_card=id_card,
            campanha=campanha,
            data_inicio_renovacao=data_inicio_renovacao,
            data_fim_renovacao=data_fim_renovacao,
        )

    _campanhas_vencimentos_invalidar_cache_kanban_renovacao(
        id_kanban=int(ID_KANBAN_RENOVACAO_CAMPANHA),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
        id_card=int(id_card),
    )

    return id_card



def _campanhas_vencimentos_atualizar_card_renovacao_dados_cadastro(
    *,
    id_card: int,
    campanha: dict,
) -> dict:
    """Atualiza o cadastro principal do card de renovação já existente.

    Essa função corrige cards antigos criados sem IDEmpresa e sem IDDimCnaes.
    A origem oficial é a cadeia informada pelo Guilherme:
    FatoVencimentoCampanhaEuromidia -> contrato -> item -> DimEmpresas -> DimCnaes.
    """

    id_card_int = int(id_card or 0)
    if id_card_int <= 0:
        return {"ok": False, "motivo": "id_card_invalido"}

    id_usuario_logado = int(_campanhas_vencimentos_usuario_logado_id() or 0)
    id_usuario_responsavel = _parse_int(campanha.get("IDDimUsuariosVendedor")) or id_usuario_logado or None
    id_empresa = _parse_int(campanha.get("IDEmpresa"))
    id_vendedor = _parse_int(campanha.get("IDVendedor"))
    id_dim_cnaes = _parse_int(campanha.get("IDDimCnaes"))
    id_contrato = _parse_int(campanha.get("IDFatoControleContratosEuromidia"))
    id_origem_atendimento = _parse_int(
        _campanhas_vencimentos_primeiro_valor_preenchido(
            campanha.get("IDDimOrigemAtendimentoItem"),
            campanha.get("IDDimOrigemAtendimentoEmpresa"),
        )
    )
    id_tipo_documento = _parse_int(campanha.get("IDDimTipoDocumento"))
    id_reserva_preferencia = _parse_int(campanha.get("IDReservaPreferenciaRenovacao"))

    marca = str(campanha.get("MarcaExibida") or "").strip() or None
    nome_empresa = str(campanha.get("RazaoSocial") or campanha.get("NomeFantasia") or "").strip() or None
    telefone = str(campanha.get("Telefone") or "").strip() or None
    email = str(campanha.get("Email") or "").strip() or None

    sets: list[str] = []
    params: dict = {"id_card": id_card_int}

    def adicionar_set_se_existir(nome_coluna: str, nome_parametro: str, valor) -> None:
        if not _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, nome_coluna):
            return
        sets.append(f"{nome_coluna} = :{nome_parametro}")
        params[nome_parametro] = valor

    coluna_empresa = _campanhas_vencimentos_coluna_empresa_card_ou_none()
    if coluna_empresa:
        sets.append(f"{coluna_empresa} = :id_empresa")
        params["id_empresa"] = id_empresa

    adicionar_set_se_existir("IDDimCnaes", "id_dim_cnaes", id_dim_cnaes)
    adicionar_set_se_existir("NomeEmpresa", "nome_empresa", nome_empresa)
    adicionar_set_se_existir("Marca", "marca", marca)
    adicionar_set_se_existir("Telefone", "telefone", telefone)
    adicionar_set_se_existir("Email", "email", email)
    adicionar_set_se_existir("IDVendedor", "id_vendedor", id_vendedor)
    adicionar_set_se_existir("IDVendedorUsuario", "id_usuario_responsavel", id_usuario_responsavel)
    adicionar_set_se_existir("IDDimUsuarios", "id_usuario_responsavel", id_usuario_responsavel)
    adicionar_set_se_existir("IDDimOrigemAtendimento", "id_origem_atendimento", id_origem_atendimento)
    adicionar_set_se_existir("IDDimTipoDocumento", "id_tipo_documento", id_tipo_documento)
    adicionar_set_se_existir("IDEmpresaAgencia", "id_empresa_agencia", _parse_int(campanha.get("IDEmpresaAgencia")))
    adicionar_set_se_existir("IDEmpresaBureau", "id_empresa_bureau", _parse_int(campanha.get("IDEmpresaBureau")))
    adicionar_set_se_existir("IDEmpresaIntermediario", "id_empresa_intermediario", _parse_int(campanha.get("IDEmpresaIntermediario")))
    if id_reserva_preferencia:
        adicionar_set_se_existir("IDReserva", "id_reserva_preferencia", id_reserva_preferencia)

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDFatoControleContratosEuromidia"):
        adicionar_set_se_existir("IDFatoControleContratosEuromidia", "id_contrato", id_contrato)
    elif _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "IDFatoControleContratoEuromidia"):
        adicionar_set_se_existir("IDFatoControleContratoEuromidia", "id_contrato", id_contrato)

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "BitAditivo"):
        sets.append("BitAditivo = 1")

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "BitContratoNovo"):
        sets.append("BitContratoNovo = 0")

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_RENOVACAO, "AtualizadoEm"):
        sets.append("AtualizadoEm = GETDATE()")

    if not sets:
        return {"ok": False, "motivo": "sem_colunas_para_atualizar", "id_card": id_card_int}

    db.session.execute(
        text(f"""
            UPDATE {TABELA_KANBAN_CARD_RENOVACAO}
               SET {', '.join(sets)}
             WHERE IDFatoKanbanCard = :id_card;
        """),
        params,
    )

    _campanhas_vencimentos_vincular_reserva_preferencia_card_renovacao(
        id_card=id_card_int,
        campanha=campanha,
    )

    _aplicar_tag_no_card_admin(
        id_card=id_card_int,
        id_tag=ID_TAG_RENOVACAO_CAMPANHA,
        id_usuario=id_usuario_logado,
        id_empresa_proprietaria=ID_EMPRESA_PROPRIETARIA_EUROMIDIA_RENOVACAO,
    )
    _aplicar_tag_no_card_admin(
        id_card=id_card_int,
        id_tag=ID_TAG_TIPO_CONTRATO_ADITIVO_ADMIN,
        id_usuario=id_usuario_logado,
        id_empresa_proprietaria=ID_EMPRESA_PROPRIETARIA_EUROMIDIA_RENOVACAO,
    )
    _remover_tag_do_card_admin(
        id_card=id_card_int,
        id_tag=ID_TAG_TIPO_CONTRATO_NOVO_ADMIN,
        id_usuario=id_usuario_logado,
    )

    return {
        "ok": True,
        "id_card": id_card_int,
        "id_empresa": id_empresa,
        "id_dim_cnaes": id_dim_cnaes,
    }


def _campanhas_vencimentos_inserir_painel_face_card_renovacao(
    *,
    id_card: int,
    campanha: dict,
    data_inicio_renovacao: date,
    data_fim_renovacao: date,
) -> dict:
    """
    Garante o vínculo operacional de painel/face do card de renovação.

    Fonte correta da renovação:
    - FatoVencimentoCampanhaEuromidia identifica o vencimento;
    - FatoControleContratosEuromidia identifica o contrato;
    - FatoControleContratosItensEuromidia identifica o item, CodPonto e CodFace;
    - FatoKanbanCardPainelFace precisa receber essa face para o modal do Kanban
      abrir com Painel / Face já preenchido.

    A função faz UPSERT, não INSERT cego:
    - se a face já existir no card, atualiza os campos;
    - se não existir, insere;
    - isso corrige também cards antigos de renovação criados antes do vínculo de face.
    """

    id_card_int = int(id_card or 0)
    if id_card_int <= 0:
        raise RuntimeError("IDFatoKanbanCard inválido ao vincular painel/face da renovação.")

    cod_ponto = str(campanha.get("CodPonto") or "").strip() or None
    cod_face = str(campanha.get("CodFace") or "").strip().upper() or None

    if not cod_face:
        return {
            "ok": False,
            "acao": "ignorado",
            "motivo": "campanha_sem_cod_face",
            "id_card": id_card_int,
        }

    id_painel = _parse_int(campanha.get("IDPainelEuromidia"))
    id_face = _parse_int(campanha.get("IDDimFacesPaineis"))
    id_contrato = _parse_int(campanha.get("IDFatoControleContratosEuromidia"))
    id_item_contrato = _parse_int(campanha.get("IDFatoControleContratosItensEuromidia"))
    id_tabela_preco = _parse_int(campanha.get("IDDimTabelaPrecosEuromidiaTabela"))
    id_reserva_preferencia = _parse_int(campanha.get("IDReservaPreferenciaRenovacao"))

    periodo_exibicao = _campanhas_vencimentos_primeiro_valor_preenchido(
        campanha.get("PeriodoExibicaoContrato"),
        campanha.get("PeriodoExibicaoTabela"),
    )

    exibicoes_dia = _parse_int(_campanhas_vencimentos_primeiro_valor_preenchido(
        campanha.get("ExibicoesDiaContrato"),
        campanha.get("ExibicoesDiaTabela"),
        campanha.get("Cota"),
    ))

    valor_tabela = _decimal_ou_none(campanha.get("ValorTabelaPreco"))
    tabela = _campanhas_vencimentos_primeiro_valor_preenchido(campanha.get("TabelaPreco"))
    politica_trocas = _campanhas_vencimentos_primeiro_valor_preenchido(campanha.get("PoliticaTrocasTabela"))
    valor_troca = _decimal_ou_none(campanha.get("ValorTrocaTabela"))
    tipo_painel = str(
        campanha.get("TipoPainelItem")
        or campanha.get("TipoPainelCadastro")
        or campanha.get("TipoFaceCadastro")
        or ""
    ).strip() or None

    campos: list[tuple[str, str, object]] = [
        ("Ordem", "ordem", 1),
        ("CodPonto", "cod_ponto", cod_ponto),
        ("CodFace", "cod_face", cod_face),
        ("DataInicio", "data_inicio", data_inicio_renovacao),
        ("DataFim", "data_fim", data_fim_renovacao),
    ]

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO, "Ativo"):
        campos.append(("Ativo", "ativo", 1))

    campos_opcionais = [
        ("IDDimPaineisEuromidia", "id_painel", id_painel),
        ("IDDimFacesPaineis", "id_face", id_face),
        ("IDFatoControleContratosEuromidia", "id_contrato", id_contrato),
        ("IDFatoControleContratoEuromidia", "id_contrato", id_contrato),
        ("IDFatoControleContratosItensEuromidia", "id_item_contrato", id_item_contrato),
        ("IDFatoControleContratoItemEuromidia", "id_item_contrato", id_item_contrato),
        ("IDFatoOcupacaoPaineisEuromidia", "id_reserva_preferencia", id_reserva_preferencia),
        ("IDReserva", "id_reserva_preferencia", id_reserva_preferencia),
        ("TipoPainel", "tipo_painel", tipo_painel),
        ("IDDimTabelaPrecosEuromidia", "id_tabela_preco", id_tabela_preco),
        ("PeriodoExibicao", "periodo_exibicao", periodo_exibicao),
        ("ExibicoesDia", "exibicoes_dia", exibicoes_dia),
        ("ValorTabela", "valor_tabela", valor_tabela),
        ("Tabela", "tabela", tabela),
        ("PoliticaTrocas", "politica_trocas", politica_trocas),
        ("ValorTroca", "valor_troca", valor_troca),
        ("Cota", "cota", campanha.get("Cota")),
        ("IDUsuario", "id_usuario", _campanhas_vencimentos_usuario_logado_id()),
        ("IDEmpresaProprietaria", "id_empresa_proprietaria", ID_EMPRESA_PROPRIETARIA_EUROMIDIA_RENOVACAO),
    ]

    for nome_coluna, nome_parametro, valor in campos_opcionais:
        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO, nome_coluna):
            campos.append((nome_coluna, nome_parametro, valor))

    params_busca = {
        "id_card": id_card_int,
        "cod_ponto": cod_ponto,
        "cod_face": cod_face,
        "id_face": id_face,
    }

    filtros_match = [
        "UPPER(LTRIM(RTRIM(ISNULL(CodFace, '')))) = UPPER(LTRIM(RTRIM(ISNULL(:cod_face, ''))))"
    ]

    if id_face and _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO, "IDDimFacesPaineis"):
        filtros_match.append("TRY_CONVERT(int, IDDimFacesPaineis) = TRY_CONVERT(int, :id_face)")

    filtros_base = ["IDFatoKanbanCard = :id_card"]
    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO, "Ativo"):
        filtros_base.append("ISNULL(Ativo, 1) = 1")

    if cod_ponto:
        filtros_match.append("""
            (
                NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), CodPonto))), '') IS NOT NULL
                AND LTRIM(RTRIM(CONVERT(varchar(80), CodPonto))) = LTRIM(RTRIM(CONVERT(varchar(80), :cod_ponto)))
                AND UPPER(LTRIM(RTRIM(ISNULL(CodFace, '')))) = UPPER(LTRIM(RTRIM(ISNULL(:cod_face, ''))))
            )
        """)

    row_existente = db.session.execute(
        text(f"""
            SELECT TOP (1)
                IDFatoKanbanCardPainelFace
            FROM {TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO}
            WHERE {' AND '.join(f'({filtro})' for filtro in filtros_base)}
              AND ({' OR '.join(f'({filtro})' for filtro in filtros_match)})
            ORDER BY
                CASE
                    WHEN UPPER(LTRIM(RTRIM(ISNULL(CodFace, '')))) = UPPER(LTRIM(RTRIM(ISNULL(:cod_face, ''))))
                    THEN 0 ELSE 1
                END,
                ISNULL(Ordem, 999999),
                IDFatoKanbanCardPainelFace DESC;
        """),
        params_busca,
    ).mappings().first()

    id_linha_existente = int((row_existente or {}).get("IDFatoKanbanCardPainelFace") or 0)

    if id_linha_existente > 0:
        params_update = {"id_linha": id_linha_existente}
        sets = []
        for nome_coluna, nome_parametro, valor in campos:
            sets.append(f"{nome_coluna} = :{nome_parametro}")
            params_update[nome_parametro] = valor

        if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO, "DataAtualizacao"):
            sets.append("DataAtualizacao = GETDATE()")

        db.session.execute(
            text(f"""
                UPDATE {TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO}
                   SET {', '.join(sets)}
                 WHERE IDFatoKanbanCardPainelFace = :id_linha;
            """),
            params_update,
        )

        return {
            "ok": True,
            "acao": "atualizado",
            "id_card": id_card_int,
            "id_linha": id_linha_existente,
            "cod_ponto": cod_ponto,
            "cod_face": cod_face,
        }

    colunas_insert = ["IDFatoKanbanCard"]
    valores_insert = [":id_card"]
    params_insert = {"id_card": id_card_int}

    for nome_coluna, nome_parametro, valor in campos:
        colunas_insert.append(nome_coluna)
        valores_insert.append(f":{nome_parametro}")
        params_insert[nome_parametro] = valor

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO, "CriadoEm"):
        colunas_insert.append("CriadoEm")
        valores_insert.append("GETDATE()")

    if _campanhas_vencimentos_coluna_existe(TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO, "DataAtualizacao"):
        colunas_insert.append("DataAtualizacao")
        valores_insert.append("GETDATE()")

    db.session.execute(
        text(f"""
            INSERT INTO {TABELA_KANBAN_CARD_PAINEL_FACE_RENOVACAO}
                ({', '.join(colunas_insert)})
            VALUES
                ({', '.join(valores_insert)});
        """),
        params_insert,
    )

    return {
        "ok": True,
        "acao": "inserido",
        "id_card": id_card_int,
        "cod_ponto": cod_ponto,
        "cod_face": cod_face,
    }





@admin.route("/vencimentos-campanhas/<int:id_vencimento>/renovar", methods=["POST"])
@login_required
@limiter.limit("60 per minute", methods=["POST"])
def vencimentos_campanhas_renovar(id_vencimento: int):
    """Cria o card de renovação no Kanban 1 a partir da linha de vencimento da campanha."""

    url_falha = request.referrer or url_for("admin.vencimentos_campanhas_euromidia")

    try:
        campanha = _campanhas_vencimentos_buscar_base_renovacao(int(id_vencimento))
        if not campanha:
            flash("Não encontrei a campanha selecionada para renovação.", "warning")
            return redirect(url_falha)

        usuario_logado_eh_vendedor = _campanhas_vencimentos_usuario_eh_vendedor()
        id_usuario_logado = int(_campanhas_vencimentos_usuario_logado_id() or 0)
        id_usuario_vendedor = int(campanha.get("IDDimUsuariosVendedor") or 0)

        if usuario_logado_eh_vendedor and id_usuario_vendedor and id_usuario_vendedor != id_usuario_logado:
            abort(403, description="Você só pode renovar campanhas vinculadas ao seu vendedor.")

        data_inicio_original = campanha.get("DataInicioCampanha")
        data_termino_original = campanha.get("DataTerminoPrevisto")
        cod_face = str(campanha.get("CodFace") or "").strip().upper()

        if not data_inicio_original or not data_termino_original:
            flash("Não consegui criar a renovação porque a campanha não possui início e término válidos.", "warning")
            return redirect(url_falha)

        if data_termino_original < data_inicio_original:
            flash("Não consegui criar a renovação porque o término da campanha é menor que a data de início.", "warning")
            return redirect(url_falha)

        if not cod_face:
            flash("Não consegui criar a renovação porque o item da campanha não possui CodFace.", "warning")
            return redirect(url_falha)

        prazo_dias = (data_termino_original - data_inicio_original).days + 1
        if prazo_dias <= 0:
            flash("Não consegui criar a renovação porque o prazo calculado da campanha ficou inválido.", "warning")
            return redirect(url_falha)

        data_inicio_renovacao = data_termino_original + timedelta(days=1)
        data_fim_renovacao = data_inicio_renovacao + timedelta(days=prazo_dias - 1)

        reserva_preferencia = _campanhas_vencimentos_preparar_campanha_com_reserva_preferencia(campanha)
        id_reserva_preferencia = _parse_int(campanha.get("IDReservaPreferenciaRenovacao"))
        if reserva_preferencia and reserva_preferencia.get("DataInicio") and reserva_preferencia.get("DataFim"):
            data_inicio_renovacao = reserva_preferencia.get("DataInicio")
            data_fim_renovacao = reserva_preferencia.get("DataFim")

        id_card_existente = _campanhas_vencimentos_card_renovacao_existente(campanha)
        if id_card_existente:
          
            _campanhas_vencimentos_atualizar_card_renovacao_dados_cadastro(
                id_card=int(id_card_existente),
                campanha=campanha,
            )
            _campanhas_vencimentos_vincular_reserva_preferencia_card_renovacao(
                id_card=int(id_card_existente),
                campanha=campanha,
                reserva=reserva_preferencia,
            )

            if cod_face:
                _campanhas_vencimentos_inserir_painel_face_card_renovacao(
                    id_card=int(id_card_existente),
                    campanha=campanha,
                    data_inicio_renovacao=data_inicio_renovacao,
                    data_fim_renovacao=data_fim_renovacao,
                )

            _campanhas_vencimentos_invalidar_cache_kanban_renovacao(
                id_kanban=int(ID_KANBAN_RENOVACAO_CAMPANHA),
                id_empresa_proprietaria=int(ID_EMPRESA_PROPRIETARIA_EUROMIDIA_RENOVACAO),
                id_card=int(id_card_existente),
            )

            db.session.commit()
            msg_reserva = f" Reserva vinculada: #{id_reserva_preferencia}." if id_reserva_preferencia else ""
            flash(
                f"Já existia um card de renovação para essa campanha: #{id_card_existente}. "
                f"Garanti novamente o vínculo de painel/face no card.{msg_reserva}",
                "info",
            )
            return redirect(url_for("kanban.kanban_view", id_kanban=int(ID_KANBAN_RENOVACAO_CAMPANHA)))

        id_card = _campanhas_vencimentos_criar_card_renovacao(
            campanha=campanha,
            data_inicio_renovacao=data_inicio_renovacao,
            data_fim_renovacao=data_fim_renovacao,
            prazo_dias=int(prazo_dias),
        )

        db.session.commit()
        msg_reserva = f" • Reserva vinculada #{id_reserva_preferencia}" if id_reserva_preferencia else ""
        flash(
            "Card de renovação criado no Kanban 1: "
            f"#{id_card} • {cod_face} • "
            f"{_campanhas_vencimentos_formatar_data_pt(data_inicio_renovacao)} até "
            f"{_campanhas_vencimentos_formatar_data_pt(data_fim_renovacao)}"
            f"{msg_reserva}.",
            "success",
        )
        return redirect(url_for("kanban.kanban_view", id_kanban=int(ID_KANBAN_RENOVACAO_CAMPANHA)))

    except HTTPException:
        db.session.rollback()
        raise

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Erro ao criar card de renovação de campanha. id_vencimento=%s",
            id_vencimento,
        )
        flash(f"Erro ao criar card de renovação: {exc}", "danger")
        return redirect(url_falha)







@admin.route("/campanhas/vencimentos", methods=["GET"])
@admin.route("/vencimentos-campanhas", methods=["GET"])
@login_required
@limiter.limit("80 per minute", methods=["GET"])
def vencimentos_campanhas_euromidia():
    """
    Tela de vencimentos de campanhas da Euromídia.

    Admin / perfis não vendedores:
        - visualizam todos os contratos/campanhas.

    Perfil VENDEDOR:
        - visualiza somente campanhas cujo IDVendedor esteja ligado ao seu IDDimUsuarios
          em Integracao.dbo.Vendedores.
    """

    _campanhas_vencimentos_atualizar_status_e_dias()

    usuario_logado_eh_vendedor = _campanhas_vencimentos_usuario_eh_vendedor()
    usuario_logado_eh_admin = _campanhas_vencimentos_usuario_eh_admin()
    id_usuario_logado = _campanhas_vencimentos_usuario_logado_id()

    q = (request.args.get("q") or "").strip()[:160]
    status_ids_selecionados = _campanhas_vencimentos_normalizar_lista_int(request.args.getlist("status"))
    marcas_selecionadas = _campanhas_vencimentos_normalizar_lista_texto(request.args.getlist("marca"))
    vendedores_ids_selecionados = _campanhas_vencimentos_normalizar_lista_int(request.args.getlist("vendedor")) if usuario_logado_eh_admin else []

    dt_ini_raw = (request.args.get("dt_ini") or "").strip()
    dt_fim_raw = (request.args.get("dt_fim") or "").strip()
    bi_semana_filtro = (request.args.get("bi_semana") or request.args.get("bisemana") or "").strip()

    data_inicio_filtro = _parse_date_br(dt_ini_raw)
    data_fim_filtro = _parse_date_br(dt_fim_raw)

    if data_inicio_filtro and data_fim_filtro and data_fim_filtro < data_inicio_filtro:
        data_inicio_filtro, data_fim_filtro = data_fim_filtro, data_inicio_filtro

    dt_ini = data_inicio_filtro.strftime("%Y-%m-%d") if data_inicio_filtro else ""
    dt_fim = data_fim_filtro.strftime("%Y-%m-%d") if data_fim_filtro else ""
    periodo_filtro_ativo = bool(dt_ini or dt_fim or bi_semana_filtro)
    ano_periodo = int((data_inicio_filtro or data_fim_filtro or date.today()).year)

    try:
        page = int(request.args.get("page") or "1")
    except Exception:
        page = 1

    per_page = 10
    page = max(1, page)

    where_sql, params = _campanhas_vencimentos_montar_filtros_sql(
        q=q,
        status_ids_selecionados=status_ids_selecionados,
        marcas_selecionadas=marcas_selecionadas,
        vendedores_ids_selecionados=vendedores_ids_selecionados,
        usuario_logado_eh_vendedor=usuario_logado_eh_vendedor,
        usuario_logado_eh_admin=usuario_logado_eh_admin,
        id_usuario_logado=id_usuario_logado,
        data_inicio_filtro=data_inicio_filtro,
        data_fim_filtro=data_fim_filtro,
    )

    sql_from_where = _campanhas_vencimentos_sql_from_where(where_sql)
    marca_sql = _campanhas_vencimentos_marca_sql()
    razao_social_sql = _campanhas_vencimentos_razao_social_sql()
    painel_sql = _campanhas_vencimentos_painel_sql()

    sql_total = text(f"""
        SELECT COUNT(1) AS Total
        {sql_from_where}
    """)

    total = int((db.session.execute(sql_total, params).scalar() or 0))

    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    sql_rows = text(f"""
        SELECT
            venc.IDFatoVencimentoCampanhaEuromidia,
            venc.IDFatoControleContratosEuromidia,
            venc.IDFatoControleContratosItensEuromidia,
            venc.IDDimStatusCampanha,
            st.NomeStatus,
            venc.IDVendedor,
            vend.NomeVendedor,
            vend.IDDimUsuarios AS IDDimUsuariosVendedor,
            ctr.NumeroContrato,
            ctr.NumeroPrevia,
            RazaoSocial = {razao_social_sql},
            ctr.TotalLiquidoContratoAGBRCTACORDO AS TotalLiquidoContrato,
            Painel = {painel_sql},
            MarcaExibida = {marca_sql},
            venc.DataInicioCampanha,
            DataTermino = venc.DataTerminoPrevisto,
            venc.DiasParaVencer,
            venc.BitAtivo,
            venc.DataCriacao,
            venc.DataAtualizacao
        {sql_from_where}
        ORDER BY
            CASE WHEN venc.DataTerminoPrevisto IS NULL THEN 1 ELSE 0 END ASC,
            venc.DataTerminoPrevisto ASC,
            venc.IDFatoVencimentoCampanhaEuromidia DESC
        OFFSET :offset ROWS
        FETCH NEXT :per_page ROWS ONLY
    """)

    params_rows = dict(params)
    params_rows.update({"offset": offset, "per_page": per_page})

    rows = db.session.execute(sql_rows, params_rows).mappings().all()
    itens = [_campanhas_vencimentos_enriquecer_item(dict(r)) for r in rows]

    status_opcoes = db.session.execute(text("""
        SELECT DISTINCT
            st.IDDimStatusCampanha,
            st.NomeStatus
        FROM [Integracao].[Silver].[FatoVencimentoCampanhaEuromidia] AS venc
        INNER JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item
            ON item.IDFatoControleContratosItensEuromidia = venc.IDFatoControleContratosItensEuromidia
           AND item.IDFatoControleContratoEuromidia = venc.IDFatoControleContratosEuromidia
           AND ISNULL(item.BitAtivo, 0) = 1
        INNER JOIN [Integracao].[Silver].[DimStatusCampanha] AS st
            ON st.IDDimStatusCampanha = venc.IDDimStatusCampanha
        WHERE ISNULL(venc.BitAtivo, 1) = 1
        ORDER BY st.IDDimStatusCampanha ASC
    """)).mappings().all()

    marca_opcoes = _campanhas_vencimentos_opcoes_marca(
        usuario_logado_eh_vendedor=usuario_logado_eh_vendedor,
        id_usuario_logado=id_usuario_logado,
    )

    vendedor_opcoes = _campanhas_vencimentos_opcoes_vendedor(
        usuario_logado_eh_admin=usuario_logado_eh_admin,
    )

    bisemanas_select = _campanhas_vencimentos_bisemanas_select(
        dt_ini_base=data_inicio_filtro,
        dt_fim_base=data_fim_filtro,
    )

    pagina_inicio = max(1, page - 3)
    pagina_fim = min(total_pages, page + 3)
    paginas_visiveis = list(range(pagina_inicio, pagina_fim + 1))

    return_to_vencimentos = request.full_path if request.query_string else request.path

    return render_template(
        "admin/vencimentos_campanhas_euromidia.html",
        itens=itens,
        return_to_vencimentos=return_to_vencimentos,
        status_opcoes=status_opcoes,
        marca_opcoes=marca_opcoes,
        vendedor_opcoes=vendedor_opcoes,
        usuario_logado_eh_vendedor=usuario_logado_eh_vendedor,
        usuario_logado_eh_admin=usuario_logado_eh_admin,
        q=q,
        dt_ini=dt_ini,
        dt_fim=dt_fim,
        ano_periodo=ano_periodo,
        bi_semana_filtro=bi_semana_filtro,
        periodo_filtro_ativo=periodo_filtro_ativo,
        bisemanas_select=bisemanas_select,
        status_ids_selecionados=status_ids_selecionados,
        marcas_selecionadas=marcas_selecionadas,
        vendedores_ids_selecionados=vendedores_ids_selecionados,
        paginacao={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "inicio": (offset + 1) if total > 0 else 0,
            "fim": min(offset + per_page, total),
            "paginas_visiveis": paginas_visiveis,
        },
    )




@admin.route("/vencimentos-campanhas/sugestoes", methods=["GET"])
@login_required
@limiter.limit("120 per minute", methods=["GET"])
def vencimentos_campanhas_sugestoes():
    """Retorna sugestões para a busca da tela de vencimentos de campanhas."""

    usuario_logado_eh_vendedor = _campanhas_vencimentos_usuario_eh_vendedor()
    usuario_logado_eh_admin = _campanhas_vencimentos_usuario_eh_admin()
    id_usuario_logado = _campanhas_vencimentos_usuario_logado_id()

    q = (request.args.get("q") or "").strip()[:160]
    if len(q) < 1:
        return jsonify({"ok": True, "items": []})

    status_ids_selecionados = _campanhas_vencimentos_normalizar_lista_int(request.args.getlist("status"))
    marcas_selecionadas = _campanhas_vencimentos_normalizar_lista_texto(request.args.getlist("marca"))
    vendedores_ids_selecionados = _campanhas_vencimentos_normalizar_lista_int(request.args.getlist("vendedor")) if usuario_logado_eh_admin else []

    data_inicio_filtro = _parse_date_br((request.args.get("dt_ini") or "").strip())
    data_fim_filtro = _parse_date_br((request.args.get("dt_fim") or "").strip())

    if data_inicio_filtro and data_fim_filtro and data_fim_filtro < data_inicio_filtro:
        data_inicio_filtro, data_fim_filtro = data_fim_filtro, data_inicio_filtro

    try:
        limite = int(request.args.get("limit") or "8")
    except Exception:
        limite = 8
    limite = max(1, min(limite, 15))

    where_sql, params = _campanhas_vencimentos_montar_filtros_sql(
        q=q,
        status_ids_selecionados=status_ids_selecionados,
        marcas_selecionadas=marcas_selecionadas,
        vendedores_ids_selecionados=vendedores_ids_selecionados,
        usuario_logado_eh_vendedor=usuario_logado_eh_vendedor,
        usuario_logado_eh_admin=usuario_logado_eh_admin,
        id_usuario_logado=id_usuario_logado,
        data_inicio_filtro=data_inicio_filtro,
        data_fim_filtro=data_fim_filtro,
    )

    sql_from_where = _campanhas_vencimentos_sql_from_where(where_sql)
    marca_sql = _campanhas_vencimentos_marca_sql()
    razao_social_sql = _campanhas_vencimentos_razao_social_sql()
    painel_sql = _campanhas_vencimentos_painel_sql()

    sql = text(f"""
        SELECT
            venc.IDFatoVencimentoCampanhaEuromidia,
            venc.IDFatoControleContratosEuromidia,
            venc.IDFatoControleContratosItensEuromidia,
            venc.IDDimStatusCampanha,
            st.NomeStatus,
            vend.NomeVendedor,
            ctr.NumeroContrato,
            ctr.NumeroPrevia,
            RazaoSocial = {razao_social_sql},
            Painel = {painel_sql},
            MarcaExibida = {marca_sql},
            venc.DataInicioCampanha,
            DataTermino = venc.DataTerminoPrevisto,
            venc.DiasParaVencer,
            venc.BitAtivo,
            venc.DataAtualizacao
        {sql_from_where}
        ORDER BY
            CASE WHEN venc.DataTerminoPrevisto IS NULL THEN 1 ELSE 0 END ASC,
            venc.DataTerminoPrevisto ASC,
            venc.IDFatoVencimentoCampanhaEuromidia DESC
        OFFSET 0 ROWS
        FETCH NEXT :limite ROWS ONLY
    """)

    params_rows = dict(params)
    params_rows["limite"] = limite

    rows = db.session.execute(sql, params_rows).mappings().all()

    items = []
    for row in rows:
        d = _campanhas_vencimentos_enriquecer_item(dict(row))
        items.append({
            "id_vencimento": int(d.get("IDFatoVencimentoCampanhaEuromidia") or 0),
            "id_contrato": int(d.get("IDFatoControleContratosEuromidia") or 0),
            "id_contrato_texto": d.get("IDFatoControleContratosExibicao") or "—",
            "numero_contrato": d.get("NumeroContratoExibicao") or "—",
            "numero_contrato_original": str(d.get("NumeroContrato") or ""),
            "contrato_exibicao": d.get("IDFatoControleContratosExibicao") or d.get("NumeroContratoExibicao") or "—",
            "razao_social": str(d.get("RazaoSocial") or "—"),
            "marca": str(d.get("MarcaExibida") or "—"),
            "painel": str(d.get("Painel") or "—"),
            "status": str(d.get("NomeStatus") or "Sem status"),
            "classe_status": d.get("ClasseStatus") or "neutro",
            "dias": d.get("DiasTexto") or "—",
            "classe_dias": d.get("ClasseDias") or "normal",
            "ativo": d.get("BitAtivoTexto") or "Inativo",
            "classe_ativo": d.get("ClasseBitAtivo") or "inativo",
            "data_inicio": _campanhas_vencimentos_data_json(d.get("DataInicioCampanha")),
            "data_termino": _campanhas_vencimentos_data_json(d.get("DataTermino")),
            "vendedor": str(d.get("NomeVendedor") or ""),
        })

    return jsonify({"ok": True, "items": items})

