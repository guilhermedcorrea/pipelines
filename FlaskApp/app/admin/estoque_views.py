from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from functools import wraps

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import asc, desc, exists, func, or_, select, text
from math import ceil
from flask_login import current_user
from flask_wtf.csrf import CSRFError, generate_csrf, validate_csrf

from ..extensions import csrf, db, limiter
from ..models.estoques_models import (
    DimEmpresaProprietaria,
    FatoOmieEstoque,
    FatoProduto,
    FatoProdutoEmpresa,
    FatoProdutoOmieVinculo,
    subquery_razao_social_produto,
    subquery_total_empresas_produto,
)


estoques_bp = Blueprint("estoques_bp", __name__)


CODIGOS_PERMISSAO_ESTOQUE_CONFIG = "ESTOQUES_PERMISSOES_ACESSO"
METODOS_SEGUROS_CSRF = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _eh_endpoint_api() -> bool:
    return request.path.startswith("/estoques/api/") or request.blueprint == "estoques_bp" and request.path.startswith("/estoques/api")


def _resposta_nao_autenticado():
    mensagem = "Faça login para continuar."

    if _eh_endpoint_api() or request.is_json:
        return jsonify({"ok": False, "mensagem": mensagem}), 401

    flash(mensagem, "warning")
    return redirect(url_for("Autenticacao.login", next=request.url))


def _resposta_sem_permissao():
    mensagem = "Você não tem permissão para acessar este recurso."

    if _eh_endpoint_api() or request.is_json:
        return jsonify({"ok": False, "mensagem": mensagem}), 403

    flash(mensagem, "danger")
    abort(403)


def _obter_codigos_permissao_configurados() -> tuple[str, ...]:
    codigos = current_app.config.get(CODIGOS_PERMISSAO_ESTOQUE_CONFIG, ()) or ()

    if isinstance(codigos, str):
        codigos = [codigos]

    return tuple(
        str(codigo).strip().upper()
        for codigo in codigos
        if str(codigo).strip()
    )


def proteger_rota_estoque(*codigos_permissao: str):
    codigos_fixos = tuple(
        str(codigo).strip().upper()
        for codigo in codigos_permissao
        if str(codigo).strip()
    )

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return _resposta_nao_autenticado()

            if not bool(getattr(current_user, "BitAtivo", False)):
                return _resposta_nao_autenticado()

            codigos_validacao = codigos_fixos or _obter_codigos_permissao_configurados()
            if codigos_validacao:
                funcao_permissao = getattr(current_user, "has_permission", None)
                if not callable(funcao_permissao):
                    abort(500, description="Usuário autenticado sem has_permission().")

                if not any(funcao_permissao(codigo) for codigo in codigos_validacao):
                    return _resposta_sem_permissao()

            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def _obter_token_csrf_requisicao() -> str:
    token = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
    )

    if token:
        return str(token).strip()

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        token = payload.get("csrf_token") or payload.get("csrf")
        if token:
            return str(token).strip()

    return ""


@estoques_bp.before_request
def proteger_blueprint_estoques():
    if request.method in METODOS_SEGUROS_CSRF:
        return None

    if not current_user.is_authenticated:
        return _resposta_nao_autenticado()

    if not bool(getattr(current_user, "BitAtivo", False)):
        return _resposta_nao_autenticado()

    token_csrf = _obter_token_csrf_requisicao()

    try:
        if token_csrf:
            validate_csrf(token_csrf)
        else:
            csrf.protect()
    except CSRFError as erro:
        return jsonify({"ok": False, "mensagem": erro.description or "Token CSRF inválido."}), 400
    except Exception:
        return jsonify({"ok": False, "mensagem": "Token CSRF inválido ou ausente."}), 400

    return None


@estoques_bp.app_context_processor
def injetar_csrf_para_templates_estoque():
    return {"csrf_token_estoques": generate_csrf}


@estoques_bp.get("/")
@limiter.limit("60 per minute")
@proteger_rota_estoque()
def index():
    return listar_produtos()



@estoques_bp.get("/produtos")
@limiter.limit("60 per minute")
@proteger_rota_estoque()
def listar_produtos():
    from math import ceil
    from sqlalchemy import asc, exists, func, or_, select

    pagina = max(request.args.get("pagina", default=1, type=int) or 1, 1)
    por_pagina = 10
    termo_busca = (request.args.get("busca", default="", type=str) or "").strip()
    id_empresa_proprietaria = request.args.get("id_empresa_proprietaria", type=int)

    razao_social_subquery = subquery_razao_social_produto().scalar_subquery()
    total_empresas_subquery = subquery_total_empresas_produto().scalar_subquery()

    consulta_base = select(
        FatoProduto,
        razao_social_subquery.label("razao_social"),
        total_empresas_subquery.label("total_empresas"),
    )

    if termo_busca:
        termo_like = f"%{termo_busca}%"
        consulta_base = consulta_base.where(
            or_(
                FatoProduto.referencia_produto.ilike(termo_like),
                FatoProduto.descricao.ilike(termo_like),
                FatoProduto.descricao_resumida.ilike(termo_like),
                FatoProduto.codigo_interno.ilike(termo_like),
                FatoProduto.marca.ilike(termo_like),
                FatoProduto.modelo.ilike(termo_like),
            )
        )

    if id_empresa_proprietaria:
        consulta_base = consulta_base.where(
            exists(
                select(1)
                .select_from(FatoProdutoEmpresa)
                .where(
                    FatoProdutoEmpresa.id_fato_produto == FatoProduto.id_fato_produto,
                    FatoProdutoEmpresa.id_empresa_proprietaria == id_empresa_proprietaria,
                    FatoProdutoEmpresa.bit_ativo == True,
                )
            )
        )

    consulta_total = select(func.count()).select_from(consulta_base.subquery())
    total = db.session.execute(consulta_total).scalar_one()

    total_paginas = ceil(total / por_pagina) if total > 0 else 0

    if pagina > 1 and total > 0 and pagina > total_paginas:
        abort(404)

    offset = (pagina - 1) * por_pagina

    consulta_paginada = (
        consulta_base
        .order_by(asc(FatoProduto.referencia_produto))
        .offset(offset)
        .limit(por_pagina)
    )

    linhas = db.session.execute(consulta_paginada).all()

    produtos = []
    for produto, razao_social, total_empresas in linhas:
        produto.razao_social = razao_social
        produto.nome_ambiente = razao_social
        produto.total_empresas = total_empresas or 0
        produtos.append(produto)

    empresas_proprietarias = db.session.scalars(
        select(DimEmpresaProprietaria)
        .where(DimEmpresaProprietaria.bit_ativo == True)
        .order_by(asc(DimEmpresaProprietaria.razao_social))
    ).all()

    class PaginacaoManual:
        def __init__(self, page, per_page, total):
            self.page = page
            self.per_page = per_page
            self.total = total

        @property
        def pages(self):
            if self.total == 0:
                return 0
            return ceil(self.total / self.per_page)

        @property
        def has_prev(self):
            return self.page > 1

        @property
        def has_next(self):
            return self.page < self.pages

        @property
        def prev_num(self):
            return self.page - 1

        @property
        def next_num(self):
            return self.page + 1

        def iter_pages(self, left_edge=1, left_current=1, right_current=2, right_edge=1):
            ultimo = 0
            for numero in range(1, self.pages + 1):
                if (
                    numero <= left_edge
                    or (self.page - left_current - 1 < numero < self.page + right_current)
                    or numero > self.pages - right_edge
                ):
                    if ultimo + 1 != numero:
                        yield None
                    yield numero
                    ultimo = numero

    paginacao = PaginacaoManual(
        page=pagina,
        per_page=por_pagina,
        total=total,
    )

    return render_template(
        "admin/lista_produtos.html",
        produtos=produtos,
        paginacao=paginacao,
        busca=termo_busca,
        empresas_proprietarias=empresas_proprietarias,
        id_empresa_proprietaria=id_empresa_proprietaria,
    )





@estoques_bp.get("/produtos/<int:id_fato_produto>")
@limiter.limit("60 per minute")
@proteger_rota_estoque()
def detalhar_produto(id_fato_produto: int):
    produto = db.session.get(FatoProduto, id_fato_produto)
    if not produto:
        abort(404)

    empresas_vinculadas = db.session.execute(
        select(DimEmpresaProprietaria)
        .join(
            FatoProdutoEmpresa,
            FatoProdutoEmpresa.id_empresa_proprietaria == DimEmpresaProprietaria.id_dim_empresa_proprietaria,
        )
        .where(
            FatoProdutoEmpresa.id_fato_produto == id_fato_produto,
            FatoProdutoEmpresa.bit_ativo == True,
            DimEmpresaProprietaria.bit_ativo == True,
        )
        .order_by(asc(DimEmpresaProprietaria.razao_social))
    ).scalars().all()

    vinculos = db.session.execute(
        select(FatoProdutoOmieVinculo, DimEmpresaProprietaria)
        .join(
            DimEmpresaProprietaria,
            DimEmpresaProprietaria.id_dim_empresa_proprietaria == FatoProdutoOmieVinculo.id_empresa_proprietaria,
        )
        .where(
            FatoProdutoOmieVinculo.id_fato_produto == id_fato_produto,
            FatoProdutoOmieVinculo.bit_ativo == True,
            DimEmpresaProprietaria.bit_ativo == True,
        )
        .order_by(asc(DimEmpresaProprietaria.razao_social))
    ).all()

    subquery_estoque_atual = (
        select(
            FatoOmieEstoque.id_fato_omie_estoque.label("id_fato_omie_estoque"),
            FatoOmieEstoque.id_empresa_proprietaria.label("id_empresa_proprietaria"),
            FatoOmieEstoque.codigo_produto_integracao.label("codigo_produto_integracao"),
            FatoOmieEstoque.codigo_local_estoque.label("codigo_local_estoque"),
            func.row_number().over(
                partition_by=(
                    FatoOmieEstoque.id_empresa_proprietaria,
                    func.coalesce(FatoOmieEstoque.codigo_produto_integracao, ""),
                    func.coalesce(FatoOmieEstoque.codigo_local_estoque, ""),
                ),
                order_by=(
                    desc(FatoOmieEstoque.data_posicao),
                    desc(FatoOmieEstoque.data_ultima_atualizacao_utc),
                    desc(FatoOmieEstoque.id_fato_omie_estoque),
                ),
            ).label("rn"),
        )
        .subquery("subquery_estoque_atual")
    )

    estoque_rows = db.session.execute(
        select(FatoOmieEstoque, DimEmpresaProprietaria, FatoProdutoOmieVinculo)
        .join(
            subquery_estoque_atual,
            subquery_estoque_atual.c.id_fato_omie_estoque == FatoOmieEstoque.id_fato_omie_estoque,
        )
        .join(
            FatoProdutoOmieVinculo,
            (FatoProdutoOmieVinculo.id_empresa_proprietaria == FatoOmieEstoque.id_empresa_proprietaria)
            & (
                func.coalesce(FatoProdutoOmieVinculo.codigo_produto_integracao, "")
                == func.coalesce(FatoOmieEstoque.codigo_produto_integracao, "")
            ),
        )
        .join(
            DimEmpresaProprietaria,
            DimEmpresaProprietaria.id_dim_empresa_proprietaria == FatoOmieEstoque.id_empresa_proprietaria,
        )
        .where(
            FatoProdutoOmieVinculo.id_fato_produto == id_fato_produto,
            FatoProdutoOmieVinculo.bit_ativo == True,
            DimEmpresaProprietaria.bit_ativo == True,
            subquery_estoque_atual.c.rn == 1,
        )
        .order_by(
            asc(DimEmpresaProprietaria.razao_social),
            asc(FatoOmieEstoque.codigo_local_estoque),
        )
    ).all()

    resumo = {
        "saldo_total": Decimal("0"),
        "reservado_total": Decimal("0"),
        "fisico_total": Decimal("0"),
        "pendente_total": Decimal("0"),
        "quantidade_ambientes": len({item.id_dim_empresa_proprietaria for item in empresas_vinculadas}),
        "quantidade_locais": 0,
    }

    locais = set()
    estoques_por_empresa = defaultdict(lambda: {"empresa": None, "itens": [], "vinculo": None})

    for vinculo, empresa in vinculos:
        bucket = estoques_por_empresa[empresa.id_dim_empresa_proprietaria]
        bucket["empresa"] = empresa
        bucket["vinculo"] = vinculo

    for estoque, empresa, vinculo in estoque_rows:
        bucket = estoques_por_empresa[empresa.id_dim_empresa_proprietaria]
        bucket["empresa"] = empresa
        bucket["vinculo"] = vinculo
        bucket["itens"].append(estoque)

        resumo["saldo_total"] += estoque.saldo or Decimal("0")
        resumo["reservado_total"] += estoque.reservado or Decimal("0")
        resumo["fisico_total"] += estoque.fisico or Decimal("0")
        resumo["pendente_total"] += estoque.pendente or Decimal("0")
        locais.add((empresa.id_dim_empresa_proprietaria, estoque.codigo_local_estoque or "-"))

    resumo["quantidade_locais"] = len(locais)

    return render_template(
        "admin/detalhe_produto.html",
        produto=produto,
        empresas_vinculadas=empresas_vinculadas,
        estoques_por_empresa=list(estoques_por_empresa.values()),
        resumo=resumo,
    )








@estoques_bp.get("/compras/pedido/novo")
@limiter.limit("30 per minute")
@proteger_rota_estoque()
def novo_pedido_compra():
    empresas_proprietarias = db.session.scalars(
        select(DimEmpresaProprietaria)
        .where(
            or_(
                DimEmpresaProprietaria.bit_ativo == True,
                DimEmpresaProprietaria.bit_ativo.is_(None),
            )
        )
        .order_by(asc(DimEmpresaProprietaria.razao_social))
    ).all()

    return render_template(
        "admin/pedido_compra_form.html",
        empresas_proprietarias=empresas_proprietarias,
    )






@estoques_bp.get("/compras/pedidos")
@limiter.limit("60 per minute")
@proteger_rota_estoque()
def listar_pedidos_compra():
    pagina = max(request.args.get("pagina", default=1, type=int) or 1, 1)
    por_pagina = 10

    sql_total = text(
        """
        SELECT COUNT(1) AS total
        FROM [Integracao].[Silver].[NotaFornecedorOmie] nfo
        WHERE ISNULL(nfo.[BitAtivo], 1) = 1
        """
    )

    total = db.session.execute(sql_total).scalar_one()
    total_paginas = ceil(total / por_pagina) if total > 0 else 0

    if pagina > 1 and total > 0 and pagina > total_paginas:
        abort(404)

    offset = (pagina - 1) * por_pagina

    sql_lista = text(
        """
        SELECT
            nfo.[IDNotaFornecedorOmie] AS id_nota_fornecedor_omie,
            COALESCE(NULLIF(nfo.[CodigoIntegracaoNota], ''), CONCAT('PED-', CONVERT(VARCHAR(20), nfo.[IDNotaFornecedorOmie]))) AS referencia_pedido,
            nfo.[DataCadastroUtc] AS data_criacao_utc,
            nfo.[DataPrevisao] AS data_prometida,
            nfo.[RazaoSocialFornecedor] AS razao_social_fornecedor,
            dep.[RazaoSocial] AS razao_social_empresa_proprietaria,
            nfo.[Status] AS status
        FROM [Integracao].[Silver].[NotaFornecedorOmie] nfo
        LEFT JOIN [Integracao].[dbo].[EmpresaProprietaria] dep
            ON dep.[IDEmpresaProprietaria] = nfo.[IDEmpresaProprietaria]
        WHERE ISNULL(nfo.[BitAtivo], 1) = 1
        ORDER BY
            nfo.[DataCadastroUtc] DESC,
            nfo.[IDNotaFornecedorOmie] DESC
        OFFSET :offset ROWS FETCH NEXT :por_pagina ROWS ONLY
        """
    )

    linhas = db.session.execute(
        sql_lista,
        {
            "offset": offset,
            "por_pagina": por_pagina,
        },
    ).mappings().all()

    pedidos = []
    for linha in linhas:
        registro = dict(linha)

        data_criacao = registro.get("data_criacao_utc")
        data_prometida = registro.get("data_prometida")

        registro["data_criacao_formatada"] = (
            data_criacao.strftime("%d/%m/%Y %H:%M") if data_criacao else None
        )
        registro["data_prometida_formatada"] = (
            data_prometida.strftime("%d/%m/%Y") if data_prometida else None
        )

        pedidos.append(registro)

    class PaginacaoManual:
        def __init__(self, page, per_page, total):
            self.page = page
            self.per_page = per_page
            self.total = total

        @property
        def pages(self):
            if self.total == 0:
                return 0
            return ceil(self.total / self.per_page)

        @property
        def has_prev(self):
            return self.page > 1

        @property
        def has_next(self):
            return self.page < self.pages

        @property
        def prev_num(self):
            return self.page - 1

        @property
        def next_num(self):
            return self.page + 1

        def iter_pages(self, left_edge=1, left_current=1, right_current=2, right_edge=1):
            ultimo = 0
            for numero in range(1, self.pages + 1):
                if (
                    numero <= left_edge
                    or (self.page - left_current - 1 < numero < self.page + right_current)
                    or numero > self.pages - right_edge
                ):
                    if ultimo + 1 != numero:
                        yield None
                    yield numero
                    ultimo = numero

    paginacao = PaginacaoManual(
        page=pagina,
        per_page=por_pagina,
        total=total,
    )

    return render_template(
        "admin/lista_pedidos_compra.html",
        pedidos=pedidos,
        paginacao=paginacao,
    )

@estoques_bp.get("/api/compras/produtos/buscar")
@limiter.limit("120 per minute")
@proteger_rota_estoque()
def buscar_produtos_compra():
    termo = (request.args.get("termo", default="", type=str) or "").strip()
    id_empresa_proprietaria = request.args.get("id_empresa_proprietaria", type=int)

    codigo_fornecedor_omie = (request.args.get("codigo_fornecedor_omie", default="", type=str) or "").strip()
    codigo_fornecedor_integracao = (request.args.get("codigo_fornecedor_integracao", default="", type=str) or "").strip()
    cnpj_fornecedor = (request.args.get("cnpj_fornecedor", default="", type=str) or "").strip()

    cnpj_fornecedor_numeros = "".join(caractere for caractere in cnpj_fornecedor if caractere.isdigit())

    if not id_empresa_proprietaria or len(termo) < 2:
        return jsonify({"itens": []})

    termo_like = f"%{termo}%"
    termo_maiusculo = termo.upper()

    sql = text(
        """
        SELECT TOP (20)
            fpov.IDFatoProdutoOmieVinculo AS id_fato_produto_omie_vinculo,
            fpov.IDFatoProduto AS id_fato_produto,
            COALESCE(NULLIF(fp.ReferenciaProduto, ''), NULLIF(fpov.CodigoInternoOmie, ''), NULLIF(fpov.CodigoProdutoIntegracao, ''), TRY_CONVERT(VARCHAR(60), fpov.CodigoProdutoOmie)) AS referencia_produto,
            COALESCE(NULLIF(fp.Descricao, ''), NULLIF(fpov.DescricaoOmie, ''), NULLIF(fpov.CodigoOmie, ''), 'Sem descrição') AS descricao,
            COALESCE(NULLIF(fp.DescricaoResumida, ''), NULLIF(fpov.DescricaoOmie, '')) AS descricao_resumida,
            COALESCE(NULLIF(fp.CodigoProdutoIntegracao, ''), NULLIF(fpov.CodigoProdutoIntegracao, '')) AS codigo_produto_integracao,
            COALESCE(NULLIF(fp.CodigoInterno, ''), NULLIF(fpov.CodigoInternoOmie, '')) AS codigo_interno,
            COALESCE(NULLIF(fp.Unidade, ''), NULLIF(fpov.UnidadeOmie, '')) AS unidade,
            COALESCE(NULLIF(fp.Marca, ''), NULLIF(fpov.MarcaOmie, '')) AS marca,
            COALESCE(NULLIF(fp.Modelo, ''), NULLIF(fpov.ModeloOmie, '')) AS modelo,
            COALESCE(NULLIF(fp.TipoItem, ''), NULLIF(fpov.TipoItemOmie, '')) AS tipo_item,
            COALESCE(fp.BitAtivo, fpov.BitAtivo, 1) AS bit_ativo_produto,
            dep.RazaoSocial AS razao_social,
            fpov.CodigoProdutoOmie AS codigo_produto_omie,
            fpov.CodigoOmie AS codigo_omie,

            vinculo_fornecedor.IDFatoProdutoFornecedorOmieVinculo AS id_fato_produto_fornecedor_omie_vinculo,
            vinculo_fornecedor.CodigoProdutoFornecedor AS codigo_produto_fornecedor,
            vinculo_fornecedor.DescricaoProdutoFornecedor AS descricao_produto_fornecedor,
            vinculo_fornecedor.RazaoSocialFornecedor AS razao_social_fornecedor,

            CASE
                WHEN vinculo_fornecedor.IDFatoProdutoFornecedorOmieVinculo IS NULL THEN 0
                ELSE 1
            END AS tem_vinculo_fornecedor

        FROM [Integracao].[Silver].[FatoProdutoOmieVinculo] fpov
        LEFT JOIN [Integracao].[Silver].[FatoProdutos] fp
            ON fp.IDFatoProduto = fpov.IDFatoProduto
        LEFT JOIN [Integracao].[Silver].[FatoProdutoEmpresa] fpe
            ON fpe.IDFatoProduto = fpov.IDFatoProduto
           AND fpe.IDEmpresaProprietaria = fpov.IDEmpresaProprietaria
        INNER JOIN [Integracao].[dbo].[EmpresaProprietaria] dep
            ON dep.IDEmpresaProprietaria = fpov.IDEmpresaProprietaria

        OUTER APPLY (
            SELECT TOP (1)
                vf.IDFatoProdutoFornecedorOmieVinculo,
                vf.CodigoProdutoFornecedor,
                vf.DescricaoProdutoFornecedor,
                vf.RazaoSocialFornecedor,
                vf.DataUltimaAtualizacaoUtc
            FROM [Integracao].[Bronze].[FatoProdutoFornecedorOmieVinculo] vf
            WHERE vf.IDEmpresaProprietaria = fpov.IDEmpresaProprietaria
              AND (
                    (:codigo_fornecedor_omie <> '' AND TRY_CONVERT(VARCHAR(60), vf.CodigoFornecedorOmie) = :codigo_fornecedor_omie)
                 OR (:codigo_fornecedor_integracao <> '' AND ISNULL(vf.CodigoFornecedorIntegracao, '') = :codigo_fornecedor_integracao)
                 OR (:cnpj_fornecedor_numeros <> '' AND REPLACE(REPLACE(REPLACE(ISNULL(vf.CpfCnpjFornecedor, ''), '.', ''), '/', ''), '-', '') = :cnpj_fornecedor_numeros)
              )
              AND (
                    (
                        ISNULL(vf.CodigoProdutoIntegracao, '') <> ''
                        AND ISNULL(vf.CodigoProdutoIntegracao, '') = COALESCE(NULLIF(fp.CodigoProdutoIntegracao, ''), NULLIF(fpov.CodigoProdutoIntegracao, ''), '')
                    )
                 OR (
                        TRY_CONVERT(VARCHAR(60), vf.CodigoProdutoOmie) <> ''
                    AND TRY_CONVERT(VARCHAR(60), vf.CodigoProdutoOmie) = TRY_CONVERT(VARCHAR(60), fpov.CodigoProdutoOmie)
                 )
              )
            ORDER BY
                vf.DataUltimaAtualizacaoUtc DESC,
                vf.IDFatoProdutoFornecedorOmieVinculo DESC
        ) vinculo_fornecedor

        WHERE
            fpov.IDEmpresaProprietaria = :id_empresa_proprietaria
            AND ISNULL(dep.BitAtivo, 1) = 1
            AND ISNULL(fpov.BitAtivo, 1) = 1
            AND (fpe.IDFatoProdutoEmpresa IS NULL OR ISNULL(fpe.BitAtivo, 1) = 1)
            AND (
                   ISNULL(fp.ReferenciaProduto, '') LIKE :termo_like
                OR ISNULL(fp.Descricao, '') LIKE :termo_like
                OR ISNULL(fp.DescricaoResumida, '') LIKE :termo_like
                OR ISNULL(fp.CodigoInterno, '') LIKE :termo_like
                OR ISNULL(fp.CodigoProdutoIntegracao, '') LIKE :termo_like
                OR ISNULL(fp.Marca, '') LIKE :termo_like
                OR ISNULL(fp.Modelo, '') LIKE :termo_like
                OR ISNULL(fpov.DescricaoOmie, '') LIKE :termo_like
                OR ISNULL(fpov.CodigoProdutoIntegracao, '') LIKE :termo_like
                OR ISNULL(fpov.CodigoInternoOmie, '') LIKE :termo_like
                OR ISNULL(fpov.CodigoOmie, '') LIKE :termo_like
                OR TRY_CONVERT(VARCHAR(60), fpov.CodigoProdutoOmie) LIKE :termo_like
            )
        ORDER BY
            CASE
                WHEN UPPER(COALESCE(NULLIF(fp.Descricao, ''), NULLIF(fpov.DescricaoOmie, ''), '')) = :termo_maiusculo THEN 0
                ELSE 1
            END,
            CASE
                WHEN TRY_CONVERT(VARCHAR(60), fpov.CodigoProdutoOmie) = :termo_maiusculo THEN 0
                ELSE 1
            END,
            CASE
                WHEN vinculo_fornecedor.IDFatoProdutoFornecedorOmieVinculo IS NULL THEN 1
                ELSE 0
            END,
            COALESCE(NULLIF(fp.Descricao, ''), NULLIF(fpov.DescricaoOmie, ''), '') ASC,
            fpov.IDFatoProdutoOmieVinculo ASC
        """
    )

    linhas = db.session.execute(
        sql,
        {
            "id_empresa_proprietaria": id_empresa_proprietaria,
            "termo_like": termo_like,
            "termo_maiusculo": termo_maiusculo,
            "codigo_fornecedor_omie": codigo_fornecedor_omie,
            "codigo_fornecedor_integracao": codigo_fornecedor_integracao,
            "cnpj_fornecedor_numeros": cnpj_fornecedor_numeros,
        },
    ).mappings().all()

    itens = []
    vistos = set()

    for linha in linhas:
        chave = (
            linha["id_fato_produto_omie_vinculo"],
            linha["codigo_produto_fornecedor"],
        )
        if chave in vistos:
            continue
        vistos.add(chave)

        razao_social = linha["razao_social"] or ""

        itens.append(
            {
                "id_fato_produto": linha["id_fato_produto"],
                "referencia_produto": linha["referencia_produto"],
                "descricao": linha["descricao"],
                "descricao_resumida": linha["descricao_resumida"],
                "codigo_produto_integracao": linha["codigo_produto_integracao"],
                "codigo_interno": linha["codigo_interno"],
                "unidade": linha["unidade"],
                "marca": linha["marca"],
                "modelo": linha["modelo"],
                "tipo_item": linha["tipo_item"],
                "bit_ativo_produto": linha["bit_ativo_produto"],
                "id_fato_produto_omie_vinculo": linha["id_fato_produto_omie_vinculo"],
                "codigo_produto_omie": linha["codigo_produto_omie"],
                "codigo_omie": linha["codigo_omie"],
                "razao_social": razao_social,
                "razao_social_curta": razao_social.strip().split(" ")[0] if razao_social else "-",
                "tem_vinculo_fornecedor": bool(linha["tem_vinculo_fornecedor"]),
                "id_fato_produto_fornecedor_omie_vinculo": linha["id_fato_produto_fornecedor_omie_vinculo"],
                "codigo_produto_fornecedor": linha["codigo_produto_fornecedor"],
                "descricao_produto_fornecedor": linha["descricao_produto_fornecedor"],
                "razao_social_fornecedor": linha["razao_social_fornecedor"],
                "preco_referencia": 0,
            }
        )

    return jsonify({"itens": itens})


@estoques_bp.get("/api/compras/fornecedores/buscar")
@limiter.limit("120 per minute")
@proteger_rota_estoque()
def buscar_fornecedores_compra():
    termo = (request.args.get("termo", default="", type=str) or "").strip()
    id_empresa_proprietaria = request.args.get("id_empresa_proprietaria", type=int)

    if not id_empresa_proprietaria or len(termo) < 2:
        return jsonify({"itens": []})

    termo_like = f"%{termo}%"

    sql = text("""
        SELECT TOP (20)
            feo.IDFatoEmpresasOmie AS id_fato_empresas_omie,
            feo.IDEmpresaProprietaria AS id_empresa_proprietaria,
            feo.NomeAmbiente AS nome_ambiente,
            feo.CodigoClienteOmie AS codigo_cliente_omie,
            feo.CodigoClienteIntegracao AS codigo_cliente_integracao,
            feo.RazaoSocial AS razao_social,
            feo.CnpjCpf AS cnpj_cpf,
            feo.NomeFantasia AS nome_fantasia,
            feo.Contato AS contato,
            feo.Email AS email
        FROM [Integracao].[Bronze].[FatoEmpresasOmie] feo
        WHERE
            feo.IDEmpresaProprietaria = :id_empresa_proprietaria
            AND (
                feo.RazaoSocial LIKE :termo
                OR feo.NomeFantasia LIKE :termo
                OR feo.CnpjCpf LIKE :termo
                OR CAST(feo.CodigoClienteOmie AS VARCHAR(50)) LIKE :termo
                OR ISNULL(feo.CodigoClienteIntegracao, '') LIKE :termo
            )
        ORDER BY
            feo.RazaoSocial ASC,
            feo.CodigoClienteOmie ASC
    """)

    linhas = db.session.execute(
        sql,
        {
            "id_empresa_proprietaria": id_empresa_proprietaria,
            "termo": termo_like,
        },
    ).mappings().all()

    itens = []
    vistos = set()

    for linha in linhas:
        chave = (
            linha["id_fato_empresas_omie"],
            linha["codigo_cliente_omie"],
            linha["razao_social"],
        )
        if chave in vistos:
            continue
        vistos.add(chave)

        itens.append(
            {
                "id_fato_empresas_omie": linha["id_fato_empresas_omie"],
                "id_empresa_proprietaria": linha["id_empresa_proprietaria"],
                "nome_ambiente": linha["nome_ambiente"],
                "codigo_cliente_omie": linha["codigo_cliente_omie"],
                "codigo_cliente_integracao": linha["codigo_cliente_integracao"],
                "razao_social": linha["razao_social"],
                "cnpj_cpf": linha["cnpj_cpf"],
                "nome_fantasia": linha["nome_fantasia"],
                "contato": linha["contato"],
                "email": linha["email"],
            }
        )

    return jsonify({"itens": itens})

def _para_decimal(valor) -> Decimal:
    """Eu converto qualquer valor numérico para Decimal com segurança."""
    if valor is None:
        return Decimal("0")
    return Decimal(str(valor))


def _arredondar_6(valor: Decimal) -> Decimal:
    """Eu padronizo os valores de estoque com 6 casas decimais."""
    return valor.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _recalcular_custo_medio(
    fisico_atual: Decimal,
    custo_medio_atual: Decimal,
    quantidade_entrada: Decimal,
    custo_entrada: Decimal,
) -> Decimal:
    """Eu recalculo o custo médio ponderado sem sobrescrever brutalmente o custo anterior."""
    fisico_atual = _para_decimal(fisico_atual)
    custo_medio_atual = _para_decimal(custo_medio_atual)
    quantidade_entrada = _para_decimal(quantidade_entrada)
    custo_entrada = _para_decimal(custo_entrada)

    if quantidade_entrada <= 0:
        return _arredondar_6(custo_medio_atual)

    if fisico_atual <= 0 or custo_medio_atual <= 0:
        return _arredondar_6(custo_entrada)

    valor_total_antigo = fisico_atual * custo_medio_atual
    valor_total_entrada = quantidade_entrada * custo_entrada
    quantidade_total = fisico_atual + quantidade_entrada

    if quantidade_total <= 0:
        return _arredondar_6(custo_entrada)

    novo_custo = (valor_total_antigo + valor_total_entrada) / quantidade_total
    return _arredondar_6(novo_custo)


def _buscar_snapshot_bronze(
    id_empresa_proprietaria: int,
    codigo_produto_omie,
    codigo_produto_integracao,
):
    """Eu busco a foto mais recente da Bronze para usar como base do estoque operacional."""
    sql = text(
        """
        SELECT TOP (1)
            ISNULL(be.[CodigoLocalEstoque], 0) AS codigo_local_estoque,
            be.[NomeAmbiente] AS nome_ambiente,
            ISNULL(be.[Saldo], 0) AS saldo,
            ISNULL(be.[Cmc], 0) AS cmc,
            ISNULL(be.[Pendente], 0) AS pendente,
            ISNULL(be.[EstoqueMinimo], 0) AS estoque_minimo,
            ISNULL(be.[Reservado], 0) AS reservado,
            ISNULL(be.[Fisico], 0) AS fisico,
            ISNULL(be.[PrecoUnitario], 0) AS preco_unitario
        FROM [Integracao].[Bronze].[FatoOmieEstoque] be
        WHERE
            be.[IDEmpresaProprietaria] = :id_empresa_proprietaria
            AND (
                   (:codigo_produto_omie IS NOT NULL AND be.[CodigoProduto] = :codigo_produto_omie)
                OR (:codigo_produto_integracao <> '' AND ISNULL(be.[CodigoProdutoIntegracao], '') = :codigo_produto_integracao)
            )
        ORDER BY
            be.[DataUltimaAtualizacaoUtc] DESC,
            be.[IDFatoOmieEstoque] DESC
        """
    )

    return db.session.execute(
        sql,
        {
            "id_empresa_proprietaria": id_empresa_proprietaria,
            "codigo_produto_omie": codigo_produto_omie,
            "codigo_produto_integracao": codigo_produto_integracao or "",
        },
    ).mappings().first()


def _obter_ou_criar_estoque_atual(
    id_empresa_proprietaria: int,
    id_fato_produto: int,
    id_fato_produto_omie_vinculo,
    codigo_produto_omie,
    codigo_omie,
    codigo_produto_integracao,
    nome_produto,
    unidade,
):
    """Eu localizo o estoque atual na Silver ou crio um registro base usando a Bronze mais recente."""
    sql_busca = text(
        """
        SELECT TOP (1)
            *
        FROM [Integracao].[Silver].[FatoEstoqueAtual]
        WHERE
            [IDEmpresaProprietaria] = :id_empresa_proprietaria
            AND [IDFatoProduto] = :id_fato_produto
        ORDER BY
            [DataUltimaMovimentacaoUtc] DESC,
            [IDFatoEstoqueAtual] DESC
        """
    )

    registro = db.session.execute(
        sql_busca,
        {
            "id_empresa_proprietaria": id_empresa_proprietaria,
            "id_fato_produto": id_fato_produto,
        },
    ).mappings().first()

    if registro:
        return dict(registro)

    snapshot = _buscar_snapshot_bronze(
        id_empresa_proprietaria=id_empresa_proprietaria,
        codigo_produto_omie=codigo_produto_omie,
        codigo_produto_integracao=codigo_produto_integracao,
    )

    codigo_local_estoque = 0
    nome_ambiente = None
    saldo = Decimal("0")
    cmc = Decimal("0")
    pendente = Decimal("0")
    estoque_minimo = Decimal("0")
    reservado = Decimal("0")
    fisico = Decimal("0")
    preco_unitario = Decimal("0")

    if snapshot:
        codigo_local_estoque = int(snapshot["codigo_local_estoque"] or 0)
        nome_ambiente = snapshot["nome_ambiente"]
        saldo = _para_decimal(snapshot["saldo"])
        cmc = _para_decimal(snapshot["cmc"])
        pendente = _para_decimal(snapshot["pendente"])
        estoque_minimo = _para_decimal(snapshot["estoque_minimo"])
        reservado = _para_decimal(snapshot["reservado"])
        fisico = _para_decimal(snapshot["fisico"])
        preco_unitario = _para_decimal(snapshot["preco_unitario"])

    sql_insert = text(
        """
        INSERT INTO [Integracao].[Silver].[FatoEstoqueAtual] (
            [IDEmpresaProprietaria],
            [IDFatoProduto],
            [IDFatoProdutoOmieVinculo],
            [CodigoProdutoOmie],
            [CodigoOmie],
            [CodigoProdutoIntegracao],
            [NomeAmbiente],
            [NomeProduto],
            [Unidade],
            [CodigoLocalEstoque],
            [Saldo],
            [Cmc],
            [Pendente],
            [EstoqueMinimo],
            [Reservado],
            [Fisico],
            [PrecoUnitario],
            [OrigemRegistro],
            [DataUltimaMovimentacaoUtc]
        )
        OUTPUT INSERTED.*
        VALUES (
            :id_empresa_proprietaria,
            :id_fato_produto,
            :id_fato_produto_omie_vinculo,
            :codigo_produto_omie,
            :codigo_omie,
            :codigo_produto_integracao,
            :nome_ambiente,
            :nome_produto,
            :unidade,
            :codigo_local_estoque,
            :saldo,
            :cmc,
            :pendente,
            :estoque_minimo,
            :reservado,
            :fisico,
            :preco_unitario,
            'PEDIDO',
            SYSUTCDATETIME()
        )
        """
    )

    novo_registro = db.session.execute(
        sql_insert,
        {
            "id_empresa_proprietaria": id_empresa_proprietaria,
            "id_fato_produto": id_fato_produto,
            "id_fato_produto_omie_vinculo": id_fato_produto_omie_vinculo,
            "codigo_produto_omie": codigo_produto_omie,
            "codigo_omie": codigo_omie,
            "codigo_produto_integracao": codigo_produto_integracao,
            "nome_ambiente": nome_ambiente,
            "nome_produto": nome_produto,
            "unidade": unidade,
            "codigo_local_estoque": codigo_local_estoque,
            "saldo": _arredondar_6(saldo),
            "cmc": _arredondar_6(cmc),
            "pendente": _arredondar_6(pendente),
            "estoque_minimo": _arredondar_6(estoque_minimo),
            "reservado": _arredondar_6(reservado),
            "fisico": _arredondar_6(fisico),
            "preco_unitario": _arredondar_6(preco_unitario),
        },
    ).mappings().one()

    return dict(novo_registro)


def _registrar_movimento_estoque(
    id_fato_estoque_atual,
    id_empresa_proprietaria,
    id_fato_produto,
    id_fato_produto_omie_vinculo,
    id_nota_fornecedor_omie,
    id_nota_fornecedor_item_omie,
    tipo_movimento,
    observacao,
    quantidade_movimento,
    preco_unitario_entrada,
    pendente_antes,
    pendente_depois,
    reservado_antes,
    reservado_depois,
    fisico_antes,
    fisico_depois,
    saldo_antes,
    saldo_depois,
    cmc_antes,
    cmc_depois,
    preco_unitario_depois,
):
    """Eu gravo cada evento no livro razão do estoque."""
    sql = text(
        """
        INSERT INTO [Integracao].[Silver].[FatoMovimentoEstoque] (
            [IDFatoEstoqueAtual],
            [IDEmpresaProprietaria],
            [IDFatoProduto],
            [IDFatoProdutoOmieVinculo],
            [IDNotaFornecedorOmie],
            [IDNotaFornecedorItemOmie],
            [TipoMovimento],
            [Observacao],
            [QuantidadeMovimento],
            [PrecoUnitarioEntrada],
            [PendenteAntes],
            [PendenteDepois],
            [ReservadoAntes],
            [ReservadoDepois],
            [FisicoAntes],
            [FisicoDepois],
            [SaldoAntes],
            [SaldoDepois],
            [CmcAntes],
            [CmcDepois],
            [PrecoUnitarioDepois]
        )
        VALUES (
            :id_fato_estoque_atual,
            :id_empresa_proprietaria,
            :id_fato_produto,
            :id_fato_produto_omie_vinculo,
            :id_nota_fornecedor_omie,
            :id_nota_fornecedor_item_omie,
            :tipo_movimento,
            :observacao,
            :quantidade_movimento,
            :preco_unitario_entrada,
            :pendente_antes,
            :pendente_depois,
            :reservado_antes,
            :reservado_depois,
            :fisico_antes,
            :fisico_depois,
            :saldo_antes,
            :saldo_depois,
            :cmc_antes,
            :cmc_depois,
            :preco_unitario_depois
        )
        """
    )

    db.session.execute(
        sql,
        {
            "id_fato_estoque_atual": id_fato_estoque_atual,
            "id_empresa_proprietaria": id_empresa_proprietaria,
            "id_fato_produto": id_fato_produto,
            "id_fato_produto_omie_vinculo": id_fato_produto_omie_vinculo,
            "id_nota_fornecedor_omie": id_nota_fornecedor_omie,
            "id_nota_fornecedor_item_omie": id_nota_fornecedor_item_omie,
            "tipo_movimento": tipo_movimento,
            "observacao": observacao,
            "quantidade_movimento": _arredondar_6(_para_decimal(quantidade_movimento)),
            "preco_unitario_entrada": _arredondar_6(_para_decimal(preco_unitario_entrada)),
            "pendente_antes": _arredondar_6(_para_decimal(pendente_antes)),
            "pendente_depois": _arredondar_6(_para_decimal(pendente_depois)),
            "reservado_antes": _arredondar_6(_para_decimal(reservado_antes)),
            "reservado_depois": _arredondar_6(_para_decimal(reservado_depois)),
            "fisico_antes": _arredondar_6(_para_decimal(fisico_antes)),
            "fisico_depois": _arredondar_6(_para_decimal(fisico_depois)),
            "saldo_antes": _arredondar_6(_para_decimal(saldo_antes)),
            "saldo_depois": _arredondar_6(_para_decimal(saldo_depois)),
            "cmc_antes": _arredondar_6(_para_decimal(cmc_antes)),
            "cmc_depois": _arredondar_6(_para_decimal(cmc_depois)),
            "preco_unitario_depois": _arredondar_6(_para_decimal(preco_unitario_depois)),
        },
    )


def _aplicar_pendente_no_salvar(
    id_empresa_proprietaria,
    id_fato_produto,
    id_fato_produto_omie_vinculo,
    codigo_produto_omie,
    codigo_omie,
    codigo_produto_integracao,
    nome_produto,
    unidade,
    quantidade,
    valor_unitario,
    id_nota_fornecedor_omie,
    id_nota_fornecedor_item_omie,
):
    """Quando eu salvo o pedido, eu só aumento o pendente. Eu não altero físico nem saldo."""
    estoque = _obter_ou_criar_estoque_atual(
        id_empresa_proprietaria=id_empresa_proprietaria,
        id_fato_produto=id_fato_produto,
        id_fato_produto_omie_vinculo=id_fato_produto_omie_vinculo,
        codigo_produto_omie=codigo_produto_omie,
        codigo_omie=codigo_omie,
        codigo_produto_integracao=codigo_produto_integracao,
        nome_produto=nome_produto,
        unidade=unidade,
    )

    pendente_antes = _para_decimal(estoque["Pendente"])
    reservado_antes = _para_decimal(estoque["Reservado"])
    fisico_antes = _para_decimal(estoque["Fisico"])
    saldo_antes = _para_decimal(estoque["Saldo"])
    cmc_antes = _para_decimal(estoque["Cmc"])
    preco_unitario_antes = _para_decimal(estoque["PrecoUnitario"])
    quantidade = _para_decimal(quantidade)

    pendente_depois = _arredondar_6(pendente_antes + quantidade)

    sql_update = text(
        """
        UPDATE [Integracao].[Silver].[FatoEstoqueAtual]
        SET
            [Pendente] = :pendente_depois,
            [DataUltimaMovimentacaoUtc] = SYSUTCDATETIME(),
            [DataUltimaAtualizacaoUtc] = SYSUTCDATETIME()
        WHERE [IDFatoEstoqueAtual] = :id_fato_estoque_atual
        """
    )

    db.session.execute(
        sql_update,
        {
            "id_fato_estoque_atual": estoque["IDFatoEstoqueAtual"],
            "pendente_depois": pendente_depois,
        },
    )

    _registrar_movimento_estoque(
        id_fato_estoque_atual=estoque["IDFatoEstoqueAtual"],
        id_empresa_proprietaria=id_empresa_proprietaria,
        id_fato_produto=id_fato_produto,
        id_fato_produto_omie_vinculo=id_fato_produto_omie_vinculo,
        id_nota_fornecedor_omie=id_nota_fornecedor_omie,
        id_nota_fornecedor_item_omie=id_nota_fornecedor_item_omie,
        tipo_movimento="PEDIDO_PENDENTE",
        observacao="Pedido salvo. Quantidade enviada para pendente.",
        quantidade_movimento=quantidade,
        preco_unitario_entrada=valor_unitario,
        pendente_antes=pendente_antes,
        pendente_depois=pendente_depois,
        reservado_antes=reservado_antes,
        reservado_depois=reservado_antes,
        fisico_antes=fisico_antes,
        fisico_depois=fisico_antes,
        saldo_antes=saldo_antes,
        saldo_depois=saldo_antes,
        cmc_antes=cmc_antes,
        cmc_depois=cmc_antes,
        preco_unitario_depois=preco_unitario_antes,
    )


def _confirmar_item_no_estoque(item: dict, nota: dict):
    """Quando eu confirmo, eu tiro do pendente, somo no físico e recalculo o custo médio."""
    quantidade_total = _para_decimal(item["Quantidade"])
    quantidade_confirmada = _para_decimal(item["QuantidadeConfirmada"])
    quantidade_restante = _arredondar_6(quantidade_total - quantidade_confirmada)

    if quantidade_restante <= 0:
        return

    estoque = _obter_ou_criar_estoque_atual(
        id_empresa_proprietaria=nota["IDEmpresaProprietaria"],
        id_fato_produto=item["IDFatoProduto"],
        id_fato_produto_omie_vinculo=item["IDFatoProdutoOmieVinculo"],
        codigo_produto_omie=item["CodigoProdutoOmie"],
        codigo_omie=item["CodigoOmie"],
        codigo_produto_integracao=item["CodigoProdutoIntegracao"],
        nome_produto=item["NomeProdutoOmie"],
        unidade=item["Unidade"],
    )

    pendente_antes = _para_decimal(estoque["Pendente"])
    reservado_antes = _para_decimal(estoque["Reservado"])
    fisico_antes = _para_decimal(estoque["Fisico"])
    saldo_antes = _para_decimal(estoque["Saldo"])
    cmc_antes = _para_decimal(estoque["Cmc"])
    custo_entrada = _para_decimal(item["ValorUnitario"])

    pendente_depois = _arredondar_6(max(pendente_antes - quantidade_restante, Decimal("0")))
    fisico_depois = _arredondar_6(fisico_antes + quantidade_restante)
    saldo_depois = _arredondar_6(fisico_depois - reservado_antes)
    cmc_depois = _recalcular_custo_medio(
        fisico_atual=fisico_antes,
        custo_medio_atual=cmc_antes,
        quantidade_entrada=quantidade_restante,
        custo_entrada=custo_entrada,
    )
    preco_unitario_depois = cmc_depois

    sql_update_estoque = text(
        """
        UPDATE [Integracao].[Silver].[FatoEstoqueAtual]
        SET
            [Pendente] = :pendente_depois,
            [Fisico] = :fisico_depois,
            [Saldo] = :saldo_depois,
            [Cmc] = :cmc_depois,
            [PrecoUnitario] = :preco_unitario_depois,
            [DataUltimaMovimentacaoUtc] = SYSUTCDATETIME(),
            [DataUltimaAtualizacaoUtc] = SYSUTCDATETIME()
        WHERE [IDFatoEstoqueAtual] = :id_fato_estoque_atual
        """
    )

    db.session.execute(
        sql_update_estoque,
        {
            "id_fato_estoque_atual": estoque["IDFatoEstoqueAtual"],
            "pendente_depois": pendente_depois,
            "fisico_depois": fisico_depois,
            "saldo_depois": saldo_depois,
            "cmc_depois": cmc_depois,
            "preco_unitario_depois": preco_unitario_depois,
        },
    )

    _registrar_movimento_estoque(
        id_fato_estoque_atual=estoque["IDFatoEstoqueAtual"],
        id_empresa_proprietaria=nota["IDEmpresaProprietaria"],
        id_fato_produto=item["IDFatoProduto"],
        id_fato_produto_omie_vinculo=item["IDFatoProdutoOmieVinculo"],
        id_nota_fornecedor_omie=nota["IDNotaFornecedorOmie"],
        id_nota_fornecedor_item_omie=item["IDNotaFornecedorItemOmie"],
        tipo_movimento="ENTRADA_PEDIDO",
        observacao="Confirmação da entrada do pedido de compra.",
        quantidade_movimento=quantidade_restante,
        preco_unitario_entrada=custo_entrada,
        pendente_antes=pendente_antes,
        pendente_depois=pendente_depois,
        reservado_antes=reservado_antes,
        reservado_depois=reservado_antes,
        fisico_antes=fisico_antes,
        fisico_depois=fisico_depois,
        saldo_antes=saldo_antes,
        saldo_depois=saldo_depois,
        cmc_antes=cmc_antes,
        cmc_depois=cmc_depois,
        preco_unitario_depois=preco_unitario_depois,
    )

    sql_update_item = text(
        """
        UPDATE [Integracao].[Silver].[NotaFornecedorItemOmie]
        SET
            [QuantidadeConfirmada] = [QuantidadeConfirmada] + :quantidade_confirmada,
            [PrecoUnitarioConfirmado] = :preco_unitario_confirmado,
            [DataConfirmacaoUtc] = SYSUTCDATETIME(),
            [CodigoLocalEstoque] = :codigo_local_estoque,
            [DataUltimaAtualizacaoUtc] = SYSUTCDATETIME()
        WHERE [IDNotaFornecedorItemOmie] = :id_nota_fornecedor_item_omie
        """
    )

    db.session.execute(
        sql_update_item,
        {
            "quantidade_confirmada": quantidade_restante,
            "preco_unitario_confirmado": custo_entrada,
            "codigo_local_estoque": estoque["CodigoLocalEstoque"],
            "id_nota_fornecedor_item_omie": item["IDNotaFornecedorItemOmie"],
        },
    )


@estoques_bp.post("/api/compras/pedido/salvar")
@limiter.limit("20 per minute")
@proteger_rota_estoque()
def salvar_pedido_compra():
    """Eu salvo o pedido e já empurro a quantidade para a coluna pendente do estoque atual."""
    payload = request.get_json(silent=True) or {}

    cabecalho = payload.get("cabecalho") or {}
    itens = payload.get("itens") or []

    id_empresa_proprietaria = cabecalho.get("id_empresa_proprietaria")
    id_fato_empresas_omie = cabecalho.get("id_fato_empresas_omie")
    codigo_fornecedor_omie = cabecalho.get("codigo_fornecedor_omie")
    codigo_fornecedor_integracao = (cabecalho.get("codigo_fornecedor_integracao") or "").strip() or None
    razao_social_fornecedor = (cabecalho.get("razao_social_fornecedor") or "").strip()
    nome_fantasia_fornecedor = (cabecalho.get("nome_fantasia_fornecedor") or "").strip() or None
    contato_fornecedor = (cabecalho.get("contato_fornecedor") or "").strip() or None
    email_fornecedor = (cabecalho.get("email_fornecedor") or "").strip() or None
    cnpj_cpf_fornecedor = (cabecalho.get("cnpj_cpf_fornecedor") or "").strip() or None
    codigo_integracao_nota = (cabecalho.get("codigo_integracao_nota") or "").strip() or None
    data_previsao = (cabecalho.get("data_previsao") or "").strip() or None
    observacoes = (cabecalho.get("observacoes") or "").strip() or None

    if not id_empresa_proprietaria:
        return jsonify({"ok": False, "mensagem": "Selecione a empresa proprietária."}), 400

    if not id_fato_empresas_omie:
        return jsonify({"ok": False, "mensagem": "Selecione o fornecedor."}), 400

    if not razao_social_fornecedor:
        return jsonify({"ok": False, "mensagem": "A razão social do fornecedor é obrigatória."}), 400

    if not itens:
        return jsonify({"ok": False, "mensagem": "Adicione pelo menos um item antes de salvar."}), 400

    for indice, item in enumerate(itens, start=1):
        if not item.get("id_fato_produto"):
            return jsonify({"ok": False, "mensagem": f"O item {indice} precisa estar vinculado a um produto interno."}), 400

        quantidade = _para_decimal(item.get("quantidade"))
        valor_unitario = _para_decimal(item.get("valor_unitario"))

        if quantidade <= 0:
            return jsonify({"ok": False, "mensagem": f"O item {indice} precisa ter quantidade maior que zero."}), 400

        if valor_unitario < 0:
            return jsonify({"ok": False, "mensagem": f"O item {indice} precisa ter valor unitário válido."}), 400

    sql_insert_cabecalho = text(
        """
        INSERT INTO [Integracao].[Silver].[NotaFornecedorOmie] (
            [IDEmpresaProprietaria],
            [IDFatoEmpresasOmie],
            [CodigoFornecedorOmie],
            [CodigoFornecedorIntegracao],
            [RazaoSocialFornecedor],
            [NomeFantasiaFornecedor],
            [ContatoFornecedor],
            [EmailFornecedor],
            [CnpjCpfFornecedor],
            [CodigoIntegracaoNota],
            [DataPrevisao],
            [Observacoes],
            [Status]
        )
        OUTPUT INSERTED.[IDNotaFornecedorOmie]
        VALUES (
            :id_empresa_proprietaria,
            :id_fato_empresas_omie,
            :codigo_fornecedor_omie,
            :codigo_fornecedor_integracao,
            :razao_social_fornecedor,
            :nome_fantasia_fornecedor,
            :contato_fornecedor,
            :email_fornecedor,
            :cnpj_cpf_fornecedor,
            :codigo_integracao_nota,
            :data_previsao,
            :observacoes,
            'Salvo'
        )
        """
    )

    sql_insert_item = text(
        """
        INSERT INTO [Integracao].[Silver].[NotaFornecedorItemOmie] (
            [IDNotaFornecedorOmie],
            [NumeroItem],
            [IDEmpresaProprietaria],
            [IDFatoEmpresasOmie],
            [CodigoFornecedorOmie],
            [IDFatoProduto],
            [IDFatoProdutoOmieVinculo],
            [CodigoProdutoOmie],
            [CodigoOmie],
            [CodigoProdutoIntegracao],
            [CodigoProdutoFornecedorOmie],
            [NomeProdutoOmie],
            [NomeProdutoFornecedor],
            [Unidade],
            [Quantidade],
            [ValorUnitario],
            [BitVinculoProdutoFornecedor]
        )
        OUTPUT INSERTED.[IDNotaFornecedorItemOmie]
        VALUES (
            :id_nota_fornecedor_omie,
            :numero_item,
            :id_empresa_proprietaria,
            :id_fato_empresas_omie,
            :codigo_fornecedor_omie,
            :id_fato_produto,
            :id_fato_produto_omie_vinculo,
            :codigo_produto_omie,
            :codigo_omie,
            :codigo_produto_integracao,
            :codigo_produto_fornecedor_omie,
            :nome_produto_omie,
            :nome_produto_fornecedor,
            :unidade,
            :quantidade,
            :valor_unitario,
            :bit_vinculo_produto_fornecedor
        )
        """
    )

    try:
        id_nota_fornecedor_omie = db.session.execute(
            sql_insert_cabecalho,
            {
                "id_empresa_proprietaria": id_empresa_proprietaria,
                "id_fato_empresas_omie": id_fato_empresas_omie,
                "codigo_fornecedor_omie": codigo_fornecedor_omie,
                "codigo_fornecedor_integracao": codigo_fornecedor_integracao,
                "razao_social_fornecedor": razao_social_fornecedor,
                "nome_fantasia_fornecedor": nome_fantasia_fornecedor,
                "contato_fornecedor": contato_fornecedor,
                "email_fornecedor": email_fornecedor,
                "cnpj_cpf_fornecedor": cnpj_cpf_fornecedor,
                "codigo_integracao_nota": codigo_integracao_nota,
                "data_previsao": data_previsao,
                "observacoes": observacoes,
            },
        ).scalar_one()

        for numero_item, item in enumerate(itens, start=1):
            id_nota_fornecedor_item_omie = db.session.execute(
                sql_insert_item,
                {
                    "id_nota_fornecedor_omie": id_nota_fornecedor_omie,
                    "numero_item": numero_item,
                    "id_empresa_proprietaria": id_empresa_proprietaria,
                    "id_fato_empresas_omie": id_fato_empresas_omie,
                    "codigo_fornecedor_omie": codigo_fornecedor_omie,
                    "id_fato_produto": item.get("id_fato_produto"),
                    "id_fato_produto_omie_vinculo": item.get("id_fato_produto_omie_vinculo"),
                    "codigo_produto_omie": item.get("codigo_produto_omie"),
                    "codigo_omie": item.get("codigo_omie"),
                    "codigo_produto_integracao": item.get("codigo_produto_integracao"),
                    "codigo_produto_fornecedor_omie": item.get("codigo_produto_fornecedor"),
                    "nome_produto_omie": item.get("descricao"),
                    "nome_produto_fornecedor": item.get("descricao_produto_fornecedor") or item.get("descricao"),
                    "unidade": item.get("unidade"),
                    "quantidade": item.get("quantidade"),
                    "valor_unitario": item.get("valor_unitario"),
                    "bit_vinculo_produto_fornecedor": 1 if item.get("tem_vinculo_fornecedor") else 0,
                },
            ).scalar_one()

            _aplicar_pendente_no_salvar(
                id_empresa_proprietaria=id_empresa_proprietaria,
                id_fato_produto=item.get("id_fato_produto"),
                id_fato_produto_omie_vinculo=item.get("id_fato_produto_omie_vinculo"),
                codigo_produto_omie=item.get("codigo_produto_omie"),
                codigo_omie=item.get("codigo_omie"),
                codigo_produto_integracao=item.get("codigo_produto_integracao"),
                nome_produto=item.get("descricao"),
                unidade=item.get("unidade"),
                quantidade=item.get("quantidade"),
                valor_unitario=item.get("valor_unitario"),
                id_nota_fornecedor_omie=id_nota_fornecedor_omie,
                id_nota_fornecedor_item_omie=id_nota_fornecedor_item_omie,
            )

        db.session.commit()

        return jsonify(
            {
                "ok": True,
                "mensagem": "Pedido salvo com sucesso e quantidade enviada para pendente.",
                "id_nota_fornecedor_omie": id_nota_fornecedor_omie,
            }
        )
    except Exception as erro:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro ao salvar pedido: {erro}"}), 500


@estoques_bp.post("/api/compras/pedido/<int:id_nota_fornecedor_omie>/confirmar")
@limiter.limit("15 per minute")
@proteger_rota_estoque()
def confirmar_pedido_compra(id_nota_fornecedor_omie: int):
    """Eu confirmo a entrada real: tiro do pendente, somo no físico, recalculo saldo e custo médio."""
    sql_nota = text(
        """
        SELECT TOP (1) *
        FROM [Integracao].[Silver].[NotaFornecedorOmie]
        WHERE [IDNotaFornecedorOmie] = :id_nota_fornecedor_omie
          AND ISNULL([BitAtivo], 1) = 1
        """
    )

    sql_itens = text(
        """
        SELECT *
        FROM [Integracao].[Silver].[NotaFornecedorItemOmie]
        WHERE [IDNotaFornecedorOmie] = :id_nota_fornecedor_omie
          AND ISNULL([BitAtivo], 1) = 1
        ORDER BY [NumeroItem] ASC
        """
    )

    nota = db.session.execute(
        sql_nota,
        {"id_nota_fornecedor_omie": id_nota_fornecedor_omie},
    ).mappings().first()

    if not nota:
        return jsonify({"ok": False, "mensagem": "Pedido não encontrado."}), 404

    if nota["Status"] == "Recebido Total":
        return jsonify({"ok": False, "mensagem": "Este pedido já foi confirmado integralmente."}), 400

    itens = db.session.execute(
        sql_itens,
        {"id_nota_fornecedor_omie": id_nota_fornecedor_omie},
    ).mappings().all()

    if not itens:
        return jsonify({"ok": False, "mensagem": "O pedido não possui itens."}), 400

    try:
        for item in itens:
            if not item["IDFatoProduto"]:
                raise ValueError(
                    f"O item {item['NumeroItem']} não possui IDFatoProduto e não pode atualizar estoque."
                )

            _confirmar_item_no_estoque(item=dict(item), nota=dict(nota))

        sql_status = text(
            """
            UPDATE [Integracao].[Silver].[NotaFornecedorOmie]
            SET
                [Status] = 'Recebido Total',
                [DataConfirmacaoUtc] = SYSUTCDATETIME(),
                [DataUltimaAtualizacaoUtc] = SYSUTCDATETIME()
            WHERE [IDNotaFornecedorOmie] = :id_nota_fornecedor_omie
            """
        )

        db.session.execute(
            sql_status,
            {"id_nota_fornecedor_omie": id_nota_fornecedor_omie},
        )

        db.session.commit()

        return jsonify(
            {
                "ok": True,
                "mensagem": "Pedido confirmado com sucesso. Pendente baixado, físico e saldo atualizados, custo médio recalculado.",
                "id_nota_fornecedor_omie": id_nota_fornecedor_omie,
            }
        )
    except Exception as erro:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro ao confirmar pedido: {erro}"}), 500





