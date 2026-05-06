from flask_sqlalchemy import SQLAlchemy
from ..extensions import db,limiter
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify,current_app,abort
from ..models.admin_models import FatoMovimentoFinanceiroEmpresas, DimEmpresaProprietaria,DimProdutoAuvo
from datetime import datetime, date, timedelta
from sqlalchemy import func, case,text
from flask_login import login_required, current_user
from ..autenticacao.autenticacao_views import requer_permissao
from pathlib import Path
import hashlib




admin = Blueprint("admin", __name__)




ID_STATUS_CONTRATO_APROVADO = 2
ID_FASE_FORMULARIO_CONTRATO = 4
TABELA_CARD_OCORRENCIA = "[Integracao].[Silver].[FatoCardOCorrencia]"












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

    existe = db.session.execute(
        text("""
            SELECT TOP 1 1
            FROM [Kanban].[Silver].[FatoKanbanCardTag]
            WHERE IDFatoKanbanCard = :id_card
              AND IDDimKanbanTag = :id_tag
              AND RemovidoEm IS NULL
        """),
        {"id_card": int(id_card), "id_tag": int(id_tag)},
    ).scalar()

    return bool(existe)


def _aplicar_tag_no_card_admin(*, id_card: int | None, id_tag: int | None, id_usuario: int | None, id_empresa_proprietaria: int | None) -> bool:
    if id_card in (None, '', 0) or id_tag in (None, '', 0):
        return False

    if _card_possui_tag_ativa_admin(id_card, id_tag):
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
    return [dict(row) for row in rows]






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
        "Cota": item_solicitacao.get("Cota"),
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
        "DataAssinaturaRenovacao": item_solicitacao.get("DataAssinaturaRenovacao"),
        "IDTrimestre": item_solicitacao.get("IDTrimestre"),
        "TexmpoExposicao": item_solicitacao.get("TexmpoExposicao"),
        "DataInicioPrevisto": item_solicitacao.get("DataInicioPrevisto"),
        "DataTerminoPrevisto": item_solicitacao.get("DataTerminoPrevisto"),
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
        "AtivoCancelamento": item_solicitacao.get("AtivoCancelamento"),
        "FaturamentoLiquidoFinalMensal": item_solicitacao.get("FaturamentoLiquidoFinalMensal"),
        "ComissaoGerenciaNordeste": item_solicitacao.get("ComissaoGerenciaNordeste"),
        "Faturamento": item_solicitacao.get("Faturamento"),
        "DataCancelamento": item_solicitacao.get("DataCancelamento"),
        "OBS": item_solicitacao.get("OBS"),
        "IDVendedor": item_solicitacao.get("IDVendedor"),
        "IDPainelEuromidia": item_solicitacao.get("IDPainelEuromidia"),
        "IDDimFacesPaineis": item_solicitacao.get("IDDimFacesPaineis"),
        "DataFimEfetiva": item_solicitacao.get("DataFimEfetiva"),
        "Status": item_solicitacao.get("Status"),
        "IDDimCheckinHistorico": item_solicitacao.get("IDDimCheckingHistorico"),
        "IDFatoKanbanCard": item_solicitacao.get("IDFatoKanbanCard"),
        "BitAtivo": item_solicitacao.get("BitAtivo") if item_solicitacao.get("BitAtivo") is not None else 1,
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
            SELECT TOP 1
                   c.name AS NomeColunaFase
            FROM sys.columns c
            INNER JOIN sys.objects o
                    ON o.object_id = c.object_id
            INNER JOIN sys.schemas s
                    ON s.schema_id = o.schema_id
            WHERE s.name = 'Silver'
              AND o.name = 'FatoKanbanCard'
              AND o.type = 'U'
              AND c.name IN ('IDDimKanbanFaseAtual', 'IDDimKanbanFase')
            ORDER BY CASE c.name
                        WHEN 'IDDimKanbanFaseAtual' THEN 1
                        WHEN 'IDDimKanbanFase' THEN 2
                        ELSE 99
                     END
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

    row_existente = db.session.execute(
        text("""
            SELECT TOP 1
                   f.IDFatoContatoClienteDiretoEuromidia
            FROM [Integracao].[Silver].[FatoContatoClienteDiretoEuromidia] f
            WHERE f.IDFatoControleContratosEuromidia = :id_contrato
               OR (:id_card IS NOT NULL AND f.IDFatoKanbanCard = :id_card)
            ORDER BY
                CASE WHEN :id_card IS NOT NULL AND f.IDFatoKanbanCard = :id_card THEN 0 ELSE 1 END,
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

def _mover_solicitacao_aprovada_para_controle(*, id_solicitacao: int, id_usuario_logado: int | None) -> dict:
    cab = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao))
    if not cab:
        raise ValueError("Solicitação não encontrada para aprovação.")

    itens_solicitacao = _obter_itens_solicitacao_brutos(int(id_solicitacao))

    id_contrato_controle = _int_ou_none(cab.get("IDFatoControleContratosEuromidia"))
    referencia_informada = _texto_ou_none(cab.get("Referencia"))
    ids_itens_controle: list[int] = []
    precos_praticados: list[dict] = []

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
        "DataAssinaturaRenovacao": cab.get("DataAssinaturaRenovacao"),
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
        id_item_solicitacao = _int_ou_none(item.get("IDFatoSolicitacaoContratoItemEuromidia"))
        id_item_controle_origem = _int_ou_none(item.get("IDFatoControleContratosItensEuromidia"))

        row_item_existente = db.session.execute(
            text("""
                SELECT TOP 1
                       i.IDFatoControleContratosItensEuromidia,
                       i.Referencia AS ReferenciaAtual
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
                WHERE
                    i.IDFatoControleContratoEuromidia = :id_contrato_controle
                    AND
                    (
                        i.IDFatoControleContratosItensEuromidia = :id_item_controle_origem
                        OR
                        (
                            ISNULL(LTRIM(RTRIM(CAST(i.CodPonto AS varchar(60)))), '') = ISNULL(LTRIM(RTRIM(CAST(:cod_ponto AS varchar(60)))), '')
                            AND
                            ISNULL(UPPER(LTRIM(RTRIM(CAST(i.CodFace AS varchar(60))))), '') = ISNULL(UPPER(LTRIM(RTRIM(CAST(:cod_face AS varchar(60))))), '')
                        )
                    )
                ORDER BY i.IDFatoControleContratosItensEuromidia DESC
            """),
            {
                "id_contrato_controle": int(id_contrato_controle),
                "id_item_controle_origem": int(id_item_controle_origem) if id_item_controle_origem not in (None, "", 0) else None,
                "cod_ponto": item.get("CodPonto"),
                "cod_face": item.get("CodFace"),
            },
        ).mappings().first()

        id_item_controle_existente = (
            int(row_item_existente["IDFatoControleContratosItensEuromidia"])
            if row_item_existente and row_item_existente.get("IDFatoControleContratosItensEuromidia") is not None
            else None
        )

        referencia_item_resolvida = _resolver_referencia_item_controle(
            id_fato_controle_contratos=int(id_contrato_controle),
            id_item_controle_atual=id_item_controle_existente,
            id_item_solicitacao=id_item_solicitacao,
            referencia_informada=item.get("Referencia"),
            referencia_contrato=referencia_final,
            referencia_atual=(row_item_existente or {}).get("ReferenciaAtual"),
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

        params_item = {
            "IDFatoControleContratoEuromidia": int(id_contrato_controle),
            "Referencia": referencia_item_resolvida,
            "NumeroContrato": item.get("NumeroContrato") or cab.get("NumeroContrato"),
            "NumeroPrevia": item.get("NumeroPrevia") or cab.get("NumeroPrevia"),
            "CNPJ": item.get("CNPJ") or cab.get("CNPJ"),
            "CodPonto": item.get("CodPonto"),
            "CodFace": item.get("CodFace"),
            "DataLancamento": item.get("DataLancamento") or cab.get("DataLancamento"),
            "Cota": item.get("Cota"),
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
            "DataAssinaturaRenovacao": item.get("DataAssinaturaRenovacao") or cab.get("DataAssinaturaRenovacao"),
            "IDTrimestre": item.get("IDTrimestre") or cab.get("IDTrimestre"),
            "TexmpoExposicao": item.get("TexmpoExposicao"),
            "DataInicioPrevisto": item.get("DataInicioPrevisto"),
            "DataTerminoPrevisto": item.get("DataTerminoPrevisto"),
            "InicioRenovacao": item.get("InicioRenovacao"),
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
            "AtivoCancelamento": item.get("AtivoCancelamento"),
            "FaturamentoLiquidoFinalMensal": item.get("FaturamentoLiquidoFinalMensal"),
            "ComissaoGerenciaNordeste": item.get("ComissaoGerenciaNordeste"),
            "Faturamento": item.get("Faturamento"),
            "DataCancelamento": item.get("DataCancelamento"),
            "OBS": item.get("OBS"),
            "IDVendedor": item.get("IDVendedor"),
            "IDPainelEuromidia": item.get("IDPainelEuromidia"),
            "IDDimFacesPaineis": item.get("IDDimFacesPaineis"),
            "DataFimEfetiva": item.get("DataFimEfetiva"),
            "Status": item.get("Status"),
            "IDDimCheckinHistorico": item.get("IDDimCheckingHistorico"),
            "IDFatoKanbanCard": item.get("IDFatoKanbanCard"),
            "BitAtivo": item.get("BitAtivo") if item.get("BitAtivo") is not None else 1,
            "IDEmpresaAgencia": item.get("IDEmpresaAgencia") or cab.get("IDEmpresaAgencia"),
        }

        if row_item_existente and row_item_existente.get("IDFatoControleContratosItensEuromidia") is not None:
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

            id_item_controle = int(row_item_novo.get("id_item_controle") or 0) if row_item_novo else None

        if id_item_controle in (None, "", 0):
            raise RuntimeError("Não foi possível inserir/atualizar um item do contrato no controle.")

        ids_itens_controle.append(int(id_item_controle))

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
        "id_card": _int_ou_none(cab.get("IDFatoKanbanCard")),
        "id_empresa": _int_ou_none(cab.get("IDEmpresa")),
        "id_empresa_proprietaria": _int_ou_none(cab.get("IDEmpresaProprietaria")),
        "tipo_solicitacao": _tipo_solicitacao_normalizado(cab.get("TipoSolicitacao")),
    }









def _resolver_id_dim_tipo_documento_admin(nome_tipo_documento: str | None, id_empresa_proprietaria: int | None = None) -> int | None:
    """Resolve o IDDimTipoDocumento pelo nome salvo na solicitação."""
    nome = _texto_ou_none(nome_tipo_documento)
    if not nome:
        return None

    row = db.session.execute(
        text("""
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
                CASE WHEN td.IDEmpresaProprietaria = :id_empresa_proprietaria THEN 0 ELSE 1 END,
                td.IDDimTipoDocumento ASC;
        """),
        {
            "nome_tipo_documento": nome,
            "id_empresa_proprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, "", 0) else None,
        },
    ).mappings().first()

    return _int_ou_none(row.get("IDDimTipoDocumento")) if row else None


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
        raise ValueError(
            "Não foi possível registrar a ocorrência do card porque o IDDimTipoDocumento não foi resolvido."
        )

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

    item_ids = form.getlist("item_id")
    sql_update_item = text("""
        UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]
           SET [IDPainelEuromidia] = :IDPainelEuromidia, [IDDimFacesPaineis] = :IDDimFacesPaineis, [CodPonto] = :CodPonto, [CodFace] = :CodFace, [CidadeExibicao] = :CidadeExibicao,
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
        cod_ponto = _texto_ou_none(form.get(f"{prefixo}CodPonto"))
        cod_face = _texto_ou_none(form.get(f"{prefixo}CodFace"))
        info_face = None
        if cod_ponto and cod_face:
            info_face = _resolver_face_e_painel_por_codigos(cod_ponto, cod_face)
            if not info_face:
                raise ValueError(f"Não encontrei CodPonto/CodFace válidos para o item {item_id}: {cod_ponto} / {cod_face}.")

        params_item = {
            "IDFatoSolicitacaoContratoItemEuromidia": item_id, "IDFatoSolicitacaoContratoEuromidia": int(id_solicitacao),
            "IDPainelEuromidia": info_face.get("IDDimPaineisEuromidia") if info_face else None, "IDDimFacesPaineis": info_face.get("IDDimFacesPaineis") if info_face else None,
            "CodPonto": cod_ponto, "CodFace": cod_face, "CidadeExibicao": _texto_ou_none(form.get(f"{prefixo}CidadeExibicao")), "Tipo": _texto_ou_none(form.get(f"{prefixo}Tipo")),
            "Cota": _decimal_ou_none(form.get(f"{prefixo}Cota")), "DataInicioPrevisto": _data_ou_none(form.get(f"{prefixo}DataInicioPrevisto")), "DataTerminoPrevisto": _data_ou_none(form.get(f"{prefixo}DataTerminoPrevisto")),
            "NumeroParcelas": _int_ou_none(form.get(f"{prefixo}NumeroParcelas")), "DataInicioVencimento": _data_ou_none(form.get(f"{prefixo}DataInicioVencimento")),
            "FaturamentoBrutoMensal": _decimal_ou_none(form.get(f"{prefixo}FaturamentoBrutoMensal")), "PercentualPermuta": _decimal_ou_none(form.get(f"{prefixo}PercentualPermuta")),
            "ValorPermuta": _decimal_ou_none(form.get(f"{prefixo}ValorPermuta")), "FaturamentoLiquidoPermuta": _decimal_ou_none(form.get(f"{prefixo}FaturamentoLiquidoPermuta")),
            "FaturamentoLiquidoMensal": _decimal_ou_none(form.get(f"{prefixo}FaturamentoLiquidoMensal")), "FaturamentoLiquidoFinalMensal": _decimal_ou_none(form.get(f"{prefixo}FaturamentoLiquidoFinalMensal")),
            "PercentualComissaoVendedor": _decimal_ou_none(form.get(f"{prefixo}PercentualComissaoVendedor")), "ValorVendedor": _decimal_ou_none(form.get(f"{prefixo}ValorVendedor")),
            "ValorVendedorTotal": _decimal_ou_none(form.get(f"{prefixo}ValorVendedorTotal")), "Status": _texto_ou_none(form.get(f"{prefixo}Status")), "OBS": _texto_ou_none(form.get(f"{prefixo}OBS")),
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
                  ,fsci.[IDDimCheckingHistorico]
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
        item["DataLancamentoInput"] = _data_para_input_date(item.get("DataLancamento"))
        item["DataAssinaturaRenovacaoInput"] = _data_para_input_date(item.get("DataAssinaturaRenovacao"))
        item["DataInicioPrevistoInput"] = _data_para_input_date(item.get("DataInicioPrevisto"))
        item["DataTerminoPrevistoInput"] = _data_para_input_date(item.get("DataTerminoPrevisto"))
        item["DataInicioVencimentoInput"] = _data_para_input_date(item.get("DataInicioVencimento"))
        item["DataCancelamentoInput"] = _data_para_input_date(item.get("DataCancelamento"))
        item["DataFimEfetivaInput"] = _data_para_input_date(item.get("DataFimEfetiva"))
        itens.append(item)

    return {
        "solicitacao": cab,
        "itens": itens,
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

            if acao == "salvar":
                _sincronizar_contato_contrato_se_fase_4(
                    id_fato_kanban_card=id_card,
                    id_empresa=id_empresa,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    id_fato_controle_contratos=id_fato_controle,
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

                db.session.commit()
                flash("Alterações salvas com sucesso.", "success")
                return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

            if acao == "aprovar":
                resultado_aprovacao = _mover_solicitacao_aprovada_para_controle(
                    id_solicitacao=int(id_solicitacao),
                    id_usuario_logado=id_usuario_logado,
                )

                id_fato_controle = _int_ou_none(resultado_aprovacao.get("id_contrato_controle"))
                ids_itens_controle = resultado_aprovacao.get("ids_itens_controle") or []
                id_card = _int_ou_none(resultado_aprovacao.get("id_card"))
                id_empresa = _int_ou_none(resultado_aprovacao.get("id_empresa"))
                id_empresa_proprietaria = _int_ou_none(resultado_aprovacao.get("id_empresa_proprietaria"))
                tipo_solicitacao = resultado_aprovacao.get("tipo_solicitacao") or tipo_solicitacao

                cab_aprovada = _obter_cabecalho_solicitacao_bruta(int(id_solicitacao)) or {}
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
                    form=request.form,
                    cabecalho_solicitacao=cab_aprovada,
                )
                _upsert_destinatarios_externos_contrato(
                    id_fato_controle_contratos=id_fato_controle,
                    id_empresa_destinatario=id_empresa,
                    id_empresa=id_empresa,
                    ids_itens_controle=ids_itens_controle,
                )

                _aplicar_resultado_aprovacao_no_card(
                    id_fato_kanban_card=id_card,
                    id_usuario_logado=id_usuario_logado,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    aprovar=True,
                )

                id_dim_tipo_documento = _int_ou_none(cab_aprovada.get("IDDimTipoDocumento"))
                if id_dim_tipo_documento in (None, "", 0):
                    id_dim_tipo_documento = _resolver_id_dim_tipo_documento_admin(
                        cab_aprovada.get("TipoDocumento"),
                        id_empresa_proprietaria,
                    )

                if _card_admin_esta_na_fase_formulario_contrato(id_card):
                    _registrar_ocorrencia_card_tipo_documento_admin(
                        id_fato_kanban_card=id_card,
                        id_dim_tipo_documento=id_dim_tipo_documento,
                        id_usuario_logado=id_usuario_logado,
                        id_empresa_proprietaria=id_empresa_proprietaria,
                        id_fato_solicitacao=int(id_solicitacao),
                        id_fato_controle_contratos=id_fato_controle,
                        tipo_ocorrencia="APROVADO",
                        observacao="Card aprovado na fase 4 pela tela admin/aprovacao/contratos.",
                    )

                _registrar_historico_contrato_euromidia(
                    id_fato_controle_contratos=id_fato_controle,
                    id_fato_solicitacao=int(id_solicitacao),
                    id_dim_acao=_obter_id_dim_acao_solicitacao_contrato("APROVADO", fallback=1),
                    id_empresa=id_empresa,
                    id_empresa_proprietaria=id_empresa_proprietaria,
                    id_fato_kanban_card=id_card,
                    tipo_evento="APROVADO",
                    tipo_solicitacao=tipo_solicitacao,
                    descricao_evento="Solicitação aprovada e movida para Controle de Contratos Euromídia.",
                    id_dim_usuario_acao=id_usuario_logado,
                )

                db.session.commit()
                flash("Solicitação aprovada com sucesso.", "success")
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
        diagrama_status=dados["diagrama_status"],
    )
