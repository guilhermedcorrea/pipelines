from flask_sqlalchemy import SQLAlchemy
from ..extensions import db,limiter
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify,current_app,abort
from ..models.admin_models import FatoMovimentoFinanceiroEmpresas, DimEmpresaProprietaria,DimProdutoAuvo
from datetime import datetime, date, timedelta
from sqlalchemy import func, case,text
from flask_login import login_required, current_user
from ..autenticacao.autenticacao_views import requer_permissao
from pathlib import Path




admin = Blueprint("admin", __name__)



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
              ,ep.[Logo] AS [LogoEmpresaProprietaria]
              ,ep.[RazaoSocial] AS [RazaoSocialEmpresaProprietaria]
        FROM [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia] fsce
        INNER JOIN [Integracao].[Silver].[DimUsuarios] du
                ON du.[IDDimUsuarios] = fsce.[IDDimUsuariosCriacao]
        INNER JOIN [Integracao].[Silver].[DimEmpresas] de
                ON de.[IDEmpresa] = fsce.[IDEmpresa]
        INNER JOIN [Integracao].[dbo].[EmpresaProprietaria] ep
                ON ep.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        WHERE fsce.[IDFatoSolicitacaoContratoEuromidia] = :id_solicitacao
    """)

    sql_itens = text("""
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
              ,fsci.[CodPonto]
              ,fsci.[CodFace]
              ,fsci.[DataLancamento]
              ,fsci.[Cota]
              ,fsci.[CidadeExibicao]
              ,fsci.[Tipo]
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
              ,df.[Tipo] AS [TipoFaceCadastro]
              ,dp.[Cidade] AS [CidadePainelCadastro]
              ,dp.[UF] AS [UFPainelCadastro]
              ,dp.[Tipo] AS [TipoPainelCadastro]
              ,dp.[Logradouro] AS [LogradouroPainelCadastro]
              ,dp.[Bairro] AS [BairroPainelCadastro]
              ,dp.[Referencia] AS [ReferenciaPainelCadastro]
        FROM [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia] fsci
        LEFT JOIN [Integracao].[Silver].[DimFacesPaineis] df
               ON df.[IDDimFacesPaineis] = fsci.[IDDimFacesPaineis]
        LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] dp
               ON dp.[IDDimPaineisEuromidia] = COALESCE(fsci.[IDPainelEuromidia], df.[IDDimPaineisEuromidia])
        WHERE fsci.[IDFatoSolicitacaoContratoEuromidia] = :id_solicitacao
        ORDER BY fsci.[IDFatoSolicitacaoContratoItemEuromidia] ASC
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
        WHERE
            (
                :q = ''
                OR CAST(fsce.[IDFatoSolicitacaoContratoEuromidia] AS varchar(50)) LIKE '%' + :q + '%'
                OR CAST(fsce.[IDFatoKanbanCard] AS varchar(50)) LIKE '%' + :q + '%'
                OR ISNULL(fsce.[CNPJ], '') LIKE '%' + :q + '%'
                OR ISNULL(de.[RazaoSocial], '') LIKE '%' + :q + '%'
                OR ISNULL(du.[NomeUsuario], '') LIKE '%' + :q + '%'
                OR ISNULL(fsce.[TipoSolicitacao], '') LIKE '%' + :q + '%'
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
        FROM [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia] fsce
        INNER JOIN [Integracao].[Silver].[DimUsuarios] du
            ON du.[IDDimUsuarios] = fsce.[IDDimUsuariosCriacao]
        INNER JOIN [Integracao].[Silver].[DimEmpresas] de
            ON de.[IDEmpresa] = fsce.[IDEmpresa]
        INNER JOIN [Integracao].[dbo].[EmpresaProprietaria] ep
            ON ep.[IDEmpresaProprietaria] = fsce.[IDEmpresaProprietaria]
        WHERE
            (
                :q = ''
                OR CAST(fsce.[IDFatoSolicitacaoContratoEuromidia] AS varchar(50)) LIKE '%' + :q + '%'
                OR CAST(fsce.[IDFatoKanbanCard] AS varchar(50)) LIKE '%' + :q + '%'
                OR ISNULL(fsce.[CNPJ], '') LIKE '%' + :q + '%'
                OR ISNULL(de.[RazaoSocial], '') LIKE '%' + :q + '%'
                OR ISNULL(du.[NomeUsuario], '') LIKE '%' + :q + '%'
                OR ISNULL(fsce.[TipoSolicitacao], '') LIKE '%' + :q + '%'
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

            tipo_solicitacao = _tipo_solicitacao_normalizado(request.form.get("TipoSolicitacao"))

            params_cab = {
                "id_solicitacao": int(id_solicitacao),
                "TipoSolicitacao": tipo_solicitacao,
                "Referencia": _texto_ou_none(request.form.get("Referencia")),
                "NumeroContrato": _texto_ou_none(request.form.get("NumeroContrato")),
                "NumeroPrevia": _texto_ou_none(request.form.get("NumeroPrevia")),
                "CNPJ": _texto_ou_none(request.form.get("CNPJ")),
                "DataAssinaturaRenovacao": _data_ou_none(request.form.get("DataAssinaturaRenovacao")),
                "IDTrimestre": _texto_ou_none(request.form.get("IDTrimestre")),
                "DataLancamento": _data_ou_none(request.form.get("DataLancamento")),
                "RazaoSocial": _texto_ou_none(request.form.get("RazaoSocial")),
                "CPF": _texto_ou_none(request.form.get("CPF")),
                "MarcaExibida": _texto_ou_none(request.form.get("MarcaExibida")),
                "Vendedor": _texto_ou_none(request.form.get("Vendedor")),
                "TipoDocumento": _texto_ou_none(request.form.get("TipoDocumento")),
                "Origem": _texto_ou_none(request.form.get("Origem")),
                "SDR": _texto_ou_none(request.form.get("SDR")),
                "Agencia": _texto_ou_none(request.form.get("Agencia")),
                "CnpjAgencia": _texto_ou_none(request.form.get("CnpjAgencia")),
                "Bureau": _texto_ou_none(request.form.get("Bureau")),
                "CnpjBureau": _texto_ou_none(request.form.get("CnpjBureau")),
                "Intermediario": _texto_ou_none(request.form.get("Intermediario")),
                "CnpjIntermediario": _texto_ou_none(request.form.get("CnpjIntermediario")),
                "QuantidadePontos": _int_ou_none(request.form.get("QuantidadePontos")),
                "QuantidadeFaces": _int_ou_none(request.form.get("QuantidadeFaces")),
                "TotalFaturamentoBrutoMensal": _decimal_ou_none(request.form.get("TotalFaturamentoBrutoMensal")),
                "TotalPercentualPermuta": _decimal_ou_none(request.form.get("TotalPercentualPermuta")),
                "TotalCotaOportunidade": _decimal_ou_none(request.form.get("TotalCotaOportunidade")),
                "TotalValorPermuta": _decimal_ou_none(request.form.get("TotalValorPermuta")),
                "TotalFaturamentoLiquidoPermuta": _decimal_ou_none(request.form.get("TotalFaturamentoLiquidoPermuta")),
                "TotalBrutoContrato": _decimal_ou_none(request.form.get("TotalBrutoContrato")),
                "TotalLiquidoContratoAGBRCTACORDO": _decimal_ou_none(request.form.get("TotalLiquidoContratoAGBRCTACORDO")),
                "TotalLiquidoContratoAGBRVENDGERCOOR": _decimal_ou_none(request.form.get("TotalLiquidoContratoAGBRVENDGERCOOR")),
                "TotalPercentualAgencia": _decimal_ou_none(request.form.get("TotalPercentualAgencia")),
                "TotalValorMensalAgencia": _decimal_ou_none(request.form.get("TotalValorMensalAgencia")),
                "TotalPercentualBureau": _decimal_ou_none(request.form.get("TotalPercentualBureau")),
                "TotalValorBureauMensal": _decimal_ou_none(request.form.get("TotalValorBureauMensal")),
                "TotalPercentualCartaAcordo": _decimal_ou_none(request.form.get("TotalPercentualCartaAcordo")),
                "TotalValorCartaAcordoMensal": _decimal_ou_none(request.form.get("TotalValorCartaAcordoMensal")),
                "TotalValorOutrasComissoes": _decimal_ou_none(request.form.get("TotalValorOutrasComissoes")),
                "TotalFaturamentoLiquidoMensal": _decimal_ou_none(request.form.get("TotalFaturamentoLiquidoMensal")),
                "TotalPercentualComissaoVendedor": _decimal_ou_none(request.form.get("TotalPercentualComissaoVendedor")),
                "TotalValorVendedor": _decimal_ou_none(request.form.get("TotalValorVendedor")),
                "ValorVendedorTotal": _decimal_ou_none(request.form.get("ValorVendedorTotal")),
                "TotalPercentualComissaoCoordenacao": _decimal_ou_none(request.form.get("TotalPercentualComissaoCoordenacao")),
                "Observacao": _texto_ou_none(request.form.get("Observacao")),
                "MotivoRejeicao": _texto_ou_none(request.form.get("MotivoRejeicao")),
                "MotivoCancelamento": _texto_ou_none(request.form.get("MotivoCancelamento")),
            }

            sql_update_cab = text("""
                UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
                   SET [TipoSolicitacao] = :TipoSolicitacao
                      ,[Referencia] = :Referencia
                      ,[NumeroContrato] = :NumeroContrato
                      ,[NumeroPrevia] = :NumeroPrevia
                      ,[CNPJ] = :CNPJ
                      ,[DataAssinaturaRenovacao] = :DataAssinaturaRenovacao
                      ,[IDTrimestre] = :IDTrimestre
                      ,[DataLancamento] = :DataLancamento
                      ,[RazaoSocial] = :RazaoSocial
                      ,[CPF] = :CPF
                      ,[MarcaExibida] = :MarcaExibida
                      ,[Vendedor] = :Vendedor
                      ,[TipoDocumento] = :TipoDocumento
                      ,[Origem] = :Origem
                      ,[SDR] = :SDR
                      ,[Agencia] = :Agencia
                      ,[CnpjAgencia] = :CnpjAgencia
                      ,[Bureau] = :Bureau
                      ,[CnpjBureau] = :CnpjBureau
                      ,[Intermediario] = :Intermediario
                      ,[CnpjIntermediario] = :CnpjIntermediario
                      ,[QuantidadePontos] = :QuantidadePontos
                      ,[QuantidadeFaces] = :QuantidadeFaces
                      ,[TotalFaturamentoBrutoMensal] = :TotalFaturamentoBrutoMensal
                      ,[TotalPercentualPermuta] = :TotalPercentualPermuta
                      ,[TotalCotaOportunidade] = :TotalCotaOportunidade
                      ,[TotalValorPermuta] = :TotalValorPermuta
                      ,[TotalFaturamentoLiquidoPermuta] = :TotalFaturamentoLiquidoPermuta
                      ,[TotalBrutoContrato] = :TotalBrutoContrato
                      ,[TotalLiquidoContratoAGBRCTACORDO] = :TotalLiquidoContratoAGBRCTACORDO
                      ,[TotalLiquidoContratoAGBRVENDGERCOOR] = :TotalLiquidoContratoAGBRVENDGERCOOR
                      ,[TotalPercentualAgencia] = :TotalPercentualAgencia
                      ,[TotalValorMensalAgencia] = :TotalValorMensalAgencia
                      ,[TotalPercentualBureau] = :TotalPercentualBureau
                      ,[TotalValorBureauMensal] = :TotalValorBureauMensal
                      ,[TotalPercentualCartaAcordo] = :TotalPercentualCartaAcordo
                      ,[TotalValorCartaAcordoMensal] = :TotalValorCartaAcordoMensal
                      ,[TotalValorOutrasComissoes] = :TotalValorOutrasComissoes
                      ,[TotalFaturamentoLiquidoMensal] = :TotalFaturamentoLiquidoMensal
                      ,[TotalPercentualComissaoVendedor] = :TotalPercentualComissaoVendedor
                      ,[TotalValorVendedor] = :TotalValorVendedor
                      ,[ValorVendedorTotal] = :ValorVendedorTotal
                      ,[TotalPercentualComissaoCoordenacao] = :TotalPercentualComissaoCoordenacao
                      ,[Observacao] = :Observacao
                      ,[MotivoRejeicao] = :MotivoRejeicao
                      ,[MotivoCancelamento] = :MotivoCancelamento
                      ,[DataAtualizacao] = GETDATE()
                 WHERE [IDFatoSolicitacaoContratoEuromidia] = :id_solicitacao
            """)

            db.session.execute(sql_update_cab, params_cab)

            item_ids = request.form.getlist("item_id")

            sql_update_item = text("""
                UPDATE [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]
                   SET [IDPainelEuromidia] = :IDPainelEuromidia
                      ,[IDDimFacesPaineis] = :IDDimFacesPaineis
                      ,[CodPonto] = :CodPonto
                      ,[CodFace] = :CodFace
                      ,[CidadeExibicao] = :CidadeExibicao
                      ,[Tipo] = :Tipo
                      ,[Cota] = :Cota
                      ,[DataInicioPrevisto] = :DataInicioPrevisto
                      ,[DataTerminoPrevisto] = :DataTerminoPrevisto
                      ,[NumeroParcelas] = :NumeroParcelas
                      ,[DataInicioVencimento] = :DataInicioVencimento
                      ,[FaturamentoBrutoMensal] = :FaturamentoBrutoMensal
                      ,[PercentualPermuta] = :PercentualPermuta
                      ,[ValorPermuta] = :ValorPermuta
                      ,[FaturamentoLiquidoPermuta] = :FaturamentoLiquidoPermuta
                      ,[FaturamentoLiquidoMensal] = :FaturamentoLiquidoMensal
                      ,[FaturamentoLiquidoFinalMensal] = :FaturamentoLiquidoFinalMensal
                      ,[PercentualComissaoVendedor] = :PercentualComissaoVendedor
                      ,[ValorVendedor] = :ValorVendedor
                      ,[ValorVendedorTotal] = :ValorVendedorTotal
                      ,[Status] = :Status
                      ,[OBS] = :OBS
                      ,[BitAtivo] = :BitAtivo
                      ,[DataAtualizacao] = GETDATE()
                      ,[IDDimUsuariosAtualizacao] = :IDDimUsuariosAtualizacao
                 WHERE [IDFatoSolicitacaoContratoItemEuromidia] = :IDFatoSolicitacaoContratoItemEuromidia
                   AND [IDFatoSolicitacaoContratoEuromidia] = :IDFatoSolicitacaoContratoEuromidia
            """)

            for item_id_raw in item_ids:
                item_id = _int_ou_none(item_id_raw)
                if item_id is None:
                    continue

                prefixo = f"item_{item_id}__"

                cod_ponto = _texto_ou_none(request.form.get(f"{prefixo}CodPonto"))
                cod_face = _texto_ou_none(request.form.get(f"{prefixo}CodFace"))

                info_face = None
                if cod_ponto and cod_face:
                    info_face = _resolver_face_e_painel_por_codigos(cod_ponto, cod_face)
                    if not info_face:
                        raise ValueError(
                            f"Não encontrei CodPonto/CodFace válidos para o item {item_id}: {cod_ponto} / {cod_face}."
                        )

                params_item = {
                    "IDFatoSolicitacaoContratoItemEuromidia": item_id,
                    "IDFatoSolicitacaoContratoEuromidia": int(id_solicitacao),
                    "IDPainelEuromidia": info_face.get("IDDimPaineisEuromidia") if info_face else None,
                    "IDDimFacesPaineis": info_face.get("IDDimFacesPaineis") if info_face else None,
                    "CodPonto": cod_ponto,
                    "CodFace": cod_face,
                    "CidadeExibicao": _texto_ou_none(request.form.get(f"{prefixo}CidadeExibicao")),
                    "Tipo": _texto_ou_none(request.form.get(f"{prefixo}Tipo")),
                    "Cota": _decimal_ou_none(request.form.get(f"{prefixo}Cota")),
                    "DataInicioPrevisto": _data_ou_none(request.form.get(f"{prefixo}DataInicioPrevisto")),
                    "DataTerminoPrevisto": _data_ou_none(request.form.get(f"{prefixo}DataTerminoPrevisto")),
                    "NumeroParcelas": _int_ou_none(request.form.get(f"{prefixo}NumeroParcelas")),
                    "DataInicioVencimento": _data_ou_none(request.form.get(f"{prefixo}DataInicioVencimento")),
                    "FaturamentoBrutoMensal": _decimal_ou_none(request.form.get(f"{prefixo}FaturamentoBrutoMensal")),
                    "PercentualPermuta": _decimal_ou_none(request.form.get(f"{prefixo}PercentualPermuta")),
                    "ValorPermuta": _decimal_ou_none(request.form.get(f"{prefixo}ValorPermuta")),
                    "FaturamentoLiquidoPermuta": _decimal_ou_none(request.form.get(f"{prefixo}FaturamentoLiquidoPermuta")),
                    "FaturamentoLiquidoMensal": _decimal_ou_none(request.form.get(f"{prefixo}FaturamentoLiquidoMensal")),
                    "FaturamentoLiquidoFinalMensal": _decimal_ou_none(request.form.get(f"{prefixo}FaturamentoLiquidoFinalMensal")),
                    "PercentualComissaoVendedor": _decimal_ou_none(request.form.get(f"{prefixo}PercentualComissaoVendedor")),
                    "ValorVendedor": _decimal_ou_none(request.form.get(f"{prefixo}ValorVendedor")),
                    "ValorVendedorTotal": _decimal_ou_none(request.form.get(f"{prefixo}ValorVendedorTotal")),
                    "Status": _texto_ou_none(request.form.get(f"{prefixo}Status")),
                    "OBS": _texto_ou_none(request.form.get(f"{prefixo}OBS")),
                    "BitAtivo": 1 if request.form.get(f"{prefixo}BitAtivo") == "1" else 0,
                    "IDDimUsuariosAtualizacao": id_usuario_logado,
                }

                db.session.execute(sql_update_item, params_item)

            db.session.commit()
            flash("Solicitação salva com sucesso.", "success")
            return redirect(url_for("admin.detalhe_aprovacao_contrato", id_solicitacao=id_solicitacao))

        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao salvar a solicitação: {str(exc)}", "danger")

    payload = _obter_solicitacao_contrato_detalhe(id_solicitacao)
    if not payload:
        abort(404)

    return render_template(
        "admin/aprovacao_contrato_detalhe.html",
        solicitacao=payload["solicitacao"],
        itens=payload["itens"],
    )