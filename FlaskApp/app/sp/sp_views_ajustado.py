from flask import Blueprint, render_template, request, redirect, url_for, session,flash, jsonify,current_app,send_from_directory, send_file,abort
from flask_login import current_user, login_required
from datetime import date, datetime, timedelta
from ..models.euro_models import (Produto,Caracteristica,CategoriasProdutos,Saldo,Ativo
                                 ,Familia,Movimentacao,Funcionario,Empresa,EstoqueMatriz,EstoqueEuro
                                 ,EstoqueSp,Departamento,GruposCompativeis,Pmv,EstoqueManutencaoInterna,EstoqueContainer,
                                 EstoqueManutencaoExterna,Usuario,DiagramaProduto,Pedidos,PedidoItens,EstoqueLotes,PedidoLotes
                                 ,EstoqueSerie,PedidoItemSerie,EntradasProdutos,Projeto,TipoEstoque,EstoqueEuroMatriz,VersaoFirmewire
                                 ,ImagensProdutos,StatusPedido,ObservacoesPedidos,AnexosPedidos,TabelaPrecos,PrecosProdutos
                                 ,ProdutoComposicao,NotaDebito,NotaDebitoItem,PontosEuro,ComposicaoAtivo,MovimentacaoAtivo,
                                 TipoOperacao,MovimentacaoPecas,PedidoAtivo,AtivoAuvo,ClienteAuvo,ClienteEmpresa,Medicao,Contrato
                                 ,AprovarMedicao,MedicoesAprovada,TipoContrato,EtiquetaProduto,ContratoOmie,FluxoMedicao,OmieOS,LancamentoOmie
                                 ,PagamentoMedicoesAtrasados,ContratoOmieItens,MedicoesItens,AprovarMedicoesItens,ClienteOmie,OsItens
                                 ,EmpresaProprietaria,LogMedicoes,VendedoresOmie,CategoriafinanceiraOmie,ProjetosOmie)

from ..extensions import db,ALLOWED_EXTENSIONS
from ..autenticacao.acl_menu_paineis import requer_acesso_catalogo_produtos, requer_item_menu_paineis


from sqlalchemy import or_, cast, String,func,text,bindparam,literal_column,Date as SQLDate,literal
import math
from functools import wraps
import os
import json
import csv
import io
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql import func, distinct
from sqlalchemy.orm import aliased
from collections import defaultdict
from sqlalchemy.exc import SQLAlchemyError
from dateutil.relativedelta import relativedelta
from calendar import monthrange
from uuid import uuid4
from markupsafe import Markup
from werkzeug.utils import secure_filename
from pathlib import Path
from urllib.parse import quote_plus
from sqlalchemy import and_,func, distinct, extract
import logging
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
#import barcode
#from barcode.writer import ImageWriter
#import numpy as np
#import pytesseract
#from pytesseract import Output
import tempfile
#import os, re, cv2,functools
#import unicodedata
from types import SimpleNamespace
from io import BytesIO
from openpyxl import Workbook
import time, random
#from geopy.geocoders import Nominatim
from typing import Dict, Any, List, Optional
from functools import wraps
from sqlalchemy.exc import OperationalError





euro = Blueprint('euro', __name__, url_prefix='/admin/sp/v1')
SP_BIND_KEY = "sp"


def _quantidade_total_carrinho():
    """Retorna a quantidade total de unidades guardadas no carrinho da sessão."""
    cart = session.get('cart', {}) or {}
    if not isinstance(cart, dict):
        return 0

    total = 0
    for entrada in cart.values():
        if isinstance(entrada, dict):
            entrada = entrada.get('qty', 0)

        try:
            quantidade = int(entrada or 0)
        except (TypeError, ValueError):
            quantidade = 0

        total += max(quantidade, 0)

    return total


@euro.app_context_processor
def contexto_checkout_sp():
    """Disponibiliza o contador do carrinho no cabeçalho compartilhado."""
    return {'sp_checkout_qtd': _quantidade_total_carrinho()}


def _sp_engine():
    """Retorna a engine configurada exclusivamente para o banco Sp."""
    return db.engines[SP_BIND_KEY]


def _sp_execute(statement, params=None):
    """Executa SQL textual no bind Sp dentro da sessão atual."""
    return db.session.execute(
        statement,
        params,
        bind_arguments={"bind": _sp_engine()},
    )





def _id_usuario_compatibilidade_euro():
    """Mantém o ID usado nas rotinas antigas do Sp sem autenticar por ele."""
    email = str(getattr(current_user, 'Email', '') or '').strip()

    if email:
        try:
            usuario_euro = (
                db.session.query(Usuario)
                .filter(Usuario.EmailUsuario == email)
                .first()
            )
            if usuario_euro is not None:
                return int(usuario_euro.IDUsuario)
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Não foi possível vincular o usuário autenticado ao cadastro legado do Euro."
            )

    try:
        return int(current_user.get_id())
    except (TypeError, ValueError):
        return None


@euro.before_request
def exigir_autenticacao_central():
    """Protege todo o blueprint com a autenticação de autenticacao_views.py."""
    if not current_user.is_authenticated:
        proximo_destino = request.url
        if request.endpoint in {'euro.login', 'euro.logout'}:
            proximo_destino = url_for('euro.lista_produtos')

        login_url = url_for('Autenticacao.login', next=proximo_destino)

        if request.path.startswith(f'{euro.url_prefix}/api/') or request.is_json:
            return jsonify({
                'erro': 'Autenticação necessária.',
                'login_url': login_url,
            }), 401

        flash("Faça login para acessar o módulo Euro.", "warning")
        return redirect(login_url)

    id_autenticacao = str(current_user.get_id() or '')
    if session.get('_euro_id_autenticacao') != id_autenticacao:
        id_usuario_euro = _id_usuario_compatibilidade_euro()
        if id_usuario_euro is None:
            session.pop('user_id', None)
        else:
            session['user_id'] = id_usuario_euro
        session['_euro_id_autenticacao'] = id_autenticacao







def _calcular_total_pedido(id_pedido: int) -> float:
    """
    Soma (Quantidade * ValorUnitario) de todos os itens do pedido.
    Usa COALESCE para tratar nulos como zero e retorna float.
    """
    total = (
        db.session.query(
            func.sum(
                func.coalesce(PedidoItens.Quantidade, 0) * func.coalesce(PedidoItens.ValorUnitario, 0)
            )
        )
        .filter(PedidoItens.IDPedido == id_pedido)
        .scalar()
    )
    return float(total or 0.0)







_TRANSIENT_SQLSTATE = ("08S01",)           
_TRANSIENT_MSG = ("10054", "10060", "timeout", "communication link failure", "reset by peer")

def _is_disconnect_error(exc: OperationalError) -> bool:
    s = str(getattr(exc, "orig", exc)).lower()
    return any(code.lower() in s for code in _TRANSIENT_SQLSTATE) or any(m in s for m in _TRANSIENT_MSG)

def _cleanup_session_and_pool(db):
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        db.session.close()
    except Exception:
        pass
    # Agressivo: descarta o pool para forçar novas conexões
    try:
        _sp_engine().dispose()
    except Exception:
        pass

def retry_get_view(db, attempts: int = 6, base_delay: float = 0.2, max_delay: float = 1.5):
    """
    Decorador: reexecuta o view em caso de erro de desconexão **apenas em GET**.
    Não aplica retry em POST/PUT/DELETE para evitar duplicação de efeitos.
    """
    def _decorator(fn):
        @wraps(fn)
        def _wrapped(*args, **kwargs):
            if request.method != "GET":
                return fn(*args, **kwargs)

            last_exc = None
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except OperationalError as e:
                    if not _is_disconnect_error(e):
                        raise
                    last_exc = e
                    _cleanup_session_and_pool(db)
                    # backoff exponencial com jitter
                    sleep_s = min(max_delay, base_delay * (2 ** i)) + random.uniform(0, 0.15)
                    time.sleep(sleep_s)
                except Exception:
                    # Não “mascara” outros erros
                    raise
            # Se esgotou as tentativas, propaga o último erro
            raise last_exc
        return _wrapped
    return _decorator







APP_KEY_SH53 = os.getenv("APP_KEY_SH53")
APP_SECRET_SH53 = os.getenv("APP_SECRET_SH53")
APP_KEY_SINAMOVEL = os.getenv("APP_KEY_SINAMOVEL")
APP_SECRET_SINAMOVEL = os.getenv("APP_SECRET_SINAMOVEL")

def get_omie_credentials(id_empresa_proprietaria: int) -> tuple[str, str]:
    if id_empresa_proprietaria == 1:
        return APP_KEY_SH53, APP_SECRET_SH53
    if id_empresa_proprietaria == 2:
        return APP_KEY_SINAMOVEL, APP_SECRET_SINAMOVEL
    raise ValueError(f"IDEmpresaProprietaria inesperado: {id_empresa_proprietaria}")




def get_ncodcc(id_empresa_proprietaria: int) -> int:
    if id_empresa_proprietaria == 1:
        return 277_138_738
    if id_empresa_proprietaria == 2:
        
        return 573_091_015 
    raise ValueError(f"IDEmpresaProprietaria inesperado: {id_empresa_proprietaria}")






def registrar_log_operacao(med, usuario_id, novo_estado):
    anterior = med.IDOperacao
    med.IDOperacao = novo_estado
    log = LogMedicoes(
        NumeroMedicao = med.NumeroMedicao,
        IDOperacaoAnterior = anterior,
        IDOperacaoAtual = novo_estado,
        IDUsuario = usuario_id
    )
    db.session.add(log)





@euro.route('/anexos/<path:subpath>')
@login_required
def serve_anexo(subpath):
    base = os.path.join(current_app.root_path, 'anexos')
    full = os.path.join(base, subpath)
    if not os.path.isfile(full):
        abort(404)
    return send_from_directory(directory=base, path=subpath, as_attachment=False)




def get_item(item_id):
    return db.session.query(
        Produto.IDItem,
        Produto.ReferenciaExterna,
        Produto.IDCategoriaProduto, 
        Produto.IDDepartamento,      
        Produto.FamiliaID,           
        Produto.PmvID,             
        CategoriasProdutos.NomeCategoria,
        Produto.NomeProduto,
        Produto.BitAtivo.label("StatusComponente"),
        Produto.IDAtivo.label("AtivoAssociado"),
        Familia.NomeFamilia,
        Ativo.NomeAtivo
    ).outerjoin(
        Familia, Produto.FamiliaID == Familia.FamiliaID
    ).outerjoin(
        CategoriasProdutos, Produto.IDCategoriaProduto == CategoriasProdutos.IDCategoria
    ).outerjoin(
        Ativo, Produto.IDAtivo == Ativo.IDAtivo
    ).filter(
        Produto.IDItem == item_id
    ).first()



def _normalizar_nome_arquivo_imagem(valor):
    """Extrai somente o nome do arquivo, aceitando caminhos Windows ou Linux."""
    if valor is None:
        return ''

    caminho = str(valor).strip().replace('\\', '/')
    return os.path.basename(caminho.rstrip('/'))


_EXTENSOES_IMAGEM_PRODUTO = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def _pasta_imagens_produtos():
    """Resolve a pasta real das imagens de produtos dentro de app/static."""
    pasta = current_app.config.get('PRODUTOS_UPLOAD_FOLDER')

    if not pasta:
        pasta = os.path.join(
            current_app.root_path,
            'static',
            'imagens',
            'produtos',
        )
    elif not os.path.isabs(pasta):
        pasta = os.path.join(current_app.root_path, pasta)

    return os.path.realpath(pasta)


def _caminho_imagem_produto(valor):
    """Monta um caminho seguro dentro da pasta exclusiva de imagens de produtos."""
    filename = _normalizar_nome_arquivo_imagem(valor)
    if not filename:
        return None

    return os.path.join(_pasta_imagens_produtos(), filename)





@euro.route('/imagensprodutos/<path:filename>')
@login_required
@requer_acesso_catalogo_produtos
def imagem_produto(filename):
    filename = _normalizar_nome_arquivo_imagem(filename)
    if not filename:
        abort(404)

    return send_from_directory(_pasta_imagens_produtos(), filename)





def _build_display_pages(cur, total):
    if total <= 7:
        return list(range(1, total + 1))

    pages = [1, 2]
    left  = max(3,    cur - 1)
    right = min(total - 2, cur + 1)

    if left > 3:
        pages.append('...')
    pages += list(range(left, right + 1))
    if right < total - 2:
        pages.append('...')
    pages += [total - 1, total]
    return pages




@euro.route('/lista_produtos')
@login_required
@requer_acesso_catalogo_produtos
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def lista_produtos():
    page = request.args.get('page', 1, type=int)
    per_page  = 18
    search = request.args.get('search', '').strip()

    other_args = request.args.to_dict()
    other_args.pop('page', None)

    query = db.session.query(
        Produto.IDItem,
        Produto.ReferenciaExterna,
        Produto.NomeProduto,
        CategoriasProdutos.NomeCategoria,
        Produto.BitAtivo,
        EstoqueMatriz.Saldo.label('Quantidade')
    ).outerjoin(
        CategoriasProdutos, Produto.IDCategoriaProduto == CategoriasProdutos.IDCategoria
    ).outerjoin(
        Ativo, Produto.IDAtivo == Ativo.IDAtivo
    ).outerjoin(
        EstoqueMatriz, EstoqueMatriz.IDItem == Produto.IDItem
    )

    if search:
        series_subq = (
            db.session.query(EstoqueSerie.IDItem)
            .filter(EstoqueSerie.NumeroSerie.ilike(f'%{search}%'))
            .distinct()
            .subquery()
        )

        query = query.filter(
            or_(
                cast(Produto.IDItem, String).ilike(f'%{search}%'),
                Produto.ReferenciaExterna.ilike(f'%{search}%'),
                Produto.NomeProduto.ilike(f'%{search}%'),
                Produto.IDItem.in_(series_subq)
            )
        )

    fcar = request.args.get('fcar', '').strip()
    fval = request.args.get('fval', '').strip()
    if fcar and fval:
        query = query.join(
            Caracteristica, Caracteristica.IDItem == Produto.IDItem
        ).filter(
            Caracteristica.Caracteristica == fcar,
            Caracteristica.Valor        == fval,
            Caracteristica.BitFiltro    == True
        )


    query = query.order_by(Produto.IDItem)
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page
    cart  = session.get('cart', {})


    filtro_data = db.session.query(
        Caracteristica.Caracteristica,
        Caracteristica.Valor
    ).filter(Caracteristica.BitFiltro == True).distinct().all()
    filter_groups = defaultdict(set)
    for car, val in filtro_data:
        filter_groups[car].add(val)
    filter_groups = {k: sorted(v) for k, v in filter_groups.items()}

    item_ids = [i.IDItem for i in items]
    stock_map = defaultdict(list)
    consultas = [
        (EstoqueMatriz,'EstoqueMatriz',EstoqueMatriz.Saldo),
        (EstoqueContainer, 'EstoqueContainer', EstoqueContainer.Saldo),
        (EstoqueEuro, 'EstoqueEuro', EstoqueEuro.Saldo),
        (EstoqueEuroMatriz, 'EstoqueEuroMatriz', EstoqueEuroMatriz.Quantidade),
        (EstoqueManutencaoExterna, 'EstoqueManutencaoExterna', EstoqueManutencaoExterna.Saldo),
        (EstoqueManutencaoInterna, 'EstoqueManutencaoInterna', EstoqueManutencaoInterna.Saldo),
        (EstoqueSp,'EstoqueSp', EstoqueSp.Saldo),
    ]
    for model, nome, coluna in consultas:
        rows = (
            db.session.query(
                model.IDItem,
                func.coalesce(func.sum(coluna), 0).label('total')
            )
            .filter(model.IDItem.in_(item_ids))
            .group_by(model.IDItem)
            .all()
        )
        for iid, tot in rows:
            if tot > 0:
                stock_map[iid].append(f"{nome}: {int(tot)}")
    stock_summary = {iid: ", ".join(v) for iid, v in stock_map.items()}
    for iid in item_ids:
        stock_summary.setdefault(iid, '')

    imagens = db.session.query(
        ImagensProdutos.IDItem,
        ImagensProdutos.CaminhoArquivo
    ).filter(
        ImagensProdutos.IDItem.in_(item_ids),
        ImagensProdutos.Ordem == 1
    ).all()
    image_urls = {}
    for iid, caminho in imagens:
        arquivo = _normalizar_nome_arquivo_imagem(caminho)
        if arquivo:
            image_urls[iid] = url_for('euro.imagem_produto', filename=arquivo)
    for iid in item_ids:
        image_urls.setdefault(iid, None)

    display_pages = _build_display_pages(page, total_pages)

    return render_template(
        'sp/index.html',
        items= items,
        page = page,
        total_pages  = total_pages,
        display_pages = display_pages,
        cart = cart,
        search= search,
        filter_groups = filter_groups,
        fcar= fcar,
        fval= fval,
        stock_summary = stock_summary,
        image_urls = image_urls,
        other_args = other_args,
    )






def build_tree(root_id, parent_to_children, item_lookup, visited=None):
    if visited is None:
        visited = set()
    
    if root_id in visited:
        return {
            "id": root_id,
            "name": item_lookup.get(root_id, {}).get("name", "Desconhecido"),
            "category": item_lookup.get(root_id, {}).get("category", "-"),
            "department": item_lookup.get(root_id, {}).get("department", "-"),
            "reference": item_lookup.get(root_id, {}).get("ref", ""),
            "bitPrincipal": item_lookup.get(root_id, {}).get("bitPrincipal", False),
            "children": []
        }
    visited.add(root_id)
    
    node_info = item_lookup.get(root_id, {
        "name": "Desconhecido",
        "category": "-",
        "department": "-",
        "ref": "",
        "bitPrincipal": False
    })
    node = {
        "id": root_id,
        "name": node_info["name"],
        "category": node_info["category"],
        "department": node_info["department"],
        "reference": node_info["ref"],
        "bitPrincipal": node_info["bitPrincipal"],
        "children": []
    }
   
    for child_id in parent_to_children.get(root_id, []):
        child_node = build_tree(child_id, parent_to_children, item_lookup, visited)
        if child_node is not None:
            node["children"].append(child_node)
    return node



@euro.route('/componentes/<int:pmv_id>')
@login_required
def componentes_pdf(pmv_id):
    pdf_dir = os.path.join(current_app.root_path, 'pdfs', 'componentes')
    filename = f'pmvid-{pmv_id}.pdf'
    full_path = os.path.join(pdf_dir, filename)

    if not os.path.isfile(full_path):
        current_app.logger.warning(f'PDF não encontrado: {full_path}')
        return abort(404)

    return send_file(
        full_path,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=filename
    )




@euro.route('/imagensprodutos/<filename>')
@login_required
@requer_acesso_catalogo_produtos
def imagensprodutos(filename):
    filename = _normalizar_nome_arquivo_imagem(filename)
    if not filename:
        abort(404)

    return send_from_directory(_pasta_imagens_produtos(), filename)




@euro.route('/item/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def item_detail(item_id):    
    resultado = (
        db.session.query(
            Produto,
            CategoriasProdutos.NomeCategoria.label('NomeCategoria'),
            Familia.NomeFamilia.label('NomeFamilia'),
            Departamento.NomeDepartamento.label('NomeDepartamento'),
            Produto.Descricao.label('Descricao')      
        )
        .outerjoin(CategoriasProdutos, Produto.IDCategoriaProduto == CategoriasProdutos.IDCategoria)
        .outerjoin(Familia,Produto.FamiliaID  == Familia.FamiliaID)
        .outerjoin(Departamento,  Produto.IDDepartamento == Departamento.IDDepartamento)
        .filter(Produto.IDItem == item_id)
        .first()
    )
    if not resultado:
        return "Item não encontrado", 404


    item = resultado[0]
    item.NomeCategoria = resultado[1] or '-'
    item.NomeFamilia = resultado[2] or '-'
    item.NomeDepartamento = resultado[3] or '-'
    item.Descricao = resultado[4] or ''      


    item.StatusComponente = 'Desconhecido'
    item.AtivoAssociado = '---'
    item.NomeAtivo = '---'
    item.Quantidade = 0
    item.unidade = 'UN'


    ativo_obj = db.session.query(Ativo).filter_by(IDItem=item_id).first()
    ativo_badge = None
    if ativo_obj:
        ativo_badge = {
            'IDAtivo': ativo_obj.IDAtivo,
            'url': url_for('euro.ativos_detalhes', ativo_id=ativo_obj.IDAtivo)
        }


    caracteristicas = db.session.query(Caracteristica).filter_by(IDItem=item_id).all()
    categories = db.session.query(CategoriasProdutos).filter_by(BitAtivo=1).all()
    departments = db.session.query(Departamento).filter_by(BitAtivo=1).all()
    families = db.session.query(Familia).filter_by(BitAtivo=1).all()
    pmvs = db.session.query(Pmv).filter_by(BitAtivo=1).all()

    im_objs = (
        db.session.query(ImagensProdutos)
                  .filter_by(IDItem=item_id)
                  .order_by(ImagensProdutos.Ordem)
                  .all()
    )
    images = []
    used_ordens = set()
    for img in im_objs:
        filename = _normalizar_nome_arquivo_imagem(
            img.NomeArquivo or img.CaminhoArquivo
        )
        if not filename:
            continue
        url = url_for('euro.imagem_produto', filename=filename)
        images.append({
            'url': url,
            'NomeArquivo': filename,
            'CaminhoArquivo': img.CaminhoArquivo,
            'Ordem': img.Ordem
        })
        used_ordens.add(img.Ordem)
    next_position = next((p for p in range(1, 5) if p not in used_ordens), None)

    pmv_id = request.args.get('pmv_id', type=int)
    if pmv_id is None:
        grupo_rel = db.session.query(GruposCompativeis).filter_by(IDItem=item_id).first()
        pmv_id = grupo_rel.PmvID if grupo_rel else item.PmvID

    vm = (
        db.session.query(VersaoFirmewire)
                  .filter_by(IDItem=item_id, PmvID=pmv_id)
                  .order_by(VersaoFirmewire.FirmewireID.desc())
                  .first()
    )
    firmware_selo = vm.Firmware if vm else None

    categories_map  = {c.IDCategoria : c.NomeCategoria  for c in categories}
    departments_map = {d.IDDepartamento: d.NomeDepartamento for d in departments}

    if pmv_id:
        main_item = (
            db.session.query(Produto)
                      .filter(Produto.PmvID == pmv_id, Produto.BitPMV == True)
                      .first()
        ) or item

        diagram_rels = (
            db.session.query(DiagramaProduto)
                      .filter(
                          DiagramaProduto.IDItemMaster == main_item.IDItem,
                          DiagramaProduto.PmvID       == pmv_id
                      )
                      .all()
        )
        parent_to_children = {}
        ids_set = {main_item.IDItem}
        for rel in diagram_rels:
            parent_to_children.setdefault(rel.IDItemA, []).append(rel.IDItemB)
            ids_set.update([rel.IDItemA, rel.IDItemB])

        cable_ids = set()
        for rel in diagram_rels:
            if rel.IDCaboLigacao:
                cable_ids.add(rel.IDCaboLigacao)
            if rel.IDCaboLigacao2:
                cable_ids.add(rel.IDCaboLigacao2)

        group_products = (
            db.session.query(Produto)
                      .filter(Produto.IDItem.in_(ids_set.union(cable_ids)))
                      .all()
        )

        item_lookup = {
            prod.IDItem: {
                "name":prod.NomeProduto,
                "category":categories_map.get(prod.IDCategoriaProduto, "-"),
                "department": departments_map.get(prod.IDDepartamento, "-"),
                "ref":prod.ReferenciaExterna or "",
                "bitPrincipal": prod.IDItem == main_item.IDItem
            }
            for prod in group_products
        }

        diagram_data = build_tree(main_item.IDItem, parent_to_children, item_lookup)

        cable_map = {}
        for rel in diagram_rels:
            cabos_ids = [rel.IDCaboLigacao, rel.IDCaboLigacao2]
            cabos = []
            for c in cabos_ids:
                if c and c in item_lookup:
                    cabos.append({"id": c, "name": item_lookup[c]["name"]})
            if not cabos:
                cabos = [{"id": None, "name": "Ligação Direta"}]
            cable_map[(rel.IDItemA, rel.IDItemB)] = cabos

        def attach_cables(node):
            for child in node.get("children", []):
                key = (node["id"], child["id"])
                child["cabos"] = cable_map.get(key, [{"id": None, "name": "Ligação Direta"}])
                attach_cables(child)
        attach_cables(diagram_data)

    else:
        main_item = item
        diagram_data = {
            "id": item.IDItem,
            "name":item.NomeProduto,
            "category": item.NomeCategoria,
            "department":item.NomeDepartamento,
            "reference":item.ReferenciaExterna or "",
            "bitPrincipal":True,
            "children": []
        }

    pdf_url = url_for('euro.componentes_pdf', pmv_id=pmv_id) if pmv_id else None

    filhos = db.session.query(ProdutoComposicao.IDItem).filter_by(IDProdutoPai=item_id).all()
    child_ids = [f.IDItem for f in filhos]

    components = []
    if child_ids:
        rows = (
            db.session.query(
                PedidoItens.IDItem.label('IDItem'),
                Produto.NomeProduto.label('NomeProduto'),
                PedidoItens.Quantidade,
                PedidoItens.NumeroLote,
                PedidoItens.NumerodeSerie,
                PedidoItens.IDTipoEstoque,
                PedidoItens.CodPonto
            )
            .join(Pedidos, PedidoItens.IDPedido == Pedidos.IDPedido)
            .join(Produto, PedidoItens.IDItem == Produto.IDItem)
            .filter(
                Pedidos.IDStatusPedido == 9,
                PedidoItens.IDItem.in_(child_ids)
            )
            .all()
        )
        components = [
            {
                'IDItem':r.IDItem,
                'NomeProduto':r.NomeProduto,
                'Quantidade':r.Quantidade,
                'NumeroLote':r.NumeroLote,
                'NumerodeSerie':r.NumerodeSerie,
                'IDTipoEstoque':r.IDTipoEstoque,
                'CodPonto':r.CodPonto
            }
            for r in rows
        ]

    tipo_est_map = {t.IDTipoEstoque: t.NomeEstoque for t in db.session.query(TipoEstoque).all()}

    component_item_ids = [c['IDItem'] for c in components]
    im_rows = (
        db.session.query(ImagensProdutos.IDItem, ImagensProdutos.NomeArquivo)
                  .filter(
                      ImagensProdutos.IDItem.in_(component_item_ids),
                      ImagensProdutos.Ordem == 1
                  )
                  .all()
    )
    image_urls = {
        iid: url_for(
            'euro.imagem_produto',
            filename=_normalizar_nome_arquivo_imagem(fn),
        )
        for iid, fn in im_rows
        if _normalizar_nome_arquivo_imagem(fn)
    }
    for iid in component_item_ids:
        image_urls.setdefault(iid, None)

    return render_template(
        'sp/item_detail.html',
        item = item,
        main_item = main_item,
        diagram_data = diagram_data,
        categories= categories,
        departments= departments,
        families= families,
        pmvs = pmvs,
        caracteristicas = caracteristicas,
        images = images,
        next_position = next_position,
        firmware_selo = firmware_selo,
        pmv_id = pmv_id,
        pdf_url = pdf_url,
        ativo_badge = ativo_badge,
        components = components,
        tipo_est_map = tipo_est_map,
        image_urls = image_urls
    )






@euro.route('/get_estoque/<int:item_id>')
@login_required
def get_estoque(item_id):

    estoque_container = _sp_execute(
         text("SELECT TOP (1000) IDEstoque, CodPonto, Saldo, IDItem FROM EstoqueContainer WHERE IDItem = :item_id"),
         {'item_id': item_id}
    ).fetchall()

    estoque_euro = _sp_execute(
         text("SELECT TOP (1000) IDEstoque, EuroID, IDItem, CodPonto, Saldo FROM EstoqueEuro WHERE IDItem = :item_id"),
         {'item_id': item_id}
    ).fetchall()

    estoque_manut_externa = _sp_execute(
         text("SELECT TOP (1000) IDEstoque, CodPonto, Saldo, IDItem FROM EstoqueManutencaoExterna WHERE IDItem = :item_id"),
         {'item_id': item_id}
    ).fetchall()

    estoque_manut_interna = _sp_execute(
         text("SELECT TOP (1000) IDEstoque, CodPonto, Saldo, IDItem FROM EstoqueManutencaoInterna WHERE IDItem = :item_id"),
         {'item_id': item_id}
    ).fetchall()

    estoque_matriz = _sp_execute(
         text("SELECT TOP (1000) IDEstoque, IDItem, CodPonto, Saldo FROM EstoqueMatriz WHERE IDItem = :item_id"),
         {'item_id': item_id}
    ).fetchall()

    data = {
         'EstoqueContainer': [dict(row._mapping) for row in estoque_container],
         'EstoqueEuro': [dict(row._mapping) for row in estoque_euro],
         'EstoqueManutencaoExterna': [dict(row._mapping) for row in estoque_manut_externa],
         'EstoqueManutencaoInterna': [dict(row._mapping) for row in estoque_manut_interna],
         'EstoqueMatriz': [dict(row._mapping) for row in estoque_matriz]
    }
    return jsonify(data)




@euro.route('/item/<int:item_id>/atualizar_produto_info', methods=['POST'])
@login_required
@requer_acesso_catalogo_produtos
def atualizar_produto_info(item_id):
    item = db.session.query(Produto).filter_by(IDItem=item_id).first()
    if not item:
        flash('Item não encontrado.', 'error')
        return redirect(url_for('euro.item_detail', item_id=item_id))

    nome = str(request.form.get('nome_produto') or item.NomeProduto or '').strip()
    ref = str(
        request.form.get('referencia_externa')
        or item.ReferenciaExterna
        or ''
    ).strip()

    if not nome or not ref:
        flash('Nome e referência externa são obrigatórios.', 'error')
        return redirect(url_for('euro.item_detail', item_id=item_id))

    referencia_existente = (
        db.session.query(Produto.IDItem)
        .filter(
            func.lower(cast(Produto.ReferenciaExterna, String)) == ref.lower(),
            Produto.IDItem != item_id,
        )
        .first()
    )
    if referencia_existente:
        flash('A referência externa já pertence a outro produto.', 'error')
        return redirect(url_for('euro.item_detail', item_id=item_id))

    cat_id = request.form.get('category_id',    type=int)
    department_id= request.form.get('department_id',  type=int)
    family_id = request.form.get('family_id',      type=int)
    pmv_id = request.form.get('pmv_id',         type=int)

    item.NomeProduto = nome
    item.ReferenciaExterna = ref

    
    if cat_id is not None:
        item.IDCategoriaProduto = cat_id
    if department_id is not None:
        item.IDDepartamento = department_id
    if family_id is not None:
        item.FamiliaID = family_id
    if pmv_id is not None:
        item.PmvID = pmv_id

    try:
        db.session.commit()
        flash('Informações do produto atualizadas com sucesso.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Falha ao atualizar o produto %s.', item_id)
        flash('Não foi possível atualizar as informações do produto.', 'error')

    return redirect(url_for('euro.item_detail', item_id=item_id))



@euro.route('/ver_movimentacao/<int:item_id>')
@login_required
def ver_movimentacao(item_id):
    from sqlalchemy import text
    page = request.args.get('page', 1, type=int)
    per_page = 10  

    query_str = """
    SELECT DISTINCT
        M.IDMovimentacao,
        U.NomeUsuario,
        M.Quantidade,
        CASE
            WHEN M.TipoEstoqueOrigem = 'Estoquemidia' THEN
                 COALESCE(PEOrigem.Logradouro, SHOrigem.Logradouro)
                 + ' - ' + CAST(M.CodPontoOrigem AS VARCHAR(20))
            WHEN M.TipoEstoqueOrigem = 'EstoqueSp' THEN
                 COALESCE(PEOrigem.Logradouro, SHOrigem.Logradouro)
                 + ' - ' + CAST(M.CodPontoOrigem AS VARCHAR(20))
            WHEN M.TipoEstoqueOrigem = 'EstoqueManutencaoInterna' THEN 'EstoqueManutencaoInterna'
            WHEN M.TipoEstoqueOrigem = 'EstoqueManutencaoExterna' THEN 'EstoqueManutencaoExterna'
            WHEN M.TipoEstoqueOrigem = 'EstoqueContainer' THEN 'EstoqueContainer'
            ELSE 'EstoqueMatriz'
        END AS ProprietarioOrigem,
        M.CodPontoOrigem,
        CASE
            WHEN M.TipoEstoqueDestino = 'Estoquemidia' THEN 'Estoquemidia'
            WHEN M.TipoEstoqueDestino = 'EstoqueEuro' THEN 'EstoqueEuro'
            WHEN M.TipoEstoqueDestino = 'EstoqueSp' THEN 'EstoqueSp'
            WHEN M.TipoEstoqueDestino = 'EstoqueManutencaoInterna' THEN 'EstoqueManutencaoInterna'
            WHEN M.TipoEstoqueDestino = 'EstoqueManutencaoExterna' THEN 'EstoqueManutencaoExterna'
            WHEN M.TipoEstoqueDestino = 'EstoqueContainer' THEN 'EstoqueContainer'
            WHEN M.TipoEstoqueDestino = 'EstoqueMatriz' THEN 'EstoqueMatriz'
            ELSE M.TipoEstoqueDestino
        END AS ProprietarioDestino,
        M.CodPontoDestino,
        M.DataMovimentacao,
        M.NomeMovimentacao,
        P.NomeProduto
    FROM Movimentacao M
    LEFT JOIN Produto P
           ON P.IDItem = M.IDItem
    LEFT JOIN Usuarios U
           ON U.IDUsuario = M.IDUsuario
    LEFT JOIN EstoqueEuro EEO
           ON EEO.EuroID   = M.IDProprietarioOrigem
          AND EEO.CodPonto = M.CodPontoOrigem
    LEFT JOIN PontosEuro PEOrigem
           ON PEOrigem.EuroID   = EEO.EuroID
          AND PEOrigem.CodPonto = EEO.CodPonto
    LEFT JOIN EstoqueSp ESO
           ON ESO.SpId = M.IDProprietarioOrigem
          AND ESO.CodPonto = M.CodPontoOrigem
    LEFT JOIN Sp SHOrigem
           ON SHOrigem.SpId = ESO.SpId
          AND SHOrigem.CodPonto = ESO.CodPonto
    LEFT JOIN EstoqueEuro EED
           ON EED.EuroID   = M.IDProprietarioDestino
          AND EED.CodPonto = M.CodPontoDestino
    LEFT JOIN PontosEuro PEDest
           ON PEDest.EuroID   = EED.EuroID
          AND PEDest.CodPonto = EED.CodPonto
    LEFT JOIN EstoqueSp ESD
           ON ESD.SpId = M.IDProprietarioDestino
          AND ESD.CodPonto = M.CodPontoDestino
    LEFT JOIN Sp SHDest
           ON SHDest.SpId = ESD.SpId
          AND SHDest.CodPonto = ESD.CodPonto
    WHERE M.IDItem = :item_id
    ORDER BY M.IDMovimentacao DESC
    """

    result = _sp_execute(text(query_str), {"item_id": item_id})
    movs_all = result.fetchall()

    total = len(movs_all)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    movs = movs_all[start:start+per_page]

   
    item = db.session.query(Produto).filter_by(IDItem=item_id).first()

    return render_template(
        'sp/ver_movimentacao.html',
        movs=movs,
        item=item,
        page=page,
        total_pages=total_pages
    )



@euro.route('/atualizar_saldo/<int:item_id>', methods=['POST'])
@login_required
def atualizar_saldo(item_id):
    tipo_ids = {
        'EstoqueMatriz': 1,
        'Reserva Técnico': 2,
        'EstoqueManutencaoExterna': 3,
        'Pedido Venda': 4,
        'EstoqueContainer': 6,
        'Estoquemidia': 7,
        'EstoqueSp': 8,
        'EstoqueManutencaoInterna': 9
    }

    matriz_reg = db.session.query(EstoqueMatriz).filter_by(IDItem=item_id).first()
    if not matriz_reg:
        matriz_reg = EstoqueMatriz(
            IDItem=item_id,
            CodPonto=0,
            Saldo=0
        )
        db.session.add(matriz_reg)
        db.session.commit()

    saldo_entrada = request.form.get('saldo_entrada')
    saldo_saida = request.form.get('saldo_saida')
    atualizado = False


    if saldo_entrada:
        try:
            entrada = int(saldo_entrada)
        except ValueError:
            return "Valor inválido para entrada", 400

        if entrada > 0:
            matriz_reg.Saldo += entrada

            mov = Movimentacao(
                IDUsuario = session.get('user_id'),
                IDItem = item_id,
                Quantidade= entrada,
                IDProprietarioOrigem= None,
                CodPontoOrigem= None,

                IDProprietarioDestino= matriz_reg.IDEstoque,
                CodPontoDestino= 0,

              
                IDTipoEstoqueOrigem = None,
                IDTipoEstoqueDestino= tipo_ids['EstoqueMatriz'],

                NomeMovimentacao= "Entrada",
                DataMovimentacao = datetime.now()
            )
            db.session.add(mov)
            atualizado = True

   
    if saldo_saida:
        try:
            saida = int(saldo_saida)
        except ValueError:
            return "Valor inválido para saída", 400

        if saida > 0:
            if matriz_reg.Saldo < saida:
                return "Saldo insuficiente para remoção", 400
            matriz_reg.Saldo -= saida

            mov = Movimentacao(
                IDUsuario = session.get('user_id'),
                IDItem = item_id,
                Quantidade = saida,

                IDProprietarioOrigem = matriz_reg.IDEstoque,
                CodPontoOrigem = 0,

                IDProprietarioDestino = None,
                CodPontoDestino = None,

                IDTipoEstoqueOrigem = tipo_ids['EstoqueMatriz'],
                IDTipoEstoqueDestino= None,

                NomeMovimentacao = "Saida",
                DataMovimentacao = datetime.now()
            )
            db.session.add(mov)
            atualizado = True

    if atualizado:
        db.session.commit()

    return redirect(url_for('euro.item_detail', item_id=item_id))





@euro.route('/atualizar_caracteristicas/<int:item_id>', methods=['POST'])
@login_required
@requer_acesso_catalogo_produtos
def atualizar_caracteristicas(item_id):
    for key, value in request.form.items():
        if key.startswith("valor_"):
            carac_id = int(key.replace("valor_", ""))
            caracteristica = db.session.query(Caracteristica).filter_by(IDCaracteristica=carac_id).first()
            if caracteristica:
                caracteristica.Valor = value
    db.session.commit()
    return redirect(url_for('euro.item_detail', item_id=item_id))



@euro.route('/nova_caracteristica/<int:item_id>', methods=['POST'])
@login_required
@requer_acesso_catalogo_produtos
def nova_caracteristica(item_id):
    nova_caracteristica = request.form.get('nova_caracteristica')
    novo_valor = request.form.get('novo_valor')
    
    item = db.session.query(Produto).filter_by(IDItem=item_id).first()
    if not item:
        return "Item não encontrado", 404

    nova = Caracteristica(
        IDItem=item.IDItem,
        FamiliaID=item.FamiliaID,
        Caracteristica=nova_caracteristica,
        Valor=novo_valor,
        BitAtivo=True,
        IDCategoria=None
    )
    db.session.add(nova)
    db.session.commit()
    return redirect(url_for('euro.item_detail', item_id=item_id))




@euro.route('/Admin/Sp/V1/remover_caracteristica/<int:item_id>/<int:carac_id>', methods=['GET', 'POST'])
@login_required
@requer_acesso_catalogo_produtos
def remover_caracteristica(item_id, carac_id):
   
    carac = db.session.query(Caracteristica).filter_by(IDCaracteristica=carac_id, IDItem=item_id).first()
    if carac:
        db.session.delete(carac)
        db.session.commit()
        flash("Característica removida com sucesso.", "success")
    else:
        flash("Característica não encontrada.", "error")

    return redirect(url_for('euro.item_detail', item_id=item_id))



@euro.route('/marcar_filtro/<int:item_id>/<int:carac_id>', methods=['GET', 'POST'])
@login_required
@requer_acesso_catalogo_produtos
def marcar_filtro(item_id, carac_id):

    carac = db.session.query(Caracteristica).filter_by(IDCaracteristica=carac_id, IDItem=item_id).first()
    if carac:
        carac.BitFiltro = True  
        db.session.commit()
        flash("Característica marcada como filtro.", "success")
    else:
        flash("Característica não encontrada.", "error")

    return redirect(url_for('euro.item_detail', item_id=item_id))




@euro.route('/familias')
@login_required
def listar_familias():
    familias = db.session.query(
        Familia.FamiliaID,
        Familia.NomeFamilia,
        Familia.BitAtivo
    ).filter(Familia.BitAtivo == 1).order_by(Familia.NomeFamilia).all()

    return render_template('sp/familias.html', familias=familias)



@euro.route('/familia/<int:familia_id>')
@login_required
def familia_por_id(familia_id):
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = db.session.query(
        Produto.IDItem,
        Produto.ReferenciaExterna,
        Produto.NomeProduto,
        CategoriasProdutos.NomeCategoria,
        Produto.BitAtivo,
        Saldo.Quantidade.label('Quantidade'),
        Produto.FamiliaID,
        Familia.NomeFamilia,
        Familia.DescricaoFamilia
    ).outerjoin(
        CategoriasProdutos, Produto.IDCategoriaProduto == CategoriasProdutos.IDCategoria
    ).outerjoin(
        Ativo, Produto.IDAtivo == Ativo.IDAtivo
    ).outerjoin(
        Saldo, Saldo.IDItem == Produto.IDItem
    ).join(
        Familia, Produto.FamiliaID == Familia.FamiliaID
    ).filter(
        Produto.FamiliaID == familia_id,
        Produto.BitPMV == 1
    ).order_by(Produto.IDItem)

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

 
    return render_template(
        'sp/familia_pmv2000.html',
        items=items,
        page=page,
        total_pages=total_pages,
        familia_id=familia_id  
    )





@euro.route('/familia/<int:familia_id>')
@login_required
def familia(familia_id):
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = db.session.query(
        Produto.IDItem,
        Produto.ReferenciaExterna,
        Produto.NomeProduto,
        CategoriasProdutos.NomeCategoria,
        Produto.BitAtivo,
        Saldo.Quantidade.label('Quantidade'),
        Produto.FamiliaID,
        Familia.NomeFamilia,
        Familia.DescricaoFamilia
    ).outerjoin(
        CategoriasProdutos, Produto.IDCategoriaProduto == CategoriasProdutos.IDCategoria
    ).outerjoin(
        Ativo, Produto.IDAtivo == Ativo.IDAtivo
    ).outerjoin(
        Saldo, Saldo.IDItem == Produto.IDItem
    ).join(
        Familia, Produto.FamiliaID == Familia.FamiliaID
    ).filter(
        Produto.FamiliaID == familia_id,
        Produto.BitPMV == 1  
    ).order_by(Produto.IDItem)

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    
    return render_template(
        'sp/familia_pmv2000.html',
        items=items,
        page=page,
        total_pages=total_pages,
        familia_id=familia_id
    )





@euro.route('/produto/<int:produto_id>/compativeis')
@login_required
def produto_compativeis(produto_id):
    page     = request.args.get('page', 1, type=int)
    per_page = 10

    search = request.args.get('search', '').strip()
    fcar= request.args.get('fcar',   '').strip()
    fval= request.args.get('fval',   '').strip()

    prod = db.session.query(Produto).filter_by(IDItem=produto_id).first()
    if not prod:
        return "Produto não encontrado", 404

    pmv_id = prod.PmvID

    query = (
        db.session.query(
            Produto.IDItem,
            Produto.ReferenciaExterna,
            Produto.NomeProduto,
            CategoriasProdutos.NomeCategoria,
            Departamento.NomeDepartamento,
            Familia.NomeFamilia,
            Produto.BitAtivo
        )
        .join(GruposCompativeis, GruposCompativeis.IDItem == Produto.IDItem)
        .outerjoin(CategoriasProdutos, Produto.IDCategoriaProduto == CategoriasProdutos.IDCategoria)
        .outerjoin(Departamento, Produto.IDDepartamento == Departamento.IDDepartamento)
        .outerjoin(Familia,Produto.FamiliaID == Familia.FamiliaID)
        .filter(
            GruposCompativeis.PmvID == pmv_id,
            Produto.IDItem != produto_id
        )
        .order_by(Produto.IDItem)
    )

    if search:
        query = query.filter(
            or_(
                cast(Produto.IDItem, String).ilike(f'%{search}%'),
                Produto.ReferenciaExterna.ilike(f'%{search}%'),
                Produto.NomeProduto.ilike(f'%{search}%')
            )
        )

    if fcar and fval:
        query = (
            query
            .join(Caracteristica, Caracteristica.IDItem == Produto.IDItem)
            .filter(
                Caracteristica.Caracteristica == fcar,
                Caracteristica.Valor == fval,
                Caracteristica.BitFiltro == True
            )
        )


    filtro_data = (
        db.session.query(
            Caracteristica.Caracteristica,
            Caracteristica.Valor
        )
        .join(Produto,Caracteristica.IDItem == Produto.IDItem)
        .join(GruposCompativeis, Caracteristica.IDItem == GruposCompativeis.IDItem)
        .filter(
            Caracteristica.BitFiltro == True,
            GruposCompativeis.PmvID == pmv_id,
            Produto.IDItem != produto_id
        )
        .distinct()
        .all()
    )
    filter_groups = {}
    for car, val in filtro_data:
        filter_groups.setdefault(car, set()).add(val)
    for car in filter_groups:
        filter_groups[car] = sorted(filter_groups[car])

 
    total= query.count()
    produtos= query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page


    item_ids = [p.IDItem for p in produtos]
    imagens = (
        db.session
        .query(ImagensProdutos.IDItem, ImagensProdutos.NomeArquivo)
        .filter(
            ImagensProdutos.IDItem.in_(item_ids),
            ImagensProdutos.Ordem == 1
        )
        .all()
    )
    image_urls = {
        iid: url_for('euro.imagensprodutos', filename=fn)
        for iid, fn in imagens
    }
    for iid in item_ids:
        image_urls.setdefault(iid, None)


    return render_template(
        'sp/produto_compativeis.html',
        prod = prod,
        produtos = produtos,
        page = page,
        total_pages  = total_pages,
        search = search,
        fcar = fcar,
        fval = fval,
        filter_groups = filter_groups,
        pmv_id = pmv_id,
        image_urls = image_urls
    )





@euro.route('/familia_4600')
@login_required
def familia_4600():
    return render_template('sp/familia_4600.html')



@euro.route('/adicionar', methods=['POST'])
@login_required
@requer_acesso_catalogo_produtos
def adicionar():
    item_id = request.form.get('item_id')
    quantity = request.form.get('quantity')
    if not item_id or not quantity:
        return "Dados inválidos", 400

    item_id = int(item_id)
    quantity = int(quantity)

    item = get_item(item_id)
    if not item:
        return "Item não encontrado", 404

    saldo = db.session.query(Saldo).filter_by(IDItem=item_id).first()
    if not saldo or saldo.Quantidade < quantity:
        return "Quantidade insuficiente em estoque", 400

    
    cart = session.get('cart', {})
    cart[str(item_id)] = cart.get(str(item_id), 0) + quantity
    session['cart'] = cart

    
    return redirect(url_for('euro.lista_produtos'))




@euro.route('/pecas_componentes')
@login_required
def pecas_componentes():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    search = request.args.get('search', '').strip()

    query = db.session.query(
        Produto.IDItem,
        Produto.ReferenciaExterna,
        Produto.NomeProduto,
        CategoriasProdutos.NomeCategoria,
        Produto.BitAtivo,
        Saldo.Quantidade.label('Quantidade')
    ).outerjoin(
        CategoriasProdutos, Produto.IDCategoriaProduto == CategoriasProdutos.IDCategoria
    ).outerjoin(
        Ativo, Produto.IDAtivo == Ativo.IDAtivo
    ).outerjoin(
        Saldo, Saldo.IDItem == Produto.IDItem
    )

    if search:
        query = query.filter(
            or_(
                cast(Produto.IDItem, String).ilike(f'%{search}%'),
                Produto.ReferenciaExterna.ilike(f'%{search}%'),
                Produto.NomeProduto.ilike(f'%{search}%')
            )
        )

    query = query.order_by(Produto.IDItem)
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    return render_template('sp/pecas_componentes.html', items=items, page=page, total_pages=total_pages, search=search)



@euro.route('/estoque_manut_externa_detail/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def estoque_manut_externa_detail(item_id):
    product = db.session.query(Produto).filter_by(IDItem=item_id).first()

    detail_query = text("""
       SELECT A.IDEstoque, A.IDItem, B.NomeProduto, A.Saldo
       FROM EstoqueManutencaoExterna AS A
       INNER JOIN Produto AS B
         ON B.IDItem = A.IDItem
       WHERE A.IDItem = :item_id
       ORDER BY A.IDEstoque
    """)
    details = _sp_execute(detail_query, {"item_id": item_id}).fetchall()

   
    total_externa = sum(row.Saldo for row in details) if details else 0

    return render_template(
        'sp/estoque_manut_externa_detail.html',
        product=product,
        total_externa=total_externa,
        manut_externa=details
    )



@euro.route('/estoque_manut_interna_detail/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def estoque_manut_interna_detail(item_id):
    product = db.session.query(Produto).filter_by(IDItem=item_id).first()

    detail_query = text("""
       SELECT 
           A.IDEstoque,
           A.IDItem,
           B.NomeProduto,
           A.Saldo
       FROM EstoqueManutencaoInterna AS A
       INNER JOIN Produto AS B 
           ON B.IDItem = A.IDItem
       WHERE A.IDItem = :item_id
       ORDER BY A.IDEstoque
    """)
    details = _sp_execute(detail_query, {"item_id": item_id}).fetchall()

    total_interna = sum(row.Saldo for row in details) if details else 0

    return render_template(
        'sp/estoque_manut_interna_detail.html',
        product=product,
        total_interna=total_interna,
        manut_interna=details
    )





@euro.route('/estoque_container_detail/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def estoque_container_detail(item_id):
    product = db.session.query(Produto).filter_by(IDItem=item_id).first()

    detail_query = text("""
       SELECT 
           A.IDEstoque,
           A.IDItem,
           B.NomeProduto,
           A.Saldo
       FROM EstoqueContainer AS A
       INNER JOIN Produto AS B 
         ON B.IDItem = A.IDItem
       WHERE A.IDItem = :item_id
       ORDER BY A.IDEstoque
    """)
    details = _sp_execute(detail_query, {"item_id": item_id}).fetchall()

    total_container = sum(row.Saldo for row in details) if details else 0

    return render_template(
        'sp/estoque_container_detail.html',
        product=product,
        total_container=total_container,
        container=details
    )





@euro.route('/ver_estoques/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def ver_estoques(item_id):
    product = db.session.query(
        Produto.IDItem,
        Produto.ReferenciaExterna,
        Produto.NomeProduto,
        Produto.BitAtivo
    ).filter_by(IDItem=item_id).first()

    if product is None:
        abort(404)

    imagem = (
        db.session.query(ImagensProdutos)
        .filter(ImagensProdutos.IDItem == item_id)
        .order_by(ImagensProdutos.Ordem)
        .first()
    )
    image_url = None
    if imagem is not None:
        arquivo_imagem = _normalizar_nome_arquivo_imagem(
            imagem.NomeArquivo or imagem.CaminhoArquivo
        )
        if arquivo_imagem:
            image_url = url_for(
                'euro.imagem_produto',
                filename=arquivo_imagem,
            )

   
    estoque_matriz = db.session.query(EstoqueMatriz).filter_by(IDItem=item_id).all()
    estoque_midia= db.session.query(EstoqueEuro).filter_by(IDItem=item_id).all()
    estoque_euromatriz= db.session.query(EstoqueEuroMatriz).filter_by(IDItem=item_id).all()
    estoque_sp= db.session.query(EstoqueSp).filter_by(IDItem=item_id).all()
    estoque_externa = db.session.query(EstoqueManutencaoExterna).filter_by(IDItem=item_id).all()
    estoque_interna= db.session.query(EstoqueManutencaoInterna).filter_by(IDItem=item_id).all()
    estoque_container= db.session.query(EstoqueContainer).filter_by(IDItem=item_id).all()

  
    total_matriz= sum(e.Saldo for e in estoque_matriz) if estoque_matriz else 0
    total_midia  = sum(e.Saldo for e in estoque_midia) if estoque_midia else 0
    total_euromatriz = sum(e.Quantidade for e in estoque_euromatriz) if estoque_euromatriz else 0
    total_sp = sum(e.Saldo for e in estoque_sp) if estoque_sp else 0
    total_externa= sum(e.Saldo for e in estoque_externa) if estoque_externa else 0
    total_interna  = sum(e.Saldo for e in estoque_interna) if estoque_interna else 0
    total_container= sum(e.Saldo for e in estoque_container) if estoque_container else 0

    return render_template(
        'sp/ver_estoques.html',
        item_id=item_id,
        product=product,
        image_url=image_url,
        estoque_matriz=estoque_matriz,
        total_matriz=total_matriz,
        estoque_midia=estoque_midia,
        total_midia=total_midia,
        estoque_euromatriz=estoque_euromatriz,
        total_euromatriz=total_euromatriz,
        estoque_sp=estoque_sp,
        total_sp=total_sp,
        estoque_externa=estoque_externa,
        total_externa=total_externa,
        estoque_interna=estoque_interna,
        total_interna=total_interna,
        estoque_container=estoque_container,
        total_container=total_container
    )






@euro.route('/movimentar_estoque/<int:item_id>', methods=['GET', 'POST'])
@login_required
@requer_acesso_catalogo_produtos
def movimentar_estoque(item_id):
    tipo_ids = {
        'EstoqueMatriz': 1,
        'Reserva Técnico': 2,
        'EstoqueManutencaoExterna': 3,
        'Pedido Venda': 4,
        'EstoqueContainer':6,
        'Estoquemidia':7,
        'EstoqueEuroMatriz':10,
        'EstoqueSp':8,
        'EstoqueManutencaoInterna':9,
    }

    item = db.session.query(Produto).filter_by(IDItem=item_id).first()
    if not item:
        return "Item não encontrado", 404

  
    matriz = db.session.query(EstoqueMatriz).filter_by(IDItem=item_id).all()
    euro_reg = db.session.query(EstoqueEuro).filter_by(IDItem=item_id).all()
    euromat_reg = db.session.query(EstoqueEuroMatriz).filter_by(IDItem=item_id).all()
    sp_reg = db.session.query(EstoqueSp).filter_by(IDItem=item_id).all()
    manut_externa   = db.session.query(EstoqueManutencaoExterna).filter_by(IDItem=item_id).all()
    estoque_container = db.session.query(EstoqueContainer).filter_by(IDItem=item_id).all()
    manut_interna  = db.session.query(EstoqueManutencaoInterna).filter_by(IDItem=item_id).all()

    for e in matriz: e.NomeEstoque = 'EstoqueMatriz'
    for e in euro_reg: e.NomeEstoque = 'Estoquemidia'
    for e in euromat_reg:e.NomeEstoque = 'EstoqueEuroMatriz'
    for e in sp_reg: e.NomeEstoque = 'EstoqueSp'
    for e in manut_externa:e.NomeEstoque = 'EstoqueManutencaoExterna'
    for e in estoque_container: e.NomeEstoque = 'EstoqueContainer'
    for e in manut_interna: e.NomeEstoque = 'EstoqueManutencaoInterna'

    existentes = (
        matriz + euro_reg + euromat_reg + sp_reg +
        manut_externa + estoque_container + manut_interna
    )

    def to_dict(e):
        nome = e.NomeEstoque
        prop = (
            e.EuroID if nome in ('Estoquemidia', 'EstoqueEuroMatriz') else
            e.SpId if nome == 'EstoqueSp' else 0
        )
        return {
            'IDEstoque': e.IDEstoque,
            'IDItem': e.IDItem,
            'CodPonto':getattr(e, 'CodPonto', '') or '',
            'Saldo': e.Saldo or 0,
            'NomeEstoque':  nome,
            'IDProprietario': prop,
            'Logradouro': getattr(e, 'Logradouro', ''),
            'IDTipoEstoque':getattr(e, 'IDTipoEstoque', tipo_ids.get(nome)),
        }

    registros = [to_dict(e) for e in existentes]

    origem_groups: dict[str, list] = {}
    destino_groups: dict[str, list] = {}
    for r in registros:
        if r['Saldo'] > 0:
            origem_groups.setdefault(r['NomeEstoque'], []).append(r)
        if r['NomeEstoque'] != 'Estoquemidia':
            destino_groups.setdefault(r['NomeEstoque'], []).append(r)

    
    pontos_euro = _sp_execute(text(
        'SELECT EuroID, NomeEstoque, CodPonto, Logradouro FROM PontosEuro'
    )).fetchall()
    euro_opts = [{
        'IDEstoque':None,
        'IDItem':item_id,
        'CodPonto':row.CodPonto,
        'Saldo': 0,
        'NomeEstoque': row.NomeEstoque,
        'IDProprietario': row.EuroID,
        'Logradouro': row.Logradouro,
    } for row in pontos_euro]
    destino_groups['Estoquemidia']  = list({r['CodPonto']: r for r in euro_opts}.values())
    destino_groups['EstoqueEuroMatriz'] = list({r['CodPonto']: r for r in euro_opts}.values())

    for nome in (
        'EstoqueMatriz','EstoqueSp',
        'EstoqueManutencaoExterna','EstoqueContainer',
        'EstoqueManutencaoInterna',
    ):
        destino_groups.setdefault(nome, [{
            'IDEstoque':None,
            'IDItem':item_id,
            'CodPonto':'',
            'Saldo': 0,
            'NomeEstoque': nome,
            'IDProprietario':0,
            'Logradouro': '',
        }])


    lotes_data: dict[int, list] = {}
    if item.ControlaLote:
        sql = 'SELECT IDEstoque, NumeroLote, Quantidade FROM EstoqueLotes WHERE IDItem = :item_id'
        for row in _sp_execute(text(sql), {'item_id': item_id}):
            lotes_data.setdefault(row.IDEstoque, []).append({'NumeroLote': row.NumeroLote, 'Quantidade': row.Quantidade})

    series_data: dict[int, dict] = {}
    if item.ControlaNumerodeSerie:
        sql = 'SELECT IDEstoque, NumeroLote, NumeroSerie FROM EstoqueSerie WHERE IDItem = :item_id AND DataSaida IS NULL'
        for row in _sp_execute(text(sql), {'item_id': item_id}):
            series_data.setdefault(row.IDEstoque, {}).setdefault(row.NumeroLote, []).append(row.NumeroSerie)


    if request.method == 'POST':
        esc_origem = request.form.get('estoque_origem', '')
        prop_origem = request.form.get('proprietario_origem', '')
        esc_dest = request.form.get('estoque_destino', '')
        prop_dest= request.form.get('proprietario_destino', '')
        try:
            qtd = int(request.form.get('quantidade', '0') or 0)
        except ValueError:
            qtd = 0

        if not esc_origem or not prop_origem or not esc_dest or not prop_dest:
            flash('Selecione corretamente os estoques de origem e de destino.', 'error')
            return redirect(url_for('euro.movimentar_estoque', item_id=item_id))

        if qtd <= 0:
            flash('A quantidade a transferir deve ser maior que zero.', 'error')
            return redirect(url_for('euro.movimentar_estoque', item_id=item_id))

        lote_num = request.form.get('numero_lote_escolhido', '').strip()
        if item.ControlaLote and not lote_num:
            flash('Informe obrigatoriamente o número do lote.', 'error')
            return redirect(url_for('euro.movimentar_estoque', item_id=item_id))

       
        def parse_prop(val: str):
            parts = val.split('|')
            if len(parts) >= 3:
                id_est = int(parts[0]) if parts[0].isdigit() else None
                prop = parts[1].strip()
                cod  = parts[2].strip() or None
                if cod and cod.lower() in {'none', 'null'}:
                    cod = None
                return id_est, prop, cod
            return None, None, None

        def resolve(nome: str) -> str:
            return {
                'estoquemidia':'Estoquemidia',
                'estoqueeuro':'Estoquemidia',
                'estoqueeuromatriz': 'EstoqueEuroMatriz',
                'estoquesp': 'EstoqueSp',
                'estoquemanutencaointerna': 'EstoqueManutencaoInterna',
                'estoquemanutencaoexterna': 'EstoqueManutencaoExterna',
                'estoquecontainer':'EstoqueContainer',
            }.get(nome.lower().strip(),'EstoqueMatriz')

        def get_reg(tipo: str, item_id: int, prop_val: str, lock=False):
            model = {
                'EstoqueMatriz':EstoqueMatriz,
                'EstoqueSp':EstoqueSp,
                'Estoquemidia': EstoqueEuro,
                'EstoqueEuroMatriz':EstoqueEuroMatriz,
                'EstoqueManutencaoInterna': EstoqueManutencaoInterna,
                'EstoqueManutencaoExterna': EstoqueManutencaoExterna,
                'EstoqueContainer': EstoqueContainer,
            }[tipo]

            q = db.session.query(model).filter(
                model.IDItem == item_id,
                model.IDTipoEstoque == tipo_ids[tipo],
            )
            id_est, prop_id, cod = parse_prop(prop_val)
            if id_est is not None:
                q = q.filter(model.IDEstoque == id_est)
            if tipo in ('Estoquemidia', 'EstoqueEuroMatriz'):
                q = q.filter(model.EuroID == prop_id)
                if cod:
                    q = q.filter(model.CodPonto == cod)
            elif tipo == 'EstoqueSp':
                q = q.filter(model.SpId == prop_id)
            if lock:
                q = q.with_for_update()
            return q.first()

        tipo_o = resolve(esc_origem)
        tipo_d = resolve(esc_dest)

        def chave_localizacao(tipo: str, prop_val: str):
            """Identifica o local físico, mesmo quando o destino ainda não tem IDEstoque."""
            id_est, proprietario, cod_ponto = parse_prop(prop_val)
            proprietario = str(proprietario or '').strip()
            cod_ponto = str(cod_ponto or '').strip().casefold()

            if (proprietario and proprietario != '0') or cod_ponto:
                return tipo, 'proprietario', proprietario, cod_ponto
            if id_est is not None:
                return tipo, 'estoque', id_est
            return tipo, 'generico', proprietario, cod_ponto

        if chave_localizacao(tipo_o, prop_origem) == chave_localizacao(tipo_d, prop_dest):
            flash('O estoque de destino deve ser diferente do estoque de origem.', 'error')
            return redirect(url_for('euro.movimentar_estoque', item_id=item_id))

        try:
            with db.session.begin_nested():
               
                origem = get_reg(tipo_o, item_id, prop_origem, lock=True)
                if not origem or origem.Saldo < qtd:
                    raise ValueError('Saldo insuficiente ou origem não encontrada')

                destino = get_reg(tipo_d, item_id, prop_dest, lock=True)
                if (
                    destino is not None
                    and tipo_o == tipo_d
                    and origem.IDEstoque == destino.IDEstoque
                ):
                    raise ValueError(
                        'O estoque de destino deve ser diferente do estoque de origem.'
                    )

                origem.Saldo -= qtd
                db.session.flush()

                if destino:
                    destino.Saldo += qtd
                else:
                    _, prop_id, cod = parse_prop(prop_dest)
                    kwargs = {'IDItem': item_id, 'Saldo': qtd, 'IDTipoEstoque': tipo_ids[tipo_d]}
                    if tipo_d in ('Estoquemidia', 'EstoqueEuroMatriz'):
                        kwargs.update(EuroID=prop_id, CodPonto=cod)
                    elif tipo_d == 'EstoqueSp':
                        kwargs.update(SpId=prop_id)
                    destino = globals()[tipo_d](**kwargs)
                    db.session.add(destino)
                    db.session.flush()

             
                if item.ControlaLote:
                    
                    lote_origem = (
                        db.session.query(EstoqueLotes)
                        .filter_by(
                            IDItem = item_id,
                            IDEstoque = origem.IDEstoque,
                            NumeroLote = lote_num
                        )
                        .with_for_update()
                        .first()
                    )
                    if not lote_origem or lote_origem.Quantidade < qtd:
                        raise ValueError(f"Lote '{lote_num}' inválido ou saldo insuficiente na origem")
                    lote_origem.Quantidade -= qtd

                    
                    lote_dest = (
                        db.session.query(EstoqueLotes)
                        .filter_by(
                            IDItem= item_id,
                            IDTipoEstoque = tipo_ids[tipo_d],
                            NumeroLote = lote_num
                        )
                        .with_for_update()
                        .first()
                    )
                    if lote_dest:
                      
                        lote_dest.Quantidade += qtd
                    else:
                       
                        lote_dest = EstoqueLotes(
                            IDItem = item_id,
                            IDEstoque = destino.IDEstoque,
                            NumeroLote = lote_num,
                            Quantidade = qtd,
                            DataEntrada = datetime.now(),
                            IDTipoEstoque = tipo_ids[tipo_d],
                            CodPonto= getattr(destino, "CodPonto", None),
                        )
                        db.session.add(lote_dest)

                    db.session.flush()

                if item.ControlaNumerodeSerie:
                    for s_val in request.form.getlist('numero_serie_escolhido[]'):
                        s_val = s_val.strip()
                        if not s_val:
                            continue
                        sreg = db.session.query(EstoqueSerie).filter_by(
                            IDItem=item_id,
                            IDEstoque=origem.IDEstoque,
                            NumeroLote=lote_num,
                            NumeroSerie=s_val,
                            DataSaida=None,
                        ).with_for_update().first()
                        if not sreg:
                            raise ValueError(f"Série '{s_val}' não disponível no lote '{lote_num}'")
                        sreg.IDEstoque = destino.IDEstoque
                        sreg.TipoEstoque = tipo_d
                        sreg.IDTipoEstoque = tipo_ids[tipo_d]
                        sreg.DataEntrada = datetime.now()
                        if hasattr(sreg, 'CodPonto'):
                            sreg.CodPonto = getattr(destino, 'CodPonto', None)
                    db.session.flush()

             
                op = Movimentacao(
                    IDUsuario = session.get('user_id'),
                    NomeMovimentacao = 'Transferência entre estoques',
                    IDItem = item_id,
                    Quantidade = qtd,
                    IDProprietarioOrigem  = parse_prop(prop_origem)[1] or origem.IDEstoque,
                    IDTipoEstoqueOrigem = tipo_ids[tipo_o],
                    CodPontoOrigem = parse_prop(prop_origem)[2] or None,
                    IDProprietarioDestino = parse_prop(prop_dest)[1] or destino.IDEstoque,
                    IDTipoEstoqueDestino = tipo_ids[tipo_d],
                    CodPontoDestino= parse_prop(prop_dest)[2] or None,
                    DataMovimentacao = datetime.now(),
                )
                db.session.add(op)

                sp_params = {
                "IDItem":  item_id,
                "NumeroLote": lote_num if item.ControlaLote else None,
                "NumeroSerie":None, 
                "IDOrigem":origem.IDEstoque,
                "TipoOrigem":tipo_o,
                "IDDestino":destino.IDEstoque,
                "TipoDestino": tipo_d,
                "Quantidade": qtd,
                "IDOperacao":  4,             
                "IDTipoEstoque": tipo_ids[tipo_o], 
                "IDUsuario": session['user_id']
            }


            with _sp_engine().begin() as conn:
                conn.execute(
                    text("""
                        EXEC dbo.sp_RegistrarMovimentacao
                            @IDItem  = :IDItem,
                            @NumeroLote = :NumeroLote,
                            @NumeroSeri= :NumeroSerie,
                            @IDOrigem = :IDOrigem,
                            @TipoOrigem = :TipoOrigem,
                            @IDDestino = :IDDestino,
                            @TipoDestino= :TipoDestino,
                            @Quantidade = :Quantidade,
                            @IDOperacao = :IDOperacao,
                            @IDTipoEstoque= :IDTipoEstoque,
                            @IDUsuario = :IDUsuario
                    """),
                    sp_params
                )

            db.session.commit()
            flash('Transferência realizada com sucesso.', 'success')
        except (ValueError, SQLAlchemyError) as e:
            db.session.rollback()
            flash(str(e), 'error')

        return redirect(url_for('euro.ver_estoques', item_id=item_id))

  
    return render_template(
        'sp/movimentar_estoque_transfer.html',
        item = item,
        origem_groups = origem_groups,
        destino_groups = destino_groups,
        lotes_data = lotes_data,
        series_data = series_data,
    )





@euro.route('/estoque_midia_detail/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def estoque_midia_detail(item_id):
    product = db.session.query(Produto).filter_by(IDItem=item_id).first()

    total_query = text("""
       SELECT COALESCE(SUM(E.Saldo), 0) AS TotalSaldo
       FROM EstoqueEuro E
       WHERE E.IDItem = :item_id
    """)
    total_result = _sp_execute(total_query, {"item_id": item_id}).fetchone()
    total_midia = total_result.TotalSaldo if total_result and total_result.TotalSaldo is not None else 0

    detail_query = text("""
       SELECT 
           E.IDEstoque,
           E.IDItem,
           C.NomeProduto,
           E.CodPonto,
           E.Saldo,
           B.Logradouro,
           B.Faces
       FROM EstoqueEuro E
       INNER JOIN PontosEuro B
           ON B.EuroID = E.EuroID
           AND B.CodPonto = E.CodPonto
       INNER JOIN Produto C
           ON C.IDItem = E.IDItem
       WHERE E.IDItem = :item_id
       ORDER BY E.CodPonto
    """)
    details = _sp_execute(detail_query, {"item_id": item_id}).fetchall()

    return render_template(
        'sp/estoque_midia_detail.html',
        product=product,
        total_midia=total_midia,
        details=details
    )




@euro.route('/estoque_sp_detail/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def estoque_sp_detail(item_id):
    product = db.session.query(Produto).filter_by(IDItem=item_id).first()

    detail_query = text("""
       SELECT 
           A.IDEstoque,
           A.IDItem,
           C.NomeProduto,
           A.CodPonto,
           A.Saldo,
           B.Logradouro,
           B.Faces
       FROM EstoqueSp AS A
       LEFT JOIN Sp AS B 
         ON B.CodPonto = A.CodPonto
       INNER JOIN Produto AS C 
         ON C.IDItem = A.IDItem
       WHERE A.IDItem = :item_id
       ORDER BY A.CodPonto
    """)
    details = _sp_execute(detail_query, {"item_id": item_id}).fetchall()

    
    total_sp = sum(row.Saldo for row in details) if details else 0

    return render_template(
        'sp/estoque_sp_detail.html',
        product=product,
        total_sp=total_sp,
        details=details
    )





@euro.route('/criar_componente', methods=['GET', 'POST'])
@login_required
@requer_acesso_catalogo_produtos
def criar_componente():
    if request.method == 'POST':
        nome_produto = str(request.form.get('nome_produto') or '').strip()
        referencia_externa = str(
            request.form.get('referencia_externa') or ''
        ).strip()
        id_departamento = request.form.get('id_departamento', type=int)
        familia_id = request.form.get('familia_id', type=int)
        id_categoria = request.form.get('id_categoria', type=int)

        if not all(
            (
                nome_produto,
                referencia_externa,
                id_departamento,
                familia_id,
                id_categoria,
            )
        ):
            flash('Preencha os campos obrigatórios do produto.', 'error')
            return redirect(url_for('euro.criar_componente'))

        existente = (
            db.session.query(Produto.IDItem)
            .filter(
                func.lower(cast(Produto.ReferenciaExterna, String))
                == referencia_externa.lower()
            )
            .first()
        )
        if existente:
            flash('Referência Externa já existe.', 'error')
            return redirect(url_for('euro.criar_componente'))

        try:
            entrada = max(int(request.form.get('saldo_entrada') or 0), 0)
            saida = max(int(request.form.get('saldo_saida') or 0), 0)
        except (TypeError, ValueError):
            flash('Informe valores válidos para o saldo inicial.', 'error')
            return redirect(url_for('euro.criar_componente'))

        if saida > entrada:
            flash(
                'A saída inicial não pode ser maior que a entrada inicial.',
                'error',
            )
            return redirect(url_for('euro.criar_componente'))

        bit_ativo = request.form.get('bit_ativo') == '1'
        classifica_ativo = request.form.get('classifica_ativo') == '1'
        bit_pmv = request.form.get('bit_pmv') == '1'
        chassi = str(request.form.get('chassi') or '').strip() or None
        renavam = str(request.form.get('renavam') or '').strip() or None
        saldo_inicial = entrada - saida
        id_usuario = session.get('user_id') or _id_usuario_compatibilidade_euro()

        try:
            novo_produto = Produto(
                NomeProduto=nome_produto,
                ReferenciaExterna=referencia_externa,
                BitAtivo=bit_ativo,
                ClassificaAtivo=classifica_ativo,
                Chassi=chassi,
                Renavam=renavam,
                BitPMV=bit_pmv,
                IDDepartamento=id_departamento,
                FamiliaID=familia_id,
                IDCategoriaProduto=id_categoria,
            )
            db.session.add(novo_produto)
            db.session.flush()

            novo_estoque = EstoqueMatriz(
                IDItem=novo_produto.IDItem,
                CodPonto=0,
                Saldo=saldo_inicial,
            )
            db.session.add(novo_estoque)
            db.session.flush()

            if entrada > 0:
                db.session.add(
                    Movimentacao(
                        IDUsuario=id_usuario,
                        IDItem=novo_produto.IDItem,
                        Quantidade=entrada,
                        IDProprietarioOrigem=None,
                        CodPontoOrigem=None,
                        IDProprietarioDestino=novo_estoque.IDEstoque,
                        CodPontoDestino=0,
                        NomeMovimentacao='Entrada',
                        DataMovimentacao=datetime.now(),
                    )
                )

            if saida > 0:
                db.session.add(
                    Movimentacao(
                        IDUsuario=id_usuario,
                        IDItem=novo_produto.IDItem,
                        Quantidade=saida,
                        IDProprietarioOrigem=novo_estoque.IDEstoque,
                        CodPontoOrigem=0,
                        IDProprietarioDestino=None,
                        CodPontoDestino=None,
                        NomeMovimentacao='Saida',
                        DataMovimentacao=datetime.now(),
                    )
                )

            caracteristicas = request.form.getlist('nova_caracteristica')
            valores = request.form.getlist('novo_valor')
            for carac, valor in zip(caracteristicas, valores):
                carac = str(carac or '').strip()
                valor = str(valor or '').strip()
                if carac and valor:
                    db.session.add(
                        Caracteristica(
                            IDItem=novo_produto.IDItem,
                            FamiliaID=familia_id,
                            Caracteristica=carac,
                            Valor=valor,
                            BitAtivo=True,
                            IDCategoria=None,
                        )
                    )

            pmvs_informados = set()
            for pmv in request.form.getlist('pmv_compat'):
                try:
                    pmv_id = int(pmv)
                except (TypeError, ValueError):
                    continue
                if pmv_id in pmvs_informados:
                    continue
                pmvs_informados.add(pmv_id)
                db.session.add(
                    GruposCompativeis(
                        IDItem=novo_produto.IDItem,
                        PmvID=pmv_id,
                    )
                )

            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Falha ao cadastrar produto.')
            flash('Não foi possível cadastrar o produto.', 'error')
            return redirect(url_for('euro.criar_componente'))

        flash('Produto cadastrado com sucesso.', 'success')
        return redirect(
            url_for('euro.item_detail', item_id=novo_produto.IDItem)
        )

    departamentos = (
        db.session.query(Departamento)
        .filter(Departamento.BitAtivo == 1)
        .order_by(Departamento.NomeDepartamento)
        .all()
    )
    familias = (
        db.session.query(Familia)
        .filter(Familia.BitAtivo == 1)
        .order_by(Familia.NomeFamilia)
        .all()
    )
    categorias = (
        db.session.query(CategoriasProdutos)
        .filter(CategoriasProdutos.BitAtivo == 1)
        .order_by(CategoriasProdutos.NomeCategoria)
        .all()
    )
    pmvs = (
        db.session.query(Pmv)
        .filter(Pmv.BitAtivo == 1)
        .order_by(Pmv.NomePMV)
        .all()
    )
    return render_template(
        'sp/novo_componente.html',
        departamentos=departamentos,
        familias=familias,
        categorias=categorias,
        pmvs=pmvs,
    )





@euro.route('/vincular_componentes', methods=['GET', 'POST'])
@login_required
def vincular_componentes():
    if request.method == 'POST':
        
        id_componente = request.form.get('id_componente')
        try:
            id_componente = int(id_componente)
        except (ValueError, TypeError):
            flash("ID Componente inválido.", "error")
            return redirect(url_for('euro.vincular_componentes'))
        
        
        componente = db.session.query(Produto).filter_by(IDItem=id_componente).first()
        if not componente:
            flash("ID Componente não encontrado.", "error")
            return redirect(url_for('euro.vincular_componentes'))
        
        
        pmv_list = request.form.getlist('pmv_compat')
        if not pmv_list or all(not pmv.strip() for pmv in pmv_list):
            flash("Nenhuma compatibilidade foi informada.", "error")
            return redirect(url_for('euro.vincular_componentes'))
        
        
        for pmv in pmv_list:
            if pmv.strip():
                novo_grupo = GruposCompativeis(
                    IDItem=id_componente,
                    PmvID=pmv.strip()
                )
                db.session.add(novo_grupo)
        db.session.commit()
        flash("Compatibilidades vinculadas com sucesso.", "success")
        return redirect(url_for('euro.item_detail', item_id=id_componente))
    else:
        
        pmvs = db.session.query(Pmv).all()
        return render_template('sp/vincular_componentes.html', pmvs=pmvs)



@euro.route('/componentes_vinculados')
@login_required
def componentes_vinculados():
   
    page = request.args.get('page', 1, type=int)
    per_page = 10

    
    query = db.session.query(
        Produto.IDItem,
        Produto.ReferenciaExterna,
        Produto.NomeProduto,
        Pmv.PmvID,
        Pmv.NomePMV
    ).join(
        GruposCompativeis, GruposCompativeis.IDItem == Produto.IDItem
    ).join(
        Pmv, GruposCompativeis.PmvID == Pmv.PmvID
    ).order_by(Produto.IDItem)

    total = query.count()
    componentes = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = math.ceil(total / per_page)

    return render_template(
        "sp/componentes_vinculados.html",
        componentes=componentes,
        page=page,
        total_pages=total_pages
    )



@euro.route('/login', methods=['GET', 'POST'])
def login():
    """Compatibilidade com links antigos; o login real pertence a Autenticacao."""
    return redirect(url_for('euro.lista_produtos'))



@euro.route('/logout')
def logout():
    """Delega o encerramento da sessão ao blueprint central de autenticação."""
    return redirect(url_for('Autenticacao.logout'))





@euro.route('/inserir_ativo', methods=['POST'])
@login_required
def inserir_ativo():

    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()


    iditem_str = request.form.get('IDItem','').strip()
    if not iditem_str:
        flash("Por favor, informe o IDItem.", "warning")
        return redirect(url_for('euro.ativos', page=page, search=search))
    try:
        iditem = int(iditem_str)
    except ValueError:
        flash("IDItem inválido.", "warning")
        return redirect(url_for('euro.ativos', page=page, search=search))

 
    produto = db.session.query(Produto).filter_by(IDItem=iditem).first()
    if not produto:
        flash(f"Produto com IDItem={iditem} não encontrado.", "danger")
        return redirect(url_for('euro.ativos', page=page, search=search))

 
    ref = produto.ReferenciaExterna
    if db.session.query(Ativo).filter_by(ReferenciaExterna=ref).first():
        flash(f"ReferenciaExterna {ref} já cadastrada em Ativos.", "warning")
        return redirect(url_for('euro.ativos', page=page, search=search))


    novo = Ativo(
        IDItem = produto.IDItem,
        ReferenciaExterna = produto.ReferenciaExterna,
        Chassi = produto.Chassi,
        Renavam = produto.Renavam,
        NomeAtivo = produto.NomeProduto
    )
    db.session.add(novo)
    db.session.commit()

    produto.IDAtivo = novo.IDAtivo
    db.session.commit()

    flash("Ativo inserido com sucesso!", "success")
    return redirect(url_for('euro.ativos', page=page, search=search))









def buscar_caracteristicas_do_item(item_id: int) -> list[dict]:
    resultados = (
        db.session.query(Caracteristica)
        .filter(Caracteristica.IDItem == item_id)
        .all()
    )
    specs = []
    for row in resultados:
        specs.append({
            "name":          row.Caracteristica,
            "specification": row.Valor
        })
    return specs


def gerar_description_equipamento(ativo_id: int) -> str:
    ativo = db.session.query(Ativo).get(ativo_id)
    if not ativo or not ativo.IDPedidoAtivo:
        return ""
    pedido_id = ativo.IDPedidoAtivo
    itens = (
        db.session.query(PedidoItens, Produto)
        .join(Produto, Produto.IDItem == PedidoItens.IDItem)
        .filter(PedidoItens.IDPedido == pedido_id)
        .all()
    )
    linhas = []
    for pitem, produto in itens:
        id_item = pitem.IDItem
        quantidade = pitem.Quantidade or 0
        lote = pitem.NumeroLote or ""
        serie = pitem.NumerodeSerie or ""
        nome_produto = produto.NomeProduto or ""
        linha = (
            f"IDItem: {id_item} | "
            f"Quantidade: {quantidade} | "
            f"NumeroLote: {lote} | "
            f"NumeroSerie: {serie} | "
            f"NomeProduto: {nome_produto}"
        )
        linhas.append(linha)
    return "\n".join(linhas)




import json, logging, time, urllib.parse, requests
from typing import Any, Dict, Optional


def buscar_cliente_por_externalid_filtrado(
    token_inicial: str,
    external_id: int | str,
    tentativas: int = 3
) -> Dict[str, Any] | None:
    ext = str(external_id).strip()
    if not ext:
        logging.debug("[Auvo] externalId vazio – retornando {}")
        return {}

  
    filtro_encoded = urllib.parse.quote(
        json.dumps({"externalId": ext}, ensure_ascii=False), safe=''
    )
    url = (
        f"{AUVO_BASE_URL}/customers"
        f"?paramFilter={filtro_encoded}"
        "&page=1&pageSize=1&order=asc"
    )

    for tentativa in range(1, tentativas + 1):
        token   = token_inicial if tentativa == 1 else obter_access_token_auvo()
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}

        try:
            resp   = requests.get(url, headers=headers, timeout=AUVO_TIMEOUT)
            status = resp.status_code
            logging.debug(
                "[Auvo] GET /customers ext=%s tent=%s status=%s",
                ext, tentativa, status
            )

 
            if status == 200:
                lst = resp.json().get("result", {}).get("entityList", [])
                return lst[0] if lst else {}

            if status == 404:
                return {} 

 
            if status in (401, 403):
                logging.warning(
                    "[Auvo] token rejeitado (HTTP %s) – renovando...",
                    status
                )
                continue


            logging.error("[Auvo] HTTP %s: %s", status, resp.text)
            return None

        except requests.RequestException as exc:
            logging.error(
                "[Auvo] exceção %s (tentativa %s de %s)",
                exc, tentativa, tentativas
            )
            if tentativa == tentativas:
                return None
            time.sleep(2 * tentativa) 

    return None 



def buscar_customer_id_por_description(access_token: str, descricao_exata: str) -> int:
    filtro = {"description": descricao_exata}
    filtro_json = json.dumps(filtro, ensure_ascii=False)
    filtro_encoded = quote(filtro_json, safe='')
    url = (
        f"{AUVO_BASE_URL}/customers/"
        f"?paramFilter={filtro_encoded}"
        f"&page=1&pageSize=1&order=asc"
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    resp = requests.get(url, headers=headers, timeout=AUVO_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"Erro ao buscar cliente por descrição ({resp.status_code}): {resp.text}")

    resultado = resp.json().get("result", {})
    lista = resultado.get("entityList", [])
    if not lista:
        raise RuntimeError(f"Nenhum cliente encontrado para description = '{descricao_exata}'.")
    cliente = lista[0]
    customer_id = cliente.get("id")
    if not customer_id:
        raise RuntimeError("O JSON retornado não contém campo 'id' do cliente.")
    return customer_id




def criar_equipamento(access_token: str, nome_equip: str, identifier_equip: str, customer_id: int) -> int:
    url = f"{AUVO_BASE_URL}/equipments/"
    headers = {
        "Accept": "application/json",
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    payload = {
        "name": nome_equip,
        "identifier": identifier_equip,
        "associatedCustomerId": customer_id,
        "active": True
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=AUVO_TIMEOUT)
    if resp.status_code not in (200, 201):
        try:
            data = resp.json()
            if isinstance(data, list):
                for err in data:
                    if err.get("target") == "Identifier" and err.get("errorCode") == 121:
                        return None
        except ValueError:
            pass
        raise RuntimeError(f"Falha ao criar equipamento (HTTP {resp.status_code}): {resp.text}")

    data = resp.json().get("result", {})
    novo_id = data.get("id")
    if not novo_id:
        raise RuntimeError("JSON de resposta não contém campo 'id' do equipamento criado.")
    return novo_id




def excluir_equipamento_por_id(access_token: str, equipment_id: int):
    url = f"{AUVO_BASE_URL}/equipments/{equipment_id}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        req = Request(url, headers=headers)
        req.get_method = lambda: "DELETE"
        with urlopen(req, timeout=AUVO_TIMEOUT) as resp:
            pass
    except Exception:
        pass


def excluir_cliente_por_id(access_token: str, customer_id: int):
    url = f"{AUVO_BASE_URL}/customers/{customer_id}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        req = Request(url, headers=headers)
        req.get_method = lambda: "DELETE"
        with urlopen(req, timeout=AUVO_TIMEOUT) as resp:
            pass
    except Exception:
        pass








def integrar_cliente_e_equipamento_auvo(ativo: Ativo) -> None:
    empresa_local: Empresa | None = db.session.get(Empresa, ativo.IDEmpresa)
    if not empresa_local or not empresa_local.CNPJ:
        raise RuntimeError(f"EmpresaID {ativo.IDEmpresa} sem CNPJ cadastrado.")
    cnpj_num = re.sub(r"\D", "", empresa_local.CNPJ)


    token = obter_access_token_auvo()
    cli_auvo_local = (db.session.query(ClienteAuvo)
                      .filter_by(EmpresaID=empresa_local.EmpresaID)
                      .first())
    if cli_auvo_local:
        group_id = cli_auvo_local.IDGrupoAuvo
    else:
        grupo_resp  = criar_grupo_cliente(token, empresa_local.NomeEmpresa)
        group_id = grupo_resp["result"]["id"]
        cli_auvo_local = ClienteAuvo(IDGrupoAuvo=group_id,
                                     CNPJ=cnpj_num,
                                     NomeGrupo=empresa_local.NomeEmpresa,
                                     IDProjeto=ativo.IDProjeto,
                                     EmpresaID=empresa_local.EmpresaID,
                                     BitAtivo=1)
        db.session.add(cli_auvo_local)
        db.session.commit()

 
    headers_cli = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

  
    linha_auvo = AtivoAuvo(IDAtivo=ativo.IDAtivo,
                           Endereco=empresa_local.ENDERECO or "",
                           Latitude=None, Longitude=None,
                           Cidade=empresa_local.CidadeEmpresa or "",
                           UF=empresa_local.UF or "",
                           CEP=empresa_local.CEP or "",
                           EmpresaAuvo=None, 
                           GroupID=group_id,
                           EmpresaID=empresa_local.EmpresaID,
                           BitAtivo=1)
    db.session.add(linha_auvo)
    db.session.flush()


    payload_cli = {
        "externalId": str(linha_auvo.IDAtivoAuvo),
        "name":empresa_local.NomeEmpresa,
        "cpfCnpj":cnpj_num,
        "address":empresa_local.ENDERECO or "",
        "groupId":group_id,
        "active":True,
    }
    resp_cli = requests.post(f"{AUVO_BASE_URL}/customers/", headers=headers_cli,
                             json=payload_cli, timeout=AUVO_TIMEOUT)
    if resp_cli.status_code not in (200, 201):
        raise RuntimeError(f"Erro criar cliente Auvo (HTTP {resp_cli.status_code}): {resp_cli.text}")
    customer_id = resp_cli.json()["result"]["id"]

    linha_auvo.AuvoID = customer_id


    equip_name = f"{ativo.IDAtivo} - {ativo.NomeAtivo}"
    equip_id = criar_equipamento(token, equip_name, equip_name, customer_id)
    if equip_id is None:
        filtro_eq = json.dumps({"identifier": equip_name}, ensure_ascii=False)
        url_eq = (f"{AUVO_BASE_URL}/equipments?paramFilter={quote(filtro_eq, safe='')}"
                  f"&page=1&pageSize=1&order=asc")
        resp_eq = requests.get(url_eq, headers={"Authorization": f"Bearer {token}"}, timeout=AUVO_TIMEOUT)
        resp_eq.raise_for_status()
        equip_id = resp_eq.json()["result"]["entityList"][0]["id"]

    linha_auvo.EmpresaAuvo = equip_id   
    ativo.AuvoID = equip_id  
    ativo.IDAtivoAuvo= linha_auvo.IDAtivoAuvo

    db.session.add_all([ativo, linha_auvo])
    db.session.commit()


    try:
        specs = buscar_caracteristicas_do_item(ativo.IDItem) 
    except Exception:
        specs = []

    headers_eq = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if specs:
        patch_specs = [{"op": "replace", "path": "/equipmentSpecifications", "value": specs}]
        requests.patch(f"{AUVO_BASE_URL}/equipments/{equip_id}", headers=headers_eq,
                       json=patch_specs, timeout=AUVO_TIMEOUT)

    try:
        desc_txt = gerar_description_equipamento(ativo.IDAtivo)
        if desc_txt:
            patch_desc = [{"op": "replace", "path": "/description", "value": desc_txt}]
            requests.patch(f"{AUVO_BASE_URL}/equipments/{equip_id}", headers=headers_eq,
                           json=patch_desc, timeout=AUVO_TIMEOUT)
    except Exception:
        pass





def criar_grupo_cliente(access_token: str, descricao: str, clients_id_list: list[int] | None = None) -> dict:
    if clients_id_list is None:
        clients_id_list = []
    payload = {"description": descricao, "clientsId": clients_id_list}
    resp    = requests.post(f"{AUVO_BASE_URL}/customerGroups/", headers=_headers(access_token), json=payload, timeout=AUVO_TIMEOUT)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Erro criar grupo: {resp.status_code} {resp.text}")
    return resp.json()




def criar_equipamento(access_token: str, nome: str, identifier: str, customer_id: int) -> int | None:
    payload = {"name": nome, "identifier": identifier, "associatedCustomerId": customer_id, "active": True}
    resp    = requests.post(f"{AUVO_BASE_URL}/equipments/", headers=_headers(access_token), json=payload, timeout=AUVO_TIMEOUT)
    if resp.status_code in (200, 201):
        return resp.json().get("result", {}).get("id")
    if resp.status_code == 400 and "duplicate" in resp.text.lower():
        return None
    raise RuntimeError(f"Erro criar equipamento: {resp.status_code} {resp.text}")






from requests.exceptions import ReadTimeout, RequestException
import time



def patch_equipment_customer(token: str, equipment_id: int, new_customer_id: int) -> None:
    body = [
        {"op": "replace", "path": "associatedCustomerId", "value": new_customer_id},
        {"op": "replace", "path": "active", "value": True}
    ]
    url = f"{AUVO_BASE_URL}/equipments/{equipment_id}"
    for tent in range(1, AUVO_RETRY + 1):
        try:
            r = requests.patch(url, headers=_headers(token), json=body, timeout=AUVO_TIMEOUT)
            if r.status_code == 200:
                return
            raise RuntimeError(f"PATCH /equipments {r.status_code}: {r.text}")
        except (ReadTimeout, RequestException) as e:
            if tent == AUVO_RETRY:
                raise RuntimeError(f"PATCH /equipments falhou após {AUVO_RETRY} tentativas: {e}")
            time.sleep(2)








@euro.route("/ativos", methods=["GET", "POST"])
@login_required
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def ativos():
    import re, requests
    from uuid import uuid4


    page = request.args.get("page", 1, type=int)
    per_page = 6
    search = request.args.get("search", "").strip()
    last_id = request.args.get("last_id", type=int) 

 
    def atualizar_nome_equipamento_auvo(ativo_obj: Ativo):
        rec_auvo = (db.session.query(AtivoAuvo)
                              .filter_by(IDAtivo=ativo_obj.IDAtivo)
                              .first())
        if not rec_auvo or not rec_auvo.EmpresaAuvo:
  
            return

        equipment_id = rec_auvo.EmpresaAuvo

        base_nome = re.split(r"\sPlaca\s-\s", ativo_obj.NomeAtivo, maxsplit=1)[0].strip()

        if ativo_obj.PlacaAtual:
            novo_nome = f"{base_nome} Placa - {ativo_obj.PlacaAtual}"
        else:
            novo_nome = base_nome

        novo_nome = novo_nome[:100]

        access_token = obter_access_token_auvo()
        headers_auvo = {
            "Accept":"application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
        patch_doc = [{
            "op":"replace",
            "path":"/name",
            "value":novo_nome
        }]

        resp = requests.patch(
            f"{AUVO_BASE_URL}/equipments/{equipment_id}",
            headers=headers_auvo,
            json=patch_doc,
            timeout=AUVO_TIMEOUT
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Auvo atualizar nome: HTTP {resp.status_code} → {resp.text}")
   


    if request.method == "POST":
        user_id = session.get("user_id")

     
        if "IDItem" in request.form:
            id_item = request.form.get("IDItem", type=int)
            produto = db.session.get(Produto, id_item)
            if not produto:
                flash(f"Produto (IDItem={id_item}) não encontrado.", "danger")
            elif db.session.query(Ativo).filter_by(IDItem=id_item).first():
                flash("Ativo já existe.", "warning")
            else:
                novo = Ativo(
                    IDItem=id_item,
                    NomeAtivo=produto.NomeProduto,
                    Renavam=produto.Renavam,
                    ReferenciaExterna=produto.ReferenciaExterna
                )
                db.session.add(novo)
                db.session.commit()
                try:
                    integrar_cliente_e_equipamento_auvo(novo)
                    flash("Ativo criado e integrado ao Auvo.", "success")
                except Exception as exc:
                    flash(f"Ativo criado localmente, mas falha na integração Auvo: {exc}", "warning")
            return redirect(url_for("euro.ativos", page=page, search=search))

   
        ativo = db.session.get(Ativo, request.form.get("IDAtivo", type=int))
        if not ativo:
            flash("Ativo não encontrado.", "danger")
            return redirect(url_for("euro.ativos", page=page, search=search))

        if "IDEmpresa" in request.form:
            nova_emp = request.form.get("IDEmpresa", type=int)
  
            ativo.IDEmpresa = nova_emp or None
            db.session.commit()
            flash("Empresa atualizada localmente.", "success")

   
            if ativo.IDProjeto:
                valid = db.session.query(ClienteEmpresa).filter_by(
                    EmpresaID=nova_emp,
                    IDProjeto=ativo.IDProjeto
                ).first()
                if valid:
                    try:
                        mover_equipamento_para_projeto(ativo, ativo.IDProjeto, user_id)
                        flash("Movimentação concluída após atualização de Empresa.", "success")
                    except Exception as exc:
                        db.session.rollback()
                        flash(f"Erro na movimentação: {exc}", "danger")
                else:
                    flash("Combinação Empresa × Projeto inválida.", "warning")
            else:
                flash("Selecione antes um Projeto para permitir movimentação.", "warning")

            return redirect(url_for("euro.ativos", page=page, search=search))


        if "IDProjeto" in request.form:
            novo_proj = request.form.get("IDProjeto", type=int)

 
            ativo.IDProjeto = novo_proj or None
            db.session.commit()
            flash("Projeto atualizado localmente.", "success")

  
            if ativo.IDEmpresa:
                valid = db.session.query(ClienteEmpresa).filter_by(
                    EmpresaID=ativo.IDEmpresa,
                    IDProjeto=novo_proj
                ).first()
                if valid:
                    try:
                        mover_equipamento_para_projeto(ativo, novo_proj, user_id)
                        flash("Movimentação concluída.", "success")
                    except Exception as exc:
                        db.session.rollback()
                        flash(f"Erro na movimentação: {exc}", "danger")
                else:
                    flash("Combinação Empresa × Projeto inválida.", "warning")
            else:
                flash("Selecione antes uma Empresa para permitir movimentação.", "warning")

            return redirect(url_for("euro.ativos", page=page, search=search))


        if "IDEmpresaProprietaria" in request.form:
            ativo.IDEmpresaProprietaria = request.form.get("IDEmpresaProprietaria", type=int)
            db.session.commit()
            flash("Empresa proprietária atualizada.", "success")
            return redirect(url_for("euro.ativos", page=page, search=search))

       
        if "PlacaAtual" in request.form:
            ativo.PlacaAtual = request.form.get("PlacaAtual", "").strip() or None
            db.session.commit()

            try:
                atualizar_nome_equipamento_auvo(ativo)
                flash("Placa (e nome no Auvo) atualizada.", "success")
            except Exception as exc:
                db.session.rollback() 
                flash(f"Placa alterada localmente, mas falha ao atualizar Auvo: {exc}", "warning")
        
            return redirect(url_for("euro.ativos", page=page, search=search))


    q = (
        db.session.query(
            Ativo.IDAtivo,
            Ativo.NomeAtivo,
            Ativo.Renavam,
            Ativo.PlacaAtual,
            Ativo.IDItem,
            Empresa.NomeEmpresa.label("EmpresaNome"),
            Projeto.NomeProjeto.label("ProjetoNome"),
            Ativo.IDOperacao
        )
        .join(Empresa, Empresa.EmpresaID == Ativo.IDEmpresa)
        .join(Projeto, Projeto.IDProjeto == Ativo.IDProjeto)
        .order_by(Ativo.IDAtivo.desc())
    )

   
    if search:
        ilikep = f"%{search}%"
        q = q.filter(or_(
            cast(Ativo.IDAtivo, String).ilike(ilikep),
            Ativo.NomeAtivo.ilike(ilikep),
            Ativo.Renavam.ilike(ilikep),
        ))

    
    if last_id:
        q = q.filter(Ativo.IDAtivo < last_id)


    total = q.count()
    total_pages = math.ceil(total / per_page) if total else 1

    ativos_list = (
        q
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    
    projetos = db.session.query(Projeto).order_by(Projeto.NomeProjeto).all()
    empresas = db.session.query(Empresa).order_by(Empresa.NomeEmpresa).all()

    item_ids = [a.IDItem for a in ativos_list]
    conjunto_set = {
        r.IDProdutoPai
        for r in db.session.query(ProdutoComposicao.IDProdutoPai)
                       .filter(ProdutoComposicao.IDProdutoPai.in_(item_ids))
                       .distinct()
    }
    pmv_map = {
        r.IDItem: r.PmvID
        for r in db.session.query(GruposCompativeis.IDItem, GruposCompativeis.PmvID)
                          .filter(GruposCompativeis.IDItem.in_(item_ids))
    }

    ops = db.session.query(TipoOperacao.IDOperacao, TipoOperacao.NomeOperacao).all()
    operacoes_map = {id_op: nome for id_op, nome in ops}

 
    return render_template(
        "sp/ativos.html",
        ativos = ativos_list,
        page = page,  
        total_pages= total_pages,
        search = search,
        projetos = projetos,
        empresas= empresas,
        conjunto_set = conjunto_set,
        pmv_map = pmv_map,
        operacoes_map = operacoes_map,
    )





def mover_equipamento_para_projeto(ativo: Ativo, novo_projeto: int, usuario_id: int) -> None:
    import re, requests, re as _re
    from datetime import datetime


    cli_emp = (
        db.session.query(ClienteEmpresa)
        .filter(ClienteEmpresa.IDProjeto == novo_projeto)
        .first()
    )
    if not cli_emp:
        raise RuntimeError(
            f"[MoverAtivo] Não há vínculo Empresa×Projeto em ClienteEmpresa "
            f"(IDProjeto={novo_projeto})."
        )

    empresa_id_dest = cli_emp.EmpresaID
    empresa_dest    = db.session.get(Empresa, empresa_id_dest)
    if not empresa_dest:
        raise RuntimeError(f"[MoverAtivo] EmpresaID {empresa_id_dest} não encontrada.")


    ativo_auvo_atual = (
        db.session.query(AtivoAuvo)
        .filter_by(IDAtivo=ativo.IDAtivo, BitAtivo=1)
        .first()
    )
    if not ativo_auvo_atual or not ativo_auvo_atual.EmpresaAuvo:
        integrar_cliente_e_equipamento_auvo(ativo)
        db.session.flush()
        ativo_auvo_atual = (
            db.session.query(AtivoAuvo)
            .filter_by(IDAtivo=ativo.IDAtivo, BitAtivo=1)
            .first()
        )
        if not ativo_auvo_atual or not ativo_auvo_atual.EmpresaAuvo:
            raise RuntimeError("Falha ao criar integração inicial no Auvo.")

    equipment_id  = ativo_auvo_atual.EmpresaAuvo
    cliente_id_atual = ativo_auvo_atual.AuvoID
    token  = obter_access_token_auvo()


    try:
        requests.patch(
            f"{AUVO_BASE_URL}/customers/{cliente_id_atual}",
            headers=_headers(token),
            json=[{"op": "replace", "path": "/active", "value": False}],
            timeout=AUVO_TIMEOUT,
        )
    except Exception:
        pass

    ativo_auvo_atual.BitAtivo = 0
    ativo_auvo_atual.IDAtivo = 0
    ativo_auvo_atual.DataAlterado = datetime.utcnow()
    db.session.add(ativo_auvo_atual)


    cli_auvo_dest = (
        db.session.query(ClienteAuvo)
        .filter_by(EmpresaID=empresa_id_dest)
        .first()
    )
    if cli_auvo_dest:
        group_id = cli_auvo_dest.IDGrupoAuvo
    else:
        group_id = criar_grupo_cliente(token, empresa_dest.NomeEmpresa)["result"]["id"]
        cli_auvo_dest = ClienteAuvo(
            IDGrupoAuvo=group_id,
            CNPJ=re.sub(r"\D", "", empresa_dest.CNPJ or ""),
            NomeGrupo=empresa_dest.NomeEmpresa,
            IDProjeto=novo_projeto,
            EmpresaID=empresa_id_dest,
            BitAtivo=1,
        )
        db.session.add(cli_auvo_dest)


    endereco_base = (ativo.EnderecoAtivo or empresa_dest.ENDERECO or "").strip()
    cep_base = (ativo.CEP  or empresa_dest.CEP or "").strip()
    cidade_base= (ativo.Cidade  or empresa_dest.CidadeEmpresa or "").strip()
    uf_base = (ativo.UF  or empresa_dest.UF or "").strip()

    if not ativo.EnderecoAtivo:
        ativo.EnderecoAtivo = endereco_base
    if not ativo.CEP:
        ativo.CEP = cep_base
    if not ativo.Cidade:
        ativo.Cidade = cidade_base
    if not ativo.UF:
        ativo.UF = uf_base

    endereco_completo = _re.sub(
        r"\s{2,}", " ", f"{endereco_base} {cidade_base} - {uf_base} {cep_base}".strip()
    )

   
    linha_dest = AtivoAuvo(
        IDAtivo = ativo.IDAtivo,
        Endereco= endereco_base,
        Latitude= ativo_auvo_atual.Latitude,
        Longitude = ativo_auvo_atual.Longitude,
        Cidade = cidade_base,
        UF = uf_base,
        CEP= cep_base,
        EmpresaAuvo = equipment_id,
        GroupID = group_id,
        EmpresaID = empresa_id_dest,
        BitAtivo = 1,
        DataAlterado= datetime.utcnow(),
    )
    db.session.add(linha_dest)
    db.session.flush()


    cliente_json = buscar_cliente_por_externalid_filtrado(token, linha_dest.IDAtivoAuvo)
    if cliente_json is None:
        raise RuntimeError("Falha ao consultar cliente destino no Auvo.")

    if cliente_json:
        cliente_id_dest = cliente_json["id"]
        patches = []
        if not cliente_json.get("active", True):
            patches.append({"op": "replace", "path": "/active", "value": True})
        if cliente_json.get("address") != endereco_completo:
            patches.append({"op": "replace", "path": "/address", "value": endereco_completo})
        if patches:
            requests.patch(
                f"{AUVO_BASE_URL}/customers/{cliente_id_dest}",
                headers=_headers(token),
                json=patches,
                timeout=AUVO_TIMEOUT,
            )
    else:
        payload_cli = {
            "externalId": str(linha_dest.IDAtivoAuvo),
            "name":  empresa_dest.NomeEmpresa,
            "cpfCnpj": re.sub(r"\D", "", empresa_dest.CNPJ or ""),
            "address": endereco_completo,
            "groupId": group_id,
            "active": True,
        }
        resp_cli = requests.post(
            f"{AUVO_BASE_URL}/customers/",
            headers=_headers(token),
            json=payload_cli,
            timeout=AUVO_TIMEOUT,
        )
        resp_cli.raise_for_status()
        cliente_id_dest = resp_cli.json()["result"]["id"]

    linha_dest.AuvoID      = cliente_id_dest
    linha_dest.EmpresaAuvo = equipment_id


    patch_equipment_customer(token, equipment_id, cliente_id_dest)


    db.session.add_all([
        linha_dest,
        MovimentacaoAtivo(
            IDProjetoOrigem = ativo.IDProjeto,
            IDProjetoDestino  = novo_projeto,
            IDAtivo = ativo.IDAtivo,
            IDUsuario = usuario_id,
            IDOperacaoOrigem  = 5,
            IDOperacaoDestino = 6,
            DataMovimento = int(datetime.utcnow().strftime("%Y%m%d")),
        ),
    ])

    ativo.IDProjeto = novo_projeto
    ativo.IDEmpresa = empresa_id_dest
    ativo.IDAtivoAuvo = linha_dest.IDAtivoAuvo
    db.session.add(ativo)

    db.session.commit()



def atualizar_cliente_address_auvo(access_token: str, cliente_id: int, novo_address: str) -> bool:
    url = f"{AUVO_BASE_URL}/customers/{cliente_id}"
    print(f"[DEBUG Auvo] URL de PATCH cliente: {url}")
    print(f"[DEBUG Auvo] novo_address: {novo_address}")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    patch_ops = [
        {
            "op": "replace",
            "path": "/address",
            "value": novo_address
        }
    ]
    body = json.dumps(patch_ops, ensure_ascii=False).encode("utf-8")
    print(f"[DEBUG Auvo] corpo do PATCH address: {json.dumps(patch_ops, indent=2, ensure_ascii=False)}")

    try:
        resp = requests.patch(url, headers=headers, data=body, timeout=AUVO_TIMEOUT)
        print(f"[DEBUG Auvo] status_code PATCH cliente: {resp.status_code}")
        print(f"[DEBUG Auvo] resp.text PATCH cliente: {resp.text}")
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG Auvo] RequestException no PATCH cliente: {e}")
        return False

    return resp.status_code == 200




@euro.route('/atualizar_ativo', methods=['POST'])
@login_required
def atualizar_ativo():
    data = request.get_json(silent=True) or {}
    ativo_id = data.get('ativo_id')
    field = data.get('field')
    value = data.get('value')

    campos_validos = {'PlacaAtual', 'EnderecoAtivo', 'CEP', 'Cidade', 'UF'}
    if not isinstance(ativo_id, int) or field not in campos_validos:
        return jsonify({'error': 'Parâmetros inválidos'}), 400

    ativo = db.session.query(Ativo).get(ativo_id)
    if not ativo:
        return jsonify({'error': 'Ativo não encontrado'}), 404


    print(f"[DEBUG] Atualizando Ativo ID {ativo_id}: campo '{field}' → '{value}'")
    setattr(ativo, field, value or None)
    db.session.add(ativo)
    try:
        db.session.commit()
        print(f"[DEBUG] Commit local bem-sucedido para campo '{field}'")
    except Exception as e:
        db.session.rollback()
        print(f"[DEBUG] Erro no commit local: {e}")
        return jsonify({'error': f'Erro ao salvar no banco local: {e}'}), 500


    def _obter_headers_auvo() -> dict:
        token = obter_access_token_auvo()
        return {
            "Accept":"application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

    def _atualizar_nome_equipment_auvo():
        rec_auvo = (
            db.session.query(AtivoAuvo)
                      .filter_by(IDAtivo=ativo.IDAtivo)
                      .first()
        )
        if not rec_auvo or not rec_auvo.EmpresaAuvo:
            print("[DEBUG] Equipamento ainda não integrado – nada a renomear.")
            return

        equipment_id = rec_auvo.EmpresaAuvo
        import re
        base_nome = re.split(r"\sPlaca\s-\s", ativo.NomeAtivo, maxsplit=1)[0].strip()
        novo_nome = (f"{base_nome} Placa - {ativo.PlacaAtual}"
                     if ativo.PlacaAtual else base_nome)[:100]

        patch_body = [{
            "op":"replace",
            "path": "/name",
            "value": novo_nome
        }]

        headers = _obter_headers_auvo()
        resp = requests.patch(
            f"{AUVO_BASE_URL}/equipments/{equipment_id}",
            headers=headers,
            json=patch_body,
            timeout=AUVO_TIMEOUT
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Auvo atualizar nome: HTTP {resp.status_code} → {resp.text}")

    def _atualizar_address_cliente_auvo():
        partes = []
        if ativo.EnderecoAtivo:
            partes.append(ativo.EnderecoAtivo.strip())
        if ativo.CEP:
            partes.append(ativo.CEP.strip())
        if ativo.Cidade:
            partes.append(ativo.Cidade.strip())
        if ativo.UF:
            partes.append(ativo.UF.strip())
        novo_address = " ".join(partes).strip()


        token   = obter_access_token_auvo()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }


        ext_id_busca = str(ativo.IDAtivoAuvo or "").strip()
        if not ext_id_busca:
            raise RuntimeError("IDAtivoAuvo vazio – não é possível localizar cliente no Auvo.")
        print(f"[DEBUG] Buscando cliente Auvo por externalId = '{ext_id_busca}'")
        cliente = buscar_cliente_por_externalid_filtrado(token, ext_id_busca)
        if not cliente:
            raise RuntimeError(f"Cliente Auvo com externalId={ext_id_busca} não encontrado.")

        cliente_id = cliente.get("id")
        latitude   = cliente.get("latitude")
        longitude  = cliente.get("longitude")

        patch_body = [{
            "op":"replace",
            "path": "address",  
            "value": novo_address
        }]

        resp = requests.patch(
            f"{AUVO_BASE_URL}/customers/{cliente_id}",
            headers=headers,
            json=patch_body,
            timeout=AUVO_TIMEOUT
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Auvo atualizar endereço: HTTP {resp.status_code} → {resp.text}")

  
        ativo_auvo = (
            db.session.query(AtivoAuvo)
                      .filter_by(IDAtivo=ativo_id)
                      .first()
        )
        if not ativo_auvo:
            ativo_auvo = AtivoAuvo(
                IDAtivo = ativo_id,
                AuvoID = cliente_id,
                Endereco= ativo.EnderecoAtivo or None,
                Cidade = ativo.Cidade or None,
                UF = ativo.UF or None,
                Latitude  = latitude,
                Longitude = longitude
            )
            db.session.add(ativo_auvo)
        else:
            ativo_auvo.AuvoID= cliente_id
            ativo_auvo.Endereco = ativo.EnderecoAtivo or None
            ativo_auvo.Cidade= ativo.Cidade or None
            ativo_auvo.UF = ativo.UF or None
            ativo_auvo.Latitude = latitude
            ativo_auvo.Longitude = longitude
        db.session.commit()
        print("[DEBUG] AtivoAuvo sincronizado com novo endereço.")


    try:
        if field == 'PlacaAtual':
            _atualizar_nome_equipment_auvo()

        if field in {'EnderecoAtivo', 'CEP', 'Cidade', 'UF'}:
            _atualizar_address_cliente_auvo()

    except Exception as exc:

        print(f"[DEBUG] Erro na sincronização Auvo: {exc}")
        return jsonify({'error': f'Falha ao sincronizar com Auvo: {exc}'}), 502

    return jsonify({'success': True}), 200




def _get_or_create_group(token: str, empresa) -> int:
    headers = _make_headers(token)
    cnpj = "".join(filter(str.isdigit, empresa.CNPJ or ""))

    reg = db.session.query(ClienteAuvo).filter_by(CNPJ=cnpj).first()
    if reg and reg.IDGrupoAuvo:
        return reg.IDGrupoAuvo

 
    payload = {"description": empresa.NomeEmpresa, "clientsId": []}
    r = requests.post(f"{AUVO_BASE_URL}/customerGroups/",
                      headers=headers, json=payload, timeout=AUVO_TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Auvo criar grupo HTTP {r.status_code}: {r.text}")
    gid = r.json()["result"]["clientGroupSearchReturn"]["id"]

    novo = ClienteAuvo(
        IDGrupoAuvo = gid,
        CNPJ= cnpj,
        NomeGrupo= empresa.NomeEmpresa,
        IDProjeto = empresa.IDProjeto,
        EmpresaID= empresa.EmpresaID,
        BitAtivo = 1
    )
    db.session.add(novo)
    db.session.flush()
    return gid



def _concat_endereco(ativo: Ativo) -> str:
    partes = [ativo.EnderecoAtivo or '', ativo.CEP or '', ativo.Cidade or '', ativo.UF or '']
    return ' '.join(p.strip() for p in partes if p).strip()


def _buscar_specs_item(item_id: int) -> list[dict]:
    rows = db.session.query(Caracteristica.Caracteristica, Caracteristica.Valor)
    rows = rows.filter_by(IDItem=item_id)
    rows = rows.filter(Caracteristica.Caracteristica.isnot(None), Caracteristica.Valor.isnot(None)).all()
    vistos, specs = set(), []
    for c, v in rows:
        key = (c.strip().lower(), v.strip().lower())
        if key not in vistos:
            vistos.add(key)
            specs.append({'name': c, 'specification': v})
    return specs



def _resolver_grupo_cliente(token: str, empresa: Empresa, projeto_id: Optional[int]) -> int:
    cli_auvo = db.session.query(ClienteAuvo).filter_by(EmpresaID=empresa.EmpresaID).first()
    if cli_auvo and cli_auvo.IDGrupoAuvo:
        return cli_auvo.IDGrupoAuvo

    payload = {"description": empresa.NomeEmpresa, "clientsId": []}
    r = requests.post(f"{AUVO_BASE_URL}/customerGroups/", headers=_headers(token), json=payload, timeout=AUVO_TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Criar grupo Auvo HTTP {r.status_code}: {r.text}")
    grupo_id = r.json()['result'].get('id') or r.json()['result'].get('clientGroupSearchReturn',{}).get('id')
    if not grupo_id:
        raise RuntimeError('Resposta criar grupo sem id.')



def _resolver_ativo_auvo(ativo: Ativo, empresa: Empresa, group_id: int) -> AtivoAuvo:
    aa = db.session.query(AtivoAuvo).filter_by(IDAtivo=ativo.IDAtivo).first()
    if aa:
        aa.GroupID = aa.GroupID or group_id
        return aa

    aa = AtivoAuvo(IDAtivo=ativo.IDAtivo,
                   Endereco=empresa.ENDERECO or '',
                   Cidade=empresa.CidadeEmpresa or '',
                   UF=empresa.UF or '',
                   CEP=empresa.CEP or '',
                   EmpresaID=empresa.EmpresaID,
                   GroupID=group_id,
                   BitAtivo=1,
                   DataAlterado=datetime.now())
    db.session.add(aa)
    db.session.flush()
    ativo.IDAtivoAuvo = aa.IDAtivoAuvo
    return aa




def _upsert_cliente(token: str,
                    aa: AtivoAuvo,
                    empresa: Empresa,
                    endereco: str) -> dict:
   
    payload = {
        "externalId": str(aa.IDAtivoAuvo),
        "name":empresa.NomeEmpresa[:100] if empresa else f"Cliente {aa.IDAtivoAuvo}",
        "active": bool(aa.BitAtivo),
        "groupsId":[aa.GroupID] if aa.GroupID else []
    }
    if endereco:
        payload["address"] = endereco[:250]

    h = _make_headers(token)
    r = requests.put(f"{AUVO_BASE_URL}/customers/",
                     headers=h, json=payload, timeout=AUVO_TIMEOUT)

    if r.status_code not in (200, 201):
        raise RuntimeError(f"UPSERT cliente HTTP {r.status_code}: {r.text}")

    return r.json().get("result")




def _create_or_patch_equipment(token: str, ativo: Ativo, cliente_id: int, specs: list[dict[str,str]]) -> int:
    filtro = json.dumps({'associatedCustomerId': cliente_id}, separators=(',',':'))
    url = f"{AUVO_BASE_URL}/equipments?paramFilter={requests.utils.quote(filtro,safe='')}&page=1&pageSize=50&order=asc"
    r = requests.get(url, headers=_headers(token), timeout=AUVO_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"List equip HTTP {r.status_code}: {r.text}")
    equips = r.json().get('result', {}).get('entityList', [])

    equip = next((e for e in equips if str(e.get('externalId')) == str(ativo.IDAtivo)), None)
    nome = (ativo.NomeAtivo or '').split(' Placa - ')[0].strip()
    if ativo.PlacaAtual:
        nome = f"{nome} Placa - {ativo.PlacaAtual}"
    nome = nome[:100]

    if equip is None:
        payload = {
            'name': nome,
            'identifier': f"{ativo.IDAtivo}-{uuid4().hex[:6]}",
            'associatedCustomerId': cliente_id,
            'externalId': str(ativo.IDAtivo),
            'categoryId': 0,
            'active': True,
            'equipmentSpecifications': specs
        }
        r = requests.post(f"{AUVO_BASE_URL}/equipments/", headers=_headers(token), json=payload, timeout=AUVO_TIMEOUT)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Criar equip HTTP {r.status_code}: {r.text}")
        return r.json()['result']['id']

    equip_id = equip['id']
    patch = [
        {'op':'replace','path':'/name','value': nome},
        {'op':'replace','path':'/externalId','value': str(ativo.IDAtivo)},
        {'op':'replace','path':'/equipmentSpecifications','value': specs}
    ]
    r = requests.patch(f"{AUVO_BASE_URL}/equipments/{equip_id}", headers=_headers(token), json=patch, timeout=AUVO_TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Patch equip HTTP {r.status_code}: {r.text}")
    return equip_id





@euro.route('/ativos_sincronizar/<int:ativo_id>', methods=['POST'])
@login_required
def ativos_sincronizar(ativo_id: int):
    try:
        ativo = db.session.get(Ativo, ativo_id)
        if not ativo:
            return jsonify({'error': 'Ativo não encontrado'}), 404

        empresa = db.session.get(Empresa, ativo.IDEmpresa) if ativo.IDEmpresa else None
        if not empresa:
            return jsonify({'error': 'Empresa vinculada não encontrada'}), 400

        token = obter_access_token_auvo()

        group_id = _resolver_grupo_cliente(token, empresa, ativo.IDProjeto)
        aa = _resolver_ativo_auvo(ativo, empresa, group_id)


        res_cli= _upsert_cliente(token, ativo, aa)
        cliente_id = res_cli['id']
        aa.AuvoID = cliente_id
        aa.Latitude = res_cli.get('latitude')
        aa.Longitude = res_cli.get('longitude')
        ativo.AuvoID = cliente_id

       
        equip_id = _create_or_patch_equipment(
            token,
            ativo,
            cliente_id,
            _buscar_specs_item(ativo.IDItem)
        )
        aa.EmpresaAuvo  = equip_id
        aa.DataAlterado = datetime.now()

        db.session.add_all([ativo, aa])
        db.session.commit()
        return jsonify({'success': True,
                        'clienteId': cliente_id,
                        'equipmentId': equip_id}), 200

    except Exception as exc:
        db.session.rollback()
        logging.exception('Erro sincronizar ativo')
        return jsonify({'error': str(exc)}), 500




def _make_headers(token: str) -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }





def _cliente_por_ids(token: str,
                     external_id: str | None) -> dict | None:
    h = _make_headers(token)

    if external_id:
        filtro = requests.utils.quote(
            json.dumps({"externalId": external_id}, separators=(",", ":"))
        )
        url = f"{AUVO_BASE_URL}/customers?paramFilter={filtro}&page=1&pageSize=1"
        print(f"[Auvo 🔍] BUSCANDO POR externalId={external_id}: GET {url}")
        r = requests.get(url, headers=h, timeout=AUVO_TIMEOUT)
        print(f"[Auvo 🔍]   → status {r.status_code}; body: {r.text}")
        if r.status_code == 200:
            lst = r.json().get("result", {}).get("entityList", [])
            if lst:
                print(f"[Auvo 🔍]   → ENCONTRADO via externalId: {lst[0]}")
                return lst[0]

    print("[Auvo 🔍] cliente NÃO encontrado por externalId.")
    return None






def _criar_cliente(token: str,
                   aa: AtivoAuvo,
                   empresa: Empresa,
                   endereco: str) -> dict:

    group_id = _get_or_create_group(token, empresa)
    payload = {
        "externalId": str(aa.IDAtivoAuvo),
        "name":empresa.NomeEmpresa,
        "address": endereco,
        "groupsId":[group_id],
        "active": bool(aa.BitAtivo),
        "creationDate": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }
    h = _make_headers(token)
    print(f"[Auvo ➕] CRIANDO cliente → POST {AUVO_BASE_URL}/customers/")
    print(f"[Auvo ➕] Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")
    r = requests.post(f"{AUVO_BASE_URL}/customers/", headers=h,
                      json=payload, timeout=AUVO_TIMEOUT)
    print(f"[Auvo ➕]   → status {r.status_code}; body: {r.text}")
    if r.status_code not in (200, 201):
        raise RuntimeError(f"POST cliente HTTP {r.status_code}: {r.text}")

    cid = r.json()["result"]["id"]
    full = requests.get(f"{AUVO_BASE_URL}/customers/{cid}",
                        headers=h, timeout=AUVO_TIMEOUT)
    print(f"[Auvo 🔍] GET pós-criação → status {full.status_code}; body: {full.text}")
    return full.json()["result"]



def _upsert_equipment(token: str,
                      ativo: Ativo,
                      cliente_id: int,
                      specs: list[dict],
                      nome_produto: str) -> int:
  
    h = _make_headers(token)
    filtro = json.dumps({"associatedCustomerId": cliente_id}, separators=(",", ":"))
    url = f"{AUVO_BASE_URL}/equipments?paramFilter={requests.utils.quote(filtro)}&page=1&pageSize=50"
    print(f"[Auvo 🔍] LISTANDO EQUIPAMENTOS → GET {url}")
    r = requests.get(url, headers=h, timeout=AUVO_TIMEOUT)

    if r.status_code == 404:
        equips = []
        print(f"[Auvo 🔍]   → 404; assumindo nenhum equipamento para cliente {cliente_id}")
    elif r.status_code != 200:
        raise RuntimeError(f"List equip HTTP {r.status_code}: {r.text}")
    else:
        equips = r.json().get("result", {}).get("entityList", [])
        print(f"[Auvo 🔍]   → {len(equips)} equipamentos retornados")

    external = str(ativo.IDAtivo)
    exist = next((e for e in equips if str(e.get("externalId")) == external), None)

    nome_base = nome_produto.split(" Placa - ")[0].strip()
    nome = f"{nome_base} Placa - {ativo.PlacaAtual}" if ativo.PlacaAtual else nome_base
    nome = nome[:100]

    if not exist:
        payload = {
            "name": nome,
            "identifier":f"{ativo.IDAtivo}-{uuid4().hex[:6]}",
            "associatedCustomerId":  cliente_id,
            "externalId": external,
            "categoryId": 0,
            "active": True,
            "equipmentSpecifications": specs
        }
        print(f"[Auvo ➕] CREATE EQUIPMENT → POST {AUVO_BASE_URL}/equipments/")
        print(f"[Auvo ➕] Payload: {json.dumps(payload, indent=2)}")
        r2 = requests.post(f"{AUVO_BASE_URL}/equipments/", headers=h, json=payload, timeout=AUVO_TIMEOUT)
        print(f"[Auvo ➕] Response {r2.status_code}; body: {r2.text}")
        if r2.status_code not in (200, 201):
            raise RuntimeError(f"POST equip HTTP {r2.status_code}: {r2.text}")
        return r2.json()["result"]["id"]


    equip_id = exist["id"]
    patch = [
        {"op": "replace", "path": "/name", "value": nome},
        {"op": "replace", "path": "/equipmentSpecifications","value": specs}
    ]
    print(f"[Auvo 🔄] UPDATE EQUIPMENT {equip_id} → PATCH {AUVO_BASE_URL}/equipments/{equip_id}")
    print(f"[Auvo 🔄] Patch payload: {json.dumps(patch, indent=2)}")
    r3 = requests.patch(f"{AUVO_BASE_URL}/equipments/{equip_id}", headers=h, json=patch, timeout=AUVO_TIMEOUT)
    print(f"[Auvo 🔄] Response {r3.status_code}; body: {r3.text}")
    if r3.status_code not in (200, 201):
        raise RuntimeError(f"PATCH equip HTTP {r3.status_code}: {r3.text}")
    return equip_id





@euro.route('/ativos_atualizar/<int:ativo_id>', methods=['POST'])
@login_required
def ativos_atualizar(ativo_id: int):
    try:
        ativo = db.session.get(Ativo, ativo_id)
        if not ativo:
            return jsonify(error="Ativo não encontrado"), 404

        aa = db.session.get(AtivoAuvo, ativo.IDAtivoAuvo) if ativo.IDAtivoAuvo else None
        if not aa:
            cli_emp = db.session.query(ClienteEmpresa)\
                                .filter_by(IDProjeto=ativo.IDProjeto,
                                           EmpresaID=ativo.IDEmpresa)\
                                .first()
            empresa_id = cli_emp.EmpresaID if cli_emp else ativo.IDEmpresa
            aa = AtivoAuvo(
                IDAtivo   = ativo.IDAtivo,
                EmpresaID = empresa_id,
                BitAtivo = True,
                GroupID = None, 
                DataAlterado  = datetime.now()
            )
            db.session.add(aa)
            db.session.flush()
            ativo.IDAtivoAuvo = aa.IDAtivoAuvo

        emp = db.session.get(Empresa, aa.EmpresaID)
        aa.Endereco = aa.Endereco or emp.ENDERECO or ''
        aa.Cidade  = aa.Cidade  or emp.CidadeEmpresa or ''
        aa.UF = aa.UF or emp.UF or ''
        aa.CEP = aa.CEP or emp.CEP or ''
        db.session.add(aa)
        db.session.flush()
        endereco_txt = ' '.join(filter(None, [
            aa.Endereco.strip(),
            aa.CEP.strip(),
            aa.Cidade.strip(),
            aa.UF.strip()
        ]))


       
        token   = obter_access_token_auvo()
        cliente = _cliente_por_ids(token, str(aa.IDAtivoAuvo))
        if not cliente:
            print(f"[DEBUG] Cliente não encontrado → criando novo")
            cliente = _criar_cliente(token, aa, emp, endereco_txt)

        cid = cliente["id"]
        print(f"[DEBUG] Cliente Auvo ID final: {cid}")

    
        aa.AuvoID = cid
        aa.Latitude = cliente.get("latitude")
        aa.Longitude = cliente.get("longitude")
        grupos = cliente.get("groupsId")
        aa.GroupID = grupos[0] if isinstance(grupos, list) and grupos else None 
        ativo.AuvoID = cid
        db.session.add_all([aa, ativo])
        db.session.commit()

        specs = _buscar_specs_item(ativo.IDItem)
        prod  = db.session.get(Produto, ativo.IDItem)
        nome  = prod.NomeProduto if prod else ativo.NomeAtivo
        print(f"[DEBUG] Specs a enviar: {specs}")
        print(f"[DEBUG] Nome do produto para equipamento: {nome}")

   
        equip_id = _upsert_equipment(token, ativo, cid, specs, nome)
        print(f"[DEBUG] Equipment Auvo ID final: {equip_id}")

        aa.EmpresaAuvo  = equip_id
        aa.DataAlterado = datetime.now()
        db.session.add(aa)
        db.session.commit()

        return jsonify(success=True, clienteId=cid, equipmentId=equip_id), 200

    except Exception as e:
        db.session.rollback()
        logging.exception("Erro ativos_atualizar")
        return jsonify(error=str(e)), 500






@euro.route('/ativos_detalhes/<int:ativo_id>', methods=['GET'])
@login_required
def ativos_detalhes(ativo_id):
    ativo = db.session.query(Ativo).get(ativo_id)
    if not ativo:
        flash('Ativo não encontrado.', 'danger')
        return redirect(url_for('euro.ativos'))

    map_query = quote_plus(f"{ativo.CEP or ''}, {ativo.Cidade or ''} - {ativo.UF or ''}")
    proprietaria_empresa = (
        db.session.query(Empresa).get(ativo.IDEmpresaProprietaria)
        if ativo.IDEmpresaProprietaria else None
    )
    empresa = (
        db.session.query(Empresa).get(ativo.IDEmpresa)
        if ativo.IDEmpresa else None
    )
    projeto_obj = db.session.query(Projeto).get(ativo.IDProjeto) if ativo.IDProjeto else None

    pedidos_vinculados = (
        db.session.query(Pedidos)
        .join(PedidoAtivo, PedidoAtivo.IDPedido == Pedidos.IDPedido)
        .filter(PedidoAtivo.IDAtivo == ativo.IDAtivo)
        .all()
    )
    for pedido in pedidos_vinculados:
        itens_query = db.session.query(PedidoItens).filter(PedidoItens.IDPedido == pedido.IDPedido).all()
        lista_itens = []
        for pitem in itens_query:
            produto = db.session.query(Produto).get(pitem.IDItem)
            nome_produto = produto.NomeProduto if produto else '—'
            foto_url = "https://cdn.awsli.com.br/production/static/img/produto-sem-imagem.gif"
            tipo_estoque_obj = db.session.query(TipoEstoque).get(pitem.IDTipoEstoque)
            tipo_estoque = tipo_estoque_obj.NomeEstoque if tipo_estoque_obj else '—'

            lista_itens.append({
                'id_pedido_iten':pitem.IDPedidoIten,
                'id_item':pitem.IDItem,
                'foto_url':foto_url,
                'nome_produto':  nome_produto,
                'quantidade': pitem.Quantidade,
                'lote':pitem.NumeroLote or '—',
                'serie':pitem.NumerodeSerie or '—',
                'tipo_estoque': tipo_estoque,
                'codponto': pitem.CodPonto or '—',
            })

        setattr(pedido, 'itens', lista_itens)


    ativo_auvo = (
                db.session
                .query(AtivoAuvo)
                .filter(
                    AtivoAuvo.IDAtivo  == ativo.IDAtivo,
                    AtivoAuvo.AuvoID  != None 
                )
                .first()
            )


    operacoes_list = (
            db.session
            .query(TipoOperacao)
            .filter(TipoOperacao.IDOperacao.in_([1015, 1016, 1017, 1018]))
            .order_by(TipoOperacao.NomeOperacao)
            .all()
        )
 
    return render_template(
        'sp/ativos_detalhes.html',
        ativo=ativo,
        map_query=map_query,
        proprietaria_empresa=proprietaria_empresa,
        empresa=empresa,
        projeto_obj=projeto_obj,
        pedidos_vinculados=pedidos_vinculados,
        ativo_auvo=ativo_auvo,
        operacoes_list=operacoes_list
    )





@euro.route('/ativos_detalhes/<int:ativo_id>/operacao', methods=['POST'])
@login_required
def atualizar_operacao(ativo_id):
    novo_id = request.form.get('IDOperacao', type=int)
    ativo = db.session.get(Ativo, ativo_id)
    if not ativo:
        flash('Ativo não encontrado.', 'danger')
    else:
        ativo.IDOperacao = novo_id
        db.session.commit()
        flash('Operação atualizada com sucesso.', 'success')
    return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))






def buscar_equipamentos_por_cliente(access_token: str, cliente_id: int) -> list[dict]:
    page = 1
    page_size = 50
    encontrados: list[dict] = []

  
    filtro = {'associatedCustomerId': cliente_id}
    filtro_json = json.dumps(filtro, separators=(',', ':'), ensure_ascii=False)
    filtro_encoded = quote(filtro_json, safe='')

    while True:
        url = (
            f"{AUVO_BASE_URL}/equipments"
            f"?paramFilter={filtro_encoded}"
            f"&page={page}"
            f"&pageSize={page_size}"
            f"&order=asc"
        )
        headers = {
            'Accept':        'application/json',
            'Authorization': f'Bearer {access_token}'
        }

        resp = requests.get(url, headers=headers, timeout=AUVO_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(f"Falha ao listar equipamentos. "
                               f"HTTP {resp.status_code}: {resp.text}")

        data = resp.json().get('result', {})
        lista = data.get('entityList', [])
        encontrados.extend(lista)

     
        paged = data.get('pagedSearchReturnData', {})
        total_items   = paged.get('totalItems', 0)
        page_size_ret = paged.get('pageSize', page_size)
        total_pages   = (total_items + page_size_ret - 1) // page_size_ret

        if page >= total_pages:
            break
        page += 1

    return encontrados




@euro.route('/vincular_pedido_ativo/<int:ativo_id>', methods=['POST'])
@login_required
def vincular_pedido_ativo(ativo_id):
    pedido_id = request.form.get('id_pedido')
    if not pedido_id:
        flash("ID do pedido não informado.", "danger")
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))
    try:
        pedido_int = int(pedido_id)
    except ValueError:
        flash("ID do pedido inválido.", "warning")
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))


    if not db.session.query(Pedidos).get(pedido_int):
        flash(f"Pedido #{pedido_int} não encontrado.", "danger")
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))


    if db.session.query(PedidoAtivo).filter_by(IDAtivo=ativo_id, IDPedido=pedido_int).first():
        flash("Esse pedido já está vinculado ao ativo.", "warning")
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))

    db.session.add(PedidoAtivo(IDAtivo=ativo_id, IDPedido=pedido_int))
    db.session.commit()


    itens = (
        db.session.query(PedidoItens, Produto)
        .join(Produto, Produto.IDItem == PedidoItens.IDItem)
        .filter(PedidoItens.IDPedido == pedido_int)
        .all()
    )
    linhas = []
    for pitem, prod in itens:
        nome = prod.NomeProduto if prod else '—'
        linhas.append(
            f"IDItem: {pitem.IDItem} | Quantidade: {pitem.Quantidade or 0}"
            f" | NumeroLote: {pitem.NumeroLote or '—'} | NumeroSerie: {pitem.NumerodeSerie or '—'}"
            f" | NomeProduto: {nome}"
        )
    novo_conteudo = "\n".join(linhas)

    
    try:
        token = obter_access_token_auvo()
    except Exception as e:
        flash(f"Erro ao autenticar no Auvo: {e}", "danger")
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))


    ativo = db.session.query(Ativo).get(ativo_id)
    cliente = buscar_cliente_por_externalid_filtrado(token, str(ativo_id))
    if not cliente:
        flash('Cliente Auvo não encontrado.', 'warning')
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))
    lista_eq = buscar_equipamentos_por_cliente(token, cliente['id'])
    if not lista_eq:
        flash('Nenhum equipamento associado ao cliente Auvo.', 'warning')
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))

 
    eq_id = lista_eq[0]['id']
    resp_det = requests.get(f"{AUVO_BASE_URL}/equipments/{eq_id}",
                             headers={'Authorization': f'Bearer {token}'}, timeout=30)
    if resp_det.status_code != 200:
        flash(f'Erro ao obter detalhes do equipamento. HTTP {resp_det.status_code}', 'danger')
        return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))
    descricao_atual = resp_det.json().get('result', {}).get('description', '')

 
    descricao_final = f"{descricao_atual}\n{novo_conteudo}" if descricao_atual else novo_conteudo
    patch_data = [{"op": "replace", "path": "description", "value": descricao_final}]
    resp_patch = requests.patch(
        f"{AUVO_BASE_URL}/equipments/{eq_id}",
        json=patch_data,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        timeout=30
    )
    if resp_patch.status_code != 200:
        flash(f'Erro ao atualizar descrição no Auvo. HTTP {resp_patch.status_code}', 'danger')
    else:
        flash(f"Pedido #{pedido_int} vinculado e descrição atualizada com sucesso.", 'success')

    return redirect(url_for('euro.ativos_detalhes', ativo_id=ativo_id))






@euro.route('/item/<int:item_id>/remover_imagem', methods=['POST'])
@login_required
@requer_acesso_catalogo_produtos
def remover_imagem(item_id):
    filename = _normalizar_nome_arquivo_imagem(request.form.get('filename'))
    if not filename:
        flash('Nenhum arquivo especificado para remoção.', 'danger')
        return redirect(url_for('euro.item_detail', item_id=item_id))

    imagens_item = (
        db.session.query(ImagensProdutos)
        .filter_by(IDItem=item_id)
        .all()
    )
    img = next(
        (
            registro
            for registro in imagens_item
            if _normalizar_nome_arquivo_imagem(
                registro.NomeArquivo or registro.CaminhoArquivo
            ) == filename
        ),
        None,
    )

    if not img:
        flash('Registro de imagem não encontrado.', 'warning')
        return redirect(url_for('euro.item_detail', item_id=item_id))

    full_path = _caminho_imagem_produto(
        img.NomeArquivo or img.CaminhoArquivo
    )
    try:
        if full_path and os.path.isfile(full_path):
            os.remove(full_path)
        else:
            flash('Arquivo não encontrado no sistema de arquivos.', 'warning')
    except Exception as e:
        flash(f'Erro ao apagar arquivo: {e}', 'danger')


    try:
        db.session.delete(img)
        db.session.commit()
        flash('Imagem removida com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao remover registro do banco: {e}', 'danger')

    return redirect(url_for('euro.item_detail', item_id=item_id))




def get_item_images(item_id):
    upload_folder = _pasta_imagens_produtos()
    imagens = []
    try:
        for fname in os.listdir(upload_folder):
            if fname.startswith(f"{item_id}-"):
                imagens.append(fname)
    except FileNotFoundError:
        return []
    return sorted(imagens)

def get_next_position(item_id):
    images = get_item_images(item_id)
    used = set()
    for img in images:
        name, _ = os.path.splitext(img)
        parts = name.split('-', 1)
        if len(parts) == 2 and parts[0] == str(item_id):
            try:
                used.add(int(parts[1]))
            except ValueError:
                pass

    ordens_banco = (
        db.session.query(ImagensProdutos.Ordem)
        .filter(ImagensProdutos.IDItem == item_id)
        .all()
    )
    used.update(
        int(ordem)
        for (ordem,) in ordens_banco
        if ordem is not None
    )

    for pos in range(1, 5):
        if pos not in used:
            return pos
    return None





@euro.route('/item/<int:item_id>/upload_imagem', methods=['POST'])
@login_required
@requer_acesso_catalogo_produtos
def upload_imagem(item_id):
    imagem = request.files.get('imagem')
    if not imagem or not imagem.filename:
        flash('Nenhuma imagem enviada.', 'danger')
        return redirect(url_for('euro.item_detail', item_id=item_id))


    ordem = get_next_position(item_id)
    if ordem is None:
        flash('Limite de 4 imagens por item atingido.', 'warning')
        return redirect(url_for('euro.item_detail', item_id=item_id))


    original = secure_filename(imagem.filename)
    _, ext = os.path.splitext(original)
    ext = ext.lower()
    if ext not in _EXTENSOES_IMAGEM_PRODUTO:
        flash('Formato inválido. Envie PNG, JPG, JPEG, GIF ou WEBP.', 'danger')
        return redirect(url_for('euro.item_detail', item_id=item_id))

    novo_nome = f"{item_id}-{ordem}{ext}"

    upload_folder = _pasta_imagens_produtos()
    os.makedirs(upload_folder, exist_ok=True)
    caminho_absoluto = os.path.join(upload_folder, novo_nome)
    imagem.save(caminho_absoluto)

    caminho_relativo = os.path.join(
        'static',
        'imagens',
        'produtos',
        novo_nome,
    ).replace(os.sep, '/')
    registro = ImagensProdutos(
        NomeArquivo=novo_nome,
        CaminhoArquivo=caminho_relativo,
        IDItem=item_id,
        Ordem=ordem
    )
    try:
        db.session.add(registro)
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            if os.path.isfile(caminho_absoluto):
                os.remove(caminho_absoluto)
        except OSError:
            current_app.logger.exception(
                'Não foi possível remover a imagem após falha no banco.'
            )
        current_app.logger.exception('Falha ao registrar imagem do produto.')
        flash('Não foi possível registrar a imagem enviada.', 'danger')
        return redirect(url_for('euro.item_detail', item_id=item_id))

    flash('Imagem anexada com sucesso.', 'success')
    return redirect(url_for('euro.item_detail', item_id=item_id))








@euro.route('/get_nome_produto/<int:item_id>')
@login_required
def get_nome_produto(item_id):
    prod = db.session.query(Produto).filter_by(IDItem=item_id).first()
    if not prod:
        return jsonify({"nome": "", "controla_lote": 0})
    return jsonify({
        "nome": prod.NomeProduto,
        "controla_lote": prod.ControlaLote 
    })









@euro.route('/adicionar_saldos', methods=['GET', 'POST'])
@login_required
def adicionar_saldos():
    tipo_ids = {
        'EstoqueMatriz': 1,
        'Reserva Técnico': 2,
        'EstoqueManutencaoExterna': 3,
        'Pedido Venda': 4,
        'EstoqueContainer': 6,
        'EstoqueEuroMatriz': 10,
        'EstoqueEuro': 7,
        'EstoqueSp': 8,
        'EstoqueManutencaoInterna': 9
    }

    estoque_models = {
        'EstoqueMatriz': EstoqueMatriz,
        'EstoqueManutencaoExterna': EstoqueManutencaoExterna,
        'Pedido Venda': None,
        'EstoqueContainer': EstoqueContainer,
        'EstoqueEuroMatriz': EstoqueEuroMatriz,
        'EstoqueEuro': EstoqueEuro,
        'EstoqueSp': EstoqueSp,
        'EstoqueManutencaoInterna': EstoqueManutencaoInterna
    }

    if request.method == 'POST':
        lines = request.form.getlist('lines[]')
        if not lines:
            flash("Nenhuma linha enviada.", "error")
            return redirect(url_for('euro.adicionar_saldos'))

        try:
            lines_parsed = [json.loads(l) for l in lines]
        except Exception as e:
            flash(f"Erro ao decodificar linhas: {e}", "error")
            return redirect(url_for('euro.adicionar_saldos'))

        for idx, line in enumerate(lines_parsed, start=1):
            try:
                id_item = int(line.get('id_item', 0))
                quantidade = int(line.get('quantidade', 0))
                raw_op = line.get('operacao', '').strip().lower()
                nome_estoque = line.get('nome_estoque', '').strip()
                cod_ponto_val = line.get('cod_ponto', '').strip()
                numero_lote = line.get('numero_lote', '').strip() or None
                numero_serie = line.get('numero_serie', '').strip() or None

                if raw_op in ('adicionar', 'entrada'):
                    operacao = 'entrada'
                elif raw_op in ('remover', 'saida', 'saída'):
                    operacao = 'remover'
                else:
                    operacao = None

                if operacao == 'entrada' and numero_lote is None:
                    numero_lote = '1'

                if (not id_item
                        or operacao not in ('entrada', 'remover')
                        or quantidade <= 0
                        or not nome_estoque):
                    flash(f"[Linha {idx}] Preencha todos os campos obrigatórios.", "error")
                    continue

                prod = db.session.query(Produto).filter_by(IDItem=id_item).first()
                if not prod:
                    flash(f"[Linha {idx}] Produto {id_item} não encontrado.", "error")
                    continue

                if numero_lote and not prod.ControlaLote:
                    prod.ControlaLote = True
                if numero_serie and not prod.ControlaNumerodeSerie:
                    prod.ControlaNumerodeSerie = True

                tipo = tipo_ids.get(nome_estoque)
                if not tipo:
                    flash(f"[Linha {idx}] Estoque inválido: {nome_estoque}.", "error")
                    continue

                estoque_reg = None
                cod_ponto = None
                euro_id = None
                sp_id = None

                Model = estoque_models.get(nome_estoque)
                if nome_estoque in ('EstoqueEuro', 'EstoqueEuroMatriz'):
                    try:
                        euro_id, cod_ponto = map(int, cod_ponto_val.split('|'))
                    except Exception:
                        flash(f"[Linha {idx}] Formato inválido de ponto para EstoqueEuro/EUroMatriz: '{cod_ponto_val}'.", "error")
                        continue
                    estoque_reg = db.session.query(Model).filter_by(
                        IDItem=id_item, EuroID=euro_id, CodPonto=cod_ponto
                    ).first()

                elif nome_estoque == 'EstoqueSp':
                    try:
                        sp_id, cod_ponto = map(int, cod_ponto_val.split('|'))
                    except Exception:
                        flash(f"[Linha {idx}] Formato inválido de ponto para EstoqueSp: '{cod_ponto_val}'.", "error")
                        continue
                    estoque_reg = db.session.query(Model).filter_by(
                        IDItem=id_item, SpId=sp_id, CodPonto=cod_ponto
                    ).first()

                else: 
                    estoque_reg = db.session.query(Model).filter_by(IDItem=id_item).first()

                if operacao == 'entrada' and not estoque_reg:
                    kwargs = {'IDItem': id_item, 'IDTipoEstoque': tipo}

                    if nome_estoque in ('EstoqueEuro', 'EstoqueEuroMatriz'):
                        kwargs.update(EuroID=euro_id, CodPonto=cod_ponto)
                        if nome_estoque == 'EstoqueEuroMatriz':
                            kwargs['Quantidade'] = quantidade
                        else:
                            kwargs['Saldo'] = quantidade

                    elif nome_estoque == 'EstoqueSp':
                        kwargs.update(SpId=sp_id,
                                      CodPonto=cod_ponto,
                                      Saldo=quantidade)

                    elif nome_estoque == 'EstoqueMatriz':
                        kwargs['Saldo'] = 0
                    elif nome_estoque in ('EstoqueContainer', 'EstoqueManutencaoExterna', 'EstoqueManutencaoInterna'):
                        kwargs['Saldo'] = 0

                    novo = Model(**kwargs)
                    db.session.add(novo)
                    db.session.flush()
                    estoque_reg = novo

                if not estoque_reg:
                    flash(f"[Linha {idx}] Não foi possível localizar ou criar registro de estoque para '{nome_estoque}'.", "error")
                    continue

                field = 'Saldo' if hasattr(estoque_reg, 'Saldo') else 'Quantidade'
                atual = getattr(estoque_reg, field) or 0

                if operacao == 'remover':
                    if atual < quantidade:
                        flash(f"[Linha {idx}] Saldo insuficiente em {nome_estoque}.", "error")
                        continue
                    setattr(estoque_reg, field, atual - quantidade)
                else:
                    setattr(estoque_reg, field, atual + quantidade)

                estoque_reg.IDTipoEstoque = tipo
                db.session.add(estoque_reg)
                db.session.flush()
                estoque_id = estoque_reg.IDEstoque

                if prod.ControlaLote and numero_lote:
                    lote_filtros = {
                        'IDItem': id_item,
                        'IDEstoque': estoque_id,
                        'NumeroLote': numero_lote,
                        'IDTipoEstoque': tipo,
                        'CodPonto': cod_ponto
                    }
                    lote_q = db.session.query(EstoqueLotes).filter_by(**lote_filtros).first()

                    if operacao == 'remover':
                        if not lote_q or (lote_q.Quantidade or 0) < quantidade:
                            flash(f"[Linha {idx}] Quantidade de lote insuficiente: {numero_lote}.", "error")
                            continue
                        lote_q.Quantidade = (lote_q.Quantidade or 0) - quantidade
                        db.session.add(lote_q)
                    else:
                        if lote_q:
                            lote_q.Quantidade = (lote_q.Quantidade or 0) + quantidade
                        else:
                            novo_lote = EstoqueLotes(
                                IDItem=id_item,
                                IDEstoque=estoque_id,
                                NumeroLote=numero_lote,
                                NumerodeSerie=numero_serie,
                                Quantidade=quantidade,
                                IDTipoEstoque=tipo,
                                CodPonto=cod_ponto
                            )
                            db.session.add(novo_lote)

                if numero_serie:
                    serie_filtros = {
                        'IDItem': id_item,
                        'IDEstoque': estoque_id,
                        'NumeroSerie': numero_serie,
                        'IDTipoEstoque': tipo,
                        'CodPonto': cod_ponto
                    }
                    if numero_lote:
                        serie_filtros['NumeroLote'] = numero_lote

                    serie_q = db.session.query(EstoqueSerie).filter_by(**serie_filtros).first()

                    if operacao == 'remover':
                        if not serie_q:
                            flash(f"[Linha {idx}] Série não encontrada para remoção: {numero_serie}.", "error")
                            continue
                        serie_q.DataSaida = datetime.now()
                        db.session.add(serie_q)
                    else:
                        if not serie_q:
                            novo_serie = EstoqueSerie(
                                IDItem=id_item,
                                NumeroSerie=numero_serie,
                                NumeroLote=numero_lote,
                                IDEstoque=estoque_id,
                                TipoEstoque=nome_estoque,
                                CodPonto=cod_ponto,
                                IDTipoEstoque=tipo,
                                DataEntrada=datetime.now()
                            )
                            db.session.add(novo_serie)

                mov = Movimentacao(
                    IDUsuario=session.get('user_id'),
                    NomeMovimentacao='Saída' if operacao == 'remover' else 'Entrada',
                    IDItem=id_item,
                    Quantidade=quantidade,
                    IDProprietarioOrigem=estoque_id if operacao == 'remover' else None,
                    CodPontoOrigem=cod_ponto if operacao == 'remover' else None,
                    IDProprietarioDestino=estoque_id if operacao == 'entrada' else None,
                    CodPontoDestino=cod_ponto if operacao == 'entrada' else None,
                    NumeroLoteOrigem=numero_lote if operacao == 'remover' else None,
                    NumeroLoteDestino=numero_lote if operacao == 'entrada' else None,
                    DataMovimentacao=datetime.now(),
                    IDTipoEstoqueOrigem=tipo_ids['EstoqueMatriz'],
                    IDTipoEstoqueDestino=tipo
                )
                db.session.add(mov)

                if operacao == 'remover':
                    sp_op = 5
                    sp_idorigem = estoque_id
                    sp_tipoorigem = nome_estoque
                    sp_iddest = None
                    sp_tipodest = None
                    sp_idtipo = tipo_ids['EstoqueMatriz']
                else:
                    sp_op = 6
                    sp_idorigem = None
                    sp_tipoorigem = None
                    sp_iddest = estoque_id
                    sp_tipodest = nome_estoque
                    sp_idtipo = tipo

                sp_params = {
                    "IDItem": id_item,
                    "NumeroLote": numero_lote,
                    "NumeroSerie": numero_serie,
                    "IDOrigem": sp_idorigem,
                    "TipoOrigem": sp_tipoorigem,
                    "IDDestino": sp_iddest,
                    "TipoDestino": sp_tipodest,
                    "Quantidade": quantidade,
                    "IDOperacao": sp_op,
                    "IDTipoEstoque": sp_idtipo,
                    "IDUsuario": session['user_id']
                }

                _sp_execute(text("""
                    EXEC dbo.sp_PopularMovimentacaoPecasPorPedido
                        @IDItem = :IDItem,
                        @NumeroLote = :NumeroLote,
                        @NumeroSerie = :NumeroSerie,
                        @IDOrigem = :IDOrigem,
                        @TipoOrigem= :TipoOrigem,
                        @IDDestino = :IDDestino,
                        @TipoDestino= :TipoDestino,
                        @Quantidade= :Quantidade,
                        @IDOperacao= :IDOperacao,
                        @IDTipoEstoque = :IDTipoEstoque,
                        @IDUsuario = :IDUsuario
                """), sp_params)

                if operacao == 'entrada':
                    id_serie_reg = None
                    if numero_serie:
                        id_serie_reg = db.session.query(EstoqueSerie.IDSerie).filter_by(
                            IDItem=id_item,
                            IDEstoque=estoque_id,
                            IDTipoEstoque=tipo,
                            NumeroSerie=numero_serie
                        ).scalar()
                    entrada = EntradasProdutos(
                        IDItem=id_item,
                        Quantidade=quantidade,
                        DataEntrada=datetime.now(),
                        NumeroNF=None,
                        IDEstoque=estoque_id,
                        IDTipoEstoque=tipo,
                        IDSerie=id_serie_reg,
                        BitEtiqueta=0
                    )
                    db.session.add(entrada)
             

            except Exception as e:
                flash(f"[Linha {idx}] Erro inesperado: {e}", "error")
                continue

        db.session.commit()
        flash("Operações concluídas com sucesso.", "success")
        return redirect(url_for('euro.adicionar_saldos'))

  
    pontos_euro_raw = _sp_execute(text(
        "SELECT EuroID, NomeEstoque, CodPonto, Logradouro FROM PontosEuro"
    )).fetchall()
    pontos_euro = [
        dict(IDProprietario=r.EuroID,
             NomeEstoque=r.NomeEstoque,
             CodPonto=r.CodPonto,
             Logradouro=r.Logradouro)
        for r in pontos_euro_raw
    ]

    pontos_sp_raw = _sp_execute(text(
        "SELECT SpId AS ID, CodPonto, Logradouro FROM Sp"
    )).fetchall()
    pontos_sp = [
        dict(ID=r.ID,
             CodPonto=r.CodPonto,
             Logradouro=r.Logradouro)
        for r in pontos_sp_raw
    ]

    return render_template(
        'sp/adicionar_saldos.html',
        pontos_euro=pontos_euro,
        pontos_sp=pontos_sp
    )










@euro.route('/movimentacao_produto')
@login_required
@requer_acesso_catalogo_produtos
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def movimentacao_produto():
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = request.args.get('per_page', 10, type=int) or 10
    per_page = min(max(per_page, 1), 100)
    search= request.args.get('search',   '').strip()

  
    where_clauses = ["1=1"]
    params = {}
    if search:
        where_clauses.append(
            "(CAST(P.IDItem AS VARCHAR(10)) LIKE :search OR P.NomeProduto LIKE :search)"
        )
        params["search"] = f"%{search}%"
    where_sql = "\n    AND ".join(where_clauses)

   
    count_sql = f"""
    SELECT COUNT(DISTINCT M.IDMovimentacao)
      FROM Movimentacao M
      LEFT JOIN Produto P ON P.IDItem = M.IDItem
     WHERE {where_sql}
    """
    total = _sp_execute(text(count_sql), params).scalar() or 0
    total_pages = (total + per_page - 1) // per_page

    if total_pages and page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    
    main_sql = f"""
    SELECT
        M.IDMovimentacao,
        U.NomeUsuario,
        M.Quantidade,

        CASE
          WHEN M.IDTipoEstoqueOrigem IN (7, 8) THEN
            COALESCE(PEO.Logradouro, SHO.Logradouro)
            + ' - ' + CAST(M.CodPontoOrigem AS VARCHAR(20))
          ELSE TEO.NomeEstoque
        END AS ProprietarioOrigem,
        M.CodPontoOrigem,

        TED.NomeEstoque AS ProprietarioDestino,
        M.CodPontoDestino,

        M.DataMovimentacao,
        M.NomeMovimentacao,
        P.IDItem,
        P.NomeProduto,
        P.ControlaLote
      FROM Movimentacao M

      LEFT JOIN Usuarios U
        ON U.IDUsuario = M.IDUsuario

      LEFT JOIN Produto P
        ON P.IDItem = M.IDItem

      LEFT JOIN TipoEstoque TEO
        ON TEO.IDTipoEstoque = M.IDTipoEstoqueOrigem

      LEFT JOIN EstoqueEuro EEO
        ON EEO.EuroID   = M.IDProprietarioOrigem
       AND EEO.CodPonto = M.CodPontoOrigem
      LEFT JOIN PontosEuro PEO
        ON PEO.EuroID   = EEO.EuroID
       AND PEO.CodPonto = EEO.CodPonto

      LEFT JOIN EstoqueSp ESO
        ON ESO.SpId  = M.IDProprietarioOrigem
       AND ESO.CodPonto  = M.CodPontoOrigem
      LEFT JOIN Sp SHO
        ON SHO.SpId   = ESO.SpId
       AND SHO.CodPonto  = ESO.CodPonto

      LEFT JOIN TipoEstoque TED
        ON TED.IDTipoEstoque = M.IDTipoEstoqueDestino

      LEFT JOIN EstoqueEuro EED
        ON EED.EuroID    = M.IDProprietarioDestino
       AND EED.CodPonto = M.CodPontoDestino
      LEFT JOIN PontosEuro PED
        ON PED.EuroID    = EED.EuroID
       AND PED.CodPonto = EED.CodPonto

      LEFT JOIN EstoqueSp ESD
        ON ESD.SpId   = M.IDProprietarioDestino
       AND ESD.CodPonto  = M.CodPontoDestino
      LEFT JOIN Sp SHD
        ON SHD.SpId   = ESD.SpId
       AND SHD.CodPonto  = ESD.CodPonto

     WHERE {where_sql}
     ORDER BY M.IDMovimentacao DESC
     OFFSET :offset ROWS
     FETCH NEXT :per_page ROWS ONLY
    """
    params.update(offset=offset, per_page=per_page)
    rows = _sp_execute(text(main_sql), params).fetchall()

    
    item_ids = {r.IDItem for r in rows if r.IDItem}
    lotes_por_item = defaultdict(list)
    if item_ids:
        lotes_sql = text("""
        SELECT IDLote, IDItem, IDEstoque, NumeroLote, NumerodeSerie, Quantidade, DataEntrada
          FROM EstoqueLotes
         WHERE IDItem IN :item_ids
        """).bindparams(bindparam("item_ids", expanding=True))
        for lot in _sp_execute(lotes_sql, {"item_ids": list(item_ids)}):
            lotes_por_item[lot.IDItem].append(dict(lot._mapping))

    movimentacoes = []
    for r in rows:
        d = dict(r._mapping)
        if d.get("IDItem") and d.get("ControlaLote") == 1:
            d["Lotes"] = lotes_por_item[d["IDItem"]]
        else:
            d["Lotes"] = []
        movimentacoes.append(d)

    
    image_urls = {}
    if item_ids:
        imagens = (
            db.session.query(ImagensProdutos.IDItem, ImagensProdutos.NomeArquivo)
                      .filter(
                          ImagensProdutos.IDItem.in_(item_ids),
                          ImagensProdutos.Ordem == 1
                      )
                      .all()
        )
        image_urls = {
            iid: url_for('euro.imagensprodutos', filename=fn)
            for iid, fn in imagens
            if fn
        }
  
    for iid in item_ids:
        image_urls.setdefault(iid, None)

    return render_template(
        "sp/movimentacao_produto.html",
        movimentacoes = movimentacoes,
        page= page,
        per_page= per_page,
        total_pages= total_pages,
        search = search,
        image_urls = image_urls
    )






@euro.route('/movimentacao_pecas')
@login_required
@requer_acesso_catalogo_produtos
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def movimentacao_pecas():
    """Exibe o histórico detalhado de peças, lotes e números de série."""
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = 18
    search = request.args.get('search', '').strip()

    where_clauses = ["1 = 1"]
    params = {}

    if search:
        where_clauses.append("""
            (
                CAST(MP.IDMovimentacao AS VARCHAR(30)) LIKE :search
                OR CAST(MP.IDItem AS VARCHAR(30)) LIKE :search
                OR MP.NomeProduto LIKE :search
                OR MP.NumeroLote LIKE :search
                OR MP.NumeroSerie LIKE :search
                OR U.NomeUsuario LIKE :search
            )
        """)
        params['search'] = f"%{search}%"

    where_sql = "\n        AND ".join(where_clauses)

    count_sql = f"""
        SELECT COUNT(*)
          FROM MovimentacaoPecas MP
          LEFT JOIN Usuarios U
            ON U.IDUsuario = MP.IDUsuario
         WHERE {where_sql}
    """
    total = _sp_execute(text(count_sql), params).scalar() or 0
    total_pages = (total + per_page - 1) // per_page

    if total_pages and page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    query_params = dict(params)
    query_params.update(offset=offset, per_page=per_page)

    main_sql = f"""
        SELECT
            MP.IDMovimentacao,
            MP.DataMovimentacao,
            MP.IDItem,
            MP.NomeProduto,
            MP.NumeroLote,
            MP.NumeroSerie,
            MP.TipoOrigem,
            MP.IDOrigem,
            MP.TipoDestino,
            MP.IDDestino,
            MP.Quantidade,
            MP.NomeOperacao,
            MP.IDTipoEstoque,
            COALESCE(U.NomeUsuario, MP.Usuario, '') AS Usuario
          FROM MovimentacaoPecas MP
          LEFT JOIN Usuarios U
            ON U.IDUsuario = MP.IDUsuario
         WHERE {where_sql}
         ORDER BY MP.DataMovimentacao DESC, MP.IDMovimentacao DESC
         OFFSET :offset ROWS
         FETCH NEXT :per_page ROWS ONLY
    """

    rows = _sp_execute(text(main_sql), query_params).fetchall()
    items = [dict(row._mapping) for row in rows]
    display_pages = _build_display_pages(page, total_pages)

    return render_template(
        'sp/movimentacao_pecas.html',
        items=items,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        display_pages=display_pages,
        search=search,
    )




@euro.route('/disponibilidade_produtos', methods=['GET'])
@login_required
def disponibilidade_produtos():
   
    item_id = request.args.get('item_id', None, type=int)
    page = request.args.get('page', 1, type=int)
    per_page = 10

    disponiveis = []
    total = 0

    if item_id is not None:
        query_str = """
        SELECT 
            a.IDItem,
            b.NomeProduto,
            a.CodPonto,
            a.Saldo,
            'EstoqueEuro' AS TipoEstoque
        FROM [Sp].[dbo].[EstoqueEuro] AS a
        INNER JOIN [Sp].[dbo].[Produto] AS b 
            ON b.IDItem = a.IDItem
        WHERE a.IDItem = :item_id 
          AND a.Saldo > 0

        UNION ALL

        SELECT 
            a.IDItem,
            b.NomeProduto,
            a.CodPonto,
            a.Saldo,
            'EstoqueMatriz' AS TipoEstoque
        FROM [Sp].[dbo].[EstoqueMatriz] AS a
        INNER JOIN [Sp].[dbo].[Produto] AS b 
            ON b.IDItem = a.IDItem
        WHERE a.IDItem = :item_id 
          AND a.Saldo > 0

        UNION ALL

        SELECT 
            a.IDItem,
            b.NomeProduto,
            a.CodPonto,
            a.Saldo,
            'EstoqueContainer' AS TipoEstoque
        FROM [Sp].[dbo].[EstoqueContainer] AS a
        INNER JOIN [Sp].[dbo].[Produto] AS b 
            ON b.IDItem = a.IDItem
        WHERE a.IDItem = :item_id 
          AND a.Saldo > 0

        UNION ALL

        SELECT 
            a.IDItem,
            b.NomeProduto,
            a.CodPonto,
            a.Saldo,
            'EstoqueManutencaoExterna' AS TipoEstoque
        FROM [Sp].[dbo].[EstoqueManutencaoExterna] AS a
        INNER JOIN [Sp].[dbo].[Produto] AS b 
            ON b.IDItem = a.IDItem
        WHERE a.IDItem = :item_id 
          AND a.Saldo > 0

        UNION ALL

        SELECT 
            a.IDItem,
            b.NomeProduto,
            a.CodPonto,
            a.Saldo,
            'EstoqueManutencaoInterna' AS TipoEstoque
        FROM [Sp].[dbo].[EstoqueManutencaoInterna] AS a
        INNER JOIN [Sp].[dbo].[Produto] AS b 
            ON b.IDItem = a.IDItem
        WHERE a.IDItem = :item_id 
          AND a.Saldo > 0
        """
        result = _sp_execute(text(query_str), {"item_id": item_id})
        disponiveis = list(result.fetchall())
        total = len(disponiveis)

    return render_template("sp/disponibilidade_produtos.html",
                           disponiveis=disponiveis,
                           item_id=item_id,
                           page=page,
                           total_pages=(total + per_page - 1) // per_page)









@euro.route('/saldo_por_estoque', methods=['GET'])
@login_required
def saldo_por_estoque():
    item_id = request.args.get('item_id', None, type=int)
    estoque = request.args.get('estoque', None, type=str)
 
    
    produto = None
    saldo = 0
    results = [] 

    if item_id:
        produto = db.session.query(Produto).filter_by(IDItem=item_id).first()
        if produto is None:
            print(f"Produto com IDItem {item_id} não encontrado!")
        else:
            print(f"Produto encontrado: {produto.NomeProduto}")
        
        if estoque:
            est_lower = estoque.lower()
            if est_lower == 'estoqueeuro':
                
                records = db.session.query(EstoqueEuro).filter_by(IDItem=item_id).all()
                saldo = sum(r.Saldo for r in records) if records else 0
                results = records
            elif est_lower == 'estoquesp':
                records = db.session.query(EstoqueSp).filter_by(IDItem=item_id).all()
                saldo = sum(r.Saldo for r in records) if records else 0
                results = records
            elif est_lower == 'estoquematriz':
                record = db.session.query(EstoqueMatriz).filter_by(IDItem=item_id).first()
                saldo = record.Saldo if record else 0
                results = [record] if record else []
            elif est_lower == 'estoquecontainer':
                record = db.session.query(EstoqueContainer).filter_by(IDItem=item_id).first()
                saldo = record.Saldo if record else 0
                results = [record] if record else []
            elif est_lower == 'estoquemanutencaointerna':
                record = db.session.query(EstoqueManutencaoInterna).filter_by(IDItem=item_id).first()
                saldo = record.Saldo if record else 0
                results = [record] if record else []
            elif est_lower == 'estoquemanutencaexterna':
                record = db.session.query(EstoqueManutencaoExterna).filter_by(IDItem=item_id).first()
                saldo = record.Saldo if record else 0
                results = [record] if record else []

   
    pontos_euro = _sp_execute(text("SELECT EuroID, NomeEstoque, CodPonto, Logradouro FROM PontosEuro")).fetchall()
    pontos_sp = _sp_execute(text("SELECT SpId AS ID, CodPonto, Logradouro FROM Sp")).fetchall()
    
    pontos_euro = [dict(row._mapping) for row in pontos_euro]
    pontos_sp = [dict(row._mapping) for row in pontos_sp]

    return render_template("sp/saldo_por_estoque.html",
                           item_id=item_id,
                           estoque=estoque,
                           saldo=saldo,
                           produto=produto,
                           results=results,
                           pontos_euro=pontos_euro,
                           pontos_sp=pontos_sp)





from flask import Response


@euro.route('/estoquematriz')
@login_required
@requer_acesso_catalogo_produtos
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def estoquematriz():
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = 18
    search = request.args.get('search', '', type=str).strip().lower()
    fcar = request.args.get('fcar',   '', type=str).strip()
    fval = request.args.get('fval',   '', type=str).strip()
    store_type = request.args.get('store_type', 'EstoqueMatriz', type=str).strip()
    export = request.args.get('export', None)

    store_types = [
        'Todos',
        'EstoqueMatriz',
        'EstoqueEuroMatriz',
        'EstoqueContainer',
        'EstoqueManutencaoExterna',
        'EstoqueManutencaoInterna',
        'EstoqueSp'
    ]

    if store_type not in store_types:
        store_type = 'EstoqueMatriz'

    def fetch_rows(model, saldo_attr, minimo_attr):
        return (
            db.session
              .query(
                  model.IDItem.label('IDItem'),
                  Produto.ReferenciaExterna.label('ReferenciaExterna'),
                  Produto.NomeProduto.label('NomeProduto'),
                  model.CodPonto.label('CodPonto'),
                  saldo_attr.label('Saldo'),
                  minimo_attr.label('EstoqueMinimo'),
              )
              .join(Produto, Produto.IDItem == model.IDItem)
              .all()
        )

 
    rows = []
    if store_type == 'Todos':
        rows.extend(fetch_rows(EstoqueMatriz,EstoqueMatriz.Saldo,EstoqueMatriz.EstoqueMinimo))
        rows.extend(fetch_rows(EstoqueEuroMatriz,EstoqueEuroMatriz.Quantidade,  EstoqueEuroMatriz.EstoqueMinimo))
        rows.extend(fetch_rows(EstoqueContainer,EstoqueContainer.Saldo, EstoqueContainer.EstoqueMinimo))
        rows.extend(fetch_rows(EstoqueManutencaoExterna, EstoqueManutencaoExterna.Saldo,EstoqueManutencaoExterna.EstoqueMinimo))
        rows.extend(fetch_rows(EstoqueManutencaoInterna, EstoqueManutencaoInterna.Saldo,EstoqueManutencaoInterna.EstoqueMinimo))
        rows.extend(fetch_rows(EstoqueSp, EstoqueSp.Saldo, EstoqueSp.EstoqueMinimo))
    else:
        mapping = {
            'EstoqueMatriz': (EstoqueMatriz,EstoqueMatriz.Saldo,EstoqueMatriz.EstoqueMinimo),
            'EstoqueEuroMatriz':(EstoqueEuroMatriz, EstoqueEuroMatriz.Quantidade, EstoqueEuroMatriz.EstoqueMinimo),
            'EstoqueContainer':(EstoqueContainer,EstoqueContainer.Saldo,EstoqueContainer.EstoqueMinimo),
            'EstoqueManutencaoExterna': (EstoqueManutencaoExterna,EstoqueManutencaoExterna.Saldo,EstoqueManutencaoExterna.EstoqueMinimo),
            'EstoqueManutencaoInterna': (EstoqueManutencaoInterna,EstoqueManutencaoInterna.Saldo,EstoqueManutencaoInterna.EstoqueMinimo),
            'EstoqueSp': (EstoqueSp, EstoqueSp.Saldo, EstoqueSp.EstoqueMinimo),
        }
        model, saldo_attr, minimo_attr = mapping.get(
            store_type,
            (EstoqueMatriz, EstoqueMatriz.Saldo, EstoqueMatriz.EstoqueMinimo)
        )
        rows = fetch_rows(model, saldo_attr, minimo_attr)

    def matches(r):
        txt = f"{r.IDItem}{r.NomeProduto}{r.ReferenciaExterna}".lower()
        if search and search not in txt:
            return False
        if fcar and fval:
            return (
                db.session.query(Caracteristica)
                          .filter_by(IDItem=r.IDItem,
                                     Caracteristica=fcar,
                                     Valor=fval,
                                     BitFiltro=True)
                          .count() > 0
            )
        return True

    filtered = [r for r in rows if matches(r) and (r.Saldo or 0) > 0]

    if export:
        si = io.StringIO()
        writer = csv.writer(si)
        writer.writerow(['IDItem', 'ReferenciaExterna', 'NomeProduto', 'Saldo'])
        for r in filtered:
            writer.writerow([r.IDItem, r.ReferenciaExterna, r.NomeProduto, r.Saldo])
        data = si.getvalue()
        bom = '\ufeff'
        output = bom + data
        return Response(
            output,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename="{store_type}.csv"'}
        )

  
    total = len(filtered)
    total_pages = (total + per_page - 1) // per_page

    if total_pages and page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    page_items = filtered[offset: offset + per_page]


    item_ids = [r.IDItem for r in page_items]
    imagens = (
        db.session.query(ImagensProdutos.IDItem, ImagensProdutos.NomeArquivo)
                  .filter(ImagensProdutos.IDItem.in_(item_ids),
                          ImagensProdutos.Ordem == 1)
                  .all()
    )
    image_urls = {iid: url_for('euro.imagensprodutos', filename=fn) for iid, fn in imagens}
    for iid in item_ids:
        image_urls.setdefault(iid, None)

    filtro_data = (
        db.session
          .query(Caracteristica.Caracteristica, Caracteristica.Valor)
          .filter(Caracteristica.BitFiltro == True)
          .distinct()
          .all()
    )
    filter_groups = {
        car: sorted({v for c, v in filtro_data if c == car})
        for car, _ in filtro_data
    }
    display_pages = _build_display_pages(page, total_pages)

    return render_template(
        'sp/estoquematriz.html',
        items = page_items,
        page = page,
        total_pages  = total_pages,
        search= search,
        fcar = fcar,
        fval= fval,
        store_type = store_type,
        store_types = store_types,
        filter_groups = filter_groups,
        image_urls = image_urls,
        total = total,
        display_pages = display_pages,
    )




@euro.route('/visualizar_saldo_lotes', methods=['GET'])
@login_required
@requer_acesso_catalogo_produtos
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def visualizar_saldo_lotes():
    """Lista lotes, números de série e quantidades registrados nos estoques."""
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = 18
    search = request.args.get('search', '', type=str).strip()
    tipo = request.args.get('tipo', '', type=str).strip()

    tipos_rows = _sp_execute(text("""
        SELECT DISTINCT NomeEstoque
          FROM dbo.TipoEstoque
         WHERE NomeEstoque IS NOT NULL
           AND LTRIM(RTRIM(NomeEstoque)) <> ''
         ORDER BY NomeEstoque
    """)).fetchall()
    tipos_list = [row.NomeEstoque for row in tipos_rows]

    where_clauses = ["1 = 1"]
    params = {}

    if search:
        where_clauses.append("""
            (
                CAST(l.IDLote AS VARCHAR(30)) LIKE :search
                OR CAST(l.IDItem AS VARCHAR(30)) LIKE :search
                OR CAST(l.IDEstoque AS VARCHAR(30)) LIKE :search
                OR p.ReferenciaExterna LIKE :search
                OR p.NomeProduto LIKE :search
                OR l.NumeroLote LIKE :search
                OR l.NumerodeSerie LIKE :search
                OR CAST(l.CodPonto AS VARCHAR(30)) LIKE :search
            )
        """)
        params['search'] = f'%{search}%'

    if tipo:
        where_clauses.append("t.NomeEstoque = :tipo")
        params['tipo'] = tipo

    where_sql = "\n          AND ".join(where_clauses)

    count_sql = f"""
        SELECT COUNT(*)
          FROM dbo.EstoqueLotes AS l
          INNER JOIN dbo.Produto AS p
            ON p.IDItem = l.IDItem
          INNER JOIN dbo.TipoEstoque AS t
            ON t.IDTipoEstoque = l.IDTipoEstoque
         WHERE {where_sql}
    """
    total = _sp_execute(text(count_sql), params).scalar() or 0
    total_pages = (total + per_page - 1) // per_page

    if total_pages and page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    query_params = dict(params)
    query_params.update(offset=offset, per_page=per_page)

    data_sql = f"""
        SELECT
            l.IDLote,
            l.IDItem,
            p.ReferenciaExterna,
            p.NomeProduto,
            l.IDEstoque,
            l.NumeroLote,
            l.NumerodeSerie,
            l.Quantidade,
            l.DataEntrada,
            t.NomeEstoque,
            l.CodPonto
          FROM dbo.EstoqueLotes AS l
          INNER JOIN dbo.Produto AS p
            ON p.IDItem = l.IDItem
          INNER JOIN dbo.TipoEstoque AS t
            ON t.IDTipoEstoque = l.IDTipoEstoque
         WHERE {where_sql}
         ORDER BY l.IDLote DESC
         OFFSET :offset ROWS
         FETCH NEXT :per_page ROWS ONLY
    """

    rows = [
        dict(row._mapping)
        for row in _sp_execute(text(data_sql), query_params).fetchall()
    ]
    display_pages = _build_display_pages(page, total_pages)

    return render_template(
        'sp/visualizar_saldo_lotes.html',
        rows=rows,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        display_pages=display_pages,
        search=search,
        tipo=tipo,
        tipos_list=tipos_list,
    )




@euro.route('/criar_pedido', methods=['GET', 'POST'])
def criar_pedido():
    if request.method == 'POST':
   
        referencia_pedido = request.form.get('referencia_pedido')
        op_inicial = request.form.get('op_inicial', type=int)
        id_projeto = request.form.get('id_projeto', type=int)
        id_empresa_vendedora = request.form.get('id_empresa_vendedora', type=int)
        id_empresa_compradora = request.form.get('id_empresa_compradora', type=int)

        lines = request.form.getlist('lines[]')
        if not lines:
            flash("Nenhum item informado.", "error")
            return redirect(url_for('criar_pedido'))
        try:
            lines_parsed = [json.loads(l) for l in lines]
        except Exception as e:
            flash(f"Erro ao decodificar os itens: {e}", "error")
            return redirect(url_for('criar_pedido'))

        total_valor = 0.0
        pedido_itens = []  
        erro_encontrado = False

        for idx, line in enumerate(lines_parsed):
            try:
                id_item = int(line.get('id_item', 0))
                quantidade = int(line.get('quantidade', 0))
                valor_unitario = float(line.get('valor', 0))
            except Exception as e:
                flash(f"[Linha {idx+1}] Erro na conversão de dados: {e}", "error")
                erro_encontrado = True
                continue

            if id_item <= 0 or quantidade <= 0 or valor_unitario <= 0:
                flash(f"[Linha {idx+1}] Verifique os campos do item.", "error")
                erro_encontrado = True
                continue

            produto = Produto.query.filter_by(IDItem=id_item).first()
            if not produto:
                flash(f"[Linha {idx+1}] Produto ID {id_item} não encontrado.", "error")
                erro_encontrado = True
                continue

            estoque = EstoqueMatriz.query.filter_by(IDItem=id_item).first()
            if not estoque:
                flash(f"[Linha {idx+1}] EstoqueMatriz para o produto ID {id_item} não encontrado.", "error")
                erro_encontrado = True
                continue

            estoque.Saldo -= quantidade

           
            total_item = quantidade * valor_unitario
            total_valor += total_item

           
            pedido_item = PedidoItens(
                IDItem=id_item,
                Quantidade=quantidade,
                ValorUnitario=valor_unitario
            )
            db.session.add(pedido_item)
            pedido_itens.append(pedido_item)

           
            mov = Movimentacao(
                IDUsuario=session.get('user_id', 1), 
                NomeMovimentacao="Saida-Pedido",
                IDItem=id_item,
                Quantidade=quantidade,
                IDProprietarioOrigem=getattr(estoque, "IDEstoque", 0),
                TipoEstoqueOrigem="EstoqueMatriz",
                CodPontoOrigem=0,
                IDProprietarioDestino=0,
                TipoEstoqueDestino="Pedido",
                CodPontoDestino=0,
                DataMovimentacao=datetime.now()
            )
            db.session.add(mov)

        if erro_encontrado and not pedido_itens:
            db.session.rollback()
            return redirect(url_for('criar_pedido'))

        
        pedido = Pedidos(
            ReferenciaPedido=referencia_pedido,
            OPInicial=op_inicial,
            IDProjeto=id_projeto,
            IDEmpresaVendedora=id_empresa_vendedora,
            IDEmpresaCompradora=id_empresa_compradora,
            Valor=total_valor,
            DataPedido=datetime.now()
        )
        db.session.add(pedido)
        db.session.flush()  

     
        for pi in pedido_itens:
            pi.IDPedido = pedido.IDPedido
            db.session.add(pi)

        db.session.commit()
        flash("Pedido criado com sucesso.", "success")
        return redirect(url_for('criar_pedido'))

    return render_template('criar_pedido.html')






@euro.route('/curva_giro')
@login_required
def curva_giro():
    page = request.args.get('page',1,  type=int)
    per_page = request.args.get('per_page',18, type=int)
    search= request.args.get('search', '', type=str).strip()
    sort = request.args.get('sort',None, type=str)
    order = request.args.get('order',None, type=str)


    base_q = (
        db.session.query(
            PedidoItens.IDItem,
            Produto.NomeProduto,
            Produto.ReferenciaExterna,
            Produto.PrazoMedioRecebimento.label('prazo_medio'),
            func.sum(PedidoItens.Quantidade).label('total_vendido'),
            EstoqueMatriz.Saldo.label('estoque_atual')
        )
        .join(Produto, PedidoItens.IDItem == Produto.IDItem)
        .outerjoin(EstoqueMatriz, EstoqueMatriz.IDItem == PedidoItens.IDItem)
        .group_by(
            PedidoItens.IDItem,
            Produto.NomeProduto,
            Produto.ReferenciaExterna,
            Produto.PrazoMedioRecebimento,
            EstoqueMatriz.Saldo
        )
    )
    if search:
        like = f'%{search}%'
        base_q = base_q.filter(
            or_(
                cast(PedidoItens.IDItem, String).ilike(like),
                Produto.NomeProduto.ilike(like),
                Produto.ReferenciaExterna.ilike(like),
            )
        )

   
    rows = base_q.all()


    hoje = date.today()
    dias_mes = monthrange(hoje.year, hoje.month)[1]

   
    raw = []
    for r in rows:
        sold_month = (
            db.session.query(func.sum(PedidoItens.Quantidade))
                .join(Pedidos, PedidoItens.IDPedido == Pedidos.IDPedido)
                .filter(
                    PedidoItens.IDItem == r.IDItem,
                    func.year(Pedidos.DataPedido)  == hoje.year,
                    func.month(Pedidos.DataPedido) == hoje.month
                )
                .scalar()
        ) or 0

        consumo_diario = sold_month / dias_mes if dias_mes else 0
        proj_dias = round(r.estoque_atual / consumo_diario) if consumo_diario and r.estoque_atual else None
        consumo_pct = (
            round((consumo_diario * dias_mes) / r.estoque_atual * 100, 2)
            if r.estoque_atual and consumo_diario else 0
        )
        tempo_seguro = (proj_dias - r.prazo_medio) if proj_dias is not None and r.prazo_medio is not None else None

        raw.append({
            'IDItem': r.IDItem,
            'NomeProduto':r.NomeProduto,
            'ReferenciaExterna': r.ReferenciaExterna or '-',
            'total_vendido': r.total_vendido,
            'estoque_atual': r.estoque_atual or 0,
            'consumo_pct': consumo_pct,
            'proj_dias': proj_dias,
            'tempo_seguro': tempo_seguro,
        })

   
    if sort == 'tempo_seguro':
        raw.sort(
            key=lambda x: x['tempo_seguro'] if x['tempo_seguro'] is not None else float('-inf'),
            reverse=(order == 'desc')
        )
    else:
        raw.sort(key=lambda x: x['total_vendido'], reverse=True)

 
    total= len(raw)
    total_pages  = (total + per_page - 1) // per_page
    start, end = (page - 1) * per_page, (page - 1) * per_page + per_page
    results = raw[start:end]

 
    item_ids = [item['IDItem'] for item in results]
    images = (
        db.session
          .query(ImagensProdutos.IDItem, ImagensProdutos.NomeArquivo)
          .filter(
              ImagensProdutos.IDItem.in_(item_ids),
              ImagensProdutos.Ordem == 1
          )
          .all()
    )
    image_urls = {iid: url_for('euro.imagem_produto', filename=fname) for iid, fname in images}
    for iid in item_ids:
        image_urls.setdefault(iid, None)

   
    return render_template(
        "sp/curva_giro.html",
        results= results,
        image_urls= image_urls,
        page = page,
        total_pages = total_pages,
        per_page = per_page,
        search= search,
        sort= sort,
        order = order
    )





def first_month_day(d: date) -> date:
    return d.replace(day=1)

def last_month_day(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])

def date_series(begin: date, end: date, step: str):
    out = []
    if step == "day":
        cur = begin
        while cur <= end:
            out.append(cur)
            cur += timedelta(days=1)
    else:
        cur = first_month_day(begin)
        while cur <= end:
            out.append(cur)
            y, m = cur.year + (cur.month // 12), (cur.month % 12) + 1
            cur = date(y, m, 1)
    return out


def fmt_lbl(d: date, step: str) -> str:
    return d.strftime("%d/%m") if step == "day" else d.strftime("%b/%y")






@euro.route("/curva_giro/<int:item_id>/ver_mais")
@login_required
def ver_mais_curva_produto(item_id: int):
    period = request.args.get("period", "day").lower()
    if period not in {"day", "last90", "month"}:
        period = "day"

    prod = (
        db.session
          .query(Produto.NomeProduto, Produto.ReferenciaExterna)
          .filter_by(IDItem=item_id)
          .first()
    )
    nome_prod = prod.NomeProduto if prod else "(sem nome)"
    ref_ext = prod.ReferenciaExterna if prod else "—"
    estoque_atual = (
        db.session
          .query(EstoqueMatriz.Saldo)
          .filter_by(IDItem=item_id)
          .scalar()
        or 0
    )

   
    im_objs = (
        db.session.query(ImagensProdutos)
                  .filter_by(IDItem=item_id)
                  .order_by(ImagensProdutos.Ordem)
                  .all()
    )
    images = []
    for img in im_objs:
        filename = img.NomeArquivo
        url = url_for('euro.imagensprodutos', filename=filename)
        images.append({'url': url, 'ordem': img.Ordem})


    hoje = date.today()
    if period == "day":
        d_first= first_month_day(hoje)
        d_last= last_month_day(hoje)
        step= "day"
        days_div = monthrange(hoje.year, hoje.month)[1]
    elif period == "last90":
        d_last = hoje
        d_first= hoje - timedelta(days=89)
        step = "day"
        days_div = 90
    else:
        d_last = hoje.replace(day=1)
        d_first= date(d_last.year - 1, (d_last.month % 12) + 1, 1)
        step = "month"
        days_div = 360

   
    if step == "day":
        ent_date = cast(EntradasProdutos.DataEntrada, SQLDate).label("dia")
        ent_q = (
            db.session
              .query(ent_date, func.sum(EntradasProdutos.Quantidade).label("qty"))
              .filter(
                  EntradasProdutos.IDItem == item_id,
                  EntradasProdutos.DataEntrada >= d_first,
                  EntradasProdutos.DataEntrada <  (d_last + timedelta(days=1))
              )
              .group_by(ent_date)
              .all()
        )
    else:
        y_e = func.year(EntradasProdutos.DataEntrada)
        m_e = func.month(EntradasProdutos.DataEntrada)
        ent_q = (
            db.session
              .query(y_e.label("y"), m_e.label("m"), func.sum(EntradasProdutos.Quantidade).label("qty"))
              .filter(
                  EntradasProdutos.IDItem == item_id,
                  EntradasProdutos.DataEntrada >= d_first,
                  EntradasProdutos.DataEntrada <  (d_last + timedelta(days=1))
              )
              .group_by(y_e, m_e)
              .all()
        )

    
    if step == "day":
        ven_date = cast(Pedidos.DataPedido, SQLDate).label("dia")
        ven_q = (
            db.session
              .query(ven_date, func.sum(PedidoItens.Quantidade).label("qty"))
              .select_from(Pedidos)
              .join(PedidoItens, PedidoItens.IDPedido == Pedidos.IDPedido)
              .filter(
                  PedidoItens.IDItem == item_id,
                  Pedidos.DataPedido >= d_first,
                  Pedidos.DataPedido <  (d_last + timedelta(days=1))
              )
              .group_by(ven_date)
              .all()
        )
    else:
        y_v = func.year(Pedidos.DataPedido)
        m_v = func.month(Pedidos.DataPedido)
        ven_q = (
            db.session
              .query(y_v.label("y"), m_v.label("m"), func.sum(PedidoItens.Quantidade).label("qty"))
              .select_from(Pedidos)
              .join(PedidoItens, PedidoItens.IDPedido == Pedidos.IDPedido)
              .filter(
                  PedidoItens.IDItem == item_id,
                  Pedidos.DataPedido >= d_first,
                  Pedidos.DataPedido <  (d_last + timedelta(days=1))
              )
              .group_by(y_v, m_v)
              .all()
        )

    
    if step == "day":
        ent_map = { r.dia: r.qty for r in ent_q }
        ven_map = { r.dia: r.qty for r in ven_q }
    else:
        ent_map = { date(r.y, r.m, 1): r.qty for r in ent_q }
        ven_map = { date(r.y, r.m, 1): r.qty for r in ven_q }

   
    base = date_series(d_first, d_last, step)
    base_iso = [d.isoformat() for d in base]
    entradas_data = [ ent_map.get(d, 0) for d in base ]
    vendas_data = [ ven_map.get(d, 0) for d in base ]

    
    saldo_ini   = estoque_atual - (sum(entradas_data) - sum(vendas_data))
    s = saldo_ini
    saldo_corr = []
    for e, v in zip(entradas_data, vendas_data):
        s += e - v
        saldo_corr.append(s)

    
    total_v = sum(vendas_data)
    consumo_dia  = total_v / days_div if days_div else 0
    consumo_pct = round((consumo_dia * 30 / estoque_atual) * 100, 2) if estoque_atual else 0
    proj_dias = round(estoque_atual / consumo_dia) if consumo_dia else None

  
    return render_template(
        "sp/ver_mais_curva_produto.html",
        item_id = item_id,
        nome_prod = nome_prod,
        ref_ext = ref_ext,
        period = period,
        estoque_atual = estoque_atual,
        consumo_diario = round(consumo_dia, 2),
        consumo_percent = consumo_pct,
        previsao_dias= proj_dias,
        base_iso = base_iso,
        entradas_data = entradas_data,
        vendas_data = vendas_data,
        saldo_corrente = saldo_corr,
        images = images
    )






@euro.route('/add_to_cart/<int:item_id>')
@login_required
@requer_acesso_catalogo_produtos
def add_to_cart(item_id):
    from sqlalchemy import func

   
    saldo_matriz = db.session.query(func.sum(EstoqueMatriz.Saldo)) \
        .filter(EstoqueMatriz.IDItem == item_id).scalar() or 0
    saldo_container = db.session.query(func.sum(EstoqueContainer.Saldo)) \
        .filter(EstoqueContainer.IDItem == item_id).scalar() or 0
    saldo_euro = db.session.query(func.sum(EstoqueEuro.Saldo)) \
        .filter(EstoqueEuro.IDItem == item_id).scalar() or 0
    saldo_euro_matriz = db.session.query(func.sum(EstoqueEuroMatriz.Quantidade)) \
        .filter(EstoqueEuroMatriz.IDItem == item_id).scalar() or 0
    saldo_ext_externa = db.session.query(func.sum(EstoqueManutencaoExterna.Saldo)) \
        .filter(EstoqueManutencaoExterna.IDItem == item_id).scalar() or 0
    saldo_ext_interna = db.session.query(func.sum(EstoqueManutencaoInterna.Saldo)) \
        .filter(EstoqueManutencaoInterna.IDItem == item_id).scalar() or 0
    saldo_sp = db.session.query(func.sum(EstoqueSp.Saldo)) \
        .filter(EstoqueSp.IDItem == item_id).scalar() or 0

    total_saldo = (
        saldo_matriz +
        saldo_container +
        saldo_euro +
        saldo_euro_matriz +
        saldo_ext_externa +
        saldo_ext_interna +
        saldo_sp
    )

    if total_saldo <= 0:
        flash("Estoque não encontrado para esse item.", "error")
        return redirect(url_for('euro.lista_produtos'))

    cart = session.get('cart', {})
    key = str(item_id)
    current_qty = cart.get(key, 0)

 
    if current_qty + 1 > total_saldo:
        flash("Estoque insuficiente.", "error")
        return redirect(url_for('euro.lista_produtos'))

    cart[key] = current_qty + 1
    session['cart'] = cart
    flash("Item adicionado ao carrinho.", "success")
    return redirect(url_for('euro.lista_produtos'))






@euro.route('/remove_from_cart/<int:item_id>', methods=['POST'])
@login_required
@requer_acesso_catalogo_produtos
def remove_from_cart(item_id):
    cart = session.get('cart', {})
    key  = str(item_id)

    if key not in cart:
        flash('Item não está no carrinho.', 'warning')
        return redirect(url_for('euro.checkout'))

    entry = cart[key]

    if isinstance(entry, int):
        cart[key] = entry - 1
        if cart[key] <= 0:
            cart.pop(key)


    elif isinstance(entry, dict):
        qty    = int(entry.get('qty', 0))
        series = entry.get('series', [])

        if series:
            series.pop() 
        elif qty > 0:
            entry['qty'] = qty - 1


        if entry.get('qty', 0) <= 0 and not entry.get('series'):
            cart.pop(key)

    else:
        flash('Formato de carrinho desconhecido.', 'error')
        return redirect(url_for('euro.checkout'))

    session['cart'] = cart
    flash('Item removido do carrinho.', 'info')
    return redirect(url_for('euro.checkout'))




@euro.route('/limpar_carrinho')
@login_required
@requer_acesso_catalogo_produtos
def limpar_carrinho():
   
    session.pop('cart', None)
    flash("Carrinho esvaziado com sucesso.", "info")
  
    return redirect(url_for('euro.checkout'))


@euro.route('/checkout/status', methods=['GET'])
@login_required
@requer_acesso_catalogo_produtos
def checkout_status():
    """Retorna a contagem usada pelo badge do checkout no cabeçalho."""
    return jsonify(ok=True, qtd=_quantidade_total_carrinho())




def expandir_composicao(cart, db):
    composition_children = set()
    pais = [int(i) for i in cart.keys()]
    comps = db.session.query(ProdutoComposicao) \
             .filter(ProdutoComposicao.IDProdutoPai.in_(pais)) \
             .all()

    comps_por_pai = defaultdict(list)
    for c in comps:
        comps_por_pai[c.IDProdutoPai].append(c)


    for pai_str, qtd in list(cart.items()):
        pai = int(pai_str)
        for comp in comps_por_pai.get(pai, []):
            filho = comp.IDItem
            cart[str(filho)] = cart.get(str(filho), 0) + comp.Quantidade * qtd
            composition_children.add(filho)

    return cart, composition_children







@euro.route('/checkout', methods=['GET', 'POST'])
@login_required
@requer_acesso_catalogo_produtos
def checkout():
    cart_raw = session.get('cart', {}) or {}
    cart, composition_children = expandir_composicao(cart_raw, db)
    session['cart'] = cart

    if not cart:
        flash("Carrinho vazio. Adicione itens primeiro.", "warning")
        return redirect(url_for('euro.lista_produtos'))

    tipo_ids = {
        'EstoqueMatriz': 1,
        'Reserva Técnico': 2,
        'EstoqueManutencaoExterna': 3,
        'Pedido Venda': 4,
        'EstoqueContainer': 6,
        'Estoquemidia': 7,
        'EstoqueSp': 8,
        'EstoqueManutencaoInterna': 9,
        'EstoqueEuroMatriz': 10,
    }

    item_ids = [int(i) for i in cart.keys()]
    produtos = db.session.query(Produto).filter(Produto.IDItem.in_(item_ids)).all()
    prod_map = {p.IDItem: p for p in produtos}

    pmv_parents = {
        int(pai_id_str)
        for pai_id_str, _ in cart.items()
        if prod_map.get(int(pai_id_str)) and prod_map[int(pai_id_str)].BitPMV
    }

    print("DEBUG [checkout] cart após expansão:", cart)
    print("DEBUG [checkout] pmv_parents:", pmv_parents)
    print("DEBUG [checkout] composition_children:", composition_children)
    print("DEBUG [checkout] prod_map keys após expansão:", prod_map.keys())

    if request.method == 'POST':
        for item_id in list(cart.keys()):
            v = request.form.get(f'quantidade_{item_id}')
            if v is not None:
                try:
                    q = int(v)
                    if q <= 0:
                        raise ValueError()
                    cart[item_id] = q
                except ValueError:
                    flash(f"Quantidade inválida para o item {item_id}.", "error")
                    return redirect(url_for('euro.checkout'))
        session['cart'] = cart

        if 'tipo_pedido' not in request.form:
            return redirect(url_for('euro.checkout'))

    allowed = [
        tipo_ids['EstoqueMatriz'],
        tipo_ids['EstoqueContainer'],
        tipo_ids['EstoqueEuroMatriz'],
        tipo_ids['EstoqueSp'],
    ]
    generic = []
    generic += db.session.query(EstoqueMatriz).filter(
        EstoqueMatriz.IDItem.in_(item_ids),
        EstoqueMatriz.IDTipoEstoque.in_(allowed)
    ).all()
    generic += db.session.query(EstoqueContainer).filter(
        EstoqueContainer.IDItem.in_(item_ids),
        EstoqueContainer.IDTipoEstoque.in_(allowed)
    ).all()
    generic += db.session.query(EstoqueSp).filter(
        EstoqueSp.IDItem.in_(item_ids),
        EstoqueSp.IDTipoEstoque.in_(allowed)
    ).all()
    euro_regs = db.session.query(EstoqueEuroMatriz).filter(
        EstoqueEuroMatriz.IDItem.in_(item_ids)
    ).all()
    for e in euro_regs:
        e.IDTipoEstoque = tipo_ids['EstoqueEuroMatriz']
        e.Saldo = e.Quantidade
        generic.append(e)

    estoques_por_item = defaultdict(list)
    for est in generic:
        if getattr(est, 'Saldo', 0) > 0:
            estoques_por_item[est.IDItem].append(est)

    tipo_est_map = {
        t.IDTipoEstoque: t.NomeEstoque
        for t in db.session.query(TipoEstoque)
                       .filter(TipoEstoque.IDTipoEstoque.in_(allowed))
                       .all()
    }

    funcionarios = db.session.query(Funcionario).filter_by(BitAtivo=True).all()
    projetos = db.session.query(Projeto).all()
    empresas = db.session.query(Empresa).filter_by(BitAtivo=True).all()

    if request.method == 'POST' and 'tipo_pedido' in request.form:
        tp = request.form['tipo_pedido']
        print("DEBUG [checkout] tipo_pedido selecionado:", tp)
        if tp == 'reserva':
            tec = request.form.get('tecnico')
            os_auvo = request.form.get('os_auvo')
            if not tec:
                flash("Para reserva, informe o Técnico.", "error")
                return redirect(url_for('euro.checkout'))
            tipo_final = "Reserva Técnico"
            ref = prj = cp = vd = None
        else:
            ref = request.form.get('referencia_pedido') or None
            prj = request.form.get('id_projeto')
            cp  = request.form.get('id_empresa_compradora')
            vd  = request.form.get('id_empresa_vendedora')
            if not (prj and cp and vd):
                flash("Para venda, informe Projeto e Empresas.", "error")
                return redirect(url_for('euro.checkout'))
            tipo_final = "Pedido Venda"
            tec = os_auvo = None

        # Lê (opcional) valor_unitario_{IDITEM} e valor_total_{IDITEM} do form.
        # Total do pedido prioriza 'valor_total'; se vazio, usa (valor_unitario × qtd).
        # Valor do pedido = soma dos valor_unitario informados (se houver ao menos um).
        def _to_float(x):
            if x is None:
                return None
            s = str(x).strip()
            if not s:
                return None
            # aceita formato "1.234,56" ou "1234.56"
            s = s.replace('.', '').replace(',', '.')
            try:
                return float(s)
            except ValueError:
                return None

        soma_valor_unitarios = 0.0
        soma_totais_itens = 0.0
        tem_algum_vunit = False
        tem_algum_vtotal = False

        for key, qty in cart.items():
            item_id = int(key)
            q = int(qty)

            vunit_raw = request.form.get(f'valor_unitario_{item_id}')
            vtot_raw  = request.form.get(f'valor_total_{item_id}')

            vunit = _to_float(vunit_raw)
            vtot  = _to_float(vtot_raw)

            if vtot is not None:
                soma_totais_itens += vtot
                tem_algum_vtotal = True
            elif vunit is not None:
                soma_totais_itens += (vunit * q)
                tem_algum_vtotal = True

            if vunit is not None:
                soma_valor_unitarios += vunit
                tem_algum_vunit = True

        pedido = Pedidos(
            DataPedido = datetime.now(),
            TipoPedido = tipo_final,
            OSAuvo = int(os_auvo) if os_auvo else None,
            IDFuncionario = int(tec)  if tec   else None,
            ReferenciaPedido = int(ref) if ref else None,
            IDProjeto = int(prj)  if prj else None,
            IDEmpresaCompradora = int(cp) if cp  else None,
            IDEmpresaVendedora  = int(vd) if vd   else None,
            IDStatusPedido = 2,

            Valor = soma_valor_unitarios if tem_algum_vunit else None,
            Total = soma_totais_itens   if tem_algum_vtotal else None
        )
        db.session.add(pedido)
        db.session.flush()
        print(f"DEBUG Pedido criado com ID: {pedido.IDPedido}")

        # ------------------------------------------------------------------
        # AJUSTE: mapa dos valores unitários enviados no formulário por item
        # ------------------------------------------------------------------
        vunit_map = {}
        for key in cart.keys():
            _id = int(key)
            _raw = request.form.get(f'valor_unitario_{_id}')
            _val = _to_float(_raw)
            vunit_map[_id] = _val if _val is not None else 0.0
        # ------------------------------------------------------------------

        for key, qty in cart.items():
            id_item  = int(key)
            quantidade = int(qty)
            produto = prod_map[id_item]
            print(f"DEBUG processando item {id_item} - {produto.NomeProduto}: qtde {quantidade}")

            sel = request.form.get(f'estoque_{id_item}')
            estoque = next((e for e in estoques_por_item[id_item] if str(e.IDEstoque) == sel), None)
            print(estoque, sel)
            if not estoque or estoque.Saldo < quantidade:
                flash(f"Estoque insuficiente para {produto.NomeProduto}.", "error")
                return redirect(url_for('euro.checkout'))

            codp = None
            if estoque.IDTipoEstoque == tipo_ids['EstoqueEuroMatriz']:
                codp = request.form.get(f'codponto_{id_item}')
                if not codp or str(estoque.CodPonto) != codp:
                    flash(f"Selecione um CodPonto válido para {produto.NomeProduto}.", "error")
                    return redirect(url_for('euro.checkout'))

            estoque.Saldo -= quantidade
            db.session.add(estoque)

            lote_input = None
            if produto.ControlaLote:
                lote_input = request.form.get(f'lote_{id_item}', '').strip() or None
                if lote_input:
                    lote_reg = db.session.query(EstoqueLotes).filter_by(
                        IDItem  = id_item,
                        IDEstoque = estoque.IDEstoque,
                        IDTipoEstoque = estoque.IDTipoEstoque,
                        NumeroLote = lote_input
                    ).first()
                    if lote_reg:
                        lote_reg.Quantidade -= quantidade
                        db.session.add(lote_reg)

            mv = f"{'Saída Reserva' if tp=='reserva' else 'Saída Pedido'} {pedido.IDPedido}"

            series_list = []
            if produto.ControlaNumerodeSerie:

                selected = request.form.getlist(f'serie_{id_item}[]')
                if not selected:
                    flash(f"Selecione ao menos uma série para o item {id_item}.", "error")
                    return redirect(url_for('euro.checkout'))

                for num in selected:
                    serie_reg = (
                        db.session.query(EstoqueSerie)
                        .filter(
                            EstoqueSerie.IDItem == id_item,
                            EstoqueSerie.IDEstoque  == estoque.IDEstoque,
                            EstoqueSerie.IDTipoEstoque== estoque.IDTipoEstoque,
                            EstoqueSerie.DataSaida.is_(None),
                            EstoqueSerie.NumeroSerie  == num
                        )
                        .first()
                    )
                    if not serie_reg:
                        flash(f"Série {num} não encontrada para o item {id_item}.", "error")
                        return redirect(url_for('euro.checkout'))
                    series_list.append(serie_reg)
                print(f"DEBUG séries selecionadas para item {id_item}: {series_list}")

            if produto.ControlaNumerodeSerie and series_list:
                for serie_reg in series_list:
                    db.session.delete(serie_reg)
                    pitem = PedidoItens(
                        IDPedido = pedido.IDPedido,
                        IDItem = id_item,
                        Quantidade   = 1,
                        ValorUnitario = vunit_map.get(id_item, 0.0),  # <<< AJUSTE
                        NumeroLote = serie_reg.NumeroLote,
                        NumerodeSerie = serie_reg.NumeroSerie,
                        IDTipoEstoque = estoque.IDTipoEstoque,
                        IDEstoque = estoque.IDEstoque,
                        CodPonto = codp,
                        BitConjunto = 1 if id_item in composition_children else 0
                    )
                    db.session.add(pitem)
                    db.session.flush()
                    print(f"DEBUG pitem série criado: {pitem.IDPedidoIten}")

                    db.session.add(PedidoItemSerie(
                        IDPedido = pedido.IDPedido,
                        IDPedidoIten = pitem.IDPedidoIten,
                        IDItem = id_item,
                        IDSerie = serie_reg.IDSerie,
                        NumeroSerie = serie_reg.NumeroSerie,
                        NumeroLote = serie_reg.NumeroLote,
                        IDEstoque = serie_reg.IDEstoque,
                        TipoEstoque = tipo_final,
                        DataVinculo  = datetime.now(),
                        IDTipoEstoque= serie_reg.IDTipoEstoque, 
                        CodPonto = serie_reg.CodPonto
                    ))
                    if lote_input:
                        db.session.add(PedidoLotes(
                            IDPedido = pedido.IDPedido,
                            IDPedidoIten  = pitem.IDPedidoIten,
                            IDItem = id_item,
                            IDEstoque = estoque.IDEstoque,
                            NumeroLote = lote_input,
                            Quantidade = 1,
                            DataRegistro = datetime.now(),
                            IDTipoEstoque = estoque.IDTipoEstoque
                        ))
                    db.session.add(Movimentacao(
                        IDUsuario= session.get('user_id'),
                        IDItem = id_item,
                        Quantidade = 1,
                        IDProprietarioOrigem  = estoque.IDEstoque,
                        CodPontoOrigem = codp,
                        DataMovimentacao = datetime.now(),
                        NomeMovimentacao  = mv,
                        IDTipoEstoqueOrigem  = estoque.IDTipoEstoque,
                        IDTipoEstoqueDestino  = tipo_ids[tipo_final],
                        NumeroLoteOrigem  = lote_input
                    ))

            else:
                pitem = PedidoItens(
                    IDPedido = pedido.IDPedido,
                    IDItem  = id_item,
                    Quantidade = quantidade,
                    ValorUnitario = vunit_map.get(id_item, 0.0),  # <<< AJUSTE
                    NumeroLote = lote_input if produto.ControlaLote else None,
                    NumerodeSerie = None,
                    IDTipoEstoque = estoque.IDTipoEstoque,
                    IDEstoque = estoque.IDEstoque,
                    CodPonto = codp,
                    BitConjunto = 1 if id_item in composition_children else 0
                )
                db.session.add(pitem)
                db.session.flush()
                print(f"DEBUG fallback pitem criado: {pitem.IDPedidoIten}")

                if lote_input:
                    db.session.add(PedidoLotes(
                        IDPedido= pedido.IDPedido,
                        IDPedidoIten  = pitem.IDPedidoIten,
                        IDItem= id_item,
                        IDEstoque = estoque.IDEstoque,
                        NumeroLote= lote_input,
                        Quantidade= quantidade,
                        DataRegistro = datetime.now(),
                        IDTipoEstoque = estoque.IDTipoEstoque
                    ))

                db.session.add(Movimentacao(
                    IDUsuario = session.get('user_id'),
                    IDItem = id_item,
                    Quantidade = quantidade,
                    IDProprietarioOrigem  = estoque.IDEstoque,
                    CodPontoOrigem = codp,
                    DataMovimentacao= datetime.now(),
                    NomeMovimentacao= mv,
                    IDTipoEstoqueOrigem  = estoque.IDTipoEstoque,
                    IDTipoEstoqueDestino = tipo_ids[tipo_final],
                    NumeroLoteOrigem = lote_input
                ))

        db.session.commit()
        with _sp_engine().begin() as conn:

            conn.execute(
                text("""
                    EXEC dbo.ingestao_movimentacao_pecas
                        @IDPedido  = :pedido_id,
                        @IDUsuario = :user_id
                """),
                {
                    "pedido_id": pedido.IDPedido,
                    "user_id":   session['user_id']
                }
            )

        print("DEBUG commit realizado")
        flash("Pedido realizado com sucesso!", "success")
        session.pop('cart', None)
        return redirect(url_for('euro.lista_produtos'))

    lotes_data = {}
    series_data = {}
    codpontos_data = {}
    for id_item, lista in estoques_por_item.items():
        for est in lista:
            key = f"{id_item},{est.IDEstoque}"
            lotes = db.session.query(EstoqueLotes).filter(
                and_(
                    EstoqueLotes.IDItem == id_item,
                    EstoqueLotes.IDEstoque == est.IDEstoque,
                    EstoqueLotes.IDTipoEstoque == est.IDTipoEstoque,
                    EstoqueLotes.Quantidade  > 0
                )
            ).all()

            if not lotes:
                lotes = db.session.query(EstoqueLotes).filter(
                    EstoqueLotes.IDItem     == id_item,
                    EstoqueLotes.IDEstoque  == est.IDEstoque,
                    EstoqueLotes.Quantidade > 0
                ).all()

            lotes_data[key] = [
                {'NumeroLote': l.NumeroLote, 'Quantidade': l.Quantidade}
                for l in lotes
            ]
            print(lotes)

            q = db.session.query(EstoqueSerie).filter(
                EstoqueSerie.IDItem == id_item,
                EstoqueSerie.IDEstoque == est.IDEstoque,
                EstoqueSerie.DataSaida.is_(None)
            )
            if getattr(est, 'CodPonto', None) is not None:
                q = q.filter(EstoqueSerie.CodPonto == est.CodPonto)
            raw_series = q.order_by(EstoqueSerie.DataEntrada).all()

            grouped = {}
            for s in raw_series:
                grouped.setdefault(s.NumeroLote, []).append({
                    'NumeroSerie': s.NumeroSerie,
                    'NomeProduto': prod_map[id_item].NomeProduto
                })
            series_data[key] = grouped

            if est.IDTipoEstoque == tipo_ids['EstoqueEuroMatriz']:
                codpontos_data[key] = [
                    e.CodPonto for e in euro_regs
                    if e.IDItem   == id_item and
                       e.IDEstoque == est.IDEstoque
                ]

    items = [
        {'produto': prod_map[i], 'estoques': estoques_por_item[i]}
        for i in item_ids
    ]
    prefill = session.pop('scan_series', None) or {}

    return render_template(
        'sp/checkout.html',
        items = items,
        cart = cart,
        funcionarios = funcionarios,
        projetos = projetos,
        empresas = empresas,
        tipo_est_map = tipo_est_map,
        lotes_json = lotes_data,
        series_json = series_data,
        codpontos_json  = codpontos_data,
        tipo_ids = tipo_ids,
        prefill_json = prefill
    )








@euro.route('/api/series', methods=['GET'])
@login_required
@requer_acesso_catalogo_produtos
def api_series():
    item = request.args.get('item', type=int)
    lote = request.args.get('lote', type=str)
    tipo = request.args.get('tipo', type=int)

    if item is None or not lote or tipo is None:
        return jsonify(series=[])

    regs = (
        db.session.query(EstoqueSerie.NumeroSerie)
                  .filter(
                      EstoqueSerie.IDItem == item,
                      EstoqueSerie.NumeroLote == lote,
                      EstoqueSerie.IDTipoEstoque == tipo,
                      EstoqueSerie.DataSaida.is_(None)
                  )
                  .order_by(EstoqueSerie.DataEntrada)
                  .all()
    )
    series = [r[0] for r in regs]
    return jsonify(series=series)





def get_page_links(current, total, delta=2):
    pages = []
    left, right = current - delta, current + delta
    for p in range(1, total + 1):
        if p == 1 or p == total or (left <= p <= right):
            pages.append(p)
        elif pages and pages[-1] != '...':
            pages.append('...')
    return pages






@euro.route('/ver_pedidos')
@login_required
@requer_item_menu_paineis('ver_pedidos')
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def ver_pedidos():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    search = request.args.get('search', '').strip()
    date_filter = request.args.get('date_filter', '').strip()
    status_filter = request.args.get('status_filter', '').strip()

   
    EmpresaCompr = aliased(Empresa)
    EmpresaVend = aliased(Empresa)
    StatusModel = aliased(StatusPedido)

    
    statuses = db.session.query(StatusPedido).all()

   
    base_q = (
        db.session.query(
            Pedidos,
            Funcionario.NomeFuncionario.label('NomeFuncionario'),
            Projeto.NomeProjeto.label('NomeProjeto'),
            EmpresaCompr.NomeEmpresa.label('NomeEmpresaCompradora'),
            EmpresaVend.NomeEmpresa.label('NomeEmpresaVendedora'),
            StatusModel.NomeStatusPedido.label('NomeStatusPedido'),
        )
        .outerjoin(Funcionario, Pedidos.IDFuncionario == Funcionario.IDFuncionario)
        .outerjoin(Projeto, Pedidos.IDProjeto == Projeto.IDProjeto)
        .outerjoin(EmpresaCompr, Pedidos.IDEmpresaCompradora == EmpresaCompr.EmpresaID)
        .outerjoin(EmpresaVend, Pedidos.IDEmpresaVendedora == EmpresaVend.EmpresaID)
        .outerjoin(StatusModel, Pedidos.IDStatusPedido == StatusModel.IDStatusPedido)
    )

    
    if search:
        like = f'%{search}%'
        base_q = base_q.filter(
            or_(
                cast(Pedidos.IDPedido, String).ilike(like),
                cast(Pedidos.OPInicial, String).ilike(like),
                cast(Pedidos.ReferenciaPedido, String).ilike(like),
                cast(Pedidos.OSAuvo, String).ilike(like),
                Pedidos.TipoPedido.ilike(like),
            )
        )

 
    if date_filter:
        try:
            dt = datetime.strptime(date_filter, '%Y-%m-%d').date()
            base_q = base_q.filter(func.cast(Pedidos.DataPedido, SQLDate) == dt)
        except ValueError:
            flash("Data inválida para filtro.", "error")

   
    if status_filter:
        base_q = base_q.filter(StatusModel.NomeStatusPedido == status_filter)

   
    total = base_q.order_by(None).with_entities(func.count()).scalar() or 0

    
    rows = (
        base_q
       
        .order_by(Pedidos.IDPedido.desc(), Pedidos.DataPedido.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

 
    pedidos = []
    for pedido, nome_func, nome_proj, nome_comp, nome_vend, nome_status in rows:
      
        pedido.NomeFuncionario = nome_func or '-'
        pedido.NomeProjeto = nome_proj
        pedido.NomeEmpresaCompradora = nome_comp
        pedido.NomeEmpresaVendedora = nome_vend
        pedido.NomeStatusPedido = nome_status or '—'

      
        pedido.TotalCalculado = _calcular_total_pedido(pedido.IDPedido)
        

        pedidos.append(pedido)

  
    total_pages = (total + per_page - 1) // per_page
    page_links = get_page_links(page, total_pages, delta=2)

    return render_template(
        'sp/verpedidos.html',
        pedidos=pedidos,
        page=page,
        total_pages=total_pages,
        page_links=page_links,
        search=search,
        date_filter=date_filter,
        status_filter=status_filter,
        statuses=statuses,
    )







@euro.route(
    '/pedido/<int:pedido_id>/espelho',
    endpoint='espelho_pedido',
)
@euro.route('/pedidos_detalhes/<int:pedido_id>')
@login_required
@requer_item_menu_paineis('ver_pedidos')
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def ver_pedido(pedido_id):
    """
    Exibe o pedido em modo somente leitura.

    A rota /pedidos_detalhes/<id> abre a tela "Ver Pedido".
    A rota /pedido/<id>/espelho abre o espelho imprimível do mesmo pedido.
    """
    modo_espelho = request.endpoint == 'euro.espelho_pedido'
    pedido = db.session.query(Pedidos).filter_by(IDPedido=pedido_id).first()
    if not pedido:
        flash("Pedido não encontrado.", "danger")
        return redirect(url_for('euro.ver_pedidos'))

    status = (
        db.session.query(StatusPedido)
        .filter_by(IDStatusPedido=pedido.IDStatusPedido)
        .first()
        if pedido.IDStatusPedido else None
    )
    nome_status = status.NomeStatusPedido if status else None

    observacoes = []
    obs_rows = (
        db.session.query(ObservacoesPedidos)
        .filter_by(IDPedido=pedido_id)
        .order_by(ObservacoesPedidos.DataObservacao.desc())
        .all()
    )
    usuarios_observacoes = {}
    ids_usuarios = {o.IDUsuario for o in obs_rows if o.IDUsuario}
    if ids_usuarios:
        usuarios_observacoes = {
            u.IDUsuario: u.NomeUsuario
            for u in db.session.query(Usuario).filter(Usuario.IDUsuario.in_(ids_usuarios)).all()
        }
    for observacao in obs_rows:
        observacoes.append({
            "usuario": usuarios_observacoes.get(observacao.IDUsuario, "Desconhecido"),
            "data": observacao.DataObservacao,
            "texto": observacao.Observacao,
        })

    projeto = db.session.query(Projeto).get(pedido.IDProjeto) if pedido.IDProjeto else None
    funcionario = db.session.query(Funcionario).get(pedido.IDFuncionario) if pedido.IDFuncionario else None
    compradora_empresa = (
        db.session.query(Empresa).filter_by(EmpresaID=pedido.IDEmpresaCompradora).first()
        if pedido.IDEmpresaCompradora else None
    )
    vendedora_empresa = (
        db.session.query(Empresa).filter_by(EmpresaID=pedido.IDEmpresaVendedora).first()
        if pedido.IDEmpresaVendedora else None
    )

    tipo_est_map = {
        tipo.IDTipoEstoque: tipo.NomeEstoque
        for tipo in db.session.query(TipoEstoque).all()
    }

    itens = (
        db.session.query(PedidoItens, Produto, PedidoLotes, PedidoItemSerie)
        .join(Produto, Produto.IDItem == PedidoItens.IDItem)
        .outerjoin(PedidoLotes, PedidoLotes.IDPedidoIten == PedidoItens.IDPedidoIten)
        .outerjoin(PedidoItemSerie, PedidoItemSerie.IDPedidoIten == PedidoItens.IDPedidoIten)
        .filter(PedidoItens.IDPedido == pedido_id)
        .order_by(PedidoItens.IDPedidoIten)
        .all()
    )

    dados_por_item = {}
    lotes_vistos = defaultdict(set)
    series_vistas = defaultdict(set)
    for pitem, produto, lote, serie in itens:
        chave = pitem.IDPedidoIten
        if chave not in dados_por_item:
            dados_por_item[chave] = {
                "pitem": pitem,
                "produto": produto,
                "lotes": [],
                "series": [],
            }

        if lote and lote.NumeroLote:
            chave_lote = (
                lote.NumeroLote,
                getattr(lote, 'Quantidade', None),
                getattr(lote, 'IDEstoque', None),
            )
            if chave_lote not in lotes_vistos[chave]:
                lotes_vistos[chave].add(chave_lote)
                dados_por_item[chave]["lotes"].append(lote)

        if serie and serie.NumeroSerie:
            chave_serie = (
                serie.NumeroSerie,
                getattr(serie, 'NumeroLote', None),
                getattr(serie, 'IDEstoque', None),
            )
            if chave_serie not in series_vistas[chave]:
                series_vistas[chave].add(chave_serie)
                dados_por_item[chave]["series"].append(serie)

    item_ids = list({dados["pitem"].IDItem for dados in dados_por_item.values()})
    image_urls = {}
    if item_ids:
        imagens = (
            db.session.query(
                ImagensProdutos.IDItem,
                ImagensProdutos.NomeArquivo,
                ImagensProdutos.CaminhoArquivo,
            )
            .filter(
                ImagensProdutos.IDItem.in_(item_ids),
                ImagensProdutos.Ordem == 1,
            )
            .all()
        )
        for item_id, nome_arquivo, caminho_arquivo in imagens:
            arquivo = _normalizar_nome_arquivo_imagem(nome_arquivo or caminho_arquivo)
            if arquivo:
                image_urls[item_id] = url_for('euro.imagem_produto', filename=arquivo)
    for item_id in item_ids:
        image_urls.setdefault(item_id, None)

    anexos = (
        db.session.query(AnexosPedidos)
        .filter_by(IDPedido=pedido_id)
        .order_by(AnexosPedidos.Ordem, AnexosPedidos.DataUpload)
        .all()
    )

    return render_template(
        'sp/pedidos_detalhes.html',
        pedido=pedido,
        nome_status=nome_status,
        observacoes=observacoes,
        projeto=projeto,
        funcionario=funcionario,
        compradora_empresa=compradora_empresa,
        vendedora_empresa=vendedora_empresa,
        dados_por_item=dados_por_item,
        tipo_est_map=tipo_est_map,
        image_urls=image_urls,
        anexos=anexos,
        total_pedido=_calcular_total_pedido(pedido_id),
        is_edicao=False,
        modo_espelho=modo_espelho,
    )


_ANEXO_EXTENSOES = {
    'excel': {'xlsx', 'xlsm', 'csv'},
    'img': {'png', 'jpg', 'jpeg', 'gif', 'webp'},
    'word': {'doc', 'docx'},
    'pdf': {'pdf'},
}


def _save_anexo(file, pedido_id, user_id):
    """Salva um anexo do pedido em app/anexos e registra o arquivo no banco."""
    filename = secure_filename(file.filename or '')
    if not filename or '.' not in filename:
        return None

    extensao = filename.rsplit('.', 1)[-1].lower()
    tipo = None
    subpasta = None
    for grupo, extensoes in _ANEXO_EXTENSOES.items():
        if extensao in extensoes:
            tipo = 'imagem' if grupo == 'img' else grupo
            subpasta = grupo
            break
    if not subpasta:
        return None

    pasta = os.path.join(current_app.root_path, 'anexos', subpasta)
    os.makedirs(pasta, exist_ok=True)

    nome_salvo = f"{datetime.now():%Y%m%d%H%M%S}_{uuid4().hex[:8]}_{filename}"
    caminho_completo = os.path.join(pasta, nome_salvo)
    file.save(caminho_completo)

    ordem = (
        db.session.query(func.count(AnexosPedidos.IDAnexo))
        .filter(AnexosPedidos.IDPedido == pedido_id)
        .scalar()
        or 0
    ) + 1

    anexo = AnexosPedidos(
        NomeArquivo=filename,
        CaminhoArquivo=f"{subpasta}/{nome_salvo}",
        TipoArquivo=tipo,
        DataUpload=datetime.now(),
        IDUsuario=user_id,
        Ordem=ordem,
        IDPedido=pedido_id,
    )
    db.session.add(anexo)
    return anexo


AUVO_BASE_URL = os.getenv('AUVO_BASE_URL', 'https://api.auvo.com.br/v2').rstrip('/')
try:
    AUVO_TIMEOUT = int(os.getenv('AUVO_TIMEOUT', '150'))
except (TypeError, ValueError):
    AUVO_TIMEOUT = 150

_AUVO_TOKEN_CACHE = {'token': None, 'expires_at': 0.0}


def obter_access_token_auvo():
    """Obtém o token Auvo somente quando alguma rotina realmente precisa dele."""
    import requests

    agora = time.time()
    if _AUVO_TOKEN_CACHE['token'] and _AUVO_TOKEN_CACHE['expires_at'] > agora:
        return _AUVO_TOKEN_CACHE['token']

    api_key = os.getenv('API_KEY')
    api_token = os.getenv('API_TOKEN')
    if not api_key or not api_token:
        raise RuntimeError('API_KEY/API_TOKEN da Auvo não configurados no ambiente.')

    resposta = requests.get(
        f"{AUVO_BASE_URL}/login/",
        params={'apiKey': api_key, 'apiToken': api_token},
        timeout=AUVO_TIMEOUT,
    )
    resposta.raise_for_status()
    token = resposta.json().get('result', {}).get('accessToken')
    if not token:
        raise RuntimeError("A Auvo não retornou o campo 'accessToken'.")

    _AUVO_TOKEN_CACHE['token'] = token
    _AUVO_TOKEN_CACHE['expires_at'] = agora + (55 * 60)
    return token


@euro.route('/pedido/<int:pedido_id>/editar', methods=['GET', 'POST'])
@euro.route('/editar_pedido/<int:pedido_id>', methods=['GET', 'POST'])
@login_required
@requer_item_menu_paineis('ver_pedidos')
@retry_get_view(db, attempts=6, base_delay=0.2, max_delay=1.5)
def editar_pedido(pedido_id):

    import os, base64, mimetypes
    from uuid import uuid4
    from flask import current_app
    from sqlalchemy import func  # <-- agregado para somar quantidades

    pedido = db.session.query(Pedidos).get(pedido_id)
    if not pedido:
        flash("Pedido não encontrado.", "error")
        return redirect(url_for('euro.ver_pedidos'))

    user_id = session.get('user_id')

    def _resolver_item_pai(id_pedido: int) -> int:
        itens = (db.session.query(PedidoItens.IDItem, PedidoItens.IDPedidoIten)
                           .filter_by(IDPedido=id_pedido)
                           .order_by(PedidoItens.IDPedidoIten)
                           .all())
        lista = [iid for iid, _ in itens]
        if len(set(lista)) == 1:
            return lista[0]

        candidatos = (db.session.query(ProdutoComposicao.IDProdutoPai)
                                  .filter(ProdutoComposicao.IDProdutoPai.in_(lista))
                                  .distinct()
                                  .all())
        for (pai,) in candidatos:
            filhos_pai = [x.IDItem for x in
                          db.session.query(ProdutoComposicao.IDItem)
                                    .filter_by(IDProdutoPai=pai)]

            if all(i in filhos_pai or i == pai for i in lista):
                return pai

        return lista[0]

    def _specs_do_item(item_id: int):
        rows = (db.session.query(Caracteristica.Caracteristica,
                                 Caracteristica.Valor)
                         .filter_by(IDItem=item_id)
                         .filter(Caracteristica.Caracteristica.isnot(None),
                                 Caracteristica.Valor.isnot(None))
                         .all())
        vistos, specs = set(), []
        for c, v in rows:
            k = (c.strip().lower(), v.strip().lower())
            if k not in vistos:
                vistos.add(k)
                specs.append({"name": c, "specification": v})
        return specs

    def _imagem_base64(item_id: int):
        img = (db.session.query(ImagensProdutos)
                         .filter_by(IDItem=item_id, Ordem=1)
                         .first())
        if not img:
            return None
        arq_path = _caminho_imagem_produto(img.NomeArquivo or img.CaminhoArquivo)
        if not arq_path or not os.path.exists(arq_path):
            return None
        mime, _ = mimetypes.guess_type(arq_path)
        if not mime:
            mime = 'image/jpeg'
        with open(arq_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{b64}"

    if request.method == 'POST' and 'nova_observacao' in request.form:
        texto = request.form.get('nova_observacao', '').strip()
        if texto:
            obs = ObservacoesPedidos(
                IDPedido = pedido_id,
                Observacao= texto,
                IDUsuario = user_id,
                DataObservacao= datetime.now()
            )
            db.session.add(obs)
            db.session.commit()
            flash("Observação adicionada com sucesso.", "success")
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

    if request.method == 'POST' and 'anexo' in request.files:
        file = request.files['anexo']
        if file and file.filename:
            anexo = _save_anexo(file, pedido_id, user_id)
            if anexo:
                db.session.commit()
                flash("Arquivo anexado com sucesso.", "success")
            else:
                flash("Tipo de arquivo não permitido.", "error")
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

    if request.method == 'POST' and 'status_id' in request.form:
        novo_status = int(request.form['status_id'])
        original_status  = pedido.IDStatusPedido
        pedido.IDStatusPedido = novo_status
        db.session.add(pedido)

        if original_status != 9 and novo_status == 9:

            try:
                with db.session.begin_nested():

                    item_pai_id = _resolver_item_pai(pedido_id)

                    produto_pai = db.session.query(Produto).get(item_pai_id)
                    nome_produto = produto_pai.NomeProduto if produto_pai else f"Ativo {item_pai_id}"

                    ativo = Ativo(
                        ReferenciaExterna = pedido.ReferenciaPedido,
                        Chassi = None,
                        Renavam = None,
                        IDProjeto = pedido.IDProjeto,
                        IDEmpresa= pedido.IDEmpresaCompradora,
                        NomeAtivo= nome_produto,
                        IDItem = item_pai_id,
                        PlacaAtual = None,
                        IDEmpresaProprietaria = pedido.IDEmpresaCompradora,
                        IDFabricante = pedido.IDFuncionario,
                        EnderecoAtivo = None,
                        CEP= None,
                        Cidade = None,
                        UF = None,
                        IDPedidoAtivo = pedido.IDPedido
                    )
                    db.session.add(ativo)
                    db.session.flush()

                    filhos = db.session.query(PedidoItens).filter_by(IDPedido=pedido_id).all()
                    for f in filhos:
                        db.session.add(ComposicaoAtivo(
                            IDAtivo = ativo.IDAtivo,
                            IDItem= f.IDItem,
                            Quantidade= f.Quantidade or 0,
                            NumeroLote = f.NumeroLote,
                            NumeroSerie  = f.NumerodeSerie,
                            CodPonto = f.CodPonto,
                            IDTipoEstoque = f.IDTipoEstoque,
                            IDProdutoPai = item_pai_id
                        ))

                    mov_ativo = MovimentacaoAtivo(
                        IDProjetoOrigem = None,
                        IDProjetoDestino = pedido.IDProjeto,
                        IDAtivo= ativo.IDAtivo,
                        DataMovimento = int(datetime.now().strftime('%Y%m%d')),
                        IDUsuario= user_id,
                        IDOperacaoOrigem= None,
                        IDOperacaoDestino = 6
                    )
                    db.session.add(mov_ativo)

                    db.session.add(PedidoAtivo(IDAtivo=ativo.IDAtivo, IDPedido=pedido_id))

                    empresa_local = db.session.query(Empresa).get(ativo.IDEmpresa)
                    if not empresa_local or not empresa_local.CNPJ:
                        emp_id = empresa_local.EmpresaID if empresa_local else "Desconhecido"
                        emp_nome = empresa_local.NomeEmpresa if empresa_local else "Desconhecido"
                        raise RuntimeError(
                            f"Empresa (ID: {emp_id}, Nome: {emp_nome}) sem CNPJ – integração Auvo abortada."
                        )

                    endereco_bruto = empresa_local.ENDERECO or ""
                    cidade_emp = empresa_local.CidadeEmpresa or ""
                    uf_emp = empresa_local.UF or ""
                    cep_emp= empresa_local.CEP or ""

                    endereco_envio = ""
                    if endereco_bruto:
                        endereco_envio = endereco_bruto
                        if cidade_emp and uf_emp and cep_emp:
                            endereco_envio += f" {cidade_emp} {uf_emp} {cep_emp}"

                    access_token = obter_access_token_auvo()
                    headers_auvo = {
                        "Accept": "application/json",
                        "Content-Type":  "application/json",
                        "Authorization": f"Bearer {access_token}"
                    }
                    cnpj_numeros = "".join(filter(str.isdigit, empresa_local.CNPJ))

                    cliente_auvo_reg = (
                        db.session.query(ClienteAuvo)
                            .filter_by(CNPJ=cnpj_numeros)
                            .first()
                    )
                    if cliente_auvo_reg:
                        group_id = cliente_auvo_reg.IDGrupoAuvo
                    else:
                        payload_grupo = {
                            "description": empresa_local.NomeEmpresa,
                            "clientsId":   []
                        }
                        resp_grupo = requests.post(
                            f"{AUVO_BASE_URL}/customerGroups/",
                            headers=headers_auvo,
                            json=payload_grupo,
                            timeout=AUVO_TIMEOUT
                        )
                        if resp_grupo.status_code not in (200, 201):
                            raise RuntimeError(f"Auvo criar grupo: HTTP {resp_grupo.status_code}")
                        group_id = (
                            resp_grupo
                                .json()
                                .get("result", {})
                                .get("clientGroupSearchReturn", {})
                                .get("id")
                        )
                        if not group_id:
                            raise RuntimeError("Auvo: falta 'id' no grupo.")

                        db.session.add(ClienteAuvo(
                            IDGrupoAuvo = group_id,
                            CNPJ = cnpj_numeros,
                            NomeGrupo = empresa_local.NomeEmpresa,
                            IDProjeto = ativo.IDProjeto,
                            EmpresaID = empresa_local.EmpresaID,
                            BitAtivo  = 1
                        ))

                    novo_ativo_auvo = AtivoAuvo(
                        IDAtivo = ativo.IDAtivo,
                        Endereco = endereco_bruto,
                        CEP = cep_emp,
                        Cidade = cidade_emp,
                        UF = uf_emp,
                        GroupID = group_id,
                        EmpresaID = empresa_local.EmpresaID,
                        BitAtivo = 1,
                        DataAlterado= datetime.now()
                    )
                    db.session.add(novo_ativo_auvo)
                    db.session.flush()
                    ativo.IDAtivoAuvo = novo_ativo_auvo.IDAtivoAuvo
                    db.session.add(ativo)

                    payload_cliente = {
                        "cpfCnpj":cnpj_numeros,
                        "name":empresa_local.NomeEmpresa,
                        "externalId": str(novo_ativo_auvo.IDAtivoAuvo),
                        "creationDate": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                        "groupsId": [group_id]
                    }
                    if endereco_envio:
                        payload_cliente["address"] = endereco_envio

                    resp_cliente = requests.post(
                        f"{AUVO_BASE_URL}/customers/",
                        headers=headers_auvo,
                        json=payload_cliente,
                        timeout=AUVO_TIMEOUT
                    )
                    if resp_cliente.status_code not in (200, 201):
                        raise RuntimeError(f"Auvo criar cliente: HTTP {resp_cliente.status_code}")

                    res_cli = resp_cliente.json().get("result", {})
                    customer_id = res_cli.get("id")
                    if not customer_id:
                        raise RuntimeError("Auvo: falta 'id' no cliente.")
                    novo_ativo_auvo.AuvoID = customer_id
                    novo_ativo_auvo.Latitude  = res_cli.get("latitude")
                    novo_ativo_auvo.Longitude = res_cli.get("longitude")

                    specs_pai = _specs_do_item(item_pai_id)
                    img_b64   = _imagem_base64(item_pai_id)

                    payload_equip = {
                        "name": f"{ativo.IDAtivo} - {ativo.NomeAtivo}"[:100],
                        "identifier": f"{ativo.IDAtivo}-{uuid4().hex[:6]}",
                        "associatedCustomerId": customer_id,
                        "active": True,
                        "categoryId":0
                    }
                    if img_b64:
                        payload_equip["base64Image"] = img_b64

                    resp_equip = requests.post(
                        f"{AUVO_BASE_URL}/equipments/",
                        headers=headers_auvo,
                        json=payload_equip,
                        timeout=AUVO_TIMEOUT
                    )
                    if resp_equip.status_code not in (200, 201):
                        raise RuntimeError(f"Auvo criar equipamento: HTTP {resp_equip.status_code} → {resp_equip.text}")

                    equipment_id = resp_equip.json().get("result", {}).get("id")
                    if not equipment_id:
                        raise RuntimeError("Auvo: falta 'id' no equipamento.")

                    novo_ativo_auvo.EmpresaAuvo = equipment_id
                    ativo.AuvoID               = customer_id
                    db.session.add_all([novo_ativo_auvo, ativo])

                    itens_info = (
                        db.session.query(PedidoItens, Produto)
                            .join(Produto, Produto.IDItem == PedidoItens.IDItem)
                            .filter(PedidoItens.IDPedido == pedido_id)
                            .all()
                    )
                    desc_parts = []
                    for p_it, prod in itens_info:
                        series = (
                            db.session.query(PedidoItemSerie.NumeroSerie)
                                .filter_by(IDPedidoIten=p_it.IDPedidoIten)
                                .all()
                        )
                        series_txt = ",".join(s[0] for s in series) if series else ""
                        desc_parts.append(
                            f"IDItem: {p_it.IDItem} | NomeProduto: {prod.NomeProduto} | "
                            f"Quantidade: {p_it.Quantidade or 0} | NumeroLote: {p_it.NumeroLote or ''} | "
                            f"NumeroSerie: {series_txt} | IDPedidoReferencia: {pedido_id}"
                        )

                    patch_ops = []
                    if specs_pai:
                        patch_ops.append({
                            "op":   "replace",
                            "path": "/equipmentSpecifications",
                            "value": specs_pai
                        })
                    if desc_parts:
                        patch_ops.append({
                            "op":   "replace",
                            "path": "/description",
                            "value": "\n".join(desc_parts)
                        })
                    if patch_ops:
                        resp_patch = requests.patch(
                            f"{AUVO_BASE_URL}/equipments/{equipment_id}",
                            headers=headers_auvo,
                            json=patch_ops,
                            timeout=AUVO_TIMEOUT
                        )
                        if resp_patch.status_code not in (200, 201):
                            raise RuntimeError(f"Auvo atualizar equip.: HTTP {resp_patch.status_code} → {resp_patch.text}")

                db.session.commit()
                flash("Ativo criado e integrado ao Auvo com sucesso.", "success")

            except Exception as e:
                db.session.rollback()
                flash(f"Erro – operação cancelada sem alterações. Detalhes: {e}", "error")

        if novo_status == 3:
            tipo_ids = {'EstoqueMatriz': 1,
                        'EstoqueContainer': 6,
                        'EstoqueEuroMatriz': 10}
            itens = db.session.query(PedidoItens).filter_by(IDPedido=pedido_id).all()
            for pitem in itens:
                pid = pitem.IDTipoEstoque
                est_id = pitem.IDEstoque
                cp = pitem.CodPonto
                qtd= pitem.Quantidade or 0

                if pid == tipo_ids['EstoqueMatriz']:
                    estoque_reg = db.session.query(EstoqueMatriz).get(est_id)
                elif pid == tipo_ids['EstoqueContainer']:
                    estoque_reg = db.session.query(EstoqueContainer).get(est_id)
                elif pid == tipo_ids['EstoqueEuroMatriz']:
                    estoque_reg = db.session.query(EstoqueEuroMatriz).get(est_id)
                else:
                    continue

                series_vinc = db.session.query(PedidoItemSerie).filter_by(
                    IDPedidoIten=pitem.IDPedidoIten, IDPedido=pedido_id
                ).all()
                for ps in series_vinc:
                    db.session.add(EstoqueSerie(
                        IDItem  = ps.IDItem,
                        NumeroSerie  = ps.NumeroSerie,
                        NumeroLote = ps.NumeroLote,
                        IDEstoque = est_id,
                        TipoEstoque = ps.TipoEstoque,
                        CodPonto = cp,
                        DataEntrada= datetime.now(),
                        DataSaida = None,
                        IDTipoEstoque = pid
                    ))
                db.session.query(PedidoItemSerie).filter_by(
                    IDPedidoIten=pitem.IDPedidoIten, IDPedido=pedido_id
                ).delete(synchronize_session=False)

                if pitem.NumeroLote:
                    lote_input = pitem.NumeroLote
                    lote_reg   = db.session.query(EstoqueLotes).filter_by(
                        IDItem=pitem.IDItem,
                        IDEstoque=est_id,
                        NumeroLote=lote_input,
                        IDTipoEstoque=pid
                    ).first()
                    if lote_reg:
                        lote_reg.Quantidade += qtd
                    else:
                        db.session.add(EstoqueLotes(
                            IDItem = pitem.IDItem,
                            IDEstoque = est_id,
                            NumeroLote = lote_input,
                            NumerodeSerie = None,
                            Quantidade = qtd,
                            IDTipoEstoque = pid,
                            CodPonto = cp
                        ))
                    db.session.query(PedidoLotes).filter_by(
                        IDPedidoIten=pitem.IDPedidoIten,
                        IDPedido=pedido_id
                    ).delete(synchronize_session=False)

                estoque_reg.Saldo = (estoque_reg.Saldo or 0) + qtd
                db.session.add(estoque_reg)

                db.session.add(Movimentacao(
                    IDUsuario = user_id,
                    IDItem = pitem.IDItem,
                    Quantidade = qtd,
                    IDProprietarioOrigem  = None,
                    CodPontoOrigem = None,
                    DataMovimentacao = datetime.now(),
                    NomeMovimentacao  = f"Devolução Pedido {pedido_id}",
                    IDTipoEstoqueOrigem  = pid,
                    IDTipoEstoqueDestino = pid,
                    NumeroLoteOrigem = pitem.NumeroLote
                ))

                pitem.Quantidade = 0
                pitem.NumeroLote = None
                pitem.NumerodeSerie = None
                pitem.CodPonto = None
                pitem.IDEstoque  = None
                pitem.IDTipoEstoque = None
                db.session.add(pitem)

        db.session.commit()
        flash("Situação atualizada com sucesso.", "success")
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

    status_model = db.session.query(StatusPedido).get(pedido.IDStatusPedido) if pedido.IDStatusPedido else None
    nome_status = status_model.NomeStatusPedido if status_model else None
    status_list = db.session.query(StatusPedido).order_by(StatusPedido.NomeStatusPedido).all()

    obs_entries = (
        db.session.query(ObservacoesPedidos)
            .filter_by(IDPedido=pedido_id)
            .order_by(ObservacoesPedidos.DataObservacao)
            .all()
    )
    observacoes = []
    for o in obs_entries:
        usuario = db.session.query(Usuario).get(o.IDUsuario)
        observacoes.append({
            'usuario':usuario.NomeUsuario if usuario else 'Desconhecido',
            'data':o.DataObservacao,
            'texto':o.Observacao
        })

    anexos = (
        db.session.query(AnexosPedidos)
            .filter_by(IDPedido=pedido_id)
            .order_by(AnexosPedidos.Ordem)
            .all()
    )

    funcionario = db.session.query(Funcionario).get(pedido.IDFuncionario) if pedido.IDFuncionario else None
    projeto = db.session.query(Projeto).get(pedido.IDProjeto)  if pedido.IDProjeto else None
    compradora_empresa = db.session.query(Empresa).get(pedido.IDEmpresaCompradora)
    vendedora_empresa  = db.session.query(Empresa).get(pedido.IDEmpresaVendedora)

    tipo_est_map = {t.IDTipoEstoque: t.NomeEstoque for t in db.session.query(TipoEstoque).all()}

    itens_completos = (
        db.session.query(PedidoItens, Produto, PedidoLotes, PedidoItemSerie)
            .join(Produto, Produto.IDItem == PedidoItens.IDItem)
            .outerjoin(PedidoLotes,  PedidoLotes.IDPedidoIten  == PedidoItens.IDPedidoIten)
            .outerjoin(PedidoItemSerie, PedidoItemSerie.IDPedidoIten == PedidoItens.IDPedidoIten)
            .filter(PedidoItens.IDPedido == pedido_id)
            .all()
    )
    dados_por_item = {}
    for pitem, produto, lote, serie in itens_completos:
        if pitem.IDPedidoIten not in dados_por_item:
            dados_por_item[pitem.IDPedidoIten] = {
                "pitem":  pitem,
                "produto":produto,
                "lotes":  [],
                "series": []
            }
        if lote and lote.NumeroLote:
            dados_por_item[pitem.IDPedidoIten]["lotes"].append(lote)
        if serie and serie.NumeroSerie:
            dados_por_item[pitem.IDPedidoIten]["series"].append(serie)

    item_ids = [dados["pitem"].IDItem for dados in dados_por_item.values()]
    imagens  = (
        db.session.query(ImagensProdutos.IDItem, ImagensProdutos.NomeArquivo)
            .filter(ImagensProdutos.IDItem.in_(item_ids),
                    ImagensProdutos.Ordem == 1)
            .all()
    )
    image_urls = {}
    for iid, fn in imagens:
        arquivo = _normalizar_nome_arquivo_imagem(fn)
        if arquivo:
            image_urls[iid] = url_for('euro.imagem_produto', filename=arquivo)
    for iid in item_ids:
        image_urls.setdefault(iid, None)

   
    filhos_raw = (
        db.session.query(
            Pedidos.IDPedido,
            Pedidos.IDStatusPedido,
            Pedidos.DataAgendado,
            Empresa.NomeEmpresa.label('NomeEmpresaVendedora'),
            ObservacoesPedidos.Observacao.label('Observacao'),
        )
        .join(Empresa, Empresa.EmpresaID == Pedidos.IDEmpresaVendedora)
        .outerjoin(ObservacoesPedidos, ObservacoesPedidos.IDPedido == Pedidos.IDPedido)
        .filter(Pedidos.IDPedidoPai == pedido.IDPedido)
        .order_by(Pedidos.IDPedido.desc())
        .all()
    )

    filhos_info = []
    if filhos_raw:
        status_dict = {
            s.IDStatusPedido: s.NomeStatusPedido
            for s in db.session.query(StatusPedido).all()
        }

       
        filhos_ids = []
        vistos = set()
        for pid, _, _, _, _ in filhos_raw:
            if pid not in vistos:
                vistos.add(pid)
                filhos_ids.append(pid)

       
        tooltip_rows = []
        if filhos_ids:
            tooltip_rows = (
                db.session.query(
                    PedidoItens.IDPedido.label('pid'),
                    Produto.NomeProduto.label('nome'),
                    func.coalesce(func.sum(PedidoItens.Quantidade), 0).label('qtd')
                )
                .join(Produto, Produto.IDItem == PedidoItens.IDItem)
                .filter(PedidoItens.IDPedido.in_(filhos_ids))
                .group_by(PedidoItens.IDPedido, Produto.NomeProduto)
                .order_by(PedidoItens.IDPedido)
                .all()
            )

        
        tooltip_map = {}
        for row in tooltip_rows:
            pid = row.pid
            linha = f"NomeItem: {row.nome}|Qtd: {int(row.qtd or 0)}"
            tooltip_map.setdefault(pid, []).append(linha)

        tooltip_html_map = {
            pid: "<br>".join(linhas)  
            for pid, linhas in tooltip_map.items()
        }

       
        for pid in filhos_ids:
            
            for _pid, status_id, data_ag, nome_emp_vend, obs_text in filhos_raw:
                if _pid == pid:
                    total_filho = _calcular_total_pedido(pid)
                    filhos_info.append({
                        "id": pid,
                        "status_id": status_id,
                        "status_nome": status_dict.get(status_id, "-"),
                        "data_agendado": data_ag,
                        "total": total_filho,
                        "vendedora_nome": nome_emp_vend,
                        "observacao": obs_text,
                        "tooltip_html": tooltip_html_map.get(pid, "Sem itens")  
                    })
                    break

    total_pedido = _calcular_total_pedido(pedido_id)

    return render_template(
        'sp/editar_pedido.html',
        pedido = pedido,
        nome_status = nome_status,
        status_list = status_list,
        observacoes = observacoes,
        anexos  = anexos,
        funcionario = funcionario,
        projeto = projeto,
        compradora_empresa = compradora_empresa,
        vendedora_empresa = vendedora_empresa,
        dados_por_item = dados_por_item,
        tipo_est_map = tipo_est_map,
        image_urls  = image_urls,
        is_edicao = True,
        total_pedido = total_pedido,
        filhos_info = filhos_info,
        tem_rota_desmembrar = 'euro.desmembrar_modal' in current_app.view_functions,
        tem_rota_remover_item = 'euro.remover_item_pedido' in current_app.view_functions,
    )





from requests.exceptions import ConnectionError, RequestException



@euro.route(
    '/devolver_item/<int:pedido_id>/<int:pedido_item_id>',
    methods=['POST'],
    endpoint='devolver_item'
)
@login_required
@requer_item_menu_paineis('ver_pedidos')
def devolver_item(pedido_id, pedido_item_id):
  
    pedido_item = db.session.query(PedidoItens).get(pedido_item_id)
    if not pedido_item or pedido_item.IDPedido != pedido_id:
        flash("Item não encontrado neste pedido.", "danger")
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

 
    item_id = pedido_item.IDItem
    produto = db.session.query(Produto).get(item_id)
    produto_nome = produto.NomeProduto if produto else str(item_id)
    original_lote  = pedido_item.NumeroLote or '-'
    original_serie = pedido_item.NumerodeSerie or '-'

   
    pid, est_id, cp = (pedido_item.IDTipoEstoque,
                       pedido_item.IDEstoque,
                       pedido_item.CodPonto)
    if not pid or not est_id:
        rec = (db.session.query(PedidoItemSerie).filter_by(
                   IDPedidoIten=pedido_item_id, IDPedido=pedido_id
               ).first()
               or db.session.query(PedidoLotes).filter_by(
                   IDPedidoIten=pedido_item_id, IDPedido=pedido_id
               ).first())
        if rec:
            pid, est_id, cp = rec.IDTipoEstoque, rec.IDEstoque, rec.CodPonto
    if not pid or not est_id:
        flash("Não foi possível determinar o estoque de origem para devolução.", "danger")
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

  
    estoque_map = {
        1: EstoqueMatriz,
        6: EstoqueContainer,
        7: EstoqueEuro,
        10: EstoqueEuroMatriz,
        3: EstoqueManutencaoExterna,
        9: EstoqueManutencaoInterna,
        8: EstoqueSp,
    }
    ModelEstoque = estoque_map.get(pid)
    if not ModelEstoque:
        flash("Tipo de estoque não suportado para devolução.", "danger")
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))
    estoque_reg = db.session.query(ModelEstoque).get(est_id)
    if not estoque_reg:
        flash("Registro de estoque de origem não encontrado.", "danger")
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

 
    qtd = pedido_item.Quantidade or 0

    controla_serie = getattr(produto, 'ControlaNumerodeSerie', False)
    controla_lote  = getattr(produto, 'ControlaLote', False)

    if controla_serie:
        series_vinc = db.session.query(PedidoItemSerie).filter_by(
            IDPedidoIten=pedido_item_id, IDPedido=pedido_id
        ).all()
        for ps in series_vinc:
            db.session.add(EstoqueSerie(
                IDItem = ps.IDItem,
                NumeroSerie = ps.NumeroSerie,
                NumeroLote = ps.NumeroLote,
                IDEstoque = est_id,
                TipoEstoque = ps.TipoEstoque,
                CodPonto = cp,
                DataEntrada = datetime.now(),
                DataSaida = None,
                IDTipoEstoque = pid
            ))
            if controla_lote and ps.NumeroLote:
                lote_reg = db.session.query(EstoqueLotes).filter_by(
                    IDItem = ps.IDItem,
                    IDEstoque = est_id,
                    NumeroLote = ps.NumeroLote,
                    IDTipoEstoque = pid
                ).first()
                if lote_reg:
                    lote_reg.Quantidade += 1
                else:
                    db.session.add(EstoqueLotes(
                        IDItem = ps.IDItem,
                        IDEstoque = est_id,
                        NumeroLote= ps.NumeroLote,
                        NumerodeSerie = None,
                        Quantidade = 1,
                        IDTipoEstoque = pid,
                        CodPonto  = cp
                    ))
        db.session.query(PedidoItemSerie).filter_by(
            IDPedidoIten=pedido_item_id, IDPedido=pedido_id
        ).delete(synchronize_session=False)
        db.session.query(PedidoLotes).filter_by(
            IDPedidoIten=pedido_item_id, IDPedido=pedido_id
        ).delete(synchronize_session=False)

    elif controla_lote and original_lote != '-':
        lote_reg = db.session.query(EstoqueLotes).filter_by(
            IDItem = item_id,
            IDEstoque = est_id,
            NumeroLote = original_lote,
            IDTipoEstoque = pid
        ).first()
        if lote_reg:
            lote_reg.Quantidade += qtd
        else:
            db.session.add(EstoqueLotes(
                IDItem = item_id,
                IDEstoque = est_id,
                NumeroLote = original_lote,
                NumerodeSerie = None,
                Quantidade= qtd,
                IDTipoEstoque = pid,
                CodPonto = cp
            ))
        db.session.query(PedidoLotes).filter_by(
            IDPedidoIten=pedido_item_id, IDPedido=pedido_id
        ).delete(synchronize_session=False)

 
    estoque_reg.Saldo = (estoque_reg.Saldo or 0) + qtd
    db.session.add(estoque_reg)

  
    db.session.add(Movimentacao(
        IDUsuario = session.get('user_id'),
        IDItem = item_id,
        Quantidade = qtd,
        IDProprietarioOrigem = None,
        CodPontoOrigem = None,
        DataMovimentacao = datetime.now(),
        NomeMovimentacao = f"Devolução Pedido {pedido_id}",
        IDTipoEstoqueOrigem  = pid,
        IDTipoEstoqueDestino = pid,
        NumeroLoteOrigem = original_lote
    ))


    db.session.delete(pedido_item)


    usuario = db.session.query(Usuario).get(session.get('user_id'))
    nome_usr  = usuario.NomeUsuario if usuario else 'Desconhecido'
    db.session.add(ObservacoesPedidos(
        IDPedido= pedido_id,
        Observacao = (
            f'Devolução Item "{produto_nome}" - Lote: "{original_lote}" '
            f'- Série: "{original_serie}" feita por {nome_usr} - '
            f'{datetime.now():%d/%m/%Y}'
        ),
        IDUsuario = session.get('user_id'),
        DataObservacao = datetime.now()
    ))

  
    db.session.commit()
    flash("Item devolvido e estoque restituído com sucesso.", "success")

 
    try:
        token  = obter_access_token_auvo()
        vinculo = db.session.query(PedidoAtivo).filter_by(IDPedido=pedido_id).first()
        if vinculo:
            ativo_obj = db.session.query(Ativo).get(vinculo.IDAtivo)
            auvo_id  = getattr(ativo_obj, 'AuvoID', None)
            if auvo_id:
                headers = {
                    'Accept':'application/json',
                    'Authorization': f'Bearer {token}',
                    'Content-Type':'application/json'
                }
               
                resp = requests.get(
                    f"{AUVO_BASE_URL}/equipments/{auvo_id}",
                    headers=headers,
                    timeout=AUVO_TIMEOUT
                )
                resp.raise_for_status()
                specs = resp.json().get('result', {}).get('equipmentSpecifications', [])
             
                novas = [s for s in specs if s.get('name') != produto_nome]
               
                patch_doc = [{
                    'op':'replace',
                    'path':'/equipmentSpecifications',
                    'value': novas
                }]
                resp2 = requests.patch(
                    f"{AUVO_BASE_URL}/equipments/{auvo_id}",
                    headers=headers,
                    json=patch_doc,
                    timeout=AUVO_TIMEOUT
                )
                resp2.raise_for_status()
    except ConnectionError:
        flash("Erro de conexão com Auvo: verifique DNS/URL.", "warning")
    except RequestException as e:
        flash(f"Falha na integração com Auvo: {e}", "warning")
    except Exception as e:
        flash(f"Erro inesperado na integração Auvo: {e}", "warning")

    return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))





@euro.route(
    '/movimentar_estoque_pedido/<int:pedido_id>/<int:pedido_item_id>',
    methods=['GET', 'POST'],
    endpoint='movimentar_estoque_pedido'
)
@login_required
@requer_item_menu_paineis('ver_pedidos')
def movimentar_estoque_pedido(pedido_id, pedido_item_id):
    """Transfere saldo de um item reservado pelo técnico para um estoque físico."""

    tipo_ids = {
        'EstoqueMatriz': 1,
        'Reserva Técnico': 2,
        'EstoqueManutencaoExterna': 3,
        'Pedido Venda': 4,
        'EstoqueContainer': 6,
        'Estoquemidia': 7,
        'EstoqueSp': 8,
        'EstoqueManutencaoInterna': 9,
        'EstoqueEuroMatriz': 10,
    }

    estoques_config = {
        'EstoqueMatriz': {
            'model': EstoqueMatriz,
            'saldo_attr': 'Saldo',
            'proprietario_attr': None,
        },
        'Estoquemidia': {
            'model': EstoqueEuro,
            'saldo_attr': 'Saldo',
            'proprietario_attr': 'EuroID',
        },
        'EstoqueEuroMatriz': {
            'model': EstoqueEuroMatriz,
            'saldo_attr': 'Quantidade',
            'proprietario_attr': 'EuroID',
        },
        'EstoqueSp': {
            'model': EstoqueSp,
            'saldo_attr': 'Saldo',
            'proprietario_attr': 'SpId',
        },
        'EstoqueManutencaoExterna': {
            'model': EstoqueManutencaoExterna,
            'saldo_attr': 'Saldo',
            'proprietario_attr': None,
        },
        'EstoqueContainer': {
            'model': EstoqueContainer,
            'saldo_attr': 'Saldo',
            'proprietario_attr': None,
        },
        'EstoqueManutencaoInterna': {
            'model': EstoqueManutencaoInterna,
            'saldo_attr': 'Saldo',
            'proprietario_attr': None,
        },
    }

    pedido = db.session.query(Pedidos).get(pedido_id)
    if not pedido or pedido.TipoPedido != 'Reserva Técnico':
        flash('Pedido não encontrado ou não é uma Reserva Técnico.', 'error')
        return redirect(url_for('euro.ver_pedidos'))

    pedido_item = db.session.query(PedidoItens).get(pedido_item_id)
    if not pedido_item or pedido_item.IDPedido != pedido_id:
        flash('Item não localizado neste pedido.', 'error')
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

    produto = db.session.query(Produto).get(pedido_item.IDItem)
    if not produto:
        flash('Produto vinculado ao pedido não foi encontrado.', 'error')
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

    if (pedido_item.Quantidade or 0) <= 0:
        flash('Este item não possui mais saldo reservado para movimentação.', 'warning')
        return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

    item_id = pedido_item.IDItem

    def _int_opcional(valor):
        valor = str(valor or '').strip()
        if not valor or valor.lower() in {'none', 'null'}:
            return None
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None

    def _opcao_destino(registro, nome_estoque, logradouro=''):
        config = estoques_config[nome_estoque]
        proprietario_attr = config['proprietario_attr']
        proprietario = (
            getattr(registro, proprietario_attr, None)
            if proprietario_attr else None
        )
        return {
            'IDEstoque': getattr(registro, 'IDEstoque', None),
            'IDItem': item_id,
            'CodPonto': getattr(registro, 'CodPonto', None),
            'Saldo': getattr(registro, config['saldo_attr'], 0) or 0,
            'NomeEstoque': nome_estoque,
            'IDProprietario': proprietario,
            'Logradouro': logradouro or '',
        }

    origem_groups = {
        'Reserva Técnico': [{
            'IDEstoque': pedido.IDPedido,
            'IDItem': item_id,
            'CodPonto': pedido_item.CodPonto,
            'Saldo': pedido_item.Quantidade or 0,
            'NomeEstoque': 'Reserva Técnico',
            'IDProprietario': pedido.IDPedido,
            'Logradouro': '',
        }]
    }

    registros_por_tipo = {}
    for nome_estoque, config in estoques_config.items():
        registros_por_tipo[nome_estoque] = (
            db.session.query(config['model'])
            .filter(config['model'].IDItem == item_id)
            .order_by(config['model'].IDEstoque)
            .all()
        )

    destino_groups = {}

    # Os estoques vinculados à EuroMídia precisam listar também os equipamentos
    # que ainda não possuem uma linha de saldo para este produto.
    pontos_euro = (
        db.session.query(PontosEuro)
        .order_by(PontosEuro.CodPonto, PontosEuro.EuroID)
        .all()
    )
    endereco_por_ponto = {
        (p.EuroID, p.CodPonto): (p.Logradouro or '')
        for p in pontos_euro
    }

    for nome_estoque in ('Estoquemidia', 'EstoqueEuroMatriz'):
        existentes = registros_por_tipo[nome_estoque]
        por_ponto = {
            (getattr(reg, 'EuroID', None), getattr(reg, 'CodPonto', None)): reg
            for reg in existentes
        }
        opcoes = []
        chaves_adicionadas = set()

        for ponto in pontos_euro:
            chave = (ponto.EuroID, ponto.CodPonto)
            registro = por_ponto.get(chave)
            if registro is not None:
                opcoes.append(
                    _opcao_destino(
                        registro,
                        nome_estoque,
                        endereco_por_ponto.get(chave, ''),
                    )
                )
            else:
                opcoes.append({
                    'IDEstoque': None,
                    'IDItem': item_id,
                    'CodPonto': ponto.CodPonto,
                    'Saldo': 0,
                    'NomeEstoque': nome_estoque,
                    'IDProprietario': ponto.EuroID,
                    'Logradouro': ponto.Logradouro or '',
                })
            chaves_adicionadas.add(chave)

        for registro in existentes:
            chave = (
                getattr(registro, 'EuroID', None),
                getattr(registro, 'CodPonto', None),
            )
            if chave not in chaves_adicionadas:
                opcoes.append(_opcao_destino(registro, nome_estoque))

        destino_groups[nome_estoque] = opcoes

    for nome_estoque in (
        'EstoqueMatriz',
        'EstoqueSp',
        'EstoqueManutencaoExterna',
        'EstoqueContainer',
        'EstoqueManutencaoInterna',
    ):
        opcoes = [
            _opcao_destino(registro, nome_estoque)
            for registro in registros_por_tipo[nome_estoque]
        ]
        if not opcoes:
            opcoes = [{
                'IDEstoque': None,
                'IDItem': item_id,
                'CodPonto': None,
                'Saldo': 0,
                'NomeEstoque': nome_estoque,
                'IDProprietario': None,
                'Logradouro': '',
            }]
        destino_groups[nome_estoque] = opcoes

    pedido_lotes_data = []
    if getattr(produto, 'ControlaLote', False):
        pedido_lotes = (
            db.session.query(PedidoLotes)
            .filter(
                PedidoLotes.IDPedido == pedido_id,
                PedidoLotes.IDPedidoIten == pedido_item_id,
                PedidoLotes.IDItem == item_id,
                PedidoLotes.Quantidade > 0,
            )
            .order_by(PedidoLotes.NumeroLote)
            .all()
        )
        pedido_lotes_data = [{
            'NumeroLote': lote.NumeroLote,
            'Quantidade': lote.Quantidade or 0,
            'NomeProduto': produto.NomeProduto,
        } for lote in pedido_lotes]

    pedido_series_data = []
    if getattr(produto, 'ControlaNumerodeSerie', False):
        pedido_series = (
            db.session.query(PedidoItemSerie)
            .filter(
                PedidoItemSerie.IDPedido == pedido_id,
                PedidoItemSerie.IDPedidoIten == pedido_item_id,
                PedidoItemSerie.IDItem == item_id,
            )
            .order_by(PedidoItemSerie.NumeroSerie)
            .all()
        )
        pedido_series_data = [{
            'IDSerie': serie.IDSerie,
            'NumeroSerie': serie.NumeroSerie,
            'NumeroLote': serie.NumeroLote,
        } for serie in pedido_series]

    if request.method == 'POST':
        try:
            pedido_item = (
                db.session.query(PedidoItens)
                .filter(
                    PedidoItens.IDPedidoIten == pedido_item_id,
                    PedidoItens.IDPedido == pedido_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if pedido_item is None:
                raise ValueError('O item não está mais disponível neste pedido.')

            try:
                quantidade = int(request.form.get('quantidade', '0') or 0)
            except (TypeError, ValueError):
                quantidade = 0

            saldo_reservado = int(pedido_item.Quantidade or 0)
            if quantidade <= 0:
                raise ValueError('Informe uma quantidade maior que zero.')
            if quantidade > saldo_reservado:
                raise ValueError(
                    'A quantidade informada é maior que o saldo reservado do item.'
                )

            tipo_destino = request.form.get('estoque_destino', '').strip()
            if tipo_destino not in estoques_config:
                raise ValueError('Selecione um tipo de estoque de destino válido.')

            partes_destino = request.form.get(
                'proprietario_destino', ''
            ).split('|', 2)
            if len(partes_destino) != 3:
                raise ValueError('Selecione corretamente o equipamento/estoque de destino.')

            estoque_id = _int_opcional(partes_destino[0])
            proprietario_id = _int_opcional(partes_destino[1])
            cod_ponto = _int_opcional(partes_destino[2])

            config = estoques_config[tipo_destino]
            model = config['model']
            saldo_attr = config['saldo_attr']
            proprietario_attr = config['proprietario_attr']

            query_destino = db.session.query(model).filter(model.IDItem == item_id)
            destino = None

            if estoque_id is not None:
                destino = (
                    query_destino
                    .filter(model.IDEstoque == estoque_id)
                    .with_for_update()
                    .first()
                )
                if destino is None:
                    raise ValueError('O estoque de destino selecionado não foi encontrado.')
            else:
                if proprietario_attr:
                    if proprietario_id is None:
                        raise ValueError('Selecione o proprietário/equipamento de destino.')
                    query_destino = query_destino.filter(
                        getattr(model, proprietario_attr) == proprietario_id
                    )
                if hasattr(model, 'CodPonto'):
                    query_destino = query_destino.filter(model.CodPonto == cod_ponto)
                destino = query_destino.with_for_update().first()

            if tipo_destino in ('Estoquemidia', 'EstoqueEuroMatriz'):
                ponto_valido = (
                    db.session.query(PontosEuro.EuroID)
                    .filter(
                        PontosEuro.EuroID == proprietario_id,
                        PontosEuro.CodPonto == cod_ponto,
                    )
                    .first()
                )
                if estoque_id is None and ponto_valido is None:
                    raise ValueError('O equipamento/painel selecionado não é válido.')

            if destino is None:
                campos_destino = {
                    'IDItem': item_id,
                    saldo_attr: quantidade,
                }
                if proprietario_attr:
                    campos_destino[proprietario_attr] = proprietario_id
                if hasattr(model, 'CodPonto'):
                    campos_destino['CodPonto'] = cod_ponto
                if hasattr(model, 'IDTipoEstoque'):
                    campos_destino['IDTipoEstoque'] = tipo_ids[tipo_destino]

                destino = model(**campos_destino)
                db.session.add(destino)
                db.session.flush()
            else:
                saldo_atual = getattr(destino, saldo_attr, 0) or 0
                setattr(destino, saldo_attr, saldo_atual + quantidade)
                if hasattr(destino, 'IDTipoEstoque'):
                    destino.IDTipoEstoque = tipo_ids[tipo_destino]
                db.session.add(destino)
                db.session.flush()

            lote_numero = request.form.get(
                'numero_lote_escolhido', ''
            ).strip()

            if getattr(produto, 'ControlaLote', False):
                if not lote_numero:
                    raise ValueError('Selecione o lote que será movimentado.')

                lotes_reserva = (
                    db.session.query(PedidoLotes)
                    .filter(
                        PedidoLotes.IDPedido == pedido_id,
                        PedidoLotes.IDPedidoIten == pedido_item_id,
                        PedidoLotes.IDItem == item_id,
                        PedidoLotes.NumeroLote == lote_numero,
                    )
                    .with_for_update()
                    .all()
                )
                saldo_lote = sum(int(lote.Quantidade or 0) for lote in lotes_reserva)
                if saldo_lote < quantidade:
                    raise ValueError(
                        'O lote selecionado não possui quantidade suficiente na reserva.'
                    )

                restante = quantidade
                for lote_reserva in lotes_reserva:
                    retirar = min(restante, int(lote_reserva.Quantidade or 0))
                    lote_reserva.Quantidade -= retirar
                    restante -= retirar
                    if lote_reserva.Quantidade <= 0:
                        db.session.delete(lote_reserva)
                    if restante == 0:
                        break

                lote_destino = (
                    db.session.query(EstoqueLotes)
                    .filter(
                        EstoqueLotes.IDItem == item_id,
                        EstoqueLotes.IDEstoque == destino.IDEstoque,
                        EstoqueLotes.IDTipoEstoque == tipo_ids[tipo_destino],
                        EstoqueLotes.NumeroLote == lote_numero,
                    )
                    .with_for_update()
                    .first()
                )
                if lote_destino:
                    lote_destino.Quantidade = (
                        int(lote_destino.Quantidade or 0) + quantidade
                    )
                    lote_destino.CodPonto = getattr(destino, 'CodPonto', None)
                else:
                    lote_destino = EstoqueLotes(
                        IDItem=item_id,
                        IDEstoque=destino.IDEstoque,
                        NumeroLote=lote_numero,
                        NumerodeSerie=None,
                        Quantidade=quantidade,
                        DataEntrada=datetime.now(),
                        IDTipoEstoque=tipo_ids[tipo_destino],
                        CodPonto=getattr(destino, 'CodPonto', None),
                    )
                    db.session.add(lote_destino)

            if getattr(produto, 'ControlaNumerodeSerie', False):
                series_informadas = [
                    numero.strip()
                    for numero in request.form.getlist('numero_serie_escolhido[]')
                    if numero and numero.strip()
                ]
                series_informadas = list(dict.fromkeys(series_informadas))

                if len(series_informadas) != quantidade:
                    raise ValueError(
                        'Selecione exatamente uma série para cada unidade movimentada.'
                    )

                series_reserva = (
                    db.session.query(PedidoItemSerie)
                    .filter(
                        PedidoItemSerie.IDPedido == pedido_id,
                        PedidoItemSerie.IDPedidoIten == pedido_item_id,
                        PedidoItemSerie.IDItem == item_id,
                        PedidoItemSerie.NumeroSerie.in_(series_informadas),
                    )
                    .with_for_update()
                    .all()
                )
                if len(series_reserva) != quantidade:
                    raise ValueError(
                        'Uma ou mais séries selecionadas não pertencem mais à reserva.'
                    )

                if getattr(produto, 'ControlaLote', False):
                    serie_lote_invalida = any(
                        (serie.NumeroLote or '') != lote_numero
                        for serie in series_reserva
                    )
                    if serie_lote_invalida:
                        raise ValueError(
                            'As séries selecionadas não pertencem ao lote informado.'
                        )

                for serie_reserva in series_reserva:
                    db.session.add(EstoqueSerie(
                        IDSerie=serie_reserva.IDSerie,
                        IDItem=serie_reserva.IDItem,
                        NumeroSerie=serie_reserva.NumeroSerie,
                        NumeroLote=serie_reserva.NumeroLote,
                        IDEstoque=destino.IDEstoque,
                        TipoEstoque=tipo_destino,
                        CodPonto=getattr(destino, 'CodPonto', None),
                        IDTipoEstoque=tipo_ids[tipo_destino],
                        DataEntrada=datetime.now(),
                        DataSaida=None,
                    ))
                    db.session.delete(serie_reserva)

            pedido_item.Quantidade = saldo_reservado - quantidade
            if pedido_item.Quantidade <= 0:
                pedido_item.Quantidade = 0
            db.session.add(pedido_item)

            db.session.add(Movimentacao(
                IDUsuario=session.get('user_id'),
                NomeMovimentacao=(
                    f'Transferência Reserva Técnico Pedido {pedido.IDPedido}'
                ),
                IDItem=item_id,
                Quantidade=quantidade,
                IDProprietarioOrigem=pedido.IDPedido,
                IDTipoEstoqueOrigem=tipo_ids['Reserva Técnico'],
                CodPontoOrigem=pedido_item.CodPonto,
                IDProprietarioDestino=destino.IDEstoque,
                IDTipoEstoqueDestino=tipo_ids[tipo_destino],
                CodPontoDestino=getattr(destino, 'CodPonto', None),
                NumeroLoteOrigem=lote_numero or None,
                NumeroLoteDestino=lote_numero or None,
                DataMovimentacao=datetime.now(),
            ))

            db.session.add(ObservacoesPedidos(
                IDPedido=pedido_id,
                Observacao=(
                    f'Movimentação de {quantidade} unidade(s) do item '
                    f'{item_id} para {tipo_destino}'
                    + (
                        f' / CodPonto {getattr(destino, "CodPonto", None)}'
                        if getattr(destino, 'CodPonto', None) is not None else ''
                    )
                    + (
                        f' / Lote {lote_numero}' if lote_numero else ''
                    )
                ),
                IDUsuario=session.get('user_id'),
                DataObservacao=datetime.now(),
            ))

            db.session.commit()
            flash('Estoque movimentado com sucesso.', 'success')
            return redirect(url_for('euro.editar_pedido', pedido_id=pedido_id))

        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return redirect(url_for(
                'euro.movimentar_estoque_pedido',
                pedido_id=pedido_id,
                pedido_item_id=pedido_item_id,
            ))
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                'Erro ao movimentar item %s da reserva técnica %s.',
                pedido_item_id,
                pedido_id,
            )
            flash(
                'Não foi possível concluir a movimentação. Nenhum saldo foi alterado.',
                'error',
            )
            return redirect(url_for(
                'euro.movimentar_estoque_pedido',
                pedido_id=pedido_id,
                pedido_item_id=pedido_item_id,
            ))

    return render_template(
        'sp/movimentar_estoque_pedido.html',
        item=produto,
        pedido=pedido,
        pedido_item=pedido_item,
        origem_groups=origem_groups,
        destino_groups=destino_groups,
        pedido_lotes_data=pedido_lotes_data,
        pedido_series_data=pedido_series_data,
    )




@euro.route('/pedido/<int:pedido_id>/retirada')
@euro.route('/<int:pedido_id>/retirada')
@login_required
@requer_item_menu_paineis('ver_pedidos')
def formulario_retirada(pedido_id):

  
    pedido = db.session.query(Pedidos).filter_by(IDPedido=pedido_id).first()
    if not pedido:
        flash("Pedido não encontrado.", "danger")
        return redirect(url_for('euro.ver_pedidos'))

  
    funcionario = None
    if getattr(pedido, 'IDFuncionario', None):
        funcionario = db.session.query(Funcionario).get(pedido.IDFuncionario)

  
    itens_query = (
        db.session.query(PedidoItens, Produto)
        .join(Produto, Produto.IDItem == PedidoItens.IDItem)
        .filter(PedidoItens.IDPedido == pedido_id)
        .order_by(PedidoItens.IDPedidoIten)
        .all()
    )

    pedido_item_ids = [pitem.IDPedidoIten for pitem, _ in itens_query]
    series_por_item = defaultdict(list)
    lotes_por_item = defaultdict(list)

    if pedido_item_ids:
        for serie in (
            db.session.query(PedidoItemSerie)
            .filter(PedidoItemSerie.IDPedidoIten.in_(pedido_item_ids))
            .order_by(PedidoItemSerie.IDPedidoIten, PedidoItemSerie.NumeroSerie)
            .all()
        ):
            series_por_item[serie.IDPedidoIten].append(serie)

        for lote in (
            db.session.query(PedidoLotes)
            .filter(PedidoLotes.IDPedidoIten.in_(pedido_item_ids))
            .order_by(PedidoLotes.IDPedidoIten, PedidoLotes.NumeroLote)
            .all()
        ):
            lotes_por_item[lote.IDPedidoIten].append(lote)

    dados_itens = []
    for pitem, produto in itens_query:
        series = series_por_item.get(pitem.IDPedidoIten, [])
        lotes = lotes_por_item.get(pitem.IDPedidoIten, [])

        if series:
            for serie in series:
                dados_itens.append({
                    "IDItem": pitem.IDItem,
                    "NomeProduto": produto.NomeProduto,
                    "Quantidade": 1,
                    "NumeroSerie": serie.NumeroSerie,
                    "NumeroLote": serie.NumeroLote or pitem.NumeroLote,
                })
            continue

        if lotes:
            for lote in lotes:
                dados_itens.append({
                    "IDItem": pitem.IDItem,
                    "NomeProduto": produto.NomeProduto,
                    "Quantidade": lote.Quantidade or pitem.Quantidade,
                    "NumeroSerie": pitem.NumerodeSerie,
                    "NumeroLote": lote.NumeroLote,
                })
            continue

        dados_itens.append({
            "IDItem": pitem.IDItem,
            "NomeProduto": produto.NomeProduto,
            "Quantidade": pitem.Quantidade,
            "NumeroSerie": pitem.NumerodeSerie,
            "NumeroLote": pitem.NumeroLote,
        })

   
    empresa_nome = None
    projeto_nome = None
    responsavel_nome = funcionario.NomeFuncionario if funcionario else None
    tipo_pedido = getattr(pedido, 'TipoPedido', None)

    if tipo_pedido == "Pedido Venda":
        joined = (
            db.session.query(
                Pedidos.IDPedido,
                Funcionario.NomeFuncionario.label('NomeFuncionario'),
                Empresa.NomeEmpresa.label('NomeEmpresa'),
                Projeto.NomeProjeto.label('NomeProjeto'),
            )
            .outerjoin(Empresa,   Empresa.EmpresaID == Pedidos.IDEmpresaCompradora)
            .outerjoin(Projeto,   Projeto.IDProjeto  == Pedidos.IDProjeto)
            .outerjoin(Funcionario, Funcionario.IDFuncionario == Pedidos.IDFuncionario)
            .filter(Pedidos.IDPedido == pedido_id)
            .first()
        )
        if joined:
            empresa_nome = joined.NomeEmpresa
            projeto_nome = joined.NomeProjeto
   
            responsavel_nome = joined.NomeFuncionario


    data_retirada = datetime.now()


    ultima_obs = (
    db.session.query(ObservacoesPedidos)
      .filter(ObservacoesPedidos.IDPedido == pedido_id)
      .filter(ObservacoesPedidos.Observacao.isnot(None))
      .order_by(ObservacoesPedidos.DataObservacao.desc())
      .first()
    )
    observacao_retirada = None
    if ultima_obs:
        texto = (ultima_obs.Observacao or "").strip()
        if texto:
            observacao_retirada = texto




    return render_template(
        'sp/formulario_retirada.html',
        pedido=pedido,
        funcionario=funcionario,  
        dados_itens=dados_itens,
        data_retirada=data_retirada,
        tipo_pedido=tipo_pedido,
        empresa_nome=empresa_nome,
        projeto_nome=projeto_nome,
        observacao_retirada=observacao_retirada,
        responsavel_nome=responsavel_nome 
    )
