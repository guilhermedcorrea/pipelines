import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any
from decimal import Decimal, InvalidOperation
import requests
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text
from flask_socketio import disconnect, emit, join_room, leave_room
from ..extensions import cache, db, limiter, socketio
from decimal import Decimal
from ..retry_deadlock import (
    eh_deadlock_sql_server,
    executar_transacao_com_retry_deadlock_ou_enfileirar,
)



"""Kanban Euromidia Comercial"""


kanban_bp = Blueprint("kanban", __name__)





TABELA_PERMISSOES_USUARIO = "[Integracao].[Silver].[PermissoesUsuario]"
TABELA_DIM_PERMISSOES = "[Integracao].[Silver].[DimPermissoes]"
TABELA_PERMISSAO_DESCONTO = "[Kanban].[Silver].[DimKanbanPermissaoDesconto]"
TABELA_CARD_APROVA_PRECO = "[Kanban].[Silver].[FatoAprovaPreco]"

TABELA_CARD_PAINEL_FACE = "[Kanban].[Silver].[FatoKanbanCardPainelFace]"
TABELA_DIM_PAINEIS_EUROMIDIA = "[Integracao].[Silver].[DimPaineisEuromidia]"
TABELA_DIM_FACES_PAINEIS = "[Integracao].[Silver].[DimFacesPaineis]"
TABELA_TABELA_PRECOS_EUROMIDIA = "[Integracao].[Silver].[FatoTabelaPrecosEuromidia]"


HEALTH_CHECK_KANBAN_PADRAO = 1

MAPA_TAGS_HEALTH_CHECK = {
    "novo_contrato": ["novo contrato"],
    "aditivo": ["aditivo"],
    "perda_preco": [
        "perda por preco",
        "perda preço",
        "perda de preco",
        "perda de preço",
        "preco",
        "preço",
    ],
    "perda_concorrente": [
        "perda por concorrente",
        "concorrente",
    ],
    "perda_falta_painel": [
        "perda por falta de painel",
        "falta de painel",
        "sem painel",
    ],
}

MAPA_MOTIVOS_HEALTH_CHECK = {
    "perda_preco": [
        "preco",
        "preço",
        "perda por preco",
        "perda preço",
        "valor alto",
        "valor",
    ],
    "perda_concorrente": [
        "concorrente",
        "concorrencia",
        "concorrência",
    ],
    "perda_falta_painel": [
        "falta de painel",
        "sem painel",
        "sem disponibilidade",
        "sem inventario",
        "sem inventário",
    ],
}










TABELA_RELACIONAMENTO_EMPRESA = "[Integracao].[Silver].[DimRelacionamentoEmpresa]"
TABELA_KANBAN = "[Kanban].[Silver].[DimKanban]"
TABELA_KANBAN_FASE = "[Kanban].[Silver].[DimKanbanFase]"
TABELA_CARD = "[Kanban].[Silver].[FatoKanbanCard]"
TABELA_CARD_MOVIMENTO = "[Kanban].[Silver].[FatoKanbanCardMovimento]"
TABELA_CARD_NOTA = "[Kanban].[Silver].[FatoKanbanCardNota]"
TABELA_CARD_LOG = "[Kanban].[Silver].[FatoKanbanCardLog]"
TABELA_EMPRESAS = "[Integracao].[Silver].[DimEmpresas]"
TABELA_CNAES = "[Integracao].[Silver].[DimCnaes]"
TABELA_TIPO_CLIENTE_DESCONTO = "[Integracao].[Silver].[DimTipoCliente]"
TABELA_ORIGEM_ATENDIMENTO = "[Integracao].[Silver].[DimOrigemAtendimento]"




TABELA_CONTRATO_CARD_EUROMIDIA = "[Integracao].[Silver].[FatoContratoCardEuromidia]"


TABELA_CONTROLE_CONTRATOS = "[Integracao].[Silver].[FatoControleContratosEuromidia]"
TABELA_CONTROLE_CONTRATOS_ITENS = "[Integracao].[Silver].[FatoControleContratosItensEuromidia]"
TABELA_CONTRATO_ITEM_PRECO_PRATICADO = "[Integracao].[Silver].[FatoContratoItemPrecoPraticadoEuromidia]"
COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO = "IDFatoContratoItemPrecoPraticadoEuromidia"
ID_TAG_CONTRATO_APROVADO = 13


ID_TAG_TIPO_CONTRATO_ADITIVO = 8
ID_TAG_TIPO_CONTRATO_NOVO = 9

TIPO_SOLICITACAO_ADITIVO = "ADITIVO"
TIPO_SOLICITACAO_NOVO = "NOVO CONTRATO"

VALOR_OPCAO_NOVO_CONTRATO = "NOVO CONTRATO"
VALOR_OPCAO_NOVO_PAINEL = "NOVO PAINEL"
ID_EMPRESA_PROPRIETARIA_CONTRATOS = 3



TABELA_SOLICITACAO_CONTRATO = "[Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]"
TABELA_SOLICITACAO_CONTRATO_ITEM = "[Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]"
TABELA_STATUS_CONTRATOS = "[Integracao].[Silver].[DimStatusContratos]"
TABELA_CONTATOS_CONTRATO = "[Integracao].[Silver].[DimContatosContrato]"

ID_TAG_CONTRATO_EM_AVALIACAO = 14
ID_TAG_PLANO_MIDIA = 15
FASES_COM_TAG_PLANO_MIDIA = {1, 2, 3, 4}
NOME_TAG_CONTRATO_EM_AVALIACAO = "Contrato em Avaliação"
NOME_TAG_TIPO_CONTRATO_NOVO = "Novo Contrato"
NOME_TAG_TIPO_CONTRATO_ADITIVO = "Aditivo"



TABELA_EMPRESAS_PROPRIETARIAS = "[Integracao].[dbo].[EmpresaProprietaria]"
URL_API_MINHA_RECEITA = "https://minhareceita.org"

NOME_TAG_DESCONTO_APROVADO = "Desconto Aprovado"
TIPO_NOTA_APROVACAO_DESCONTO = "APROVACAO_DESCONTO"

NOME_TAG_APROVACAO_DESCONTO = "Aprovação Desconto"
PERCENTUAL_LIMITE_APROVACAO_DIRETORIA_SOBRE_CUSTO = Decimal("12")
TABELA_MOTIVO_ENCERRAMENTO_CARD = "[Kanban].[Silver].[DimKanbanMotivoEncerramento]"
TABELA_HISTORICO_ENCERRAMENTO_CARD = "[Kanban].[Silver].[FatoDimHistoricoEncerramentoCard]"
TABELA_CARD_TAG_HISTORICO= "[Kanban].[Silver].[FatoKanbanCardTagHistorico]"
TABELA_CARD_STATUS_HISTORICO = "[Kanban].[Silver].[FatoKanbanCardStatusHistorico]"
TABELA_CARD_OBSERVACOES = "[Kanban].[Silver].[FatoKanbanCardObservacoes]"
TABELA_CARD_NEGOCIACAO_PRECO = "[Kanban].[Silver].[FatoKanbanNegociacaoPreco]"
TABELA_STATUS_CARD = "[Kanban].[Silver].[DimKanbanStatusCard]"
TABELA_MOTIVO_INATIVACAO_CARD = "[Kanban].[Silver].[DimKanbanMotivoInativacaoCard]"
STATUS_CARD_FALLBACK_PADRAO = "ATIVO"
STATUS_CARD_FALLBACK_INATIVACAO = "CANCELADO"
TIPO_FASE_FALLBACK_PADRAO = "ATIVA"
TIPO_TAG_FALLBACK_PADRAO = "OPERACIONAL"
NAMESPACE_SOCKET_KANBAN = "/kanban"

TIMEOUT_CACHE_CURTO = 20
TIMEOUT_CACHE_MEDIO = 60
TIMEOUT_CACHE_LONGO = 300
LIMITE_CARDS_POR_FASE = 100

TABELA_KANBAN = "[Kanban].[Silver].[DimKanban]"
TABELA_KANBAN_FASE = "[Kanban].[Silver].[DimKanbanFase]"
TABELA_CARD = "[Kanban].[Silver].[FatoKanbanCard]"
TABELA_CARD_MOVIMENTO = "[Kanban].[Silver].[FatoKanbanCardMovimento]"
TABELA_CARD_NOTA = "[Kanban].[Silver].[FatoKanbanCardNota]"
TABELA_CARD_LOG = "[Kanban].[Silver].[FatoKanbanCardLog]"
TABELA_EMPRESAS = "[Integracao].[Silver].[DimEmpresas]"
TABELA_CNAES = "[Integracao].[Silver].[DimCnaes]"


def _id_empresa_usuario() -> int:
    return int(getattr(current_user, "IDEmpresaProprietaria", 0) or 0)



def _id_usuario() -> int:
    return int(getattr(current_user, "IDDimUsuarios", 0) or 0)



def _assert_login() -> int:
    if not getattr(current_user, "is_authenticated", False):
        abort(401)
    uid = _id_usuario()
    if not uid:
        abort(401)
    return uid



def _id_empresa_usuario_or_403() -> int:
    _assert_login()
    id_emp = _id_empresa_usuario()
    if not id_emp:
        abort(403, "Usuário sem IDEmpresaProprietaria definida")
    return id_emp












def _valor_booleano_verdadeiro(valor: Any) -> bool:
    """Eu converto flags comuns de usuário em booleano verdadeiro."""
    if isinstance(valor, bool):
        return valor

    if valor is None:
        return False

    texto = str(valor).strip().lower()
    return texto in {"1", "true", "sim", "s", "yes", "y", "admin", "administrador"}


def _usuario_logado_eh_admin_aprovacao_desconto() -> bool:
    """
    Eu valido se o usuário logado pode acessar a aprovação de desconto.

    Regra correta:
    - a tela de aprovação é de Admin;
    - o vendedor/usuário que deu o desconto não deve enxergar a fila de aprovação;
    - a permissão do Admin serve para acessar/aprovar;
    - a permissão do Admin não serve para recalcular o limite do desconto solicitado.
    """
    id_usuario = int(_assert_login() or 0)

    for atributo in (
        "BitAdmin",
        "IsAdmin",
        "EhAdmin",
        "Admin",
        "Administrador",
        "is_admin",
        "eh_admin",
    ):
        if _valor_booleano_verdadeiro(getattr(current_user, atributo, None)):
            return True

    for atributo in ("IDDimPermissoes", "id_permissao", "id_perfil"):
        try:
            if int(getattr(current_user, atributo, 0) or 0) == 1:
                return True
        except Exception:
            pass

    if id_usuario <= 0:
        return False

    if not _objeto_existe(TABELA_PERMISSOES_USUARIO):
        return False

    filtros = ["pu.IDDimUsuarios = :id_usuario"]

    if _coluna_existe(TABELA_PERMISSOES_USUARIO, "TipoAtribuicao"):
        filtros.append("UPPER(LTRIM(RTRIM(ISNULL(pu.TipoAtribuicao, 'CONCEDER')))) = 'CONCEDER'")

    if _coluna_existe(TABELA_PERMISSOES_USUARIO, "DataExpiracao"):
        filtros.append("(pu.DataExpiracao IS NULL OR pu.DataExpiracao >= GETDATE())")

    if _coluna_existe(TABELA_PERMISSOES_USUARIO, "BitAtivo"):
        filtros.append("ISNULL(pu.BitAtivo, 1) = 1")

    condicoes_admin = ["TRY_CONVERT(int, pu.IDDimPermissoes) = 1"]
    join_dim_permissoes = ""

    if _objeto_existe(TABELA_DIM_PERMISSOES):
        join_dim_permissoes = f"""
        LEFT JOIN {TABELA_DIM_PERMISSOES} p
            ON p.IDDimPermissoes = pu.IDDimPermissoes
        """

        for coluna in ("NomePermissao", "Nome", "Descricao", "Codigo", "Chave", "Slug"):
            if _coluna_existe(TABELA_DIM_PERMISSOES, coluna):
                condicoes_admin.append(f"""
                    UPPER(LTRIM(RTRIM(ISNULL(CONVERT(nvarchar(300), p.{coluna}), '')))) IN (
                        'ADMIN',
                        'ADMINISTRADOR',
                        'ADMINISTRADOR GERAL',
                        'ADMINISTRADOR DO SISTEMA',
                        'SUPER ADMIN',
                        'SUPERADMIN'
                    )
                    OR UPPER(LTRIM(RTRIM(ISNULL(CONVERT(nvarchar(300), p.{coluna}), '')))) LIKE '%ADMIN%'
                """)

    sql = text(f"""
        SELECT TOP (1) 1
        FROM {TABELA_PERMISSOES_USUARIO} pu
        {join_dim_permissoes}
        WHERE {' AND '.join(filtros)}
          AND (
                {' OR '.join(condicoes_admin)}
          );
    """)

    try:
        return bool(db.session.execute(sql, {"id_usuario": id_usuario}).scalar() or 0)
    except Exception:
        current_app.logger.exception(
            "APROVACAO_DESCONTO | falha ao validar permissão Admin | id_usuario=%s",
            id_usuario,
        )
        return False


def _exigir_admin_aprovacao_desconto() -> None:
    """Eu bloqueio a tela/API de aprovação para qualquer usuário que não seja Admin."""
    if not _usuario_logado_eh_admin_aprovacao_desconto():
        abort(403, "Somente Admin pode acessar a aprovação de descontos.")










def _resolver_id_empresa_proprietaria_movimento(id_kanban: int, id_empresa_padrao: int) -> int:
    """
    Regra de negócio:
    - se o kanban for o principal IDDimKanban = 1, o movimento deve ser gravado com IDEmpresaProprietaria = 3
    - caso contrário, uso a empresa proprietária padrão do card/usuário
    """
    if int(id_kanban or 0) == 1:
        return 3

    return int(id_empresa_padrao or 0)






def _sql_filtro_cards_nao_concluidos_no_quadro(alias_card: str = "c") -> str:
   
    return f"""
      AND NOT EXISTS (
            SELECT 1
            FROM {TABELA_KANBAN_FASE} f_final
            WHERE f_final.IDDimKanbanFase = {alias_card}.IDDimKanbanFaseAtual
              AND f_final.IDDimKanban = {alias_card}.IDDimKanban
              AND f_final.Ativo = 1
              AND (
                    LOWER(LTRIM(RTRIM(ISNULL(f_final.NomeFase, '')))) = 'concluido'
                 OR UPPER(LTRIM(RTRIM(ISNULL(f_final.TipoFase, '')))) = 'SUCESSO'
              )
        )
    """.rstrip()






def _obter_relacionamento_empresa_proprietaria(
    *,
    id_empresa: int | None,
    id_empresa_proprietaria: int,
) -> dict[str, Any] | None:
    if id_empresa in (None, "", 0):
        return None

    sql = text(f"""
        SELECT TOP (1)
            DimRelacionamentoEmpresa,
            IDEmpresa,
            IDEmpresaProprietaria,
            IDDimOrigemAtendimento,
            IDDimTipoCliente
        FROM {TABELA_RELACIONAMENTO_EMPRESA}
        WHERE IDEmpresa = :id_empresa
          AND IDEmpresaProprietaria = :id_empresa_proprietaria
        ORDER BY DimRelacionamentoEmpresa DESC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_empresa": int(id_empresa),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    return dict(row) if row else None





def _resolver_id_usuario_solicitante_desconto_card(
    *,
    id_card: int,
    id_usuario_fallback: int | None = None,
) -> int | None:
    """
    Eu resolvo qual usuário deve ser usado para validar o limite de desconto do card.

    Problema que esta função resolve:
    - o card pode ser salvo por um usuário;
    - a tela de aprovação pode ser aberta por outro;
    - mas o desconto precisa ser comparado contra o limite de quem solicitou/deu o desconto.

    Ordem de prioridade:
    1) usuário da pendência já existente em FatoAprovaPreco;
    2) usuário do último histórico em FatoKanbanNegociacaoPreco;
    3) usuário vinculado ao próprio card;
    4) usuário logado/fallback.

    Assim, se o usuário com limite de 5% der 15% de desconto,
    o sistema compara 15% contra 5%, e não contra o limite de um gestor com 99%.
    """
    try:
        id_card_int = int(id_card or 0)
    except Exception:
        id_card_int = 0

    try:
        id_usuario_fallback_int = int(id_usuario_fallback or 0)
    except Exception:
        id_usuario_fallback_int = 0

    if id_card_int <= 0:
        return id_usuario_fallback_int if id_usuario_fallback_int > 0 else None

    candidatos: list[int] = []

    def _adicionar_candidato(valor: object) -> None:
        try:
            valor_int = int(valor or 0)
            if valor_int > 0 and valor_int not in candidatos:
                candidatos.append(valor_int)
        except Exception:
            pass

    """
    1) Se já existir pendência em FatoAprovaPreco, ela é a fonte mais forte.
    Essa tabela representa justamente a solicitação pendente de aprovação.
    """
    try:
        if _objeto_existe(TABELA_CARD_APROVA_PRECO):
            sql_aprovacao = text(f"""
                SELECT TOP (1)
                    TRY_CONVERT(int, IDDimUsuarios) AS IDDimUsuarios
                FROM {TABELA_CARD_APROVA_PRECO}
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimUsuarios IS NOT NULL
                ORDER BY
                    CASE
                        WHEN PrecoAprovado IS NULL
                         AND DataAprovacaoPreco IS NULL
                        THEN 0
                        ELSE 1
                    END,
                    DataPrecoProposto DESC,
                    IDFatoAprovaPreco DESC;
            """)

            row_aprovacao = db.session.execute(
                sql_aprovacao,
                {"id_card": id_card_int},
            ).mappings().first()

            if row_aprovacao:
                _adicionar_candidato(row_aprovacao.get("IDDimUsuarios"))

    except Exception:
        current_app.logger.exception(
            "APROVACAO_DESCONTO | falha ao buscar usuário solicitante em FatoAprovaPreco | id_card=%s",
            id_card_int,
        )

    """
    2) Se não tiver pendência ainda, uso o último histórico de negociação de preço.
    Normalmente esse histórico carrega o usuário que fez a proposta.
    """
    try:
        if _objeto_existe(TABELA_CARD_NEGOCIACAO_PRECO):
            sql_negociacao = text(f"""
                SELECT TOP (1)
                    TRY_CONVERT(int, IDDimUsuarios) AS IDDimUsuarios
                FROM {TABELA_CARD_NEGOCIACAO_PRECO}
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimUsuarios IS NOT NULL
                ORDER BY
                    DataPrecoProposto DESC,
                    IDFatoKanbanNegociacaoPreco DESC;
            """)

            row_negociacao = db.session.execute(
                sql_negociacao,
                {"id_card": id_card_int},
            ).mappings().first()

            if row_negociacao:
                _adicionar_candidato(row_negociacao.get("IDDimUsuarios"))

    except Exception:
        current_app.logger.exception(
            "APROVACAO_DESCONTO | falha ao buscar usuário solicitante em FatoKanbanNegociacaoPreco | id_card=%s",
            id_card_int,
        )

    """
    3) Fallback pelo próprio card.
    Uso _coluna_existe para não quebrar caso alguma coluna não exista no schema físico.
    """
    try:
        colunas_select: list[str] = []

        if _coluna_existe(TABELA_CARD, "IDVendedorUsuario"):
            colunas_select.append("TRY_CONVERT(int, c.IDVendedorUsuario) AS IDVendedorUsuario")
        else:
            colunas_select.append("CAST(NULL AS int) AS IDVendedorUsuario")

        if _coluna_existe(TABELA_CARD, "IDDimUsuarios"):
            colunas_select.append("TRY_CONVERT(int, c.IDDimUsuarios) AS IDDimUsuarios")
        else:
            colunas_select.append("CAST(NULL AS int) AS IDDimUsuarios")

        if _coluna_existe(TABELA_CARD, "IDUsuarioCriacao"):
            colunas_select.append("TRY_CONVERT(int, c.IDUsuarioCriacao) AS IDUsuarioCriacao")
        else:
            colunas_select.append("CAST(NULL AS int) AS IDUsuarioCriacao")

        if _coluna_existe(TABELA_CARD, "IDUsuarioAtualizacao"):
            colunas_select.append("TRY_CONVERT(int, c.IDUsuarioAtualizacao) AS IDUsuarioAtualizacao")
        else:
            colunas_select.append("CAST(NULL AS int) AS IDUsuarioAtualizacao")

        if _coluna_existe(TABELA_CARD, "CriadoPorIDDimUsuarios"):
            colunas_select.append("TRY_CONVERT(int, c.CriadoPorIDDimUsuarios) AS CriadoPorIDDimUsuarios")
        else:
            colunas_select.append("CAST(NULL AS int) AS CriadoPorIDDimUsuarios")

        if _coluna_existe(TABELA_CARD, "AtualizadoPorIDDimUsuarios"):
            colunas_select.append("TRY_CONVERT(int, c.AtualizadoPorIDDimUsuarios) AS AtualizadoPorIDDimUsuarios")
        else:
            colunas_select.append("CAST(NULL AS int) AS AtualizadoPorIDDimUsuarios")

        sql_card = text(f"""
            SELECT TOP (1)
                {", ".join(colunas_select)}
            FROM {TABELA_CARD} c
            WHERE c.IDFatoKanbanCard = :id_card;
        """)

        row_card = db.session.execute(
            sql_card,
            {"id_card": id_card_int},
        ).mappings().first()

        if row_card:
            _adicionar_candidato(row_card.get("IDVendedorUsuario"))
            _adicionar_candidato(row_card.get("IDDimUsuarios"))
            _adicionar_candidato(row_card.get("IDUsuarioAtualizacao"))
            _adicionar_candidato(row_card.get("AtualizadoPorIDDimUsuarios"))
            _adicionar_candidato(row_card.get("IDUsuarioCriacao"))
            _adicionar_candidato(row_card.get("CriadoPorIDDimUsuarios"))

    except Exception:
        current_app.logger.exception(
            "APROVACAO_DESCONTO | falha ao buscar usuário solicitante no card | id_card=%s",
            id_card_int,
        )

 
 
    _adicionar_candidato(id_usuario_fallback_int)

    try:
        _adicionar_candidato(_obter_id_dim_usuario_logado())
    except Exception:
        pass

    if candidatos:
        return int(candidatos[0])

    return None
















def _obter_painel_por_codponto(cod_ponto: int | str | None) -> dict[str, Any] | None:
    if cod_ponto in (None, ""):
        return None

    try:
        cod_ponto_int = int(str(cod_ponto).strip())
    except (TypeError, ValueError):
        return None

    sql = text("""
        SELECT TOP 1
            p.IDDimPaineisEuromidia,
            p.CodPonto,
            p.Tipo,
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.CEP,
            p.QuantidadeFaces,
            p.BitAtivo
        FROM [Integracao].[Silver].[DimPaineisEuromidia] p
        WHERE p.CodPonto = :cod_ponto
        ORDER BY p.DataAtualizacao DESC, p.IDDimPaineisEuromidia DESC;
    """)
    row = db.session.execute(sql, {"cod_ponto": cod_ponto_int}).mappings().first()
    return dict(row) if row else None









def _obter_face_por_id(id_face: int | None) -> dict[str, Any] | None:
    if id_face in (None, "", 0):
        return None

    sql = text("""
        SELECT TOP 1
            f.IDDimFacesPaineis,
            f.CodPonto,
            f.Face,
            f.CodFace,
            f.Tipo,
            f.IDDimPaineisEuromidia
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE TRY_CONVERT(int, f.IDDimFacesPaineis) = TRY_CONVERT(int, :id_face)
        ORDER BY f.IDDimFacesPaineis DESC;
    """)
    row = db.session.execute(sql, {"id_face": int(id_face)}).mappings().first()
    return dict(row) if row else None











def _obter_face_por_codponto_codface(
    cod_ponto: int | str | None,
    cod_face: str | None,
) -> dict[str, Any] | None:
    if cod_ponto in (None, "") or cod_face in (None, ""):
        return None

    try:
        cod_ponto_int = int(str(cod_ponto).strip())
    except (TypeError, ValueError):
        return None

    cod_face_txt = _normalizar_texto(cod_face).upper()
    if not cod_face_txt:
        return None

    sql = text("""
        SELECT TOP 1
            f.IDDimFacesPaineis,
            f.CodPonto,
            f.Face,
            f.CodFace,
            f.Tipo,
            f.IDDimPaineisEuromidia
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE TRY_CONVERT(int, f.CodPonto) = TRY_CONVERT(int, :cod_ponto)
          AND UPPER(LTRIM(RTRIM(ISNULL(f.CodFace, '')))) = :cod_face
        ORDER BY f.IDDimFacesPaineis DESC;
    """)
    row = db.session.execute(
        sql,
        {
            "cod_ponto": cod_ponto_int,
            "cod_face": cod_face_txt,
        },
    ).mappings().first()
    return dict(row) if row else None










def _resolver_item_base_snapshot_preco_praticado(
    *,
    id_card: int,
    id_contrato_existente: int | None,
    cod_ponto_contrato: object = None,
    cod_face_contrato: object = None,
) -> dict[str, Any]:
    """
    Regra:
    1) tento resolver o item oficial do contrato;
    2) se não existir ainda, tento o item do snapshot de solicitação do próprio card;
    3) se ainda assim não achar, retorno motivo detalhado.
    """
    item_oficial = _obter_item_contrato_euromidia(
        id_contrato=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        cod_ponto=cod_ponto_contrato,
        cod_face=cod_face_contrato,
    )

    if item_oficial:
        return {
            "ok": True,
            "origem": "item_oficial_contrato",
            "item": dict(item_oficial),
            "possui_item_oficial": True,
        }

    snapshot_solicitacao = _obter_ultima_solicitacao_contrato_por_card(int(id_card))
    if snapshot_solicitacao and isinstance(snapshot_solicitacao, dict):
        cod_ponto_snapshot = str(snapshot_solicitacao.get("CodPonto") or "").strip()
        cod_face_snapshot = str(snapshot_solicitacao.get("CodFace") or "").strip().upper()
        cod_ponto_card = str(cod_ponto_contrato or "").strip()
        cod_face_card = str(cod_face_contrato or "").strip().upper()

        bate_ponto = (not cod_ponto_card) or (cod_ponto_snapshot == cod_ponto_card)
        bate_face = (not cod_face_card) or (cod_face_snapshot == cod_face_card)

        tem_minimo_item = any(
            snapshot_solicitacao.get(chave) not in (None, "", 0)
            for chave in (
                "IDPainelEuromidia",
                "IDDimFacesPaineis",
                "CodPonto",
                "CodFace",
            )
        )

        if tem_minimo_item and bate_ponto and bate_face:
            return {
                "ok": True,
                "origem": "item_snapshot_solicitacao",
                "item": dict(snapshot_solicitacao),
                "possui_item_oficial": False,
            }

    return {
        "ok": False,
        "motivo": "item_base_snapshot_nao_resolvido",
        "id_card": int(id_card),
        "id_contrato_existente": int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        "cod_ponto_contrato": str(cod_ponto_contrato or "").strip() or None,
        "cod_face_contrato": str(cod_face_contrato or "").strip().upper() or None,
    }









def _mover_aprovacao_preco_para_historico(
    *,
    id_aprova_preco: int,
    id_card: int,
    id_empresa_proprietaria: int,
    id_usuario_aprovador: int,
    preco_aprovado: Decimal,
    desconto_aprovado: Decimal | None,
    observacoes_aprovacao: str | None,
) -> dict[str, Any]:
    """
    Eu aprovo uma pendência de preço/desconto.

    Regra correta:
    - FatoAprovaPreco guarda somente pendências.
    - FatoKanbanNegociacaoPreco guarda o histórico de preços.
    - Ao aprovar, eu tento atualizar a linha histórica já criada no salvamento do card.
    - Se não existir histórico compatível, eu insiro uma linha.
    - Depois excluo a pendência de FatoAprovaPreco.
    - ObservacoesProposta no histórico aprovado fica como 'Desconto Aprovado'.
    """

    id_aprova_preco_int = int(id_aprova_preco)
    id_card_int = int(id_card)
    id_empresa_prop_int = int(id_empresa_proprietaria)
    id_usuario_aprovador_int = int(id_usuario_aprovador)

    preco_aprovado_dec = _valor_decimal(preco_aprovado)
    desconto_aprovado_dec = _valor_decimal(desconto_aprovado)

    if preco_aprovado_dec is None:
        raise ValueError("Preço aprovado inválido.")

    sql_buscar_pendencia = text(f"""
        SELECT TOP (1)
            *
        FROM {TABELA_CARD_APROVA_PRECO}
        WHERE IDFatoAprovaPreco = :id_aprova_preco
          AND IDFatoKanbanCard = :id_card
          AND IDEmpresaProprietaria = :id_empresa_proprietaria
          AND DataAprovacaoPreco IS NULL
          AND PrecoAprovado IS NULL;
    """)

    pendencia = db.session.execute(
        sql_buscar_pendencia,
        {
            "id_aprova_preco": id_aprova_preco_int,
            "id_card": id_card_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
        },
    ).mappings().first()

    if not pendencia:
        raise ValueError("Pendência de aprovação de preço não encontrada ou já aprovada.")

    pendencia_dict = dict(pendencia)

    id_painel = pendencia_dict.get("IDDimPaineisEuromidia")
    id_face = pendencia_dict.get("IDDimFacesPaineis")
    preco_proposto = _valor_decimal(pendencia_dict.get("PrecoProposto"))

    if desconto_aprovado_dec is None:
        desconto_aprovado_dec = _valor_decimal(pendencia_dict.get("DescontoProposto"))

    observacoes_aprovacao_txt = str(observacoes_aprovacao or "").strip()[:1000] or None

    sql_buscar_historico = text(f"""
        SELECT TOP (1)
            h.IDFatoKanbanNegociacaoPreco
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} h
        WHERE h.IDFatoKanbanCard = :id_card
          AND h.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ISNULL(h.IDDimPaineisEuromidia, 0) = ISNULL(:id_painel, 0)
          AND ISNULL(h.IDDimFacesPaineis, 0) = ISNULL(:id_face, 0)
        ORDER BY
            CASE
                WHEN h.DataAprovacaoPreco IS NULL
                 AND h.PrecoAprovado IS NULL
                THEN 0
                ELSE 1
            END,
            CASE
                WHEN :preco_proposto IS NOT NULL
                 AND h.PrecoProposto IS NOT NULL
                 AND ABS(
                        TRY_CONVERT(decimal(19, 4), h.PrecoProposto)
                        - TRY_CONVERT(decimal(19, 4), :preco_proposto)
                     ) <= 0.01
                THEN 0
                ELSE 1
            END,
            h.IDFatoKanbanNegociacaoPreco DESC;
    """)

    id_historico_existente = db.session.execute(
        sql_buscar_historico,
        {
            "id_card": id_card_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
            "id_painel": int(id_painel) if id_painel not in (None, "", 0) else None,
            "id_face": int(id_face) if id_face not in (None, "", 0) else None,
            "preco_proposto": preco_proposto,
        },
    ).scalar()

    acao_historico = None
    id_historico_final = None

    if id_historico_existente:
        sql_update_historico = text(f"""
            UPDATE {TABELA_CARD_NEGOCIACAO_PRECO}
               SET
                    IDDimUsuarios = COALESCE(IDDimUsuarios, :id_usuario_solicitante),
                    IDEmpresaProprietaria = :id_empresa_proprietaria,
                    IDDimTabelaPrecosEuromidia = COALESCE(:id_tabela_preco, IDDimTabelaPrecosEuromidia),
                    IDEmpresa = COALESCE(:id_empresa, IDEmpresa),
                    IDDimKanbanFase = COALESCE(:id_fase, IDDimKanbanFase),
                    IDDimKanbanStatusCard = COALESCE(:id_status_card, IDDimKanbanStatusCard),
                    IDFatoControleContratosEuromidia = COALESCE(:id_contrato, IDFatoControleContratosEuromidia),
                    BitAditivoContrato = COALESCE(:bit_aditivo, BitAditivoContrato),

                    ObservacoesProposta = :observacoes_proposta,

                    IDDimPaineisEuromidia = COALESCE(:id_painel, IDDimPaineisEuromidia),
                    IDDimFacesPaineis = COALESCE(:id_face, IDDimFacesPaineis),

                    CustoAtual = COALESCE(:custo_atual, CustoAtual),
                    PrecoAtual = COALESCE(:preco_atual, PrecoAtual),
                    MargemAtual = COALESCE(:margem_atual, MargemAtual),

                    CustoAtualRateado = COALESCE(:custo_atual_rateado, CustoAtualRateado),
                    PrecoAtualRateado = COALESCE(:preco_atual_rateado, PrecoAtualRateado),
                    MargemAtualRateado = COALESCE(:margem_atual_rateado, MargemAtualRateado),

                    DataPrecoProposto = COALESCE(DataPrecoProposto, :data_preco_proposto, GETDATE()),

                    CustoProposto = COALESCE(:custo_proposto, CustoProposto),
                    PrecoProposto = COALESCE(:preco_proposto, PrecoProposto),
                    MargemProposta = COALESCE(:margem_proposta, MargemProposta),

                    CustoPropostoRateado = COALESCE(:custo_proposto_rateado, CustoPropostoRateado),
                    PrecoPropostoRateado = COALESCE(:preco_proposto_rateado, PrecoPropostoRateado),

                    DescontoProposto = COALESCE(:desconto_proposto, DescontoProposto),
                    PeriodoInicio = COALESCE(:periodo_inicio, PeriodoInicio),
                    PeriodoTermino = COALESCE(:periodo_termino, PeriodoTermino),

                    IDDimUsuariosAprovacaoPreco = :id_usuario_aprovador,
                    DataAprovacaoPreco = GETDATE(),
                    PrecoAprovado = :preco_aprovado,
                    DescontoAprovado = :desconto_aprovado,
                    ObservacoesAprovacao = :observacoes_aprovacao,

                    BitAutorizacaoDiretoria = COALESCE(:bit_autorizacao_diretoria, BitAutorizacaoDiretoria),
                    BitAutorizacaoCoordenador = COALESCE(:bit_autorizacao_coordenador, BitAutorizacaoCoordenador)

             WHERE IDFatoKanbanNegociacaoPreco = :id_historico;
        """)

        db.session.execute(
            sql_update_historico,
            {
                "id_historico": int(id_historico_existente),
                "id_usuario_solicitante": pendencia_dict.get("IDDimUsuarios"),
                "id_empresa_proprietaria": id_empresa_prop_int,
                "id_tabela_preco": pendencia_dict.get("IDDimTabelaPrecosEuromidia"),
                "id_empresa": pendencia_dict.get("IDEmpresa"),
                "id_fase": pendencia_dict.get("IDDimKanbanFase"),
                "id_status_card": pendencia_dict.get("IDDimKanbanStatusCard"),
                "id_contrato": pendencia_dict.get("IDFatoControleContratosEuromidia"),
                "bit_aditivo": pendencia_dict.get("BitAditivoContrato"),
                "observacoes_proposta": "Desconto Aprovado",
                "id_painel": pendencia_dict.get("IDDimPaineisEuromidia"),
                "id_face": pendencia_dict.get("IDDimFacesPaineis"),
                "custo_atual": pendencia_dict.get("CustoAtual"),
                "preco_atual": pendencia_dict.get("PrecoAtual"),
                "margem_atual": pendencia_dict.get("MargemAtual"),
                "custo_atual_rateado": pendencia_dict.get("CustoAtualRateado"),
                "preco_atual_rateado": pendencia_dict.get("PrecoAtualRateado"),
                "margem_atual_rateado": pendencia_dict.get("MargemAtualRateado"),
                "data_preco_proposto": pendencia_dict.get("DataPrecoProposto"),
                "custo_proposto": pendencia_dict.get("CustoProposto"),
                "preco_proposto": pendencia_dict.get("PrecoProposto"),
                "margem_proposta": pendencia_dict.get("MargemProposta"),
                "custo_proposto_rateado": pendencia_dict.get("CustoPropostoRateado"),
                "preco_proposto_rateado": pendencia_dict.get("PrecoPropostoRateado"),
                "desconto_proposto": pendencia_dict.get("DescontoProposto"),
                "periodo_inicio": pendencia_dict.get("PeriodoInicio"),
                "periodo_termino": pendencia_dict.get("PeriodoTermino"),
                "id_usuario_aprovador": id_usuario_aprovador_int,
                "preco_aprovado": preco_aprovado_dec,
                "desconto_aprovado": desconto_aprovado_dec,
                "observacoes_aprovacao": observacoes_aprovacao_txt,
                "bit_autorizacao_diretoria": pendencia_dict.get("BitAutorizacaoDiretoria"),
                "bit_autorizacao_coordenador": pendencia_dict.get("BitAutorizacaoCoordenador"),
            },
        )

        id_historico_final = int(id_historico_existente)
        acao_historico = "atualizado"

    else:
        sql_insert_historico = text(f"""
            INSERT INTO {TABELA_CARD_NEGOCIACAO_PRECO}
            (
                IDDimUsuarios,
                IDEmpresaProprietaria,
                IDDimTabelaPrecosEuromidia,
                IDEmpresa,
                IDFatoKanbanCard,
                IDDimKanbanFase,
                IDDimKanbanStatusCard,
                IDFatoControleContratosEuromidia,
                BitAditivoContrato,
                ObservacoesProposta,
                IDDimPaineisEuromidia,
                IDDimFacesPaineis,
                CustoAtual,
                PrecoAtual,
                MargemAtual,
                CustoAtualRateado,
                PrecoAtualRateado,
                MargemAtualRateado,
                DataPrecoProposto,
                CustoProposto,
                PrecoProposto,
                MargemProposta,
                CustoPropostoRateado,
                PrecoPropostoRateado,
                DescontoProposto,
                PeriodoInicio,
                PeriodoTermino,
                IDDimUsuariosAprovacaoPreco,
                DataAprovacaoPreco,
                PrecoAprovado,
                DescontoAprovado,
                ObservacoesAprovacao,
                BitAutorizacaoDiretoria,
                BitAutorizacaoCoordenador
            )
            OUTPUT INSERTED.IDFatoKanbanNegociacaoPreco
            SELECT
                IDDimUsuarios,
                IDEmpresaProprietaria,
                IDDimTabelaPrecosEuromidia,
                IDEmpresa,
                IDFatoKanbanCard,
                IDDimKanbanFase,
                IDDimKanbanStatusCard,
                IDFatoControleContratosEuromidia,
                BitAditivoContrato,
                :observacoes_proposta,
                IDDimPaineisEuromidia,
                IDDimFacesPaineis,
                CustoAtual,
                PrecoAtual,
                MargemAtual,
                CustoAtualRateado,
                PrecoAtualRateado,
                MargemAtualRateado,
                COALESCE(DataPrecoProposto, GETDATE()),
                CustoProposto,
                PrecoProposto,
                MargemProposta,
                CustoPropostoRateado,
                PrecoPropostoRateado,
                DescontoProposto,
                PeriodoInicio,
                PeriodoTermino,
                :id_usuario_aprovador,
                GETDATE(),
                :preco_aprovado,
                :desconto_aprovado,
                :observacoes_aprovacao,
                BitAutorizacaoDiretoria,
                BitAutorizacaoCoordenador
            FROM {TABELA_CARD_APROVA_PRECO}
            WHERE IDFatoAprovaPreco = :id_aprova_preco
              AND IDFatoKanbanCard = :id_card
              AND IDEmpresaProprietaria = :id_empresa_proprietaria;
        """)

        id_historico_final = db.session.execute(
            sql_insert_historico,
            {
                "id_aprova_preco": id_aprova_preco_int,
                "id_card": id_card_int,
                "id_empresa_proprietaria": id_empresa_prop_int,
                "id_usuario_aprovador": id_usuario_aprovador_int,
                "preco_aprovado": preco_aprovado_dec,
                "desconto_aprovado": desconto_aprovado_dec,
                "observacoes_aprovacao": observacoes_aprovacao_txt,
                "observacoes_proposta": "Desconto Aprovado",
            },
        ).scalar()

        acao_historico = "inserido"

    sql_delete_pendencia = text(f"""
        DELETE FROM {TABELA_CARD_APROVA_PRECO}
        WHERE IDFatoAprovaPreco = :id_aprova_preco
          AND IDFatoKanbanCard = :id_card
          AND IDEmpresaProprietaria = :id_empresa_proprietaria;
    """)

    db.session.execute(
        sql_delete_pendencia,
        {
            "id_aprova_preco": id_aprova_preco_int,
            "id_card": id_card_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
        },
    )

    return {
        "ok": True,
        "id_aprova_preco_removido": id_aprova_preco_int,
        "id_historico_negociacao_preco": int(id_historico_final) if id_historico_final else None,
        "acao_historico": acao_historico,
        "observacoes_proposta": "Desconto Aprovado",
    }






def _buscar_snapshot_preco_praticado_para_upsert(
    *,
    id_item_contrato: int | None,
    id_card: int | None,
    id_painel: int | None,
    id_face: int | None,
    id_contrato: int | None = None,
) -> dict[str, Any] | None:
    if not _objeto_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO):
        return None

    if id_item_contrato not in (None, "", 0):
        row_item = _buscar_snapshot_preco_praticado_por_item_contrato(int(id_item_contrato))
        if row_item:
            return dict(row_item)

    if id_card in (None, "", 0) or id_painel in (None, "", 0) or id_face in (None, "", 0):
        return None

    filtros = [
        "TRY_CONVERT(int, IDFatoKanbanCard) = TRY_CONVERT(int, :id_card)",
        "TRY_CONVERT(int, IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)",
        "TRY_CONVERT(int, IDDimFacesPaineis) = TRY_CONVERT(int, :id_face)",
    ]
    params: dict[str, object] = {
        "id_card": int(id_card),
        "id_painel": int(id_painel),
        "id_face": int(id_face),
    }

    if id_contrato not in (None, "", 0) and _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "IDFatoControleContratosEuromidia"):
        filtros.append("TRY_CONVERT(int, ISNULL(IDFatoControleContratosEuromidia, 0)) = TRY_CONVERT(int, :id_contrato)")
        params["id_contrato"] = int(id_contrato)

    sql = text(
        f"""
        SELECT TOP (1)
            *
        FROM {TABELA_CONTRATO_ITEM_PRECO_PRATICADO}
        WHERE {' AND '.join(filtros)}
        ORDER BY
            CASE WHEN IDFatoControleContratosItensEuromidia IS NULL THEN 1 ELSE 0 END,
            [{COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO}] DESC;
        """
    )

    row = db.session.execute(sql, params).mappings().first()
    return dict(row) if row else None

















def _garantir_relacionamento_empresa_tipo_cliente(
    *,
    id_empresa: int | None,
    id_empresa_proprietaria: int,
    id_dim_tipo_cliente: int | None,
    id_dim_origem_atendimento: int | None = None,
) -> dict[str, Any]:
    """
    Regra:
    - procura relacionamento por IDEmpresa + IDEmpresaProprietaria;
    - se existir, atualiza IDDimTipoCliente;
    - se não existir, cria;
    - nunca duplica;
    - só altera a linha da empresa proprietária informada.
    """
    if id_empresa in (None, "", 0):
        return {
            "ok": False,
            "criado": False,
            "atualizado": False,
            "ja_existia": False,
            "motivo": "id_empresa_invalido",
        }

    id_empresa_int = int(id_empresa)
    id_empresa_prop_int = int(id_empresa_proprietaria)
    id_tipo_int = int(id_dim_tipo_cliente) if id_dim_tipo_cliente not in (None, "", 0) else None
    id_origem_int = int(id_dim_origem_atendimento) if id_dim_origem_atendimento not in (None, "", 0) else None

    relacionamento_atual = _obter_relacionamento_empresa_proprietaria(
        id_empresa=id_empresa_int,
        id_empresa_proprietaria=id_empresa_prop_int,
    )

    if relacionamento_atual:
        sql_update = text(f"""
            UPDATE {TABELA_RELACIONAMENTO_EMPRESA}
            SET
                IDDimTipoCliente = :id_dim_tipo_cliente,
                IDDimOrigemAtendimento = COALESCE(:id_dim_origem_atendimento, IDDimOrigemAtendimento)
            WHERE DimRelacionamentoEmpresa = :id_relacionamento;
        """)

        db.session.execute(
            sql_update,
            {
                "id_dim_tipo_cliente": id_tipo_int,
                "id_dim_origem_atendimento": id_origem_int,
                "id_relacionamento": int(relacionamento_atual["DimRelacionamentoEmpresa"]),
            },
        )

        return {
            "ok": True,
            "criado": False,
            "atualizado": True,
            "ja_existia": True,
            "motivo": "relacionamento_atualizado",
            "DimRelacionamentoEmpresa": int(relacionamento_atual["DimRelacionamentoEmpresa"]),
            "IDDimTipoCliente": id_tipo_int,
        }

    sql_insert = text(f"""
        INSERT INTO {TABELA_RELACIONAMENTO_EMPRESA} (
            IDEmpresa,
            IDEmpresaProprietaria,
            IDDimOrigemAtendimento,
            IDDimTipoCliente
        )
        VALUES (
            :id_empresa,
            :id_empresa_proprietaria,
            :id_dim_origem_atendimento,
            :id_dim_tipo_cliente
        );
    """)

    db.session.execute(
        sql_insert,
        {
            "id_empresa": id_empresa_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
            "id_dim_origem_atendimento": id_origem_int,
            "id_dim_tipo_cliente": id_tipo_int,
        },
    )

    return {
        "ok": True,
        "criado": True,
        "atualizado": False,
        "ja_existia": False,
        "motivo": "relacionamento_criado",
        "IDDimTipoCliente": id_tipo_int,
    }














def _obter_id_dim_usuario_logado() -> int | None:
    candidatos = [
        getattr(current_user, "IDDimUsuarios", None),
        getattr(current_user, "id", None),
    ]

    for valor in candidatos:
        try:
            valor_int = int(valor)
            if valor_int > 0:
                return valor_int
        except Exception:
            pass

    if hasattr(current_user, "get_id"):
        try:
            valor_int = int(current_user.get_id())
            if valor_int > 0:
                return valor_int
        except Exception:
            pass

    return None




def _obter_id_empresa_proprietaria_usuario_logado() -> int | None:
    candidatos = [
        getattr(current_user, "IDEmpresaProprietaria", None),
        getattr(current_user, "id_empresa_proprietaria", None),
    ]

    for valor in candidatos:
        try:
            valor_int = int(valor)
            if valor_int > 0:
                return valor_int
        except Exception:
            pass

    return None








def _normalizar_tipo_contrato_card(valor: object) -> str | None:
    texto = _normalizar_texto_comparacao(valor)

    mapa = {
        "aditivo": TIPO_SOLICITACAO_ADITIVO,
        "novo contrato": TIPO_SOLICITACAO_NOVO,
        "novo_contrato": TIPO_SOLICITACAO_NOVO,
        "novocontrato": TIPO_SOLICITACAO_NOVO,
    }
    return mapa.get(texto)












def _montar_campos_empresas_relacionadas_card_sql(
    empresas_relacionadas_card: Mapping[str, Any] | dict[str, Any] | None,
    *,
    id_tipo_cliente: Any | None = None,
) -> dict[str, Any]:
    """
    Eu monto os campos dinâmicos do card ligados à estrutura de empresas relacionadas.

    Regras:
    - calculo os bits a partir do id_tipo_cliente final já resolvido no fluxo;
    - persisto apenas campos de empresas relacionadas;
    - NÃO persisto IDDimTipoCliente aqui para evitar duplicidade e ambiguidade;
    - o IDDimTipoCliente deve ser salvo diretamente na api_card_atualizar.
    """
    dados = dict(empresas_relacionadas_card or {})
    id_tipo_cliente_final = _int_ou_none(id_tipo_cliente)
    mapa_bits_tipo_cliente = _montar_bits_tipo_cliente_desconto(id_tipo_cliente_final)

    campos: list[str] = []
    parametros: dict[str, Any] = {}

    if _coluna_existe(TABELA_CARD, "BitClienteDireto"):
        campos.append("BitClienteDireto = :bit_cliente_direto")
        parametros["bit_cliente_direto"] = int(mapa_bits_tipo_cliente.get("BitClienteDireto") or 0)

    if _coluna_existe(TABELA_CARD, "BitAgencia"):
        campos.append("BitAgencia = :bit_agencia")
        parametros["bit_agencia"] = int(mapa_bits_tipo_cliente.get("BitAgencia") or 0)

    if _coluna_existe(TABELA_CARD, "BitPlanejador"):
        campos.append("BitPlanejador = :bit_planejador")
        parametros["bit_planejador"] = int(mapa_bits_tipo_cliente.get("BitPlanejador") or 0)

    if _coluna_existe(TABELA_CARD, "IDEmpresaAgencia"):
        campos.append("IDEmpresaAgencia = :id_empresa_agencia_card")
        parametros["id_empresa_agencia_card"] = _int_ou_none(dados.get("id_empresa_agencia_card"))

    if _coluna_existe(TABELA_CARD, "IDEmpresaBureau"):
        campos.append("IDEmpresaBureau = :id_empresa_bureau_card")
        parametros["id_empresa_bureau_card"] = _int_ou_none(dados.get("id_empresa_bureau_card"))

    return {
        "campos": campos,
        "parametros": parametros,
    }







def _sincronizar_item_oficial_contrato_com_card_salvo(
    *,
    id_card: int,
    id_empresa_relacionada: int | None,
    id_contrato_existente: int | None,
    cod_ponto_contrato: object = None,
    cod_face_contrato: object = None,
    contexto_erro: str = "salvar card",
) -> dict[str, object]:
    """
    Eu sincronizo imediatamente o item oficial do contrato com o card salvo.

    Regra:
    - se houver contrato + CodPonto + CodFace no card, eu atualizo
      FatoControleContratosItensEuromidia.IDFatoKanbanCard na hora;
    - se não houver vínculo completo, eu não erro: apenas informo o motivo;
    - se houver vínculo completo e nenhuma linha for atualizada, eu erro,
      porque isso indica divergência de contrato/ponto/face.
    """
    resultado = {
        "sincronizado": False,
        "quantidade_itens_controle_contrato_atualizados": 0,
        "motivo_itens_controle_contrato": None,
    }

    if id_contrato_existente in (None, "", 0):
        resultado["motivo_itens_controle_contrato"] = "card_sem_contrato"
        return resultado

    cod_ponto_txt = str(cod_ponto_contrato or "").strip()
    if not cod_ponto_txt:
        resultado["motivo_itens_controle_contrato"] = "card_sem_cod_ponto_contrato"
        return resultado

    cod_face_txt = str(cod_face_contrato or "").strip().upper()
    if not cod_face_txt:
        resultado["motivo_itens_controle_contrato"] = "card_sem_cod_face_contrato"
        return resultado

    quantidade_itens_controle_contrato_atualizados = _atualizar_card_nos_itens_contrato_euromidia(
        id_empresa=int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
        id_contrato=int(id_contrato_existente),
        cod_ponto=cod_ponto_txt,
        cod_face=cod_face_txt,
        id_card=int(id_card),
    )

    resultado["sincronizado"] = True
    resultado["quantidade_itens_controle_contrato_atualizados"] = int(
        quantidade_itens_controle_contrato_atualizados or 0
    )

    if int(quantidade_itens_controle_contrato_atualizados or 0) <= 0:
        raise RuntimeError(
            "Nenhum item de FatoControleContratosItensEuromidia foi atualizado com o "
            f"IDFatoKanbanCard ao {contexto_erro}. "
            "Verifique contrato + CodPonto + CodFace gravados no card."
        )

    return resultado












def _garantir_relacionamento_empresa_proprietaria(
    *,
    id_empresa: int | None,
    id_empresa_proprietaria: int,
) -> dict[str, object]:
    """
    Garante que exista o relacionamento IDEmpresa -> IDEmpresaProprietaria
    na tabela Silver.DimRelacionamentoEmpresa.

    Regra:
    - se já existir, não duplica;
    - se não existir, cria;
    - retorna um resumo do que aconteceu.
    """
    if id_empresa in (None, "", 0):
        return {
            "ok": False,
            "criado": False,
            "ja_existia": False,
            "motivo": "id_empresa_invalido",
        }

    id_empresa_int = int(id_empresa)
    id_empresa_prop_int = int(id_empresa_proprietaria)

    sql_existe = text(f"""
        SELECT TOP (1) 1 AS Existe
        FROM {TABELA_RELACIONAMENTO_EMPRESA}
        WHERE IDEmpresa = :id_empresa
          AND IDEmpresaProprietaria = :id_empresa_proprietaria;
    """)

    existe = db.session.execute(
        sql_existe,
        {
            "id_empresa": id_empresa_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
        },
    ).scalar()

    if existe:
        return {
            "ok": True,
            "criado": False,
            "ja_existia": True,
            "motivo": "relacionamento_ja_existia",
        }

    sql_insert = text(f"""
        INSERT INTO {TABELA_RELACIONAMENTO_EMPRESA} (
            IDEmpresa,
            IDEmpresaProprietaria
        )
        VALUES (
            :id_empresa,
            :id_empresa_proprietaria
        );
    """)

    db.session.execute(
        sql_insert,
        {
            "id_empresa": id_empresa_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
        },
    )

    return {
        "ok": True,
        "criado": True,
        "ja_existia": False,
        "motivo": "relacionamento_criado",
    }












def _obter_tag_tipo_contrato(id_kanban: int, tipo_contrato: str) -> dict[str, object] | None:
    tipo_norm = _normalizar_tipo_contrato_card(tipo_contrato)

    if tipo_norm == TIPO_SOLICITACAO_ADITIVO:
        return (
            _obter_tag_por_nome(id_kanban, NOME_TAG_TIPO_CONTRATO_ADITIVO, somente_ativa=True)
            or {
                "IDDimKanbanTag": ID_TAG_TIPO_CONTRATO_ADITIVO,
                "NomeTag": NOME_TAG_TIPO_CONTRATO_ADITIVO,
            }
        )

    if tipo_norm == TIPO_SOLICITACAO_NOVO:
        return (
            _obter_tag_por_nome(id_kanban, NOME_TAG_TIPO_CONTRATO_NOVO, somente_ativa=True)
            or {
                "IDDimKanbanTag": ID_TAG_TIPO_CONTRATO_NOVO,
                "NomeTag": NOME_TAG_TIPO_CONTRATO_NOVO,
            }
        )

    return None







def _resolver_contexto_tipo_contrato_payload(
    *,
    id_empresa: object,
    id_contrato_existente: object,
    tipo_contrato_card: object,
) -> dict[str, object]:
    """
    Regras:
    1) sem contrato selecionado => sempre NOVO_CONTRATO
    2) com contrato selecionado e tipo vazio => default ADITIVO
    3) com contrato selecionado o usuário pode trocar para NOVO_CONTRATO
    """
    id_empresa_int = int(id_empresa) if id_empresa not in (None, "", 0) else None
    id_contrato_int = int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None
    tipo_norm = _normalizar_tipo_contrato_card(tipo_contrato_card)

    if id_contrato_int is None:
        tipo_norm = TIPO_SOLICITACAO_NOVO
    elif tipo_norm is None:
        tipo_norm = TIPO_SOLICITACAO_ADITIVO

    return {
        "id_empresa": id_empresa_int,
        "id_contrato_existente": id_contrato_int,
        "tipo_contrato": tipo_norm,
        "bit_aditivo": 1 if tipo_norm == TIPO_SOLICITACAO_ADITIVO else 0,
        "bit_contrato_novo": 1 if tipo_norm == TIPO_SOLICITACAO_NOVO else 0,
    }









def _anexar_campos_vinculo_contrato_card(
    *,
    campos_sql: list[str],
    parametros: dict[str, object],
    id_contrato_existente: object,
    cod_ponto_contrato: object,
    cod_face_contrato: object,
    prefixo_parametros: str = "",
) -> None:
    """
    Eu anexo ao INSERT/UPDATE do card os campos persistentes do vínculo
    com contrato existente, ponto e face, quando essas colunas existirem
    fisicamente na tabela do card.
    """
    id_contrato_int = int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None
    cod_ponto_limpo = str(cod_ponto_contrato or "").strip() or None
    cod_face_limpo = str(cod_face_contrato or "").strip().upper() or None

    nome_param_id_contrato = f"{prefixo_parametros}id_contrato_vinculado"
    nome_param_cod_ponto = f"{prefixo_parametros}cod_ponto_contrato"
    nome_param_cod_face = f"{prefixo_parametros}cod_face_contrato"

    if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia"):
        campos_sql.append(f"IDFatoControleContratosEuromidia = :{nome_param_id_contrato}")
        parametros[nome_param_id_contrato] = id_contrato_int

    elif _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia"):
        campos_sql.append(f"IDFatoControleContratoEuromidia = :{nome_param_id_contrato}")
        parametros[nome_param_id_contrato] = id_contrato_int

    if _coluna_existe(TABELA_CARD, "CodPontoContrato"):
        campos_sql.append(f"CodPontoContrato = :{nome_param_cod_ponto}")
        parametros[nome_param_cod_ponto] = cod_ponto_limpo

    if _coluna_existe(TABELA_CARD, "CodFaceContrato"):
        campos_sql.append(f"CodFaceContrato = :{nome_param_cod_face}")
        parametros[nome_param_cod_face] = cod_face_limpo








def _obter_item_contrato_euromidia(
    *,
    id_contrato: int | None,
    cod_ponto: object = None,
    cod_face: object = None,
) -> dict[str, Any] | None:
    """
    Eu resolvo o item do contrato selecionado a partir do contrato, CodPonto e CodFace.

    Regras:
    - sem contrato => retorno None
    - com contrato e sem CodPonto/CodFace => retorno apenas o cabeçalho lógico do contrato
    - com contrato + CodPonto + CodFace => retorno o item específico do contrato
    - com contrato + CodPonto + Novo Painel => não existe item antigo, então retorno None para o item
    """
    if id_contrato in (None, "", 0):
        return None

    id_contrato_int = int(id_contrato)
    cod_ponto_limpo = str(cod_ponto or "").strip()
    cod_face_limpo = str(cod_face or "").strip().upper()

    if not cod_ponto_limpo or _normalizar_texto_comparacao(cod_ponto_limpo) == _normalizar_texto_comparacao(VALOR_OPCAO_NOVO_PAINEL):
        return None

    if not cod_face_limpo or _normalizar_texto_comparacao(cod_face_limpo) == _normalizar_texto_comparacao(VALOR_OPCAO_NOVO_PAINEL):
        return None

    sql = text(f"""
        SELECT TOP (1)
            i.IDFatoControleContratosItensEuromidia,
            i.IDFatoControleContratoEuromidia,
            i.CodPonto,
            i.CodFace,
            i.NumeroContrato,
            i.NumeroPrevia,
            i.Referencia,
            i.CNPJ,
            i.RazaoSocial,
            i.MarcaExibida,
            i.Vendedor,
            i.TipoDocumento,
            i.Origem,
            i.SDR,
            i.Agencia,
            i.CnpjAgencia,
            i.Bureau,
            i.CnpjBureau,
            i.Intermediario,
            i.CnpjIntermediario,
            i.DataAssinaturaRenovacao,
            i.IDTrimestre,
            i.DataLancamento,
            i.Cota,
            i.CidadeExibicao,
            i.Tipo,
            i.EmpresaEuro,
            i.CnpjExibibora,
            i.TexmpoExposicao,
            i.DataInicioPrevisto,
            i.DataTerminoPrevisto,
            i.InicioRenovacao,
            i.FaturamentoBrutoMensal,
            i.PercentualPermuta,
            i.CotaOportunidade,
            i.ValorPermuta,
            i.FaturamentoLiquidoPermuta,
            i.NumeroParcelas,
            i.DataInicioVencimento,
            i.TotalBrutoContrato,
            i.TotalLiquidoContratoAGBRCTACORDO,
            i.TotalLiquidoContratoAGBRVENDGERCOOR,
            i.PercentualAgencia,
            i.ValorMensalAgencia,
            i.PercentualBureau,
            i.ValorBureauMensal,
            i.PercentualCartaAcordo,
            i.ValorCartaAcordoMensal,
            i.ValorOutrasComissoes,
            i.FaturamentoLiquidoMensal,
            i.PercentualComissaoVendedor,
            i.ValorVendedor,
            i.ValorVendedorTotal,
            i.PercentualComissaoCoordenacao,
            i.ValorCoordenador,
            i.ValorCoordenadorTotal,
            i.PercentualComissaoGerencia,
            i.ValorGerencia,
            i.ValorGerenciaTotal,
            i.AtivoCancelamento,
            i.FaturamentoLiquidoFinalMensal,
            i.ComissaoGerenciaNordeste,
            i.Faturamento,
            i.DataCancelamento,
            i.OBS,
            i.DataFimEfetiva,
            i.Status,
            i.IDPainelEuromidia,
            i.IDDimFacesPaineis
        FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
        WHERE i.IDFatoControleContratoEuromidia = :id_contrato
          AND LTRIM(RTRIM(ISNULL(i.CodPonto, ''))) = :cod_ponto
          AND UPPER(LTRIM(RTRIM(ISNULL(i.CodFace, '')))) = :cod_face
          AND ISNULL(i.BitAtivo, 1) = 1
        ORDER BY i.IDFatoControleContratosItensEuromidia DESC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_contrato": id_contrato_int,
            "cod_ponto": cod_ponto_limpo,
            "cod_face": cod_face_limpo,
        },
    ).mappings().first()

    return dict(row) if row else None










def _registrar_log_contrato_card_euromidia(
    *,
    id_contrato: int | None,
    id_item_contrato: int | None,
    id_card: int | None = None,
    id_usuario: int | None = None,
    evento: str | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    """
    Eu gravo o vínculo/log solicitado em FatoContratoCardEuromidia
    somente quando houver contrato selecionado no fluxo de avaliação.

    Observação:
    - mantenho 'evento' e 'payload' por compatibilidade com chamadas antigas;
    - se o id_usuario vier vazio, eu tento resolver pelo usuário logado.
    """
    if not _objeto_existe(TABELA_CONTRATO_CARD_EUROMIDIA):
        return

    if id_contrato in (None, "", 0):
        return

    id_usuario_resolvido = None

    try:
        if id_usuario not in (None, "", 0):
            id_usuario_resolvido = int(id_usuario)
    except Exception:
        id_usuario_resolvido = None

    if id_usuario_resolvido in (None, 0):
        try:
            id_usuario_resolvido = _obter_id_dim_usuario_logado()
        except Exception:
            id_usuario_resolvido = None

    _inserir_registro_dinamico(
        TABELA_CONTRATO_CARD_EUROMIDIA,
        {
            "IDFatoControleContratosEuromidia": int(id_contrato),
            "IDFatoControleContratosItensEuromidia": (
                int(id_item_contrato) if id_item_contrato not in (None, "", 0) else None
            ),
            "IDFatoKanbanCard": (
                int(id_card) if id_card not in (None, "", 0) else None
            ),
            "IDDimUsuarios": (
                int(id_usuario_resolvido) if id_usuario_resolvido not in (None, "", 0) else None
            ),
        },
        colunas_getdate=("DataAtualizacao",),
    )



def _validar_contrato_empresa(
    *,
    id_empresa: int | None,
    id_contrato_existente: int | None,
) -> dict[str, object] | None:
    if not id_contrato_existente:
        return None

    if not id_empresa:
        raise ValueError("Selecione a empresa antes de escolher um contrato existente.")

    sql_empresa = text(f"""
        SELECT TOP (1)
            e.IDEmpresa,
            e.CNPJ
        FROM {TABELA_EMPRESAS} e
        WHERE e.IDEmpresa = :id_empresa;
    """)

    empresa = db.session.execute(
        sql_empresa,
        {"id_empresa": int(id_empresa)},
    ).mappings().first()

    if not empresa:
        raise ValueError("Empresa selecionada não foi encontrada.")

    cnpj_empresa = str(empresa.get("CNPJ") or "").strip()

    sql = text(f"""
        SELECT TOP (1)
            c.IDFatoControleContratosEuromidia,
            c.IDEmpresa,
            c.NumeroContrato,
            c.NumeroPrevia,
            c.Referencia,
            c.RazaoSocial,
            c.CNPJ,
            c.MarcaExibida,
            c.BitAtivo,
            c.IDDimStatusContratos
        FROM {TABELA_CONTROLE_CONTRATOS} c
        WHERE c.IDFatoControleContratosEuromidia = :id_contrato
          AND ISNULL(c.BitAtivo, 1) = 1
          AND (
                c.IDEmpresa = :id_empresa
                OR (:cnpj_empresa <> '' AND LTRIM(RTRIM(ISNULL(c.CNPJ, ''))) = :cnpj_empresa)
          );
    """)

    row = db.session.execute(
        sql,
        {
            "id_contrato": int(id_contrato_existente),
            "id_empresa": int(id_empresa),
            "cnpj_empresa": cnpj_empresa,
        },
    ).mappings().first()

    if not row:
        raise ValueError("O contrato selecionado não pertence à empresa informada ou não está ativo.")

    return dict(row)









def _validar_ponto_face_contrato(
    *,
    id_contrato_existente: int | None,
    cod_ponto: object,
    cod_face: object,
) -> dict[str, object]:
    cod_ponto_limpo = str(cod_ponto or "").strip()
    cod_face_limpo = str(cod_face or "").strip().upper()

    if not id_contrato_existente:
        return {
            "cod_ponto": None,
            "cod_face": None,
        }

    if cod_ponto_limpo and _normalizar_texto_comparacao(cod_ponto_limpo) != _normalizar_texto_comparacao(VALOR_OPCAO_NOVO_PAINEL):
        sql_ponto = text(f"""
            SELECT TOP (1) 1
            FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
            WHERE i.IDFatoControleContratoEuromidia = :id_contrato
              AND LTRIM(RTRIM(ISNULL(i.CodPonto, ''))) = :cod_ponto
              AND ISNULL(i.BitAtivo, 1) = 1;
        """)
        existe_ponto = db.session.execute(
            sql_ponto,
            {
                "id_contrato": int(id_contrato_existente),
                "cod_ponto": cod_ponto_limpo,
            },
        ).scalar()

        if not existe_ponto:
            raise ValueError("O CodPonto selecionado não pertence ao contrato informado.")

    if cod_face_limpo and _normalizar_texto_comparacao(cod_face_limpo) != _normalizar_texto_comparacao(VALOR_OPCAO_NOVO_PAINEL):
        if not cod_ponto_limpo:
            raise ValueError("Selecione o CodPonto antes de escolher a face do contrato.")

        sql_face = text(f"""
            SELECT TOP (1) 1
            FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
            WHERE i.IDFatoControleContratoEuromidia = :id_contrato
              AND LTRIM(RTRIM(ISNULL(i.CodPonto, ''))) = :cod_ponto
              AND UPPER(LTRIM(RTRIM(ISNULL(i.CodFace, '')))) = :cod_face
              AND ISNULL(i.BitAtivo, 1) = 1;
        """)
        existe_face = db.session.execute(
            sql_face,
            {
                "id_contrato": int(id_contrato_existente),
                "cod_ponto": cod_ponto_limpo,
                "cod_face": cod_face_limpo,
            },
        ).scalar()

        if not existe_face:
            raise ValueError("A CodFace selecionada não pertence ao CodPonto/contrato informado.")

    return {
        "cod_ponto": cod_ponto_limpo or None,
        "cod_face": cod_face_limpo or None,
    }













def _sincronizar_tipo_contrato_card(
    *,
    id_card: int,
    id_kanban: int,
    id_fase_atual: int | None,
    id_usuario: int,
    id_empresa_proprietaria: int,
    tipo_contrato: str,
    aplicar_tags: bool = True,
) -> dict[str, object]:
    """
    Regras:
    - ADITIVO => BitAditivo=1, BitContratoNovo=0, tag Aditivo ativa, tag Novo Contrato removida
    - NOVO_CONTRATO => BitAditivo=0, BitContratoNovo=1, tag Novo Contrato ativa, tag Aditivo removida
    """
    tipo_norm = _normalizar_tipo_contrato_card(tipo_contrato)
    if tipo_norm not in {TIPO_SOLICITACAO_ADITIVO, TIPO_SOLICITACAO_NOVO}:
        raise ValueError("Tipo de contrato inválido.")

    tag_desejada = _obter_tag_tipo_contrato(id_kanban, tipo_norm)
    tag_oposta = _obter_tag_tipo_contrato(
        id_kanban,
        TIPO_SOLICITACAO_NOVO if tipo_norm == TIPO_SOLICITACAO_ADITIVO else TIPO_SOLICITACAO_ADITIVO,
    )

    campos_update: list[str] = []
    parametros_update: dict[str, object] = {"id_card": int(id_card)}

    if _coluna_existe(TABELA_CARD, "BitAditivo"):
        campos_update.append("BitAditivo = :bit_aditivo")
        parametros_update["bit_aditivo"] = 1 if tipo_norm == TIPO_SOLICITACAO_ADITIVO else 0

    if _coluna_existe(TABELA_CARD, "BitContratoNovo"):
        campos_update.append("BitContratoNovo = :bit_contrato_novo")
        parametros_update["bit_contrato_novo"] = 1 if tipo_norm == TIPO_SOLICITACAO_NOVO else 0

    if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
        campos_update.append("AtualizadoEm = GETDATE()")

    if campos_update:
        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
               SET {', '.join(campos_update)}
             WHERE IDFatoKanbanCard = :id_card;
        """)
        db.session.execute(sql_upd, parametros_update)

    tags_adicionadas: list[int] = []
    tags_removidas: list[int] = []

    if tag_oposta and int(tag_oposta.get("IDDimKanbanTag") or 0) > 0:
        if _remover_tag_do_card(
            id_card=int(id_card),
            id_tag=int(tag_oposta.get("IDDimKanbanTag") or 0),
            id_usuario=int(id_usuario),
        ):
            tags_removidas.append(int(tag_oposta.get("IDDimKanbanTag") or 0))

    if aplicar_tags and tag_desejada and int(tag_desejada.get("IDDimKanbanTag") or 0) > 0:
        if _aplicar_tag_no_card(
            id_card=int(id_card),
            id_tag=int(tag_desejada.get("IDDimKanbanTag") or 0),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
        ):
            tags_adicionadas.append(int(tag_desejada.get("IDDimKanbanTag") or 0))

    return {
        "tipo_contrato": tipo_norm,
        "bit_aditivo": 1 if tipo_norm == TIPO_SOLICITACAO_ADITIVO else 0,
        "bit_contrato_novo": 1 if tipo_norm == TIPO_SOLICITACAO_NOVO else 0,
        "tags_adicionadas": tags_adicionadas,
        "tags_removidas": tags_removidas,
        "id_tag_ativa": int(tag_desejada.get("IDDimKanbanTag") or 0) if tag_desejada else None,
        "id_fase_atual": int(id_fase_atual or 0) or None,
    }






def _montar_label_contrato_existente(contrato: Mapping[str, Any] | dict[str, Any]) -> str:
    item = dict(contrato or {})

    id_contrato = int(
        item.get("IDFatoControleContratosEuromidia")
        or item.get("IDFatoControleContratoEuromidia")
        or 0
    )

    numero_contrato = str(item.get("NumeroContrato") or "").strip()
    numero_previa = str(item.get("NumeroPrevia") or "").strip()
    referencia = str(item.get("Referencia") or "").strip()
    razao_social = str(item.get("RazaoSocial") or "").strip()
    cnpj = _formatar_cnpj(item.get("CNPJ"))

    partes: list[str] = []

    if numero_contrato:
        partes.append(f"Contrato {numero_contrato}")
    elif id_contrato > 0:
        partes.append(f"Contrato #{id_contrato}")
    else:
        partes.append("Contrato")

    if numero_previa:
        partes.append(f"Prévia {numero_previa}")

    if razao_social:
        partes.append(razao_social)

    if cnpj:
        partes.append(cnpj)

    if referencia:
        partes.append(f"Ref. {referencia}")

    return " | ".join([parte for parte in partes if str(parte).strip()])












def _listar_contratos_existentes_empresa(id_empresa: int) -> list[dict[str, object]]:
    id_empresa_int = int(id_empresa or 0)
    if id_empresa_int <= 0:
        return []

    colunas_select: list[str] = [
        "c.IDFatoControleContratosEuromidia",
        "c.IDEmpresa",
        "c.NumeroContrato",
        "c.NumeroPrevia",
        "c.Referencia",
        "c.RazaoSocial",
        "c.CNPJ",
    ]

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "MarcaExibida"):
        colunas_select.append("c.MarcaExibida")

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "DataLancamento"):
        colunas_select.append("c.DataLancamento")

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "DataAtualizacao"):
        colunas_select.append("c.DataAtualizacao")

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "QuantidadePontos"):
        colunas_select.append("c.QuantidadePontos")

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "QuantidadeFaces"):
        colunas_select.append("c.QuantidadeFaces")

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "TotalFaturamentoLiquidoMensal"):
        colunas_select.append("c.TotalFaturamentoLiquidoMensal")

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "IDDimStatusContratos"):
        colunas_select.append("c.IDDimStatusContratos")

    expressao_ordenacao = "c.IDFatoControleContratosEuromidia DESC"

    if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "DataAssinaturaRenovacao") and _coluna_existe(TABELA_CONTROLE_CONTRATOS, "DataAtualizacao"):
        expressao_ordenacao = """
            CASE
                WHEN c.DataAssinaturaRenovacao IS NULL THEN c.DataAtualizacao
                ELSE c.DataAssinaturaRenovacao
            END DESC,
            c.IDFatoControleContratosEuromidia DESC
        """
    elif _coluna_existe(TABELA_CONTROLE_CONTRATOS, "DataAtualizacao"):
        expressao_ordenacao = "c.DataAtualizacao DESC, c.IDFatoControleContratosEuromidia DESC"

    sql_empresa = text(f"""
        SELECT TOP (1)
            e.IDEmpresa,
            e.CNPJ
        FROM {TABELA_EMPRESAS} e
        WHERE e.IDEmpresa = :id_empresa;
    """)

    empresa = db.session.execute(
        sql_empresa,
        {"id_empresa": id_empresa_int},
    ).mappings().first()

    if not empresa:
        return []

    cnpj_empresa = str(empresa.get("CNPJ") or "").strip()

    def _executar_consulta(sql_consulta, parametros: dict[str, object]) -> list[dict[str, object]]:
        rows = db.session.execute(sql_consulta, parametros).mappings().all()

        contratos: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["label"] = _montar_label_contrato_existente(item)
            contratos.append(item)

        return contratos

    sql_por_id_empresa = text(f"""
        SELECT
            {', '.join(colunas_select)}
        FROM {TABELA_CONTROLE_CONTRATOS} c
        WHERE c.IDEmpresa = :id_empresa
          AND ISNULL(c.BitAtivo, 1) = 1
        ORDER BY {expressao_ordenacao};
    """)

    contratos = _executar_consulta(
        sql_por_id_empresa,
        {"id_empresa": id_empresa_int},
    )

    if contratos:
        return contratos

    if not cnpj_empresa:
        return []

    sql_por_cnpj = text(f"""
        SELECT
            {', '.join(colunas_select)}
        FROM {TABELA_CONTROLE_CONTRATOS} c
        WHERE c.CNPJ = :cnpj
          AND ISNULL(c.BitAtivo, 1) = 1
        ORDER BY {expressao_ordenacao};
    """)

    return _executar_consulta(
        sql_por_cnpj,
        {"cnpj": cnpj_empresa},
    )





def _listar_cod_ponto_por_contrato(id_contrato: int) -> list[dict[str, object]]:
    sql = text(f"""
        SELECT
            i.CodPonto,
            MAX(i.CidadeExibicao) AS CidadeExibicao,
            MAX(i.Tipo) AS TipoPainel,
            COUNT(DISTINCT NULLIF(LTRIM(RTRIM(ISNULL(i.CodFace, ''))), '')) AS QuantidadeFaces,
            MIN(i.DataInicioPrevisto) AS DataInicioPrevisto,
            MAX(i.DataTerminoPrevisto) AS DataTerminoPrevisto,
            SUM(i.FaturamentoLiquidoMensal) AS FaturamentoLiquidoMensalTotal,
            SUM(i.FaturamentoBrutoMensal) AS FaturamentoBrutoMensalTotal
        FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
        WHERE i.IDFatoControleContratoEuromidia = :id_contrato
          AND ISNULL(i.BitAtivo, 1) = 1
          AND NULLIF(LTRIM(RTRIM(ISNULL(i.CodPonto, ''))), '') IS NOT NULL
        GROUP BY i.CodPonto
        ORDER BY i.CodPonto ASC;
    """)

    rows = db.session.execute(sql, {"id_contrato": int(id_contrato)}).mappings().all()

    resultado: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)

        item["FaturamentoLiquidoMensalTotal"] = _decimal_para_float(item.get("FaturamentoLiquidoMensalTotal"))
        item["FaturamentoBrutoMensalTotal"] = _decimal_para_float(item.get("FaturamentoBrutoMensalTotal"))

        data_inicio_previsto = item.get("DataInicioPrevisto")
        data_termino_previsto = item.get("DataTerminoPrevisto")

        if hasattr(data_inicio_previsto, "date"):
            try:
                data_inicio_previsto = data_inicio_previsto.date()
            except Exception:
                pass

        if hasattr(data_termino_previsto, "date"):
            try:
                data_termino_previsto = data_termino_previsto.date()
            except Exception:
                pass

        item["DataInicioPrevisto"] = (
            data_inicio_previsto.isoformat()
            if hasattr(data_inicio_previsto, "isoformat")
            else (str(data_inicio_previsto) if data_inicio_previsto not in (None, "") else None)
        )
        item["DataTerminoPrevisto"] = (
            data_termino_previsto.isoformat()
            if hasattr(data_termino_previsto, "isoformat")
            else (str(data_termino_previsto) if data_termino_previsto not in (None, "") else None)
        )

        partes_label = [
            str(item.get("CodPonto") or "").strip(),
            str(item.get("TipoPainel") or "").strip(),
            str(item.get("CidadeExibicao") or "").strip(),
        ]

        if item.get("QuantidadeFaces") not in (None, "", 0):
            partes_label.append(f"{item.get('QuantidadeFaces')} face(s)")

        item["label"] = " | ".join([parte for parte in partes_label if str(parte).strip()])
        resultado.append(item)

    return resultado








def _listar_cod_face_por_contrato_ponto(id_contrato: int, cod_ponto: str) -> list[dict[str, object]]:
    sql = text(f"""
        SELECT
            MAX(i.IDFatoControleContratosItensEuromidia) AS IDFatoControleContratosItensEuromidia,
            MAX(i.CodPonto) AS CodPonto,
            i.CodFace,
            MAX(i.Tipo) AS TipoPainel,
            MAX(i.CidadeExibicao) AS CidadeExibicao,
            MAX(i.Cota) AS Cota,
            MAX(i.FaturamentoBrutoMensal) AS FaturamentoBrutoMensal,
            MAX(i.FaturamentoLiquidoMensal) AS FaturamentoLiquidoMensal,
            MAX(i.FaturamentoLiquidoFinalMensal) AS FaturamentoLiquidoFinalMensal,
            MAX(i.TotalBrutoContrato) AS TotalBrutoContrato,
            MAX(i.TotalLiquidoContratoAGBRCTACORDO) AS TotalLiquidoContratoAGBRCTACORDO,
            MAX(i.PercentualPermuta) AS PercentualPermuta,
            MAX(i.ValorPermuta) AS ValorPermuta,
            MAX(i.NumeroParcelas) AS NumeroParcelas,
            MAX(i.DataInicioVencimento) AS DataInicioVencimento,
            MAX(i.DataInicioPrevisto) AS DataInicioPrevisto,
            MAX(i.DataTerminoPrevisto) AS DataTerminoPrevisto,
            MAX(i.IDPainelEuromidia) AS IDPainelEuromidia,
            MAX(i.IDDimFacesPaineis) AS IDDimFacesPaineis
        FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
        WHERE i.IDFatoControleContratoEuromidia = :id_contrato
          AND LTRIM(RTRIM(ISNULL(i.CodPonto, ''))) = :cod_ponto
          AND ISNULL(i.BitAtivo, 1) = 1
          AND NULLIF(LTRIM(RTRIM(ISNULL(i.CodFace, ''))), '') IS NOT NULL
        GROUP BY i.CodFace
        ORDER BY i.CodFace ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_contrato": int(id_contrato),
            "cod_ponto": str(cod_ponto).strip(),
        },
    ).mappings().all()

    resultado: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)

        preco_venda_atual = item.get("TotalLiquidoContratoAGBRCTACORDO")
        if preco_venda_atual in (None, ""):
            preco_venda_atual = item.get("FaturamentoLiquidoFinalMensal")
        if preco_venda_atual in (None, ""):
            preco_venda_atual = item.get("FaturamentoLiquidoMensal")

        for campo_decimal in (
            "FaturamentoBrutoMensal",
            "FaturamentoLiquidoMensal",
            "FaturamentoLiquidoFinalMensal",
            "TotalBrutoContrato",
            "TotalLiquidoContratoAGBRCTACORDO",
            "PercentualPermuta",
            "ValorPermuta",
        ):
            item[campo_decimal] = _decimal_para_float(item.get(campo_decimal))

        item["preco_venda_atual"] = _decimal_para_float(preco_venda_atual)
        item["valor_mensal"] = item.get("FaturamentoLiquidoMensal")

        for campo_data in ("DataInicioPrevisto", "DataTerminoPrevisto", "DataInicioVencimento"):
            valor_data = item.get(campo_data)

            if hasattr(valor_data, "date"):
                try:
                    valor_data = valor_data.date()
                except Exception:
                    pass

            item[campo_data] = (
                valor_data.isoformat()
                if hasattr(valor_data, "isoformat")
                else (str(valor_data) if valor_data not in (None, "") else None)
            )

        partes_label: list[str] = [str(item.get("CodFace") or "").strip()]

        tipo_painel = str(item.get("TipoPainel") or "").strip()
        if tipo_painel:
            partes_label.append(tipo_painel)

        cota = item.get("Cota")
        if cota not in (None, ""):
            partes_label.append(f"Cota {cota}")

        if item.get("DataInicioPrevisto") or item.get("DataTerminoPrevisto"):
            partes_label.append(
                f"{item.get('DataInicioPrevisto') or '—'} até {item.get('DataTerminoPrevisto') or '—'}"
            )

        valor_label = item.get("preco_venda_atual")
        if valor_label not in (None, ""):
            try:
                valor_decimal = Decimal(str(valor_label))
                valor_formatado = f"R$ {valor_decimal:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                partes_label.append(valor_formatado)
            except Exception:
                pass

        item["label"] = " | ".join([parte for parte in partes_label if str(parte).strip()])
        resultado.append(item)

    return resultado











@kanban_bp.route("/api/empresas/<int:id_empresa>/contratos", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_listar_contratos_empresa(id_empresa: int):
    _assert_login()
    _id_empresa_usuario_or_403()

    try:
        sql_empresa = text(f"""
            SELECT TOP (1)
                IDEmpresa,
                IDEmpresaProprietaria,
                RazaoSocial,
                CNPJ
            FROM {TABELA_EMPRESAS}
            WHERE IDEmpresa = :id_empresa;
        """)

        empresa = db.session.execute(
            sql_empresa,
            {"id_empresa": int(id_empresa)},
        ).mappings().first()

        if not empresa:
            return jsonify({"ok": False, "msg": "Empresa não encontrada."}), 404

        contratos = _listar_contratos_existentes_empresa(int(id_empresa))

        return jsonify(
            {
                "ok": True,
                "empresa": dict(empresa),
                "contratos": contratos,
                "tem_contratos": bool(contratos),
                "opcoes": [
                    {"valor": VALOR_OPCAO_NOVO_CONTRATO, "label": "Novo Contrato"}
                ],
            }
        )

    except Exception as erro:
        current_app.logger.exception(
            "KANBAN CONTRATOS EMPRESA: erro ao listar contratos. id_empresa=%s",
            id_empresa,
        )
        return jsonify(
            {
                "ok": False,
                "msg": "Erro ao listar contratos da empresa.",
                "erro": str(erro),
            }
        ), 500





@kanban_bp.route("/api/contratos/<int:id_contrato>/pontos", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_listar_pontos_contrato(id_contrato: int):
    _assert_login()
    _id_empresa_usuario_or_403()

    sql_contrato = text(f"""
        SELECT TOP (1)
            c.IDFatoControleContratosEuromidia,
            c.IDEmpresa,
            c.NumeroContrato,
            c.RazaoSocial,
            c.CNPJ,
            c.MarcaExibida
        FROM {TABELA_CONTROLE_CONTRATOS} c
        WHERE c.IDFatoControleContratosEuromidia = :id_contrato
          AND ISNULL(c.BitAtivo, 1) = 1;
    """)

    contrato = db.session.execute(
        sql_contrato,
        {
            "id_contrato": int(id_contrato),
        },
    ).mappings().first()

    if not contrato:
        return jsonify({"ok": False, "msg": "Contrato não encontrado."}), 404

    pontos = _listar_cod_ponto_por_contrato(int(id_contrato))

    return jsonify(
        {
            "ok": True,
            "contrato": dict(contrato),
            "pontos": pontos,
            "opcoes": [
                {"valor": VALOR_OPCAO_NOVO_PAINEL, "label": "Novo Painel"}
            ],
        }
    )












@kanban_bp.route("/api/contratos/<int:id_contrato>/pontos/<string:cod_ponto>/faces", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_listar_faces_contrato_ponto(id_contrato: int, cod_ponto: str):
    _assert_login()
    _id_empresa_usuario_or_403()

    cod_ponto_limpo = str(cod_ponto or "").strip()
    if not cod_ponto_limpo:
        return jsonify({"ok": False, "msg": "CodPonto obrigatório."}), 400

    sql_contrato = text(f"""
        SELECT TOP (1)
            c.IDFatoControleContratosEuromidia
        FROM {TABELA_CONTROLE_CONTRATOS} c
        WHERE c.IDFatoControleContratosEuromidia = :id_contrato
          AND ISNULL(c.BitAtivo, 1) = 1;
    """)

    contrato_existe = db.session.execute(
        sql_contrato,
        {
            "id_contrato": int(id_contrato),
        },
    ).scalar()

    if not contrato_existe:
        return jsonify({"ok": False, "msg": "Contrato não encontrado."}), 404

    faces = _listar_cod_face_por_contrato_ponto(int(id_contrato), cod_ponto_limpo)

    return jsonify(
        {
            "ok": True,
            "id_contrato": int(id_contrato),
            "cod_ponto": cod_ponto_limpo,
            "faces": faces,
        }
    )




def _sincronizar_tipo_contrato_card(
    *,
    id_card: int,
    id_kanban: int,
    id_fase_atual: int | None,
    id_usuario: int,
    id_empresa_proprietaria: int,
    tipo_contrato: str,
    id_contrato_existente: int | None = None,
    cod_ponto_contrato: object = None,
    cod_face_contrato: object = None,
    aplicar_tags: bool = True,
) -> dict[str, object]:
    """
    Regras:
    - ADITIVO => BitAditivo=1, BitContratoNovo=0
    - NOVO_CONTRATO => BitAditivo=0, BitContratoNovo=1
    - quando o fluxo estiver em NOVO_CONTRATO, eu limpo o vínculo persistido de contrato/ponto/face
    - quando o fluxo estiver em ADITIVO, eu persisto o contrato/ponto/face no card
    - as tags de tipo de contrato (Aditivo / Novo Contrato) só devem aparecer quando o card estiver na fase 4
    """
    tipo_norm = _normalizar_tipo_contrato_card(tipo_contrato)
    if tipo_norm not in {TIPO_SOLICITACAO_ADITIVO, TIPO_SOLICITACAO_NOVO}:
        raise ValueError("Tipo de contrato inválido.")

    tag_novo = _obter_tag_tipo_contrato(id_kanban, TIPO_SOLICITACAO_NOVO)
    tag_aditivo = _obter_tag_tipo_contrato(id_kanban, TIPO_SOLICITACAO_ADITIVO)
    tag_desejada = tag_aditivo if tipo_norm == TIPO_SOLICITACAO_ADITIVO else tag_novo
    # A tag de tipo do contrato precisa refletir o estado persistido do card.
    # Se eu remover a tag fora da fase 4, o card perde o rótulo Aditivo no segundo salvamento.
    deve_exibir_tag_tipo = bool(aplicar_tags)

    campos_update: list[str] = []
    parametros_update: dict[str, object] = {"id_card": int(id_card)}

    if _coluna_existe(TABELA_CARD, "BitAditivo"):
        campos_update.append("BitAditivo = :bit_aditivo")
        parametros_update["bit_aditivo"] = 1 if tipo_norm == TIPO_SOLICITACAO_ADITIVO else 0

    if _coluna_existe(TABELA_CARD, "BitContratoNovo"):
        campos_update.append("BitContratoNovo = :bit_contrato_novo")
        parametros_update["bit_contrato_novo"] = 1 if tipo_norm == TIPO_SOLICITACAO_NOVO else 0

    id_contrato_para_persistir = id_contrato_existente if tipo_norm == TIPO_SOLICITACAO_ADITIVO else None
    cod_ponto_para_persistir = cod_ponto_contrato if tipo_norm == TIPO_SOLICITACAO_ADITIVO else None
    cod_face_para_persistir = cod_face_contrato if tipo_norm == TIPO_SOLICITACAO_ADITIVO else None

    _anexar_campos_vinculo_contrato_card(
        campos_sql=campos_update,
        parametros=parametros_update,
        id_contrato_existente=id_contrato_para_persistir,
        cod_ponto_contrato=cod_ponto_para_persistir,
        cod_face_contrato=cod_face_para_persistir,
        prefixo_parametros="sync_",
    )

    if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
        campos_update.append("AtualizadoEm = GETDATE()")

    if campos_update:
        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
               SET {', '.join(campos_update)}
             WHERE IDFatoKanbanCard = :id_card;
        """)
        db.session.execute(sql_upd, parametros_update)

    tags_adicionadas: list[int] = []
    tags_removidas: list[int] = []

    tag_oposta = tag_novo if tipo_norm == TIPO_SOLICITACAO_ADITIVO else tag_aditivo
    id_tag_oposta = int(tag_oposta.get("IDDimKanbanTag") or 0) if tag_oposta else 0
    if id_tag_oposta > 0:
        if _remover_tag_do_card(
            id_card=int(id_card),
            id_tag=id_tag_oposta,
            id_usuario=int(id_usuario),
        ):
            tags_removidas.append(id_tag_oposta)

    if deve_exibir_tag_tipo and tag_desejada and int(tag_desejada.get("IDDimKanbanTag") or 0) > 0:
        id_tag_desejada = int(tag_desejada.get("IDDimKanbanTag") or 0)
        if _aplicar_tag_no_card(
            id_card=int(id_card),
            id_tag=id_tag_desejada,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
        ):
            tags_adicionadas.append(id_tag_desejada)

    return {
        "tipo_contrato": tipo_norm,
        "bit_aditivo": 1 if tipo_norm == TIPO_SOLICITACAO_ADITIVO else 0,
        "bit_contrato_novo": 1 if tipo_norm == TIPO_SOLICITACAO_NOVO else 0,
        "tags_adicionadas": tags_adicionadas,
        "tags_removidas": tags_removidas,
        "id_tag_ativa": int(tag_desejada.get("IDDimKanbanTag") or 0) if (deve_exibir_tag_tipo and tag_desejada) else None,
        "id_fase_atual": int(id_fase_atual or 0) or None,
        "deve_exibir_tag_tipo": bool(deve_exibir_tag_tipo),
        "id_contrato_existente": int(id_contrato_para_persistir) if id_contrato_para_persistir not in (None, "", 0) else None,
        "cod_ponto_contrato": str(cod_ponto_para_persistir).strip() if cod_ponto_para_persistir not in (None, "") else None,
        "cod_face_contrato": str(cod_face_para_persistir).strip().upper() if cod_face_para_persistir not in (None, "") else None,
    }




def _resolver_id_status_card_movimento(
    nome_fase_para: str | None = None,
    *,
    card_inativado: bool = False,
) -> int | None:
    """
    Regra fixa para gravar IDDimKanbanStatusCard no histórico.

    Regras:
    - fases em andamento:
        A Fazer (Back Office)
        Proposta Enviada
        Refazer
        Aprovado Cliente
        Aguardando Liberação (Gerencia)
        Documentos Enviados
      => 1

    - fase Concluido
      => 3

    - card inativado/excluído
      => 2
    """
    if card_inativado:
        return 2

    nome_normalizado = _normalizar_texto_comparacao(nome_fase_para)

    fases_status_1 = {
        "a fazer (back office)",
        "proposta enviada",
        "refazer",
        "aprovado cliente",
        "aguardando liberacao (gerencia)",
        "documentos enviados",
    }

    if nome_normalizado in fases_status_1:
        return 1

    if nome_normalizado == "concluido":
        return 3

    return None


def _registrar_status_historico_card(
    *,
    id_card: int,
    id_fase: int | None,
    id_status_card: int | None,
    id_usuario: int | None,
    id_empresa_proprietaria: int | None,
) -> None:
    """
    Registra o snapshot lógico de status do card na fase informada.

    Observação importante:
    esta tabela não possui data/hora no esquema atual, então ela funciona
    como trilha estrutural complementar. A linha do tempo cronológica continua
    vindo principalmente de FatoKanbanCardMovimento, FatoKanbanCardObservacoes
    e FatoKanbanCardLog.
    """
    if id_status_card in (None, "", 0):
        return

    _inserir_registro_dinamico(
        TABELA_CARD_STATUS_HISTORICO,
        {
            "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, "", 0) else None,
            "IDDimKanbanStatusCard": int(id_status_card),
            "IDFatoKanbanCard": int(id_card),
            "IDDimKanbanFase": int(id_fase) if id_fase not in (None, "", 0) else None,
            "IDDimUsuarios": int(id_usuario) if id_usuario not in (None, "", 0) else None,
        },
    )


def _registrar_tag_historico_card(
    *,
    id_fato_kanban_card_tag: int | None,
    id_card: int,
    id_fase: int | None,
    id_usuario: int | None,
    id_empresa_proprietaria: int | None,
) -> None:
    """
    Registra em qual fase a alteração de tag ocorreu.

    A tabela histórica de tag também não carrega timestamp no esquema atual.
    Por isso eu gravo aqui a referência estrutural da fase/usuário e deixo a
    cronologia completa por conta da própria FatoKanbanCardTag (AplicadoEm /
    RemovidoEm) e da FatoKanbanCardLog.
    """
    if id_fato_kanban_card_tag in (None, "", 0):
        return

    _inserir_registro_dinamico(
        TABELA_CARD_TAG_HISTORICO,
        {
            "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, "", 0) else None,
            "IDFatoKanbanCardTag": int(id_fato_kanban_card_tag),
            "IDFatoKanbanCard": int(id_card),
            "IDDimKanbanFase": int(id_fase) if id_fase not in (None, "", 0) else None,
            "IDDimUsuarios": int(id_usuario) if id_usuario not in (None, "", 0) else None,
        },
    )






def _rows_para_dicts(rows: Any) -> list[dict[str, Any]]:
    return [dict(r) if isinstance(r, Mapping) else r for r in (rows or [])]



def _row_para_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if isinstance(row, Mapping) else row



def _log_debug_usuario() -> None:
    is_auth = getattr(current_user, "is_authenticated", None)
    ia = getattr(current_user, "is_active", None)
    try:
        is_active_val = ia() if callable(ia) else ia
    except Exception:
        is_active_val = "erro"

    current_app.logger.warning(
        "KANBAN DEBUG: is_authenticated=%s is_active=%s IDDimUsuarios=%s get_id=%s email=%s",
        is_auth,
        is_active_val,
        getattr(current_user, "IDDimUsuarios", None),
        (current_user.get_id() if hasattr(current_user, "get_id") else None),
        getattr(current_user, "Email", None),
    )



def _request_pede_dado_fresco() -> bool:
    """
    Permite ao front pedir leitura sem usar cache do backend.

    Exemplos aceitos:
    - ?fresh=1
    - ?fresh=true
    - ?no_cache=1
    """
    valor = (
        request.args.get("fresh")
        or request.args.get("no_cache")
        or ""
    ).strip().lower()

    return valor in {"1", "true", "t", "yes", "y", "sim"}




from datetime import datetime, date

def _para_data_sql_ou_none(valor: Any):
    if valor is None:
        return None

    if isinstance(valor, (datetime, date)):
        return valor

    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None

        formatos = (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        )

        for formato in formatos:
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                pass

    return None













def _rowversion_para_hex(valor: Any) -> str | None:
    print(f"[KANBAN][_rowversion_para_hex] INICIO tipo={type(valor).__name__} valor={valor!r}")

    if valor is None:
        print("[KANBAN][_rowversion_para_hex] valor is None -> retorno None")
        current_app.logger.warning("KANBAN: _rowversion_para_hex recebeu None.")
        return None

    if isinstance(valor, memoryview):
        print("[KANBAN][_rowversion_para_hex] valor é memoryview -> convertendo para bytes")
        current_app.logger.info("KANBAN: _rowversion_para_hex recebeu memoryview.")
        valor = valor.tobytes()

    if isinstance(valor, (bytes, bytearray)):
        resultado = bytes(valor).hex().upper()
        print(
            f"[KANBAN][_rowversion_para_hex] valor binário convertido com sucesso -> "
            f"hex={resultado} tamanho_bytes={len(bytes(valor))}"
        )
        current_app.logger.info(
            "KANBAN: _rowversion_para_hex converteu bytes/bytearray com sucesso. hex=%s",
            resultado,
        )
        return resultado

    if isinstance(valor, str):
        texto_original = valor
        texto = valor.strip().upper()

        print(
            f"[KANBAN][_rowversion_para_hex] valor é str -> original={texto_original!r} normalizado={texto!r}"
        )
        current_app.logger.info(
            "KANBAN: _rowversion_para_hex recebeu string. original=%r normalizado=%r",
            texto_original,
            texto,
        )

        if not texto:
            print("[KANBAN][_rowversion_para_hex] string vazia -> retorno None")
            current_app.logger.warning("KANBAN: _rowversion_para_hex recebeu string vazia.")
            return None

        if texto.startswith("0X"):
            texto = texto[2:]
            print(f"[KANBAN][_rowversion_para_hex] removido prefixo 0X -> {texto!r}")

        texto = re.sub(r"[^0-9A-F]", "", texto)
        print(f"[KANBAN][_rowversion_para_hex] texto após regex hex -> {texto!r}")

        if not texto:
            print("[KANBAN][_rowversion_para_hex] texto sem caracteres hex válidos -> retorno None")
            current_app.logger.warning(
                "KANBAN: _rowversion_para_hex recebeu string sem conteúdo hexadecimal válido. original=%r",
                texto_original,
            )
            return None

        if len(texto) % 2 != 0:
            print(
                f"[KANBAN][_rowversion_para_hex] quantidade ímpar de caracteres hex ({len(texto)}) -> retorno None"
            )
            current_app.logger.warning(
                "KANBAN: _rowversion_para_hex recebeu string hex com tamanho ímpar. texto=%r tamanho=%s",
                texto,
                len(texto),
            )
            return None

        print(f"[KANBAN][_rowversion_para_hex] string convertida com sucesso -> hex={texto}")
        current_app.logger.info(
            "KANBAN: _rowversion_para_hex converteu string com sucesso. hex=%s",
            texto,
        )
        return texto

    try:
        print(
            f"[KANBAN][_rowversion_para_hex] tentando conversão defensiva com bytes(valor) para tipo={type(valor).__name__}"
        )
        valor_bytes = bytes(valor)
        if valor_bytes:
            resultado = valor_bytes.hex().upper()
            print(
                f"[KANBAN][_rowversion_para_hex] conversão defensiva funcionou -> "
                f"hex={resultado} tamanho_bytes={len(valor_bytes)}"
            )
            current_app.logger.info(
                "KANBAN: _rowversion_para_hex converteu via bytes(valor). tipo=%s hex=%s",
                type(valor).__name__,
                resultado,
            )
            return resultado

        print("[KANBAN][_rowversion_para_hex] bytes(valor) retornou vazio -> retorno None")
        current_app.logger.warning(
            "KANBAN: _rowversion_para_hex bytes(valor) retornou vazio. tipo=%s valor=%r",
            type(valor).__name__,
            valor,
        )
        return None
    except Exception as exc:
        print(
            f"[KANBAN][_rowversion_para_hex] ERRO na conversão defensiva tipo={type(valor).__name__} erro={exc}"
        )
        current_app.logger.warning(
            "KANBAN: _rowversion_para_hex falhou na conversão defensiva. tipo=%s valor=%r erro=%s",
            type(valor).__name__,
            valor,
            exc,
        )

    print(
        f"[KANBAN][_rowversion_para_hex] tipo não suportado -> tipo={type(valor).__name__} valor={valor!r}"
    )
    current_app.logger.warning(
        "KANBAN: _rowversion_para_hex recebeu tipo não suportado. tipo=%s valor=%r",
        type(valor).__name__,
        valor,
    )
    return None




def _rowversion_hex_para_bytes(valor: Any) -> bytes | None:
    texto = str(valor or "").strip().upper()
    if not texto:
        return None

    if texto.startswith("0X"):
        texto = texto[2:]

    try:
        return bytes.fromhex(texto)
    except ValueError:
        return None

















def _obter_tag_em_atendimento(id_kanban: int) -> dict[str, Any] | None:
    return _obter_tag_por_nome(id_kanban, "Em Atendimento", somente_ativa=True)


def _card_possui_tag_ativa(id_card: int, id_tag: int) -> bool:
    sql = text("""
        SELECT TOP (1) 1
        FROM [Kanban].[Silver].[FatoKanbanCardTag]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)

    existe = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_tag": int(id_tag),
        },
    ).scalar()

    return bool(existe)


def _aplicar_tag_no_card(
    *,
    id_card: int,
    id_tag: int,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
) -> bool:
    if not id_card or not id_tag:
        return False

    if _card_possui_tag_ativa(id_card, id_tag):
        return False

    sql_insert = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
            (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
        VALUES
            (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa);
    """)

    db.session.execute(
        sql_insert,
        {
            "id_card": int(id_card),
            "id_tag": int(id_tag),
            "id_usuario": int(id_usuario),
            "id_empresa": int(id_empresa_proprietaria or 0) or None,
        },
    )
    return True


def _remover_tag_do_card(*, id_card: int, id_tag: int, id_usuario: int) -> bool:
    if not id_card or not id_tag:
        return False

    sql = text("""
        UPDATE [Kanban].[Silver].[FatoKanbanCardTag]
        SET RemovidoEm = GETDATE(),
            RemovidoPor = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)

    resultado = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_tag": int(id_tag),
            "id_usuario": int(id_usuario),
        },
    )

    return bool(getattr(resultado, "rowcount", 0) or 0)


def _garantir_tag_em_atendimento_no_card(
    *,
    id_card: int,
    id_kanban: int,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
    falhar_se_nao_existir: bool = False,
) -> bool:
    tag = _obter_tag_em_atendimento(id_kanban)

    if not tag:
        if falhar_se_nao_existir:
            raise RuntimeError(
                "A tag obrigatória 'Em Atendimento' não está cadastrada/ativa para este kanban."
            )
        return False

    return _aplicar_tag_no_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=id_empresa_proprietaria,
    )


def _remover_tag_em_atendimento_do_card(*, id_card: int, id_kanban: int, id_usuario: int) -> bool:
    tag = _obter_tag_em_atendimento(id_kanban)
    if not tag:
        return False

    return _remover_tag_do_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
    )











def _salvar_vinculos_painel_face_card(
    id_card: int,
    vinculos_preparados: list[dict] | None = None,
    id_usuario: int | None = None,
    id_empresa_proprietaria: int | None = None,
    itens_painel_face: list[dict] | None = None,
) -> None:
    """
    Salva o estado operacional de painel/face do card sem duplicar linhas ativas idênticas.

    Lógica desta versão:
    1) a identidade operacional do vínculo é a combinação painel + face
    2) a linha ativa só é encerrada quando o estado comercial/operacional realmente mudou
    3) reenviar o mesmo payload não cria nova linha
    4) remover um painel/face do payload encerra logicamente a linha ativa correspondente
    5) diferenças cosméticas de formato (None, "", vírgula, casas decimais) são normalizadas antes da comparação
    """

    if isinstance(vinculos_preparados, list):
        itens_entrada = vinculos_preparados
    elif isinstance(itens_painel_face, list):
        itens_entrada = itens_painel_face
    else:
        itens_entrada = []

    def obter_primeiro(item: dict, chaves: tuple[str, ...]) -> object:
        for chave in chaves:
            if chave in item:
                valor = item.get(chave)
                if valor is not None and str(valor).strip() != "":
                    return valor
        return None

    def normalizar_texto(valor: object) -> str | None:
        if valor is None:
            return None
        texto = " ".join(str(valor).strip().split())
        return texto if texto else None

    def normalizar_inteiro(valor: object) -> int | None:
        if valor is None or valor == "":
            return None
        if isinstance(valor, bool):
            return int(valor)
        try:
            return int(str(valor).strip())
        except (TypeError, ValueError):
            return None

    def normalizar_decimal(valor: object, casas: str = "0.0001") -> Decimal | None:
        if valor is None or valor == "":
            return None

        if isinstance(valor, Decimal):
            dec = valor
        else:
            if isinstance(valor, (int, float)):
                texto = str(valor)
            else:
                texto = str(valor).strip()
                if not texto:
                    return None
                if "," in texto:
                    texto = texto.replace(".", "").replace(",", ".")
            try:
                dec = Decimal(str(texto))
            except (InvalidOperation, TypeError, ValueError):
                return None

        try:
            return dec.quantize(Decimal(casas))
        except Exception:
            return dec

    def normalizar_data(valor: object) -> date | None:
        return _normalizar_data_reserva_kanban(valor)

    def linha_tem_conteudo_minimo(linha: dict) -> bool:
        return bool(linha.get("IDDimPaineisEuromidia") and linha.get("IDDimFacesPaineis"))

    def normalizar_linha(item: dict, ordem_padrao: int) -> dict:
        linha = {
            "Ordem": normalizar_inteiro(
                obter_primeiro(item, ("Ordem", "ordem", "Indice", "indice", "Sequencia", "sequencia"))
            ),
            "IDDimPaineisEuromidia": normalizar_inteiro(
                obter_primeiro(
                    item,
                    (
                        "IDDimPaineisEuromidia",
                        "id_dim_paineis_euromidia",
                        "IDPainel",
                        "id_painel",
                        "IDPainelMidia",
                        "IDDimPainel",
                    ),
                )
            ),
            "IDDimFacesPaineis": normalizar_inteiro(
                obter_primeiro(
                    item,
                    (
                        "IDDimFacesPaineis",
                        "id_dim_faces_paineis",
                        "IDFace",
                        "id_face",
                        "IDPainelFace",
                        "IDDimFace",
                        "id_dim_face",
                    ),
                )
            ),
            "CodPonto": normalizar_texto(
                obter_primeiro(item, ("CodPonto", "cod_ponto", "CodigoPonto", "codigo_ponto"))
            ),
            "CodFace": normalizar_texto(
                obter_primeiro(item, ("CodFace", "cod_face", "CodigoFace", "codigo_face"))
            ),
            "TipoPainel": normalizar_texto(
                obter_primeiro(item, ("TipoPainel", "tipo_painel", "TipoMidia", "tipo_midia"))
            ),
            "AnoCusto": normalizar_inteiro(obter_primeiro(item, ("AnoCusto", "ano_custo"))),
            "CustoTabela": normalizar_decimal(
                obter_primeiro(item, ("CustoTabela", "custo_tabela", "Custo", "custo"))
            ),
            "IDDimTabelaPrecosEuromidia": normalizar_inteiro(
                obter_primeiro(
                    item,
                    (
                        "IDDimTabelaPrecosEuromidia",
                        "id_dim_tabela_precos_euromidia",
                        "IDTabelaPreco",
                        "id_tabela_preco",
                        "id_preco",
                    ),
                )
            ),
            "PeriodoExibicao": normalizar_texto(
                obter_primeiro(item, ("PeriodoExibicao", "periodo_exibicao", "Periodo", "periodo"))
            ),
            "ExibicoesDia": normalizar_inteiro(obter_primeiro(item, ("ExibicoesDia", "exibicoes_dia"))),
            "ValorTabela": normalizar_decimal(obter_primeiro(item, ("ValorTabela", "valor_tabela"))),
            "Tabela": normalizar_texto(obter_primeiro(item, ("Tabela", "tabela", "NomeTabela", "nome_tabela"))),
            "PoliticaTrocas": normalizar_texto(obter_primeiro(item, ("PoliticaTrocas", "politica_trocas"))),
            "ValorTroca": normalizar_decimal(obter_primeiro(item, ("ValorTroca", "valor_troca"))),
            "NovoValor": normalizar_decimal(
                obter_primeiro(item, ("NovoValor", "novo_valor", "ValorNegociado", "valor_negociado"))
            ),
            "PercentualDesconto": normalizar_decimal(
                obter_primeiro(item, ("PercentualDesconto", "percentual_desconto", "DescontoPercentual"))
            ),
            "ValorVendaFinal": normalizar_decimal(
                obter_primeiro(item, ("ValorVendaFinal", "valor_venda_final", "ValorVenda", "valor_venda"))
            ),
            "MargemValor": normalizar_decimal(obter_primeiro(item, ("MargemValor", "margem_valor"))),
            "MargemPercentual": normalizar_decimal(
                obter_primeiro(item, ("MargemPercentual", "margem_percentual"))
            ),
            "DataInicio": normalizar_data(
                obter_primeiro(
                    item,
                    (
                        "DataInicio",
                        "data_inicio",
                        "PeriodoInicio",
                        "periodo_inicio",
                        "DataInicioReserva",
                        "data_inicio_reserva",
                    ),
                )
            ),
            "DataFim": normalizar_data(
                obter_primeiro(
                    item,
                    (
                        "DataFim",
                        "data_fim",
                        "PeriodoTermino",
                        "periodo_termino",
                        "DataFimReserva",
                        "data_fim_reserva",
                    ),
                )
            ),
        }

        if linha["Ordem"] is None:
            linha["Ordem"] = ordem_padrao

        if (linha["DataInicio"] is None) ^ (linha["DataFim"] is None):
            raise ValueError("Preencha Data de e Data até para o mesmo painel/face.")

        if (
            linha["DataInicio"] is not None
            and linha["DataFim"] is not None
            and linha["DataFim"] < linha["DataInicio"]
        ):
            raise ValueError("A Data até não pode ser menor que a Data de.")

        return linha

    def chave_principal(linha: dict) -> tuple[int | None, int | None]:
        return (
            normalizar_inteiro(linha.get("IDDimPaineisEuromidia")),
            normalizar_inteiro(linha.get("IDDimFacesPaineis")),
        )

    def assinatura_estado(linha: dict) -> tuple:
        return (
            normalizar_texto(linha.get("CodPonto")),
            normalizar_texto(linha.get("CodFace")),
            normalizar_texto(linha.get("TipoPainel")),
            normalizar_inteiro(linha.get("AnoCusto")),
            normalizar_decimal(linha.get("CustoTabela")),
            normalizar_inteiro(linha.get("IDDimTabelaPrecosEuromidia")),
            normalizar_texto(linha.get("PeriodoExibicao")),
            normalizar_inteiro(linha.get("ExibicoesDia")),
            normalizar_decimal(linha.get("ValorTabela")),
            normalizar_texto(linha.get("Tabela")),
            normalizar_texto(linha.get("PoliticaTrocas")),
            normalizar_decimal(linha.get("ValorTroca")),
            normalizar_decimal(linha.get("NovoValor")),
            normalizar_decimal(linha.get("PercentualDesconto")),
            normalizar_decimal(linha.get("ValorVendaFinal")),
            normalizar_decimal(linha.get("MargemValor")),
            normalizar_decimal(linha.get("MargemPercentual")),
            normalizar_data(linha.get("DataInicio")),
            normalizar_data(linha.get("DataFim")),
        )

    sql_buscar_ativos = text(
        """
        SELECT
            IDFatoKanbanCardPainelFace,
            IDFatoKanbanCard,
            Ordem,
            IDDimPaineisEuromidia,
            IDDimFacesPaineis,
            CodPonto,
            CodFace,
            TipoPainel,
            AnoCusto,
            CustoTabela,
            IDDimTabelaPrecosEuromidia,
            PeriodoExibicao,
            ExibicoesDia,
            ValorTabela,
            Tabela,
            PoliticaTrocas,
            ValorTroca,
            NovoValor,
            PercentualDesconto,
            ValorVendaFinal,
            MargemValor,
            MargemPercentual,
            DataInicio,
            DataFim,
            Ativo
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace]
        WHERE IDFatoKanbanCard = :id_card
          AND ISNULL(Ativo, 1) = 1
        """
    )

    linhas_ativas = [
        dict(linha._mapping)
        for linha in db.session.execute(sql_buscar_ativos, {"id_card": int(id_card)})
    ]

    ativos_por_chave: dict[tuple[int | None, int | None], dict] = {}
    for linha in linhas_ativas:
        linha_normalizada = {
            "IDFatoKanbanCardPainelFace": normalizar_inteiro(linha.get("IDFatoKanbanCardPainelFace")),
            "Ordem": normalizar_inteiro(linha.get("Ordem")),
            "IDDimPaineisEuromidia": normalizar_inteiro(linha.get("IDDimPaineisEuromidia")),
            "IDDimFacesPaineis": normalizar_inteiro(linha.get("IDDimFacesPaineis")),
            "CodPonto": normalizar_texto(linha.get("CodPonto")),
            "CodFace": normalizar_texto(linha.get("CodFace")),
            "TipoPainel": normalizar_texto(linha.get("TipoPainel")),
            "AnoCusto": normalizar_inteiro(linha.get("AnoCusto")),
            "CustoTabela": normalizar_decimal(linha.get("CustoTabela")),
            "IDDimTabelaPrecosEuromidia": normalizar_inteiro(linha.get("IDDimTabelaPrecosEuromidia")),
            "PeriodoExibicao": normalizar_texto(linha.get("PeriodoExibicao")),
            "ExibicoesDia": normalizar_inteiro(linha.get("ExibicoesDia")),
            "ValorTabela": normalizar_decimal(linha.get("ValorTabela")),
            "Tabela": normalizar_texto(linha.get("Tabela")),
            "PoliticaTrocas": normalizar_texto(linha.get("PoliticaTrocas")),
            "ValorTroca": normalizar_decimal(linha.get("ValorTroca")),
            "NovoValor": normalizar_decimal(linha.get("NovoValor")),
            "PercentualDesconto": normalizar_decimal(linha.get("PercentualDesconto")),
            "ValorVendaFinal": normalizar_decimal(linha.get("ValorVendaFinal")),
            "MargemValor": normalizar_decimal(linha.get("MargemValor")),
            "MargemPercentual": normalizar_decimal(linha.get("MargemPercentual")),
            "DataInicio": normalizar_data(linha.get("DataInicio")),
            "DataFim": normalizar_data(linha.get("DataFim")),
        }

        chave = chave_principal(linha_normalizada)
        if chave[0] and chave[1] and chave not in ativos_por_chave:
            ativos_por_chave[chave] = linha_normalizada

    novos_por_chave: dict[tuple[int | None, int | None], dict] = {}
    for indice, item in enumerate(itens_entrada, start=1):
        if not isinstance(item, dict):
            continue
        linha = normalizar_linha(item, ordem_padrao=indice)
        if not linha_tem_conteudo_minimo(linha):
            continue
        chave = chave_principal(linha)
        if chave[0] and chave[1]:
            novos_por_chave[chave] = linha

    sql_encerrar = text(
        """
        UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
           SET Ativo = 0,
               DataAtualizacao = GETDATE(),
               RemovidoEm = GETDATE(),
               RemovidoPor = :id_usuario
         WHERE IDFatoKanbanCardPainelFace = :id_fato_kanban_card_painel_face
           AND ISNULL(Ativo, 1) = 1
        """
    )

    sql_inserir = text(
        """
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardPainelFace]
        (
            IDFatoKanbanCard,
            Ordem,
            IDDimPaineisEuromidia,
            IDDimFacesPaineis,
            CodPonto,
            CodFace,
            TipoPainel,
            AnoCusto,
            CustoTabela,
            IDDimTabelaPrecosEuromidia,
            PeriodoExibicao,
            ExibicoesDia,
            ValorTabela,
            Tabela,
            PoliticaTrocas,
            ValorTroca,
            NovoValor,
            PercentualDesconto,
            ValorVendaFinal,
            MargemValor,
            MargemPercentual,
            Ativo,
            CriadoEm,
            DataAtualizacao,
            RemovidoEm,
            RemovidoPor,
            IDUsuario,
            IDEmpresaProprietaria,
            DataInicio,
            DataFim
        )
        VALUES
        (
            :id_card,
            :ordem,
            :id_painel,
            :id_face,
            :cod_ponto,
            :cod_face,
            :tipo_painel,
            :ano_custo,
            :custo_tabela,
            :id_tabela_preco,
            :periodo_exibicao,
            :exibicoes_dia,
            :valor_tabela,
            :tabela,
            :politica_trocas,
            :valor_troca,
            :novo_valor,
            :percentual_desconto,
            :valor_venda_final,
            :margem_valor,
            :margem_percentual,
            1,
            GETDATE(),
            GETDATE(),
            NULL,
            NULL,
            :id_usuario,
            :id_empresa_proprietaria,
            :data_inicio,
            :data_fim
        )
        """
    )

    chaves_ativas = set(ativos_por_chave.keys())
    chaves_novas = set(novos_por_chave.keys())

    for chave_removida in sorted(
        chaves_ativas - chaves_novas,
        key=lambda item: (
            item[0] is None,
            item[0] or 0,
            item[1] is None,
            item[1] or 0,
        ),
    ):
        linha_ativa = ativos_por_chave[chave_removida]
        id_linha = linha_ativa.get("IDFatoKanbanCardPainelFace")
        if id_linha:
            db.session.execute(
                sql_encerrar,
                {
                    "id_usuario": id_usuario,
                    "id_fato_kanban_card_painel_face": int(id_linha),
                },
            )

    for chave, linha_nova in novos_por_chave.items():
        linha_ativa = ativos_por_chave.get(chave)

        if linha_ativa:
            assinatura_ativa = assinatura_estado(linha_ativa)
            assinatura_nova = assinatura_estado(linha_nova)

            if assinatura_ativa == assinatura_nova:
                continue

            id_linha = linha_ativa.get("IDFatoKanbanCardPainelFace")
            if id_linha:
                db.session.execute(
                    sql_encerrar,
                    {
                        "id_usuario": id_usuario,
                        "id_fato_kanban_card_painel_face": int(id_linha),
                    },
                )

        db.session.execute(
            sql_inserir,
            {
                "id_card": int(id_card),
                "ordem": linha_nova.get("Ordem"),
                "id_painel": linha_nova.get("IDDimPaineisEuromidia"),
                "id_face": linha_nova.get("IDDimFacesPaineis"),
                "cod_ponto": linha_nova.get("CodPonto"),
                "cod_face": linha_nova.get("CodFace"),
                "tipo_painel": linha_nova.get("TipoPainel"),
                "ano_custo": linha_nova.get("AnoCusto"),
                "custo_tabela": linha_nova.get("CustoTabela"),
                "id_tabela_preco": linha_nova.get("IDDimTabelaPrecosEuromidia"),
                "periodo_exibicao": linha_nova.get("PeriodoExibicao"),
                "exibicoes_dia": linha_nova.get("ExibicoesDia"),
                "valor_tabela": linha_nova.get("ValorTabela"),
                "tabela": linha_nova.get("Tabela"),
                "politica_trocas": linha_nova.get("PoliticaTrocas"),
                "valor_troca": linha_nova.get("ValorTroca"),
                "novo_valor": linha_nova.get("NovoValor"),
                "percentual_desconto": linha_nova.get("PercentualDesconto"),
                "valor_venda_final": linha_nova.get("ValorVendaFinal"),
                "margem_valor": linha_nova.get("MargemValor"),
                "margem_percentual": linha_nova.get("MargemPercentual"),
                "id_usuario": id_usuario,
                "id_empresa_proprietaria": id_empresa_proprietaria,
                "data_inicio": linha_nova.get("DataInicio"),
                "data_fim": linha_nova.get("DataFim"),
            },
        )










def _quebrar_nome_tabela_schema_objeto(nome_tabela: str) -> tuple[str | None, str]:
    partes = [
        str(parte or "").strip().strip("[]").strip()
        for parte in str(nome_tabela or "").strip().split(".")
    ]
    partes = [parte for parte in partes if parte]

    if not partes:
        return None, ""

    if len(partes) == 1:
        return "dbo", partes[0]

    if len(partes) == 2:
        return partes[0], partes[1]

    return partes[-2], partes[-1]


def _obter_tipo_coluna_sql(nome_tabela: str, nome_coluna: str) -> str | None:
    schema_nome, tabela_nome = _quebrar_nome_tabela_schema_objeto(nome_tabela)
    coluna_nome = str(nome_coluna or "").strip().strip("[]").strip()

    if not tabela_nome or not coluna_nome:
        return None

    sql = text("""
        SELECT TOP (1)
            tp.name
        FROM sys.tables t
        INNER JOIN sys.schemas s
            ON s.schema_id = t.schema_id
        INNER JOIN sys.columns c
            ON c.object_id = t.object_id
        INNER JOIN sys.types tp
            ON tp.user_type_id = c.user_type_id
        WHERE s.name = :schema_nome
          AND t.name = :tabela_nome
          AND c.name = :coluna_nome;
    """)

    tipo = db.session.execute(
        sql,
        {
            "schema_nome": schema_nome or "dbo",
            "tabela_nome": tabela_nome,
            "coluna_nome": coluna_nome,
        },
    ).scalar()

    if not tipo:
        return None

    return str(tipo).strip().lower()


def _normalizar_hex_sql(valor: Any) -> str | None:
    if valor is None:
        return None

    texto = str(valor).strip().upper()
    if not texto:
        return None

    if texto.startswith("0X"):
        texto = texto[2:]

    texto = re.sub(r"[^0-9A-F]", "", texto)
    if not texto:
        return None

    if len(texto) % 2 != 0:
        return None

    return texto


def _garantir_versao_concorrencia_card(id_card: int, id_kanban: int) -> str | None:
  
    if not _card_tem_versao_concorrencia():
        return None

    tipo_coluna = _obter_tipo_coluna_sql(TABELA_CARD, "VersaoConcorrencia")
    print(
        f"[KANBAN][_garantir_versao_concorrencia_card] INICIO id_card={id_card} "
        f"id_kanban={id_kanban} tipo_coluna={tipo_coluna!r}"
    )

    if tipo_coluna not in {"rowversion", "timestamp"}:
        try:
            sql_seed = text(f"""
                UPDATE {TABELA_CARD}
                SET VersaoConcorrencia = CONVERT(varbinary(8), CRYPT_GEN_RANDOM(8))
                OUTPUT INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimKanban = :id_kanban
                  AND Ativo = 1
                  AND VersaoConcorrencia IS NULL;
            """)

            valor_seed = db.session.execute(
                sql_seed,
                {
                    "id_card": int(id_card),
                    "id_kanban": int(id_kanban),
                },
            ).scalar()

            if valor_seed is not None:
                db.session.commit()
                _bump_card(int(id_card))
                _bump_kanban(int(id_kanban))

                versao_seed_hex = _rowversion_para_hex(valor_seed)
                print(
                    "[KANBAN][_garantir_versao_concorrencia_card] "
                    f"seed executado com sucesso -> versao_seed_hex={versao_seed_hex!r}"
                )
                if versao_seed_hex:
                    return versao_seed_hex

        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "KANBAN: falha ao semear VersaoConcorrencia para card legado. id_card=%s id_kanban=%s",
                id_card,
                id_kanban,
            )

    sql_ler = text(f"""
        SELECT TOP (1)
            VersaoConcorrencia,
            CONVERT(varchar(34), VersaoConcorrencia, 1) AS VersaoConcorrenciaHexSql
        FROM {TABELA_CARD}
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)

    row = db.session.execute(
        sql_ler,
        {
            "id_card": int(id_card),
            "id_kanban": int(id_kanban),
        },
    ).mappings().first()

    if not row:
        return None

    versao_hex = _rowversion_para_hex(row.get("VersaoConcorrencia"))
    if versao_hex:
        return versao_hex

    return _normalizar_hex_sql(row.get("VersaoConcorrenciaHexSql"))











def _sala_kanban(id_kanban: int) -> str:
    return f"kanban:{int(id_kanban)}"



def _obter_celery_app() -> Any | None:
    return current_app.extensions.get("celery")









def _enfileirar_retry_movimento_card(payload: dict[str, Any]) -> str | None:
    """
    Envio uma operacao de movimento do card para retry rapido no Redis/Celery.
    Uso isso somente quando o retry sincronico por deadlock se esgota.
    """
    celery_app = _obter_celery_app()
    nome_task = current_app.config.get("KANBAN_DEADLOCK_TASK_NAME")
    nome_fila = current_app.config.get("KANBAN_DEADLOCK_QUEUE_NAME", "kanban_retry_rapido")
    countdown = int(current_app.config.get("KANBAN_DEADLOCK_COUNTDOWN", 2) or 2)
    expires = int(current_app.config.get("KANBAN_DEADLOCK_EXPIRES", 30) or 30)

    if not celery_app or not nome_task:
        current_app.logger.warning(
            "Celery/task de deadlock nao configurados. task=%s fila=%s",
            nome_task,
            nome_fila,
        )
        return None

    try:
        task = celery_app.send_task(
            nome_task,
            kwargs={"payload": payload},
            queue=nome_fila,
            countdown=countdown,
            expires=expires,
        )
        return getattr(task, "id", None)
    except Exception:
        current_app.logger.exception(
            "Falha ao enfileirar retry de movimento do card no Redis/Celery. payload=%s",
            payload,
        )
        return None







def _executar_movimento_card_core(
    *,
    id_card: int,
    id_usuario: int,
    id_emp: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Eu executo a transacao principal de mover card.
    Nao dou commit aqui.
    Nao emito socket aqui.
    Nao invalido cache aqui.
    """
    row = _obter_card_autorizado(id_card)
    if not row:
        raise ValueError("Card não encontrado.")

    id_kanban = int(row.get("IDDimKanban") or 0)
    id_fase_de = int(row.get("IDDimKanbanFaseAtual") or 0)

    id_fase_para = int(payload.get("id_fase_para") or 0)
    posicao = str(payload.get("posicao") or "LAST").strip().upper()
    observacao = (payload.get("observacao") or "").strip()
    nota_movimento = (payload.get("nota_movimento") or "").strip()
    versao_concorrencia = payload.get("versao_concorrencia")

    if not id_fase_para:
        raise ValueError("Fase de destino inválida.")

    _obter_kanban_autorizado(id_kanban)

    fase_destino = db.session.execute(
        text(f"""
            SELECT TOP (1)
                f.IDDimKanbanFase,
                f.NomeFase,
                f.TipoFase,
                f.IDEmpresaProprietaria
            FROM {TABELA_KANBAN_FASE} f
            WHERE f.IDDimKanbanFase = :id_fase_para
              AND f.IDDimKanban = :id_kanban
              AND f.Ativo = 1;
        """),
        {
            "id_fase_para": id_fase_para,
            "id_kanban": id_kanban,
        },
    ).mappings().first()

    if not fase_destino:
        raise ValueError("Fase de destino não encontrada.")

    if int(id_fase_para) == 4:
        _validar_preenchimento_empresas_fase_4(
            id_tipo_cliente=_resolver_id_tipo_cliente_desconto_por_bits(row),
            id_empresa_principal=_resolver_id_empresa_principal_por_tipo_cliente(row),
            id_empresa_agencia=_int_ou_none(row.get("IDEmpresaAgencia")),
            id_empresa_bureau=_int_ou_none(row.get("IDEmpresaBureau")),
            id_empresa_cliente_direto=_int_ou_none(row.get("IDEmpresa")),
            contexto="mover o card para a fase 4",
        )

    snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

    has_ordem = _coluna_existe(TABELA_CARD, "OrdemNaFase")
    has_atualizado = _coluna_existe(TABELA_CARD, "AtualizadoEm")
    has_versao = _card_tem_versao_concorrencia()

    versao_concorrencia_bytes = None
    if has_versao:
        versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia)
        if not versao_concorrencia_bytes:
            raise RuntimeError("Versão de concorrência inválida ou ausente.")

    proxima_ordem = None
    if has_ordem:
        sql_next_ordem = text(f"""
            SELECT ISNULL(MAX(fc.OrdemNaFase), 0) + 1
            FROM {TABELA_CARD} fc
            WHERE fc.IDDimKanban = :id_kanban
              AND fc.IDDimKanbanFaseAtual = :id_fase_para
              AND fc.Ativo = 1;
        """)
        proxima_ordem = db.session.execute(
            sql_next_ordem,
            {
                "id_kanban": id_kanban,
                "id_fase_para": id_fase_para,
            },
        ).scalar()

        try:
            proxima_ordem = int(proxima_ordem or 1)
        except Exception:
            proxima_ordem = 1

    row_upd = None

    if has_ordem and has_atualizado:
        output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
        where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
        params_upd = {
            "id_fase_para": id_fase_para,
            "ordem_na_fase": proxima_ordem,
            "id_card": id_card,
        }
        if has_versao:
            params_upd["versao_concorrencia"] = versao_concorrencia_bytes

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET IDDimKanbanFaseAtual = :id_fase_para,
                OrdemNaFase = :ordem_na_fase,
                AtualizadoEm = GETDATE()
            OUTPUT
                INSERTED.IDFatoKanbanCard,
                INSERTED.IDDimKanbanFaseAtual,
                INSERTED.OrdemNaFase,
                INSERTED.AtualizadoEm{output_versao}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1{where_versao};
        """)
        row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

    elif has_ordem and not has_atualizado:
        output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
        where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
        params_upd = {
            "id_fase_para": id_fase_para,
            "ordem_na_fase": proxima_ordem,
            "id_card": id_card,
        }
        if has_versao:
            params_upd["versao_concorrencia"] = versao_concorrencia_bytes

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET IDDimKanbanFaseAtual = :id_fase_para,
                OrdemNaFase = :ordem_na_fase
            OUTPUT
                INSERTED.IDFatoKanbanCard,
                INSERTED.IDDimKanbanFaseAtual,
                INSERTED.OrdemNaFase{output_versao}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1{where_versao};
        """)
        row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

    elif (not has_ordem) and has_atualizado:
        output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
        where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
        params_upd = {
            "id_fase_para": id_fase_para,
            "id_card": id_card,
        }
        if has_versao:
            params_upd["versao_concorrencia"] = versao_concorrencia_bytes

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET IDDimKanbanFaseAtual = :id_fase_para,
                AtualizadoEm = GETDATE()
            OUTPUT
                INSERTED.IDFatoKanbanCard,
                INSERTED.IDDimKanbanFaseAtual,
                INSERTED.AtualizadoEm{output_versao}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1{where_versao};
        """)
        row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

    else:
        output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
        where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
        params_upd = {
            "id_fase_para": id_fase_para,
            "id_card": id_card,
        }
        if has_versao:
            params_upd["versao_concorrencia"] = versao_concorrencia_bytes

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET IDDimKanbanFaseAtual = :id_fase_para
            OUTPUT
                INSERTED.IDFatoKanbanCard,
                INSERTED.IDDimKanbanFaseAtual{output_versao}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1{where_versao};
        """)
        row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

    if not row_upd:
        raise RuntimeError("Este card foi alterado ou movido por outro usuário. Recarregue antes de tentar novamente.")

    nome_fase_destino = str(fase_destino.get("NomeFase") or "").strip()
    tipo_fase_destino = str(fase_destino.get("TipoFase") or "").strip()

    id_status_movimento = _resolver_id_status_card_movimento(
        nome_fase_para=nome_fase_destino,
        card_inativado=False,
    )

    campos_status_pos_mov: list[str] = []
    params_status_pos_mov: dict[str, Any] = {
        "id_card": id_card,
    }

    if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_movimento is not None:
        campos_status_pos_mov.append("IDDimKanbanStatusCard = :id_status_destino")
        params_status_pos_mov["id_status_destino"] = int(id_status_movimento)

    if _coluna_existe(TABELA_CARD, "StatusCard"):
        if id_status_movimento == 3:
            campos_status_pos_mov.append("StatusCard = :status_destino")
            params_status_pos_mov["status_destino"] = "CONCLUIDO"
        elif id_status_movimento == 2:
            campos_status_pos_mov.append("StatusCard = :status_destino")
            params_status_pos_mov["status_destino"] = "CANCELADO"
        elif id_status_movimento == 1:
            campos_status_pos_mov.append("StatusCard = :status_destino")
            params_status_pos_mov["status_destino"] = "ATIVO"

    if _coluna_existe(TABELA_CARD, "EncerradoEm"):
        if id_status_movimento == 3:
            campos_status_pos_mov.append("EncerradoEm = ISNULL(EncerradoEm, GETDATE())")
        else:
            campos_status_pos_mov.append("EncerradoEm = NULL")

    if campos_status_pos_mov:
        sql_status_pos_mov = text(f"""
            UPDATE {TABELA_CARD}
            SET {', '.join(campos_status_pos_mov)}
            WHERE IDFatoKanbanCard = :id_card;
        """)
        db.session.execute(sql_status_pos_mov, params_status_pos_mov)

    if _fase_define_status_concluido(nome_fase_destino, tipo_fase_destino):
        _remover_tag_em_atendimento_do_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_usuario=int(id_usuario),
        )

    id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=row.get("IDEmpresaProprietaria"),
    )

    observacao_movimento = nota_movimento or observacao

    sql_ins = text(f"""
        INSERT INTO {TABELA_CARD_MOVIMENTO}
        (
            IDFatoKanbanCard,
            IDFaseDe,
            IDFasePara,
            MovidoEm,
            MovidoPor,
            Observacao,
            IDEmpresaProprietaria,
            IDDimKanbanTag,
            IDDimKanbanStatusCard,
            IDDimKanban
        )
        OUTPUT INSERTED.IDFatoKanbanCardMovimento
        VALUES
        (
            :id_card,
            :id_fase_de,
            :id_fase_para,
            GETDATE(),
            :movido_por,
            :obs,
            :id_empresa,
            :id_tag,
            :id_status_card,
            :id_kanban
        );
    """)

    row_movimento = db.session.execute(
        sql_ins,
        {
            "id_card": id_card,
            "id_fase_de": id_fase_de,
            "id_fase_para": id_fase_para,
            "movido_por": id_usuario,
            "obs": observacao_movimento[:2000] if observacao_movimento else None,
            "id_empresa": id_empresa_movimento,
            "id_tag": None,
            "id_status_card": int(id_status_movimento) if id_status_movimento is not None else None,
            "id_kanban": int(id_kanban),
        },
    ).mappings().first()

    _registrar_status_historico_card(
        id_card=int(id_card),
        id_fase=int(id_fase_para),
        id_status_card=int(id_status_movimento) if id_status_movimento is not None else None,
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
    )

    if observacao_movimento:
        _registrar_observacao_historica_card(
            id_card=int(id_card),
            texto_observacao=observacao_movimento,
            id_usuario=int(id_usuario),
            id_status_card=int(id_status_movimento) if id_status_movimento is not None else None,
            id_fase=int(id_fase_para),
        )

    tag_contrato_em_avaliacao_aplicada = False
    if int(id_fase_para) == 4:
        tag_contrato_em_avaliacao_aplicada = _aplicar_tag_no_card(
            id_card=int(id_card),
            id_tag=int(ID_TAG_CONTRATO_EM_AVALIACAO),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
        )

    sincronizacao_tag_plano_midia = _sincronizar_tag_plano_midia_por_fase(
        id_card=int(id_card),
        id_fase_atual=int(id_fase_para),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_emp),
    )

    sincronizacao_solicitacao_fase = None
    snapshot_preco_praticado = None
    sincronizacao_contato_contrato = None
    sincronizacao_reservas = {"criadas": 0, "canceladas": 0, "mantidas": 0}

    try:
        sincronizacao_solicitacao_fase = _sincronizar_ativacao_solicitacao_por_fase_do_card(
            id_card=int(id_card),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
        )

        if isinstance(sincronizacao_solicitacao_fase, dict):
            sincronizacao_solicitacao_fase["tag_14_aplicada_na_fase_4"] = bool(tag_contrato_em_avaliacao_aplicada)

        if int(id_fase_para) == 4:
            sincronizacao_reservas = _sincronizar_reservas_painel_faces_kanban(
                id_card=int(id_card),
                titulo_card=str(row.get("Titulo") or "").strip(),
                id_empresa_relacionada=_obter_id_empresa_relacionada_card(row),
                id_usuario=int(id_usuario),
                id_empresa_proprietaria=int(id_emp),
            )
            sincronizacao_contato_contrato = _upsert_dim_contatos_contrato_por_card(
                id_card=int(id_card),
                id_empresa=_resolver_id_empresa_principal_por_tipo_cliente(row),
                id_empresa_proprietaria=int(id_emp),
                id_fato_controle_contratos=_int_ou_none(row.get("IDFatoControleContratosEuromidia")),
            )
            try:
                snapshot_preco_praticado = _sincronizar_snapshot_preco_praticado_fase_4(
                    id_card=int(id_card),
                    id_usuario=int(id_usuario),
                    id_empresa_proprietaria=int(id_emp),
                )

                current_app.logger.warning(
                    "KANBAN SNAPSHOT PRECO PRATICADO FASE 4: id_card=%s resultado=%s",
                    id_card,
                    snapshot_preco_praticado,
                )

                if not snapshot_preco_praticado or not snapshot_preco_praticado.get("ok"):
                    motivo_snapshot = (
                        snapshot_preco_praticado.get("motivo")
                        if isinstance(snapshot_preco_praticado, dict)
                        else "snapshot_preco_praticado_nao_retorno_ok"
                    )
                    current_app.logger.warning(
                        "KANBAN SNAPSHOT PRECO PRATICADO FASE 4 NAO BLOQUEOU O MOVER: id_card=%s motivo=%s",
                        id_card,
                        motivo_snapshot,
                    )
            except Exception as exc_snapshot:
                snapshot_preco_praticado = {
                    "ok": False,
                    "motivo": "erro_ao_sincronizar_snapshot_preco_praticado_fase_4",
                    "erro": str(exc_snapshot),
                }
                current_app.logger.exception(
                    "Falha ao sincronizar snapshot de preço praticado na fase 4 id_card=%s",
                    id_card,
                )

    except Exception as exc:
        raise RuntimeError(
            f"Falha ao sincronizar a solicitação após mover o card: {str(exc)}"
        ) from exc

    snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
    _registrar_log_card(
        id_card=id_card,
        id_kanban=id_kanban,
        id_empresa_proprietaria=id_emp,
        id_usuario_acao=id_usuario,
        tipo_evento="CARD_MOVIDO",
        id_fase_de=id_fase_de,
        id_fase_para=id_fase_para,
        observacao=observacao_movimento or "Card movido entre fases.",
        tabela_origem=TABELA_CARD_MOVIMENTO,
        id_registro_origem=int(row_movimento.get("IDFatoKanbanCardMovimento") or 0) if row_movimento else None,
        payload_antes=snapshot_antes,
        payload_depois=snapshot_depois,
    )

    return {
        "id_card": int(id_card),
        "id_kanban": int(id_kanban),
        "id_fase_de": int(id_fase_de),
        "id_fase_para": int(id_fase_para),
        "ordem_na_fase": proxima_ordem if has_ordem else None,
        "sincronizacao_solicitacao_fase": sincronizacao_solicitacao_fase,
        "snapshot_preco_praticado": snapshot_preco_praticado,
        "sincronizacao_tag_plano_midia": sincronizacao_tag_plano_midia,
        "sincronizacao_reservas": sincronizacao_reservas,
        "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
    }







def _finalizar_pos_movimento_card(
    *,
    id_card: int,
    id_emp: int,
    resultado_core: dict[str, Any],
) -> dict[str, Any]:
    """
    Eu executo o pos-commit comum do movimento.
    Invalido cache, carrego detalhe e emito socket.
    """
    id_kanban = int(resultado_core["id_kanban"])
    id_fase_de = int(resultado_core["id_fase_de"])
    id_fase_para = int(resultado_core["id_fase_para"])
    ordem_na_fase = resultado_core.get("ordem_na_fase")
    sincronizacao_solicitacao_fase = resultado_core.get("sincronizacao_solicitacao_fase")
    snapshot_preco_praticado = resultado_core.get("snapshot_preco_praticado")
    sincronizacao_tag_plano_midia = resultado_core.get("sincronizacao_tag_plano_midia")
    sincronizacao_reservas = resultado_core.get("sincronizacao_reservas")

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    detalhe = _obter_card_detalhe_payload(id_card)

    _emitir_evento_kanban(
        id_kanban,
        "card_movido",
        {
            "id_card": id_card,
            "id_fase_de": id_fase_de,
            "id_fase_para": id_fase_para,
            "ordem_na_fase": ordem_na_fase,
            "card": detalhe.get("card"),
            "tags": detalhe.get("tags", []),
            "notas": detalhe.get("notas", []),
            "snapshot_solicitacao": sincronizacao_solicitacao_fase,
            "snapshot_preco_praticado": snapshot_preco_praticado,
            "sincronizacao_reservas": sincronizacao_reservas,
        },
    )

    return {
        "ok": True,
        "id_card": id_card,
        "id_fase_de": id_fase_de,
        "id_fase_para": id_fase_para,
        "ordem_na_fase": ordem_na_fase,
        "card": detalhe.get("card"),
        "tags": detalhe.get("tags", []),
        "notas": detalhe.get("notas", []),
        "snapshot_solicitacao": sincronizacao_solicitacao_fase,
        "snapshot_preco_praticado": snapshot_preco_praticado,
        "sincronizacao_tag_plano_midia": sincronizacao_tag_plano_midia,
        "sincronizacao_reservas": sincronizacao_reservas,
    }









def _enfileirar_evento_assincrono(nome_evento: str, payload: dict[str, Any]) -> None:
    """
    Gancho opcional para Celery.
    Não transformo operação síncrona do usuário em tarefa assíncrona.
    Uso Celery apenas para pós-processamento: auditoria, integração, indexação etc.
    """
    celery_app = _obter_celery_app()
    nome_task = current_app.config.get("KANBAN_EVENT_TASK_NAME")

    if not celery_app or not nome_task:
        return

    try:
        celery_app.send_task(nome_task, kwargs={"nome_evento": nome_evento, "payload": payload})
    except Exception:
        current_app.logger.exception(
            "Falha ao enviar evento do kanban para Celery. evento=%s payload=%s",
            nome_evento,
            payload,
        )


def _emitir_evento_kanban(id_kanban: int, nome_evento: str, payload: dict[str, Any]) -> None:
    payload_final = _serializar_para_socket({**payload, "id_kanban": int(id_kanban)})
    print(payload_final)

    try:
        socketio.emit(
            nome_evento,
            payload_final,
            namespace=NAMESPACE_SOCKET_KANBAN,
            to=_sala_kanban(id_kanban),
        )
    except Exception:
        current_app.logger.exception(
            "Falha ao emitir evento Socket.IO. evento=%s payload=%s",
            nome_evento,
            payload_final,
        )

    _enfileirar_evento_assincrono(nome_evento=nome_evento, payload=payload_final)



def _cache_get_int(chave: str, padrao: int = 1) -> int:
    valor = cache.get(chave)
    try:
        return int(valor)
    except Exception:
        return padrao



def _cache_inc(chave: str) -> int:
    valor_atual = _cache_get_int(chave, 1)
    novo = valor_atual + 1
    cache.set(chave, novo, timeout=TIMEOUT_CACHE_LONGO * 100)
    return novo



def _versao_empresa(id_emp: int) -> int:
    return _cache_get_int(f"kanban:versao:empresa:{int(id_emp)}", 1)



def _versao_kanban(id_kanban: int) -> int:
    return _cache_get_int(f"kanban:versao:kanban:{int(id_kanban)}", 1)



def _versao_card(id_card: int) -> int:
    return _cache_get_int(f"kanban:versao:card:{int(id_card)}", 1)



def _bump_empresa(id_emp: int) -> None:
    _cache_inc(f"kanban:versao:empresa:{int(id_emp)}")



def _bump_kanban(id_kanban: int) -> None:
    _cache_inc(f"kanban:versao:kanban:{int(id_kanban)}")



def _bump_card(id_card: int) -> None:
    _cache_inc(f"kanban:versao:card:{int(id_card)}")



def _invalidar_kanban(id_emp: int, id_kanban: int, id_card: int | None = None) -> None:
    _bump_empresa(id_emp)
    _bump_kanban(id_kanban)
    if id_card:
        _bump_card(id_card)





from datetime import date, datetime
from decimal import Decimal
from collections.abc import Mapping

def _serializar_para_socket(valor: Any) -> Any:
    if valor is None:
        return None

    if isinstance(valor, (str, int, float, bool)):
        return valor

    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, (datetime, date)):
        return valor.isoformat()

    if isinstance(valor, memoryview):
        valor = valor.tobytes()

    if isinstance(valor, (bytes, bytearray)):
        return bytes(valor).hex().upper()

    if isinstance(valor, Mapping):
        return {str(chave): _serializar_para_socket(v) for chave, v in valor.items()}

    if isinstance(valor, (list, tuple, set)):
        return [_serializar_para_socket(v) for v in valor]

    return str(valor)








def _chave_cache_json(prefixo: str, *partes: Any) -> str:
    partes_str = [prefixo]
    for parte in partes:
        if isinstance(parte, (dict, list, tuple)):
            partes_str.append(json.dumps(parte, sort_keys=True, ensure_ascii=False, default=str))
        else:
            partes_str.append(str(parte))
    return "|".join(partes_str)



def _cache_json_get(chave: str) -> dict[str, Any] | list[Any] | None:
    valor = cache.get(chave)
    return valor if isinstance(valor, (dict, list)) else None



def _cache_json_set(chave: str, valor: dict[str, Any] | list[Any], timeout: int) -> None:
    cache.set(chave, valor, timeout=timeout)



def _objeto_existe(nome_objeto: str) -> bool:
    chave = _chave_cache_json("kanban:schema:objeto", nome_objeto)
    em_cache = cache.get(chave)
    if em_cache is not None:
        return bool(em_cache)

    sql = text("SELECT CASE WHEN OBJECT_ID(:nome_objeto) IS NULL THEN 0 ELSE 1 END;")
    existe = bool(db.session.execute(sql, {"nome_objeto": nome_objeto}).scalar() or 0)
    cache.set(chave, existe, timeout=TIMEOUT_CACHE_LONGO * 100)
    return existe



def _quebrar_nome_banco_schema_tabela(nome_tabela: str) -> tuple[str | None, str | None, str]:
    partes = [
        str(parte or "").strip().strip("[]").strip()
        for parte in str(nome_tabela or "").strip().split(".")
    ]
    partes = [parte for parte in partes if parte]

    if not partes:
        return None, None, ""

    if len(partes) == 1:
        return None, "dbo", partes[0]

    if len(partes) == 2:
        return None, partes[0], partes[1]

    return partes[-3], partes[-2], partes[-1]





def _coluna_existe(nome_tabela: str, nome_coluna: str, ignorar_cache: bool = False) -> bool:
    ignorar_cache = bool(
        ignorar_cache or str(nome_coluna).strip().lower() == "versaoconcorrencia"
    )

    banco_nome, schema_nome, tabela_nome = _quebrar_nome_banco_schema_tabela(nome_tabela)
    coluna_nome = str(nome_coluna or "").strip().strip("[]").strip()

    print(
        f"[KANBAN][_coluna_existe] INICIO nome_tabela={nome_tabela!r} "
        f"nome_coluna={nome_coluna!r} banco={banco_nome!r} schema={schema_nome!r} tabela={tabela_nome!r}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe iniciado. nome_tabela=%r nome_coluna=%r banco=%r schema=%r tabela=%r",
        nome_tabela,
        nome_coluna,
        banco_nome,
        schema_nome,
        tabela_nome,
    )

    if not tabela_nome or not coluna_nome:
        print(
            f"[KANBAN][_coluna_existe] nome inválido. "
            f"tabela={tabela_nome!r} coluna={coluna_nome!r}"
        )
        current_app.logger.warning(
            "KANBAN: _coluna_existe recebeu nome inválido. tabela=%r coluna=%r",
            tabela_nome,
            coluna_nome,
        )
        return False

    chave = _chave_cache_json(
        "kanban:schema:coluna",
        banco_nome or "",
        schema_nome or "dbo",
        tabela_nome,
        coluna_nome,
    )

    if not ignorar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            print(
                f"[KANBAN][_coluna_existe] CACHE HIT banco={banco_nome!r} "
                f"schema={schema_nome!r} tabela={tabela_nome!r} coluna={coluna_nome!r} valor={bool(em_cache)}"
            )
            current_app.logger.info(
                "KANBAN: _coluna_existe cache hit. banco=%r schema=%r tabela=%r coluna=%r valor=%s",
                banco_nome,
                schema_nome,
                tabela_nome,
                coluna_nome,
                bool(em_cache),
            )
            return bool(em_cache)

    if banco_nome:
        sql = text(f"""
            SELECT TOP (1)
                1
            FROM [{banco_nome}].sys.tables t
            INNER JOIN [{banco_nome}].sys.schemas s
                ON s.schema_id = t.schema_id
            INNER JOIN [{banco_nome}].sys.columns c
                ON c.object_id = t.object_id
            WHERE s.name = :schema_nome
              AND t.name = :tabela_nome
              AND c.name = :coluna_nome;
        """)
    else:
        sql = text("""
            SELECT TOP (1)
                1
            FROM sys.tables t
            INNER JOIN sys.schemas s
                ON s.schema_id = t.schema_id
            INNER JOIN sys.columns c
                ON c.object_id = t.object_id
            WHERE s.name = :schema_nome
              AND t.name = :tabela_nome
              AND c.name = :coluna_nome;
        """)

    resultado = db.session.execute(
        sql,
        {
            "schema_nome": schema_nome or "dbo",
            "tabela_nome": tabela_nome,
            "coluna_nome": coluna_nome,
        },
    ).scalar()

    existe = bool(resultado)

    print(
        f"[KANBAN][_coluna_existe] RESULTADO banco={banco_nome!r} schema={schema_nome!r} "
        f"tabela={tabela_nome!r} coluna={coluna_nome!r} existe={existe}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe resultado. banco=%r schema=%r tabela=%r coluna=%r existe=%s",
        banco_nome,
        schema_nome,
        tabela_nome,
        coluna_nome,
        existe,
    )

    if not ignorar_cache:
        cache.set(chave, existe, timeout=TIMEOUT_CACHE_LONGO)
        print(
            f"[KANBAN][_coluna_existe] CACHE SET banco={banco_nome!r} schema={schema_nome!r} "
            f"tabela={tabela_nome!r} coluna={coluna_nome!r} valor={existe}"
        )
        current_app.logger.info(
            "KANBAN: _coluna_existe cache set. banco=%r schema=%r tabela=%r coluna=%r valor=%s timeout=%s",
            banco_nome,
            schema_nome,
            tabela_nome,
            coluna_nome,
            existe,
            TIMEOUT_CACHE_LONGO,
        )

    return existe





    def _quebrar_nome_tabela(nome: str) -> tuple[str | None, str]:
        bruto = str(nome or "").strip()
        if not bruto:
            return None, ""

        partes = [_limpar_identificador(p) for p in bruto.split(".") if _limpar_identificador(p)]

        if not partes:
            return None, ""

        if len(partes) == 1:
            return "dbo", partes[0]

        if len(partes) == 2:
            return partes[0], partes[1]

        return partes[-2], partes[-1]

    """
    Eu continuo ignorando cache automaticamente para VersaoConcorrencia,
    porque ela já era um caso especial no seu código.
    Além disso, agora também posso forçar ignorar cache externamente.
    """
    ignorar_cache = bool(
        ignorar_cache or str(nome_coluna).strip().lower() == "versaoconcorrencia"
    )

    schema_nome, tabela_nome = _quebrar_nome_tabela(nome_tabela)
    coluna_nome = _limpar_identificador(nome_coluna)

    chave = _chave_cache_json(
        "kanban:schema:coluna",
        schema_nome or "dbo",
        tabela_nome,
        coluna_nome,
    )

    if not ignorar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            print(
                f"[KANBAN][_coluna_existe] CACHE HIT schema={schema_nome!r} tabela={tabela_nome!r} "
                f"coluna={coluna_nome!r} valor={bool(em_cache)}"
            )
            current_app.logger.info(
                "KANBAN: _coluna_existe cache hit. schema=%r tabela=%r coluna=%r valor=%s",
                schema_nome,
                tabela_nome,
                coluna_nome,
                bool(em_cache),
            )
            return bool(em_cache)

    print(
        f"[KANBAN][_coluna_existe] CONSULTANDO BANCO nome_tabela_original={nome_tabela!r} "
        f"schema_resolvido={schema_nome!r} tabela_resolvida={tabela_nome!r} "
        f"coluna={coluna_nome!r} ignorar_cache={ignorar_cache}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe consultando banco. nome_tabela_original=%r schema_resolvido=%r "
        "tabela_resolvida=%r coluna=%r ignorar_cache=%s",
        nome_tabela,
        schema_nome,
        tabela_nome,
        coluna_nome,
        ignorar_cache,
    )

    if not tabela_nome or not coluna_nome:
        print(
            f"[KANBAN][_coluna_existe] nome inválido -> schema={schema_nome!r} tabela={tabela_nome!r} coluna={coluna_nome!r}"
        )
        current_app.logger.warning(
            "KANBAN: _coluna_existe recebeu nome inválido. nome_tabela=%r nome_coluna=%r",
            nome_tabela,
            nome_coluna,
        )
        return False

    sql = text("""
        SELECT TOP (1)
            1
        FROM sys.tables t
        INNER JOIN sys.schemas s
            ON s.schema_id = t.schema_id
        INNER JOIN sys.columns c
            ON c.object_id = t.object_id
        WHERE s.name = :schema_nome
          AND t.name = :tabela_nome
          AND c.name = :coluna_nome;
    """)

    resultado = db.session.execute(
        sql,
        {
            "schema_nome": schema_nome or "dbo",
            "tabela_nome": tabela_nome,
            "coluna_nome": coluna_nome,
        },
    ).scalar()

    existe = bool(resultado)

    print(
        f"[KANBAN][_coluna_existe] RESULTADO schema={schema_nome!r} tabela={tabela_nome!r} "
        f"coluna={coluna_nome!r} existe={existe}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe resultado. schema=%r tabela=%r coluna=%r existe=%s",
        schema_nome,
        tabela_nome,
        coluna_nome,
        existe,
    )

    if not ignorar_cache:
        cache.set(chave, existe, timeout=TIMEOUT_CACHE_LONGO)
        print(
            f"[KANBAN][_coluna_existe] CACHE SET schema={schema_nome!r} tabela={tabela_nome!r} "
            f"coluna={coluna_nome!r} valor={existe}"
        )
        current_app.logger.info(
            "KANBAN: _coluna_existe cache set. schema=%r tabela=%r coluna=%r valor=%s timeout=%s",
            schema_nome,
            tabela_nome,
            coluna_nome,
            existe,
            TIMEOUT_CACHE_LONGO,
        )

    return existe


def _nome_coluna_empresa_relacionada_card() -> str | None:
    """
    Retorna a coluna da FatoKanbanCard usada para vincular a empresa ao card.

    Regra atual do kanban:
    - a coluna correta é IDEmpresa
    - mantenho fallback apenas por compatibilidade com estruturas antigas
    """
    for nome_coluna in ("IDEmpresa", "IDCliente", "IDEmpresaRelacionada"):
        if _coluna_existe(TABELA_CARD, nome_coluna, ignorar_cache=True):
            return nome_coluna

    return None


def _nome_coluna_usuario_relacionado_card() -> str | None:
    for nome_coluna in ("IDVendedorUsuario", "IDDimUsuarios"):
        if _coluna_existe(TABELA_CARD, nome_coluna):
            return nome_coluna
    return None



def _sql_select_empresa_relacionada_card(alias_card: str = "c") -> str:
    nome_coluna = _nome_coluna_empresa_relacionada_card()
    if nome_coluna:
        return f"{alias_card}.{nome_coluna} AS IDEmpresaRelacionadaCard"
    return "CAST(NULL AS int) AS IDEmpresaRelacionadaCard"




def _sql_select_versao_concorrencia_card(alias_card: str = "c") -> str:
    print(
        f"[KANBAN][_sql_select_versao_concorrencia_card] INICIO alias_card={alias_card!r} TABELA_CARD={TABELA_CARD!r}"
    )
    current_app.logger.info(
        "KANBAN: _sql_select_versao_concorrencia_card iniciado. alias_card=%r TABELA_CARD=%r",
        alias_card,
        TABELA_CARD,
    )

    tem_versao = _card_tem_versao_concorrencia()

    if tem_versao:
        sql_select = f"{alias_card}.VersaoConcorrencia AS VersaoConcorrencia"
        print(
            f"[KANBAN][_sql_select_versao_concorrencia_card] usando coluna real -> {sql_select}"
        )
        current_app.logger.info(
            "KANBAN: _sql_select_versao_concorrencia_card usando coluna real. sql_select=%r",
            sql_select,
        )
        return sql_select

    sql_fallback = "CAST(NULL AS varbinary(8)) AS VersaoConcorrencia"
    print(
        f"[KANBAN][_sql_select_versao_concorrencia_card] coluna não encontrada -> usando fallback {sql_fallback}"
    )
    current_app.logger.warning(
        "KANBAN: _sql_select_versao_concorrencia_card não encontrou coluna VersaoConcorrencia em %r. Usando fallback.",
        TABELA_CARD,
    )
    return sql_fallback




def _sql_select_usuario_relacionado_card(alias_card: str = "c") -> str:
    nome_coluna = _nome_coluna_usuario_relacionado_card()
    if nome_coluna:
        return f"{alias_card}.{nome_coluna} AS IDUsuarioRelacionadoCard"
    return "CAST(NULL AS int) AS IDUsuarioRelacionadoCard"

def _card_tem_versao_concorrencia() -> bool:
    print(
        f"[KANBAN][_card_tem_versao_concorrencia] verificando coluna VersaoConcorrencia em TABELA_CARD={TABELA_CARD!r}"
    )
    current_app.logger.info(
        "KANBAN: _card_tem_versao_concorrencia verificando coluna. TABELA_CARD=%r",
        TABELA_CARD,
    )

    existe = _coluna_existe(TABELA_CARD, "VersaoConcorrencia")

    print(
        f"[KANBAN][_card_tem_versao_concorrencia] resultado existe={existe} para TABELA_CARD={TABELA_CARD!r}"
    )
    current_app.logger.info(
        "KANBAN: _card_tem_versao_concorrencia resultado. TABELA_CARD=%r existe=%s",
        TABELA_CARD,
        existe,
    )

    return existe


    def _sql_select_versao_concorrencia_card(alias_card: str = "c") -> str:

        print(
            f"[KANBAN][_sql_select_versao_concorrencia_card] INICIO alias_card={alias_card!r} TABELA_CARD={TABELA_CARD!r}"
        )
        current_app.logger.info(
            "KANBAN: _sql_select_versao_concorrencia_card iniciado. alias_card=%r TABELA_CARD=%r",
            alias_card,
            TABELA_CARD,
        )

        tem_versao = _card_tem_versao_concorrencia()

        if tem_versao:
            sql_select = f"{alias_card}.VersaoConcorrencia AS VersaoConcorrencia"
            print(
                f"[KANBAN][_sql_select_versao_concorrencia_card] usando coluna real -> {sql_select}"
            )
            current_app.logger.info(
                "KANBAN: _sql_select_versao_concorrencia_card usando coluna real. sql_select=%r",
                sql_select,
            )
            return sql_select

        sql_fallback = "CAST(NULL AS varbinary(8)) AS VersaoConcorrencia"
        print(
            f"[KANBAN][_sql_select_versao_concorrencia_card] coluna não encontrada -> usando fallback {sql_fallback}"
        )
        current_app.logger.warning(
            "KANBAN: _sql_select_versao_concorrencia_card não encontrou coluna VersaoConcorrencia em %r. "
            "Usando fallback.",
            TABELA_CARD,
        )
        return sql_fallback



def _sql_join_empresa_relacionada_card(alias_card: str = "c", alias_empresa: str = "e", alias_cnae: str = "cn") -> str:
    nome_coluna = _nome_coluna_empresa_relacionada_card()
    if nome_coluna:
        return f"""
        LEFT JOIN {TABELA_EMPRESAS} {alias_empresa}
          ON {alias_empresa}.IDEmpresa = {alias_card}.{nome_coluna}
        LEFT JOIN {TABELA_CNAES} {alias_cnae}
          ON {alias_cnae}.cnaepadrao = {alias_empresa}.CNAE
        """.strip()

    return f"""
    LEFT JOIN {TABELA_EMPRESAS} {alias_empresa}
      ON 1 = 0
    LEFT JOIN {TABELA_CNAES} {alias_cnae}
      ON 1 = 0
    """.strip()






def _obter_id_empresa_relacionada_card(card: Mapping[str, Any] | dict[str, Any] | None) -> int | None:
    if not card:
        return None

    id_principal = _resolver_id_empresa_principal_por_tipo_cliente(card)
    if id_principal is not None:
        return id_principal

    for chave in ("IDEmpresaRelacionadaCard", "IDEmpresa", "IDEmpresaAgencia", "IDEmpresaBureau", "IDCliente", "IDEmpresaRelacionada"):
        valor = card.get(chave)
        if valor not in (None, ""):
            try:
                return int(valor)
            except Exception:
                return None
    return None


def _obter_id_usuario_relacionado_card(card: Mapping[str, Any] | dict[str, Any] | None) -> int | None:
    if not card:
        return None

    for chave in ("IDUsuarioRelacionadoCard", "IDVendedorUsuario", "IDDimUsuarios"):
        valor = card.get(chave)
        if valor not in (None, ""):
            try:
                return int(valor)
            except Exception:
                return None
    return None



def _json_para_log(valor: Any) -> str | None:
    if valor is None:
        return None
    try:
        return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(valor)



def _cor_hex_valida(cor_hex: str | None) -> bool:
    if not cor_hex:
        return False
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", cor_hex.strip()))



def _normalizar_texto_comparacao(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"\s+", " ", texto)
    return texto



def _normalizar_codigo_dominio(valor: Any) -> str:
    return str(valor or "").strip().upper()


def _status_card_tabela_existe() -> bool:
    return _objeto_existe(TABELA_STATUS_CARD)


def _obter_status_card_configurados(*, incluir_inativos: bool = False) -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:dominio:status_card", incluir_inativos)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    resultado: list[dict[str, Any]] = []

    if _status_card_tabela_existe():
        where_ativo = "" if incluir_inativos else "WHERE ISNULL(sc.Ativo, 1) = 1"
        sql = text(f"""
            SELECT
                sc.IDDimKanbanStatusCard,
                sc.CodigoStatus,
                sc.NomeExibicao,
                ISNULL(sc.Ativo, 1) AS Ativo,
                sc.CriadoEm,
                sc.AtualizadoEm
            FROM {TABELA_STATUS_CARD} sc
            {where_ativo}
            ORDER BY sc.IDDimKanbanStatusCard ASC;
        """)
        rows = db.session.execute(sql).mappings().all()
        for row in rows:
            codigo = _normalizar_codigo_dominio(row.get("CodigoStatus"))
            if not codigo:
                continue
            resultado.append(
                {
                    "IDDimKanbanStatusCard": row.get("IDDimKanbanStatusCard"),
                    "CodigoStatus": codigo,
                    "NomeExibicao": str(row.get("NomeExibicao") or codigo.title()).strip(),
                    "Ativo": int(row.get("Ativo") or 0),
                    "BitPadrao": 1 if codigo == STATUS_CARD_FALLBACK_PADRAO else 0,
                    "BitStatusFinal": 1 if codigo in {"CONCLUIDO", STATUS_CARD_FALLBACK_INATIVACAO} else 0,
                    "BitStatusInativacao": 1 if codigo == STATUS_CARD_FALLBACK_INATIVACAO else 0,
                    "BitExibirNoKanban": 1,
                }
            )
    else:
        sql = text(f"""
            SELECT DISTINCT c.StatusCard AS CodigoStatus
            FROM {TABELA_CARD} c
            WHERE NULLIF(LTRIM(RTRIM(ISNULL(c.StatusCard, ''))), '') IS NOT NULL
            ORDER BY c.StatusCard ASC;
        """)
        rows = db.session.execute(sql).mappings().all()
        for row in rows:
            codigo = _normalizar_codigo_dominio(row.get("CodigoStatus"))
            if not codigo:
                continue
            resultado.append(
                {
                    "IDDimKanbanStatusCard": None,
                    "CodigoStatus": codigo,
                    "NomeExibicao": codigo.title(),
                    "Ativo": 1,
                    "BitPadrao": 1 if codigo == STATUS_CARD_FALLBACK_PADRAO else 0,
                    "BitStatusFinal": 1 if codigo in {"CONCLUIDO", STATUS_CARD_FALLBACK_INATIVACAO} else 0,
                    "BitStatusInativacao": 1 if codigo == STATUS_CARD_FALLBACK_INATIVACAO else 0,
                    "BitExibirNoKanban": 1,
                }
            )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _obter_status_card_validos() -> set[str]:
    return {
        _normalizar_codigo_dominio(item.get("CodigoStatus"))
        for item in _obter_status_card_configurados()
        if _normalizar_codigo_dominio(item.get("CodigoStatus"))
    }


def _obter_status_card_por_codigo(codigo_status: Any) -> dict[str, Any] | None:
    codigo_normalizado = _normalizar_codigo_dominio(codigo_status)
    if not codigo_normalizado:
        return None
    for item in _obter_status_card_configurados(incluir_inativos=True):
        if _normalizar_codigo_dominio(item.get("CodigoStatus")) == codigo_normalizado:
            return item
    return None


def _obter_id_status_card_por_codigo(codigo_status: Any) -> int | None:
    item = _obter_status_card_por_codigo(codigo_status)
    if not item:
        return None
    valor = item.get("IDDimKanbanStatusCard")
    try:
        return int(valor) if valor is not None else None
    except Exception:
        return None


def _obter_status_card_padrao() -> str:
    if STATUS_CARD_FALLBACK_PADRAO in _obter_status_card_validos():
        return STATUS_CARD_FALLBACK_PADRAO
    for item in _obter_status_card_configurados():
        if int(item.get("BitPadrao") or 0) == 1:
            codigo = _normalizar_codigo_dominio(item.get("CodigoStatus"))
            if codigo:
                return codigo
    for item in _obter_status_card_configurados():
        codigo = _normalizar_codigo_dominio(item.get("CodigoStatus"))
        if codigo:
            return codigo
    return STATUS_CARD_FALLBACK_PADRAO


def _obter_status_card_inativacao() -> str:
    if STATUS_CARD_FALLBACK_INATIVACAO in _obter_status_card_validos():
        return STATUS_CARD_FALLBACK_INATIVACAO
    for item in _obter_status_card_configurados():
        if int(item.get("BitStatusInativacao") or 0) == 1:
            codigo = _normalizar_codigo_dominio(item.get("CodigoStatus"))
            if codigo:
                return codigo
    return STATUS_CARD_FALLBACK_INATIVACAO






"""Eu busco os itens painel/face vinculados ao card para compor o histórico."""
def _buscar_itens_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        i.IDFatoKanbanCardPainelFace AS id_item,
        i.IDFatoKanbanCard AS id_card,
        i.Ordem AS ordem,
        i.IDDimPaineisEuromidia AS id_painel,
        i.IDDimFacesPaineis AS id_face_painel,
        i.CodPonto AS cod_ponto,
        i.CodFace AS cod_face,
        i.TipoPainel AS tipo_painel,
        i.AnoCusto AS ano_custo,
        i.CustoTabela AS custo_tabela,
        i.IDDimTabelaPrecosEuromidia AS id_tabela_preco,
        i.PeriodoExibicao AS periodo_exibicao,
        i.ExibicoesDia AS exibicoes_dia,
        i.ValorTabela AS valor_tabela,
        i.Tabela AS tabela,
        i.PoliticaTrocas AS politica_trocas,
        i.ValorTroca AS valor_troca,
        i.NovoValor AS novo_valor,
        i.PercentualDesconto AS percentual_desconto,
        i.ValorVendaFinal AS valor_venda_final,
        i.MargemValor AS margem_valor,
        i.MargemPercentual AS margem_percentual,
        i.Ativo AS ativo,
        i.CriadoEm AS criado_em,
        i.DataAtualizacao AS atualizado_em,
        i.RemovidoEm AS removido_em,
        i.RemovidoPor AS removido_por,
        i.IDUsuario AS id_usuario,
        i.IDEmpresaProprietaria AS id_empresa_proprietaria_evento
    FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = i.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria
    WHERE i.IDFatoKanbanCard = :id_card
    ORDER BY
        ISNULL(i.Ordem, 999999),
        i.CriadoEm DESC,
        i.IDFatoKanbanCardPainelFace DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )















def _status_card_eh_final(status_card: Any) -> bool:
    item = _obter_status_card_por_codigo(status_card)
    if item is not None:
        return int(item.get("BitStatusFinal") or 0) == 1
    codigo = _normalizar_codigo_dominio(status_card)
    return codigo in {"CONCLUIDO", STATUS_CARD_FALLBACK_INATIVACAO}


def _sql_filtro_status_card_visiveis(alias_card: str = "c") -> str:
    if _status_card_tabela_existe():
        return f"""
          AND EXISTS (
                SELECT 1
                FROM {TABELA_STATUS_CARD} sc
                WHERE ISNULL(sc.Ativo, 1) = 1
                  AND UPPER(LTRIM(RTRIM(ISNULL(sc.CodigoStatus, '')))) = UPPER(LTRIM(RTRIM(ISNULL({alias_card}.StatusCard, ''))))
          )
        """.rstrip()
    return f"AND NULLIF(LTRIM(RTRIM(ISNULL({alias_card}.StatusCard, ''))), '') IS NOT NULL"


def _obter_tipos_fase_configurados(id_kanban: int | None = None, id_emp: int | None = None) -> list[str]:
    if id_kanban is None and id_emp is None:
        return []

    chave = _chave_cache_json("kanban:dominio:tipos_fase", id_kanban, id_emp)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    if id_kanban is not None:
        sql = text(f"""
            SELECT DISTINCT f.TipoFase
            FROM {TABELA_KANBAN_FASE} f
            WHERE f.IDDimKanban = :id_kanban
              AND f.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(f.TipoFase, ''))), '') IS NOT NULL
            ORDER BY f.TipoFase ASC;
        """)
        params = {"id_kanban": int(id_kanban)}
    else:
        sql = text(f"""
            SELECT DISTINCT f.TipoFase
            FROM {TABELA_KANBAN_FASE} f
            JOIN {TABELA_KANBAN} k
              ON k.IDDimKanban = f.IDDimKanban
            WHERE k.IDEmpresaProprietaria = :id_emp
              AND k.Ativo = 1
              AND f.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(f.TipoFase, ''))), '') IS NOT NULL
            ORDER BY f.TipoFase ASC;
        """)
        params = {"id_emp": int(id_emp)}

    rows = db.session.execute(sql, params).mappings().all()
    resultado = [
        _normalizar_codigo_dominio(row.get("TipoFase"))
        for row in rows
        if _normalizar_codigo_dominio(row.get("TipoFase"))
    ]
    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _obter_tipos_tag_configurados(id_kanban: int | None = None, id_emp: int | None = None) -> list[str]:
    tabela_tag = "[Kanban].[Silver].[DimKanbanTag]"
    if not _objeto_existe(tabela_tag):
        return []
    if id_kanban is None and id_emp is None:
        return []

    chave = _chave_cache_json("kanban:dominio:tipos_tag", id_kanban, id_emp)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    if id_kanban is not None:
        sql = text(f"""
            SELECT DISTINCT t.TipoTag
            FROM {tabela_tag} t
            WHERE t.IDDimKanban = :id_kanban
              AND t.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(t.TipoTag, ''))), '') IS NOT NULL
            ORDER BY t.TipoTag ASC;
        """)
        params = {"id_kanban": int(id_kanban)}
    else:
        sql = text(f"""
            SELECT DISTINCT t.TipoTag
            FROM {tabela_tag} t
            JOIN {TABELA_KANBAN} k
              ON k.IDDimKanban = t.IDDimKanban
            WHERE k.IDEmpresaProprietaria = :id_emp
              AND k.Ativo = 1
              AND t.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(t.TipoTag, ''))), '') IS NOT NULL
            ORDER BY t.TipoTag ASC;
        """)
        params = {"id_emp": int(id_emp)}

    rows = db.session.execute(sql, params).mappings().all()
    resultado = [
        _normalizar_codigo_dominio(row.get("TipoTag"))
        for row in rows
        if _normalizar_codigo_dominio(row.get("TipoTag"))
    ]
    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _obter_tipo_fase_padrao(id_kanban: int, id_emp: int) -> str:
    tipos = _obter_tipos_fase_configurados(id_kanban=id_kanban, id_emp=id_emp)
    return tipos[0] if tipos else TIPO_FASE_FALLBACK_PADRAO


def _obter_tipo_tag_padrao(id_kanban: int, id_emp: int) -> str:
    tipos = _obter_tipos_tag_configurados(id_kanban=id_kanban, id_emp=id_emp)
    return tipos[0] if tipos else TIPO_TAG_FALLBACK_PADRAO


def _obter_motivos_inativacao_configurados() -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:dominio:motivos_inativacao")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    resultado: list[dict[str, Any]] = []

    if _objeto_existe(TABELA_MOTIVO_INATIVACAO_CARD):
        sql = text(f"""
            SELECT
                m.IDDimKanbanMotivoInativacaoCard,
                m.CodigoMotivo,
                m.NomeExibicao,
                ISNULL(m.BitAtivo, 1) AS BitAtivo
            FROM {TABELA_MOTIVO_INATIVACAO_CARD} m
            WHERE ISNULL(m.BitAtivo, 1) = 1
            ORDER BY m.IDDimKanbanMotivoInativacaoCard ASC;
        """)
        rows = db.session.execute(sql).mappings().all()
        for row in rows:
            codigo = _normalizar_codigo_dominio(row.get("CodigoMotivo"))
            nome = str(row.get("NomeExibicao") or "").strip()
            if not codigo or not nome:
                continue
            resultado.append(
                {
                    "ID": row.get("IDDimKanbanMotivoInativacaoCard"),
                    "Codigo": codigo,
                    "Descricao": nome,
                }
            )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _normalizar_motivo_inativacao_card(valor: Any) -> dict[str, Any] | None:
    texto_original = str(valor or "").strip()
    if not texto_original:
        return None

    texto_comparacao = _normalizar_texto_comparacao(texto_original)
    for item in _obter_motivos_inativacao_configurados():
        if texto_original.isdigit() and item.get("ID") is not None and int(item.get("ID")) == int(texto_original):
            return item
        codigo = _normalizar_texto_comparacao(item.get("Codigo"))
        descricao = _normalizar_texto_comparacao(item.get("Descricao"))
        if texto_comparacao in {codigo, descricao}:
            return item
    return None


def _fase_define_status_concluido(nome_fase: Any, tipo_fase: Any) -> bool:
    return _normalizar_texto_comparacao(nome_fase) == "concluido" or _normalizar_codigo_dominio(tipo_fase) == "SUCESSO"


def _obter_status_card_para_fase(id_fase: int) -> str:
    try:
        fase = _obter_fase_autorizada(id_fase, incluir_inativa=True)
    except Exception:
        return _obter_status_card_padrao()

    if _fase_define_status_concluido(fase.get("NomeFase"), fase.get("TipoFase")):
        if "CONCLUIDO" in _obter_status_card_validos():
            return "CONCLUIDO"
    return _obter_status_card_padrao()



def _inserir_registro_dinamico(nome_tabela: str, valores: dict[str, Any], colunas_getdate: tuple[str, ...] = ()) -> None:
    if not _objeto_existe(nome_tabela):
        return

    colunas_params: list[str] = []
    params: dict[str, Any] = {}
    colunas_literais: list[tuple[str, str]] = []

    for coluna, valor in valores.items():
        if valor is None or not _coluna_existe(nome_tabela, coluna):
            continue
        colunas_params.append(coluna)
        params[coluna] = valor

    for coluna in colunas_getdate:
        if _coluna_existe(nome_tabela, coluna) and coluna not in colunas_params:
            colunas_literais.append((coluna, "GETDATE()"))

    if not colunas_params and not colunas_literais:
        return

    colunas_insert = [f"[{col}]" for col in colunas_params] + [f"[{col}]" for col, _ in colunas_literais]
    valores_insert = [f":{col}" for col in colunas_params] + [expr for _, expr in colunas_literais]

    sql = text(
        f"INSERT INTO {nome_tabela} ({', '.join(colunas_insert)}) VALUES ({', '.join(valores_insert)});"
    )
    db.session.execute(sql, params)








def _inserir_registro_dinamico_output_id(
    nome_tabela: str,
    coluna_id_output: str,
    valores: dict[str, Any],
    colunas_getdate: tuple[str, ...] = (),
) -> int | None:
    """
    Insere dinamicamente e devolve o ID gerado pelo OUTPUT INSERTED.
    """
    if not _objeto_existe(nome_tabela):
        raise RuntimeError(f"A tabela {nome_tabela} não existe.")

    if not _coluna_existe(nome_tabela, coluna_id_output):
        raise RuntimeError(
            f"A coluna de retorno {coluna_id_output} não existe em {nome_tabela}."
        )

    colunas_parametros: list[str] = []
    parametros: dict[str, Any] = {}
    colunas_literais: list[tuple[str, str]] = []

    for coluna, valor in valores.items():
        if valor is None:
            continue
        if not _coluna_existe(nome_tabela, coluna):
            continue

        colunas_parametros.append(coluna)
        parametros[coluna] = valor

    for coluna in colunas_getdate:
        if _coluna_existe(nome_tabela, coluna) and coluna not in colunas_parametros:
            colunas_literais.append((coluna, "GETDATE()"))

    if not colunas_parametros and not colunas_literais:
        raise RuntimeError(
            f"Nenhuma coluna válida foi encontrada para inserir em {nome_tabela}."
        )

    colunas_insert = [f"[{coluna}]" for coluna in colunas_parametros]
    colunas_insert += [f"[{coluna}]" for coluna, _ in colunas_literais]

    valores_insert = [f":{coluna}" for coluna in colunas_parametros]
    valores_insert += [literal for _, literal in colunas_literais]

    sql = text(
        f"""
        INSERT INTO {nome_tabela}
        (
            {", ".join(colunas_insert)}
        )
        OUTPUT INSERTED.[{coluna_id_output}]
        VALUES
        (
            {", ".join(valores_insert)}
        );
        """
    )

    valor_id = db.session.execute(sql, parametros).scalar()
    return int(valor_id) if valor_id not in (None, "") else None


def _atualizar_registro_dinamico_por_id(
    nome_tabela: str,
    coluna_id: str,
    valor_id: Any,
    valores: dict[str, Any],
    colunas_getdate: tuple[str, ...] = (),
) -> bool:
    if valor_id in (None, ""):
        return False

    if not _objeto_existe(nome_tabela) or not _coluna_existe(nome_tabela, coluna_id):
        return False

    sets: list[str] = []
    params: dict[str, Any] = {"valor_id_registro": valor_id}

    for coluna, valor in valores.items():
        if not _coluna_existe(nome_tabela, coluna):
            continue
        sets.append(f"[{coluna}] = :{coluna}")
        params[coluna] = valor

    for coluna in colunas_getdate:
        if _coluna_existe(nome_tabela, coluna) and coluna not in valores:
            sets.append(f"[{coluna}] = GETDATE()")

    if not sets:
        return False

    sql = text(
        f"""
        UPDATE {nome_tabela}
           SET {', '.join(sets)}
         WHERE [{coluna_id}] = :valor_id_registro;
        """
    )
    db.session.execute(sql, params)
    return True


def _obter_solicitacao_contrato_ativa_por_card(id_card: int) -> dict[str, Any] | None:
    if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO):
        return None

    filtro_ativo = ""
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "BitAtivo"):
        filtro_ativo = "AND ISNULL(BitAtivo, 0) = 1"

    sql = text(
        f"""
        SELECT TOP (1)
            IDFatoSolicitacaoContratoEuromidia,
            IDFatoKanbanCard,
            TipoSolicitacao,
            IDDimStatusContratos
        FROM {TABELA_SOLICITACAO_CONTRATO}
        WHERE IDFatoKanbanCard = :id_card
          {filtro_ativo}
        ORDER BY
            DataAtualizacao DESC,
            DataCriacao DESC,
            IDFatoSolicitacaoContratoEuromidia DESC;
        """
    )

    row = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()
    return dict(row) if row else None


def _card_tem_tag_contrato_em_avaliacao(id_card: int) -> bool:
    sql = text(
        """
        SELECT TOP (1) 1
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        WHERE ct.IDFatoKanbanCard = :id_card
          AND ct.IDDimKanbanTag = :id_tag;
        """
    )
    valor = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_tag": int(ID_TAG_CONTRATO_EM_AVALIACAO),
        },
    ).scalar()
    return bool(valor)


def _obter_nome_coluna_atividade_solicitacao_item() -> str | None:
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "BitSolicitacaoAtiva"):
        return "BitSolicitacaoAtiva"
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "BitAtivo"):
        return "BitAtivo"
    return None


def _normalizar_valor_snapshot_solicitacao(valor: Any) -> Any:
    if valor is None:
        return None

    if isinstance(valor, Decimal):
        return format(valor.quantize(Decimal("0.0001")), "f")

    if isinstance(valor, float):
        try:
            return format(Decimal(str(valor)).quantize(Decimal("0.0001")), "f")
        except Exception:
            return str(valor)

    if isinstance(valor, str):
        return valor.strip()

    if hasattr(valor, "isoformat"):
        try:
            return valor.isoformat()
        except Exception:
            return str(valor)

    return valor










def _registro_dinamico_equivalente(
    registro_atual: dict[str, Any] | None,
    valores_novos: dict[str, Any] | None,
    campos_comparacao: list[str] | tuple[str, ...],
    *,
    nome_tabela: str | None = None,
) -> bool:
    """
    Compara um registro atual com um dicionário de novos valores,
    normalizando tipos para evitar falso positivo de diferença.

    Regras:
    - se vier campo inexistente na tabela informada, eu ignoro esse campo
    - Decimal, float, datas e strings passam pela mesma normalização
    - retorna True somente se todos os campos comparados forem equivalentes
    """
    if not isinstance(registro_atual, dict) or not isinstance(valores_novos, dict):
        return False

    for campo in campos_comparacao or []:
        if nome_tabela and not _coluna_existe(nome_tabela, campo):
            continue

        valor_atual = _normalizar_valor_snapshot_solicitacao(registro_atual.get(campo))
        valor_novo = _normalizar_valor_snapshot_solicitacao(valores_novos.get(campo))

        if valor_atual != valor_novo:
            return False

    return True








def _obter_snapshot_solicitacao_editavel_por_card(id_card: int) -> dict[str, Any] | None:
    if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO):
        return None

    ordem_header: list[str] = []
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "DataAtualizacao"):
        ordem_header.append("s.DataAtualizacao DESC")
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "DataCriacao"):
        ordem_header.append("s.DataCriacao DESC")
    ordem_header.append("s.IDFatoSolicitacaoContratoEuromidia DESC")

    sql_header = text(
        f"""
        SELECT TOP (1)
            s.*
        FROM {TABELA_SOLICITACAO_CONTRATO} s
        WHERE s.IDFatoKanbanCard = :id_card
        ORDER BY {', '.join(ordem_header)};
        """
    )
    header = db.session.execute(sql_header, {"id_card": int(id_card)}).mappings().first()
    if not header:
        return None

    resultado: dict[str, Any] = {
        "header": dict(header),
        "item": None,
        "itens": [],
    }

    if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO_ITEM):
        return resultado

    ordem_item: list[str] = []
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataAtualizacao"):
        ordem_item.append("i.DataAtualizacao DESC")
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataCriacao"):
        ordem_item.append("i.DataCriacao DESC")
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoSolicitacaoContratoItemEuromidia"):
        ordem_item.append("i.IDFatoSolicitacaoContratoItemEuromidia DESC")

    filtros = ["i.IDFatoKanbanCard = :id_card"]
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoSolicitacaoContratoEuromidia"):
        filtros.append("i.IDFatoSolicitacaoContratoEuromidia = :id_solicitacao")

    sql_itens = text(
        f"""
        SELECT
            i.*
        FROM {TABELA_SOLICITACAO_CONTRATO_ITEM} i
        WHERE {' AND '.join(filtros)}
        ORDER BY {', '.join(ordem_item) if ordem_item else 'i.IDFatoSolicitacaoContratoItemEuromidia DESC'};
        """
    )

    itens = db.session.execute(
        sql_itens,
        {
            "id_card": int(id_card),
            "id_solicitacao": int(header.get("IDFatoSolicitacaoContratoEuromidia") or 0),
        },
    ).mappings().all()

    lista_itens = [dict(item) for item in itens] if itens else []
    resultado["itens"] = lista_itens
    resultado["item"] = lista_itens[0] if lista_itens else None

    return resultado



def _obter_item_solicitacao_editavel_por_chave(
    *,
    id_card: int,
    id_solicitacao: int | None,
    id_contrato: int | None,
    id_item_contrato: int | None,
    cod_ponto: object = None,
    cod_face: object = None,
) -> dict[str, Any] | None:
    """
    Eu localizo o item já persistido da solicitação usando a chave lógica correta.

    Regra:
    - primeiro tento pela chave mais forte: IDFatoSolicitacaoContratoEuromidia + IDFatoControleContratosItensEuromidia
    - se não houver ID do item oficial do contrato, tento por:
      IDFatoSolicitacaoContratoEuromidia + IDFatoControleContratosEuromidia + CodPonto + CodFace
    - isso evita atualizar o último item do card por engano quando o card possui vários itens
    """
    if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO_ITEM):
        return None

    filtros: list[str] = ["i.IDFatoKanbanCard = :id_card"]
    parametros: dict[str, object] = {"id_card": int(id_card)}

    if id_solicitacao not in (None, "", 0) and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoSolicitacaoContratoEuromidia"):
        filtros.append("i.IDFatoSolicitacaoContratoEuromidia = :id_solicitacao")
        parametros["id_solicitacao"] = int(id_solicitacao)

    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "BitAtivo"):
        filtros.append("ISNULL(i.BitAtivo, 1) = 1")

    coluna_atividade_item = _obter_nome_coluna_atividade_solicitacao_item()
    if coluna_atividade_item:
        filtros.append(f"ISNULL(i.[{coluna_atividade_item}], 1) = 1")

    ordem_item: list[str] = []
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataAtualizacao"):
        ordem_item.append("i.DataAtualizacao DESC")
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataCriacao"):
        ordem_item.append("i.DataCriacao DESC")
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoSolicitacaoContratoItemEuromidia"):
        ordem_item.append("i.IDFatoSolicitacaoContratoItemEuromidia DESC")

    if (
        id_item_contrato not in (None, "", 0)
        and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoControleContratosItensEuromidia")
    ):
        filtros_item_oficial = list(filtros)
        parametros_item_oficial = dict(parametros)
        filtros_item_oficial.append("i.IDFatoControleContratosItensEuromidia = :id_item_contrato")
        parametros_item_oficial["id_item_contrato"] = int(id_item_contrato)

        sql_item_oficial = text(
            f"""
            SELECT TOP (1)
                i.*
            FROM {TABELA_SOLICITACAO_CONTRATO_ITEM} i
            WHERE {' AND '.join(filtros_item_oficial)}
            ORDER BY {', '.join(ordem_item) if ordem_item else 'i.IDFatoSolicitacaoContratoItemEuromidia DESC'};
            """
        )

        row_item_oficial = db.session.execute(
            sql_item_oficial,
            parametros_item_oficial,
        ).mappings().first()

        if row_item_oficial:
            return dict(row_item_oficial)

    cod_ponto_limpo = str(cod_ponto or "").strip() or None
    cod_face_limpo = str(cod_face or "").strip().upper() or None

    if not cod_ponto_limpo and not cod_face_limpo:
        return None

    filtros_chave_logica = list(filtros)
    parametros_chave_logica = dict(parametros)

    if id_contrato not in (None, "", 0) and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoControleContratosEuromidia"):
        filtros_chave_logica.append("ISNULL(i.IDFatoControleContratosEuromidia, 0) = :id_contrato")
        parametros_chave_logica["id_contrato"] = int(id_contrato)

    if cod_ponto_limpo and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "CodPonto"):
        filtros_chave_logica.append("LTRIM(RTRIM(ISNULL(i.CodPonto, ''))) = :cod_ponto")
        parametros_chave_logica["cod_ponto"] = cod_ponto_limpo

    if cod_face_limpo and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "CodFace"):
        filtros_chave_logica.append("UPPER(LTRIM(RTRIM(ISNULL(i.CodFace, '')))) = :cod_face")
        parametros_chave_logica["cod_face"] = cod_face_limpo

    sql_chave_logica = text(
        f"""
        SELECT TOP (1)
            i.*
        FROM {TABELA_SOLICITACAO_CONTRATO_ITEM} i
        WHERE {' AND '.join(filtros_chave_logica)}
        ORDER BY {', '.join(ordem_item) if ordem_item else 'i.IDFatoSolicitacaoContratoItemEuromidia DESC'};
        """
    )

    row_chave_logica = db.session.execute(
        sql_chave_logica,
        parametros_chave_logica,
    ).mappings().first()

    return dict(row_chave_logica) if row_chave_logica else None




























def _obter_ultima_solicitacao_contrato_por_card(id_card: int) -> dict[str, Any] | None:
    snapshot = _obter_snapshot_solicitacao_editavel_por_card(int(id_card))
    if not snapshot:
        return None

    header = snapshot.get("header") if isinstance(snapshot.get("header"), dict) else {}
    item = snapshot.get("item") if isinstance(snapshot.get("item"), dict) else {}

    resultado = {
        "IDFatoSolicitacaoContratoEuromidia": header.get("IDFatoSolicitacaoContratoEuromidia"),
        "IDFatoKanbanCard": header.get("IDFatoKanbanCard"),
        "IDFatoControleContratosEuromidia": (
            header.get("IDFatoControleContratosEuromidia")
            or item.get("IDFatoControleContratosEuromidia")
        ),
        "IDDimStatusContratos": header.get("IDDimStatusContratos"),
        "TipoSolicitacao": header.get("TipoSolicitacao") or item.get("TipoSolicitacao"),
        "IDEmpresa": header.get("IDEmpresa"),
        "CNPJ": header.get("CNPJ"),
        "RazaoSocial": header.get("RazaoSocial"),
        "NumeroContrato": header.get("NumeroContrato"),
        "NumeroPrevia": header.get("NumeroPrevia"),
        "Referencia": header.get("Referencia"),
        "IDFatoControleContratosItensEuromidia": item.get("IDFatoControleContratosItensEuromidia"),
        "SolicitacaoCodPonto": str(item.get("CodPonto") or "").strip() or None,
        "SolicitacaoCodFace": str(item.get("CodFace") or "").strip().upper() or None,
        "SolicitacaoPrecoVendaAtual": _decimal_para_float(item.get("TotalLiquidoContratoAGBRCTACORDO") or header.get("TotalLiquidoContratoAGBRCTACORDO")),
        "SolicitacaoDataInicioPrevisto": None,
        "SolicitacaoDataTerminoPrevisto": None,
        "SolicitacaoIDPainelEuromidia": item.get("IDPainelEuromidia"),
        "SolicitacaoIDDimFacesPaineis": item.get("IDDimFacesPaineis"),
        "SolicitacaoTotalLiquidoContratoAGBRCTACORDO": _decimal_para_float(header.get("TotalLiquidoContratoAGBRCTACORDO")),
    }

    for chave_origem, chave_destino in (("DataInicioPrevisto", "SolicitacaoDataInicioPrevisto"), ("DataTerminoPrevisto", "SolicitacaoDataTerminoPrevisto")):
        valor_data = item.get(chave_origem)
        if hasattr(valor_data, "date"):
            try:
                valor_data = valor_data.date()
            except Exception:
                pass
        if hasattr(valor_data, "isoformat"):
            resultado[chave_destino] = valor_data.isoformat()
        elif valor_data in (None, ""):
            resultado[chave_destino] = None
        else:
            resultado[chave_destino] = str(valor_data)

    return resultado



def _atualizar_card_nos_itens_contrato_euromidia(
    *,
    id_empresa: int | None,
    id_contrato: int | None,
    cod_ponto: object = None,
    cod_face: object = None,
    id_card: int,
) -> int:
    """
    Eu atualizo o ID do card nos itens ativos do contrato usando
    a chave lógica realmente necessária:

    contrato + CodPonto + CodFace

    Observação importante:
    - eu NÃO filtro por IDEmpresa aqui;
    - a regra de pertencimento do contrato à empresa já foi validada antes
      em _validar_contrato_empresa(...);
    - repetir esse filtro aqui só cria inconsistência, porque a validação
      aceita IDEmpresa OU CNPJ, enquanto o UPDATE antigo aceitava só IDEmpresa.
    """
    if not _objeto_existe(TABELA_CONTROLE_CONTRATOS_ITENS):
        return 0

    if not _coluna_existe(TABELA_CONTROLE_CONTRATOS_ITENS, "IDFatoKanbanCard"):
        return 0

    id_contrato_int = int(id_contrato) if id_contrato not in (None, "", 0) else None
    cod_ponto_txt = str(cod_ponto or "").strip()
    cod_face_txt = str(cod_face or "").strip().upper()

    if not id_contrato_int or not cod_ponto_txt or not cod_face_txt:
        return 0

    sets = ["i.IDFatoKanbanCard = :id_card"]
    if _coluna_existe(TABELA_CONTROLE_CONTRATOS_ITENS, "DataAtualizacao"):
        sets.append("i.DataAtualizacao = GETDATE()")

    parametros = {
        "id_card": int(id_card),
        "id_contrato": int(id_contrato_int),
        "cod_ponto": cod_ponto_txt,
        "cod_face": cod_face_txt,
    }

    sql = text(
        f"""
        UPDATE i
           SET {", ".join(sets)}
        FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
        WHERE
            i.IDFatoControleContratoEuromidia = :id_contrato
            AND ISNULL(i.BitAtivo, 1) = 1
            AND LTRIM(RTRIM(CAST(i.CodPonto AS varchar(50)))) = LTRIM(RTRIM(:cod_ponto))
            AND UPPER(LTRIM(RTRIM(CAST(i.CodFace AS varchar(50))))) = UPPER(LTRIM(RTRIM(:cod_face)));
        """
    )

    resultado = db.session.execute(sql, parametros)
    return int(resultado.rowcount or 0)






def _inativar_snapshots_solicitacao_contrato_do_card(id_card: int) -> None:
    if _objeto_existe(TABELA_SOLICITACAO_CONTRATO) and _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "BitAtivo"):
        sets = ["BitAtivo = 0"]
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "DataAtualizacao"):
            sets.append("DataAtualizacao = GETDATE()")

        db.session.execute(
            text(
                f"""
                UPDATE {TABELA_SOLICITACAO_CONTRATO}
                   SET {', '.join(sets)}
                 WHERE IDFatoKanbanCard = :id_card;
                """
            ),
            {"id_card": int(id_card)},
        )

    if _objeto_existe(TABELA_SOLICITACAO_CONTRATO_ITEM):
        sets_item: list[str] = []
        coluna_atividade_item = _obter_nome_coluna_atividade_solicitacao_item()
        if coluna_atividade_item:
            sets_item.append(f"[{coluna_atividade_item}] = 0")
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "BitAtivo"):
            sets_item.append("BitAtivo = 0")
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataAtualizacao"):
            sets_item.append("DataAtualizacao = GETDATE()")

        if sets_item:
            db.session.execute(
                text(
                    f"""
                    UPDATE {TABELA_SOLICITACAO_CONTRATO_ITEM}
                       SET {', '.join(sets_item)}
                     WHERE IDFatoKanbanCard = :id_card;
                    """
                ),
                {"id_card": int(id_card)},
            )













def _montar_itens_snapshot_solicitacao_do_card(
    *,
    id_card: int,
    id_solicitacao: int,
    id_usuario: int,
    id_empresa_proprietaria: int,
    id_contrato_existente: int | None,
    item_contrato: dict[str, Any] | None,
    contrato_row: dict[str, Any] | None,
    empresa: Mapping[str, Any] | dict[str, Any] | None,
    descricao_card: str | None,
    bit_registro_ativo: int,
    bit_solicitacao_ativa: int,
    coluna_atividade_item: str | None,
    tipo_norm: str,
    id_tipo_cliente: int | None = None,
    cod_ponto_contrato: object = None,
    cod_face_contrato: object = None,
    dados_item_formulario: dict[str, Any] | None = None,
    dados_itens_formulario: list[dict[str, Any]] | None = None,
    id_vendedor_formulario: int | None = None,
    nome_vendedor_formulario: str | None = None,
) -> list[dict[str, Any]]:
    def _int_positivo_ou_none_local(valor: object) -> int | None:
        if valor in (None, "", 0):
            return None
        try:
            valor_int = int(valor)
        except Exception:
            return None
        return valor_int if valor_int > 0 else None

    descricao_limpa = (descricao_card or "").strip() or None
    id_tipo_cliente_int = _int_positivo_ou_none_local(id_tipo_cliente)
    itens_resultado: list[dict[str, Any]] = []
    dados_item_formulario = dict(dados_item_formulario or {}) if isinstance(dados_item_formulario, dict) else {}
    dados_itens_formulario = [
        dict(item)
        for item in (dados_itens_formulario or [])
        if isinstance(item, dict)
    ]
    nome_vendedor_formulario = str(nome_vendedor_formulario or "").strip() or None
    id_vendedor_formulario = _int_positivo_ou_none_local(id_vendedor_formulario)

    campos_data_item_formulario = {
        "DataLancamento",
        "DataAssinaturaRenovacao",
        "DataInicioPrevisto",
        "DataTerminoPrevisto",
        "DataInicioVencimento",
        "DataCancelamento",
        "DataFimEfetiva",
    }
    campos_decimal_item_formulario = {
        "FaturamentoBrutoMensal",
        "PercentualPermuta",
        "CotaOportunidade",
        "ValorPermuta",
        "FaturamentoLiquidoPermuta",
        "TotalBrutoContrato",
        "TotalLiquidoContratoAGBRCTACORDO",
        "TotalLiquidoContratoAGBRVENDGERCOOR",
        "PercentualAgencia",
        "ValorMensalAgencia",
        "PercentualBureau",
        "ValorBureauMensal",
        "PercentualCartaAcordo",
        "ValorCartaAcordoMensal",
        "ValorOutrasComissoes",
        "FaturamentoLiquidoMensal",
        "PercentualComissaoVendedor",
        "ValorVendedor",
        "ValorVendedorTotal",
        "PercentualComissaoCoordenacao",
        "ValorCoordenador",
        "ValorCoordenadorTotal",
        "PercentualComissaoGerencia",
        "ValorGerencia",
        "ValorGerenciaTotal",
        "FaturamentoLiquidoFinalMensal",
        "ComissaoGerenciaNordeste",
        "Faturamento",
    }
    campos_int_item_formulario = {
        "Cota",
        "TexmpoExposicao",
        "NumeroParcelas",
        "IDPainelEuromidia",
        "IDDimFacesPaineis",
        "IDDimCheckingHistorico",
        "IDVendedor",
    }

    def _tem_dados_item_formulario(dados_item_atual: dict[str, Any] | None = None) -> bool:
        dados_base = dados_item_atual if isinstance(dados_item_atual, dict) else dados_item_formulario
        for chave, valor in dados_base.items():
            if chave in {"IDFatoSolicitacaoContratoEuromidia", "IDFatoControleContratosEuromidia", "IDFatoControleContratosItensEuromidia", "IDFatoKanbanCard"}:
                continue
            if valor not in (None, "", []):
                return True
        return False

    def _aplicar_dados_formulario_item(valores_item: dict[str, Any], dados_item_atual: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(valores_item, dict):
            valores_item = {}

        dados_base = dados_item_atual if isinstance(dados_item_atual, dict) else dados_item_formulario

        if id_vendedor_formulario not in (None, "", 0):
            valores_item["IDVendedor"] = id_vendedor_formulario
        if nome_vendedor_formulario:
            valores_item["Vendedor"] = nome_vendedor_formulario

        for chave, valor in dados_base.items():
            if chave not in valores_item:
                continue

            if chave in campos_data_item_formulario:
                valores_item[chave] = _para_data_sql_ou_none(valor)
            elif chave in campos_decimal_item_formulario:
                valores_item[chave] = _valor_decimal(valor)
            elif chave in campos_int_item_formulario:
                valores_item[chave] = _int_positivo_ou_none_local(valor)
            elif chave in {"CodPonto", "CodFace", "Status", "OBS", "Tipo", "Origem", "EmpresaEuro", "CnpjExibibora", "TipoDocumento", "RazaoSocial", "CPF", "MarcaExibida", "SDR", "Agencia", "CnpjAgencia", "Bureau", "CnpjBureau", "Intermediario", "CnpjIntermediario", "InicioRenovacao", "AtivoCancelamento", "CNPJ", "NumeroContrato", "NumeroPrevia", "Referencia", "CidadeExibicao"}:
                texto = str(valor or "").strip() or None
                if chave == "CodFace" and texto:
                    texto = texto.upper()
                valores_item[chave] = texto
            else:
                valores_item[chave] = valor

        return valores_item

    if tipo_norm == TIPO_SOLICITACAO_NOVO:
        paineis_card = _listar_paineis_vinculados_card(int(id_card))
        if not paineis_card and (_tem_dados_item_formulario() or any(_tem_dados_item_formulario(item) for item in dados_itens_formulario)):
            paineis_card = [{}]

        for indice_item, painel_card in enumerate(paineis_card):
            dados_item_atual = (
                dados_itens_formulario[indice_item]
                if indice_item < len(dados_itens_formulario)
                else dados_item_formulario
            )
            cod_ponto = str(painel_card.get("CodPonto") or "").strip() or None
            cod_face = str(painel_card.get("CodFace") or "").strip().upper() or None

            id_painel = _int_positivo_ou_none_local(painel_card.get("IDDimPaineisEuromidia"))
            id_face = _int_positivo_ou_none_local(painel_card.get("IDDimFacesPaineis"))

            info_face = None
            if id_face is not None:
                info_face = _obter_face_por_id(id_face)
            elif cod_ponto and cod_face:
                info_face = _obter_face_por_codponto_codface(
                    cod_ponto=cod_ponto,
                    cod_face=cod_face,
                )

            info_painel = None
            id_painel_resolvido = (
                id_painel
                or _int_positivo_ou_none_local((info_face or {}).get("IDDimPaineisEuromidia"))
            )

            if id_painel_resolvido is not None:
                sql_painel = text(
                    f"""
                    SELECT TOP (1)
                        p.IDDimPaineisEuromidia,
                        p.CodPonto,
                        p.Tipo,
                        p.Cidade,
                        p.UF,
                        p.Logradouro,
                        p.Bairro,
                        p.Numero,
                        p.CEP,
                        p.QuantidadeFaces,
                        p.BitAtivo
                    FROM {TABELA_DIM_PAINEIS_EUROMIDIA} p
                    WHERE p.IDDimPaineisEuromidia = :id_painel;
                    """
                )
                row_painel = db.session.execute(
                    sql_painel,
                    {"id_painel": int(id_painel_resolvido)},
                ).mappings().first()
                info_painel = dict(row_painel) if row_painel else None
            elif cod_ponto:
                info_painel = _obter_painel_por_codponto(cod_ponto)

            cod_ponto_resolvido = (
                cod_ponto
                or (info_face or {}).get("CodPonto")
                or (info_painel or {}).get("CodPonto")
            )
            cod_ponto_resolvido = str(cod_ponto_resolvido).strip() if cod_ponto_resolvido not in (None, "") else None

            cod_face_resolvido = (
                cod_face
                or (info_face or {}).get("CodFace")
            )
            cod_face_resolvido = str(cod_face_resolvido).strip().upper() if cod_face_resolvido not in (None, "") else None

            id_painel_final = (
                id_painel
                or _int_positivo_ou_none_local((info_painel or {}).get("IDDimPaineisEuromidia"))
                or _int_positivo_ou_none_local((info_face or {}).get("IDDimPaineisEuromidia"))
            )

            id_face_final = (
                id_face
                or _int_positivo_ou_none_local((info_face or {}).get("IDDimFacesPaineis"))
            )

            tipo_resolvido = (
                painel_card.get("TipoPainel")
                or (info_face or {}).get("Tipo")
                or (info_painel or {}).get("Tipo")
            )

            cidade_exibicao_resolvida = (
                (info_painel or {}).get("Cidade")
            )

            data_inicio = _para_data_sql_ou_none(painel_card.get("DataInicio"))
            data_fim = _para_data_sql_ou_none(painel_card.get("DataFim"))

            valores_item = {
                "IDFatoSolicitacaoContratoEuromidia": int(id_solicitacao),
                "IDFatoControleContratosEuromidia": _int_positivo_ou_none_local(id_contrato_existente),
                "IDFatoControleContratosItensEuromidia": None,
                "IDFatoKanbanCard": _int_positivo_ou_none_local(id_card),
                "IDDimUsuariosCriacao": _int_positivo_ou_none_local(id_usuario),
                "IDDimUsuariosAtualizacao": _int_positivo_ou_none_local(id_usuario),
                "IDVendedor": None,
                "IDPainelEuromidia": id_painel_final,
                "IDDimFacesPaineis": id_face_final,
                "IDDimCheckingHistorico": None,
                "IDEmpresaProprietaria": _int_positivo_ou_none_local(id_empresa_proprietaria),
                "Referencia": (contrato_row or {}).get("Referencia"),
                "NumeroContrato": (contrato_row or {}).get("NumeroContrato"),
                "NumeroPrevia": (contrato_row or {}).get("NumeroPrevia"),
                "CNPJ": (contrato_row or {}).get("CNPJ") or (empresa or {}).get("CNPJ"),
                "CodPonto": cod_ponto_resolvido,
                "CodFace": cod_face_resolvido,
                "DataLancamento": _para_data_sql_ou_none((contrato_row or {}).get("DataLancamento")),
                "Cota": None,
                "CidadeExibicao": cidade_exibicao_resolvida,
                "Tipo": tipo_resolvido,
                "Origem": (contrato_row or {}).get("Origem"),
                "EmpresaEuro": None,
                "CnpjExibibora": None,
                "TipoDocumento": (contrato_row or {}).get("TipoDocumento"),
                "RazaoSocial": (contrato_row or {}).get("RazaoSocial") or ((empresa or {}).get("RazaoSocial") if id_tipo_cliente_int == 2 else None),
                "CPF": (contrato_row or {}).get("CPF"),
                "MarcaExibida": (contrato_row or {}).get("MarcaExibida"),
                "Vendedor": (contrato_row or {}).get("Vendedor"),
                "SDR": (contrato_row or {}).get("SDR"),
                "Agencia": (contrato_row or {}).get("Agencia"),
                "CnpjAgencia": (contrato_row or {}).get("CnpjAgencia"),
                "Bureau": (contrato_row or {}).get("Bureau"),
                "CnpjBureau": (contrato_row or {}).get("CnpjBureau"),
                "Intermediario": (contrato_row or {}).get("Intermediario"),
                "CnpjIntermediario": (contrato_row or {}).get("CnpjIntermediario"),
                "DataAssinaturaRenovacao": _para_data_sql_ou_none((contrato_row or {}).get("DataAssinaturaRenovacao")),
                "IDTrimestre": (contrato_row or {}).get("IDTrimestre"),
                "TexmpoExposicao": None,
                "DataInicioPrevisto": data_inicio,
                "DataTerminoPrevisto": data_fim,
                "InicioRenovacao": None,
                "FaturamentoBrutoMensal": None,
                "PercentualPermuta": None,
                "CotaOportunidade": None,
                "ValorPermuta": None,
                "FaturamentoLiquidoPermuta": None,
                "NumeroParcelas": None,
                "DataInicioVencimento": None,
                "TotalBrutoContrato": None,
                "TotalLiquidoContratoAGBRCTACORDO": None,
                "TotalLiquidoContratoAGBRVENDGERCOOR": None,
                "PercentualAgencia": None,
                "ValorMensalAgencia": None,
                "PercentualBureau": None,
                "ValorBureauMensal": None,
                "PercentualCartaAcordo": None,
                "ValorCartaAcordoMensal": None,
                "ValorOutrasComissoes": None,
                "FaturamentoLiquidoMensal": None,
                "PercentualComissaoVendedor": None,
                "ValorVendedor": None,
                "ValorVendedorTotal": None,
                "PercentualComissaoCoordenacao": None,
                "ValorCoordenador": None,
                "ValorCoordenadorTotal": None,
                "PercentualComissaoGerencia": None,
                "ValorGerencia": None,
                "ValorGerenciaTotal": None,
                "AtivoCancelamento": None,
                "FaturamentoLiquidoFinalMensal": None,
                "ComissaoGerenciaNordeste": None,
                "Faturamento": None,
                "DataCancelamento": None,
                "OBS": descricao_limpa,
                "DataFimEfetiva": data_fim,
                "Status": None,
                "BitAtivo": bit_registro_ativo,
            }

            if coluna_atividade_item == "BitSolicitacaoAtiva":
                valores_item["BitSolicitacaoAtiva"] = bit_solicitacao_ativa

            valores_item = _aplicar_dados_formulario_item(valores_item, dados_item_atual)
            itens_resultado.append(valores_item)

        return itens_resultado

    cod_ponto_resolvido = (item_contrato or {}).get("CodPonto") or (
        str(cod_ponto_contrato).strip() if cod_ponto_contrato not in (None, "") else None
    )
    cod_face_resolvido = (item_contrato or {}).get("CodFace") or (
        str(cod_face_contrato).strip().upper() if cod_face_contrato not in (None, "") else None
    )

    valores_item = {
        "IDFatoSolicitacaoContratoEuromidia": int(id_solicitacao),
        "IDFatoControleContratosEuromidia": _int_positivo_ou_none_local(id_contrato_existente),
        "IDFatoControleContratosItensEuromidia": _int_positivo_ou_none_local(
            (item_contrato or {}).get("IDFatoControleContratosItensEuromidia")
        ),
        "IDFatoKanbanCard": _int_positivo_ou_none_local(id_card),
        "IDDimUsuariosCriacao": _int_positivo_ou_none_local(id_usuario),
        "IDDimUsuariosAtualizacao": _int_positivo_ou_none_local(id_usuario),
        "IDVendedor": _int_positivo_ou_none_local((item_contrato or {}).get("IDVendedor")),
        "IDPainelEuromidia": _int_positivo_ou_none_local((item_contrato or {}).get("IDPainelEuromidia")),
        "IDDimFacesPaineis": _int_positivo_ou_none_local((item_contrato or {}).get("IDDimFacesPaineis")),
        "IDDimCheckingHistorico": _int_positivo_ou_none_local((item_contrato or {}).get("IDDimCheckingHistorico")),
        "IDEmpresaProprietaria": _int_positivo_ou_none_local(id_empresa_proprietaria),
        "Referencia": (item_contrato or {}).get("Referencia") or (contrato_row or {}).get("Referencia"),
        "NumeroContrato": (item_contrato or {}).get("NumeroContrato") or (contrato_row or {}).get("NumeroContrato"),
        "NumeroPrevia": (item_contrato or {}).get("NumeroPrevia") or (contrato_row or {}).get("NumeroPrevia"),
        "CNPJ": (item_contrato or {}).get("CNPJ") or (contrato_row or {}).get("CNPJ") or (empresa or {}).get("CNPJ"),
        "CodPonto": cod_ponto_resolvido,
        "CodFace": cod_face_resolvido,
        "DataLancamento": _para_data_sql_ou_none((item_contrato or {}).get("DataLancamento") or (contrato_row or {}).get("DataLancamento")),
        "Cota": (item_contrato or {}).get("Cota"),
        "CidadeExibicao": (item_contrato or {}).get("CidadeExibicao"),
        "Tipo": (item_contrato or {}).get("Tipo"),
        "Origem": (item_contrato or {}).get("Origem") or (contrato_row or {}).get("Origem"),
        "EmpresaEuro": (item_contrato or {}).get("EmpresaEuro"),
        "CnpjExibibora": (item_contrato or {}).get("CnpjExibibora"),
        "TipoDocumento": (item_contrato or {}).get("TipoDocumento") or (contrato_row or {}).get("TipoDocumento"),
        "RazaoSocial": (item_contrato or {}).get("RazaoSocial") or (contrato_row or {}).get("RazaoSocial") or (empresa or {}).get("RazaoSocial"),
        "CPF": (item_contrato or {}).get("CPF") or (contrato_row or {}).get("CPF"),
        "MarcaExibida": (item_contrato or {}).get("MarcaExibida") or (contrato_row or {}).get("MarcaExibida"),
        "Vendedor": (item_contrato or {}).get("Vendedor") or (contrato_row or {}).get("Vendedor"),
        "SDR": (item_contrato or {}).get("SDR") or (contrato_row or {}).get("SDR"),
        "Agencia": (item_contrato or {}).get("Agencia") or (contrato_row or {}).get("Agencia"),
        "CnpjAgencia": (item_contrato or {}).get("CnpjAgencia") or (contrato_row or {}).get("CnpjAgencia"),
        "Bureau": (item_contrato or {}).get("Bureau") or (contrato_row or {}).get("Bureau"),
        "CnpjBureau": (item_contrato or {}).get("CnpjBureau") or (contrato_row or {}).get("CnpjBureau"),
        "Intermediario": (item_contrato or {}).get("Intermediario") or (contrato_row or {}).get("Intermediario"),
        "CnpjIntermediario": (item_contrato or {}).get("CnpjIntermediario") or (contrato_row or {}).get("CnpjIntermediario"),
        "DataAssinaturaRenovacao": _para_data_sql_ou_none((item_contrato or {}).get("DataAssinaturaRenovacao")),
        "IDTrimestre": (item_contrato or {}).get("IDTrimestre"),
        "TexmpoExposicao": (item_contrato or {}).get("TexmpoExposicao"),
        "DataInicioPrevisto": None,
        "DataTerminoPrevisto": None,
        "InicioRenovacao": _para_data_sql_ou_none((item_contrato or {}).get("InicioRenovacao")),
        "FaturamentoBrutoMensal": (item_contrato or {}).get("FaturamentoBrutoMensal"),
        "PercentualPermuta": (item_contrato or {}).get("PercentualPermuta"),
        "CotaOportunidade": (item_contrato or {}).get("CotaOportunidade"),
        "ValorPermuta": (item_contrato or {}).get("ValorPermuta"),
        "FaturamentoLiquidoPermuta": (item_contrato or {}).get("FaturamentoLiquidoPermuta"),
        "NumeroParcelas": (item_contrato or {}).get("NumeroParcelas"),
        "DataInicioVencimento": _para_data_sql_ou_none((item_contrato or {}).get("DataInicioVencimento")),
        "TotalBrutoContrato": (item_contrato or {}).get("TotalBrutoContrato"),
        "TotalLiquidoContratoAGBRCTACORDO": (item_contrato or {}).get("TotalLiquidoContratoAGBRCTACORDO"),
        "TotalLiquidoContratoAGBRVENDGERCOOR": (item_contrato or {}).get("TotalLiquidoContratoAGBRVENDGERCOOR"),
        "PercentualAgencia": (item_contrato or {}).get("PercentualAgencia"),
        "ValorMensalAgencia": (item_contrato or {}).get("ValorMensalAgencia"),
        "PercentualBureau": (item_contrato or {}).get("PercentualBureau"),
        "ValorBureauMensal": (item_contrato or {}).get("ValorBureauMensal"),
        "PercentualCartaAcordo": (item_contrato or {}).get("PercentualCartaAcordo"),
        "ValorCartaAcordoMensal": (item_contrato or {}).get("ValorCartaAcordoMensal"),
        "ValorOutrasComissoes": (item_contrato or {}).get("ValorOutrasComissoes"),
        "FaturamentoLiquidoMensal": (item_contrato or {}).get("FaturamentoLiquidoMensal"),
        "PercentualComissaoVendedor": (item_contrato or {}).get("PercentualComissaoVendedor"),
        "ValorVendedor": (item_contrato or {}).get("ValorVendedor"),
        "ValorVendedorTotal": (item_contrato or {}).get("ValorVendedorTotal"),
        "PercentualComissaoCoordenacao": (item_contrato or {}).get("PercentualComissaoCoordenacao"),
        "ValorCoordenador": (item_contrato or {}).get("ValorCoordenador"),
        "ValorCoordenadorTotal": (item_contrato or {}).get("ValorCoordenadorTotal"),
        "PercentualComissaoGerencia": (item_contrato or {}).get("PercentualComissaoGerencia"),
        "ValorGerencia": (item_contrato or {}).get("ValorGerencia"),
        "ValorGerenciaTotal": (item_contrato or {}).get("ValorGerenciaTotal"),
        "AtivoCancelamento": (item_contrato or {}).get("AtivoCancelamento"),
        "FaturamentoLiquidoFinalMensal": (item_contrato or {}).get("FaturamentoLiquidoFinalMensal"),
        "ComissaoGerenciaNordeste": (item_contrato or {}).get("ComissaoGerenciaNordeste"),
        "Faturamento": (item_contrato or {}).get("Faturamento"),
        "DataCancelamento": _para_data_sql_ou_none((item_contrato or {}).get("DataCancelamento")),
        "OBS": descricao_limpa,
        "DataFimEfetiva": None,
        "Status": str((item_contrato or {}).get("Status") or "").strip() or None,
        "BitAtivo": bit_registro_ativo,
    }

    if coluna_atividade_item == "BitSolicitacaoAtiva":
        valores_item["BitSolicitacaoAtiva"] = bit_solicitacao_ativa

    dados_item_atual = dados_itens_formulario[0] if dados_itens_formulario else dados_item_formulario
    valores_item = _aplicar_dados_formulario_item(valores_item, dados_item_atual)
    itens_resultado.append(valores_item)
    return itens_resultado







def _sincronizar_snapshot_solicitacao_contrato_do_card(
    *,
    id_card: int,
    id_usuario: int,
    id_empresa_proprietaria: int,
    id_empresa_relacionada: int | None,
    tipo_contrato: str | None,
    id_contrato_existente: int | None,
    cod_ponto_contrato: object = None,
    cod_face_contrato: object = None,
    descricao_card: str | None = None,
    contrato_existente: dict[str, Any] | None = None,
    forcar_solicitacao_ativa: bool | None = None,
    id_tipo_cliente: int | None = None,
    dados_formulario_solicitacao: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tipo_norm = _normalizar_tipo_contrato_card(tipo_contrato)
    if tipo_norm not in {TIPO_SOLICITACAO_ADITIVO, TIPO_SOLICITACAO_NOVO}:
        return {"sincronizado": False, "motivo": "tipo_contrato_ausente"}

    if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO):
        return {"sincronizado": False, "motivo": "tabela_solicitacao_ausente"}

    def _int_positivo_ou_none(valor: object) -> int | None:
        if valor in (None, "", 0):
            return None
        try:
            valor_int = int(valor)
        except Exception:
            return None
        return valor_int if valor_int > 0 else None

    def _resolver_solicitacao_ativa() -> int:
        if forcar_solicitacao_ativa is not None:
            return 1 if bool(forcar_solicitacao_ativa) else 0

        detalhe_card = _obter_card_detalhe_payload(int(id_card))
        card_atual = detalhe_card.get("card") if isinstance(detalhe_card.get("card"), dict) else {}
        tags_ativas = detalhe_card.get("tags") if isinstance(detalhe_card.get("tags"), list) else []

        id_fase_atual = int(card_atual.get("IDDimKanbanFaseAtual") or 0)
        tem_tag_contrato_em_avaliacao = any(
            int(item.get("IDDimKanbanTag") or 0) == int(ID_TAG_CONTRATO_EM_AVALIACAO)
            for item in tags_ativas
            if isinstance(item, dict)
        )
        return 1 if (id_fase_atual == 4 or tem_tag_contrato_em_avaliacao) else 0

    def _montar_ordem_item_snapshot(alias: str = "i") -> str:
        ordem: list[str] = []
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataAtualizacao"):
            ordem.append(f"{alias}.DataAtualizacao DESC")
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataCriacao"):
            ordem.append(f"{alias}.DataCriacao DESC")
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoSolicitacaoContratoItemEuromidia"):
            ordem.append(f"{alias}.IDFatoSolicitacaoContratoItemEuromidia DESC")
        return ", ".join(ordem) if ordem else f"{alias}.IDFatoSolicitacaoContratoItemEuromidia DESC"

    def _buscar_item_snapshot_existente_para_upsert(
        *,
        id_solicitacao: int,
        id_contrato: int | None,
        id_item_contrato: int | None,
        cod_ponto: object = None,
        cod_face: object = None,
    ) -> dict[str, Any] | None:
        if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO_ITEM):
            return None

        ordem_sql = _montar_ordem_item_snapshot("i")
        filtros_base = ["i.IDFatoSolicitacaoContratoEuromidia = :id_solicitacao"]
        parametros_base: dict[str, object] = {"id_solicitacao": int(id_solicitacao)}

        if (
            id_item_contrato not in (None, "", 0)
            and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoControleContratosItensEuromidia")
        ):
            filtros_item = list(filtros_base)
            parametros_item = dict(parametros_base)
            filtros_item.append("ISNULL(i.IDFatoControleContratosItensEuromidia, 0) = :id_item_contrato")
            parametros_item["id_item_contrato"] = int(id_item_contrato)

            row_item = db.session.execute(
                text(
                    f"""
                    SELECT TOP (1)
                        i.*
                    FROM {TABELA_SOLICITACAO_CONTRATO_ITEM} i
                    WHERE {' AND '.join(filtros_item)}
                    ORDER BY {ordem_sql};
                    """
                ),
                parametros_item,
            ).mappings().first()

            if row_item:
                return dict(row_item)

        cod_ponto_limpo = str(cod_ponto or "").strip() or None
        cod_face_limpo = str(cod_face or "").strip().upper() or None

        if not cod_ponto_limpo and not cod_face_limpo:
            return None

        filtros_logicos = list(filtros_base)
        parametros_logicos = dict(parametros_base)

        if id_contrato not in (None, "", 0) and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoControleContratosEuromidia"):
            filtros_logicos.append("ISNULL(i.IDFatoControleContratosEuromidia, 0) = :id_contrato")
            parametros_logicos["id_contrato"] = int(id_contrato)

        if cod_ponto_limpo and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "CodPonto"):
            filtros_logicos.append("LTRIM(RTRIM(ISNULL(CAST(i.CodPonto AS varchar(50)), ''))) = :cod_ponto")
            parametros_logicos["cod_ponto"] = cod_ponto_limpo

        if cod_face_limpo and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "CodFace"):
            filtros_logicos.append("UPPER(LTRIM(RTRIM(ISNULL(CAST(i.CodFace AS varchar(50)), '')))) = :cod_face")
            parametros_logicos["cod_face"] = cod_face_limpo

        row_logico = db.session.execute(
            text(
                f"""
                SELECT TOP (1)
                    i.*
                FROM {TABELA_SOLICITACAO_CONTRATO_ITEM} i
                WHERE {' AND '.join(filtros_logicos)}
                ORDER BY {ordem_sql};
                """
            ),
            parametros_logicos,
        ).mappings().first()

        return dict(row_logico) if row_logico else None

    def _inativar_duplicados_do_item_snapshot(
        *,
        id_solicitacao: int,
        id_item_manter: int,
        id_contrato: int | None,
        id_item_contrato: int | None,
        cod_ponto: object = None,
        cod_face: object = None,
        coluna_atividade_item: str | None = None,
    ) -> None:
        if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO_ITEM):
            return

        filtros = [
            "IDFatoSolicitacaoContratoEuromidia = :id_solicitacao",
            "IDFatoSolicitacaoContratoItemEuromidia <> :id_item_manter",
        ]
        parametros: dict[str, object] = {
            "id_solicitacao": int(id_solicitacao),
            "id_item_manter": int(id_item_manter),
        }

        if (
            id_item_contrato not in (None, "", 0)
            and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoControleContratosItensEuromidia")
        ):
            filtros.append("ISNULL(IDFatoControleContratosItensEuromidia, 0) = :id_item_contrato")
            parametros["id_item_contrato"] = int(id_item_contrato)
        else:
            cod_ponto_limpo = str(cod_ponto or "").strip() or None
            cod_face_limpo = str(cod_face or "").strip().upper() or None

            if id_contrato not in (None, "", 0) and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "IDFatoControleContratosEuromidia"):
                filtros.append("ISNULL(IDFatoControleContratosEuromidia, 0) = :id_contrato")
                parametros["id_contrato"] = int(id_contrato)

            if cod_ponto_limpo and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "CodPonto"):
                filtros.append("LTRIM(RTRIM(ISNULL(CAST(CodPonto AS varchar(50)), ''))) = :cod_ponto")
                parametros["cod_ponto"] = cod_ponto_limpo

            if cod_face_limpo and _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "CodFace"):
                filtros.append("UPPER(LTRIM(RTRIM(ISNULL(CAST(CodFace AS varchar(50)), '')))) = :cod_face")
                parametros["cod_face"] = cod_face_limpo

        sets: list[str] = []
        if coluna_atividade_item:
            sets.append(f"[{coluna_atividade_item}] = 0")
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "BitAtivo"):
            sets.append("BitAtivo = 0")
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataAtualizacao"):
            sets.append("DataAtualizacao = GETDATE()")

        if not sets:
            return

        db.session.execute(
            text(
                f"""
                UPDATE {TABELA_SOLICITACAO_CONTRATO_ITEM}
                   SET {', '.join(sets)}
                 WHERE {' AND '.join(filtros)};
                """
            ),
            parametros,
        )

    def _buscar_empresa_por_id(id_empresa: int | None) -> dict[str, Any] | None:
        if id_empresa in (None, "", 0):
            return None

        row = db.session.execute(
            text(
                f"""
                SELECT TOP (1)
                    e.IDEmpresa,
                    e.CNPJ,
                    e.RazaoSocial
                FROM {TABELA_EMPRESAS} e
                WHERE e.IDEmpresa = :id_empresa;
                """
            ),
            {"id_empresa": int(id_empresa)},
        ).mappings().first()

        return dict(row) if row else None

    def _parse_nome_tabela_sql(nome_tabela_sql: str) -> tuple[str | None, str | None]:
        texto_tabela = str(nome_tabela_sql or "").strip()
        if not texto_tabela:
            return None, None

        partes = [parte.strip("[] ") for parte in texto_tabela.split(".") if parte.strip("[] ")]
        if len(partes) >= 2:
            return partes[-2], partes[-1]
        if len(partes) == 1:
            return "dbo", partes[0]
        return None, None

    cache_tamanho_colunas_texto: dict[tuple[str, str], int | None] = {}

    def _obter_tamanho_maximo_coluna_texto(nome_tabela_sql: str, nome_coluna: str) -> int | None:
        chave_cache = (str(nome_tabela_sql or ""), str(nome_coluna or ""))
        if chave_cache in cache_tamanho_colunas_texto:
            return cache_tamanho_colunas_texto[chave_cache]

        schema_nome, tabela_nome = _parse_nome_tabela_sql(nome_tabela_sql)
        if not schema_nome or not tabela_nome or not nome_coluna:
            cache_tamanho_colunas_texto[chave_cache] = None
            return None

        try:
            row = db.session.execute(
                text(
                    """
                    SELECT TOP (1)
                        c.max_length AS max_length,
                        tp.name AS tipo_sql
                    FROM sys.columns c
                    INNER JOIN sys.tables t
                        ON t.object_id = c.object_id
                    INNER JOIN sys.schemas s
                        ON s.schema_id = t.schema_id
                    INNER JOIN sys.types tp
                        ON tp.user_type_id = c.user_type_id
                    WHERE s.name = :schema_nome
                      AND t.name = :tabela_nome
                      AND c.name = :nome_coluna;
                    """
                ),
                {
                    "schema_nome": schema_nome,
                    "tabela_nome": tabela_nome,
                    "nome_coluna": str(nome_coluna),
                },
            ).mappings().first()

            if not row:
                cache_tamanho_colunas_texto[chave_cache] = None
                return None

            max_length = row.get("max_length")
            tipo_sql = str(row.get("tipo_sql") or "").lower()

            if max_length in (None, -1):
                cache_tamanho_colunas_texto[chave_cache] = None
                return None

            try:
                max_length_int = int(max_length)
            except Exception:
                cache_tamanho_colunas_texto[chave_cache] = None
                return None

            if tipo_sql in {"nvarchar", "nchar"}:
                tamanho_final = max_length_int // 2
            else:
                tamanho_final = max_length_int

            cache_tamanho_colunas_texto[chave_cache] = tamanho_final if tamanho_final > 0 else None
            return cache_tamanho_colunas_texto[chave_cache]
        except Exception:
            cache_tamanho_colunas_texto[chave_cache] = None
            return None

    def _texto_formulario_ou_none(
        valor: Any,
        *,
        nome_coluna: str | None = None,
        nome_tabela_sql: str | None = None,
    ) -> str | None:
        if valor is None:
            return None

        texto = str(valor).strip()
        if not texto:
            return None

        texto_normalizado = _normalizar_texto_comparacao(texto)

        placeholders_invalidos = {
            "selecione",
            "selecione...",
            "escolha",
            "escolha...",
            "todos",
            "todas",
            "nenhum",
            "nenhuma",
            "n/a",
            "na",
            "null",
            "none",
            "nao informado",
            "não informado",
            "-",
            "--",
            "---",
        }

        if texto_normalizado in placeholders_invalidos:
            return None

        if "selecione" in texto_normalizado:
            return None

        if nome_coluna and nome_tabela_sql:
            tamanho_maximo = _obter_tamanho_maximo_coluna_texto(nome_tabela_sql, nome_coluna)
            if tamanho_maximo not in (None, 0) and len(texto) > int(tamanho_maximo):
                texto = texto[: int(tamanho_maximo)]

        return texto or None

    dados_formulario_solicitacao = dict(dados_formulario_solicitacao or {}) if isinstance(dados_formulario_solicitacao, dict) else {}
    dados_header_formulario = (
        dict(dados_formulario_solicitacao.get("header") or {})
        if isinstance(dados_formulario_solicitacao.get("header"), dict)
        else {}
    )
    dados_item_formulario = (
        dict(dados_formulario_solicitacao.get("item") or {})
        if isinstance(dados_formulario_solicitacao.get("item"), dict)
        else {}
    )
    dados_itens_formulario = [
        dict(item)
        for item in (dados_formulario_solicitacao.get("itens") or [])
        if isinstance(item, dict)
    ]

    campos_data_header_formulario = {"DataAssinaturaRenovacao", "DataLancamento"}
    campos_decimal_header_formulario = {
        "TotalFaturamentoBrutoMensal", "TotalPercentualPermuta", "TotalCotaOportunidade",
        "TotalValorPermuta", "TotalFaturamentoLiquidoPermuta", "TotalBrutoContrato",
        "TotalLiquidoContratoAGBRCTACORDO", "TotalLiquidoContratoAGBRVENDGERCOOR",
        "TotalPercentualAgencia", "TotalValorMensalAgencia", "TotalPercentualBureau",
        "TotalValorBureauMensal", "TotalPercentualCartaAcordo", "TotalValorCartaAcordoMensal",
        "TotalValorOutrasComissoes", "TotalFaturamentoLiquidoMensal", "TotalPercentualComissaoVendedor",
        "TotalValorVendedor", "ValorVendedorTotal", "TotalPercentualComissaoCoordenacao",
    }
    campos_int_header_formulario = {
        "IDFatoControleContratosEuromidia", "IDDimStatusContratos", "IDDimUsuariosCriacao",
        "IDEmpresa", "IDEmpresaAgencia", "IDEmpresaBureau", "IDEmpresaProprietaria",
        "IDTrimestre", "QuantidadePontos", "QuantidadeFaces", "BitAtivo"
    }

    def _aplicar_dados_formulario_header(valores_solicitacao: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(valores_solicitacao, dict):
            valores_solicitacao = {}

        for chave, valor in dados_header_formulario.items():
            if chave not in valores_solicitacao:
                continue

            if chave in campos_data_header_formulario:
                valores_solicitacao[chave] = _para_data_sql_ou_none(valor)
            elif chave in campos_decimal_header_formulario:
                valores_solicitacao[chave] = _valor_decimal(valor)
            elif chave in campos_int_header_formulario:
                valores_solicitacao[chave] = _int_positivo_ou_none(valor)
            else:
                valores_solicitacao[chave] = _texto_formulario_ou_none(
                    valor,
                    nome_coluna=chave,
                    nome_tabela_sql=TABELA_SOLICITACAO_CONTRATO,
                )

        return valores_solicitacao

    vendedor_formulario = _obter_vendedor_logado_reserva_kanban(int(id_empresa_proprietaria)) or {}
    id_vendedor_formulario = int(vendedor_formulario.get("IDVendedor") or 0) or None
    nome_vendedor_formulario = str(vendedor_formulario.get("NomeVendedor") or "").strip() or None

    detalhe_card_snapshot = _obter_card_detalhe_payload(int(id_card))
    card_snapshot = detalhe_card_snapshot.get("card") if isinstance(detalhe_card_snapshot, dict) else {}
    card_snapshot = card_snapshot if isinstance(card_snapshot, dict) else {}

    if id_tipo_cliente in (None, "", 0):
        try:
            id_tipo_cliente = int(
                card_snapshot.get("IDDimTipoCliente")
                or card_snapshot.get("IDDimKanbanTipoClienteDesconto")
                or 0
            ) or None
        except Exception:
            id_tipo_cliente = None

    id_empresa_principal_snapshot = (
        _int_positivo_ou_none(card_snapshot.get("IDEmpresa"))
        or _int_positivo_ou_none(id_empresa_relacionada)
    )
    id_empresa_agencia_snapshot = _int_positivo_ou_none(card_snapshot.get("IDEmpresaAgencia"))
    id_empresa_bureau_snapshot = _int_positivo_ou_none(card_snapshot.get("IDEmpresaBureau"))

    empresa_principal_snapshot = _buscar_empresa_por_id(id_empresa_principal_snapshot)
    empresa_agencia_snapshot = _buscar_empresa_por_id(id_empresa_agencia_snapshot)
    empresa_bureau_snapshot = _buscar_empresa_por_id(id_empresa_bureau_snapshot)

    contrato_row = dict(contrato_existente) if isinstance(contrato_existente, dict) else None
    if contrato_row is None and id_contrato_existente not in (None, "", 0):
        sql_contrato = text(
            f"""
            SELECT TOP (1)
                *
            FROM {TABELA_CONTROLE_CONTRATOS} c
            WHERE c.IDFatoControleContratosEuromidia = :id_contrato;
            """
        )
        row_contrato = db.session.execute(
            sql_contrato,
            {"id_contrato": int(id_contrato_existente)},
        ).mappings().first()
        contrato_row = dict(row_contrato) if row_contrato else None

    item_contrato = _obter_item_contrato_euromidia(
        id_contrato=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        cod_ponto=cod_ponto_contrato,
        cod_face=cod_face_contrato,
    )

    id_item_contrato = (
        item_contrato.get("IDFatoControleContratosItensEuromidia")
        if isinstance(item_contrato, dict)
        else None
    )

    _registrar_log_contrato_card_euromidia(
        id_contrato=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        id_item_contrato=int(id_item_contrato) if id_item_contrato not in (None, "", 0) else None,
        id_card=int(id_card),
        evento="SNAPSHOT_SOLICITACAO_SYNC",
        payload={
            "tipo_contrato": tipo_norm,
            "id_empresa_relacionada": id_empresa_relacionada,
            "id_empresa_principal_snapshot": id_empresa_principal_snapshot,
            "id_empresa_agencia_snapshot": id_empresa_agencia_snapshot,
            "id_empresa_bureau_snapshot": id_empresa_bureau_snapshot,
            "id_contrato_existente": id_contrato_existente,
            "cod_ponto_contrato": cod_ponto_contrato,
            "cod_face_contrato": cod_face_contrato,
        },
    )

    bit_solicitacao_ativa = _resolver_solicitacao_ativa()
    bit_registro_ativo = 1

    solicitacao_existente = _obter_solicitacao_contrato_ativa_por_card(int(id_card))
    if solicitacao_existente:
        id_solicitacao_existente = int(solicitacao_existente.get("IDFatoSolicitacaoContratoEuromidia") or 0)
    else:
        ultima_solicitacao = _obter_ultima_solicitacao_contrato_por_card(int(id_card))
        id_solicitacao_existente = int(ultima_solicitacao.get("IDFatoSolicitacaoContratoEuromidia") or 0) if ultima_solicitacao else 0

    valores_solicitacao = {
        "IDFatoKanbanCard": int(id_card),
        "IDFatoControleContratosEuromidia": _int_positivo_ou_none(id_contrato_existente),
        "IDDimStatusContratos": _int_positivo_ou_none(_obter_id_status_contrato_em_avaliacao()),
        "IDDimUsuariosCriacao": _int_positivo_ou_none(id_usuario),
        "IDEmpresa": id_empresa_principal_snapshot,
        "IDEmpresaAgencia": id_empresa_agencia_snapshot,
        "IDEmpresaBureau": id_empresa_bureau_snapshot,
        "IDEmpresaProprietaria": _int_positivo_ou_none(id_empresa_proprietaria),
        "TipoSolicitacao": tipo_norm,
        "Referencia": (contrato_row or {}).get("Referencia"),
        "NumeroContrato": (contrato_row or {}).get("NumeroContrato"),
        "NumeroPrevia": (contrato_row or {}).get("NumeroPrevia"),
        "CNPJ": (contrato_row or {}).get("CNPJ") or (empresa_principal_snapshot or {}).get("CNPJ"),
        "DataAssinaturaRenovacao": _para_data_sql_ou_none((contrato_row or {}).get("DataAssinaturaRenovacao")),
        "IDTrimestre": (contrato_row or {}).get("IDTrimestre"),
        "DataLancamento": _para_data_sql_ou_none((contrato_row or {}).get("DataLancamento")),
        "RazaoSocial": (contrato_row or {}).get("RazaoSocial") or (empresa_principal_snapshot or {}).get("RazaoSocial"),
        "CPF": (contrato_row or {}).get("CPF"),
        "MarcaExibida": (contrato_row or {}).get("MarcaExibida"),
        "Vendedor": (contrato_row or {}).get("Vendedor"),
        "TipoDocumento": (contrato_row or {}).get("TipoDocumento"),
        "Origem": (contrato_row or {}).get("Origem"),
        "SDR": (contrato_row or {}).get("SDR"),
        "Agencia": (contrato_row or {}).get("Agencia") or (empresa_agencia_snapshot or {}).get("RazaoSocial"),
        "CnpjAgencia": (contrato_row or {}).get("CnpjAgencia") or (empresa_agencia_snapshot or {}).get("CNPJ"),
        "Bureau": (contrato_row or {}).get("Bureau") or (empresa_bureau_snapshot or {}).get("RazaoSocial"),
        "CnpjBureau": (contrato_row or {}).get("CnpjBureau") or (empresa_bureau_snapshot or {}).get("CNPJ"),
        "Intermediario": (contrato_row or {}).get("Intermediario"),
        "CnpjIntermediario": (contrato_row or {}).get("CnpjIntermediario"),
        "QuantidadePontos": (contrato_row or {}).get("QuantidadePontos"),
        "QuantidadeFaces": (contrato_row or {}).get("QuantidadeFaces"),
        "TotalFaturamentoBrutoMensal": (contrato_row or {}).get("TotalFaturamentoBrutoMensal"),
        "TotalPercentualPermuta": (contrato_row or {}).get("TotalPercentualPermuta"),
        "TotalCotaOportunidade": (contrato_row or {}).get("TotalCotaOportunidade"),
        "TotalValorPermuta": (contrato_row or {}).get("TotalValorPermuta"),
        "TotalFaturamentoLiquidoPermuta": (contrato_row or {}).get("TotalFaturamentoLiquidoPermuta"),
        "TotalBrutoContrato": (contrato_row or {}).get("TotalBrutoContrato"),
        "TotalLiquidoContratoAGBRCTACORDO": (contrato_row or {}).get("TotalLiquidoContratoAGBRCTACORDO"),
        "TotalLiquidoContratoAGBRVENDGERCOOR": (contrato_row or {}).get("TotalLiquidoContratoAGBRVENDGERCOOR"),
        "TotalPercentualAgencia": (contrato_row or {}).get("TotalPercentualAgencia"),
        "TotalValorMensalAgencia": (contrato_row or {}).get("TotalValorMensalAgencia"),
        "TotalPercentualBureau": (contrato_row or {}).get("TotalPercentualBureau"),
        "TotalValorBureauMensal": (contrato_row or {}).get("TotalValorBureauMensal"),
        "TotalPercentualCartaAcordo": (contrato_row or {}).get("TotalPercentualCartaAcordo"),
        "TotalValorCartaAcordoMensal": (contrato_row or {}).get("TotalValorCartaAcordoMensal"),
        "TotalValorOutrasComissoes": (contrato_row or {}).get("TotalValorOutrasComissoes"),
        "TotalFaturamentoLiquidoMensal": (contrato_row or {}).get("TotalFaturamentoLiquidoMensal"),
        "TotalPercentualComissaoVendedor": (contrato_row or {}).get("TotalPercentualComissaoVendedor"),
        "TotalValorVendedor": (contrato_row or {}).get("TotalValorVendedor"),
        "ValorVendedorTotal": (contrato_row or {}).get("ValorVendedorTotal"),
        "TotalPercentualComissaoCoordenacao": (contrato_row or {}).get("TotalPercentualComissaoCoordenacao"),
        "Observacao": "Solicitação enviada para avaliação a partir do card.",
        "BitAtivo": bit_registro_ativo,
    }

    if nome_vendedor_formulario and not str(valores_solicitacao.get("Vendedor") or "").strip():
        valores_solicitacao["Vendedor"] = nome_vendedor_formulario

    valores_solicitacao = _aplicar_dados_formulario_header(valores_solicitacao)

    snapshot_existente_payload = _obter_snapshot_solicitacao_editavel_por_card(int(id_card))
    header_existente = (
        snapshot_existente_payload.get("header")
        if isinstance(snapshot_existente_payload, dict)
        else None
    )

    campos_header_comparacao = [
        "IDFatoKanbanCard", "IDFatoControleContratosEuromidia", "IDDimStatusContratos",
        "IDEmpresa", "IDEmpresaAgencia", "IDEmpresaBureau", "IDEmpresaProprietaria", "TipoSolicitacao", "Referencia",
        "NumeroContrato", "NumeroPrevia", "CNPJ", "DataAssinaturaRenovacao", "IDTrimestre",
        "DataLancamento", "RazaoSocial", "CPF", "MarcaExibida", "Vendedor", "TipoDocumento",
        "Origem", "SDR", "Agencia", "CnpjAgencia", "Bureau", "CnpjBureau", "Intermediario",
        "CnpjIntermediario", "QuantidadePontos", "QuantidadeFaces", "TotalFaturamentoBrutoMensal",
        "TotalPercentualPermuta", "TotalCotaOportunidade", "TotalValorPermuta",
        "TotalFaturamentoLiquidoPermuta", "TotalBrutoContrato", "TotalLiquidoContratoAGBRCTACORDO",
        "TotalLiquidoContratoAGBRVENDGERCOOR", "TotalPercentualAgencia", "TotalValorMensalAgencia",
        "TotalPercentualBureau", "TotalValorBureauMensal", "TotalPercentualCartaAcordo",
        "TotalValorCartaAcordoMensal", "TotalValorOutrasComissoes", "TotalFaturamentoLiquidoMensal",
        "TotalPercentualComissaoVendedor", "TotalValorVendedor", "ValorVendedorTotal",
        "TotalPercentualComissaoCoordenacao", "Observacao", "BitAtivo",
    ]

    header_igual = (
        isinstance(header_existente, dict)
        and _registro_dinamico_equivalente(
            header_existente,
            valores_solicitacao,
            campos_header_comparacao,
            nome_tabela=TABELA_SOLICITACAO_CONTRATO,
        )
    )

    if isinstance(header_existente, dict) and header_existente.get("IDFatoSolicitacaoContratoEuromidia") not in (None, "", 0):
        id_solicitacao = int(header_existente.get("IDFatoSolicitacaoContratoEuromidia") or 0)

        if not header_igual:
            _atualizar_registro_dinamico_por_id(
                TABELA_SOLICITACAO_CONTRATO,
                "IDFatoSolicitacaoContratoEuromidia",
                id_solicitacao,
                valores_solicitacao,
                colunas_getdate=("DataAtualizacao",),
            )
    else:
        id_solicitacao = _inserir_registro_dinamico_output_id(
            TABELA_SOLICITACAO_CONTRATO,
            "IDFatoSolicitacaoContratoEuromidia",
            valores_solicitacao,
            colunas_getdate=("DataCriacao", "DataAtualizacao", "DataEnvioAvaliacao"),
        )
        header_igual = False

    coluna_atividade_item = _obter_nome_coluna_atividade_solicitacao_item()

    itens_snapshot = _montar_itens_snapshot_solicitacao_do_card(
        id_card=int(id_card),
        id_solicitacao=int(id_solicitacao),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
        id_contrato_existente=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        item_contrato=item_contrato if isinstance(item_contrato, dict) else None,
        contrato_row=contrato_row if isinstance(contrato_row, dict) else None,
        empresa=empresa_principal_snapshot if empresa_principal_snapshot else None,
        descricao_card=descricao_card,
        bit_registro_ativo=bit_registro_ativo,
        bit_solicitacao_ativa=bit_solicitacao_ativa,
        coluna_atividade_item=coluna_atividade_item,
        tipo_norm=tipo_norm,
        id_tipo_cliente=id_tipo_cliente,
        cod_ponto_contrato=cod_ponto_contrato,
        cod_face_contrato=cod_face_contrato,
        dados_item_formulario=dados_item_formulario,
        dados_itens_formulario=dados_itens_formulario,
        id_vendedor_formulario=id_vendedor_formulario,
        nome_vendedor_formulario=nome_vendedor_formulario,
    )

    if not itens_snapshot:
        return {
            "sincronizado": False,
            "motivo": "nenhum_item_snapshot_montado",
            "id_solicitacao": int(id_solicitacao),
        }

    sem_alteracao_itens = True

    for valores_item in itens_snapshot:
        id_item_contrato_atual = _int_positivo_ou_none(valores_item.get("IDFatoControleContratosItensEuromidia"))

        item_existente = _buscar_item_snapshot_existente_para_upsert(
            id_solicitacao=int(id_solicitacao),
            id_contrato=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
            id_item_contrato=id_item_contrato_atual,
            cod_ponto=valores_item.get("CodPonto"),
            cod_face=valores_item.get("CodFace"),
        )

        campos_item_comparacao = [
            "IDFatoSolicitacaoContratoEuromidia", "IDFatoControleContratosEuromidia",
            "IDFatoControleContratosItensEuromidia", "IDFatoKanbanCard", "IDVendedor", "IDPainelEuromidia",
            "IDDimFacesPaineis", "IDDimCheckingHistorico", "IDEmpresaProprietaria", "Referencia",
            "NumeroContrato", "NumeroPrevia", "CNPJ", "CodPonto", "CodFace", "DataLancamento", "Cota",
            "CidadeExibicao", "Tipo", "Origem", "TipoDocumento", "RazaoSocial", "MarcaExibida", "Vendedor",
            "Agencia", "CnpjAgencia", "Bureau", "CnpjBureau", "Intermediario", "CnpjIntermediario",
            "DataAssinaturaRenovacao", "IDTrimestre", "DataInicioPrevisto", "DataTerminoPrevisto",
            "InicioRenovacao", "FaturamentoBrutoMensal", "PercentualPermuta", "CotaOportunidade",
            "ValorPermuta", "FaturamentoLiquidoPermuta", "TotalBrutoContrato",
            "TotalLiquidoContratoAGBRCTACORDO", "TotalLiquidoContratoAGBRVENDGERCOOR", "PercentualAgencia",
            "ValorMensalAgencia", "PercentualBureau", "ValorBureauMensal", "PercentualCartaAcordo",
            "ValorCartaAcordoMensal", "ValorOutrasComissoes", "FaturamentoLiquidoMensal", "Status", "OBS", "BitAtivo",
            "IDPainelEuromidia", "IDDimFacesPaineis", "CodPonto", "CodFace", "CidadeExibicao", "Tipo",
        ]
        if coluna_atividade_item == "BitSolicitacaoAtiva":
            campos_item_comparacao.append("BitSolicitacaoAtiva")

        item_igual = (
            bool(item_existente)
            and _registro_dinamico_equivalente(
                item_existente,
                valores_item,
                campos_item_comparacao,
                nome_tabela=TABELA_SOLICITACAO_CONTRATO_ITEM,
            )
        )

        if item_existente and item_existente.get("IDFatoSolicitacaoContratoItemEuromidia") not in (None, "", 0):
            id_item_snapshot = int(item_existente.get("IDFatoSolicitacaoContratoItemEuromidia") or 0)

            if not item_igual:
                _atualizar_registro_dinamico_por_id(
                    TABELA_SOLICITACAO_CONTRATO_ITEM,
                    "IDFatoSolicitacaoContratoItemEuromidia",
                    id_item_snapshot,
                    valores_item,
                    colunas_getdate=("DataAtualizacao",),
                )
                sem_alteracao_itens = False
        else:
            id_item_snapshot = _inserir_registro_dinamico_output_id(
                TABELA_SOLICITACAO_CONTRATO_ITEM,
                "IDFatoSolicitacaoContratoItemEuromidia",
                valores_item,
                colunas_getdate=("DataCriacao", "DataAtualizacao"),
            )
            sem_alteracao_itens = False

        if id_item_snapshot not in (None, "", 0):
            _inativar_duplicados_do_item_snapshot(
                id_solicitacao=int(id_solicitacao),
                id_item_manter=int(id_item_snapshot),
                id_contrato=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
                id_item_contrato=id_item_contrato_atual,
                cod_ponto=valores_item.get("CodPonto"),
                cod_face=valores_item.get("CodFace"),
                coluna_atividade_item=coluna_atividade_item,
            )

    return {
        "sincronizado": True,
        "sem_alteracao": bool(header_igual and sem_alteracao_itens),
        "id_solicitacao": int(id_solicitacao),
        "id_item_contrato": int(id_item_contrato) if id_item_contrato not in (None, "", 0) else None,
        "tipo_solicitacao": tipo_norm,
        "bit_solicitacao_ativa": bit_solicitacao_ativa,
        "id_solicitacao_existente": id_solicitacao_existente,
        "quantidade_itens_snapshot": len(itens_snapshot),
    }







def _resolver_tipo_solicitacao_por_tags_ativas(tags_ativas: list[dict[str, Any]]) -> str:
    nomes_tags = {
        _normalizar_texto_comparacao(item.get("NomeTag"))
        for item in (tags_ativas or [])
        if isinstance(item, dict)
    }

    tem_novo_contrato = _normalizar_texto_comparacao(NOME_TAG_TIPO_CONTRATO_NOVO) in nomes_tags
    tem_aditivo = _normalizar_texto_comparacao(NOME_TAG_TIPO_CONTRATO_ADITIVO) in nomes_tags

    if tem_novo_contrato and tem_aditivo:
        raise ValueError(
            "O card está com as tags 'Novo Contrato' e 'Aditivo' ao mesmo tempo. Corrija isso antes de enviar para avaliação."
        )

    if tem_novo_contrato:
        return "NOVO_CONTRATO"

    if tem_aditivo:
        return "ADITIVO"

    raise ValueError(
        "Para enviar o contrato para avaliação, o card precisa ter a tag 'Novo Contrato' ou 'Aditivo'."
    )


def _obter_id_status_contrato_em_avaliacao() -> int:
    """
    Quando a tag 'Contrato em Avaliação' entra no card,
    a solicitação de contrato deve nascer com IDDimStatusContratos = 1.

    Importante:
    - aqui eu NÃO procuro status pelo texto da DimStatusContratos;
    - eu retorno diretamente o ID definido pela sua regra de negócio;
    - ainda assim eu valido se esse ID existe na tabela para evitar gravar lixo.
    """
    if not _objeto_existe(TABELA_STATUS_CONTRATOS):
        raise RuntimeError(
            f"A tabela {TABELA_STATUS_CONTRATOS} não existe."
        )

    if not _coluna_existe(TABELA_STATUS_CONTRATOS, "IDDimStatusContratos"):
        raise RuntimeError(
            f"A coluna IDDimStatusContratos não existe em {TABELA_STATUS_CONTRATOS}."
        )

    id_status_contrato = 1

    sql = text(
        f"""
        SELECT TOP (1)
            IDDimStatusContratos
        FROM {TABELA_STATUS_CONTRATOS}
        WHERE IDDimStatusContratos = :id_status;
        """
    )

    valor = db.session.execute(
        sql,
        {"id_status": int(id_status_contrato)},
    ).scalar()

    if valor in (None, ""):
        raise RuntimeError(
            f"O IDDimStatusContratos={id_status_contrato} não existe em {TABELA_STATUS_CONTRATOS}."
        )

    return int(valor)












def _sincronizar_status_contrato_e_solicitacao_por_fase_do_card(
    *,
    id_card: int,
    id_fase_atual: int,
    id_contrato_existente: int | None,
) -> dict[str, Any]:
    """
    Regra de negócio da fase 4:
    - se houver contrato existente selecionado no card, eu gravo IDDimStatusContratos = 1
      tanto em FatoControleContratosEuromidia quanto em FatoSolicitacaoContratoEuromidia;
    - se for fluxo de novo contrato, eu gravo IDDimStatusContratos = 1 apenas na solicitação,
      porque ainda não existe cabeçalho oficial em FatoControleContratosEuromidia.
    """
    if int(id_fase_atual or 0) != 4:
        return {
            "sincronizado": False,
            "motivo": "fase_diferente_de_4",
            "id_status_contrato": None,
            "linhas_solicitacao_atualizadas": 0,
            "linhas_controle_contrato_atualizadas": 0,
        }

    id_status_contrato = _obter_id_status_contrato_em_avaliacao()

    linhas_solicitacao_atualizadas = 0
    linhas_controle_contrato_atualizadas = 0

    if _objeto_existe(TABELA_SOLICITACAO_CONTRATO) and _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "IDDimStatusContratos"):
        sets_solicitacao = ["IDDimStatusContratos = :id_status_contrato"]
        if _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "DataAtualizacao"):
            sets_solicitacao.append("DataAtualizacao = GETDATE()")

        sql_update_solicitacao = text(
            f"""
            UPDATE {TABELA_SOLICITACAO_CONTRATO}
               SET {', '.join(sets_solicitacao)}
             WHERE IDFatoKanbanCard = :id_card;
            """
        )

        resultado_update_solicitacao = db.session.execute(
            sql_update_solicitacao,
            {
                "id_card": int(id_card),
                "id_status_contrato": int(id_status_contrato),
            },
        )
        linhas_solicitacao_atualizadas = int(resultado_update_solicitacao.rowcount or 0)

    if (
        id_contrato_existente not in (None, "", 0)
        and _objeto_existe(TABELA_CONTROLE_CONTRATOS)
        and _coluna_existe(TABELA_CONTROLE_CONTRATOS, "IDDimStatusContratos")
    ):
        sets_controle = ["IDDimStatusContratos = :id_status_contrato"]
        if _coluna_existe(TABELA_CONTROLE_CONTRATOS, "DataAtualizacao"):
            sets_controle.append("DataAtualizacao = GETDATE()")

        sql_update_controle = text(
            f"""
            UPDATE {TABELA_CONTROLE_CONTRATOS}
               SET {', '.join(sets_controle)}
             WHERE IDFatoControleContratosEuromidia = :id_contrato;
            """
        )

        resultado_update_controle = db.session.execute(
            sql_update_controle,
            {
                "id_contrato": int(id_contrato_existente),
                "id_status_contrato": int(id_status_contrato),
            },
        )
        linhas_controle_contrato_atualizadas = int(resultado_update_controle.rowcount or 0)

    return {
        "sincronizado": True,
        "motivo": "status_fase_4_sincronizado",
        "id_status_contrato": int(id_status_contrato),
        "linhas_solicitacao_atualizadas": int(linhas_solicitacao_atualizadas),
        "linhas_controle_contrato_atualizadas": int(linhas_controle_contrato_atualizadas),
        "id_contrato_existente": int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
    }











def _obter_valor_mensal_item_solicitacao(item: dict[str, Any]) -> Decimal | None:
    for campo in ("ValorVendaFinal", "NovoValor", "ValorTabela"):
        valor = _valor_decimal(item.get(campo))
        if valor is not None:
            return valor
    return None



def _montar_resumo_paineis_solicitacao(
    painel_faces: list[dict[str, Any]],
) -> dict[str, Any]:
    codigos_ponto: set[str] = set()
    codigos_face: set[str] = set()
    valor_total_mensal = Decimal("0")

    for item in painel_faces or []:
        cod_ponto = str(item.get("CodPonto") or "").strip()
        cod_face = str(item.get("CodFace") or "").strip().upper()

        if cod_ponto:
            codigos_ponto.add(cod_ponto)

        if cod_face:
            codigos_face.add(cod_face)

        valor_item = _obter_valor_mensal_item_solicitacao(item)
        if valor_item is not None:
            valor_total_mensal += valor_item

    return {
        "quantidade_pontos": len(codigos_ponto),
        "quantidade_faces": len(codigos_face),
        "valor_total_mensal": valor_total_mensal,
    }





def _criar_solicitacao_contrato_em_avaliacao_para_card(
    *,
    id_card: int,
    id_usuario: int,
    id_empresa_proprietaria: int,
) -> dict[str, Any]:
    """
    Quando a tag 'Contrato em Avaliação' entra no card, eu ativo o snapshot
    já persistido do vínculo contratual do próprio card.

    Regras:
    - não duplico nova solicitação para o mesmo card sem mudança real;
    - se o snapshot ainda não existir, eu crio;
    - se já existir, eu faço upsert pelo ID do card;
    - a atividade para fila de aprovação passa a 1 quando a tag 14 entra.
    """
    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe.get("card"), dict) else {}

    id_empresa_relacionada = _obter_id_empresa_relacionada_card(card)
    tipo_solicitacao = (
        card.get("tipo_contrato")
        or card.get("TipoSolicitacao")
        or (TIPO_SOLICITACAO_ADITIVO if card.get("BitAditivo") else None)
        or (TIPO_SOLICITACAO_NOVO if card.get("BitContratoNovo") else None)
    )
    id_contrato_existente = (
        card.get("IDFatoControleContratosEuromidia")
        or card.get("IDFatoControleContratoEuromidia")
        or None
    )
    cod_ponto_contrato = (
        card.get("CodPontoContrato")
        or card.get("cod_ponto_contrato")
        or None
    )
    cod_face_contrato = (
        card.get("CodFaceContrato")
        or card.get("cod_face_contrato")
        or None
    )

    resultado = _sincronizar_snapshot_solicitacao_contrato_do_card(
        id_card=int(id_card),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
        id_empresa_relacionada=int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
        tipo_contrato=tipo_solicitacao,
        id_contrato_existente=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        cod_ponto_contrato=cod_ponto_contrato,
        cod_face_contrato=cod_face_contrato,
        descricao_card=str(card.get("Descricao") or "").strip() or None,
        forcar_solicitacao_ativa=True,
    )

    return {
        "criada": not bool(resultado.get("sem_alteracao")),
        "id_solicitacao": resultado.get("id_solicitacao"),
        "tipo_solicitacao": resultado.get("tipo_solicitacao"),
        "quantidade_itens": 1 if resultado.get("id_item_contrato") else 0,
        "id_controle_contrato": int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        "id_item_controle_contrato": resultado.get("id_item_contrato"),
        "modo": "upsert_snapshot_card",
    }









def _sincronizar_ativacao_solicitacao_por_fase_do_card(
    *,
    id_card: int,
    id_usuario: int,
    id_empresa_proprietaria: int,
    dados_formulario_solicitacao: dict[str, Any] | None = None,
    tipo_contrato_fallback: object = None,
    id_contrato_existente_fallback: object = None,
    cod_ponto_contrato_fallback: object = None,
    cod_face_contrato_fallback: object = None,
    id_empresa_relacionada_fallback: object = None,
) -> dict[str, Any]:
    """
    Eu sincronizo a solicitação de contrato após movimento de fase do card.

    Regra:
    - sempre sincronizo o snapshot principal do card;
    - sempre que o card tiver contrato + CodPonto + CodFace, eu mantenho
      FatoControleContratosItensEuromidia.IDFatoKanbanCard apontando para o card atual;
    - a fase 4 continua sendo especial apenas para atividade/status da solicitação;
    - depois propago BitSolicitacaoAtiva para TODOS os itens da solicitação do card.
    """
    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe.get("card"), dict) else {}

    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0)

    id_empresa_relacionada = _obter_id_empresa_relacionada_card(card)
    if id_empresa_relacionada in (None, "", 0) and id_empresa_relacionada_fallback not in (None, "", 0):
        id_empresa_relacionada = int(id_empresa_relacionada_fallback)

    snapshot_solicitacao_existente = _obter_ultima_solicitacao_contrato_por_card(int(id_card)) or {}

    tipo_solicitacao_bruto = (
        card.get("tipo_contrato")
        or card.get("tipo_contrato_card")
        or card.get("TipoSolicitacao")
        or tipo_contrato_fallback
        or snapshot_solicitacao_existente.get("TipoSolicitacao")
        or (TIPO_SOLICITACAO_ADITIVO if card.get("BitAditivo") else None)
        or (TIPO_SOLICITACAO_NOVO if card.get("BitContratoNovo") else None)
    )

    tipo_solicitacao = _normalizar_tipo_contrato_card(tipo_solicitacao_bruto)

    id_contrato_existente = (
        card.get("IDFatoControleContratosEuromidia")
        or card.get("IDFatoControleContratoEuromidia")
        or id_contrato_existente_fallback
        or snapshot_solicitacao_existente.get("IDFatoControleContratosEuromidia")
        or snapshot_solicitacao_existente.get("IDFatoControleContratoEuromidia")
        or None
    )

    cod_ponto_contrato = (
        card.get("CodPontoContrato")
        or card.get("cod_ponto_contrato")
        or cod_ponto_contrato_fallback
        or snapshot_solicitacao_existente.get("SolicitacaoCodPonto")
        or snapshot_solicitacao_existente.get("CodPonto")
        or None
    )

    cod_face_contrato = (
        card.get("CodFaceContrato")
        or card.get("cod_face_contrato")
        or cod_face_contrato_fallback
        or snapshot_solicitacao_existente.get("SolicitacaoCodFace")
        or snapshot_solicitacao_existente.get("CodFace")
        or None
    )

    if tipo_solicitacao not in {TIPO_SOLICITACAO_ADITIVO, TIPO_SOLICITACAO_NOVO}:
        if id_contrato_existente not in (None, "", 0):
            tipo_solicitacao = TIPO_SOLICITACAO_ADITIVO
        else:
            tipo_solicitacao = TIPO_SOLICITACAO_NOVO

    current_app.logger.info(
        "KANBAN: sincronizacao solicitacao pos-salvar | id_card=%s | tipo=%s | id_contrato=%s | cod_ponto=%s | cod_face=%s | origem_tipo=%r",
        id_card,
        tipo_solicitacao,
        id_contrato_existente,
        cod_ponto_contrato,
        cod_face_contrato,
        tipo_solicitacao_bruto,
    )

    resultado = _sincronizar_snapshot_solicitacao_contrato_do_card(
        id_card=int(id_card),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
        id_empresa_relacionada=int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
        tipo_contrato=tipo_solicitacao,
        id_contrato_existente=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        cod_ponto_contrato=cod_ponto_contrato,
        cod_face_contrato=cod_face_contrato,
        descricao_card=str(card.get("Descricao") or "").strip() or None,
        forcar_solicitacao_ativa=True if int(id_fase_atual) == 4 else None,
        dados_formulario_solicitacao=dados_formulario_solicitacao,
    )

    if not resultado.get("sincronizado"):
        return resultado

    resultado["status_fase_4"] = _sincronizar_status_contrato_e_solicitacao_por_fase_do_card(
        id_card=int(id_card),
        id_fase_atual=int(id_fase_atual),
        id_contrato_existente=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
    )

    sincronizacao_item_oficial_contrato = _sincronizar_item_oficial_contrato_com_card_salvo(
        id_card=int(id_card),
        id_empresa_relacionada=int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
        id_contrato_existente=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        cod_ponto_contrato=cod_ponto_contrato,
        cod_face_contrato=cod_face_contrato,
        contexto_erro="mover o card de fase",
    )

    resultado["quantidade_itens_controle_contrato_atualizados"] = int(
        sincronizacao_item_oficial_contrato.get("quantidade_itens_controle_contrato_atualizados") or 0
    )
    resultado["motivo_itens_controle_contrato"] = sincronizacao_item_oficial_contrato.get(
        "motivo_itens_controle_contrato"
    )

    resultado["snapshot_preco_praticado"] = _sincronizar_snapshot_preco_praticado_fase_4(
        id_card=int(id_card),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )

    coluna_atividade_item = _obter_nome_coluna_atividade_solicitacao_item()
    if not coluna_atividade_item:
        resultado["quantidade_itens_atualizados"] = 0
        resultado["motivo_itens"] = "coluna_atividade_item_ausente"
        return resultado

    if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO_ITEM):
        resultado["quantidade_itens_atualizados"] = 0
        resultado["motivo_itens"] = "tabela_solicitacao_item_ausente"
        return resultado

    bit_solicitacao_ativa = 1 if bool(resultado.get("bit_solicitacao_ativa")) else 0

    sets_item = [f"[{coluna_atividade_item}] = :bit_solicitacao_ativa"]
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO_ITEM, "DataAtualizacao"):
        sets_item.append("DataAtualizacao = GETDATE()")

    sql_update_itens = text(
        f"""
        UPDATE {TABELA_SOLICITACAO_CONTRATO_ITEM}
           SET {', '.join(sets_item)}
         WHERE IDFatoKanbanCard = :id_card;
        """
    )

    db.session.execute(
        sql_update_itens,
        {
            "id_card": int(id_card),
            "bit_solicitacao_ativa": int(bit_solicitacao_ativa),
        },
    )

    sql_count_itens = text(
        f"""
        SELECT COUNT(1) AS Quantidade
        FROM {TABELA_SOLICITACAO_CONTRATO_ITEM}
        WHERE IDFatoKanbanCard = :id_card
          AND ISNULL([{coluna_atividade_item}], 0) = :bit_solicitacao_ativa;
        """
    )

    quantidade_itens = db.session.execute(
        sql_count_itens,
        {
            "id_card": int(id_card),
            "bit_solicitacao_ativa": int(bit_solicitacao_ativa),
        },
    ).scalar()

    resultado["quantidade_itens_atualizados"] = int(quantidade_itens or 0)
    resultado["motivo_itens"] = "itens_solicitacao_sincronizados"
    return resultado




def _obter_snapshot_card_log(id_card: int, *, incluir_inativo: bool = True) -> dict[str, Any] | None:
    filtro_ativo = "" if incluir_inativo else "AND c.Ativo = 1"
    sql = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.Descricao,
            c.StatusCard,
            c.Ativo,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDEmpresaProprietaria,
            {_sql_select_id_origem_atendimento_card('c')}
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')}
        FROM {TABELA_CARD} c
        WHERE c.IDFatoKanbanCard = :id_card
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()
    return dict(row) if row else None



def _registrar_log_card(
    *,
    id_card: int,
    id_kanban: int | None,
    id_empresa_proprietaria: int | None,
    id_usuario_acao: int | None,
    tipo_evento: str,
    subtipo_evento: str | None = None,
    id_fase_de: int | None = None,
    id_fase_para: int | None = None,
    motivo: str | None = None,
    observacao: str | None = None,
    tabela_origem: str | None = None,
    id_registro_origem: int | None = None,
    payload_antes: dict[str, Any] | None = None,
    payload_depois: dict[str, Any] | None = None,
) -> None:
    if not _objeto_existe(TABELA_CARD_LOG):
        return

    id_empresa_relacionada = _obter_id_empresa_relacionada_card(payload_depois) or _obter_id_empresa_relacionada_card(payload_antes)
    id_usuario_relacionado = _obter_id_usuario_relacionado_card(payload_depois) or _obter_id_usuario_relacionado_card(payload_antes)

    valores = {
        "IDFatoKanbanCard": int(id_card),
        "IDDimKanban": int(id_kanban) if id_kanban else None,
        "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria else None,
        "IDEmpresaRelacionada": int(id_empresa_relacionada) if id_empresa_relacionada else None,
        "IDUsuarioAcao": int(id_usuario_acao) if id_usuario_acao else None,
        "IDUsuarioRelacionado": int(id_usuario_relacionado) if id_usuario_relacionado else None,
        "TipoEvento": (tipo_evento or "").strip()[:80] or None,
        "SubtipoEvento": (subtipo_evento or "").strip()[:120] or None,
        "IDFaseDe": int(id_fase_de) if id_fase_de else None,
        "IDFasePara": int(id_fase_para) if id_fase_para else None,
        "Motivo": (motivo or "").strip()[:300] or None,
        "Observacao": (observacao or "").strip()[:2000] or None,
        "TabelaOrigem": (tabela_origem or "").strip()[:150] or None,
        "IDRegistroOrigem": int(id_registro_origem) if id_registro_origem else None,
        "PayloadAntes": _json_para_log(payload_antes),
        "PayloadDepois": _json_para_log(payload_depois),
        "TextoLivre": (observacao or "").strip()[:2000] or None,
    }

    _inserir_registro_dinamico(
        TABELA_CARD_LOG,
        valores,
        colunas_getdate=("OcorridoEm", "DataHoraEvento", "CriadoEm", "DataInserido"),
    )



def _contar_cards_ativos_fase(id_fase: int) -> int:
    sql = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD}
        WHERE IDDimKanbanFaseAtual = :id_fase
          AND Ativo = 1;
    """)
    return int(db.session.execute(sql, {"id_fase": int(id_fase)}).scalar() or 0)



def _contar_cards_ativos_kanban(id_kanban: int) -> int:
    sql = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD}
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    return int(db.session.execute(sql, {"id_kanban": int(id_kanban)}).scalar() or 0)



def _obter_kanban_autorizado(id_kanban: int, *, incluir_inativo: bool = False) -> dict[str, Any]:
    id_emp = _id_empresa_usuario_or_403()
    filtro_ativo = "" if incluir_inativo else "AND k.Ativo = 1"
    sql = text(f"""
        SELECT
            k.IDDimKanban,
            k.NomeKanban,
            k.Descricao,
            k.Ativo,
            k.CriadoEm,
            k.BitPrincipal,
            k.IDEmpresaProprietaria
        FROM {TABELA_KANBAN} k
        WHERE k.IDDimKanban = :id_kanban
          AND k.IDEmpresaProprietaria = :id_emp
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_kanban": id_kanban, "id_emp": id_emp}).mappings().first()
    if not row:
        abort(403, "Você não tem permissão para acessar este kanban")
    return dict(row)



def _obter_cfg_kanban(id_kanban: int) -> dict[str, Any]:
    kanban = _obter_kanban_autorizado(id_kanban)
    id_emp_prop = int(kanban.get("IDEmpresaProprietaria") or 0)
    bit_principal = int(kanban.get("BitPrincipal") or 0)
    return {
        "IDDimKanban": int(kanban.get("IDDimKanban") or id_kanban),
        "IDEmpresaProprietaria": kanban.get("IDEmpresaProprietaria"),
        "BitPrincipal": kanban.get("BitPrincipal"),
        "MostrarPainelFaceNoCard": bool((id_emp_prop == 3) and (bit_principal == 1)),
    }



def _obter_fase_autorizada(id_fase: int, *, incluir_inativa: bool = False) -> dict[str, Any]:
    id_emp = _id_empresa_usuario_or_403()
    filtro_ativo = "" if incluir_inativa else "AND f.Ativo = 1"
    cor_select = "f.CorHex," if _coluna_existe(TABELA_KANBAN_FASE, "CorHex") else "CAST(NULL AS varchar(7)) AS CorHex,"
    sql = text(f"""
        SELECT
            f.IDDimKanbanFase,
            f.IDDimKanban,
            f.NomeFase,
            f.OrdemFase,
            f.TipoFase,
            {cor_select}
            f.Ativo,
            k.IDEmpresaProprietaria,
            k.BitPrincipal
        FROM {TABELA_KANBAN_FASE} f
        JOIN {TABELA_KANBAN} k
          ON k.IDDimKanban = f.IDDimKanban
        WHERE f.IDDimKanbanFase = :id_fase
          AND k.IDEmpresaProprietaria = :id_emp
          AND k.Ativo = 1
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_fase": id_fase, "id_emp": id_emp}).mappings().first()
    if not row:
        abort(403, "Você não tem permissão para acessar esta fase")
    return dict(row)



def _validar_fase_do_kanban(id_kanban: int, id_fase: int) -> bool:
    try:
        fase = _obter_fase_autorizada(id_fase)
    except Exception:
        return False
    return int(fase.get("IDDimKanban") or 0) == int(id_kanban or 0)







def _obter_motivos_encerramento_card(*, incluir_inativos: bool = False) -> list[dict[str, Any]]:
    """
    Lê os motivos de encerramento válidos do kanban.

    Regras:
    - usa DimKanbanMotivoEncerramento
    - prioriza a empresa do usuário
    - aceita também registros globais / empresa 3, para não quebrar ambiente compartilhado
    - devolve um Codigo derivado do NomeMotivo para manter compatibilidade com o front antigo
      (ex.: "Preço" -> "PRECO", "Apenas Informações" -> "APENAS_INFORMACOES")
    """
    if not _objeto_existe(TABELA_MOTIVO_ENCERRAMENTO_CARD):
        return []

    id_emp = _id_empresa_usuario_or_403()

    chave = _chave_cache_json(
        "kanban:motivos:encerramento",
        id_emp,
        1 if incluir_inativos else 0,
    )
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, list):
        return em_cache

    filtro_ativo = "" if incluir_inativos else "AND m.Ativo = 1"

    sql = text(f"""
        SELECT
            m.IDDimKanbanMotivoEncerramento,
            m.NomeMotivo,
            m.TipoMotivo,
            m.Ativo,
            m.IDEmpresaProprietaria,
            m.IDDimUsuarios
        FROM {TABELA_MOTIVO_ENCERRAMENTO_CARD} m
        WHERE
            (m.IDEmpresaProprietaria = :id_emp OR m.IDEmpresaProprietaria = 3 OR m.IDEmpresaProprietaria IS NULL)
            AND UPPER(LTRIM(RTRIM(ISNULL(m.TipoMotivo, 'ENCERRAMENTO')))) = 'ENCERRAMENTO'
            {filtro_ativo}
            AND NULLIF(LTRIM(RTRIM(ISNULL(m.NomeMotivo, ''))), '') IS NOT NULL
        ORDER BY
            CASE
                WHEN m.IDEmpresaProprietaria = :id_emp THEN 0
                WHEN m.IDEmpresaProprietaria = 3 THEN 1
                ELSE 2
            END,
            m.NomeMotivo ASC,
            m.IDDimKanbanMotivoEncerramento ASC;
    """)

    rows = db.session.execute(sql, {"id_emp": int(id_emp)}).mappings().all()

    mapa_unico: dict[str, dict[str, Any]] = {}

    for row in rows:
        item = dict(row)
        nome_motivo = str(item.get("NomeMotivo") or "").strip()
        codigo_motivo = _normalizar_codigo_dominio(nome_motivo)

        if not codigo_motivo:
            continue

        item["Codigo"] = codigo_motivo
        item["Descricao"] = nome_motivo

        """
        Se vier motivo repetido entre empresa local e base compartilhada,
        fico com o primeiro porque a ordenação já priorizou a empresa do usuário.
        """
        if codigo_motivo not in mapa_unico:
            mapa_unico[codigo_motivo] = item

    resultado = list(mapa_unico.values())
    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado









def _normalizar_motivo_encerramento_card(motivo_informado: Any) -> dict[str, Any] | None:
    """
    Normaliza o motivo enviado pelo front.

    Aceita:
    - ID numérico da DimKanbanMotivoEncerramento
    - Nome do motivo
    - Código derivado do nome
    - aliases antigos do HTML atual (PRECO, DESISTENCIA, APENAS_INFORMACOES, OUTRO_MOTIVO)
    """
    motivos = _obter_motivos_encerramento_card(incluir_inativos=True)
    if not motivos:
        return None

    texto = str(motivo_informado or "").strip()
    if not texto:
        return None

    id_informado: int | None = None
    try:
        id_informado = int(texto)
    except Exception:
        id_informado = None

    codigo_informado = _normalizar_codigo_dominio(texto)

    aliases = {
        "OUTRO_MOTIVO": "OUTROS",
        "OUTROS": "OUTROS",
        "PRECO": "PRECO",
        "DESISTENCIA": "DESISTENCIA",
        "APENAS_INFORMACOES": "APENAS_INFORMACOES",
        "CONCORRENTE": "CONCORRENTE",
    }

    codigos_aceitos = set()
    if codigo_informado:
        codigos_aceitos.add(codigo_informado)
        if codigo_informado in aliases:
            codigos_aceitos.add(aliases[codigo_informado])

    nome_comparacao = _normalizar_texto_comparacao(texto)

    for item in motivos:
        item_dict = dict(item)

        try:
            id_item = int(item_dict.get("IDDimKanbanMotivoEncerramento") or 0)
        except Exception:
            id_item = 0

        nome_item = str(item_dict.get("NomeMotivo") or "").strip()
        codigo_item = _normalizar_codigo_dominio(nome_item)

        if id_informado and id_item == id_informado:
            item_dict["Codigo"] = codigo_item
            item_dict["Descricao"] = nome_item
            return item_dict

        if codigo_item and codigo_item in codigos_aceitos:
            item_dict["Codigo"] = codigo_item
            item_dict["Descricao"] = nome_item
            return item_dict

        if nome_comparacao and _normalizar_texto_comparacao(nome_item) == nome_comparacao:
            item_dict["Codigo"] = codigo_item
            item_dict["Descricao"] = nome_item
            return item_dict

    return None









def _registrar_historico_encerramento_card(
    *,
    id_card: int,
    id_motivo_encerramento: int,
    nome_motivo: str,
    id_fase: int | None,
    id_usuario: int,
    observacoes: str | None,
) -> dict[str, Any]:
    """
    Sempre registra o encerramento nesta tabela:
    Silver.FatoDimHistoricoEncerramentoCard
    """
    if not _objeto_existe(TABELA_HISTORICO_ENCERRAMENTO_CARD):
        raise RuntimeError(
            "A tabela Silver.FatoDimHistoricoEncerramentoCard não existe ou não está acessível."
        )

    sql = text(f"""
        INSERT INTO {TABELA_HISTORICO_ENCERRAMENTO_CARD}
        (
            NomeMotivo,
            IDDimKanbanMotivoEncerramento,
            IDDimKanbanFase,
            IDFatoKanbanCard,
            IDDimUsuarios,
            Observacoes,
            DataAtualizacao
        )
        OUTPUT
            INSERTED.IDFatoDimHistoricoEncerramentoCard,
            INSERTED.NomeMotivo,
            INSERTED.IDDimKanbanMotivoEncerramento,
            INSERTED.IDDimKanbanFase,
            INSERTED.IDFatoKanbanCard,
            INSERTED.IDDimUsuarios,
            INSERTED.Observacoes,
            INSERTED.DataAtualizacao
        VALUES
        (
            :nome_motivo,
            :id_motivo_encerramento,
            :id_fase,
            :id_card,
            :id_usuario,
            :observacoes,
            GETDATE()
        );
    """)

    row = db.session.execute(
        sql,
        {
            "nome_motivo": str(nome_motivo or "").strip()[:100],
            "id_motivo_encerramento": int(id_motivo_encerramento),
            "id_fase": int(id_fase) if id_fase else None,
            "id_card": int(id_card),
            "id_usuario": int(id_usuario),
            "observacoes": (str(observacoes or "").strip()[:1000] or None),
        },
    ).mappings().first()

    if not row:
        raise RuntimeError("Não foi possível gravar o histórico de encerramento do card.")

    return dict(row)







def _obter_card_autorizado(id_card: int, *, incluir_inativo: bool = False) -> dict[str, Any]:
    id_emp = _id_empresa_usuario_or_403()
    filtro_ativo = "" if incluir_inativo else "AND c.Ativo = 1"

    select_id_contrato = (
        "c.IDFatoControleContratosEuromidia AS IDFatoControleContratosEuromidia,"
        if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia")
        else (
            "c.IDFatoControleContratoEuromidia AS IDFatoControleContratosEuromidia,"
            if _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia")
            else "CAST(NULL AS int) AS IDFatoControleContratosEuromidia,"
        )
    )

    select_cod_ponto_contrato = (
        "c.CodPontoContrato AS CodPontoContrato,"
        if _coluna_existe(TABELA_CARD, "CodPontoContrato")
        else "CAST(NULL AS varchar(50)) AS CodPontoContrato,"
    )

    select_cod_face_contrato = (
        "c.CodFaceContrato AS CodFaceContrato,"
        if _coluna_existe(TABELA_CARD, "CodFaceContrato")
        else "CAST(NULL AS varchar(50)) AS CodFaceContrato,"
    )

    select_bit_aditivo = (
        "c.BitAditivo AS BitAditivo,"
        if _coluna_existe(TABELA_CARD, "BitAditivo")
        else "CAST(0 AS bit) AS BitAditivo,"
    )

    select_bit_contrato_novo = (
        "c.BitContratoNovo AS BitContratoNovo,"
        if _coluna_existe(TABELA_CARD, "BitContratoNovo")
        else "CAST(0 AS bit) AS BitContratoNovo,"
    )

    select_id_empresa_agencia = (
        "c.IDEmpresaAgencia AS IDEmpresaAgencia,"
        if _coluna_existe(TABELA_CARD, "IDEmpresaAgencia")
        else "CAST(NULL AS int) AS IDEmpresaAgencia,"
    )

    select_id_empresa_bureau = (
        "c.IDEmpresaBureau AS IDEmpresaBureau,"
        if _coluna_existe(TABELA_CARD, "IDEmpresaBureau")
        else "CAST(NULL AS int) AS IDEmpresaBureau,"
    )

    select_marca = (
        "c.Marca AS Marca,"
        if _coluna_existe(TABELA_CARD, "Marca")
        else "CAST(NULL AS nvarchar(100)) AS Marca,"
    )

    select_telefone = (
        "c.Telefone AS Telefone,"
        if _coluna_existe(TABELA_CARD, "Telefone")
        else "CAST(NULL AS varchar(30)) AS Telefone,"
    )

    select_email = (
        "c.Email AS Email,"
        if _coluna_existe(TABELA_CARD, "Email")
        else "CAST(NULL AS nvarchar(200)) AS Email,"
    )

    select_id_tipo_cliente_card = (
        "c.IDDimTipoCliente AS IDDimTipoCliente,"
        if _coluna_existe(TABELA_CARD, "IDDimTipoCliente")
        else (
            "c.IDDimKanbanTipoClienteDesconto AS IDDimTipoCliente,"
            if _coluna_existe(TABELA_CARD, "IDDimKanbanTipoClienteDesconto")
            else "CAST(NULL AS int) AS IDDimTipoCliente,"
        )
    )

    select_id_dim_cnaes = (
        "c.IDDimCnaes AS IDDimCnaes,"
        if _coluna_existe(TABELA_CARD, "IDDimCnaes")
        else "CAST(NULL AS int) AS IDDimCnaes,"
    )

    select_nome_empresa = (
        "c.NomeEmpresa AS NomeEmpresa,"
        if _coluna_existe(TABELA_CARD, "NomeEmpresa")
        else "CAST(NULL AS nvarchar(200)) AS NomeEmpresa,"
    )

    sql = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.Descricao,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDDimKanbanOrigem,
            c.IDDimKanbanMotivoEncerramento,
            c.MotivoEncerramentoObs,
            c.IDEmpresaProprietaria,
            {_sql_select_id_origem_atendimento_card('c')}
            {select_id_contrato}
            {select_cod_ponto_contrato}
            {select_cod_face_contrato}
            {select_bit_aditivo}
            {select_bit_contrato_novo}
            {select_id_empresa_agencia}
            {select_id_empresa_bureau}
            {select_marca}
            {select_telefone}
            {select_email}
            {select_id_tipo_cliente_card}
            {select_id_dim_cnaes}
            {select_nome_empresa}
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            k.BitPrincipal,
            k.IDEmpresaProprietaria AS IDEmpresaDoKanban
        FROM {TABELA_CARD} c
        JOIN {TABELA_KANBAN} k
          ON k.IDDimKanban = c.IDDimKanban
        WHERE c.IDFatoKanbanCard = :id_card
          AND k.IDEmpresaProprietaria = :id_emp
          AND k.Ativo = 1
          {filtro_ativo};
    """)

    row = db.session.execute(
        sql,
        {"id_card": int(id_card), "id_emp": int(id_emp)},
    ).mappings().first()

    if not row:
        abort(403, "Você não tem permissão para acessar este card")

    return dict(row)





def _fase_deve_aparecer_no_template(id_fase: int | None) -> bool:
  
    return int(id_fase or 0) != 9




def _obter_fases_kanban(id_kanban: int) -> list[dict[str, Any]]:
    cor_select = "CorHex," if _coluna_existe(TABELA_KANBAN_FASE, "CorHex") else "CAST(NULL AS varchar(7)) AS CorHex,"
    sql_fases = text(f"""
        SELECT
            IDDimKanbanFase,
            NomeFase,
            OrdemFase,
            TipoFase,
            {cor_select}
            Ativo
        FROM {TABELA_KANBAN_FASE}
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1
          AND IDDimKanbanFase <> 9
        ORDER BY OrdemFase ASC;
    """)
    fases = db.session.execute(sql_fases, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(fases)


def _obter_tags_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_tags = text("""
        SELECT
            IDDimKanbanTag,
            NomeTag,
            TipoTag,
            CorHex,
            Icone,
            AfetaCorCard,
            AplicacaoUnica
        FROM [Kanban].[Silver].[DimKanbanTag]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1
        ORDER BY NomeTag ASC;
    """)
    tags = db.session.execute(sql_tags, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(tags)



def _obter_card_tags_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_card_tags = text("""
        SELECT
            ct.IDFatoKanbanCard,
            t.IDDimKanbanTag,
            t.NomeTag,
            t.CorHex,
            t.Icone
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        JOIN [Kanban].[Silver].[DimKanbanTag] t
          ON t.IDDimKanbanTag = ct.IDDimKanbanTag
        WHERE t.IDDimKanban = :id_kanban
          AND t.Ativo = 1
          AND ct.RemovidoEm IS NULL;
    """)
    card_tags = db.session.execute(sql_card_tags, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(card_tags)





def _obter_cards_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_cards = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDEmpresaProprietaria,
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          {_sql_filtro_cards_nao_concluidos_no_quadro('c')}
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
            c.IDFatoKanbanCard DESC;
    """)
    rows = db.session.execute(sql_cards, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(rows)




def _valor_decimal(valor: Any) -> Decimal | None:
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _decimal_para_float(valor: Any) -> float | None:
    dec = _valor_decimal(valor)
    return float(dec) if dec is not None else None


def _normalizar_texto(valor: Any) -> str:
    return str(valor or "").strip()




@socketio.on("connect", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_connect_kanban(auth: Any | None = None):
    if not getattr(current_user, "is_authenticated", False):
        current_app.logger.warning("Socket kanban recusado: usuário não autenticado. sid=%s", getattr(request, "sid", None))
        return False

    emit(
        "socket_ack",
        {
            "ok": True,
            "evento": "connect",
            "id_usuario": _id_usuario(),
            "id_empresa": _id_empresa_usuario(),
        },
    )


@socketio.on("disconnect", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_disconnect_kanban():
    if getattr(current_user, "is_authenticated", False):
        current_app.logger.info(
            "Socket kanban desconectado. sid=%s usuario=%s empresa=%s",
            getattr(request, "sid", None),
            _id_usuario(),
            _id_empresa_usuario(),
        )


@socketio.on("entrar_kanban", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_entrar_kanban(payload: Any | None = None):
    if not getattr(current_user, "is_authenticated", False):
        emit("socket_erro", {"ok": False, "msg": "Usuário não autenticado."})
        disconnect()
        return

    dados = payload if isinstance(payload, dict) else {}

    try:
        id_kanban = int(dados.get("id_kanban") or 0)
    except Exception:
        id_kanban = 0

    if not id_kanban:
        emit("socket_erro", {"ok": False, "msg": "id_kanban inválido."})
        return

    try:
        _obter_kanban_autorizado(id_kanban)
    except Exception:
        emit(
            "socket_erro",
            {
                "ok": False,
                "id_kanban": id_kanban,
                "msg": "Você não tem permissão para entrar neste kanban.",
            },
        )
        return

    sala = _sala_kanban(id_kanban)
    join_room(sala)
    emit(
        "socket_ack",
        {
            "ok": True,
            "evento": "entrar_kanban",
            "id_kanban": id_kanban,
            "sala": sala,
            "sid": getattr(request, "sid", None),
        },
    )


@socketio.on("sair_kanban", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_sair_kanban(payload: Any | None = None):
    if not getattr(current_user, "is_authenticated", False):
        return

    dados = payload if isinstance(payload, dict) else {}

    try:
        id_kanban = int(dados.get("id_kanban") or 0)
    except Exception:
        id_kanban = 0

    if not id_kanban:
        return

    sala = _sala_kanban(id_kanban)
    leave_room(sala)
    emit(
        "socket_ack",
        {
            "ok": True,
            "evento": "sair_kanban",
            "id_kanban": id_kanban,
            "sala": sala,
            "sid": getattr(request, "sid", None),
        },
    )


def _obter_painel_por_id(id_painel: int) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            p.IDDimPaineisEuromidia,
            p.CodPonto,
            p.Tipo,
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.CEP,
            p.QuantidadeFaces,
            p.BitAtivo
        FROM [Integracao].[Silver].[DimPaineisEuromidia] p
        WHERE TRY_CONVERT(int, p.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
        ORDER BY p.DataAtualizacao DESC, p.IDDimPaineisEuromidia DESC;
    """)
    row = db.session.execute(sql, {"id_painel": int(id_painel)}).mappings().first()
    return dict(row) if row else None


def _resolver_face_do_painel(id_painel: int, cod_face: str) -> dict[str, Any] | None:
    painel = _obter_painel_por_id(id_painel)
    if not painel:
        return None

    cod_face = _normalizar_texto(cod_face)
    if not cod_face:
        return None

    sql = text("""
        SELECT TOP 1
            f.IDDimFacesPaineis,
            f.CodPonto,
            f.Face,
            f.CodFace,
            f.Tipo
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE TRY_CONVERT(int, f.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
          AND LTRIM(RTRIM(ISNULL(f.CodFace, ''))) = :cod_face
        ORDER BY f.IDDimFacesPaineis DESC;
    """)
    row = db.session.execute(sql, {"id_painel": int(id_painel), "cod_face": cod_face}).mappings().first()
    if row:
        return dict(row)

    sql_fallback = text("""
        SELECT TOP 1
            f.IDDimFacesPaineis,
            f.CodPonto,
            f.Face,
            f.CodFace,
            f.Tipo
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE TRY_CONVERT(int, f.CodPonto) = TRY_CONVERT(int, :cod_ponto)
          AND UPPER(LTRIM(RTRIM(ISNULL(f.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
          AND LTRIM(RTRIM(ISNULL(f.CodFace, ''))) = :cod_face
        ORDER BY f.IDDimFacesPaineis DESC;
    """)
    row = db.session.execute(
        sql_fallback,
        {
            "cod_ponto": painel.get("CodPonto"),
            "tipo_painel": _normalizar_texto(painel.get("Tipo")),
            "cod_face": cod_face,
        },
    ).mappings().first()
    return dict(row) if row else None


def _obter_custo_por_codponto(cod_ponto: int) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            c.IDDimCustoPainel,
            c.Ano,
            c.CodPonto,
            c.Origem,
            c.Valor,
            c.DataCarga
        FROM [Integracao].[Silver].[DimCustoPainel] c
        WHERE TRY_CONVERT(int, c.CodPonto) = TRY_CONVERT(int, :cod_ponto)
        ORDER BY c.Ano DESC, c.DataCarga DESC, c.IDDimCustoPainel DESC;
    """)
    row = db.session.execute(sql, {"cod_ponto": int(cod_ponto)}).mappings().first()
    return dict(row) if row else None


def _obter_precos_painel_face(id_painel: int, id_dim_face: int | None, tipo_painel: str) -> list[dict[str, Any]]:
    sql = text("""
        SELECT
            tp.IDDimTabelaPrecosEuromidia,
            tp.IDDimPaineisEuromidia,
            tp.IDDimFacesPaineis,
            tp.Tipo,
            tp.PeriodoExibicao,
            tp.ExibicoesDia,
            tp.Valor,
            tp.PoliticaTrocas,
            tp.Tabela,
            tp.DataPublicacao,
            tp.DataValidade,
            tp.DataAtualizacao,
            tp.BitAtivo,
            tp.AlteradoPor,
            tp.ValorTroca
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] tp
        WHERE TRY_CONVERT(int, tp.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
          AND UPPER(LTRIM(RTRIM(ISNULL(tp.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
          AND (
                :id_dim_face IS NULL
                OR tp.IDDimFacesPaineis IS NULL
                OR TRY_CONVERT(int, tp.IDDimFacesPaineis) = TRY_CONVERT(int, :id_dim_face)
              )
        ORDER BY
            CASE WHEN ISNULL(tp.BitAtivo, 0) = 1 THEN 0 ELSE 1 END,
            ISNULL(tp.DataValidade, '9999-12-31') DESC,
            ISNULL(tp.DataPublicacao, '9999-12-31') DESC,
            tp.IDDimTabelaPrecosEuromidia DESC;
    """)
    rows = db.session.execute(
        sql,
        {
            "id_painel": int(id_painel),
            "id_dim_face": int(id_dim_face) if id_dim_face else None,
            "tipo_painel": _normalizar_texto(tipo_painel),
        },
    ).mappings().all()
    return _rows_para_dicts(rows)


def _obter_preco_por_id(id_preco: int, id_painel: int, id_dim_face: int | None, tipo_painel: str) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            tp.IDDimTabelaPrecosEuromidia,
            tp.IDDimPaineisEuromidia,
            tp.IDDimFacesPaineis,
            tp.Tipo,
            tp.PeriodoExibicao,
            tp.ExibicoesDia,
            tp.Valor,
            tp.PoliticaTrocas,
            tp.Tabela,
            tp.DataPublicacao,
            tp.DataValidade,
            tp.DataAtualizacao,
            tp.BitAtivo,
            tp.AlteradoPor,
            tp.ValorTroca
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] tp
        WHERE tp.IDDimTabelaPrecosEuromidia = :id_preco
          AND TRY_CONVERT(int, tp.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
          AND UPPER(LTRIM(RTRIM(ISNULL(tp.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
          AND (
                :id_dim_face IS NULL
                OR tp.IDDimFacesPaineis IS NULL
                OR TRY_CONVERT(int, tp.IDDimFacesPaineis) = TRY_CONVERT(int, :id_dim_face)
              )
        ORDER BY tp.IDDimTabelaPrecosEuromidia DESC;
    """)
    row = db.session.execute(
        sql,
        {
            "id_preco": int(id_preco),
            "id_painel": int(id_painel),
            "id_dim_face": int(id_dim_face) if id_dim_face else None,
            "tipo_painel": _normalizar_texto(tipo_painel),
        },
    ).mappings().first()
    return dict(row) if row else None

















def _primeiro_valor_preenchido_local(*valores: Any) -> Any:
    """
    Eu retorno o primeiro valor realmente preenchido.

    Uso isso para aceitar payloads vindos com nomes diferentes:
    - id_preco
    - IDDimTabelaPrecosEuromidia
    - id_tabela_preco
    - ValorTabela
    - valor_tabela
    """
    for valor in valores:
        if valor is None:
            continue

        if isinstance(valor, str) and not valor.strip():
            continue

        return valor

    return None


def _resolver_face_unica_do_painel(id_painel: int | None) -> dict[str, Any] | None:
    """
    Eu tento resolver uma face quando o card salvou IDDimFacesPaineis como NULL.

    Regra:
    - se o painel tiver exatamente 1 face cadastrada, posso preencher com segurança;
    - se tiver mais de 1 face, não escolho no chute, porque isso poderia aprovar preço na face errada.
    """
    if id_painel in (None, "", 0):
        return None

    sql = text("""
        ;WITH Faces AS (
            SELECT
                f.IDDimFacesPaineis,
                f.IDDimPaineisEuromidia,
                f.CodPonto,
                f.Face,
                f.CodFace,
                f.Tipo,
                COUNT(1) OVER () AS QuantidadeFaces
            FROM [Integracao].[Silver].[DimFacesPaineis] f
            WHERE TRY_CONVERT(int, f.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
        )
        SELECT TOP (1)
            IDDimFacesPaineis,
            IDDimPaineisEuromidia,
            CodPonto,
            Face,
            CodFace,
            Tipo
        FROM Faces
        WHERE QuantidadeFaces = 1
        ORDER BY IDDimFacesPaineis DESC;
    """)

    row = db.session.execute(
        sql,
        {"id_painel": int(id_painel)},
    ).mappings().first()

    return dict(row) if row else None


def _obter_preco_padrao_painel_face(
    *,
    id_painel: int | None,
    id_face: int | None,
    tipo_painel: str | None,
    id_preco: int | None = None,
) -> dict[str, Any] | None:
    """
    Eu resolvo o preço de tabela do painel/face.

    Ordem:
    1) se veio IDDimTabelaPrecosEuromidia, valido e uso esse preço;
    2) se não veio, busco o preço mais recente/ativo em FatoTabelaPrecosEuromidia;
    3) se não existir preço cadastrado, retorno None para o fluxo usar preço praticado.
    """
    if id_painel in (None, "", 0):
        return None

    tipo_painel_txt = _normalizar_texto(tipo_painel)

    if not tipo_painel_txt:
        painel = _obter_painel_por_id(int(id_painel))
        tipo_painel_txt = _normalizar_texto((painel or {}).get("Tipo"))

    if not tipo_painel_txt:
        return None

    if id_preco not in (None, "", 0):
        preco_por_id = _obter_preco_por_id(
            id_preco=int(id_preco),
            id_painel=int(id_painel),
            id_dim_face=int(id_face) if id_face not in (None, "", 0) else None,
            tipo_painel=tipo_painel_txt,
        )

        if preco_por_id:
            return preco_por_id

    precos = _obter_precos_painel_face(
        id_painel=int(id_painel),
        id_dim_face=int(id_face) if id_face not in (None, "", 0) else None,
        tipo_painel=tipo_painel_txt,
    )

    return dict(precos[0]) if precos else None


def _buscar_preco_praticado_base_card_painel_face(
    *,
    id_card: int | None,
    id_painel: int | None,
    id_face: int | None,
    id_contrato: int | None = None,
) -> Decimal | None:
    """
    Eu busco o preço praticado quando não existe preço cadastrado em FatoTabelaPrecosEuromidia.

    Regra:
    - primeiro tento FatoContratoItemPrecoPraticadoEuromidia;
    - priorizo PrecoPraticado;
    - depois PrecoProposto;
    - depois qualquer preço aprovado/proposto que existir.
    """
    if not _objeto_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO):
        return None

    filtros = []
    params: dict[str, Any] = {}

    if id_card not in (None, "", 0) and _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "IDFatoKanbanCard"):
        filtros.append("TRY_CONVERT(int, IDFatoKanbanCard) = TRY_CONVERT(int, :id_card)")
        params["id_card"] = int(id_card)

    if id_painel not in (None, "", 0) and _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "IDDimPaineisEuromidia"):
        filtros.append("TRY_CONVERT(int, IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)")
        params["id_painel"] = int(id_painel)

    if id_face not in (None, "", 0) and _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "IDDimFacesPaineis"):
        filtros.append("TRY_CONVERT(int, IDDimFacesPaineis) = TRY_CONVERT(int, :id_face)")
        params["id_face"] = int(id_face)

    if id_contrato not in (None, "", 0) and _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "IDFatoControleContratosEuromidia"):
        filtros.append("TRY_CONVERT(int, IDFatoControleContratosEuromidia) = TRY_CONVERT(int, :id_contrato)")
        params["id_contrato"] = int(id_contrato)

    if not filtros:
        return None

    coluna_ordem = COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO

    if not _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, coluna_ordem):
        return None

    ordem_data = "DataCadastro DESC," if _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "DataCadastro") else ""

    sql = text(f"""
        SELECT TOP (1)
            *
        FROM {TABELA_CONTRATO_ITEM_PRECO_PRATICADO}
        WHERE {' AND '.join(filtros)}
        ORDER BY
            {ordem_data}
            [{coluna_ordem}] DESC;
    """)

    row = db.session.execute(sql, params).mappings().first()

    if not row:
        return None

    dados = dict(row)

    for campo in (
        "PrecoPraticado",
        "PrecoAtual",
        "PrecoAprovado",
        "PrecoProposto",
        "ValorVendaFinal",
        "NovoValor",
        "ValorTabela",
    ):
        valor = _valor_decimal(dados.get(campo))
        if valor is not None and valor > 0:
            return valor

    return None


def _calcular_percentual_desconto_seguro(
    *,
    valor_tabela: Any,
    valor_final: Any,
) -> Decimal | None:
    """
    Eu calculo o percentual de desconto.

    Fórmula em texto:
    desconto = (valor_tabela - valor_final) / valor_tabela * 100

    Exemplo:
    valor_tabela = 7.000
    valor_final = 5.950

    desconto = (7.000 - 5.950) / 7.000 * 100
    desconto = 1.050 / 7.000 * 100
    desconto = 15%
    """
    valor_tabela_dec = _valor_decimal(valor_tabela)
    valor_final_dec = _valor_decimal(valor_final)

    if valor_tabela_dec is None or valor_tabela_dec <= 0:
        return None

    if valor_final_dec is None:
        return None

    desconto = ((valor_tabela_dec - valor_final_dec) / valor_tabela_dec) * Decimal("100")

    if desconto < 0:
        return Decimal("0")

    return desconto















def _buscar_snapshot_preco_praticado_por_item_contrato(
    id_item_contrato: int | None,
) -> dict[str, Any] | None:
    if id_item_contrato in (None, "", 0):
        return None

    if not _objeto_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO):
        return None

    if not _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "IDFatoControleContratosItensEuromidia"):
        return None

    if not _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO):
        return None

    ordem_data = "DataCadastro DESC," if _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "DataCadastro") else ""

    sql = text(
        f"""
        SELECT TOP (1)
            *
        FROM {TABELA_CONTRATO_ITEM_PRECO_PRATICADO}
        WHERE IDFatoControleContratosItensEuromidia = :id_item_contrato
        ORDER BY
            {ordem_data}
            {COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO} DESC;
        """
    )

    row = db.session.execute(sql, {"id_item_contrato": int(id_item_contrato)}).mappings().first()
    return dict(row) if row else None



def _obter_estado_atual_negociacao_por_painel_face(
    *,
    id_card: int,
    id_painel: int | None,
    id_face: int | None,
) -> dict[str, Any] | None:
    if id_card in (None, "", 0) or id_painel in (None, "", 0) or id_face in (None, "", 0):
        return None

    for estado in _listar_estado_atual_negociacao_card(int(id_card)):
        try:
            id_painel_estado = int(estado.get("IDDimPaineisEuromidia") or 0)
            id_face_estado = int(estado.get("IDDimFacesPaineis") or 0)
        except Exception:
            continue

        if id_painel_estado == int(id_painel) and id_face_estado == int(id_face):
            return dict(estado)

    return None



def _obter_ultima_negociacao_preco_card_painel_face(
    *,
    id_card: int,
    id_painel: int | None,
    id_face: int | None,
    id_empresa_proprietaria: int,
) -> dict[str, Any] | None:
    if id_card in (None, "", 0) or id_painel in (None, "", 0) or id_face in (None, "", 0):
        return None

    if not _objeto_existe(TABELA_CARD_NEGOCIACAO_PRECO):
        return None

    sql = text(
        f"""
        SELECT TOP (1)
            *
        FROM {TABELA_CARD_NEGOCIACAO_PRECO}
        WHERE IDFatoKanbanCard = :id_card
          AND IDEmpresaProprietaria = :id_empresa_proprietaria
          AND TRY_CONVERT(int, IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
          AND TRY_CONVERT(int, IDDimFacesPaineis) = TRY_CONVERT(int, :id_face)
        ORDER BY
            COALESCE(DataAprovacaoPreco, DataPrecoProposto, PeriodoInicio, PeriodoTermino) DESC,
            IDFatoKanbanNegociacaoPreco DESC;
        """
    )

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_painel": int(id_painel),
            "id_face": int(id_face),
        },
    ).mappings().first()

    return dict(row) if row else None



def _coalescer_primeiro_valor(*valores: Any) -> Any:
    for valor in valores:
        if valor not in (None, ""):
            return valor
    return None





def _montar_payload_snapshot_preco_praticado(
    *,
    id_card: int,
    id_empresa_proprietaria: int,
    id_usuario_evento: int | None = None,
    id_usuario_autorizacao_preco: int | None = None,
    id_usuario_aprovacao_contrato: int | None = None,
    marcar_data_aprovacao_contrato: bool = False,
    negociacao_base: dict[str, Any] | None = None,
    preco_praticado_override: Any = None,
    desconto_percentual_override: Any = None,
    margem_percentual_override: Any = None,
) -> dict[str, Any]:
    if not _objeto_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO):
        return {"ok": False, "motivo": "tabela_preco_praticado_ausente"}

    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe.get("card"), dict) else {}
    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0)

    sql_vinculo = text("""
        SELECT TOP 1
            pf.IDFatoKanbanCardPainelFace,
            pf.IDFatoKanbanCard,
            pf.Ordem,
            pf.IDDimPaineisEuromidia,
            pf.IDDimFacesPaineis,
            pf.CodPonto,
            pf.CodFace,
            pf.TipoPainel,
            pf.AnoCusto,
            pf.CustoTabela,
            pf.IDDimTabelaPrecosEuromidia,
            pf.PeriodoExibicao,
            pf.ExibicoesDia,
            pf.ValorTabela,
            pf.Tabela,
            pf.PoliticaTrocas,
            pf.ValorTroca,
            pf.NovoValor,
            pf.PercentualDesconto,
            pf.ValorVendaFinal,
            pf.MargemValor,
            pf.MargemPercentual,
            pf.DataInicio,
            pf.DataFim
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
        WHERE pf.IDFatoKanbanCard = :id_card
          AND ISNULL(pf.Ativo, 1) = 1
        ORDER BY
            ISNULL(pf.Ordem, 0),
            pf.IDFatoKanbanCardPainelFace;
    """)

    vinculo = db.session.execute(sql_vinculo, {"id_card": int(id_card)}).mappings().first()
    if not vinculo:
        return {
            "ok": False,
            "motivo": "card_sem_vinculos_ativos_em_fato_kanban_card_painel_face",
            "id_fase_atual": int(id_fase_atual),
            "id_card": int(id_card),
        }

    vinculo = dict(vinculo)

    id_painel = int(vinculo.get("IDDimPaineisEuromidia") or 0) or None
    id_face = int(vinculo.get("IDDimFacesPaineis") or 0) or None
    cod_ponto_item = _normalizar_texto(vinculo.get("CodPonto"))
    cod_face_item = _normalizar_texto(vinculo.get("CodFace")).upper()

    painel = _obter_painel_por_id(int(id_painel)) if id_painel else None
    if not painel and cod_ponto_item:
        painel = _obter_painel_por_codponto(cod_ponto_item)

    face = _obter_face_por_id(int(id_face)) if id_face else None
    if not face and painel and cod_face_item:
        face = _resolver_face_do_painel(int(painel.get("IDDimPaineisEuromidia") or 0), cod_face_item)
    if not face and cod_ponto_item and cod_face_item:
        face = _obter_face_por_codponto_codface(cod_ponto_item, cod_face_item)

    if not painel and face and face.get("IDDimPaineisEuromidia") not in (None, "", 0):
        painel = _obter_painel_por_id(int(face.get("IDDimPaineisEuromidia")))

    id_painel = int(
        (painel or {}).get("IDDimPaineisEuromidia")
        or vinculo.get("IDDimPaineisEuromidia")
        or 0
    ) or None
    id_face = int(
        (face or {}).get("IDDimFacesPaineis")
        or vinculo.get("IDDimFacesPaineis")
        or 0
    ) or None

    if not id_painel or not id_face:
        return {
            "ok": False,
            "motivo": "vinculo_card_sem_ids_resolvidos",
            "id_fase_atual": int(id_fase_atual),
            "id_card": int(id_card),
            "id_fato_kanban_card_painel_face": int(vinculo.get("IDFatoKanbanCardPainelFace") or 0) or None,
            "id_painel": id_painel,
            "id_face": id_face,
            "cod_ponto": cod_ponto_item or None,
            "cod_face": cod_face_item or None,
        }

    id_empresa_relacionada = _obter_id_empresa_relacionada_card(card)
    id_contrato_existente = _coalescer_primeiro_valor(
        card.get("IDFatoControleContratosEuromidia"),
        card.get("IDFatoControleContratoEuromidia"),
    )

    relacionamento = _obter_relacionamento_empresa_proprietaria(
        id_empresa=int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )

    estado_atual = _obter_estado_atual_negociacao_por_painel_face(
        id_card=int(id_card),
        id_painel=int(id_painel),
        id_face=int(id_face),
    )

    negociacao = negociacao_base or _obter_ultima_negociacao_preco_card_painel_face(
        id_card=int(id_card),
        id_painel=int(id_painel),
        id_face=int(id_face),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )

    id_tabela_preco = _coalescer_primeiro_valor(
        vinculo.get("IDDimTabelaPrecosEuromidia"),
        estado_atual.get("IDDimTabelaPrecosEuromidia") if estado_atual else None,
        negociacao.get("IDDimTabelaPrecosEuromidia") if negociacao else None,
    )

    tipo_painel = _normalizar_texto(
        vinculo.get("TipoPainel")
        or (painel or {}).get("Tipo")
        or (face or {}).get("Tipo")
    )

    preco_tabela = None
    if id_tabela_preco not in (None, "", 0) and tipo_painel:
        try:
            preco_tabela = _obter_preco_por_id(
                int(id_tabela_preco),
                int(id_painel),
                int(id_face),
                str(tipo_painel),
            )
        except Exception:
            preco_tabela = None

    custo_painel = _valor_decimal(vinculo.get("CustoTabela"))
    if custo_painel is None and cod_ponto_item not in (None, ""):
        try:
            custo_ref = _obter_custo_por_codponto(int(cod_ponto_item))
        except Exception:
            custo_ref = None
        custo_painel = _valor_decimal((custo_ref or {}).get("Valor") if isinstance(custo_ref, dict) else custo_ref)

    preco_cheio_tabela = _valor_decimal(vinculo.get("ValorTabela"))
    if preco_cheio_tabela is None:
        preco_cheio_tabela = _valor_decimal((preco_tabela or {}).get("Valor") if isinstance(preco_tabela, dict) else None)

    preco_proposto = _valor_decimal(vinculo.get("NovoValor"))
    if preco_proposto is None:
        preco_proposto = _valor_decimal(negociacao.get("PrecoProposto") if negociacao else None)
    if preco_proposto is None:
        preco_proposto = _valor_decimal(vinculo.get("ValorVendaFinal"))
    if preco_proposto is None:
        preco_proposto = preco_cheio_tabela

    preco_praticado = _valor_decimal(preco_praticado_override)
    if preco_praticado is None:
        preco_praticado = _valor_decimal(vinculo.get("ValorVendaFinal"))
    if preco_praticado is None:
        preco_praticado = _valor_decimal(estado_atual.get("PrecoVendaAtualContrato") if estado_atual else None)
    if preco_praticado is None:
        preco_praticado = _valor_decimal(negociacao.get("PrecoAprovado") if negociacao else None)
    if preco_praticado is None:
        preco_praticado = _valor_decimal(negociacao.get("PrecoProposto") if negociacao else None)
    if preco_praticado is None:
        preco_praticado = _valor_decimal(vinculo.get("NovoValor"))
    if preco_praticado is None:
        preco_praticado = preco_cheio_tabela

    desconto_percentual = _valor_decimal(desconto_percentual_override)
    if desconto_percentual is None:
        desconto_percentual = _valor_decimal(vinculo.get("PercentualDesconto"))
    if desconto_percentual is None:
        desconto_percentual = _valor_decimal(negociacao.get("DescontoAprovado") if negociacao else None)
    if desconto_percentual is None:
        desconto_percentual = _valor_decimal(negociacao.get("DescontoProposto") if negociacao else None)

    margem_percentual = _valor_decimal(margem_percentual_override)
    if margem_percentual is None:
        margem_percentual = _valor_decimal(vinculo.get("MargemPercentual"))
    if margem_percentual is None and preco_praticado not in (None, Decimal("0")) and custo_painel is not None:
        try:
            margem_percentual = ((preco_praticado - custo_painel) / preco_praticado) * Decimal("100")
        except Exception:
            margem_percentual = None

    data_inicio = _coalescer_primeiro_valor(
        vinculo.get("DataInicio"),
        negociacao.get("PeriodoInicio") if negociacao else None,
    )
    data_termino = _coalescer_primeiro_valor(
        vinculo.get("DataFim"),
        negociacao.get("PeriodoTermino") if negociacao else None,
    )

    payload = {
        "IDEmpresa": int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
        "DimRelacionamentoEmpresa": int(relacionamento.get("DimRelacionamentoEmpresa") or 0) if relacionamento else None,
        "IDFatoKanbanCard": int(id_card),
        "IDDimUsuarios": int(id_usuario_evento) if id_usuario_evento not in (None, "", 0) else None,
        "IDDimUsuariosAutorizacaoPreco": (
            int(id_usuario_autorizacao_preco)
            if id_usuario_autorizacao_preco not in (None, "", 0)
            else (int(negociacao.get("IDDimUsuariosAprovacaoPreco") or 0) or None if negociacao else None)
        ),
        "IDDimUsuariosAprovacaoContrato": int(id_usuario_aprovacao_contrato) if id_usuario_aprovacao_contrato not in (None, "", 0) else None,
        "IDFatoControleContratosEuromidia": int(id_contrato_existente or 0) or None,
        "IDFatoControleContratosItensEuromidia": None,
        "IDDimPaineisEuromidia": int(id_painel),
        "IDDimFacesPaineis": int(id_face),
        "IDDimTabelaPrecosEuromidia": int(id_tabela_preco) if id_tabela_preco not in (None, "", 0) else None,
        "IDFatoKanbanNegociacaoPreco": int(negociacao.get("IDFatoKanbanNegociacaoPreco") or 0) if negociacao else None,
        "Exibicoes": int(
            _coalescer_primeiro_valor(
                vinculo.get("ExibicoesDia"),
                (preco_tabela or {}).get("ExibicoesDia") if isinstance(preco_tabela, dict) else None,
            ) or 0
        ) or None,
        "CustoPainel": custo_painel,
        "PrecoProposto": preco_proposto,
        "CustoMedioPainel": custo_painel,
        "PrecoPraticado": preco_praticado,
        "DescontoPercentual": desconto_percentual,
        "MargemPercentual": margem_percentual,
        "DataInicio": data_inicio,
        "DataTermino": data_termino,
    }

    return {
        "ok": True,
        "motivo": "payload_snapshot_preco_praticado_montado_a_partir_do_card_painel_face",
        "id_fase_atual": int(id_fase_atual),
        "payload": payload,
        "marcar_data_aprovacao_contrato": bool(marcar_data_aprovacao_contrato),
        "vinculo_card": vinculo,
        "estado_atual": estado_atual,
        "negociacao": negociacao,
    }











def _upsert_snapshot_preco_praticado(
    *,
    payload_snapshot: dict[str, Any],
    marcar_data_aprovacao_contrato: bool = False,
) -> dict[str, Any]:
    if not _objeto_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO):
        return {"ok": False, "motivo": "tabela_preco_praticado_ausente"}

    id_item_contrato = payload_snapshot.get("IDFatoControleContratosItensEuromidia")
    id_card = payload_snapshot.get("IDFatoKanbanCard")
    id_painel = payload_snapshot.get("IDDimPaineisEuromidia")
    id_face = payload_snapshot.get("IDDimFacesPaineis")
    id_contrato = payload_snapshot.get("IDFatoControleContratosEuromidia")

    registro_atual = _buscar_snapshot_preco_praticado_para_upsert(
        id_item_contrato=int(id_item_contrato) if id_item_contrato not in (None, "", 0) else None,
        id_card=int(id_card) if id_card not in (None, "", 0) else None,
        id_painel=int(id_painel) if id_painel not in (None, "", 0) else None,
        id_face=int(id_face) if id_face not in (None, "", 0) else None,
        id_contrato=int(id_contrato) if id_contrato not in (None, "", 0) else None,
    )

    colunas_validas: dict[str, Any] = {}
    for coluna, valor_novo in payload_snapshot.items():
        if not _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, coluna):
            continue

        valor_final = valor_novo
        if valor_final is None and registro_atual is not None:
            valor_final = registro_atual.get(coluna)

        colunas_validas[coluna] = valor_final

    if registro_atual and registro_atual.get(COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO) not in (None, "", 0):
        id_registro = int(registro_atual.get(COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO) or 0)

        sets: list[str] = []
        params_update: dict[str, Any] = {
            "id_registro": id_registro,
        }

        for coluna, valor in colunas_validas.items():
            if coluna == COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO:
                continue
            sets.append(f"[{coluna}] = :{coluna}")
            params_update[coluna] = valor

        if _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "DataCadastro"):
            sets.append("[DataCadastro] = GETDATE()")

        if marcar_data_aprovacao_contrato and _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "DataAprovacaoContrato"):
            sets.append("[DataAprovacaoContrato] = GETDATE()")

        if not sets:
            return {"ok": False, "motivo": "nenhuma_coluna_para_update_preco_praticado"}

        sql_update = text(
            f"""
            UPDATE {TABELA_CONTRATO_ITEM_PRECO_PRATICADO}
               SET {', '.join(sets)}
             WHERE [{COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO}] = :id_registro;
            """
        )
        result = db.session.execute(sql_update, params_update)

        return {
            "ok": True,
            "motivo": "snapshot_preco_praticado_atualizado",
            "acao": "update",
            "id_registro": id_registro,
            "linhas_afetadas": int(result.rowcount or 0),
            "id_item_contrato": int(id_item_contrato) if id_item_contrato not in (None, "", 0) else None,
        }

    colunas_insert: list[str] = []
    valores_insert: list[str] = []
    params_insert: dict[str, Any] = {}

    for coluna, valor in colunas_validas.items():
        if valor is None:
            continue
        colunas_insert.append(f"[{coluna}]")
        valores_insert.append(f":{coluna}")
        params_insert[coluna] = valor

    if _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "DataCadastro") and "[DataCadastro]" not in colunas_insert:
        colunas_insert.append("[DataCadastro]")
        valores_insert.append("GETDATE()")

    if (
        marcar_data_aprovacao_contrato
        and _coluna_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO, "DataAprovacaoContrato")
        and "[DataAprovacaoContrato]" not in colunas_insert
    ):
        colunas_insert.append("[DataAprovacaoContrato]")
        valores_insert.append("GETDATE()")

    if not colunas_insert:
        return {"ok": False, "motivo": "nenhuma_coluna_para_insert_preco_praticado"}

    sql_insert = text(
        f"""
        INSERT INTO {TABELA_CONTRATO_ITEM_PRECO_PRATICADO}
        (
            {', '.join(colunas_insert)}
        )
        OUTPUT INSERTED.[{COLUNA_ID_CONTRATO_ITEM_PRECO_PRATICADO}]
        VALUES
        (
            {', '.join(valores_insert)}
        );
        """
    )

    novo_id = db.session.execute(sql_insert, params_insert).scalar()
    return {
        "ok": True,
        "motivo": "snapshot_preco_praticado_inserido",
        "acao": "insert",
        "id_registro": int(novo_id) if novo_id not in (None, "") else None,
        "linhas_afetadas": 1,
        "id_item_contrato": int(id_item_contrato) if id_item_contrato not in (None, "", 0) else None,
    }







def _sincronizar_snapshot_preco_praticado_fase_4(
    *,
    id_card: int,
    id_usuario: int,
    id_empresa_proprietaria: int,
) -> dict[str, Any]:
    if not _objeto_existe(TABELA_CONTRATO_ITEM_PRECO_PRATICADO):
        return {"ok": False, "motivo": "tabela_preco_praticado_ausente"}

    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe.get("card"), dict) else {}
    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0)

    if id_fase_atual != 4:
        return {
            "ok": False,
            "motivo": "fase_diferente_de_4",
            "id_fase_atual": int(id_fase_atual),
        }

    sql_vinculos = text("""
        SELECT
            pf.IDFatoKanbanCardPainelFace,
            pf.IDFatoKanbanCard,
            pf.Ordem,
            pf.IDDimPaineisEuromidia,
            pf.IDDimFacesPaineis,
            pf.CodPonto,
            pf.CodFace,
            pf.TipoPainel,
            pf.AnoCusto,
            pf.CustoTabela,
            pf.IDDimTabelaPrecosEuromidia,
            pf.PeriodoExibicao,
            pf.ExibicoesDia,
            pf.ValorTabela,
            pf.Tabela,
            pf.PoliticaTrocas,
            pf.ValorTroca,
            pf.NovoValor,
            pf.PercentualDesconto,
            pf.ValorVendaFinal,
            pf.MargemValor,
            pf.MargemPercentual,
            pf.DataInicio,
            pf.DataFim
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
        WHERE pf.IDFatoKanbanCard = :id_card
          AND ISNULL(pf.Ativo, 1) = 1
        ORDER BY
            ISNULL(pf.Ordem, 0),
            pf.IDFatoKanbanCardPainelFace;
    """)

    vinculos = db.session.execute(sql_vinculos, {"id_card": int(id_card)}).mappings().all()
    if not vinculos:
        return {
            "ok": False,
            "motivo": "card_sem_vinculos_ativos_em_fato_kanban_card_painel_face",
            "id_fase_atual": int(id_fase_atual),
            "id_card": int(id_card),
            "total_vinculos": 0,
        }

    detalhe_resultados: list[dict[str, Any]] = []
    total_processados = 0
    total_erros = 0

    for vinculo in vinculos:
        vinculo = dict(vinculo)

        id_painel = int(vinculo.get("IDDimPaineisEuromidia") or 0) or None
        id_face = int(vinculo.get("IDDimFacesPaineis") or 0) or None
        cod_ponto = _normalizar_texto(vinculo.get("CodPonto"))
        cod_face = _normalizar_texto(vinculo.get("CodFace")).upper()

        painel = _obter_painel_por_id(int(id_painel)) if id_painel else None
        if not painel and cod_ponto:
            painel = _obter_painel_por_codponto(cod_ponto)

        face = _obter_face_por_id(int(id_face)) if id_face else None
        if not face and painel and cod_face:
            face = _resolver_face_do_painel(int(painel.get("IDDimPaineisEuromidia") or 0), cod_face)
        if not face and cod_ponto and cod_face:
            face = _obter_face_por_codponto_codface(cod_ponto, cod_face)

        vinculo["IDDimPaineisEuromidia"] = int(
            (painel or {}).get("IDDimPaineisEuromidia")
            or vinculo.get("IDDimPaineisEuromidia")
            or 0
        ) or None
        vinculo["IDDimFacesPaineis"] = int(
            (face or {}).get("IDDimFacesPaineis")
            or vinculo.get("IDDimFacesPaineis")
            or 0
        ) or None
        vinculo["CodPonto"] = _normalizar_texto(
            (painel or {}).get("CodPonto")
            or (face or {}).get("CodPonto")
            or vinculo.get("CodPonto")
        )
        vinculo["CodFace"] = _normalizar_texto(
            (face or {}).get("CodFace")
            or vinculo.get("CodFace")
        ).upper()

        contexto = _montar_payload_snapshot_preco_praticado(
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_usuario_evento=int(id_usuario),
        )

        if not contexto.get("ok"):
            total_erros += 1
            detalhe_resultados.append(
                {
                    "ok": False,
                    "motivo": contexto.get("motivo"),
                    "id_fato_kanban_card_painel_face": int(vinculo.get("IDFatoKanbanCardPainelFace") or 0) or None,
                    "id_painel": int(vinculo.get("IDDimPaineisEuromidia") or 0) or None,
                    "id_face": int(vinculo.get("IDDimFacesPaineis") or 0) or None,
                    "cod_ponto": vinculo.get("CodPonto"),
                    "cod_face": vinculo.get("CodFace"),
                }
            )
            continue

        payload = dict(contexto.get("payload") or {})
        payload["IDDimPaineisEuromidia"] = int(vinculo.get("IDDimPaineisEuromidia") or 0) or None
        payload["IDDimFacesPaineis"] = int(vinculo.get("IDDimFacesPaineis") or 0) or None
        payload["IDDimTabelaPrecosEuromidia"] = int(vinculo.get("IDDimTabelaPrecosEuromidia") or 0) or None
        payload["Exibicoes"] = int(vinculo.get("ExibicoesDia") or 0) or None
        payload["CustoPainel"] = _valor_decimal(vinculo.get("CustoTabela"))
        payload["PrecoProposto"] = (
            _valor_decimal(vinculo.get("NovoValor"))
            or _valor_decimal(vinculo.get("ValorVendaFinal"))
            or payload.get("PrecoProposto")
        )
        payload["CustoMedioPainel"] = _valor_decimal(vinculo.get("CustoTabela"))
        payload["PrecoPraticado"] = (
            _valor_decimal(vinculo.get("ValorVendaFinal"))
            or _valor_decimal(vinculo.get("NovoValor"))
            or payload.get("PrecoPraticado")
        )
        payload["DescontoPercentual"] = (
            _valor_decimal(vinculo.get("PercentualDesconto"))
            if vinculo.get("PercentualDesconto") not in (None, "")
            else payload.get("DescontoPercentual")
        )
        payload["MargemPercentual"] = (
            _valor_decimal(vinculo.get("MargemPercentual"))
            if vinculo.get("MargemPercentual") not in (None, "")
            else payload.get("MargemPercentual")
        )
        payload["DataInicio"] = vinculo.get("DataInicio") or payload.get("DataInicio")
        payload["DataTermino"] = vinculo.get("DataFim") or payload.get("DataTermino")
        payload["IDFatoControleContratosItensEuromidia"] = None

        resultado_upsert = _upsert_snapshot_preco_praticado(
            payload_snapshot=payload,
            marcar_data_aprovacao_contrato=False,
        )

        if resultado_upsert.get("ok"):
            total_processados += 1
        else:
            total_erros += 1

        detalhe_resultados.append(
            {
                **resultado_upsert,
                "id_fato_kanban_card_painel_face": int(vinculo.get("IDFatoKanbanCardPainelFace") or 0) or None,
                "id_painel": int(vinculo.get("IDDimPaineisEuromidia") or 0) or None,
                "id_face": int(vinculo.get("IDDimFacesPaineis") or 0) or None,
                "cod_ponto": vinculo.get("CodPonto"),
                "cod_face": vinculo.get("CodFace"),
            }
        )

    return {
        "ok": total_processados > 0 and total_erros == 0,
        "motivo": (
            "snapshot_preco_praticado_sincronizado_fase_4"
            if total_processados > 0 and total_erros == 0
            else "snapshot_preco_praticado_sincronizado_parcial_fase_4"
            if total_processados > 0
            else "snapshot_preco_praticado_nao_sincronizado_fase_4"
        ),
        "id_fase_atual": int(id_fase_atual),
        "id_card": int(id_card),
        "total_vinculos": len(vinculos),
        "total_processados": int(total_processados),
        "total_erros": int(total_erros),
        "detalhes": detalhe_resultados,
    }



def _sincronizar_aprovacao_preco_no_snapshot_preco_praticado(
    *,
    id_card: int,
    id_usuario_aprovacao: int,
    id_empresa_proprietaria: int,
    negociacao: dict[str, Any],
    preco_aprovado: Decimal,
    desconto_aprovado_percentual: Decimal | None,
    margem_percentual: Decimal | None,
) -> dict[str, Any]:
    contexto = _montar_payload_snapshot_preco_praticado(
        id_card=int(id_card),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
        id_usuario_evento=int(id_usuario_aprovacao),
        id_usuario_autorizacao_preco=int(id_usuario_aprovacao),
        negociacao_base=negociacao,
        preco_praticado_override=preco_aprovado,
        desconto_percentual_override=desconto_aprovado_percentual,
        margem_percentual_override=margem_percentual,
    )

    if not contexto.get("ok"):
        return contexto

    return _upsert_snapshot_preco_praticado(
        payload_snapshot=contexto.get("payload") or {},
        marcar_data_aprovacao_contrato=False,
    )





def _sincronizar_aprovacao_contrato_no_snapshot_preco_praticado(
    *,
    id_card: int,
    id_usuario_aprovacao: int,
    id_empresa_proprietaria: int,
) -> dict[str, Any]:
    contexto = _montar_payload_snapshot_preco_praticado(
        id_card=int(id_card),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
        id_usuario_evento=int(id_usuario_aprovacao),
        id_usuario_aprovacao_contrato=int(id_usuario_aprovacao),
        marcar_data_aprovacao_contrato=True,
    )

    if not contexto.get("ok"):
        return contexto

    return _upsert_snapshot_preco_praticado(
        payload_snapshot=contexto.get("payload") or {},
        marcar_data_aprovacao_contrato=True,
    )



def _calcular_margens_comerciais(
    custo: Any,
    valor_tabela: Any,
    novo_valor: Any,
    percentual_desconto: Any,
    valor_base_referencia: Any = None,
) -> dict[str, Any]:
    custo_dec = _valor_decimal(custo) or Decimal('0')
    valor_tabela_dec = _valor_decimal(valor_tabela)
    valor_base_dec = _valor_decimal(valor_base_referencia)
    if valor_base_dec is None:
        valor_base_dec = valor_tabela_dec

    novo_valor_dec = _valor_decimal(novo_valor)
    percentual_dec = _valor_decimal(percentual_desconto)

    valor_final: Decimal | None = None
    percentual_aplicado: Decimal | None = None

    if novo_valor_dec is not None:
        valor_final = novo_valor_dec
    elif percentual_dec is not None and valor_base_dec is not None:
        percentual_aplicado = percentual_dec
        valor_final = valor_base_dec * (Decimal('1') - (percentual_dec / Decimal('100')))
    else:
        valor_final = valor_base_dec

    margem_valor: Decimal | None = None
    margem_percentual: Decimal | None = None
    if valor_final is not None:
        margem_valor = valor_final - custo_dec
        if valor_final != 0:
            margem_percentual = (margem_valor / valor_final) * Decimal('100')

    return {
        "Custo": float(custo_dec) if custo is not None else None,
        "ValorTabela": float(valor_tabela_dec) if valor_tabela_dec is not None else None,
        "ValorBaseReferencia": float(valor_base_dec) if valor_base_dec is not None else None,
        "NovoValor": float(novo_valor_dec) if novo_valor_dec is not None else None,
        "PercentualDesconto": float(percentual_aplicado if percentual_aplicado is not None else percentual_dec) if (percentual_aplicado is not None or percentual_dec is not None) else None,
        "ValorVendaFinal": float(valor_final) if valor_final is not None else None,
        "MargemValor": float(margem_valor) if margem_valor is not None else None,
        "MargemPercentual": float(margem_percentual) if margem_percentual is not None else None,
    }








def _normalizar_data_reserva_kanban(valor: Any) -> date | None:
    if valor is None:
        return None

    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor

    if isinstance(valor, datetime):
        return valor.date()

    texto = str(valor).strip()
    if not texto:
        return None

    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except Exception:
            pass

    return None


def _obter_vendedor_logado_reserva_kanban(id_empresa_proprietaria: int) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            v.IDVendedor,
            v.NomeVendedor
        FROM [Integracao].[dbo].[Vendedores] v
        INNER JOIN [Integracao].[Silver].[DimUsuarios] u
            ON u.IDDimUsuarios = v.IDDimUsuarios
           AND COALESCE(u.IDEmpresaProprietaria, 0) = COALESCE(v.IDEmpresaProprietaria, 0)
        WHERE u.IDDimUsuarios = :id_usuario
          AND COALESCE(u.BitAtivo, 1) = 1
          AND COALESCE(v.BitAtivo, 1) = 1
          AND COALESCE(v.IDEmpresaProprietaria, 0) = :id_empresa
        ORDER BY v.IDVendedor
    """)

    row = db.session.execute(
        sql,
        {
            "id_usuario": int(_id_usuario()),
            "id_empresa": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    return dict(row) if row else None


def _obter_razao_social_empresa_reserva_kanban(id_empresa_relacionada: Any) -> str:
    try:
        id_empresa_int = int(id_empresa_relacionada or 0)
    except Exception:
        id_empresa_int = 0

    if not id_empresa_int:
        return ""

    sql = text("""
        SELECT TOP 1
            RazaoSocial
        FROM [Integracao].[Silver].[DimEmpresas]
        WHERE IDEmpresa = :id_empresa
    """)
    valor = db.session.execute(sql, {"id_empresa": int(id_empresa_int)}).scalar()
    return str(valor or "").strip()


ORIGEM_RESERVA_CARD_KANBAN = "KANBAN"


def _marcador_reserva_card_kanban(id_card: int, cod_face: str) -> str:
    return f"[CARD_ID={int(id_card)}][COD_FACE={str(cod_face or '').strip().upper()}]"


def _marcador_reserva_card_kanban_legacy(id_card: int, cod_face: str) -> str:
    return f"[KANBAN_CARD={int(id_card)}][COD_FACE={str(cod_face or '').strip().upper()}]"


def _obter_chave_reserva_card_kanban(cod_face: Any, data_inicio: Any, data_fim: Any) -> tuple[str, date | None, date | None]:
    return (
        str(cod_face or "").strip().upper(),
        _normalizar_data_reserva_kanban(data_inicio),
        _normalizar_data_reserva_kanban(data_fim),
    )


def _listar_reservas_ativas_do_card_kanban(id_card: int) -> list[dict[str, Any]]:
    sql = text("""
        SELECT
            fo.IDFatoOcupacaoPaineisEuromidia,
            fo.CodPonto,
            fo.CodFace,
            fo.DataInicio,
            fo.DataFim,
            fo.Origem,
            fo.Status,
            fo.Observacao
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
        WHERE fo.CanceladoEm IS NULL
          AND fo.Status IN ('ATIVO', 'RESERVADO')
          AND (
                COALESCE(fo.Origem, '') = :origem_card
                OR COALESCE(fo.Observacao, '') LIKE :marcador_novo
                OR COALESCE(fo.Observacao, '') LIKE :marcador_antigo
          )
        ORDER BY fo.IDFatoOcupacaoPaineisEuromidia ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "origem_card": ORIGEM_RESERVA_CARD_KANBAN,
            "marcador_novo": f"[CARD_ID={int(id_card)}][COD_FACE=%",
            "marcador_antigo": f"[KANBAN_CARD={int(id_card)}][COD_FACE=%",
        },
    ).mappings().all()

    return [dict(row) for row in rows]


def _card_tem_reservas_ativas_kanban(id_card: int) -> bool:
    sql = text("""
        SELECT TOP 1 1
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
        WHERE fo.CanceladoEm IS NULL
          AND fo.Status IN ('ATIVO', 'RESERVADO')
          AND (
                COALESCE(fo.Origem, '') = :origem_card
                OR COALESCE(fo.Observacao, '') LIKE :marcador_novo
                OR COALESCE(fo.Observacao, '') LIKE :marcador_antigo
          );
    """)

    valor = db.session.execute(
        sql,
        {
            "origem_card": ORIGEM_RESERVA_CARD_KANBAN,
            "marcador_novo": f"[CARD_ID={int(id_card)}][COD_FACE=%",
            "marcador_antigo": f"[KANBAN_CARD={int(id_card)}][COD_FACE=%",
        },
    ).scalar()
    return bool(valor)


def _cancelar_reservas_card_kanban(
    *,
    id_card: int,
    id_usuario: int,
    chaves_manter: set[tuple[str, date | None, date | None]] | None = None,
    motivo: str | None = None,
) -> int:
    chaves_manter = set(chaves_manter or set())
    reservas_ativas = _listar_reservas_ativas_do_card_kanban(int(id_card))
    if not reservas_ativas:
        return 0

    sql_cancel = text("""
        UPDATE [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
        SET
            Status = 'CANCELADO',
            CanceladoEm = SYSDATETIME(),
            CanceladoPorIDUsuario = :id_usuario,
            DataAtualizacao = SYSDATETIME(),
            Observacao = CASE
                WHEN :motivo IS NULL OR LTRIM(RTRIM(:motivo)) = '' THEN Observacao
                WHEN Observacao IS NULL OR LTRIM(RTRIM(Observacao)) = '' THEN :motivo
                ELSE CONCAT(Observacao, CHAR(10), :motivo)
            END
        WHERE IDFatoOcupacaoPaineisEuromidia = :id_ocupacao
          AND CanceladoEm IS NULL;
    """)

    canceladas = 0
    motivo_final = str(motivo or '').strip() or f"[CARD_ID={int(id_card)}] Reserva cancelada por sincronização do card."

    for reserva in reservas_ativas:
        chave_reserva = _obter_chave_reserva_card_kanban(
            reserva.get("CodFace"),
            reserva.get("DataInicio"),
            reserva.get("DataFim"),
        )
        if chave_reserva in chaves_manter:
            continue

        db.session.execute(
            sql_cancel,
            {
                "id_usuario": int(id_usuario),
                "motivo": motivo_final[:1000],
                "id_ocupacao": int(reserva.get("IDFatoOcupacaoPaineisEuromidia") or 0),
            },
        )
        canceladas += 1

    return canceladas


def _normalizar_payload_reservas_card_kanban(itens: list[Any] | None) -> list[dict[str, Any]]:
    normalizados: list[dict[str, Any]] = []

    for item in (itens or []):
        if not isinstance(item, dict):
            continue

        cod_face = str(item.get("cod_face") or item.get("CodFace") or "").strip().upper()

        id_painel = item.get("id_painel")
        if id_painel in (None, "", 0):
            id_painel = item.get("IDDimPaineisEuromidia")

        data_inicio = (
            item.get("data_inicio")
            or item.get("DataInicio")
            or item.get("data_inicio_reserva")
            or item.get("DataInicioReserva")
        )
        data_fim = (
            item.get("data_fim")
            or item.get("DataFim")
            or item.get("data_fim_reserva")
            or item.get("DataFimReserva")
        )

        normalizados.append(
            {
                "id_painel": int(id_painel or 0) or None,
                "cod_face": cod_face,
                "data_inicio": _normalizar_data_reserva_kanban(data_inicio),
                "data_fim": _normalizar_data_reserva_kanban(data_fim),
                "exibicoes_dia": item.get("exibicoes_dia") or item.get("ExibicoesDia"),
                "cod_ponto": item.get("cod_ponto") or item.get("CodPonto"),
            }
        )

    return normalizados


def _sincronizar_reservas_painel_faces_kanban(
    *,
    id_card: int,
    titulo_card: str,
    id_empresa_relacionada: int | None,
    id_usuario: int,
    id_empresa_proprietaria: int,
    painel_faces_payload: list[Any] | None = None,
    vinculos_preparados: list[dict[str, Any]] | None = None,
    cancelar_todas: bool = False,
) -> dict[str, int]:
    if cancelar_todas:
        canceladas = _cancelar_reservas_card_kanban(
            id_card=int(id_card),
            id_usuario=int(id_usuario),
            chaves_manter=set(),
            motivo=f"[CARD_ID={int(id_card)}] Reserva cancelada porque o card foi removido/inativado.",
        )
        return {"criadas": 0, "canceladas": int(canceladas), "mantidas": 0}

    itens_para_sincronizar = _normalizar_payload_reservas_card_kanban(
        painel_faces_payload if isinstance(painel_faces_payload, list) else vinculos_preparados
    )
    if not itens_para_sincronizar:
        itens_para_sincronizar = _normalizar_payload_reservas_card_kanban(_listar_paineis_vinculados_card(int(id_card)))

    chaves_desejadas: set[tuple[str, date | None, date | None]] = set()
    itens_validos: list[dict[str, Any]] = []
    for item in itens_para_sincronizar:
        data_inicio = item.get("data_inicio")
        data_fim = item.get("data_fim")
        cod_face = str(item.get("cod_face") or "").strip().upper()

        if not cod_face:
            continue
        if data_inicio is None and data_fim is None:
            continue
        if data_inicio is None or data_fim is None:
            raise ValueError("Preencha Data de e Data até para reservar o painel/face.")
        if data_fim < data_inicio:
            raise ValueError("A Data até não pode ser menor que a Data de.")

        chave = _obter_chave_reserva_card_kanban(cod_face, data_inicio, data_fim)
        chaves_desejadas.add(chave)
        itens_validos.append(item)

    canceladas = _cancelar_reservas_card_kanban(
        id_card=int(id_card),
        id_usuario=int(id_usuario),
        chaves_manter=chaves_desejadas,
        motivo=f"[CARD_ID={int(id_card)}] Reserva cancelada por atualização do card.",
    )

    criadas = _criar_reservas_painel_faces_kanban(
        id_card=int(id_card),
        titulo_card=titulo_card,
        id_empresa_relacionada=int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
        painel_faces_payload=itens_validos,
        vinculos_preparados=vinculos_preparados if isinstance(vinculos_preparados, list) and vinculos_preparados else itens_validos,
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )

    return {
        "criadas": int(criadas or 0),
        "canceladas": int(canceladas or 0),
        "mantidas": int(max(len(chaves_desejadas) - int(criadas or 0), 0)),
    }


def _obter_capacidade_face_reserva_kanban(cod_face: str) -> dict[str, Any]:
    sql = text("""
        SELECT TOP 1
            IDPainelEuromidia = TRY_CONVERT(int, p.IDDimPaineisEuromidia),
            CodPonto = TRY_CONVERT(int, p.CodPonto),
            TipoPainel = UPPER(LTRIM(RTRIM(COALESCE(p.Tipo, '')))),
            QuantidadeFaces = TRY_CONVERT(int, NULLIF(p.QuantidadeFaces, 0)),
            BitAtivo = COALESCE(p.BitAtivo, 1)
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        INNER JOIN [Integracao].[Silver].[DimPaineisEuromidia] p
            ON TRY_CONVERT(int, p.CodPonto) = TRY_CONVERT(int, f.CodPonto)
        WHERE UPPER(LTRIM(RTRIM(COALESCE(f.CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
        ORDER BY
            COALESCE(p.BitAtivo, 1) DESC,
            TRY_CONVERT(int, p.IDDimPaineisEuromidia) DESC
    """)

    row = db.session.execute(sql, {"cod_face": str(cod_face or "").strip()}).mappings().first()
    if not row:
        raise ValueError(f"Não foi possível localizar o painel da face {cod_face}.")

    item = dict(row)
    tipo_painel = str(item.get("TipoPainel") or "").strip().upper()
    eh_digital = 1 if "DIGITAL" in tipo_painel else 0
    capacidade_slots = int(item.get("QuantidadeFaces") or 0) if eh_digital else 1
    if eh_digital and capacidade_slots <= 0:
        capacidade_slots = 16

    item["EhDigital"] = eh_digital
    item["CapacidadeSlots"] = capacidade_slots
    item["BitAtivo"] = int(item.get("BitAtivo") or 0)

    return item


def _reserva_kanban_ja_existe(
    *,
    id_card: int,
    cod_face: str,
    data_inicio: date,
    data_fim: date,
) -> bool:
    marcador_novo = _marcador_reserva_card_kanban(int(id_card), cod_face)
    marcador_antigo = _marcador_reserva_card_kanban_legacy(int(id_card), cod_face)

    sql = text("""
        SELECT TOP 1 1
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
        WHERE UPPER(LTRIM(RTRIM(COALESCE(CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
          AND CanceladoEm IS NULL
          AND Status IN ('ATIVO', 'RESERVADO')
          AND TRY_CONVERT(date, DataInicio) = :data_inicio
          AND TRY_CONVERT(date, DataFim) = :data_fim
          AND (
                COALESCE(Origem, '') = :origem_card
                OR COALESCE(Observacao, '') LIKE :marcador_novo
                OR COALESCE(Observacao, '') LIKE :marcador_antigo
          )
    """)

    existe = db.session.execute(
        sql,
        {
            "cod_face": str(cod_face or "").strip(),
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "origem_card": ORIGEM_RESERVA_CARD_KANBAN,
            "marcador_novo": f"{marcador_novo}%",
            "marcador_antigo": f"{marcador_antigo}%",
        },
    ).scalar()

    return bool(existe)


def _validar_conflito_reserva_kanban(
    *,
    cod_face: str,
    data_inicio: date,
    data_fim: date,
    eh_digital: int,
    capacidade_slots: int,
) -> bool:
    if int(eh_digital or 0) != 1:
        sql = text("""
            SELECT TOP 1 1
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
            WHERE UPPER(LTRIM(RTRIM(COALESCE(CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
              AND CanceladoEm IS NULL
              AND Status IN ('ATIVO', 'RESERVADO')
              AND NOT (
                    TRY_CONVERT(date, :data_fim) < TRY_CONVERT(date, DataInicio)
                 OR TRY_CONVERT(date, :data_inicio) > TRY_CONVERT(date, DataFim)
              )
        """)
        existe = db.session.execute(
            sql,
            {
                "cod_face": cod_face,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
            },
        ).scalar()
        return bool(existe)

    sql = text("""
        ;WITH Dias AS (
            SELECT c.[Data]
            FROM [Integracao].[Silver].[DimCalendario] c
            WHERE c.[Data] >= :data_inicio
              AND c.[Data] <= :data_fim
        ),
        Uso AS (
            SELECT
                d.[Data] AS Dia,
                SlotsOcupados = COALESCE(SUM(COALESCE(NULLIF(fo.SpanQtd, 0), 1)), 0)
            FROM Dias d
            LEFT JOIN [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
                ON d.[Data] >= CAST(fo.DataInicio AS date)
               AND d.[Data] <= CAST(fo.DataFim AS date)
               AND UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
               AND fo.CanceladoEm IS NULL
               AND fo.Status IN ('ATIVO', 'RESERVADO')
            GROUP BY d.[Data]
        )
        SELECT TOP 1 1
        FROM Uso
        WHERE SlotsOcupados >= :capacidade_slots
        ORDER BY Dia
    """)

    existe = db.session.execute(
        sql,
        {
            "cod_face": cod_face,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "capacidade_slots": int(capacidade_slots or 0),
        },
    ).scalar()

    return bool(existe)


def _obter_proxima_prioridade_reserva_kanban(
    *,
    cod_face: str,
    data_inicio: date,
    data_fim: date,
    cota: int | None,
    spanqtd: int | None,
) -> int:
    sql = text("""
        SELECT
            COALESCE(MAX(COALESCE(ReservaOrdemPrioridade, 0)), 0) + 1
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] WITH (UPDLOCK, HOLDLOCK)
        WHERE UPPER(LTRIM(RTRIM(COALESCE(CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
          AND CanceladoEm IS NULL
          AND Status IN ('ATIVO', 'RESERVADO')
          AND TRY_CONVERT(date, DataInicio) = :data_inicio
          AND TRY_CONVERT(date, DataFim) = :data_fim
          AND ((:cota IS NULL AND Cota IS NULL) OR (Cota = :cota))
          AND ((:spanqtd IS NULL AND SpanQtd IS NULL) OR (COALESCE(SpanQtd, 0) = COALESCE(:spanqtd, 0)))
    """)

    valor = db.session.execute(
        sql,
        {
            "cod_face": cod_face,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "cota": cota,
            "spanqtd": spanqtd,
        },
    ).scalar()

    try:
        return int(valor or 1)
    except Exception:
        return 1


def _criar_reservas_painel_faces_kanban(
    *,
    id_card: int,
    titulo_card: str,
    id_empresa_relacionada: int | None,
    painel_faces_payload: list[Any] | None,
    vinculos_preparados: list[dict[str, Any]] | None,
    id_usuario: int,
    id_empresa_proprietaria: int,
) -> int:
    if not isinstance(painel_faces_payload, list) or not painel_faces_payload:
        return 0

    mapa_vinculos: dict[tuple[int | None, str], dict[str, Any]] = {}
    for vinculo in (vinculos_preparados or []):
        chave = (
            int(vinculo.get("id_painel") or 0) or None,
            str(vinculo.get("cod_face") or "").strip().upper(),
        )
        if chave[1]:
            mapa_vinculos[chave] = vinculo

    vendedor_logado = _obter_vendedor_logado_reserva_kanban(int(id_empresa_proprietaria))
    id_vendedor = int(vendedor_logado.get("IDVendedor") or 0) if vendedor_logado else None
    nome_vendedor = str(vendedor_logado.get("NomeVendedor") or "").strip() if vendedor_logado else ""

    razao_social = _obter_razao_social_empresa_reserva_kanban(id_empresa_relacionada)
    marca_exibida_padrao = razao_social or str(titulo_card or "").strip() or f"Card {int(id_card)}"

    reservas_criadas = 0

    for item in painel_faces_payload:
        if not isinstance(item, dict):
            continue

        data_inicio = _normalizar_data_reserva_kanban(item.get("data_inicio"))
        data_fim = _normalizar_data_reserva_kanban(item.get("data_fim"))

        if data_inicio is None and data_fim is None:
            continue

        if data_inicio is None or data_fim is None:
            raise ValueError("Preencha Data de e Data até para reservar o painel/face.")

        if data_fim < data_inicio:
            raise ValueError("A Data até não pode ser menor que a Data de.")

        id_painel = int(item.get("id_painel") or 0) or None
        cod_face = str(item.get("cod_face") or "").strip().upper()

        if not cod_face:
            raise ValueError("CodFace obrigatório para criar a reserva.")

        vinculo = mapa_vinculos.get((id_painel, cod_face))
        if not vinculo:
            raise ValueError(f"Não foi possível preparar o vínculo comercial da face {cod_face} para reservar.")

        capacidade = _obter_capacidade_face_reserva_kanban(cod_face)
        if int(capacidade.get("BitAtivo") or 0) != 1:
            raise ValueError(f"O painel da face {cod_face} está inativo.")

        cod_ponto = int(vinculo.get("cod_ponto") or capacidade.get("CodPonto") or 0)
        id_painel_final = int(vinculo.get("id_painel") or capacidade.get("IDPainelEuromidia") or 0) or None
        eh_digital = int(capacidade.get("EhDigital") or 0)
        capacidade_slots = int(capacidade.get("CapacidadeSlots") or 0)

        if not cod_ponto:
            raise ValueError(f"Não foi possível resolver o CodPonto da face {cod_face}.")

        marcador_observacao = _marcador_reserva_card_kanban(int(id_card), cod_face)

        if _reserva_kanban_ja_existe(
            id_card=int(id_card),
            cod_face=cod_face,
            data_inicio=data_inicio,
            data_fim=data_fim,
        ):
            continue

        sem_capacidade = _validar_conflito_reserva_kanban(
            cod_face=cod_face,
            data_inicio=data_inicio,
            data_fim=data_fim,
            eh_digital=eh_digital,
            capacidade_slots=capacidade_slots,
        )

        spanqtd_novo = 1 if eh_digital == 1 else None

        cota_valor = vinculo.get("exibicoes_dia")
        try:
            cota_int = int(cota_valor) if cota_valor not in (None, "", 0) else None
        except Exception:
            cota_int = None

        reserva_ordem_prioridade = (
            _obter_proxima_prioridade_reserva_kanban(
                cod_face=cod_face,
                data_inicio=data_inicio,
                data_fim=data_fim,
                cota=cota_int,
                spanqtd=spanqtd_novo,
            )
            if sem_capacidade
            else 1
        )

        dias_int = ((data_fim - data_inicio).days + 1)

        observacao_insert = f"{marcador_observacao} Reserva criada pelo salvamento do card no Kanban."

        sql_insert = text("""
            INSERT INTO [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] (
                DataAtualizacao,
                Referencia,
                CodPonto,
                CodFace,
                IDPainelEuromidia,
                Origem,
                Status,
                DataInicio,
                DataFim,
                SpanQtd,
                Cota,
                MarcaExibida,
                Vendedor,
                IDVendedor,
                IDCliente,
                IDFatoControleContratos,
                NumeroContrato,
                NumeroPrevia,
                Observacao,
                Dias,
                ExpiraEm,
                CriadoEm,
                CriadoPorIDUsuario,
                ReservaOrdemPrioridade
            )
            VALUES (
                SYSDATETIME(),
                CONVERT(varchar(64),
                    HASHBYTES(
                        'SHA2_256',
                        CONCAT(
                            'KANBAN|',
                            COALESCE(CONVERT(varchar(30), :cod_ponto), ''), '|',
                            UPPER(LTRIM(RTRIM(COALESCE(:cod_face, '')))), '|',
                            COALESCE(CONVERT(varchar(10), :data_inicio, 23), ''), '|',
                            COALESCE(CONVERT(varchar(10), :data_fim, 23), ''), '|',
                            COALESCE(CONVERT(varchar(30), :spanqtd), ''), '|',
                            COALESCE(CONVERT(varchar(30), :cota), ''), '|',
                            COALESCE(CONVERT(varchar(30), :id_cliente), ''), '|',
                            COALESCE(CONVERT(varchar(30), :id_vendedor), ''), '|',
                            COALESCE(CONVERT(varchar(30), :id_card), '')
                        )
                    ),
                    2
                ),
                :cod_ponto,
                :cod_face,
                :id_painel,
                :origem,
                'RESERVADO',
                :data_inicio,
                :data_fim,
                :spanqtd,
                :cota,
                :marca_exibida,
                :vendedor_nome,
                :id_vendedor,
                :id_cliente,
                NULL,
                NULL,
                NULL,
                :observacao,
                :dias,
                DATEADD(day, :dias, SYSDATETIME()),
                SYSDATETIME(),
                :criado_por,
                :reserva_ordem_prioridade
            )
        """)

        db.session.execute(
            sql_insert,
            {
                "id_card": int(id_card),
                "cod_ponto": int(cod_ponto),
                "cod_face": cod_face,
                "id_painel": id_painel_final,
                "origem": ORIGEM_RESERVA_CARD_KANBAN,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "spanqtd": spanqtd_novo,
                "cota": cota_int,
                "marca_exibida": marca_exibida_padrao[:200],
                "vendedor_nome": nome_vendedor[:200] if nome_vendedor else None,
                "id_vendedor": id_vendedor,
                "id_cliente": int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
                "observacao": observacao_insert[:1000],
                "dias": int(dias_int),
                "criado_por": int(id_usuario),
                "reserva_ordem_prioridade": int(reserva_ordem_prioridade),
            },
        )

        reservas_criadas += 1

    return reservas_criadas







def _preparar_vinculos_painel_faces(
    painel_faces_payload: list[Any],
    id_empresa_proprietaria: int,
    id_card: int | None = None,
    id_contrato_existente: int | None = None,
) -> list[dict[str, Any]]:
    """
    Eu preparo painel/face para salvar em Kanban.Silver.FatoKanbanCardPainelFace.

    Correção principal:
    - aceito vários nomes possíveis para ID de tabela de preço;
    - se o preço não vier no payload, busco em Integracao.Silver.FatoTabelaPrecosEuromidia;
    - se não existir preço cadastrado na tabela de preços, uso preço praticado como base;
    - calculo PercentualDesconto automaticamente quando vier ValorTabela e NovoValor;
    - resolvo IDDimFacesPaineis para não deixar a face NULL.
    """
    vinculos_preparados: list[dict[str, Any]] = []

    for ordem_rel, item in enumerate((painel_faces_payload or []), start=1):
        if not isinstance(item, dict):
            raise ValueError("Cada item de painel_faces deve ser um objeto")

        id_painel_item = int(
            _primeiro_valor_preenchido_local(
                item.get("id_painel"),
                item.get("IDDimPaineisEuromidia"),
                item.get("IDPainel"),
                item.get("id_dim_paineis_euromidia"),
            )
            or 0
        )

        cod_face_item = _normalizar_texto(
            _primeiro_valor_preenchido_local(
                item.get("cod_face"),
                item.get("CodFace"),
                item.get("Face"),
                item.get("face"),
            )
        )

        id_face_payload = _primeiro_valor_preenchido_local(
            item.get("id_face"),
            item.get("IDDimFacesPaineis"),
            item.get("IDFace"),
            item.get("id_dim_faces_paineis"),
        )

        id_face_item = None
        if id_face_payload not in (None, "", 0):
            try:
                id_face_item = int(id_face_payload)
            except Exception:
                id_face_item = None

        if not id_painel_item:
            raise ValueError("Painel é obrigatório em cada vinculação")

        data_inicio_item = _normalizar_data_reserva_kanban(
            item.get("data_inicio")
            or item.get("DataInicio")
            or item.get("data_inicio_reserva")
            or item.get("DataInicioReserva")
        )

        data_fim_item = _normalizar_data_reserva_kanban(
            item.get("data_fim")
            or item.get("DataFim")
            or item.get("data_fim_reserva")
            or item.get("DataFimReserva")
        )

        if (data_inicio_item is None) ^ (data_fim_item is None):
            raise ValueError("Preencha Data de e Data até para o mesmo painel/face.")

        if data_inicio_item is not None and data_fim_item is not None and data_fim_item < data_inicio_item:
            raise ValueError("A Data até não pode ser menor que a Data de.")

        painel_item = _obter_painel_por_id(id_painel_item)
        if not painel_item:
            raise ValueError(f"Painel {id_painel_item} não encontrado")

        face_item = None

        if id_face_item:
            face_item = _obter_face_por_id(id_face_item)

        if not face_item and cod_face_item:
            face_item = _resolver_face_do_painel(id_painel_item, cod_face_item)

        if not face_item:
            face_item = _resolver_face_unica_do_painel(id_painel_item)

        if not face_item:
            raise ValueError(
                "Não consegui resolver a face do painel. "
                "Informe a face no payload ou corrija o cadastro em DimFacesPaineis."
            )

        id_face_final = int(face_item.get("IDDimFacesPaineis") or 0)
        cod_face_final = _normalizar_texto(face_item.get("CodFace") or cod_face_item)

        if not id_face_final:
            raise ValueError("Face inválida: IDDimFacesPaineis não resolvido.")

        if not cod_face_final:
            raise ValueError("Face inválida: CodFace não resolvido.")

        custo_item = _obter_custo_por_codponto(int(painel_item.get("CodPonto") or 0))

        id_preco_item = _primeiro_valor_preenchido_local(
            item.get("id_preco"),
            item.get("IDDimTabelaPrecosEuromidia"),
            item.get("id_dim_tabela_precos_euromidia"),
            item.get("IDTabelaPreco"),
            item.get("id_tabela_preco"),
        )

        if id_preco_item in ("", None):
            id_preco_item = None
        else:
            try:
                id_preco_item = int(id_preco_item)
            except Exception as exc:
                raise ValueError("Preço selecionado inválido") from exc

        tipo_painel_item = _normalizar_texto(
            _primeiro_valor_preenchido_local(
                item.get("tipo_painel"),
                item.get("TipoPainel"),
                painel_item.get("Tipo"),
                face_item.get("Tipo"),
            )
        )

        preco_item = _obter_preco_padrao_painel_face(
            id_painel=id_painel_item,
            id_face=id_face_final,
            tipo_painel=tipo_painel_item,
            id_preco=id_preco_item,
        )

        valor_tabela_payload = _valor_decimal(
            _primeiro_valor_preenchido_local(
                item.get("valor_tabela"),
                item.get("ValorTabela"),
                item.get("preco_tabela"),
                item.get("PrecoTabela"),
                item.get("preco_atual"),
                item.get("PrecoAtual"),
            )
        )

        valor_tabela_preco = _valor_decimal((preco_item or {}).get("Valor"))

        preco_praticado_base = _buscar_preco_praticado_base_card_painel_face(
            id_card=int(id_card) if id_card not in (None, "", 0) else None,
            id_painel=id_painel_item,
            id_face=id_face_final,
            id_contrato=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        )

        novo_valor_item = _valor_decimal(
            _primeiro_valor_preenchido_local(
                item.get("novo_valor"),
                item.get("NovoValor"),
                item.get("valor_negociado"),
                item.get("ValorNegociado"),
            )
        )

        valor_venda_final_item = _valor_decimal(
            _primeiro_valor_preenchido_local(
                item.get("valor_venda_final"),
                item.get("ValorVendaFinal"),
                item.get("valor_venda"),
                item.get("ValorVenda"),
            )
        )

        percentual_item = _valor_decimal(
            _primeiro_valor_preenchido_local(
                item.get("percentual_desconto"),
                item.get("PercentualDesconto"),
                item.get("DescontoPercentual"),
                item.get("desconto_percentual"),
            )
        )

        valor_tabela_final = (
            valor_tabela_preco
            or valor_tabela_payload
            or preco_praticado_base
        )

        valor_final_negociado = (
            novo_valor_item
            if novo_valor_item is not None
            else valor_venda_final_item
        )

        if percentual_item is None and valor_tabela_final is not None and valor_final_negociado is not None:
            percentual_item = _calcular_percentual_desconto_seguro(
                valor_tabela=valor_tabela_final,
                valor_final=valor_final_negociado,
            )

        if valor_tabela_final is None:
            valor_tabela_final = valor_final_negociado

        metricas = _calcular_margens_comerciais(
            custo_item.get("Valor") if custo_item else None,
            valor_tabela_final,
            valor_final_negociado,
            percentual_item,
            valor_base_referencia=valor_tabela_final,
        )

        vinculos_preparados.append(
            {
                "ordem": ordem_rel,
                "id_painel": int(id_painel_item),
                "id_dim_face": int(id_face_final),
                "cod_ponto": int(painel_item.get("CodPonto") or 0)
                if painel_item.get("CodPonto") is not None
                else None,
                "cod_face": cod_face_final,
                "tipo_painel": tipo_painel_item or None,
                "ano_custo": int(custo_item.get("Ano") or 0)
                if custo_item and custo_item.get("Ano") is not None
                else None,
                "custo_tabela": metricas.get("Custo"),
                "id_preco": int(preco_item.get("IDDimTabelaPrecosEuromidia") or 0) if preco_item else None,
                "periodo_exibicao": preco_item.get("PeriodoExibicao") if preco_item else None,
                "exibicoes_dia": int(preco_item.get("ExibicoesDia") or 0)
                if preco_item and preco_item.get("ExibicoesDia") is not None
                else None,
                "valor_tabela": metricas.get("ValorTabela"),
                "tabela": preco_item.get("Tabela") if preco_item else None,
                "politica_trocas": preco_item.get("PoliticaTrocas") if preco_item else None,
                "valor_troca": _decimal_para_float(preco_item.get("ValorTroca")) if preco_item else None,
                "novo_valor": metricas.get("NovoValor"),
                "percentual_desconto": metricas.get("PercentualDesconto"),
                "valor_venda_final": metricas.get("ValorVendaFinal"),
                "margem_valor": metricas.get("MargemValor"),
                "margem_percentual": metricas.get("MargemPercentual"),
                "data_inicio": data_inicio_item,
                "data_fim": data_fim_item,
                "id_usuario": _id_usuario(),
                "id_empresa": int(id_empresa_proprietaria),
            }
        )

    return vinculos_preparados










def _corrigir_estado_operacional_preco_card(
    *,
    id_card: int,
    id_empresa_proprietaria: int,
    id_contrato_existente: int | None = None,
) -> dict[str, Any]:
    """
    Eu corrijo linhas ativas já existentes em FatoKanbanCardPainelFace.

    Por que isso é necessário:
    - já existem cards salvos com ValorTabela NULL;
    - já existem cards salvos com PercentualDesconto NULL;
    - já existem cards salvos com IDDimFacesPaineis NULL;
    - sem esses campos, a aprovação de desconto não consegue funcionar.

    Esta função:
    1) resolve face;
    2) busca preço de tabela;
    3) usa preço praticado como fallback;
    4) calcula PercentualDesconto;
    5) atualiza somente campos faltantes/inconsistentes.
    """
    id_card_int = int(id_card or 0)

    if id_card_int <= 0:
        return {
            "ok": False,
            "linhas_avaliadas": 0,
            "linhas_corrigidas": 0,
            "motivo": "id_card_invalido",
        }

    sql = text(f"""
        SELECT
            pf.IDFatoKanbanCardPainelFace,
            pf.IDFatoKanbanCard,
            pf.Ordem,
            pf.IDDimPaineisEuromidia,
            pf.IDDimFacesPaineis,
            pf.CodPonto,
            pf.CodFace,
            pf.TipoPainel,
            pf.CustoTabela,
            pf.IDDimTabelaPrecosEuromidia,
            pf.PeriodoExibicao,
            pf.ExibicoesDia,
            pf.ValorTabela,
            pf.Tabela,
            pf.PoliticaTrocas,
            pf.ValorTroca,
            pf.NovoValor,
            pf.PercentualDesconto,
            pf.ValorVendaFinal,
            pf.MargemValor,
            pf.MargemPercentual,
            pf.DataInicio,
            pf.DataFim
        FROM {TABELA_CARD_PAINEL_FACE} pf
        WHERE pf.IDFatoKanbanCard = :id_card
          AND ISNULL(pf.Ativo, 1) = 1
        ORDER BY
            ISNULL(pf.Ordem, 0),
            pf.IDFatoKanbanCardPainelFace;
    """)

    linhas = db.session.execute(
        sql,
        {"id_card": id_card_int},
    ).mappings().all()

    linhas_avaliadas = 0
    linhas_corrigidas = 0
    problemas: list[dict[str, Any]] = []

    for row in linhas:
        linhas_avaliadas += 1
        linha = dict(row)

        id_linha = int(linha.get("IDFatoKanbanCardPainelFace") or 0)
        id_painel = int(linha.get("IDDimPaineisEuromidia") or 0) or None
        id_face = int(linha.get("IDDimFacesPaineis") or 0) or None
        cod_face = _normalizar_texto(linha.get("CodFace"))
        cod_ponto = _normalizar_texto(linha.get("CodPonto"))

        painel = _obter_painel_por_id(int(id_painel)) if id_painel else None

        face = _obter_face_por_id(int(id_face)) if id_face else None

        if not face and id_painel and cod_face:
            face = _resolver_face_do_painel(int(id_painel), cod_face)

        if not face and cod_ponto and cod_face:
            face = _obter_face_por_codponto_codface(cod_ponto, cod_face)

        if not face and id_painel:
            face = _resolver_face_unica_do_painel(int(id_painel))

        if not painel and face and face.get("IDDimPaineisEuromidia") not in (None, "", 0):
            painel = _obter_painel_por_id(int(face.get("IDDimPaineisEuromidia")))

        id_painel_final = int(
            (painel or {}).get("IDDimPaineisEuromidia")
            or id_painel
            or 0
        ) or None

        id_face_final = int(
            (face or {}).get("IDDimFacesPaineis")
            or id_face
            or 0
        ) or None

        cod_ponto_final = _normalizar_texto(
            (painel or {}).get("CodPonto")
            or (face or {}).get("CodPonto")
            or cod_ponto
        )

        cod_face_final = _normalizar_texto(
            (face or {}).get("CodFace")
            or cod_face
        )

        tipo_painel_final = _normalizar_texto(
            linha.get("TipoPainel")
            or (painel or {}).get("Tipo")
            or (face or {}).get("Tipo")
        )

        if not id_linha:
            continue

        if not id_painel_final:
            problemas.append(
                {
                    "id_linha": id_linha,
                    "motivo": "painel_nao_resolvido",
                }
            )
            continue

        if not id_face_final:
            problemas.append(
                {
                    "id_linha": id_linha,
                    "motivo": "face_nao_resolvida",
                    "id_painel": id_painel_final,
                    "cod_ponto": cod_ponto_final,
                    "cod_face": cod_face_final,
                }
            )
            continue

        id_preco_atual = int(linha.get("IDDimTabelaPrecosEuromidia") or 0) or None

        preco_item = _obter_preco_padrao_painel_face(
            id_painel=id_painel_final,
            id_face=id_face_final,
            tipo_painel=tipo_painel_final,
            id_preco=id_preco_atual,
        )

        valor_tabela_atual = _valor_decimal(linha.get("ValorTabela"))
        valor_tabela_preco = _valor_decimal((preco_item or {}).get("Valor"))

        preco_praticado_base = _buscar_preco_praticado_base_card_painel_face(
            id_card=id_card_int,
            id_painel=id_painel_final,
            id_face=id_face_final,
            id_contrato=int(id_contrato_existente) if id_contrato_existente not in (None, "", 0) else None,
        )

        valor_tabela_final = (
            valor_tabela_atual
            or valor_tabela_preco
            or preco_praticado_base
        )

        valor_final_negociado = _valor_decimal(linha.get("NovoValor"))
        if valor_final_negociado is None:
            valor_final_negociado = _valor_decimal(linha.get("ValorVendaFinal"))

        if valor_tabela_final is None:
            valor_tabela_final = valor_final_negociado

        percentual_final = _valor_decimal(linha.get("PercentualDesconto"))

        if percentual_final is None and valor_tabela_final is not None and valor_final_negociado is not None:
            percentual_final = _calcular_percentual_desconto_seguro(
                valor_tabela=valor_tabela_final,
                valor_final=valor_final_negociado,
            )

        custo_final = _valor_decimal(linha.get("CustoTabela"))

        if custo_final is None and cod_ponto_final not in (None, ""):
            try:
                custo_ref = _obter_custo_por_codponto(int(cod_ponto_final))
            except Exception:
                custo_ref = None

            custo_final = _valor_decimal((custo_ref or {}).get("Valor") if isinstance(custo_ref, dict) else None)

        metricas = _calcular_margens_comerciais(
            custo_final,
            valor_tabela_final,
            valor_final_negociado,
            percentual_final,
            valor_base_referencia=valor_tabela_final,
        )

        sets: list[str] = []
        params: dict[str, Any] = {
            "id_linha": id_linha,
        }

        def adicionar_set(campo: str, parametro: str, valor: Any) -> None:
            sets.append(f"{campo} = :{parametro}")
            params[parametro] = valor

        if id_face_final and int(linha.get("IDDimFacesPaineis") or 0) != int(id_face_final):
            adicionar_set("IDDimFacesPaineis", "id_face", int(id_face_final))

        if id_painel_final and int(linha.get("IDDimPaineisEuromidia") or 0) != int(id_painel_final):
            adicionar_set("IDDimPaineisEuromidia", "id_painel", int(id_painel_final))

        if cod_ponto_final and _normalizar_texto(linha.get("CodPonto")) != cod_ponto_final:
            adicionar_set("CodPonto", "cod_ponto", cod_ponto_final)

        if cod_face_final and _normalizar_texto(linha.get("CodFace")) != cod_face_final:
            adicionar_set("CodFace", "cod_face", cod_face_final)

        if tipo_painel_final and _normalizar_texto(linha.get("TipoPainel")) != tipo_painel_final:
            adicionar_set("TipoPainel", "tipo_painel", tipo_painel_final)

        if preco_item:
            id_preco_final = int(preco_item.get("IDDimTabelaPrecosEuromidia") or 0) or None
            if id_preco_final and int(linha.get("IDDimTabelaPrecosEuromidia") or 0) != id_preco_final:
                adicionar_set("IDDimTabelaPrecosEuromidia", "id_tabela_preco", id_preco_final)

            if _normalizar_texto(linha.get("PeriodoExibicao")) != _normalizar_texto(preco_item.get("PeriodoExibicao")):
                adicionar_set("PeriodoExibicao", "periodo_exibicao", preco_item.get("PeriodoExibicao"))

            if int(linha.get("ExibicoesDia") or 0) != int(preco_item.get("ExibicoesDia") or 0):
                adicionar_set("ExibicoesDia", "exibicoes_dia", int(preco_item.get("ExibicoesDia") or 0) or None)

            if _normalizar_texto(linha.get("Tabela")) != _normalizar_texto(preco_item.get("Tabela")):
                adicionar_set("Tabela", "tabela", preco_item.get("Tabela"))

            if _normalizar_texto(linha.get("PoliticaTrocas")) != _normalizar_texto(preco_item.get("PoliticaTrocas")):
                adicionar_set("PoliticaTrocas", "politica_trocas", preco_item.get("PoliticaTrocas"))

            if _valor_decimal(linha.get("ValorTroca")) != _valor_decimal(preco_item.get("ValorTroca")):
                adicionar_set("ValorTroca", "valor_troca", _valor_decimal(preco_item.get("ValorTroca")))

        if _valor_decimal(linha.get("CustoTabela")) != _valor_decimal(metricas.get("Custo")):
            adicionar_set("CustoTabela", "custo_tabela", metricas.get("Custo"))

        if _valor_decimal(linha.get("ValorTabela")) != _valor_decimal(metricas.get("ValorTabela")):
            adicionar_set("ValorTabela", "valor_tabela", metricas.get("ValorTabela"))

        if _valor_decimal(linha.get("PercentualDesconto")) != _valor_decimal(metricas.get("PercentualDesconto")):
            adicionar_set("PercentualDesconto", "percentual_desconto", metricas.get("PercentualDesconto"))

        if _valor_decimal(linha.get("ValorVendaFinal")) != _valor_decimal(metricas.get("ValorVendaFinal")):
            adicionar_set("ValorVendaFinal", "valor_venda_final", metricas.get("ValorVendaFinal"))

        if _valor_decimal(linha.get("MargemValor")) != _valor_decimal(metricas.get("MargemValor")):
            adicionar_set("MargemValor", "margem_valor", metricas.get("MargemValor"))

        if _valor_decimal(linha.get("MargemPercentual")) != _valor_decimal(metricas.get("MargemPercentual")):
            adicionar_set("MargemPercentual", "margem_percentual", metricas.get("MargemPercentual"))

        if not sets:
            continue

        sets.append("DataAtualizacao = GETDATE()")

        sql_update = text(f"""
            UPDATE {TABELA_CARD_PAINEL_FACE}
               SET {", ".join(sets)}
             WHERE IDFatoKanbanCardPainelFace = :id_linha
               AND ISNULL(Ativo, 1) = 1;
        """)

        resultado = db.session.execute(sql_update, params)

        if int(resultado.rowcount or 0) > 0:
            linhas_corrigidas += 1

    return {
        "ok": True,
        "linhas_avaliadas": int(linhas_avaliadas),
        "linhas_corrigidas": int(linhas_corrigidas),
        "problemas": problemas,
    }


















def _buscar_ultima_negociacao_preco_card(
    *,
    id_card: int,
    id_painel: int,
    id_face: int,
) -> dict[str, Any] | None:
    """
    Busca a última linha histórica da negociação para o mesmo card/painel/face.
    """
    sql = text("""
        SELECT TOP 1
            IDFatoKanbanNegociacaoPreco,
            IDDimTabelaPrecosEuromidia,
            CustoAtual,
            PrecoAtual,
            MargemAtual,
            CustoAtualRateado,
            PrecoAtualRateado,
            MargemAtualRateado,
            CustoProposto,
            PrecoProposto,
            MargemProposta,
            CustoPropostoRateado,
            PrecoPropostoRateado,
            DescontoProposto,
            PeriodoInicio,
            PeriodoTermino,
            ObservacoesProposta
        FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimPaineisEuromidia = :id_painel
          AND IDDimFacesPaineis = :id_face
        ORDER BY IDFatoKanbanNegociacaoPreco DESC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_painel": int(id_painel),
            "id_face": int(id_face),
        },
    ).mappings().first()

    return dict(row) if row else None






def _normalizar_int_negociacao(valor: Any) -> int | None:
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except Exception:
        return None


def _normalizar_decimal_negociacao(valor: Any, casas: str = "0.0001") -> Decimal | None:
    dec = _valor_decimal(valor)
    if dec is None:
        return None

    try:
        return dec.quantize(Decimal(casas))
    except Exception:
        return dec


def _calcular_margem_percentual_negociacao(
    custo: Any,
    preco: Any,
) -> Decimal | None:
    custo_dec = _valor_decimal(custo)
    preco_dec = _valor_decimal(preco)

    if custo_dec is None:
        return None

    if preco_dec in (None, Decimal("0")):
        return None

    return ((preco_dec - custo_dec) / preco_dec) * Decimal("100")


def _montar_assinatura_negociacao_preco(
    *,
    id_tabela_preco: Any,
    custo_atual: Any,
    preco_atual: Any,
    margem_atual: Any,
    custo_proposto: Any,
    preco_proposto: Any,
    margem_proposta: Any,
    desconto_proposto: Any,
) -> tuple:
    """
    Cria uma assinatura estável da negociação.
    Se a assinatura atual for igual à última assinatura histórica,
    não existe motivo para criar nova linha no histórico.
    """
    return (
        _normalizar_int_negociacao(id_tabela_preco),
        _normalizar_decimal_negociacao(custo_atual),
        _normalizar_decimal_negociacao(preco_atual),
        _normalizar_decimal_negociacao(margem_atual),
        _normalizar_decimal_negociacao(custo_proposto),
        _normalizar_decimal_negociacao(preco_proposto),
        _normalizar_decimal_negociacao(margem_proposta),
        _normalizar_decimal_negociacao(desconto_proposto),
    )





def _listar_estado_atual_negociacao_card(id_card: int) -> list[dict[str, Any]]:
    """
    Lê a foto atual canônica do card a partir da tabela operacional.
    É essa tabela que deve ser a fonte da verdade para decidir se houve mudança.
    """
    sql = text("""
        SELECT
            pf.IDFatoKanbanCardPainelFace,
            pf.Ordem,
            pf.IDDimPaineisEuromidia,
            pf.IDDimFacesPaineis,
            pf.IDDimTabelaPrecosEuromidia,
            pf.CustoTabela,
            pf.ValorTabela,
            pf.NovoValor,
            pf.PercentualDesconto,
            pf.ValorVendaFinal,
            pf.MargemValor,
            pf.MargemPercentual,
            pf.DataInicio,
            pf.DataFim
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
        WHERE pf.IDFatoKanbanCard = :id_card
          AND ISNULL(pf.Ativo, 1) = 1
        ORDER BY
            ISNULL(pf.Ordem, 0),
            pf.IDFatoKanbanCardPainelFace;
    """)

    rows = db.session.execute(sql, {"id_card": int(id_card), "origem_card": ORIGEM_RESERVA_CARD_KANBAN}).mappings().all()
    return [dict(row) for row in rows]














def _registrar_negociacao_preco_card(
    *,
    id_card: int,
    id_kanban: int,
    id_fase_atual: int | None,
    status_card: str | None,
    id_empresa_relacionada: int | None,
    vinculos_preparados: list[dict[str, Any]],
    observacoes_proposta: str | None = None,
) -> None:
    """
    Grava histórico de negociação de preço.

    Regras desta versão:
    - aceita dados vindos de:
        1) vinculos_preparados
        2) vínculos persistidos do card
        3) estado operacional atual
    - resolve IDDimFacesPaineis por CodPonto + CodFace quando vier nulo
    - resolve IDDimPaineisEuromidia a partir da própria face, quando necessário
    - força o primeiro INSERT quando ainda não existir histórico
    - evita duplicidade somente quando já existir histórico e a assinatura não mudou
    """

    def _tem_valor_informado(valor: Any) -> bool:
        if valor is None:
            return False
        if isinstance(valor, str) and not valor.strip():
            return False
        return True

    def _para_int_ou_none(valor: Any) -> int | None:
        if valor in (None, ""):
            return None
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None

    def _para_decimal_ou_none(valor: Any) -> Decimal | None:
        if valor in (None, ""):
            return None
        return _valor_decimal(valor)

    def _para_data_ou_none(valor: Any):
        return _normalizar_data_reserva_kanban(valor)

    def _primeiro_valor_preenchido(*valores: Any) -> Any:
        for valor in valores:
            if _tem_valor_informado(valor):
                return valor
        return None

    def _calcular_margem_percentual(custo: Any, preco: Any) -> Decimal | None:
        custo_dec = _valor_decimal(custo)
        preco_dec = _valor_decimal(preco)

        if custo_dec is None:
            return None
        if preco_dec in (None, Decimal("0")):
            return None

        return ((preco_dec - custo_dec) / preco_dec) * Decimal("100")

    def _resolver_chave_painel_face_local(dados: Mapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
        registro = dict(dados or {})

        id_painel = _para_int_ou_none(
            _primeiro_valor_preenchido(
                registro.get("IDDimPaineisEuromidia"),
                registro.get("IDPainelEuromidia"),
                registro.get("id_painel"),
                registro.get("IDPainel"),
                registro.get("idPainel"),
            )
        )
        id_face = _para_int_ou_none(
            _primeiro_valor_preenchido(
                registro.get("IDDimFacesPaineis"),
                registro.get("id_face"),
                registro.get("IDFace"),
                registro.get("idFace"),
            )
        )

        cod_ponto = _primeiro_valor_preenchido(
            registro.get("CodPonto"),
            registro.get("cod_ponto"),
        )
        cod_face = _primeiro_valor_preenchido(
            registro.get("CodFace"),
            registro.get("cod_face"),
        )

        cod_ponto_txt = str(cod_ponto).strip() if cod_ponto not in (None, "") else None
        cod_face_txt = str(cod_face).strip().upper() if cod_face not in (None, "") else None

        info_face = None
        if id_face is not None:
            info_face = _obter_face_por_id(int(id_face))
        elif cod_ponto_txt and cod_face_txt:
            info_face = _obter_face_por_codponto_codface(
                cod_ponto=cod_ponto_txt,
                cod_face=cod_face_txt,
            )

        if info_face:
            if id_face is None:
                id_face = _para_int_ou_none(info_face.get("IDDimFacesPaineis"))
            if id_painel is None:
                id_painel = _para_int_ou_none(info_face.get("IDDimPaineisEuromidia"))
            if not cod_ponto_txt and _tem_valor_informado(info_face.get("CodPonto")):
                cod_ponto_txt = str(info_face.get("CodPonto")).strip()
            if not cod_face_txt and _tem_valor_informado(info_face.get("CodFace")):
                cod_face_txt = str(info_face.get("CodFace")).strip().upper()

        if id_painel is None and cod_ponto_txt:
            info_painel = _obter_painel_por_codponto(cod_ponto_txt)
            if info_painel:
                id_painel = _para_int_ou_none(info_painel.get("IDDimPaineisEuromidia"))

        registro["IDDimPaineisEuromidia"] = id_painel
        registro["IDDimFacesPaineis"] = id_face
        registro["CodPonto"] = cod_ponto_txt
        registro["CodFace"] = cod_face_txt
        return registro

    def _montar_insert_dinamico(valores: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        colunas: list[str] = []
        marcadores: list[str] = []
        parametros: dict[str, Any] = {}

        def adicionar(
            nome_coluna: str,
            nome_parametro: str,
            valor: Any,
            usar_getdate: bool = False,
        ) -> None:
            if not _coluna_existe(TABELA_CARD_NEGOCIACAO_PRECO, nome_coluna):
                return

            colunas.append(nome_coluna)

            if usar_getdate:
                marcadores.append("GETDATE()")
            else:
                marcadores.append(f":{nome_parametro}")
                parametros[nome_parametro] = valor

        adicionar("IDDimUsuarios", "id_usuario", valores.get("id_usuario"))
        adicionar("IDEmpresaProprietaria", "id_empresa_proprietaria", valores.get("id_empresa_proprietaria"))
        adicionar("IDDimTabelaPrecosEuromidia", "id_tabela_preco", valores.get("id_tabela_preco"))
        adicionar("IDEmpresa", "id_empresa_relacionada", valores.get("id_empresa_relacionada"))
        adicionar("IDFatoKanbanCard", "id_card", valores.get("id_card"))
        adicionar("IDDimKanbanFase", "id_fase_atual", valores.get("id_fase_atual"))
        adicionar("IDDimKanbanStatusCard", "id_status_card", valores.get("id_status_card"))
        adicionar("IDFatoControleContratosEuromidia", "id_controle_contrato", valores.get("id_controle_contrato"))
        adicionar("BitAditivoContrato", "bit_aditivo", valores.get("bit_aditivo"))
        adicionar("ObservacoesProposta", "observacoes_proposta", valores.get("observacoes_proposta"))
        adicionar("IDDimPaineisEuromidia", "id_painel", valores.get("id_painel"))
        adicionar("IDDimFacesPaineis", "id_face", valores.get("id_face"))
        adicionar("DataPrecoProposto", "data_preco_proposto", None, usar_getdate=True)

        adicionar("CustoAtual", "custo_atual", valores.get("custo_atual"))
        adicionar("PrecoAtual", "preco_atual", valores.get("preco_atual"))
        adicionar("MargemAtual", "margem_atual", valores.get("margem_atual"))

        adicionar("CustoAtualRateado", "custo_atual_rateado", valores.get("custo_atual_rateado"))
        adicionar("PrecoAtualRateado", "preco_atual_rateado", valores.get("preco_atual_rateado"))
        adicionar("MargemAtualRateado", "margem_atual_rateado", valores.get("margem_atual_rateado"))

        adicionar("CustoProposto", "custo_proposto", valores.get("custo_proposto"))
        adicionar("PrecoProposto", "preco_proposto", valores.get("preco_proposto"))
        adicionar("MargemProposta", "margem_proposta", valores.get("margem_proposta"))

        adicionar("CustoPropostoRateado", "custo_proposto_rateado", valores.get("custo_proposto_rateado"))
        adicionar("PrecoPropostoRateado", "preco_proposto_rateado", valores.get("preco_proposto_rateado"))

        adicionar("DescontoProposto", "desconto_proposto", valores.get("desconto_proposto"))
        adicionar("PeriodoInicio", "periodo_inicio", valores.get("periodo_inicio"))
        adicionar("PeriodoTermino", "periodo_termino", valores.get("periodo_termino"))

        adicionar("IDDimUsuariosAprovacaoPreco", "id_usuario_aprovacao", None)
        adicionar("DataAprovacaoPreco", "data_aprovacao", None)
        adicionar("PrecoAprovado", "preco_aprovado", None)
        adicionar("DescontoAprovado", "desconto_aprovado", None)
        adicionar("ObservacoesAprovacao", "observacoes_aprovacao", None)
        adicionar("BitAutorizacaoDiretoria", "bit_autorizacao_diretoria", valores.get("bit_autorizacao_diretoria", 0))
        adicionar("BitAutorizacaoCoordenador", "bit_autorizacao_coordenador", valores.get("bit_autorizacao_coordenador", 0))

        if not colunas:
            raise ValueError("Nenhuma coluna válida encontrada em FatoKanbanNegociacaoPreco para gravar a negociação.")

        sql = f"""
            INSERT INTO {TABELA_CARD_NEGOCIACAO_PRECO}
            (
                {", ".join(colunas)}
            )
            VALUES
            (
                {", ".join(marcadores)}
            );
        """
        return sql, parametros

    coluna_empresa_relacionada = _nome_coluna_empresa_relacionada_card()

    select_id_contrato = (
        "c.IDFatoControleContratosEuromidia AS IDFatoControleContratosEuromidia"
        if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia")
        else (
            "c.IDFatoControleContratoEuromidia AS IDFatoControleContratosEuromidia"
            if _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia")
            else "CAST(NULL AS int) AS IDFatoControleContratosEuromidia"
        )
    )

    select_bit_aditivo = (
        "c.BitAditivo AS BitAditivo"
        if _coluna_existe(TABELA_CARD, "BitAditivo")
        else "CAST(0 AS int) AS BitAditivo"
    )

    select_id_fase = (
        "c.IDDimKanbanFaseAtual AS IDDimKanbanFaseAtual"
        if _coluna_existe(TABELA_CARD, "IDDimKanbanFaseAtual")
        else "CAST(NULL AS int) AS IDDimKanbanFaseAtual"
    )

    select_status = (
        "c.StatusCard AS StatusCard"
        if _coluna_existe(TABELA_CARD, "StatusCard")
        else "CAST(NULL AS varchar(50)) AS StatusCard"
    )

    select_id_empresa_relacionada = (
        f"c.{coluna_empresa_relacionada} AS IDEmpresaRelacionada"
        if coluna_empresa_relacionada
        else "CAST(NULL AS int) AS IDEmpresaRelacionada"
    )

    sql_card_contexto = text(
        f"""
        SELECT TOP (1)
            {select_id_contrato},
            {select_bit_aditivo},
            {select_id_fase},
            {select_status},
            {select_id_empresa_relacionada}
        FROM {TABELA_CARD} c
        WHERE c.IDFatoKanbanCard = :id_card;
        """
    )

    card_contexto = db.session.execute(
        sql_card_contexto,
        {"id_card": int(id_card)},
    ).mappings().first() or {}

    id_fase_atual_final = _para_int_ou_none(_primeiro_valor_preenchido(id_fase_atual, card_contexto.get("IDDimKanbanFaseAtual")))
    status_card_final = _primeiro_valor_preenchido(status_card, card_contexto.get("StatusCard"))
    id_empresa_relacionada_final = _para_int_ou_none(_primeiro_valor_preenchido(id_empresa_relacionada, card_contexto.get("IDEmpresaRelacionada")))
    id_controle_contrato_card = _para_int_ou_none(card_contexto.get("IDFatoControleContratosEuromidia"))

    bit_aditivo_card = _para_int_ou_none(card_contexto.get("BitAditivo"))
    if bit_aditivo_card is None:
        bit_aditivo_card = 0

    id_status_card = _obter_id_status_card_por_codigo(status_card_final)
    id_usuario_atual = _obter_id_dim_usuario_logado() or _id_usuario()
    id_empresa_proprietaria_negociacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=_id_empresa_usuario_or_403(),
    )

    vinculos_base: list[dict[str, Any]] = []

    for vinculo in (vinculos_preparados or []):
        if isinstance(vinculo, Mapping):
            vinculos_base.append(_resolver_chave_painel_face_local(vinculo))

    for vinculo in (_listar_paineis_vinculados_card(int(id_card)) or []):
        if isinstance(vinculo, Mapping):
            vinculos_base.append(_resolver_chave_painel_face_local(vinculo))

    for estado in (_listar_estado_atual_negociacao_card(int(id_card)) or []):
        if isinstance(estado, Mapping):
            vinculos_base.append(_resolver_chave_painel_face_local(estado))

    if not vinculos_base:
        current_app.logger.warning(
            "NEGOCIACAO_PRECO | card=%s | sem base para gravar histórico",
            int(id_card),
        )
        return

    mapa_vinculos: dict[tuple[int, int], dict[str, Any]] = {}
    for vinculo in vinculos_base:
        id_painel_vinculo = _para_int_ou_none(vinculo.get("IDDimPaineisEuromidia"))
        id_face_vinculo = _para_int_ou_none(vinculo.get("IDDimFacesPaineis"))

        if not id_painel_vinculo or not id_face_vinculo:
            current_app.logger.info(
                "NEGOCIACAO_PRECO | card=%s | vínculo ignorado por falta de painel/face resolvidos | cod_ponto=%s | cod_face=%s",
                int(id_card),
                vinculo.get("CodPonto"),
                vinculo.get("CodFace"),
            )
            continue

        chave = (int(id_painel_vinculo), int(id_face_vinculo))
        if chave not in mapa_vinculos:
            mapa_vinculos[chave] = {}

        for chave_campo, valor_campo in dict(vinculo).items():
            if chave_campo not in mapa_vinculos[chave] or not _tem_valor_informado(mapa_vinculos[chave].get(chave_campo)):
                mapa_vinculos[chave][chave_campo] = valor_campo

    if not mapa_vinculos:
        current_app.logger.warning(
            "NEGOCIACAO_PRECO | card=%s | nenhum painel/face resolvido após fallback",
            int(id_card),
        )
        return

    quantidade_inserida = 0

    for (id_painel, id_face), estado in mapa_vinculos.items():
        id_tabela_preco = _para_int_ou_none(
            _primeiro_valor_preenchido(
                estado.get("IDDimTabelaPrecosEuromidia"),
                estado.get("id_tabela_preco"),
                estado.get("IDTabelaPreco"),
            )
        )

        custo_atual = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("CustoTabela"),
                estado.get("custo_tabela"),
                estado.get("custoAtual"),
            )
        )

        preco_atual = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("ValorTabela"),
                estado.get("valor_tabela"),
                estado.get("precoAtual"),
            )
        )

        margem_atual = _calcular_margem_percentual(custo_atual, preco_atual)

        novo_valor = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("NovoValor"),
                estado.get("novo_valor"),
                estado.get("novoValor"),
            )
        )

        percentual_desconto = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("PercentualDesconto"),
                estado.get("percentual_desconto"),
                estado.get("percentualDesconto"),
                estado.get("DescontoProposto"),
                estado.get("desconto_proposto"),
            )
        )

        valor_venda_final = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("ValorVendaFinal"),
                estado.get("valor_venda_final"),
                estado.get("valorVendaFinal"),
                estado.get("PrecoProposto"),
                estado.get("preco_proposto"),
            )
        )

        periodo_inicio_atual = _para_data_ou_none(
            _primeiro_valor_preenchido(
                estado.get("DataInicio"),
                estado.get("PeriodoInicio"),
                estado.get("periodo_inicio"),
                estado.get("data_inicio"),
            )
        )

        periodo_termino_atual = _para_data_ou_none(
            _primeiro_valor_preenchido(
                estado.get("DataFim"),
                estado.get("PeriodoTermino"),
                estado.get("periodo_termino"),
                estado.get("data_fim"),
            )
        )

        preco_proposto = novo_valor
        if preco_proposto is None:
            preco_proposto = valor_venda_final
        if preco_proposto is None:
            preco_proposto = preco_atual

        custo_proposto = custo_atual
        margem_proposta = _calcular_margem_percentual(custo_proposto, preco_proposto)

        id_controle_contrato = _para_int_ou_none(
            _primeiro_valor_preenchido(
                estado.get("IDFatoControleContratosEuromidia"),
                estado.get("id_controle_contrato"),
                estado.get("id_contrato_existente"),
                id_controle_contrato_card,
            )
        )

        bit_aditivo = _para_int_ou_none(
            _primeiro_valor_preenchido(
                estado.get("BitAditivoContrato"),
                estado.get("bit_aditivo"),
                bit_aditivo_card,
            )
        )
        if bit_aditivo is None:
            bit_aditivo = 0

        precisa_aprovacao_diretoria = _estado_precisa_aprovacao_diretoria(dict(estado))

        tem_operacao_comercial_ou_periodo = any(
            _tem_valor_informado(valor)
            for valor in (
                id_tabela_preco,
                novo_valor,
                percentual_desconto,
                valor_venda_final,
                preco_atual,
                periodo_inicio_atual,
                periodo_termino_atual,
            )
        )
        if not tem_operacao_comercial_ou_periodo:
            continue

        ultima_negociacao = _buscar_ultima_negociacao_preco_card(
            id_card=int(id_card),
            id_painel=int(id_painel),
            id_face=int(id_face),
        )

        if ultima_negociacao:
            houve_alteracao = _negociacao_preco_foi_alterada(
                ultima_negociacao,
                id_tabela_preco=id_tabela_preco,
                custo_atual=custo_atual,
                preco_atual=preco_atual,
                margem_atual_percentual=margem_atual,
                custo_proposto=custo_proposto,
                preco_proposto=preco_proposto,
                margem_proposta_percentual=margem_proposta,
                desconto_proposto=percentual_desconto,
                periodo_inicio=periodo_inicio_atual,
                periodo_termino=periodo_termino_atual,
            )
            if not houve_alteracao:
                continue

        observacoes_proposta_final = _primeiro_valor_preenchido(
            observacoes_proposta,
            estado.get("ObservacoesProposta"),
            estado.get("observacoes_proposta"),
        )

        valores_insert = {
            "id_usuario": id_usuario_atual,
            "id_empresa_proprietaria": id_empresa_proprietaria_negociacao,
            "id_tabela_preco": id_tabela_preco,
            "id_empresa_relacionada": id_empresa_relacionada_final,
            "id_card": int(id_card),
            "id_fase_atual": id_fase_atual_final,
            "id_status_card": _para_int_ou_none(id_status_card),
            "id_controle_contrato": id_controle_contrato,
            "bit_aditivo": bit_aditivo,
            "observacoes_proposta": observacoes_proposta_final,
            "id_painel": int(id_painel),
            "id_face": int(id_face),
            "custo_atual": custo_atual,
            "preco_atual": preco_atual,
            "margem_atual": margem_atual,
            "custo_atual_rateado": custo_atual,
            "preco_atual_rateado": preco_atual,
            "margem_atual_rateado": margem_atual,
            "custo_proposto": custo_proposto,
            "preco_proposto": preco_proposto,
            "margem_proposta": margem_proposta,
            "custo_proposto_rateado": custo_proposto,
            "preco_proposto_rateado": preco_proposto,
            "desconto_proposto": percentual_desconto,
            "periodo_inicio": periodo_inicio_atual,
            "periodo_termino": periodo_termino_atual,
            "bit_autorizacao_diretoria": 1 if precisa_aprovacao_diretoria else 0,
            "bit_autorizacao_coordenador": 0,
        }

        sql_insert, parametros_insert = _montar_insert_dinamico(valores_insert)
        db.session.execute(text(sql_insert), parametros_insert)
        quantidade_inserida += 1

    current_app.logger.info(
        "NEGOCIACAO_PRECO | card=%s | total_inserido=%s",
        int(id_card),
        int(quantidade_inserida),
    )
















def _registrar_historico_negociacao_preco_operacional(
    *,
    id_card: int,
    id_kanban: int,
    id_fase_atual: int | None,
    id_usuario: int,
    id_empresa_proprietaria: int,
    id_empresa_relacionada: int | None = None,
    status_card: str | None = None,
    observacoes_proposta: str | None = None,
    forcar_novo_registro: bool = False,
) -> dict[str, Any]:
    """
    Eu gravo o histórico real da negociação de preço na tabela
    Kanban.Silver.FatoKanbanNegociacaoPreco usando como fonte a tabela
    operacional Kanban.Silver.FatoKanbanCardPainelFace.

    Regra de negócio:
    - FatoKanbanCardPainelFace guarda o estado atual do preço no card.
    - FatoKanbanNegociacaoPreco guarda o histórico transacional da negociação.
    - Sempre que o card tiver preço/custo/desconto/período salvo, esta função
      garante que exista um registro histórico.
    - Para não poluir o histórico, eu não insiro uma nova linha quando a última
      negociação do mesmo card/painel/face tem a mesma assinatura comercial.
    """

    id_card_int = int(id_card or 0)
    id_kanban_int = int(id_kanban or 0)
    id_usuario_int = int(id_usuario or 0)
    id_empresa_prop_int = int(id_empresa_proprietaria or 0)
    id_fase_int = int(id_fase_atual or 0) if id_fase_atual not in (None, "", 0) else None
    id_empresa_rel_int = int(id_empresa_relacionada or 0) if id_empresa_relacionada not in (None, "", 0) else None

    if id_card_int <= 0:
        return {
            "ok": False,
            "linhas_inseridas": 0,
            "motivo": "id_card_invalido",
        }

    if id_usuario_int <= 0:
        return {
            "ok": False,
            "linhas_inseridas": 0,
            "motivo": "id_usuario_invalido",
        }

    if id_empresa_prop_int <= 0:
        return {
            "ok": False,
            "linhas_inseridas": 0,
            "motivo": "id_empresa_proprietaria_invalido",
        }

    if not _objeto_existe(TABELA_CARD_NEGOCIACAO_PRECO):
        return {
            "ok": False,
            "linhas_inseridas": 0,
            "motivo": "tabela_fato_kanban_negociacao_preco_nao_existe",
        }

    id_empresa_proprietaria_negociacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban_int,
        id_empresa_padrao=id_empresa_prop_int,
    )

    id_status_card = None
    try:
        id_status_card = _obter_id_status_card_por_codigo(status_card)
    except Exception:
        id_status_card = None

    select_id_contrato = (
        "TRY_CONVERT(int, c.IDFatoControleContratosEuromidia)"
        if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia")
        else (
            "TRY_CONVERT(int, c.IDFatoControleContratoEuromidia)"
            if _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia")
            else "CAST(NULL AS int)"
        )
    )

    select_bit_aditivo = (
        "TRY_CONVERT(int, c.BitAditivo)"
        if _coluna_existe(TABELA_CARD, "BitAditivo")
        else "CAST(0 AS int)"
    )

    select_id_fase_card = (
        "TRY_CONVERT(int, c.IDDimKanbanFaseAtual)"
        if _coluna_existe(TABELA_CARD, "IDDimKanbanFaseAtual")
        else "CAST(NULL AS int)"
    )

    select_id_empresa_card = (
        "TRY_CONVERT(int, c.IDEmpresa)"
        if _coluna_existe(TABELA_CARD, "IDEmpresa")
        else "CAST(NULL AS int)"
    )

    sql_qtd_antes = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD_NEGOCIACAO_PRECO}
        WHERE IDFatoKanbanCard = :id_card;
    """)

    qtd_antes = int(
        db.session.execute(
            sql_qtd_antes,
            {"id_card": id_card_int},
        ).scalar()
        or 0
    )

    sql_insert = text(f"""
        ;WITH BaseOperacional AS (
            SELECT
                pf.IDFatoKanbanCardPainelFace,
                TRY_CONVERT(int, pf.IDFatoKanbanCard) AS IDFatoKanbanCard,
                TRY_CONVERT(int, pf.IDDimPaineisEuromidia) AS IDDimPaineisEuromidia,
                TRY_CONVERT(int, pf.IDDimFacesPaineis) AS IDDimFacesPaineis,
                TRY_CONVERT(int, pf.IDDimTabelaPrecosEuromidia) AS IDDimTabelaPrecosEuromidia,

                TRY_CONVERT(decimal(19, 2), pf.CustoTabela) AS CustoAtual,
                TRY_CONVERT(decimal(19, 2), pf.ValorTabela) AS PrecoAtual,

                TRY_CONVERT(decimal(19, 2), pf.NovoValor) AS NovoValor,
                TRY_CONVERT(float, pf.PercentualDesconto) AS PercentualDesconto,
                TRY_CONVERT(decimal(19, 2), pf.ValorVendaFinal) AS ValorVendaFinal,

                TRY_CONVERT(date, pf.DataInicio) AS PeriodoInicio,
                TRY_CONVERT(date, pf.DataFim) AS PeriodoTermino,

                {select_id_contrato} AS IDFatoControleContratosEuromidia,
                {select_bit_aditivo} AS BitAditivoContrato,
                COALESCE(:id_fase_atual, {select_id_fase_card}) AS IDDimKanbanFase,
                COALESCE(:id_empresa_relacionada, {select_id_empresa_card}) AS IDEmpresa
            FROM {TABELA_CARD_PAINEL_FACE} pf
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = pf.IDFatoKanbanCard
            WHERE pf.IDFatoKanbanCard = :id_card
              AND ISNULL(pf.Ativo, 1) = 1
        ),
        BaseCalculada AS (
            SELECT
                b.*,

                PrecoProposto =
                    COALESCE(
                        b.NovoValor,
                        b.ValorVendaFinal,
                        CASE
                            WHEN b.PercentualDesconto IS NOT NULL
                             AND b.PrecoAtual IS NOT NULL
                            THEN
                                TRY_CONVERT(
                                    decimal(19, 2),
                                    b.PrecoAtual * (
                                        CAST(1 AS decimal(19, 6))
                                        - (
                                            TRY_CONVERT(decimal(19, 6), b.PercentualDesconto)
                                            / CAST(100 AS decimal(19, 6))
                                        )
                                    )
                                )
                            ELSE b.PrecoAtual
                        END
                    ),

                DescontoProposto =
                    COALESCE(
                        b.PercentualDesconto,
                        CASE
                            WHEN b.PrecoAtual IS NOT NULL
                             AND b.PrecoAtual > 0
                             AND COALESCE(b.NovoValor, b.ValorVendaFinal) IS NOT NULL
                            THEN
                                TRY_CONVERT(
                                    float,
                                    (
                                        (
                                            b.PrecoAtual
                                            - COALESCE(b.NovoValor, b.ValorVendaFinal)
                                        )
                                        / b.PrecoAtual
                                    ) * 100
                                )
                            ELSE NULL
                        END
                    ),

                MargemAtual =
                    CASE
                        WHEN b.PrecoAtual IS NOT NULL
                         AND b.PrecoAtual > 0
                         AND b.CustoAtual IS NOT NULL
                        THEN
                            TRY_CONVERT(
                                decimal(19, 2),
                                ((b.PrecoAtual - b.CustoAtual) / b.PrecoAtual) * 100
                            )
                        ELSE NULL
                    END
            FROM BaseOperacional b
        ),
        BaseFinal AS (
            SELECT
                b.*,

                CustoProposto = b.CustoAtual,

                MargemProposta =
                    CASE
                        WHEN b.PrecoProposto IS NOT NULL
                         AND b.PrecoProposto > 0
                         AND b.CustoAtual IS NOT NULL
                        THEN
                            TRY_CONVERT(
                                decimal(19, 2),
                                ((b.PrecoProposto - b.CustoAtual) / b.PrecoProposto) * 100
                            )
                        ELSE NULL
                    END,

                BitAutorizacaoDiretoria =
                    CASE
                        WHEN b.CustoAtual IS NOT NULL
                         AND b.CustoAtual > 0
                         AND b.PrecoProposto IS NOT NULL
                         AND b.PrecoProposto <= (
                                b.CustoAtual
                                * (
                                    CAST(1 AS decimal(19, 6))
                                    + (
                                        TRY_CONVERT(decimal(19, 6), :percentual_limite_diretoria)
                                        / CAST(100 AS decimal(19, 6))
                                    )
                                )
                             )
                        THEN 1
                        ELSE 0
                    END
            FROM BaseCalculada b
        )
        INSERT INTO {TABELA_CARD_NEGOCIACAO_PRECO}
        (
            IDDimUsuarios,
            IDEmpresaProprietaria,
            IDDimTabelaPrecosEuromidia,
            IDEmpresa,
            IDFatoKanbanCard,
            IDDimKanbanFase,
            IDDimKanbanStatusCard,
            IDFatoControleContratosEuromidia,
            BitAditivoContrato,
            ObservacoesProposta,
            IDDimPaineisEuromidia,
            IDDimFacesPaineis,
            CustoAtual,
            PrecoAtual,
            MargemAtual,
            CustoAtualRateado,
            PrecoAtualRateado,
            MargemAtualRateado,
            DataPrecoProposto,
            CustoProposto,
            PrecoProposto,
            MargemProposta,
            CustoPropostoRateado,
            PrecoPropostoRateado,
            DescontoProposto,
            PeriodoInicio,
            PeriodoTermino,
            IDDimUsuariosAprovacaoPreco,
            DataAprovacaoPreco,
            PrecoAprovado,
            DescontoAprovado,
            ObservacoesAprovacao,
            BitAutorizacaoDiretoria,
            BitAutorizacaoCoordenador
        )
        SELECT
            :id_usuario,
            :id_empresa_proprietaria_negociacao,
            b.IDDimTabelaPrecosEuromidia,
            b.IDEmpresa,
            b.IDFatoKanbanCard,
            b.IDDimKanbanFase,
            :id_status_card,
            b.IDFatoControleContratosEuromidia,
            b.BitAditivoContrato,
            :observacoes_proposta,
            b.IDDimPaineisEuromidia,
            b.IDDimFacesPaineis,
            b.CustoAtual,
            b.PrecoAtual,
            b.MargemAtual,
            b.CustoAtual,
            b.PrecoAtual,
            b.MargemAtual,
            GETDATE(),
            b.CustoProposto,
            b.PrecoProposto,
            b.MargemProposta,
            b.CustoProposto,
            b.PrecoProposto,
            b.DescontoProposto,
            b.PeriodoInicio,
            b.PeriodoTermino,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            b.BitAutorizacaoDiretoria,
            0
        FROM BaseFinal b
        OUTER APPLY (
            SELECT TOP (1)
                np.IDFatoKanbanNegociacaoPreco,
                np.IDDimTabelaPrecosEuromidia,
                np.CustoAtual,
                np.PrecoAtual,
                np.MargemAtual,
                np.CustoProposto,
                np.PrecoProposto,
                np.MargemProposta,
                np.DescontoProposto,
                np.PeriodoInicio,
                np.PeriodoTermino
            FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
            WHERE np.IDFatoKanbanCard = b.IDFatoKanbanCard
              AND ISNULL(np.IDDimPaineisEuromidia, 0) = ISNULL(b.IDDimPaineisEuromidia, 0)
              AND ISNULL(np.IDDimFacesPaineis, 0) = ISNULL(b.IDDimFacesPaineis, 0)
            ORDER BY np.IDFatoKanbanNegociacaoPreco DESC
        ) ultima
        WHERE
            (
                b.IDDimPaineisEuromidia IS NOT NULL
                OR b.IDDimFacesPaineis IS NOT NULL
            )
            AND (
                b.CustoAtual IS NOT NULL
                OR b.PrecoAtual IS NOT NULL
                OR b.PrecoProposto IS NOT NULL
                OR b.DescontoProposto IS NOT NULL
                OR b.PeriodoInicio IS NOT NULL
                OR b.PeriodoTermino IS NOT NULL
            )
            AND (
                :forcar_novo_registro = 1
                OR ultima.IDFatoKanbanNegociacaoPreco IS NULL
                OR ISNULL(TRY_CONVERT(int, ultima.IDDimTabelaPrecosEuromidia), -1)
                    <> ISNULL(TRY_CONVERT(int, b.IDDimTabelaPrecosEuromidia), -1)
                OR ISNULL(TRY_CONVERT(decimal(19, 4), ultima.CustoAtual), -999999999.9999)
                    <> ISNULL(TRY_CONVERT(decimal(19, 4), b.CustoAtual), -999999999.9999)
                OR ISNULL(TRY_CONVERT(decimal(19, 4), ultima.PrecoAtual), -999999999.9999)
                    <> ISNULL(TRY_CONVERT(decimal(19, 4), b.PrecoAtual), -999999999.9999)
                OR ISNULL(TRY_CONVERT(decimal(19, 4), ultima.MargemAtual), -999999999.9999)
                    <> ISNULL(TRY_CONVERT(decimal(19, 4), b.MargemAtual), -999999999.9999)
                OR ISNULL(TRY_CONVERT(decimal(19, 4), ultima.CustoProposto), -999999999.9999)
                    <> ISNULL(TRY_CONVERT(decimal(19, 4), b.CustoProposto), -999999999.9999)
                OR ISNULL(TRY_CONVERT(decimal(19, 4), ultima.PrecoProposto), -999999999.9999)
                    <> ISNULL(TRY_CONVERT(decimal(19, 4), b.PrecoProposto), -999999999.9999)
                OR ISNULL(TRY_CONVERT(decimal(19, 4), ultima.MargemProposta), -999999999.9999)
                    <> ISNULL(TRY_CONVERT(decimal(19, 4), b.MargemProposta), -999999999.9999)
                OR ISNULL(TRY_CONVERT(decimal(19, 4), ultima.DescontoProposto), -999999999.9999)
                    <> ISNULL(TRY_CONVERT(decimal(19, 4), b.DescontoProposto), -999999999.9999)
                OR ISNULL(CONVERT(varchar(10), ultima.PeriodoInicio, 23), '')
                    <> ISNULL(CONVERT(varchar(10), b.PeriodoInicio, 23), '')
                OR ISNULL(CONVERT(varchar(10), ultima.PeriodoTermino, 23), '')
                    <> ISNULL(CONVERT(varchar(10), b.PeriodoTermino, 23), '')
            );
    """)

    db.session.execute(
        sql_insert,
        {
            "id_card": id_card_int,
            "id_usuario": id_usuario_int,
            "id_empresa_proprietaria_negociacao": int(id_empresa_proprietaria_negociacao),
            "id_fase_atual": id_fase_int,
            "id_empresa_relacionada": id_empresa_rel_int,
            "id_status_card": int(id_status_card) if id_status_card not in (None, "", 0) else None,
            "observacoes_proposta": str(observacoes_proposta or "").strip()[:1000] or None,
            "percentual_limite_diretoria": PERCENTUAL_LIMITE_APROVACAO_DIRETORIA_SOBRE_CUSTO,
            "forcar_novo_registro": 1 if forcar_novo_registro else 0,
        },
    )

    qtd_depois = int(
        db.session.execute(
            sql_qtd_antes,
            {"id_card": id_card_int},
        ).scalar()
        or 0
    )

    linhas_inseridas = max(qtd_depois - qtd_antes, 0)

    current_app.logger.warning(
        "NEGOCIACAO_PRECO_HISTORICO_OPERACIONAL | id_card=%s | linhas_inseridas=%s | qtd_antes=%s | qtd_depois=%s",
        id_card_int,
        linhas_inseridas,
        qtd_antes,
        qtd_depois,
    )

    return {
        "ok": True,
        "linhas_inseridas": int(linhas_inseridas),
        "qtd_antes": int(qtd_antes),
        "qtd_depois": int(qtd_depois),
        "motivo": "historico_negociacao_preco_sincronizado",
    }










def _listar_paineis_vinculados_card(id_card: int) -> list[dict[str, Any]]:
    sql = text(f"""
        SELECT
            r.IDFatoKanbanCardPainelFace,
            r.Ordem,

            IDDimPaineisEuromidia =
                COALESCE(
                    TRY_CONVERT(int, r.IDDimPaineisEuromidia),
                    TRY_CONVERT(int, f_resolvida.IDDimPaineisEuromidia)
                ),

            IDDimFacesPaineis =
                COALESCE(
                    TRY_CONVERT(int, r.IDDimFacesPaineis),
                    TRY_CONVERT(int, f_resolvida.IDDimFacesPaineis)
                ),

            CodPonto =
                COALESCE(
                    TRY_CONVERT(int, r.CodPonto),
                    TRY_CONVERT(int, f_resolvida.CodPonto),
                    TRY_CONVERT(int, p.CodPonto)
                ),

            CodFace =
                COALESCE(
                    NULLIF(LTRIM(RTRIM(r.CodFace)), ''),
                    NULLIF(LTRIM(RTRIM(f_resolvida.CodFace)), '')
                ),

            Face =
                NULLIF(LTRIM(RTRIM(f_resolvida.Face)), ''),

            TipoFace =
                NULLIF(LTRIM(RTRIM(f_resolvida.Tipo)), ''),

            TipoPainel =
                COALESCE(
                    NULLIF(LTRIM(RTRIM(r.TipoPainel)), ''),
                    NULLIF(LTRIM(RTRIM(p.Tipo)), ''),
                    NULLIF(LTRIM(RTRIM(f_resolvida.Tipo)), '')
                ),

            r.AnoCusto,
            r.CustoTabela,
            r.IDDimTabelaPrecosEuromidia,
            r.PeriodoExibicao,
            r.ExibicoesDia,
            r.ValorTabela,
            r.Tabela,
            r.PoliticaTrocas,
            r.ValorTroca,
            r.NovoValor,
            r.PercentualDesconto,
            r.ValorVendaFinal,
            r.MargemValor,
            r.MargemPercentual,

            CONVERT(varchar(10), r.DataInicio, 23) AS DataInicio,
            CONVERT(varchar(10), r.DataFim, 23) AS DataFim,

            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.QuantidadeFaces,

            rv.DataInicioReserva,
            rv.DataFimReserva
        FROM {TABELA_CARD_PAINEL_FACE} r

        OUTER APPLY (
            SELECT TOP (1)
                f.IDDimFacesPaineis,
                f.CodPonto,
                f.Face,
                f.CodFace,
                f.Tipo,
                f.IDDimPaineisEuromidia
            FROM {TABELA_DIM_FACES_PAINEIS} f
            WHERE
                (
                    TRY_CONVERT(int, r.IDDimFacesPaineis) IS NOT NULL
                    AND TRY_CONVERT(int, f.IDDimFacesPaineis) = TRY_CONVERT(int, r.IDDimFacesPaineis)
                )
                OR
                (
                    TRY_CONVERT(int, r.IDDimFacesPaineis) IS NULL
                    AND TRY_CONVERT(int, f.CodPonto) = TRY_CONVERT(int, r.CodPonto)
                    AND UPPER(LTRIM(RTRIM(ISNULL(f.CodFace, '')))) = UPPER(LTRIM(RTRIM(ISNULL(r.CodFace, ''))))
                )
            ORDER BY
                CASE
                    WHEN TRY_CONVERT(int, r.IDDimFacesPaineis) IS NOT NULL
                         AND TRY_CONVERT(int, f.IDDimFacesPaineis) = TRY_CONVERT(int, r.IDDimFacesPaineis)
                    THEN 0
                    ELSE 1
                END,
                f.IDDimFacesPaineis DESC
        ) f_resolvida

        LEFT JOIN {TABELA_DIM_PAINEIS_EUROMIDIA} p
          ON p.IDDimPaineisEuromidia = COALESCE(
                TRY_CONVERT(int, r.IDDimPaineisEuromidia),
                TRY_CONVERT(int, f_resolvida.IDDimPaineisEuromidia)
             )

        OUTER APPLY (
            SELECT TOP (1)
                CONVERT(varchar(10), fo.DataInicio, 23) AS DataInicioReserva,
                CONVERT(varchar(10), fo.DataFim, 23) AS DataFimReserva
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
            WHERE UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = UPPER(
                LTRIM(RTRIM(COALESCE(
                    NULLIF(r.CodFace, ''),
                    NULLIF(f_resolvida.CodFace, ''),
                    ''
                )))
            )
              AND fo.CanceladoEm IS NULL
              AND fo.Status IN ('ATIVO', 'RESERVADO')
              AND (
                    COALESCE(fo.Origem, '') = :origem_card
                    OR COALESCE(fo.Observacao, '') LIKE (
                        '[CARD_ID=' + CONVERT(varchar(20), :id_card) + '][COD_FACE=' +
                        UPPER(LTRIM(RTRIM(COALESCE(NULLIF(r.CodFace, ''), NULLIF(f_resolvida.CodFace, ''), '')))) + ']%'
                    )
                    OR COALESCE(fo.Observacao, '') LIKE (
                        '[KANBAN_CARD=' + CONVERT(varchar(20), :id_card) + '][COD_FACE=' +
                        UPPER(LTRIM(RTRIM(COALESCE(NULLIF(r.CodFace, ''), NULLIF(f_resolvida.CodFace, ''), '')))) + ']%'
                    )
              )
            ORDER BY fo.DataInicio DESC
        ) rv

        WHERE r.IDFatoKanbanCard = :id_card
          AND ISNULL(r.Ativo, 1) = 1

        ORDER BY
            ISNULL(r.Ordem, 0),
            r.IDFatoKanbanCardPainelFace;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "origem_card": "KANBAN",
        },
    ).mappings().all()

    return _rows_para_dicts(rows)







def _url_fallback_imagem_painel_orcamento() -> str:
    return url_for("static", filename="imagens/painel-publicitario.png")






def _normalizar_url_imagem_painel_orcamento(url_imagem: Any) -> str | None:
    texto = _normalizar_texto(url_imagem)
    if not texto:
        return None

    texto = str(texto).strip().replace("\\", "/")
    texto = re.sub(r"/+", "/", texto)

    if texto.startswith(("http://", "https://")):
        return texto

    caminho_static_url = getattr(current_app, "static_url_path", "/static") or "/static"
    caminho_static_url = "/" + caminho_static_url.strip("/")

    if texto.startswith(caminho_static_url + "/") or texto == caminho_static_url:
        return texto

    marcadores_static = (
        "/FlaskApp/app/static/",
        "/app/static/",
        "/app/app/static/",
        "/static/",
        "static/",
    )

    texto_lower = texto.lower()
    for marcador in marcadores_static:
        marcador_lower = marcador.lower()
        pos = texto_lower.find(marcador_lower)
        if pos >= 0:
            relativo = texto[pos + len(marcador):].lstrip("/")
            if relativo:
                return url_for("static", filename=relativo)

    padroes_relativos = (
        "orcamentopaineiseuromidia/",
        "imagens/",
        "painéis/",
        "paineis/",
    )

    texto_relativo = texto.lstrip("/")
    texto_relativo_lower = texto_relativo.lower()
    if any(texto_relativo_lower.startswith(prefixo) for prefixo in padroes_relativos):
        return url_for("static", filename=texto_relativo)

    if texto.startswith("/"):
        return texto

    return "/" + texto_relativo







def _montar_nome_painel_orcamento(item: dict[str, Any]) -> str:
    tipo_painel = _normalizar_texto(item.get("TipoPainel"))
    cod_ponto = _normalizar_texto(item.get("CodPonto"))
    cod_face = _normalizar_texto(item.get("CodFace"))

    partes: list[str] = []

    if tipo_painel:
        partes.append(tipo_painel)

    if cod_ponto:
        partes.append(f"Ponto {cod_ponto}")

    if cod_face:
        partes.append(f"Face {cod_face}")

    return " • ".join(partes) if partes else "Painel publicitário"


def _montar_endereco_painel_orcamento(item: dict[str, Any]) -> str:
    logradouro = _normalizar_texto(item.get("Logradouro"))
    numero = _normalizar_texto(item.get("Numero"))
    bairro = _normalizar_texto(item.get("Bairro"))
    cidade = _normalizar_texto(item.get("Cidade"))
    uf = _normalizar_texto(item.get("UF"))

    cidade_uf = "/".join([parte for parte in [cidade, uf] if parte])
    linha_1 = ", ".join([parte for parte in [logradouro, numero] if parte])
    linha_2 = " - ".join([parte for parte in [bairro, cidade_uf] if parte])

    endereco = " • ".join([parte for parte in [linha_1, linha_2] if parte])
    return endereco or "Endereço não informado"





def _url_cabecalho_orcamento() -> str:
    return url_for(
        "static",
        filename="OrcamentoPaineisEuromidia/cabecalho_orcamento.JPG",
    )


def _obter_imagens_painel_orcamento(
    *,
    id_face_painel: Any = None,
    cod_face: Any = None,
    cod_ponto: Any = None,
) -> list[dict[str, Any]]:
    id_face = int(id_face_painel or 0) if str(id_face_painel or "").strip() else None
    cod_face_norm = _normalizar_texto(cod_face).upper()
    cod_ponto_norm = _normalizar_texto(cod_ponto)

    sql = text("""
        WITH imagens_base AS (
            SELECT
                i.IDDimImagemPainel,
                i.IDDimFacesPaineis,
                i.UrlImagem,
                i.NumeroImagem,
                i.DataAtualizacao,
                i.BitAtivo,
                i.CodFace,
                i.CodPonto,
                i.BitImagemOrcamento,
                CASE
                    WHEN :id_face_painel IS NOT NULL
                         AND TRY_CONVERT(int, i.IDDimFacesPaineis) = TRY_CONVERT(int, :id_face_painel)
                    THEN 0
                    WHEN :cod_face <> ''
                         AND UPPER(LTRIM(RTRIM(COALESCE(i.CodFace, '')))) = :cod_face
                    THEN 1
                    WHEN :cod_ponto <> ''
                         AND LTRIM(RTRIM(COALESCE(i.CodPonto, ''))) = :cod_ponto
                    THEN 2
                    ELSE 9
                END AS OrdemCorrespondencia
            FROM [Integracao].[Silver].[DimImagemPainel] i
            WHERE ISNULL(i.BitAtivo, 1) = 1
              AND TRY_CONVERT(int, ISNULL(i.BitImagemOrcamento, 0)) = 1
              AND (
                    (:id_face_painel IS NOT NULL AND TRY_CONVERT(int, i.IDDimFacesPaineis) = TRY_CONVERT(int, :id_face_painel))
                 OR (:cod_face <> '' AND UPPER(LTRIM(RTRIM(COALESCE(i.CodFace, '')))) = :cod_face)
                 OR (:cod_ponto <> '' AND LTRIM(RTRIM(COALESCE(i.CodPonto, ''))) = :cod_ponto)
              )
        )
        SELECT
            IDDimImagemPainel,
            IDDimFacesPaineis,
            UrlImagem,
            NumeroImagem,
            DataAtualizacao,
            BitAtivo,
            CodFace,
            CodPonto,
            BitImagemOrcamento,
            OrdemCorrespondencia
        FROM imagens_base
        ORDER BY
            OrdemCorrespondencia ASC,
            CASE
                WHEN TRY_CONVERT(int, NumeroImagem) IS NULL THEN 999999
                ELSE TRY_CONVERT(int, NumeroImagem)
            END ASC,
            DataAtualizacao DESC,
            IDDimImagemPainel DESC
    """)

    rows = db.session.execute(
        sql,
        {
            "id_face_painel": id_face,
            "cod_face": cod_face_norm,
            "cod_ponto": cod_ponto_norm,
        },
    ).mappings().all()

    imagens: list[dict[str, Any]] = []
    urls_vistas: set[str] = set()

    for row in rows:
        registro = dict(row)
        url_imagem = _normalizar_url_imagem_painel_orcamento(registro.get("UrlImagem"))

        if not url_imagem:
            continue

        if url_imagem in urls_vistas:
            continue

        urls_vistas.add(url_imagem)

        imagens.append(
            {
                "id_imagem_painel": int(registro.get("IDDimImagemPainel") or 0) or None,
                "url": url_imagem,
                "numero_imagem": int(registro.get("NumeroImagem") or 0) or len(imagens) + 1,
                "cod_face": _normalizar_texto(registro.get("CodFace")),
                "cod_ponto": _normalizar_texto(registro.get("CodPonto")),
                "bit_imagem_orcamento": int(registro.get("BitImagemOrcamento") or 0),
                "ordem_correspondencia": int(registro.get("OrdemCorrespondencia") or 9),
                "fallback": False,
            }
        )

    if imagens:
        return imagens

    return [
        {
            "id_imagem_painel": None,
            "url": _url_fallback_imagem_painel_orcamento(),
            "numero_imagem": 1,
            "cod_face": cod_face_norm,
            "cod_ponto": cod_ponto_norm,
            "bit_imagem_orcamento": None,
            "ordem_correspondencia": 99,
            "fallback": True,
        }
    ]


def _montar_orcamento_card_payload(id_card: int) -> dict[str, Any]:
    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe.get("card"), dict) else {}
    painel_faces = detalhe.get("painel_faces") if isinstance(detalhe.get("painel_faces"), list) else []

    empresa = {
        "id_empresa": int(card.get("IDEmpresaRelacionadaCard") or 0) or None,
        "razao_social": _normalizar_texto(card.get("EmpresaRazaoSocial")),
        "cnpj": _normalizar_texto(card.get("EmpresaCNPJ")),
        "cnae": _normalizar_texto(card.get("EmpresaCNAE")),
        "setor": _normalizar_texto(card.get("EmpresaSetor")),
        "classe": _normalizar_texto(card.get("EmpresaClasse")),
    }

    itens_orcamento: list[dict[str, Any]] = []
    valor_total_geral = Decimal("0")

    for indice, item in enumerate(painel_faces, start=1):
        if not isinstance(item, dict):
            continue

        valor_final = _valor_decimal(item.get("ValorVendaFinal"))
        preco_venda_atual = _valor_decimal(item.get("ValorTabela"))
        valor_negociado = _valor_decimal(item.get("NovoValor"))
        percentual_desconto = _valor_decimal(item.get("PercentualDesconto"))
        exibicoes_dia = _decimal_para_float(item.get("ExibicoesDia"))

        valor_exibido = valor_final if valor_final is not None else preco_venda_atual
        origem_preco = "Valor final" if valor_final is not None else "Preço de venda atual"

        if valor_exibido is not None:
            valor_total_geral += valor_exibido

        valor_total_item = preco_venda_atual if preco_venda_atual is not None else valor_exibido
        valor_negociado_exibicao = valor_negociado if valor_negociado is not None else valor_final

        imagens = _obter_imagens_painel_orcamento(
            id_face_painel=item.get("IDDimFacesPaineis"),
            cod_face=item.get("CodFace"),
            cod_ponto=item.get("CodPonto"),
        )

        itens_orcamento.append(
            {
                "indice": indice,
                "id_item": int(item.get("IDFatoKanbanCardPainelFace") or 0) or None,
                "id_painel": int(item.get("IDDimPaineisEuromidia") or 0) or None,
                "id_face_painel": int(item.get("IDDimFacesPaineis") or 0) or None,
                "nome_painel": _montar_nome_painel_orcamento(item),
                "tipo_painel": _normalizar_texto(item.get("TipoPainel")),
                "tipo_produto": _normalizar_texto(item.get("TipoPainel")),
                "cod_ponto": _normalizar_texto(item.get("CodPonto")),
                "cod_face": _normalizar_texto(item.get("CodFace")),
                "endereco": _montar_endereco_painel_orcamento(item),
                "localizacao": _montar_endereco_painel_orcamento(item),
                "logradouro": _normalizar_texto(item.get("Logradouro")),
                "numero": _normalizar_texto(item.get("Numero")),
                "bairro": _normalizar_texto(item.get("Bairro")),
                "cidade": _normalizar_texto(item.get("Cidade")),
                "municipio": _normalizar_texto(item.get("Cidade")),
                "uf": _normalizar_texto(item.get("UF")),
                "periodo_exibicao": _normalizar_texto(item.get("PeriodoExibicao")),
                "exibicoes_dia": exibicoes_dia,
                "tabela": _normalizar_texto(item.get("Tabela")),
                "politica_trocas": _normalizar_texto(item.get("PoliticaTrocas")),
                "valor_troca": _decimal_para_float(item.get("ValorTroca")),
                "preco_venda_atual": _decimal_para_float(preco_venda_atual),
                "valor_total": _decimal_para_float(valor_total_item),
                "novo_valor": _decimal_para_float(valor_negociado),
                "valor_negociado": _decimal_para_float(valor_negociado_exibicao),
                "percentual_desconto": _decimal_para_float(percentual_desconto),
                "valor_final": _decimal_para_float(valor_final),
                "valor_exibido": _decimal_para_float(valor_exibido),
                "origem_preco": origem_preco,
                "margem_percentual": _decimal_para_float(item.get("MargemPercentual")),
                "imagens": imagens,
                "quantidade_imagens": len(imagens),
                "tem_imagem_real": any(not bool(img.get("fallback")) for img in imagens),
            }
        )

    return {
        "ok": True,
        "id_card": int(id_card),
        "titulo_card": _normalizar_texto(card.get("Titulo")) or f"Card {int(id_card)}",
        "descricao_card": _normalizar_texto(card.get("Descricao")),
        "empresa": empresa,
        "cabecalho": {
            "url": _url_cabecalho_orcamento(),
            "alt": "Cabeçalho do orçamento Euromídia",
        },
        "itens": itens_orcamento,
        "resumo": {
            "quantidade_paineis": len(itens_orcamento),
            "valor_total": float(valor_total_geral),
            "data_geracao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        },
    }






def _obter_tags_do_card(id_card: int) -> list[dict[str, Any]]:
    sql_tags = text("""
        SELECT
            ct.IDFatoKanbanCard,
            ct.IDDimKanbanTag,
            t.NomeTag,
            t.CorHex,
            t.Icone
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        JOIN [Kanban].[Silver].[DimKanbanTag] t
          ON t.IDDimKanbanTag = ct.IDDimKanbanTag
        WHERE ct.IDFatoKanbanCard = :id_card
          AND ct.RemovidoEm IS NULL
          AND t.Ativo = 1
        ORDER BY t.NomeTag ASC;
    """)
    rows = db.session.execute(sql_tags, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)



def _obter_notas_do_card(id_card: int) -> list[dict[str, Any]]:
    if not _objeto_existe(TABELA_CARD_OBSERVACOES):
        return []

    sql_notas = text(f"""
        SELECT
            o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardNota,
            o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardObservacoes,
            CASE
                WHEN TRY_CONVERT(int, o.IDDimKanbanStatusCard) = 2 THEN 'INATIVACAO'
                ELSE 'OBS'
            END AS TipoNota,
            o.Observacao AS Texto,
            o.CriadoEm,
            o.IDDimUsuarios AS CriadoPor,
            CAST(NULL AS int) AS IDEmpresa,
            o.IDEmpresaProprietaria,
            o.IDDimKanbanStatusCard,
            o.IDDimKanbanFase
        FROM {TABELA_CARD_OBSERVACOES} o
        WHERE o.IDFatoKanbanCard = :id_card
        ORDER BY o.CriadoEm DESC, o.IDFatoKanbanCardObservacoes DESC;
    """)
    rows = db.session.execute(sql_notas, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)



def _obter_painel_faces_do_card(id_card: int) -> list[dict[str, Any]]:
    return _listar_paineis_vinculados_card(int(id_card))




def _obter_card_detalhe_payload(id_card: int) -> dict[str, Any]:
   
    current_app.logger.info("KANBAN: _obter_card_detalhe_payload iniciado. id_card=%s", id_card)

    card_escopo = _obter_card_autorizado(id_card)
  

    id_kanban = int(card_escopo.get("IDDimKanban") or 0)
    

    select_id_contrato = (
        "c.IDFatoControleContratosEuromidia AS IDFatoControleContratosEuromidia,"
        if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia")
        else (
            "c.IDFatoControleContratoEuromidia AS IDFatoControleContratosEuromidia,"
            if _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia")
            else "CAST(NULL AS int) AS IDFatoControleContratosEuromidia,"
        )
    )

    select_cod_ponto_contrato = (
        "c.CodPontoContrato AS CodPontoContrato,"
        if _coluna_existe(TABELA_CARD, "CodPontoContrato")
        else "CAST(NULL AS varchar(50)) AS CodPontoContrato,"
    )

    select_cod_face_contrato = (
        "c.CodFaceContrato AS CodFaceContrato,"
        if _coluna_existe(TABELA_CARD, "CodFaceContrato")
        else "CAST(NULL AS varchar(50)) AS CodFaceContrato,"
    )

    select_bit_aditivo = (
        "c.BitAditivo AS BitAditivo,"
        if _coluna_existe(TABELA_CARD, "BitAditivo")
        else "CAST(0 AS bit) AS BitAditivo,"
    )

    select_bit_contrato_novo = (
        "c.BitContratoNovo AS BitContratoNovo,"
        if _coluna_existe(TABELA_CARD, "BitContratoNovo")
        else "CAST(0 AS bit) AS BitContratoNovo,"
    )

    select_id_empresa_agencia = (
        "c.IDEmpresaAgencia AS IDEmpresaAgencia,"
        if _coluna_existe(TABELA_CARD, "IDEmpresaAgencia")
        else "CAST(NULL AS int) AS IDEmpresaAgencia,"
    )

    select_id_empresa_bureau = (
        "c.IDEmpresaBureau AS IDEmpresaBureau,"
        if _coluna_existe(TABELA_CARD, "IDEmpresaBureau")
        else "CAST(NULL AS int) AS IDEmpresaBureau,"
    )

    select_marca = (
        "c.Marca AS Marca,"
        if _coluna_existe(TABELA_CARD, "Marca")
        else "CAST(NULL AS nvarchar(100)) AS Marca,"
    )

    select_telefone = (
        "c.Telefone AS Telefone,"
        if _coluna_existe(TABELA_CARD, "Telefone")
        else "CAST(NULL AS varchar(30)) AS Telefone,"
    )

    select_email = (
        "c.Email AS Email,"
        if _coluna_existe(TABELA_CARD, "Email")
        else "CAST(NULL AS nvarchar(200)) AS Email,"
    )

    select_id_tipo_cliente_card = (
        "c.IDDimTipoCliente AS IDDimTipoCliente,"
        if _coluna_existe(TABELA_CARD, "IDDimTipoCliente")
        else (
            "c.IDDimKanbanTipoClienteDesconto AS IDDimTipoCliente,"
            if _coluna_existe(TABELA_CARD, "IDDimKanbanTipoClienteDesconto")
            else "CAST(NULL AS int) AS IDDimTipoCliente,"
        )
    )

    select_id_dim_cnaes = (
        "c.IDDimCnaes AS IDDimCnaes,"
        if _coluna_existe(TABELA_CARD, "IDDimCnaes")
        else "CAST(NULL AS int) AS IDDimCnaes,"
    )

    select_nome_empresa = (
        "c.NomeEmpresa AS NomeEmpresa,"
        if _coluna_existe(TABELA_CARD, "NomeEmpresa")
        else "CAST(NULL AS nvarchar(200)) AS NomeEmpresa,"
    )

    sql = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.Descricao,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.VersaoConcorrencia AS VersaoConcorrencia,
            CONVERT(varchar(34), c.VersaoConcorrencia, 1) AS VersaoConcorrenciaHexSql,
            c.IDDimKanbanOrigem,
            c.IDDimKanbanMotivoEncerramento,
            c.MotivoEncerramentoObs,
            c.IDEmpresaProprietaria,
            {_sql_select_id_origem_atendimento_card('c')}
            {select_id_contrato}
            {select_cod_ponto_contrato}
            {select_cod_face_contrato}
            {select_bit_aditivo}
            {select_bit_contrato_novo}
            {select_id_empresa_agencia}
            {select_id_empresa_bureau}
            {select_marca}
            {select_telefone}
            {select_email}
            {select_id_tipo_cliente_card}
            {select_id_dim_cnaes}
            {select_nome_empresa}
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            {_sql_select_nome_usuario_relacionado_card('usuario')},
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor,
            rp.QuantidadePaineisVinculados,
            rp.QuantidadePaineisUnicos,
            rp.ValorTotalPaineis
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        {_sql_join_usuario_relacionado_card('c', 'usuario')}
        {_sql_join_resumo_paineis_card('c', 'rp')}
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.Ativo = 1;
    """)

    card = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()

    if not card:
        abort(404, "Card não encontrado")

    card_dict = dict(card)
    card_dict["QuantidadePaineisVinculados"] = int(card_dict.get("QuantidadePaineisVinculados") or 0)
    card_dict["QuantidadePaineisUnicos"] = int(card_dict.get("QuantidadePaineisUnicos") or 0)
    card_dict["ValorTotalPaineis"] = _decimal_para_float(card_dict.get("ValorTotalPaineis"))

    if not str(card_dict.get("NomeEmpresa") or "").strip():
        card_dict["NomeEmpresa"] = str(card_dict.get("EmpresaRazaoSocial") or "").strip() or None

    cnae_card = _obter_cnae_por_id(card_dict.get("IDDimCnaes"))
    if cnae_card:
        card_dict["SegmentoClasse"] = cnae_card.get("Classe")
        card_dict["SegmentoDescricao"] = cnae_card.get("Descricao")
        card_dict["SegmentoSetor"] = cnae_card.get("Setor")
    else:
        card_dict["SegmentoClasse"] = None
        card_dict["SegmentoDescricao"] = None
        card_dict["SegmentoSetor"] = None

    card_dict = _aplicar_tipo_cliente_desconto_no_card_dict(card_dict)
    card_dict = _aplicar_origem_atendimento_no_card_dict(card_dict)

    snapshot_solicitacao = _obter_ultima_solicitacao_contrato_por_card(int(id_card))
    if snapshot_solicitacao:
        if card_dict.get("IDFatoControleContratosEuromidia") in (None, "", 0):
            card_dict["IDFatoControleContratosEuromidia"] = (
                int(snapshot_solicitacao.get("IDFatoControleContratosEuromidia") or 0) or None
            )

        if not str(card_dict.get("CodPontoContrato") or "").strip():
            card_dict["CodPontoContrato"] = snapshot_solicitacao.get("SolicitacaoCodPonto")

        if not str(card_dict.get("CodFaceContrato") or "").strip():
            card_dict["CodFaceContrato"] = snapshot_solicitacao.get("SolicitacaoCodFace")

        if not str(card_dict.get("TipoSolicitacao") or "").strip():
            card_dict["TipoSolicitacao"] = str(snapshot_solicitacao.get("TipoSolicitacao") or "").strip() or None

        tipo_solicitacao_snapshot = str(card_dict.get("TipoSolicitacao") or "").strip().upper()
        if tipo_solicitacao_snapshot in {"ADITIVO", "ADITIVO DE CONTRATO"}:
            card_dict["BitAditivo"] = 1
            card_dict["BitContratoNovo"] = 0
            card_dict["tipo_contrato"] = "ADITIVO"
            card_dict["tipo_contrato_card"] = "ADITIVO"
        elif tipo_solicitacao_snapshot in {"NOVO CONTRATO", "NOVO_CONTRATO"}:
            card_dict["BitAditivo"] = 0
            card_dict["BitContratoNovo"] = 1
            card_dict["tipo_contrato"] = "NOVO_CONTRATO"
            card_dict["tipo_contrato_card"] = "NOVO_CONTRATO"
        elif card_dict.get("IDFatoControleContratosEuromidia") not in (None, "", 0):
            card_dict["tipo_contrato"] = "ADITIVO"
            card_dict["tipo_contrato_card"] = "ADITIVO"

        if snapshot_solicitacao.get("SolicitacaoPrecoVendaAtual") not in (None, ""):
            card_dict["preco_venda_atual_contrato"] = snapshot_solicitacao.get("SolicitacaoPrecoVendaAtual")

        if snapshot_solicitacao.get("SolicitacaoDataInicioPrevisto") not in (None, ""):
            card_dict["DataInicioPrevisto"] = snapshot_solicitacao.get("SolicitacaoDataInicioPrevisto")

        if snapshot_solicitacao.get("SolicitacaoDataTerminoPrevisto") not in (None, ""):
            card_dict["DataTerminoPrevisto"] = snapshot_solicitacao.get("SolicitacaoDataTerminoPrevisto")

        if snapshot_solicitacao.get("SolicitacaoIDPainelEuromidia") not in (None, "", 0):
            card_dict["IDPainelEuromidia"] = int(snapshot_solicitacao.get("SolicitacaoIDPainelEuromidia") or 0) or None

        if snapshot_solicitacao.get("SolicitacaoIDDimFacesPaineis") not in (None, "", 0):
            card_dict["IDDimFacesPaineis"] = int(snapshot_solicitacao.get("SolicitacaoIDDimFacesPaineis") or 0) or None

    valor_versao_bruta = card_dict.pop("VersaoConcorrencia", None)
    valor_versao_hex_sql = card_dict.pop("VersaoConcorrenciaHexSql", None)

    print(
        "[KANBAN][_obter_card_detalhe_payload] valor_versao_bruta=",
        repr(valor_versao_bruta),
        " tipo=",
        type(valor_versao_bruta).__name__ if valor_versao_bruta is not None else None
    )
    print(
        "[KANBAN][_obter_card_detalhe_payload] valor_versao_hex_sql=",
        repr(valor_versao_hex_sql)
    )

    current_app.logger.info(
        "KANBAN: detalhe card id=%s | versao_bruta=%r | tipo=%s | versao_hex_sql=%r",
        id_card,
        valor_versao_bruta,
        type(valor_versao_bruta).__name__ if valor_versao_bruta is not None else None,
        valor_versao_hex_sql,
    )

    versao_hex_convertida = _rowversion_para_hex(valor_versao_bruta)
    if not versao_hex_convertida:
        versao_hex_convertida = _rowversion_para_hex(valor_versao_hex_sql)

    card_dict["VersaoConcorrenciaHex"] = versao_hex_convertida
    card_dict["VersaoConcorrencia"] = versao_hex_convertida

    sql_tags = text("""
        SELECT
            ct.IDFatoKanbanCard,
            ct.IDDimKanbanTag,
            t.NomeTag,
            t.CorHex,
            t.Icone
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        JOIN [Kanban].[Silver].[DimKanbanTag] t
          ON t.IDDimKanbanTag = ct.IDDimKanbanTag
        WHERE ct.IDFatoKanbanCard = :id_card
          AND ct.RemovidoEm IS NULL
          AND t.Ativo = 1
        ORDER BY t.NomeTag ASC;
    """)
    tags = db.session.execute(sql_tags, {"id_card": int(id_card)}).mappings().all()

    if _objeto_existe(TABELA_CARD_OBSERVACOES):
        sql_notas = text(f"""
            SELECT
                o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardNota,
                o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardObservacoes,
                CASE
                    WHEN TRY_CONVERT(int, o.IDDimKanbanStatusCard) = 2 THEN 'INATIVACAO'
                    ELSE 'OBS'
                END AS TipoNota,
                o.Observacao AS Texto,
                o.CriadoEm,
                o.IDDimUsuarios AS CriadoPor,
                CAST(NULL AS int) AS IDEmpresa,
                o.IDEmpresaProprietaria,
                o.IDDimKanbanStatusCard,
                o.IDDimKanbanFase
            FROM {TABELA_CARD_OBSERVACOES} o
            WHERE o.IDFatoKanbanCard = :id_card
            ORDER BY o.CriadoEm DESC, o.IDFatoKanbanCardObservacoes DESC;
        """)
        notas = db.session.execute(sql_notas, {"id_card": int(id_card)}).mappings().all()
    else:
        notas = []

    paineis_vinculados = _listar_paineis_vinculados_card(id_card)

    snapshot_solicitacao_editavel = _obter_snapshot_solicitacao_editavel_por_card(int(id_card))
    vendedor_logado_solicitacao = _obter_vendedor_logado_reserva_kanban(int(_id_empresa_usuario_or_403()))

    retorno = {
        "ok": True,
        "card": card_dict,
        "kanban_cfg": _obter_cfg_kanban(id_kanban),
        "tags": _rows_para_dicts(tags),
        "notas": _rows_para_dicts(notas),
        "paineis_vinculados": paineis_vinculados,
        "painel_faces": paineis_vinculados,
        "painelFaces": paineis_vinculados,
        "solicitacao_snapshot_editavel": snapshot_solicitacao_editavel,
        "vendedor_logado_solicitacao": dict(vendedor_logado_solicitacao) if vendedor_logado_solicitacao else None,
    }



    return retorno



@kanban_bp.route("/atendimento", methods=["GET"])
@login_required
def atendimento_redirect():
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    sql_principal = text("""
        SELECT TOP 1 IDDimKanban
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
          AND IDEmpresaProprietaria = :id_emp
          AND BitPrincipal = 1
        ORDER BY CriadoEm DESC;
    """)
    id_kanban = db.session.execute(sql_principal, {"id_emp": id_emp}).scalar()

    if not id_kanban:
        sql_fallback = text("""
            SELECT TOP 1 IDDimKanban
            FROM [Kanban].[Silver].[DimKanban]
            WHERE Ativo = 1
              AND IDEmpresaProprietaria = :id_emp
            ORDER BY
              CASE WHEN BitPrincipal = 1 THEN 0 ELSE 1 END,
              CriadoEm DESC;
        """)
        id_kanban = db.session.execute(sql_fallback, {"id_emp": id_emp}).scalar()

    if not id_kanban:
        abort(404, "Nenhum kanban ativo encontrado para essa empresa")

    return redirect(url_for("kanban.kanban_view", id_kanban=int(id_kanban)))


@kanban_bp.route("/kanbans", methods=["GET"])
@login_required

def kanbans_lista():
    _log_debug_usuario()
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    sql = text("""
        SELECT
            IDDimKanban,
            NomeKanban,
            Descricao,
            Ativo,
            CriadoEm
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
          AND IDEmpresaProprietaria = :id_emp
        ORDER BY CriadoEm DESC;
    """)
    kanbans = db.session.execute(sql, {"id_emp": id_emp}).mappings().all()
    return render_template("kanban/kanbans_lista.html", kanbans=kanbans)


@kanban_bp.route("/<int:id_kanban>", methods=["GET"])
@login_required

def kanban_view(id_kanban: int):
    _assert_login()
    try:
        kanban = _obter_kanban_autorizado(id_kanban)
        return render_template("kanban/kanban_view.html", kanban=kanban)
    except Exception as exc:
        current_app.logger.exception("Erro no kanban_view id_kanban=%s: %s", id_kanban, exc)
        return render_template("erros/500.html"), 500


@kanban_bp.route("/<int:id_kanban>/tags", methods=["GET"])
@login_required

def kanban_tags_view(id_kanban: int):
    _assert_login()
    kanban = _obter_kanban_autorizado(id_kanban)

    sql_tags = text("""
        SELECT
            IDDimKanbanTag, NomeTag, TipoTag, CorHex, Icone, AfetaCorCard,
            PodeVendedorAplicar, PodeAdminAplicar, AplicacaoUnica, Ativo
        FROM [Kanban].[Silver].[DimKanbanTag]
        WHERE IDDimKanban = :id_kanban
        ORDER BY Ativo DESC, NomeTag ASC;
    """)
    tags = db.session.execute(sql_tags, {"id_kanban": id_kanban}).mappings().all()
    return render_template("kanban/tags.html", kanban=kanban, tags=tags)




@kanban_bp.route("/api/kanbans", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_kanbans_listar():
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    chave = _chave_cache_json(
        "kanban:api:kanbans",
        id_emp,
        _versao_empresa(id_emp),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT IDDimKanban, NomeKanban, Descricao, Ativo, CriadoEm
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
          AND IDEmpresaProprietaria = :id_emp
        ORDER BY CriadoEm DESC;
    """)
    rows = db.session.execute(sql, {"id_emp": id_emp}).mappings().all()
    payload = {"ok": True, "kanbans": _rows_para_dicts(rows)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_MEDIO)
    return jsonify(payload)








def _obter_resumo_comercial_kanban(id_kanban: int) -> dict[str, Any]:
    """
    Retorna o resumo comercial do quadro inteiro.

    Regras:
    - considera apenas cards ativos e com status visível no kanban
    - considera somente cards que estejam nas fases operacionais 1, 2, 3, 4, 5 e 6
    - não inclui cards concluídos, removidos ou posicionados fora dessas fases
    - considera apenas vínculos painel/face ativos (foto atual do card)
    - contagem de tags usa DISTINCT por card para não duplicar quando houver mais de uma linha
    - margem percentual total é calculada de forma ponderada:
      (venda_total - custo_total) / venda_total * 100
      e não pela média simples das margens linha a linha
    """
    sql = text(f"""
        ;WITH CardsBase AS (
            SELECT c.IDFatoKanbanCard
            FROM {TABELA_CARD} c
            WHERE c.IDDimKanban = :id_kanban
              AND c.Ativo = 1
              AND c.IDDimKanbanFaseAtual IN (1, 2, 3, 4, 5, 6)
              {_sql_filtro_status_card_visiveis('c')}
        ),
        TagsAtivas AS (
            SELECT DISTINCT
                ct.IDFatoKanbanCard,
                UPPER(LTRIM(RTRIM(ISNULL(t.NomeTag, '')))) AS NomeTagNormalizado
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN [Kanban].[Silver].[DimKanbanTag] t
                ON t.IDDimKanbanTag = ct.IDDimKanbanTag
            INNER JOIN CardsBase cb
                ON cb.IDFatoKanbanCard = ct.IDFatoKanbanCard
            WHERE ct.RemovidoEm IS NULL
              AND ISNULL(t.Ativo, 1) = 1
        ),
        PainelFaceAtivo AS (
            SELECT
                pf.IDFatoKanbanCard,
                CAST(ISNULL(pf.CustoTabela, 0) AS DECIMAL(18, 4)) AS ValorCustoLinha,
                CAST(
                    ISNULL(
                        pf.ValorVendaFinal,
                        ISNULL(pf.NovoValor, ISNULL(pf.ValorTabela, 0))
                    )
                    AS DECIMAL(18, 4)
                ) AS ValorVendaLinha
            FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
            INNER JOIN CardsBase cb
                ON cb.IDFatoKanbanCard = pf.IDFatoKanbanCard
            WHERE ISNULL(pf.Ativo, 1) = 1
        ),
        TotaisFinanceiros AS (
            SELECT
                CAST(ISNULL(SUM(ValorCustoLinha), 0) AS DECIMAL(18, 4)) AS ValorCustoTotal,
                CAST(ISNULL(SUM(ValorVendaLinha), 0) AS DECIMAL(18, 4)) AS ValorVendaTotal
            FROM PainelFaceAtivo
        )
        SELECT
            ISNULL((
                SELECT COUNT(DISTINCT ta.IDFatoKanbanCard)
                FROM TagsAtivas ta
                WHERE ta.NomeTagNormalizado IN ('EM ATENDIMENTO', 'ATENDIMENTO')
            ), 0) AS QuantidadeAtendimentosAtivos,
            ISNULL((
                SELECT COUNT(DISTINCT ta.IDFatoKanbanCard)
                FROM TagsAtivas ta
                WHERE ta.NomeTagNormalizado IN (
                    'APROVACAO DESCONTO',
                    'APROVAÇÃO DESCONTO',
                    'APROVACAO PRECO',
                    'APROVAÇÃO PREÇO'
                )
            ), 0) AS QuantidadeAprovacaoPreco,
            tf.ValorCustoTotal,
            tf.ValorVendaTotal,
            CAST(
                CASE
                    WHEN ISNULL(tf.ValorVendaTotal, 0) = 0 THEN 0
                    ELSE ((tf.ValorVendaTotal - tf.ValorCustoTotal) / NULLIF(tf.ValorVendaTotal, 0)) * 100
                END
                AS DECIMAL(18, 4)
            ) AS MargemPercentualTotal
        FROM TotaisFinanceiros tf;
    """)

    row = db.session.execute(sql, {"id_kanban": int(id_kanban)}).mappings().first() or {}

    return {
        "QuantidadeAtendimentosAtivos": int(row.get("QuantidadeAtendimentosAtivos") or 0),
        "QuantidadeAprovacaoPreco": int(row.get("QuantidadeAprovacaoPreco") or 0),
        "ValorCustoTotal": float(row.get("ValorCustoTotal") or 0),
        "ValorVendaTotal": float(row.get("ValorVendaTotal") or 0),
        "MargemPercentualTotal": float(row.get("MargemPercentualTotal") or 0),
    }





@kanban_bp.route("/api/kanbans/<int:id_kanban>/resumo-comercial", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_kanban_resumo_comercial(id_kanban: int):
    _assert_login()
    _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    usar_cache = not _request_pede_dado_fresco()

    chave = _chave_cache_json(
        "kanban:api:resumo-comercial",
        id_emp,
        id_kanban,
        _versao_empresa(id_emp),
        _versao_kanban(id_kanban),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return jsonify(em_cache)

    payload = {
        "ok": True,
        "resumo_comercial": _obter_resumo_comercial_kanban(id_kanban),
    }

    if usar_cache:
        _cache_json_set(chave, payload, 5)

    return jsonify(payload)




def _obter_paineis_catalogo() -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:catalogo:paineis")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    sql_paineis = text("""
        SELECT
            p.IDDimPaineisEuromidia,
            p.CodPonto,
            p.Tipo,
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.CEP,
            p.QuantidadeFaces,
            p.BitAtivo
        FROM [Integracao].[Silver].[DimPaineisEuromidia] p
        WHERE p.BitAtivo = 1
          AND p.CodPonto IS NOT NULL
          AND LTRIM(RTRIM(p.CodPonto)) <> ''
        ORDER BY p.UF ASC, p.Cidade ASC, p.Tipo ASC, p.CodPonto ASC;
    """)
    paineis = _rows_para_dicts(db.session.execute(sql_paineis).mappings().all())
    _cache_json_set(chave, paineis, TIMEOUT_CACHE_LONGO)
    return paineis



def _obter_painel_faces_catalogo() -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:catalogo:painel_faces")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    sql = text("""
        SELECT
            f.IDDimFacesPaineis,
            painel.IDDimPaineisEuromidia,
            painel.CodPonto,
            f.CodFace,
            f.Face,
            painel.Tipo,
            painel.Cidade,
            painel.UF,
            painel.Logradouro,
            painel.Bairro,
            painel.Numero,
            painel.CEP,
            painel.QuantidadeFaces,
            painel.BitAtivo
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        OUTER APPLY (
            SELECT TOP (1)
                p.IDDimPaineisEuromidia,
                p.CodPonto,
                p.Tipo,
                p.Cidade,
                p.UF,
                p.Logradouro,
                p.Bairro,
                p.Numero,
                p.CEP,
                p.QuantidadeFaces,
                p.BitAtivo
            FROM [Integracao].[Silver].[DimPaineisEuromidia] p
            WHERE ISNULL(p.BitAtivo, 1) = 1
              AND p.CodPonto IS NOT NULL
              AND LTRIM(RTRIM(p.CodPonto)) <> ''
              AND (
                    TRY_CONVERT(int, p.IDDimPaineisEuromidia) = TRY_CONVERT(int, f.IDDimPaineisEuromidia)
                    OR (
                        TRY_CONVERT(int, p.CodPonto) = TRY_CONVERT(int, f.CodPonto)
                        AND UPPER(LTRIM(RTRIM(ISNULL(p.Tipo, '')))) = UPPER(LTRIM(RTRIM(ISNULL(f.Tipo, ''))))
                    )
                  )
            ORDER BY
                CASE WHEN TRY_CONVERT(int, p.IDDimPaineisEuromidia) = TRY_CONVERT(int, f.IDDimPaineisEuromidia) THEN 0 ELSE 1 END,
                p.DataAtualizacao DESC,
                p.IDDimPaineisEuromidia DESC
        ) painel
        WHERE f.CodFace IS NOT NULL
          AND LTRIM(RTRIM(f.CodFace)) <> ''
          AND painel.IDDimPaineisEuromidia IS NOT NULL
        ORDER BY
            painel.Cidade ASC,
            painel.Tipo ASC,
            f.CodFace ASC,
            painel.Logradouro ASC;
    """)

    rows = db.session.execute(sql).mappings().all()

    resultado: list[dict[str, Any]] = []
    chaves_vistas: set[tuple[int, str]] = set()

    for row in rows:
        id_painel = int(row.get("IDDimPaineisEuromidia") or 0) if row.get("IDDimPaineisEuromidia") is not None else 0
        cod_face = _normalizar_texto(row.get("CodFace")).upper()
        if not id_painel or not cod_face:
            continue

        chave_face = (id_painel, cod_face)
        if chave_face in chaves_vistas:
            continue
        chaves_vistas.add(chave_face)

        resultado.append(
            {
                "IDDimFacesPaineis": int(row.get("IDDimFacesPaineis") or 0) if row.get("IDDimFacesPaineis") is not None else None,
                "IDDimPaineisEuromidia": id_painel,
                "CodPonto": _normalizar_texto(row.get("CodPonto")) or None,
                "CodFace": cod_face,
                "Face": _normalizar_texto(row.get("Face")) or None,
                "Tipo": _normalizar_texto(row.get("Tipo")) or None,
                "Cidade": _normalizar_texto(row.get("Cidade")) or None,
                "UF": _normalizar_texto(row.get("UF")) or None,
                "Logradouro": _normalizar_texto(row.get("Logradouro")) or None,
                "Bairro": _normalizar_texto(row.get("Bairro")) or None,
                "Numero": _normalizar_texto(row.get("Numero")) or None,
                "CEP": _normalizar_texto(row.get("CEP")) or None,
                "QuantidadeFaces": int(row.get("QuantidadeFaces") or 0) if row.get("QuantidadeFaces") is not None else None,
                "BitAtivo": int(row.get("BitAtivo") or 0) if row.get("BitAtivo") is not None else 0,
            }
        )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_LONGO)
    return resultado







@kanban_bp.route("/api/kanbans/<int:id_kanban>/dados", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_kanban_dados(id_kanban: int):
    _assert_login()
    kanban_cfg = _obter_cfg_kanban(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    try:
        limite_inicial_por_fase = int(request.args.get("limit_inicial") or 8)
    except Exception:
        return jsonify({"ok": False, "msg": "limit_inicial inválido"}), 400

    limite_inicial_por_fase = max(1, min(limite_inicial_por_fase, LIMITE_CARDS_POR_FASE))
    usar_cache = not _request_pede_dado_fresco()

    chave = _chave_cache_json(
        "kanban:api:dados",
        id_emp,
        id_kanban,
        limite_inicial_por_fase,
        _versao_empresa(id_emp),
        _versao_kanban(id_kanban),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return jsonify(em_cache)

    fases_base = _obter_fases_kanban(id_kanban)
    tags_catalogo = _obter_tags_kanban(id_kanban)
    vendedores_catalogo = _obter_vendedores_kanban(id_kanban)
    tipos_cliente_desconto_catalogo = _obter_tipos_cliente_desconto()
    origens_atendimento_catalogo = _obter_origens_atendimento()
    paineis_catalogo = _obter_paineis_catalogo() if kanban_cfg["MostrarPainelFaceNoCard"] else []

    sql_totais = text(f"""
        SELECT
            c.IDDimKanbanFaseAtual AS IDDimKanbanFase,
            COUNT(1) AS QuantidadeCardsTotal
        FROM {TABELA_CARD} c
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          AND NOT EXISTS (
                SELECT 1
                FROM {TABELA_KANBAN_FASE} f_final
                WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                  AND f_final.IDDimKanban = c.IDDimKanban
                  AND ISNULL(f_final.Ativo, 1) = 1
                  AND (
                        f_final.NomeFase = 'concluido'
                     OR f_final.TipoFase = 'SUCESSO'
                  )
            )
        GROUP BY c.IDDimKanbanFaseAtual;
    """)
    rows_totais = db.session.execute(sql_totais, {"id_kanban": id_kanban}).mappings().all()
    mapa_totais_por_fase = {
        int(r["IDDimKanbanFase"]): int(r["QuantidadeCardsTotal"] or 0)
        for r in rows_totais
        if r.get("IDDimKanbanFase") is not None
    }

    sql_cards_iniciais = text(f"""
        ;WITH CardsOrdenados AS (
            SELECT
                c.IDFatoKanbanCard,
                c.IDDimKanban,
                c.IDDimKanbanFaseAtual,
                c.Titulo,
                c.StatusCard,
                c.CriadoEm,
                c.AtualizadoEm,
                {_sql_select_versao_concorrencia_card('c')},
                c.IDEmpresaProprietaria,
                {_sql_select_id_origem_atendimento_card('c')}
                {_sql_select_empresa_relacionada_card('c')},
                {_sql_select_usuario_relacionado_card('c')},
                {_sql_select_nome_usuario_relacionado_card('usuario')},
                e.RazaoSocial AS EmpresaRazaoSocial,
                e.CNPJ AS EmpresaCNPJ,
                e.CNAE AS EmpresaCNAE,
                cn.Classe AS EmpresaClasse,
                cn.Setor AS EmpresaSetor,
                rp.QuantidadePaineisVinculados,
                rp.QuantidadePaineisUnicos,
                rp.ValorTotalPaineis,
                ROW_NUMBER() OVER (
                    PARTITION BY c.IDDimKanbanFaseAtual
                    ORDER BY
                        CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
                        c.IDFatoKanbanCard DESC
                ) AS RowNumFase
            FROM {TABELA_CARD} c
            {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
            {_sql_join_usuario_relacionado_card('c', 'usuario')}
            {_sql_join_resumo_paineis_card('c', 'rp')}
            WHERE c.IDDimKanban = :id_kanban
              AND c.Ativo = 1
              {_sql_filtro_status_card_visiveis('c')}
              AND NOT EXISTS (
                    SELECT 1
                    FROM {TABELA_KANBAN_FASE} f_final
                    WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                      AND f_final.IDDimKanban = c.IDDimKanban
                      AND ISNULL(f_final.Ativo, 1) = 1
                      AND (
                            f_final.NomeFase = 'concluido'
                         OR f_final.TipoFase = 'SUCESSO'
                      )
                )
        )
        SELECT
            IDFatoKanbanCard,
            IDDimKanban,
            IDDimKanbanFaseAtual,
            Titulo,
            StatusCard,
            CriadoEm,
            AtualizadoEm,
            VersaoConcorrencia,
            IDEmpresaProprietaria,
            IDDimOrigemAtendimento,
            IDEmpresaRelacionadaCard,
            IDUsuarioRelacionadoCard,
            NomeUsuarioResponsavel,
            EmpresaRazaoSocial,
            EmpresaCNPJ,
            EmpresaCNAE,
            EmpresaClasse,
            EmpresaSetor,
            QuantidadePaineisVinculados,
            QuantidadePaineisUnicos,
            ValorTotalPaineis,
            RowNumFase
        FROM CardsOrdenados
        WHERE RowNumFase <= :limite_inicial_por_fase
        ORDER BY IDDimKanbanFaseAtual ASC, RowNumFase ASC, IDFatoKanbanCard DESC;
    """)

    rows_cards_iniciais = db.session.execute(
        sql_cards_iniciais,
        {
            "id_kanban": id_kanban,
            "limite_inicial_por_fase": limite_inicial_por_fase,
        },
    ).mappings().all()

    cards_iniciais: list[dict[str, Any]] = []
    mapa_carregados_por_fase: dict[int, int] = {}
    ids_cards_iniciais: list[int] = []

    for row in rows_cards_iniciais:
        card_dict = dict(row)

        card_dict["VersaoConcorrenciaHex"] = _rowversion_para_hex(
            card_dict.pop("VersaoConcorrencia", None)
        )
        card_dict["QuantidadePaineisVinculados"] = int(card_dict.get("QuantidadePaineisVinculados") or 0)
        card_dict["QuantidadePaineisUnicos"] = int(card_dict.get("QuantidadePaineisUnicos") or 0)
        card_dict["ValorTotalPaineis"] = _decimal_para_float(card_dict.get("ValorTotalPaineis"))

        card_dict = _aplicar_tipo_cliente_desconto_no_card_dict(card_dict)
        card_dict = _aplicar_origem_atendimento_no_card_dict(card_dict)

        cards_iniciais.append(card_dict)

        id_card = int(card_dict.get("IDFatoKanbanCard") or 0)
        if id_card:
            ids_cards_iniciais.append(id_card)

        id_fase_card = int(card_dict.get("IDDimKanbanFaseAtual") or 0)
        if id_fase_card:
            mapa_carregados_por_fase[id_fase_card] = int(mapa_carregados_por_fase.get(id_fase_card, 0)) + 1

    card_tags_iniciais: list[dict[str, Any]] = []

    if ids_cards_iniciais:
        placeholders = ", ".join(f":id_card_{idx}" for idx, _ in enumerate(ids_cards_iniciais))
        params_card_tags = {
            f"id_card_{idx}": int(id_card)
            for idx, id_card in enumerate(ids_cards_iniciais)
        }

        sql_card_tags = text(f"""
            SELECT
                ct.IDFatoKanbanCard,
                ct.IDDimKanbanTag,
                t.NomeTag,
                t.CorHex,
                t.Icone
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            JOIN [Kanban].[Silver].[DimKanbanTag] t
              ON t.IDDimKanbanTag = ct.IDDimKanbanTag
            WHERE ct.RemovidoEm IS NULL
              AND t.Ativo = 1
              AND ct.IDFatoKanbanCard IN ({placeholders})
            ORDER BY ct.IDFatoKanbanCard ASC, t.NomeTag ASC;
        """)
        card_tags_iniciais = _rows_para_dicts(
            db.session.execute(sql_card_tags, params_card_tags).mappings().all()
        )

    fases_payload: list[dict[str, Any]] = []

    for fase in fases_base:
        fase_dict = dict(fase)
        id_fase = int(fase_dict.get("IDDimKanbanFase") or 0)

        quantidade_total = int(mapa_totais_por_fase.get(id_fase, 0))
        quantidade_carregada = int(mapa_carregados_por_fase.get(id_fase, 0))

        fase_dict["QuantidadeCardsTotal"] = quantidade_total
        fase_dict["QuantidadeCardsCarregadosInicialmente"] = quantidade_carregada
        fase_dict["QuantidadeCardsRestantes"] = max(0, quantidade_total - quantidade_carregada)
        fase_dict["CargaInicialCompleta"] = quantidade_carregada >= quantidade_total

        fases_payload.append(fase_dict)

    payload = {
        "ok": True,
        "kanban_cfg": dict(kanban_cfg),
        "fases": fases_payload,
        "cards": cards_iniciais,
        "tags": tags_catalogo,
        "vendedores": vendedores_catalogo,
        "tipos_cliente_desconto": tipos_cliente_desconto_catalogo,
        "origens_atendimento": origens_atendimento_catalogo,
        "card_tags": card_tags_iniciais,
        "paineis": paineis_catalogo,
        "resumo_comercial": _obter_resumo_comercial_kanban(id_kanban),
        "limit_inicial_por_fase": limite_inicial_por_fase,
        "carga_parcial": True,
    }

    if usar_cache:
        _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

    return jsonify(payload)





@kanban_bp.route("/api/kanbans/<int:id_kanban>/fases", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_fases_listar(id_kanban: int):
    _assert_login()
    _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    chave = _chave_cache_json(
        "kanban:api:fases",
        id_emp,
        id_kanban,
        _versao_kanban(id_kanban),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    payload = {"ok": True, "fases": _obter_fases_kanban(id_kanban)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)
    return jsonify(payload)





@kanban_bp.route("/api/kanbans/<int:id_kanban>/cards", methods=["GET"])
@login_required
@limiter.limit("180/minute")
def api_cards_listar_por_fase(id_kanban: int):
    _assert_login()
    _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    try:
        try:
            id_fase = int(request.args.get("id_fase") or 0)
        except Exception:
            return jsonify({"ok": False, "msg": "id_fase inválido"}), 400

        try:
            offset = max(int(request.args.get("offset") or 0), 0)
        except Exception:
            return jsonify({"ok": False, "msg": "offset inválido"}), 400

        try:
            limit = int(request.args.get("limit") or 30)
        except Exception:
            return jsonify({"ok": False, "msg": "limit inválido"}), 400

        limit = max(1, min(limit, LIMITE_CARDS_POR_FASE))
        row_inicio = offset
        row_fim = offset + limit

        if not id_fase or not _validar_fase_do_kanban(id_kanban, id_fase):
            return jsonify({"ok": False, "msg": "Fase inválida para este kanban"}), 400

        def _json_resposta(payload: dict, no_cache_http: bool = False):
            resposta = jsonify(payload)

            if no_cache_http:
                resposta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                resposta.headers["Pragma"] = "no-cache"
                resposta.headers["Expires"] = "0"

            return resposta

        chave = _chave_cache_json(
            "kanban:api:cards:fase",
            id_emp,
            id_kanban,
            id_fase,
            offset,
            limit,
            _versao_kanban(id_kanban),
        )

        usar_cache = not _request_pede_dado_fresco()

        if usar_cache:
            em_cache = _cache_json_get(chave)
            if em_cache is not None:
                return _json_resposta(em_cache)

        sql_total = text(f"""
            SELECT COUNT(1)
            FROM {TABELA_CARD} c
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDDimKanbanFaseAtual = :id_fase
              AND c.Ativo = 1
              {_sql_filtro_status_card_visiveis('c')}
              AND NOT EXISTS (
                    SELECT 1
                    FROM {TABELA_KANBAN_FASE} f_final
                    WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                      AND f_final.IDDimKanban = c.IDDimKanban
                      AND ISNULL(f_final.Ativo, 1) = 1
                      AND (
                            f_final.NomeFase = 'concluido'
                         OR f_final.TipoFase = 'SUCESSO'
                      )
                );
        """)

        total = int(
            db.session.execute(
                sql_total,
                {
                    "id_kanban": id_kanban,
                    "id_fase": id_fase,
                },
            ).scalar() or 0
        )

        sql_cards = text(f"""
            ;WITH CardsPaginados AS (
                SELECT
                    c.IDFatoKanbanCard,
                    c.IDDimKanban,
                    c.IDDimKanbanFaseAtual,
                    c.Titulo,
                    c.StatusCard,
                    c.CriadoEm,
                    c.AtualizadoEm,
                    {_sql_select_versao_concorrencia_card('c')},
                    c.IDEmpresaProprietaria,
                    {_sql_select_id_origem_atendimento_card('c')}
                    {_sql_select_empresa_relacionada_card('c')},
                    {_sql_select_usuario_relacionado_card('c')},
                    {_sql_select_nome_usuario_relacionado_card('usuario')},
                    e.RazaoSocial AS EmpresaRazaoSocial,
                    e.CNPJ AS EmpresaCNPJ,
                    e.CNAE AS EmpresaCNAE,
                    cn.Classe AS EmpresaClasse,
                    cn.Setor AS EmpresaSetor,
                    rp.QuantidadePaineisVinculados,
                    rp.QuantidadePaineisUnicos,
                    rp.ValorTotalPaineis,
                    ROW_NUMBER() OVER (
                        ORDER BY
                            CASE
                                WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm
                                ELSE c.AtualizadoEm
                            END DESC,
                            c.IDFatoKanbanCard DESC
                    ) AS RowNumGlobal
                FROM {TABELA_CARD} c
                {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
                {_sql_join_usuario_relacionado_card('c', 'usuario')}
                {_sql_join_resumo_paineis_card('c', 'rp')}
                WHERE c.IDDimKanban = :id_kanban
                  AND c.IDDimKanbanFaseAtual = :id_fase
                  AND c.Ativo = 1
                  {_sql_filtro_status_card_visiveis('c')}
                  AND NOT EXISTS (
                        SELECT 1
                        FROM {TABELA_KANBAN_FASE} f_final
                        WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                          AND f_final.IDDimKanban = c.IDDimKanban
                          AND ISNULL(f_final.Ativo, 1) = 1
                          AND (
                                f_final.NomeFase = 'concluido'
                             OR f_final.TipoFase = 'SUCESSO'
                          )
                    )
            )
            SELECT
                IDFatoKanbanCard,
                IDDimKanban,
                IDDimKanbanFaseAtual,
                Titulo,
                StatusCard,
                CriadoEm,
                AtualizadoEm,
                VersaoConcorrencia,
                IDEmpresaProprietaria,
                IDDimOrigemAtendimento,
                IDEmpresaRelacionadaCard,
                IDUsuarioRelacionadoCard,
                NomeUsuarioResponsavel,
                EmpresaRazaoSocial,
                EmpresaCNPJ,
                EmpresaCNAE,
                EmpresaClasse,
                EmpresaSetor,
                QuantidadePaineisVinculados,
                QuantidadePaineisUnicos,
                ValorTotalPaineis
            FROM CardsPaginados
            WHERE RowNumGlobal > :row_inicio
              AND RowNumGlobal <= :row_fim
            ORDER BY RowNumGlobal ASC;
        """)

        rows_cards = db.session.execute(
            sql_cards,
            {
                "id_kanban": id_kanban,
                "id_fase": id_fase,
                "row_inicio": row_inicio,
                "row_fim": row_fim,
            },
        ).mappings().all()

        cards: list[dict[str, Any]] = []
        ids_cards_lote: list[int] = []

        for row in rows_cards:
            try:
                card = dict(row)
                card["VersaoConcorrenciaHex"] = _rowversion_para_hex(
                    card.pop("VersaoConcorrencia", None)
                )
                card["QuantidadePaineisVinculados"] = int(card.get("QuantidadePaineisVinculados") or 0)
                card["QuantidadePaineisUnicos"] = int(card.get("QuantidadePaineisUnicos") or 0)
                card["ValorTotalPaineis"] = _decimal_para_float(card.get("ValorTotalPaineis"))
                card = _aplicar_tipo_cliente_desconto_no_card_dict(card)
                card = _aplicar_origem_atendimento_no_card_dict(card)
                cards.append(card)

                id_card_lote = int(card.get("IDFatoKanbanCard") or 0)
                if id_card_lote > 0:
                    ids_cards_lote.append(id_card_lote)
            except Exception:
                current_app.logger.exception(
                    "Erro ao normalizar card paginado do kanban | id_kanban=%s | id_fase=%s | row=%s",
                    id_kanban,
                    id_fase,
                    dict(row),
                )

        card_tags_lote: list[dict[str, Any]] = []
        if ids_cards_lote:
            ids_cards_lote = list(dict.fromkeys(ids_cards_lote))
            placeholders_ids_cards, parametros_ids_cards = _montar_placeholders_sql("id_card_lote", ids_cards_lote)

            tem_afeta_cor_card = _coluna_existe("[Kanban].[Silver].[DimKanbanTag]", "AfetaCorCard")
            tem_ordem_exibicao = _coluna_existe("[Kanban].[Silver].[DimKanbanTag]", "OrdemExibicao")

            select_afeta_cor_card = (
                "t.AfetaCorCard AS AfetaCorCard,"
                if tem_afeta_cor_card
                else "CAST(0 AS bit) AS AfetaCorCard,"
            )
            select_ordem_exibicao = (
                "t.OrdemExibicao AS OrdemExibicao"
                if tem_ordem_exibicao
                else "CAST(NULL AS int) AS OrdemExibicao"
            )
            order_by_ordem_exibicao = (
                "ISNULL(t.OrdemExibicao, 999999) ASC,"
                if tem_ordem_exibicao
                else ""
            )

            sql_card_tags_lote = text(f"""
                SELECT
                    ct.IDFatoKanbanCard,
                    t.IDDimKanbanTag,
                    t.NomeTag,
                    t.CorHex,
                    {select_afeta_cor_card}
                    {select_ordem_exibicao}
                FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
                INNER JOIN [Kanban].[Silver].[DimKanbanTag] t
                    ON t.IDDimKanbanTag = ct.IDDimKanbanTag
                WHERE ct.IDFatoKanbanCard IN ({placeholders_ids_cards})
                  AND ct.RemovidoEm IS NULL
                  AND ISNULL(t.Ativo, 1) = 1
                ORDER BY
                    ct.IDFatoKanbanCard ASC,
                    {order_by_ordem_exibicao}
                    t.NomeTag ASC,
                    t.IDDimKanbanTag ASC;
            """)

            try:
                card_tags_lote = _rows_para_dicts(
                    db.session.execute(sql_card_tags_lote, parametros_ids_cards).mappings().all()
                )
            except Exception:
                current_app.logger.exception(
                    "Erro ao carregar tags do lote paginado do kanban | id_kanban=%s | id_fase=%s | ids_cards=%s",
                    id_kanban,
                    id_fase,
                    ids_cards_lote,
                )
                card_tags_lote = []

        payload = {
            "ok": True,
            "id_kanban": id_kanban,
            "id_fase": id_fase,
            "offset": offset,
            "limit": limit,
            "total": total,
            "cards": cards,
            "card_tags": card_tags_lote,
        }

        if usar_cache:
            _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

        return _json_resposta(payload, no_cache_http=not usar_cache)

    except Exception as exc:
        current_app.logger.exception(
            "Erro ao listar cards por fase no kanban | id_kanban=%s | id_fase=%s | offset=%s | limit=%s",
            id_kanban,
            request.args.get("id_fase"),
            request.args.get("offset"),
            request.args.get("limit"),
        )
        return jsonify(
            {
                "ok": False,
                "msg": f"Erro ao carregar os cards da fase: {str(exc)}"
            }
        ), 500




@kanban_bp.route("/api/cards/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("180/minute")
def api_card_detalhe(id_card: int):
    _assert_login()

    id_emp = _id_empresa_usuario_or_403()
    card_escopo = _obter_card_autorizado(id_card)
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    usar_cache = not _request_pede_dado_fresco()

    chave = _chave_cache_json(
        "kanban:api:card:detalhe",
        id_emp,
        id_kanban,
        id_card,
        _versao_kanban(id_kanban),
        _versao_card(id_card),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return jsonify(em_cache)

    payload = _obter_card_detalhe_payload(id_card)

    if not isinstance(payload, dict):
        payload = {"ok": True}

    card_payload = payload.get("card")
    if not isinstance(card_payload, dict):
        card_payload = {}

    campos_escopo_para_reidratar = (
        "IDFatoKanbanCard",
        "IDDimKanban",
        "IDDimKanbanFaseAtual",
        "IDFatoControleContratosEuromidia",
        "CodPontoContrato",
        "CodFaceContrato",
        "BitAditivo",
        "BitContratoNovo",
        "IDEmpresaAgencia",
        "Marca",
        "Telefone",
        "Email",
        "IDEmpresaRelacionadaCard",
        "EmpresaRazaoSocial",
        "EmpresaCNPJ",
        "Titulo",
        "Descricao",
        "StatusCard",
    )

    for chave_campo in campos_escopo_para_reidratar:
        if chave_campo in card_escopo and card_escopo.get(chave_campo) is not None:
            card_payload[chave_campo] = card_escopo.get(chave_campo)

    if card_payload.get("tipo_contrato") in (None, ""):
        if int(card_payload.get("BitAditivo") or 0) == 1:
            card_payload["tipo_contrato"] = TIPO_SOLICITACAO_ADITIVO
        elif int(card_payload.get("BitContratoNovo") or 0) == 1:
            card_payload["tipo_contrato"] = TIPO_SOLICITACAO_NOVO

    versao_hex = (
        card_payload.get("VersaoConcorrenciaHex")
        or card_payload.get("versao_concorrencia")
        or card_payload.get("versaoConcorrencia")
        or card_payload.get("VersaoConcorrencia")
    )

    versao_hex = _normalizar_hex_sql(versao_hex)

    card_payload["VersaoConcorrenciaHex"] = versao_hex
    card_payload["VersaoConcorrencia"] = versao_hex
    card_payload["versaoConcorrencia"] = versao_hex
    card_payload["versao_concorrencia"] = versao_hex

    if card_payload.get("IDFatoKanbanCard") is None:
        card_payload["IDFatoKanbanCard"] = int(id_card)

    tags_payload_tipo = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    tem_tag_aditivo_payload = any(
        int((tag or {}).get("IDDimKanbanTag") or 0) == ID_TAG_TIPO_CONTRATO_ADITIVO
        or _normalizar_texto_comparacao((tag or {}).get("NomeTag")) == "aditivo"
        for tag in tags_payload_tipo
        if isinstance(tag, dict)
    )
    tem_tag_novo_payload = any(
        int((tag or {}).get("IDDimKanbanTag") or 0) == ID_TAG_TIPO_CONTRATO_NOVO
        or _normalizar_texto_comparacao((tag or {}).get("NomeTag")) == "novo contrato"
        for tag in tags_payload_tipo
        if isinstance(tag, dict)
    )

    id_contrato_payload_tipo = _int_ou_none(
        card_payload.get("IDFatoControleContratosEuromidia")
        or card_payload.get("IDFatoControleContratoEuromidia")
    )

    if tem_tag_aditivo_payload:
        card_payload["tipo_contrato"] = TIPO_SOLICITACAO_ADITIVO
        card_payload["tipo_contrato_card"] = TIPO_SOLICITACAO_ADITIVO
        card_payload["BitAditivo"] = 1
        card_payload["BitContratoNovo"] = 0
    elif id_contrato_payload_tipo and not tem_tag_novo_payload:
        card_payload["tipo_contrato"] = TIPO_SOLICITACAO_ADITIVO
        card_payload["tipo_contrato_card"] = TIPO_SOLICITACAO_ADITIVO
        card_payload["BitAditivo"] = 1
        card_payload["BitContratoNovo"] = 0
    elif tem_tag_novo_payload and not id_contrato_payload_tipo:
        card_payload["tipo_contrato"] = TIPO_SOLICITACAO_NOVO
        card_payload["tipo_contrato_card"] = TIPO_SOLICITACAO_NOVO
        card_payload["BitAditivo"] = 0
        card_payload["BitContratoNovo"] = 1

    payload["card"] = card_payload

    if "painel_faces" not in payload:
        payload["painel_faces"] = payload.get("paineis_vinculados", []) or []

    if "painelFaces" not in payload:
        payload["painelFaces"] = payload.get("painel_faces", []) or []

    if usar_cache:
        _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

    return jsonify(payload)






@kanban_bp.route("/api/cards/<int:id_card>/orcamento", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_card_orcamento(id_card: int):
    _assert_login()

    try:
        id_emp = _id_empresa_usuario_or_403()
        card_escopo = _obter_card_autorizado(id_card)
        id_kanban = int(card_escopo.get("IDDimKanban") or 0)

        usar_cache = not _request_pede_dado_fresco()

        chave = _chave_cache_json(
            "kanban:api:card:orcamento",
            id_emp,
            id_kanban,
            id_card,
            _versao_kanban(id_kanban),
            _versao_card(id_card),
        )

        if usar_cache:
            em_cache = _cache_json_get(chave)
            if em_cache is not None:
                return jsonify(em_cache)

        payload = _montar_orcamento_card_payload(id_card)

        if usar_cache:
            _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

        return jsonify(payload)

    except Exception as exc:
        current_app.logger.exception(
            "Erro ao gerar orçamento do card id_card=%s: %s",
            id_card,
            exc,
        )
        return jsonify(
            {
                "ok": False,
                "msg": "Não foi possível gerar o orçamento deste card."
            }
        ), 500






@kanban_bp.route("/api/empresas", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_empresas_lista():
    _assert_login()

    chave = _chave_cache_json("kanban:api:empresas:lista:global")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text(f"""
        SELECT TOP 500
            e.IDEmpresa,
            e.IDEmpresaProprietaria,
            e.RazaoSocial,
            e.NomeFantasia,
            e.CNPJ,
            e.CNAE,
            e.BitCliente,
            e.BitClienteDireto,
            e.IDDimOrigemAtendimento,
            cn.Classe,
            cn.Setor
        FROM {TABELA_EMPRESAS} e
        LEFT JOIN {TABELA_CNAES} cn
          ON cn.cnaepadrao = e.CNAE
        WHERE e.RazaoSocial IS NOT NULL
          AND e.RazaoSocial <> ''
        ORDER BY e.RazaoSocial ASC;
    """)

    rows = db.session.execute(sql).mappings().all()
    payload = {"ok": True, "empresas": _rows_para_dicts(rows)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_LONGO)
    return jsonify(payload)





@kanban_bp.route("/api/empresas/buscar", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_empresas_buscar():
    _assert_login()

    q = (request.args.get("q") or "").strip()
    q_digits = "".join([c for c in q if c.isdigit()])

    if not q and not q_digits:
        sql = text(f"""
            SELECT TOP 80
                e.IDEmpresa,
                e.IDEmpresaProprietaria,
                e.RazaoSocial,
                e.NomeFantasia,
                e.CNPJ,
                e.CNAE,
                e.BitCliente,
                e.BitClienteDireto,
                e.IDDimOrigemAtendimento,
                c.Classe,
                c.Setor
            FROM {TABELA_EMPRESAS} e
            LEFT JOIN {TABELA_CNAES} c
              ON c.cnaepadrao = e.CNAE
            WHERE e.RazaoSocial IS NOT NULL
              AND e.RazaoSocial <> ''
            ORDER BY e.RazaoSocial ASC;
        """)

        empresas = db.session.execute(sql).mappings().all()
        return jsonify({"ok": True, "empresas": _rows_para_dicts(empresas)})

    if len(q) < 2 and len(q_digits) < 4:
        return jsonify({"ok": True, "empresas": []})

    chave = _chave_cache_json("kanban:api:empresas:buscar:global", q.lower(), q_digits)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text(f"""
        SELECT TOP 80
            e.IDEmpresa,
            e.IDEmpresaProprietaria,
            e.RazaoSocial,
            e.NomeFantasia,
            e.CNPJ,
            e.CNAE,
            e.BitCliente,
            e.BitClienteDireto,
            e.IDDimOrigemAtendimento,
            c.Classe,
            c.Setor
        FROM {TABELA_EMPRESAS} e
        LEFT JOIN {TABELA_CNAES} c
          ON c.cnaepadrao = e.CNAE
        WHERE e.RazaoSocial IS NOT NULL
          AND LTRIM(RTRIM(e.RazaoSocial)) <> ''
          AND (
                e.RazaoSocial LIKE :q_like
             OR ISNULL(e.NomeFantasia, '') LIKE :q_like
             OR e.CNPJ LIKE :q_like
             OR (
                    :q_digits <> ''
                    AND REPLACE(REPLACE(REPLACE(REPLACE(ISNULL(e.CNPJ, ''), '.', ''), '/', ''), '-', ''), ' ', '')
                        LIKE :q_digits_like
                )
          )
        ORDER BY
            CASE
                WHEN e.RazaoSocial LIKE :q_like_inicio THEN 0
                WHEN ISNULL(e.NomeFantasia, '') LIKE :q_like_inicio THEN 1
                WHEN :q_digits <> '' AND REPLACE(REPLACE(REPLACE(REPLACE(ISNULL(e.CNPJ, ''), '.', ''), '/', ''), '-', ''), ' ', '') LIKE :q_digits_inicio THEN 2
                ELSE 3
            END,
            e.RazaoSocial ASC;
    """)

    empresas = db.session.execute(
        sql,
        {
            "q_like": f"%{q}%",
            "q_like_inicio": f"{q}%",
            "q_digits": q_digits,
            "q_digits_like": f"%{q_digits}%",
            "q_digits_inicio": f"{q_digits}%",
        },
    ).mappings().all()

    payload = {"ok": True, "empresas": _rows_para_dicts(empresas)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_MEDIO)
    return jsonify(payload)





@kanban_bp.route("/api/cnaes/buscar", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_cnaes_buscar():
    _assert_login()

    q = (request.args.get("q") or "").strip()

    if not q:
        sql = text(f"""
            SELECT TOP 80
                IDDimCnaes,
                cnaepadrao,
                Descricao,
                Classe,
                Setor,
                MacroSetor,
                SubClasse
            FROM {TABELA_CNAES}
            ORDER BY
                CASE WHEN Classe = '' THEN 1 ELSE 0 END,
                Classe ASC,
                Descricao ASC;
        """)
        rows = db.session.execute(sql).mappings().all()
        return jsonify({"ok": True, "cnaes": _rows_para_dicts(rows)})

    if len(q) < 2:
        return jsonify({"ok": True, "cnaes": []})

    chave = _chave_cache_json("kanban:api:cnaes:buscar", q.lower())
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text(f"""
        SELECT TOP 80
            IDDimCnaes,
            cnaepadrao,
            Descricao,
            Classe,
            Setor,
            MacroSetor,
            SubClasse
        FROM {TABELA_CNAES}
        WHERE
            ISNULL(Classe, '') LIKE :q_like
            OR ISNULL(Descricao, '') LIKE :q_like
            OR ISNULL(Setor, '') LIKE :q_like
            OR ISNULL(SubClasse, '') LIKE :q_like
            OR ISNULL(cnaepadrao, '') LIKE :q_like
        ORDER BY
            CASE
                WHEN ISNULL(Classe, '') LIKE :q_inicio THEN 0
                WHEN ISNULL(Descricao, '') LIKE :q_inicio THEN 1
                WHEN ISNULL(Setor, '') LIKE :q_inicio THEN 2
                WHEN ISNULL(cnaepadrao, '') LIKE :q_inicio THEN 3
                ELSE 4
            END,
            Classe ASC,
            Descricao ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "q_like": f"%{q}%",
            "q_inicio": f"{q}%",
        },
    ).mappings().all()

    payload = {"ok": True, "cnaes": _rows_para_dicts(rows)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_MEDIO)
    return jsonify(payload)




@kanban_bp.route("/api/paineis", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_paineis_lista():
    _assert_login()
    return jsonify({"ok": True, "paineis": _obter_paineis_catalogo()})



@kanban_bp.route("/api/painel-faces/catalogo", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_painel_faces_catalogo():
    _assert_login()
    return jsonify({"ok": True, "painel_faces": _obter_painel_faces_catalogo()})






@kanban_bp.route("/api/paineis/<int:cod_ponto>/faces", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_faces_por_painel(cod_ponto: int):
    _assert_login()
    cod_ponto = int(cod_ponto or 0)
    if cod_ponto <= 0:
        return jsonify({"ok": False, "msg": "CodPonto inválido"}), 400

    chave = _chave_cache_json("kanban:api:faces", cod_ponto)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT
            f.CodFace,
            f.Face
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE f.CodPonto = :cod_ponto
          AND f.CodFace IS NOT NULL
          AND f.CodFace <> ''
        GROUP BY f.CodFace, f.Face
        ORDER BY
            CASE WHEN f.Face IS NULL OR f.Face = '' THEN 1 ELSE 0 END,
            f.Face ASC,
            f.CodFace ASC;
    """)

    try:
        rows = db.session.execute(sql, {"cod_ponto": cod_ponto}).mappings().all()
    except Exception as exc:
        return jsonify({"ok": False, "msg": "Erro ao consultar faces do painel", "erro": str(exc)}), 500

    faces: list[dict[str, Any]] = []
    for row in rows:
        codface = str(row.get("CodFace") or "").strip()
        face = str(row.get("Face") or "").strip()
        if not codface:
            continue
        label = f"Face {face} • CodFace {codface}" if face else f"CodFace {codface}"
        faces.append({"CodFace": codface, "Face": face or None, "Label": label})

    payload = {"ok": True, "cod_ponto": cod_ponto, "total": len(faces), "faces": faces}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_LONGO)
    return jsonify(payload)





@kanban_bp.route("/api/paineis/id/<int:id_painel>/faces", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_faces_por_id_painel(id_painel: int):
    _assert_login()
    painel = _obter_painel_por_id(id_painel)
    if not painel:
        return jsonify({"ok": False, "msg": "Painel não encontrado"}), 404

    chave = _chave_cache_json("kanban:api:faces:id_painel", id_painel)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT
            f.IDDimFacesPaineis,
            f.CodFace,
            f.Face,
            f.Tipo
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE f.CodFace IS NOT NULL
          AND LTRIM(RTRIM(f.CodFace)) <> ''
          AND (
                f.IDDimPaineisEuromidia = :id_painel
                OR (
                    f.CodPonto = :cod_ponto
                    AND f.Tipo = :tipo_painel
                )
              )
        GROUP BY f.IDDimFacesPaineis, f.CodFace, f.Face, f.Tipo
        ORDER BY
            CASE WHEN f.Face IS NULL OR f.Face = '' THEN 1 ELSE 0 END,
            f.Face ASC,
            f.CodFace ASC;
    """)
    rows = db.session.execute(
        sql,
        {
            "id_painel": int(id_painel),
            "cod_ponto": painel.get("CodPonto"),
            "tipo_painel": _normalizar_texto(painel.get("Tipo")),
        },
    ).mappings().all()

    faces = []
    for row in rows:
        codface = _normalizar_texto(row.get("CodFace"))
        face = _normalizar_texto(row.get("Face"))
        if not codface:
            continue
        label = f"Face {face} • CodFace {codface}" if face else f"CodFace {codface}"
        faces.append(
            {
                "IDDimFacesPaineis": int(row.get("IDDimFacesPaineis") or 0) if row.get("IDDimFacesPaineis") is not None else None,
                "CodFace": codface,
                "Face": face or None,
                "Tipo": _normalizar_texto(row.get("Tipo")) or None,
                "Label": label,
            }
        )

    payload = {
        "ok": True,
        "id_painel": int(id_painel),
        "cod_ponto": painel.get("CodPonto"),
        "tipo_painel": painel.get("Tipo"),
        "total": len(faces),
        "faces": faces,
    }
    _cache_json_set(chave, payload, TIMEOUT_CACHE_LONGO)
    return jsonify(payload)







@kanban_bp.route("/api/paineis/id/<int:id_painel>/faces/<string:cod_face>/comercial", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_comercial_painel_face(id_painel: int, cod_face: str):
    _assert_login()
    painel = _obter_painel_por_id(id_painel)
    if not painel:
        return jsonify({"ok": False, "msg": "Painel não encontrado"}), 404

    face = _resolver_face_do_painel(id_painel, cod_face)
    if not face:
        return jsonify({"ok": False, "msg": "Face não encontrada para o painel selecionado"}), 404

    custo = _obter_custo_por_codponto(int(painel.get("CodPonto") or 0))
    precos = _obter_precos_painel_face(
        id_painel=int(id_painel),
        id_dim_face=int(face.get("IDDimFacesPaineis") or 0) if face.get("IDDimFacesPaineis") is not None else None,
        tipo_painel=_normalizar_texto(painel.get("Tipo")),
    )

    precos_payload = []
    for row in precos:
        linha = {
            "IDDimTabelaPrecosEuromidia": int(row.get("IDDimTabelaPrecosEuromidia") or 0),
            "PeriodoExibicao": row.get("PeriodoExibicao"),
            "ExibicoesDia": row.get("ExibicoesDia"),
            "Valor": _decimal_para_float(row.get("Valor")),
            "Tabela": row.get("Tabela"),
            "PoliticaTrocas": row.get("PoliticaTrocas"),
            "ValorTroca": _decimal_para_float(row.get("ValorTroca")),
            "BitAtivo": int(row.get("BitAtivo") or 0),
            "DataPublicacao": row.get("DataPublicacao"),
            "DataValidade": row.get("DataValidade"),
        }
        precos_payload.append(linha)

    payload = {
        "ok": True,
        "painel": painel,
        "face": face,
        "EhPainelDigital": any(row.get("ExibicoesDia") is not None for row in precos) or _normalizar_texto_comparacao(painel.get("Tipo")) == "painel digital",
        "custo": {
            "IDDimCustoPainel": int(custo.get("IDDimCustoPainel") or 0) if custo else None,
            "Ano": int(custo.get("Ano") or 0) if custo and custo.get("Ano") is not None else None,
            "CodPonto": int(custo.get("CodPonto") or 0) if custo and custo.get("CodPonto") is not None else None,
            "Origem": custo.get("Origem") if custo else None,
            "Valor": _decimal_para_float(custo.get("Valor")) if custo else None,
            "DataCarga": custo.get("DataCarga") if custo else None,
            "observacao": "O custo disponível hoje está na DimCustoPainel por CodPonto. Logo, ele é custo do painel/ponto, não custo específico da face.",
        },
        "precos": precos_payload,
    }
    return jsonify(payload)


@kanban_bp.route("/api/kanbans", methods=["POST"])
@login_required
@limiter.limit("30/minute")
def api_kanban_criar():
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or "").strip()
    descricao = (payload.get("descricao") or "").strip()

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome do kanban inválido"}), 400

    sql = text("""
        INSERT INTO [Kanban].[Silver].[DimKanban]
            (NomeKanban, Descricao, Ativo, CriadoEm, IDUsuario, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDDimKanban
        VALUES
            (:nome, :descricao, 1, GETDATE(), :id_usuario, :id_emp);
    """)
    novo_id = db.session.execute(
        sql,
        {
            "nome": nome[:100],
            "descricao": descricao[:3000] if descricao else None,
            "id_usuario": id_usuario,
            "id_emp": id_emp,
        },
    ).scalar()

    db.session.commit()
    _invalidar_kanban(id_emp=id_emp, id_kanban=int(novo_id))

    _emitir_evento_kanban(
        int(novo_id),
        "kanban_criado",
        {
            "IDDimKanban": int(novo_id),
            "NomeKanban": nome[:100],
            "Descricao": descricao[:3000] if descricao else None,
            "IDEmpresaProprietaria": id_emp,
        },
    )

    return jsonify({"ok": True, "IDDimKanban": int(novo_id)})


@kanban_bp.route("/api/kanbans/<int:id_kanban>/fases", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_fase_criar(id_kanban: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    _obter_kanban_autorizado(id_kanban)

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or "").strip()
    tipo = _normalizar_codigo_dominio(payload.get("tipo")) or _obter_tipo_fase_padrao(id_kanban=id_kanban, id_emp=id_emp)
    ordem = payload.get("ordem")
    cor_hex = (payload.get("cor_hex") or "").strip() or None

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da fase inválido"}), 400
    tipos_fase_validos = _obter_tipos_fase_configurados(id_kanban=id_kanban, id_emp=id_emp)
    if tipos_fase_validos and tipo not in tipos_fase_validos:
        return jsonify({"ok": False, "msg": "TipoFase inválido", "tipos_fase_validos": tipos_fase_validos}), 400
    if cor_hex and not _cor_hex_valida(cor_hex):
        return jsonify({"ok": False, "msg": "CorHex inválida. Use #RRGGBB"}), 400

    if ordem is None:
        sql_max = text(f"""
            SELECT ISNULL(MAX(OrdemFase), 0) + 1
            FROM {TABELA_KANBAN_FASE}
            WHERE IDDimKanban = :id_kanban;
        """)
        ordem = int(db.session.execute(sql_max, {"id_kanban": id_kanban}).scalar() or 1)
    else:
        ordem = int(ordem)

    colunas = [
        "IDDimKanban",
        "NomeFase",
        "OrdemFase",
        "TipoFase",
        "Ativo",
        "CriadoEm",
        "IDUsuario",
        "IDEmpresaProprietaria",
    ]
    valores = [
        ":id_kanban",
        ":nome",
        ":ordem",
        ":tipo",
        "1",
        "GETDATE()",
        ":id_usuario",
        ":id_emp",
    ]
    params = {
        "id_kanban": id_kanban,
        "nome": nome[:100],
        "ordem": ordem,
        "tipo": tipo,
        "id_usuario": id_usuario,
        "id_emp": id_emp,
    }

    if _coluna_existe(TABELA_KANBAN_FASE, "CorHex"):
        colunas.append("CorHex")
        valores.append(":cor_hex")
        params["cor_hex"] = cor_hex

    sql = text(
        f"""
        INSERT INTO {TABELA_KANBAN_FASE}
            ({', '.join(colunas)})
        OUTPUT INSERTED.IDDimKanbanFase
        VALUES
            ({', '.join(valores)});
        """
    )
    novo_id = db.session.execute(sql, params).scalar()

    db.session.commit()
    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)

    _emitir_evento_kanban(
        id_kanban,
        "fase_criada",
        {
            "IDDimKanbanFase": int(novo_id),
            "NomeFase": nome[:100],
            "OrdemFase": ordem,
            "TipoFase": tipo,
            "CorHex": cor_hex,
        },
    )

    return jsonify({"ok": True, "IDDimKanbanFase": int(novo_id)})


@kanban_bp.route("/api/fases/<int:id_fase>", methods=["PUT"])
@login_required
@limiter.limit("120/minute")
def api_fase_atualizar(id_fase: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    fase = _obter_fase_autorizada(id_fase)
    id_kanban = int(fase.get("IDDimKanban") or 0)

    payload = request.get_json(silent=True) or {}
    nome = payload.get("nome")
    tipo = payload.get("tipo")
    cor_hex = payload.get("cor_hex")

    campos: list[str] = []
    params: dict[str, Any] = {"id_fase": id_fase}

    if nome is not None:
        nome = str(nome).strip()
        if len(nome) < 2:
            return jsonify({"ok": False, "msg": "Nome da fase inválido"}), 400
        campos.append("NomeFase = :nome")
        params["nome"] = nome[:100]

    if tipo is not None:
        tipo = str(tipo).strip().upper()
        tipos_fase_validos = _obter_tipos_fase_configurados(id_kanban=id_kanban, id_emp=id_emp)
        if tipos_fase_validos and tipo not in tipos_fase_validos:
            return jsonify({"ok": False, "msg": "TipoFase inválido", "tipos_fase_validos": tipos_fase_validos}), 400
        campos.append("TipoFase = :tipo")
        params["tipo"] = tipo

    if cor_hex is not None and _coluna_existe(TABELA_KANBAN_FASE, "CorHex"):
        cor_hex = str(cor_hex).strip() or None
        if cor_hex and not _cor_hex_valida(cor_hex):
            return jsonify({"ok": False, "msg": "CorHex inválida. Use #RRGGBB"}), 400
        campos.append("CorHex = :cor_hex")
        params["cor_hex"] = cor_hex

    if not campos:
        return jsonify({"ok": False, "msg": "Nenhuma alteração enviada para a fase"}), 400

    if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoEm"):
        campos.append("AtualizadoEm = GETDATE()")
    if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoPor"):
        campos.append("AtualizadoPor = :id_usuario")
        params["id_usuario"] = id_usuario

    try:
        sql = text(f"""
            UPDATE {TABELA_KANBAN_FASE}
            SET {', '.join(campos)}
            WHERE IDDimKanbanFase = :id_fase
              AND Ativo = 1;
        """)
        db.session.execute(sql, params)
        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
        _emitir_evento_kanban(
            id_kanban,
            "fase_atualizada",
            {
                "IDDimKanbanFase": id_fase,
                "NomeFase": params.get("nome", fase.get("NomeFase")),
                "TipoFase": params.get("tipo", fase.get("TipoFase")),
                "CorHex": params.get("cor_hex", fase.get("CorHex")),
            },
        )
        return jsonify({"ok": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar fase id_fase=%s", id_fase)
        return jsonify({"ok": False, "msg": f"Erro ao atualizar fase: {str(exc)}"}), 500



@kanban_bp.route("/api/fases/reordenar", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_fases_reordenar():
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}
    fases = payload.get("fases") or []

    if not isinstance(fases, list) or not fases:
        return jsonify({"ok": False, "msg": "Payload inválido"}), 400

    id_kanban_afetado: int | None = None
    fases_reordenadas: list[dict[str, int]] = []

    for item in fases:
        id_fase = int(item.get("id") or 0)
        ordem = int(item.get("ordem") or 0)
        if not id_fase or not ordem:
            continue

        fase = _obter_fase_autorizada(id_fase)
        id_kanban_fase = int(fase.get("IDDimKanban") or 0)
        if id_kanban_afetado is None:
            id_kanban_afetado = id_kanban_fase
        elif id_kanban_afetado != id_kanban_fase:
            return jsonify({"ok": False, "msg": "Não é permitido reordenar fases de kanbans diferentes"}), 400

        sql = text("""
            UPDATE [Kanban].[Silver].[DimKanbanFase]
            SET OrdemFase = :ordem
            WHERE IDDimKanbanFase = :id_fase;
        """)
        db.session.execute(sql, {"id_fase": id_fase, "ordem": ordem})
        fases_reordenadas.append({"id": id_fase, "ordem": ordem})

    db.session.commit()

    if id_kanban_afetado:
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban_afetado)
        _emitir_evento_kanban(
            id_kanban_afetado,
            "fases_reordenadas",
            {"fases": fases_reordenadas},
        )

    return jsonify({"ok": True})




def _negociacao_preco_foi_alterada(
    ultima: dict[str, Any] | None,
    *,
    id_tabela_preco: int | None,
    custo_atual: Any,
    preco_atual: Any,
    margem_atual_percentual: Any,
    custo_proposto: Any,
    preco_proposto: Any,
    margem_proposta_percentual: Any,
    desconto_proposto: Any,
    periodo_inicio: Any = None,
    periodo_termino: Any = None,
) -> bool:
    """
    Decide se houve mudança real na negociação de preço.

    Fundamento:
    - histórico não deve crescer por reenvio do mesmo payload
    - comparação decimal precisa ser normalizada para evitar falso positivo por formato
    - o período também faz parte da assinatura, porque agora ele é requisito de negócio
    - porém os campos de período ficam opcionais aqui para manter compatibilidade
      com chamadas antigas que ainda não foram atualizadas
    """

    def normalizar_int(valor: Any) -> int | None:
        if valor in (None, ""):
            return None
        try:
            return int(valor)
        except Exception:
            return None

    def normalizar_dec(valor: Any, casas: str = "0.0001") -> Decimal | None:
        dec = _valor_decimal(valor)
        if dec is None:
            return None
        try:
            return dec.quantize(Decimal(casas))
        except Exception:
            return dec

    def normalizar_data(valor: Any) -> date | None:
        return _normalizar_data_reserva_kanban(valor)

    def assinatura_fonte(
        *,
        id_tabela: Any,
        custo_atual_fonte: Any,
        preco_atual_fonte: Any,
        margem_atual_fonte: Any,
        custo_proposto_fonte: Any,
        preco_proposto_fonte: Any,
        margem_proposta_fonte: Any,
        desconto_proposto_fonte: Any,
        periodo_inicio_fonte: Any,
        periodo_termino_fonte: Any,
    ) -> tuple:
        return (
            normalizar_int(id_tabela),
            normalizar_dec(custo_atual_fonte),
            normalizar_dec(preco_atual_fonte),
            normalizar_dec(margem_atual_fonte),
            normalizar_dec(custo_proposto_fonte),
            normalizar_dec(preco_proposto_fonte),
            normalizar_dec(margem_proposta_fonte),
            normalizar_dec(desconto_proposto_fonte),
            normalizar_data(periodo_inicio_fonte),
            normalizar_data(periodo_termino_fonte),
        )

    assinatura_atual = assinatura_fonte(
        id_tabela=id_tabela_preco,
        custo_atual_fonte=custo_atual,
        preco_atual_fonte=preco_atual,
        margem_atual_fonte=margem_atual_percentual,
        custo_proposto_fonte=custo_proposto,
        preco_proposto_fonte=preco_proposto,
        margem_proposta_fonte=margem_proposta_percentual,
        desconto_proposto_fonte=desconto_proposto,
        periodo_inicio_fonte=periodo_inicio,
        periodo_termino_fonte=periodo_termino,
    )

    if not ultima:
        return True

    assinatura_ultima = assinatura_fonte(
        id_tabela=ultima.get("IDDimTabelaPrecosEuromidia"),
        custo_atual_fonte=ultima.get("CustoAtual"),
        preco_atual_fonte=ultima.get("PrecoAtual"),
        margem_atual_fonte=ultima.get("MargemAtual"),
        custo_proposto_fonte=ultima.get("CustoProposto"),
        preco_proposto_fonte=ultima.get("PrecoProposto"),
        margem_proposta_fonte=ultima.get("MargemProposta"),
        desconto_proposto_fonte=ultima.get("DescontoProposto"),
        periodo_inicio_fonte=ultima.get("PeriodoInicio"),
        periodo_termino_fonte=ultima.get("PeriodoTermino"),
    )

    return assinatura_atual != assinatura_ultima









@kanban_bp.route("/api/cards/<int:id_card>/mover", methods=["POST"])
@login_required
@limiter.limit("180/minute")
def api_card_mover(id_card: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    payload = request.get_json(silent=True) or {}

    def _funcao_transacional() -> dict[str, Any]:
        return _executar_movimento_card_core(
            id_card=int(id_card),
            id_usuario=int(id_usuario),
            id_emp=int(id_emp),
            payload=dict(payload),
        )

    def _funcao_enfileirar(_erro: BaseException) -> str | None:
        payload_retry = {
            "id_card": int(id_card),
            "id_usuario": int(id_usuario),
            "id_emp": int(id_emp),
            "payload": dict(payload),
        }
        return _enfileirar_retry_movimento_card(payload_retry)

    try:
        execucao = executar_transacao_com_retry_deadlock_ou_enfileirar(
            _funcao_transacional,
            funcao_enfileirar=_funcao_enfileirar,
            max_tentativas=4,
            atraso_inicial_segundos=0.25,
            multiplicador_backoff=2.0,
            jitter_max_segundos=0.35,
        )

        if execucao["modo_execucao"] == "fila":
            return jsonify(
                {
                    "ok": True,
                    "processamento_assincrono": True,
                    "msg": "Deadlock persistente. O movimento entrou na fila rápida de retry.",
                    "task_id": execucao["task_id"],
                    "id_card": int(id_card),
                }
            ), 202

        resultado_core = execucao["resultado"]
        resposta = _finalizar_pos_movimento_card(
            id_card=int(id_card),
            id_emp=int(id_emp),
            resultado_core=resultado_core,
        )
        return jsonify(resposta)

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except RuntimeError as exc:
        db.session.rollback()

        msg = str(exc)
        if "Versão de concorrência inválida ou ausente" in msg:
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return (
                jsonify(
                    {
                        "ok": False,
                        "msg": msg,
                        "card_atual": detalhe_atual.get("card"),
                    }
                ),
                409,
            )

        if "Este card foi alterado ou movido por outro usuário" in msg:
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return (
                jsonify(
                    {
                        "ok": False,
                        "msg": msg,
                        "card_atual": detalhe_atual.get("card"),
                    }
                ),
                409,
            )

        current_app.logger.exception("Erro ao mover card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao mover card: {msg}"}), 500

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao mover card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao mover card: {str(exc)}"}), 500







@kanban_bp.route("/api/kanbans/<int:id_kanban>/tags", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_tag_criar(id_kanban: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    _obter_kanban_autorizado(id_kanban)

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or "").strip()
    tipo = _normalizar_codigo_dominio(payload.get("tipo")) or _obter_tipo_tag_padrao(id_kanban=id_kanban, id_emp=id_emp)
    cor = (payload.get("cor_hex") or "").strip()
    icone = (payload.get("icone") or "").strip()

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da tag inválido"}), 400
    tipos_tag_validos = _obter_tipos_tag_configurados(id_kanban=id_kanban, id_emp=id_emp)
    if tipos_tag_validos and tipo not in tipos_tag_validos:
        return jsonify({"ok": False, "msg": "TipoTag inválido", "tipos_tag_validos": tipos_tag_validos}), 400
    if cor and (len(cor) != 7 or not cor.startswith("#")):
        return jsonify({"ok": False, "msg": "CorHex inválida. Use #RRGGBB"}), 400

    sql = text("""
        INSERT INTO [Kanban].[Silver].[DimKanbanTag]
            (IDDimKanban, NomeTag, TipoTag, CorHex, Icone,
             AfetaCorCard, PodeVendedorAplicar, PodeAdminAplicar, AplicacaoUnica,
             Ativo, CriadoEm, IDUsuario, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDDimKanbanTag
        VALUES
            (:id_kanban, :nome, :tipo, :cor, :icone,
             0, 1, 1, 0,
             1, GETDATE(), :id_usuario, :id_emp);
    """)
    novo_id = db.session.execute(
        sql,
        {
            "id_kanban": id_kanban,
            "nome": nome[:100],
            "tipo": tipo,
            "cor": cor if cor else None,
            "icone": icone[:50] if icone else None,
            "id_usuario": id_usuario,
            "id_emp": id_emp,
        },
    ).scalar()

    db.session.commit()
    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
    _emitir_evento_kanban(
        id_kanban,
        "tag_criada",
        {
            "IDDimKanbanTag": int(novo_id),
            "NomeTag": nome[:100],
            "TipoTag": tipo,
            "CorHex": cor if cor else None,
            "Icone": icone[:50] if icone else None,
        },
    )

    return jsonify({"ok": True, "IDDimKanbanTag": int(novo_id)})





@kanban_bp.route("/api/cards/<int:id_card>/tags", methods=["POST"])
@login_required
@limiter.limit("180/minute")
def api_card_tag_adicionar(id_card: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)
    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0)
    payload = request.get_json(silent=True) or {}

    id_tag = int(payload.get("id_tag") or 0)
    if not id_tag:
        return jsonify({"ok": False, "msg": "Tag obrigatória"}), 400

    sql_tag = text("""
        SELECT
            t.IDDimKanbanTag,
            t.NomeTag
        FROM [Kanban].[Silver].[DimKanbanTag] t
        WHERE t.IDDimKanbanTag = :id_tag
          AND t.IDDimKanban = :id_kanban
          AND t.Ativo = 1;
    """)
    tag_row = db.session.execute(
        sql_tag,
        {
            "id_tag": id_tag,
            "id_kanban": id_kanban,
        },
    ).mappings().first()

    if not tag_row:
        return jsonify({"ok": False, "msg": "Tag inválida para este card"}), 400

    sql_dup = text("""
        SELECT TOP (1)
            IDFatoKanbanCardTag
        FROM [Kanban].[Silver].[FatoKanbanCardTag]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)
    existe = db.session.execute(
        sql_dup,
        {
            "id_card": id_card,
            "id_tag": id_tag,
        },
    ).scalar()

    alterou = False
    retorno_solicitacao: dict[str, Any] | None = None
    snapshot_preco_praticado: dict[str, Any] | None = None

    try:
        if not existe:
            snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

            sql_insert = text("""
                INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
                    (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
                OUTPUT INSERTED.IDFatoKanbanCardTag
                VALUES
                    (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa);
            """)
            id_card_tag_inserido = db.session.execute(
                sql_insert,
                {
                    "id_card": id_card,
                    "id_tag": id_tag,
                    "id_usuario": id_usuario,
                    "id_empresa": card.get("IDEmpresaProprietaria"),
                },
            ).scalar()

            nome_tag_aplicada = str(tag_row.get("NomeTag") or "").strip()
            nome_tag_aplicada_normalizada = _normalizar_texto_comparacao(nome_tag_aplicada)

            if (
                int(id_tag) == int(ID_TAG_CONTRATO_EM_AVALIACAO)
                or nome_tag_aplicada_normalizada == _normalizar_texto_comparacao(NOME_TAG_CONTRATO_EM_AVALIACAO)
            ):
                retorno_solicitacao = _criar_solicitacao_contrato_em_avaliacao_para_card(
                    id_card=int(id_card),
                    id_usuario=int(id_usuario),
                    id_empresa_proprietaria=int(id_emp),
                )

            snapshot_preco_praticado = None
            if int(id_tag) == int(ID_TAG_CONTRATO_APROVADO):
                snapshot_preco_praticado = _sincronizar_aprovacao_contrato_no_snapshot_preco_praticado(
                    id_card=int(id_card),
                    id_usuario_aprovacao=int(id_usuario),
                    id_empresa_proprietaria=int(id_emp),
                )

                current_app.logger.warning(
                    "KANBAN SNAPSHOT PRECO PRATICADO TAG 13: id_card=%s resultado=%s",
                    id_card,
                    snapshot_preco_praticado,
                )

                if not snapshot_preco_praticado or not snapshot_preco_praticado.get("ok"):
                    motivo_snapshot = (
                        snapshot_preco_praticado.get("motivo")
                        if isinstance(snapshot_preco_praticado, dict)
                        else "snapshot_preco_praticado_tag13_nao_retorno_ok"
                    )
                    raise RuntimeError(
                        "Falha ao sincronizar a foto do preço praticado na aprovação do contrato. "
                        f"Motivo: {motivo_snapshot}"
                    )

            id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
                id_kanban=id_kanban,
                id_empresa_padrao=card.get("IDEmpresaProprietaria"),
            )

            _registrar_tag_historico_card(
                id_fato_kanban_card_tag=int(id_card_tag_inserido or 0) or None,
                id_card=int(id_card),
                id_fase=int(id_fase_atual) if id_fase_atual else None,
                id_usuario=int(id_usuario),
                id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
            )

            snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)

            observacao_log = f"Tag adicionada: {nome_tag_aplicada}"
            if retorno_solicitacao and retorno_solicitacao.get("criada"):
                observacao_log += (
                    f" | Solicitação de contrato criada: "
                    f"{retorno_solicitacao.get('id_solicitacao')} "
                    f"({retorno_solicitacao.get('tipo_solicitacao')})"
                )

            _registrar_log_card(
                id_card=id_card,
                id_kanban=id_kanban,
                id_empresa_proprietaria=id_emp,
                id_usuario_acao=id_usuario,
                tipo_evento="TAG_ADICIONADA",
                subtipo_evento=nome_tag_aplicada[:120] or None,
                id_fase_de=id_fase_atual if id_fase_atual else None,
                id_fase_para=id_fase_atual if id_fase_atual else None,
                observacao=observacao_log[:2000],
                tabela_origem="[Kanban].[Silver].[FatoKanbanCardTag]",
                id_registro_origem=int(id_card_tag_inserido or 0) or None,
                payload_antes=snapshot_antes,
                payload_depois=snapshot_depois,
            )

            db.session.commit()
            alterou = True

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
        detalhe = _obter_card_detalhe_payload(id_card)

        if alterou:
            payload_socket = {
                "id_card": id_card,
                "id_tag": id_tag,
                "id_usuario_acao": id_usuario,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
            }

            if retorno_solicitacao:
                payload_socket["solicitacao_contrato"] = retorno_solicitacao
            if snapshot_preco_praticado is not None:
                payload_socket["snapshot_preco_praticado"] = snapshot_preco_praticado

            _emitir_evento_kanban(
                id_kanban,
                "card_tag_adicionada",
                payload_socket,
            )

        msg = "Tag adicionada com sucesso."
        if retorno_solicitacao:
            if retorno_solicitacao.get("criada"):
                msg = (
                    f"Tag adicionada com sucesso. "
                    f"Solicitação {retorno_solicitacao.get('id_solicitacao')} criada com "
                    f"{retorno_solicitacao.get('total_itens')} item(ns)."
                )
            elif retorno_solicitacao.get("motivo"):
                msg = (
                    "Tag adicionada com sucesso. "
                    f"{retorno_solicitacao.get('motivo')}"
                )

        return jsonify(
            {
                "ok": True,
                "msg": msg,
                "id_card": id_card,
                "id_tag": id_tag,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "solicitacao_contrato": retorno_solicitacao,
                "snapshot_preco_praticado": snapshot_preco_praticado,
            }
        )

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Erro ao adicionar tag no card id_card=%s id_tag=%s",
            id_card,
            id_tag,
        )
        return jsonify({"ok": False, "msg": f"Erro ao adicionar tag: {str(exc)}"}), 500














@kanban_bp.route("/api/cards/<int:id_card>/tags/<int:id_tag>", methods=["DELETE"])
@login_required
@limiter.limit("180/minute")
def api_card_tag_remover(id_card: int, id_tag: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)
    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0)

    tag_em_atendimento = _obter_tag_em_atendimento(id_kanban)
    if tag_em_atendimento and int(tag_em_atendimento.get("IDDimKanbanTag") or 0) == int(id_tag):
        return jsonify({
            "ok": False,
            "msg": "A tag 'Em Atendimento' é automática e só pode ser removida quando o card for concluído ou removido do kanban.",
        }), 400

    tag_aprovacao_desconto = _obter_tag_por_nome(
        id_kanban,
        NOME_TAG_APROVACAO_DESCONTO,
        somente_ativa=True,
    )
    if tag_aprovacao_desconto and int(tag_aprovacao_desconto.get("IDDimKanbanTag") or 0) == int(id_tag):
        if _card_precisa_aprovacao_diretoria_por_estado_atual(int(id_card)):
            return jsonify({
                "ok": False,
                "msg": "A tag 'Aprovação Desconto' é automática enquanto o preço final estiver em até 12% acima do custo.",
            }), 400

    tag_row = db.session.execute(
        text("""
            SELECT TOP (1)
                IDDimKanbanTag,
                NomeTag
            FROM [Kanban].[Silver].[DimKanbanTag]
            WHERE IDDimKanbanTag = :id_tag;
        """),
        {"id_tag": int(id_tag)},
    ).mappings().first()

    snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)
    alterou = _remover_tag_do_card(
        id_card=int(id_card),
        id_tag=int(id_tag),
        id_usuario=int(id_usuario),
    )

    if alterou:
        id_card_tag_removido = db.session.execute(
            text("""
                SELECT TOP (1)
                    IDFatoKanbanCardTag
                FROM [Kanban].[Silver].[FatoKanbanCardTag]
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimKanbanTag = :id_tag
                  AND RemovidoPor = :id_usuario
                  AND RemovidoEm IS NOT NULL
                ORDER BY RemovidoEm DESC, IDFatoKanbanCardTag DESC;
            """),
            {
                "id_card": int(id_card),
                "id_tag": int(id_tag),
                "id_usuario": int(id_usuario),
            },
        ).scalar()

        id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
            id_kanban=id_kanban,
            id_empresa_padrao=card.get("IDEmpresaProprietaria"),
        )

        _registrar_tag_historico_card(
            id_fato_kanban_card_tag=int(id_card_tag_removido or 0) or None,
            id_card=int(id_card),
            id_fase=int(id_fase_atual) if id_fase_atual else None,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
        )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="TAG_REMOVIDA",
            subtipo_evento=str((tag_row or {}).get("NomeTag") or "").strip()[:120] or None,
            id_fase_de=id_fase_atual if id_fase_atual else None,
            id_fase_para=id_fase_atual if id_fase_atual else None,
            observacao=f"Tag removida: {str((tag_row or {}).get('NomeTag') or '').strip()}",
            tabela_origem="[Kanban].[Silver].[FatoKanbanCardTag]",
            id_registro_origem=int(id_card_tag_removido or 0) or None,
            payload_antes=snapshot_antes,
            payload_depois=snapshot_depois,
        )

    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

    if alterou:
        _emitir_evento_kanban(
            id_kanban,
            "card_tag_removida",
            {"id_card": id_card, "id_tag": id_tag},
        )

    return jsonify({"ok": True})







def _obter_contexto_observacao_card(id_card: int) -> dict[str, Any]:
    """
    Busca o contexto atual do card para gravar histórico de observações.

    Retorna:
    - IDFatoKanbanCard
    - IDDimKanban
    - IDDimKanbanFaseAtual
    - NomeFaseAtual
    - IDEmpresaProprietaria do card
    - IDEmpresaDoKanban
    - IDDimKanbanStatusCard, se a coluna existir na FatoKanbanCard
    """
    id_emp = _id_empresa_usuario_or_403()

    select_status = (
        "c.IDDimKanbanStatusCard AS IDDimKanbanStatusCard,"
        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard")
        else "CAST(NULL AS INT) AS IDDimKanbanStatusCard,"
    )

    sql = text(f"""
        SELECT TOP (1)
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            f.NomeFase AS NomeFaseAtual,
            c.IDEmpresaProprietaria,
            k.IDEmpresaProprietaria AS IDEmpresaDoKanban,
            {select_status}
            c.StatusCard
        FROM {TABELA_CARD} c
        JOIN {TABELA_KANBAN} k
          ON k.IDDimKanban = c.IDDimKanban
        LEFT JOIN {TABELA_KANBAN_FASE} f
          ON f.IDDimKanbanFase = c.IDDimKanbanFaseAtual
        WHERE c.IDFatoKanbanCard = :id_card
          AND k.IDEmpresaProprietaria = :id_emp
          AND k.Ativo = 1;
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_emp": int(id_emp),
        },
    ).mappings().first()

    if not row:
        abort(403, "Você não tem permissão para acessar este card")

    return dict(row)


def _registrar_observacao_historica_card(
    *,
    id_card: int,
    texto_observacao: str,
    id_usuario: int,
    id_status_card: int | None = None,
    id_fase: int | None = None,
) -> dict[str, Any] | None:
    """
    Grava o histórico de observações digitadas no campo de notas do card.

    Regras:
    - Observacao = texto digitado
    - IDEmpresaProprietaria = regra do kanban (kanban 1 => empresa 3)
    - IDFatoKanbanCard = card atual
    - IDDimKanbanStatusCard = status atual do card; se não vier por parâmetro,
      tento pegar da FatoKanbanCard e, se não existir, derivo pela fase
    - IDDimKanbanFase = fase atual do card; se vier por parâmetro, uso o informado
    - IDDimUsuarios = usuário logado
    - CriadoEm = GETDATE()
    """
    if not _objeto_existe(TABELA_CARD_OBSERVACOES):
        return None

    texto = str(texto_observacao or "").strip()
    if len(texto) < 2:
        return None

    contexto = _obter_contexto_observacao_card(id_card)
    if not contexto:
        return None

    id_kanban = int(contexto.get("IDDimKanban") or 0)
    id_fase_atual = int(id_fase or contexto.get("IDDimKanbanFaseAtual") or 0)
    nome_fase_atual = str(contexto.get("NomeFaseAtual") or "").strip()

    id_status_atual = id_status_card
    if id_status_atual is None:
        id_status_atual = contexto.get("IDDimKanbanStatusCard")

    if id_status_atual is None:
        id_status_atual = _resolver_id_status_card_movimento(
            nome_fase_para=nome_fase_atual,
            card_inativado=False,
        )

    id_empresa_observacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=contexto.get("IDEmpresaProprietaria"),
    )

    sql = text(f"""
        INSERT INTO {TABELA_CARD_OBSERVACOES}
        (
            Observacao,
            IDEmpresaProprietaria,
            IDFatoKanbanCard,
            IDDimKanbanStatusCard,
            IDDimKanbanFase,
            IDDimUsuarios,
            CriadoEm
        )
        OUTPUT
            INSERTED.IDFatoKanbanCardObservacoes,
            INSERTED.Observacao,
            INSERTED.IDEmpresaProprietaria,
            INSERTED.IDFatoKanbanCard,
            INSERTED.IDDimKanbanStatusCard,
            INSERTED.IDDimKanbanFase,
            INSERTED.IDDimUsuarios,
            INSERTED.CriadoEm
        VALUES
        (
            :observacao,
            :id_empresa,
            :id_card,
            :id_status_card,
            :id_fase,
            :id_usuario,
            GETDATE()
        );
    """)

    row = db.session.execute(
        sql,
        {
            "observacao": texto[:1000],
            "id_empresa": int(id_empresa_observacao or 0) or None,
            "id_card": int(id_card),
            "id_status_card": int(id_status_atual) if id_status_atual is not None else None,
            "id_fase": int(id_fase_atual) if id_fase_atual else None,
            "id_usuario": int(id_usuario),
        },
    ).mappings().first()

    if not row:
        return None

    registro = dict(row)

    try:
        id_status_registro = (
            int(registro.get("IDDimKanbanStatusCard"))
            if registro.get("IDDimKanbanStatusCard") is not None
            else None
        )
    except Exception:
        id_status_registro = None

    registro["IDFatoKanbanCardNota"] = registro.get("IDFatoKanbanCardObservacoes")
    registro["TipoNota"] = "INATIVACAO" if id_status_registro == 2 else "OBS"
    registro["Texto"] = registro.get("Observacao")
    registro["CriadoPor"] = registro.get("IDDimUsuarios")
    registro["IDEmpresa"] = None

    return registro








def _registrar_observacao_historica_card(
    *,
    id_card: int,
    texto_observacao: str,
    id_usuario: int,
    id_status_card: int | None = None,
    id_fase: int | None = None,
) -> dict[str, Any] | None:
    """
    Grava o histórico de observações digitadas no campo de notas do card.

    Regras:
    - Observacao = texto digitado
    - IDEmpresaProprietaria = regra do kanban (kanban 1 => empresa 3)
    - IDFatoKanbanCard = card atual
    - IDDimKanbanStatusCard = status atual do card; se não vier por parâmetro,
      tento pegar da FatoKanbanCard e, se não existir, derivo pela fase
    - IDDimKanbanFase = fase atual do card; se vier por parâmetro, uso o informado
    - IDDimUsuarios = usuário logado
    - CriadoEm = GETDATE()
    """
    if not _objeto_existe(TABELA_CARD_OBSERVACOES):
        return None

    texto = str(texto_observacao or "").strip()
    if len(texto) < 2:
        return None

    contexto = _obter_contexto_observacao_card(id_card)
    if not contexto:
        return None

    id_kanban = int(contexto.get("IDDimKanban") or 0)
    id_fase_atual = int(id_fase or contexto.get("IDDimKanbanFaseAtual") or 0)
    nome_fase_atual = str(contexto.get("NomeFaseAtual") or "").strip()

    id_status_atual = id_status_card
    if id_status_atual is None:
        id_status_atual = contexto.get("IDDimKanbanStatusCard")

    if id_status_atual is None:
        id_status_atual = _resolver_id_status_card_movimento(
            nome_fase_para=nome_fase_atual,
            card_inativado=False,
        )

    id_empresa_observacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=contexto.get("IDEmpresaProprietaria"),
    )

    sql = text(f"""
        INSERT INTO {TABELA_CARD_OBSERVACOES}
        (
            Observacao,
            IDEmpresaProprietaria,
            IDFatoKanbanCard,
            IDDimKanbanStatusCard,
            IDDimKanbanFase,
            IDDimUsuarios,
            CriadoEm
        )
        OUTPUT
            INSERTED.IDFatoKanbanCardObservacoes,
            INSERTED.Observacao,
            INSERTED.IDEmpresaProprietaria,
            INSERTED.IDFatoKanbanCard,
            INSERTED.IDDimKanbanStatusCard,
            INSERTED.IDDimKanbanFase,
            INSERTED.IDDimUsuarios,
            INSERTED.CriadoEm
        VALUES
        (
            :observacao,
            :id_empresa,
            :id_card,
            :id_status_card,
            :id_fase,
            :id_usuario,
            GETDATE()
        );
    """)

    row = db.session.execute(
        sql,
        {
            "observacao": texto[:1000],
            "id_empresa": int(id_empresa_observacao or 0) or None,
            "id_card": int(id_card),
            "id_status_card": int(id_status_atual) if id_status_atual is not None else None,
            "id_fase": int(id_fase_atual) if id_fase_atual else None,
            "id_usuario": int(id_usuario),
        },
    ).mappings().first()

    if not row:
        return None

    registro = dict(row)

    try:
        id_status_registro = (
            int(registro.get("IDDimKanbanStatusCard"))
            if registro.get("IDDimKanbanStatusCard") is not None
            else None
        )
    except Exception:
        id_status_registro = None

    registro["IDFatoKanbanCardNota"] = registro.get("IDFatoKanbanCardObservacoes")
    registro["TipoNota"] = "INATIVACAO" if id_status_registro == 2 else "OBS"
    registro["Texto"] = registro.get("Observacao")
    registro["CriadoPor"] = registro.get("IDDimUsuarios")
    registro["IDEmpresa"] = None

    return registro



@kanban_bp.route("/api/cards/<int:id_card>/notas", methods=["POST"])
@login_required
@limiter.limit("180/minute")
def api_card_nota_criar(id_card: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)

    payload = request.get_json(silent=True) or {}
    texto = (payload.get("texto") or "").strip()

    if len(texto) < 2:
        return jsonify({"ok": False, "msg": "Texto da nota inválido"}), 400

    row_observacao = _registrar_observacao_historica_card(
        id_card=id_card,
        texto_observacao=texto,
        id_usuario=id_usuario,
    )

    if not row_observacao:
        db.session.rollback()
        return jsonify({"ok": False, "msg": "Não foi possível gravar a observação do card"}), 500

    snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
    _registrar_log_card(
        id_card=id_card,
        id_kanban=id_kanban,
        id_empresa_proprietaria=id_emp,
        id_usuario_acao=id_usuario,
        tipo_evento="CARD_NOTA_CRIADA",
        subtipo_evento="OBS",
        observacao=texto,
        tabela_origem=TABELA_CARD_OBSERVACOES,
        id_registro_origem=int(row_observacao.get("IDFatoKanbanCardObservacoes") or 0),
        payload_depois=snapshot_depois,
    )
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

    nota_payload = {
        "IDFatoKanbanCardNota": int(row_observacao.get("IDFatoKanbanCardNota") or 0),
        "IDFatoKanbanCardObservacoes": int(row_observacao.get("IDFatoKanbanCardObservacoes") or 0),
        "TipoNota": row_observacao.get("TipoNota") or "OBS",
        "Texto": row_observacao.get("Texto") or texto,
        "CriadoPor": int(row_observacao.get("CriadoPor") or id_usuario),
        "CriadoEm": row_observacao.get("CriadoEm"),
        "IDEmpresa": None,
        "IDEmpresaProprietaria": row_observacao.get("IDEmpresaProprietaria"),
        "IDDimKanbanStatusCard": row_observacao.get("IDDimKanbanStatusCard"),
        "IDDimKanbanFase": row_observacao.get("IDDimKanbanFase"),
    }

    _emitir_evento_kanban(
        id_kanban,
        "card_nota_criada",
        {"id_card": id_card, "nota": nota_payload},
    )

    return jsonify({"ok": True, "nota": nota_payload})






@kanban_bp.route("/api/cards/<int:id_card>/inativar", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_inativar(id_card: int):
    id_usuario = _assert_login()
    card_escopo = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    payload = request.get_json(silent=True) or {}

    motivo_informado = payload.get("motivo")
    descricao = (payload.get("descricao") or "").strip()

    motivo_normalizado = _normalizar_motivo_encerramento_card(motivo_informado)

    if not motivo_normalizado:
        return jsonify({"ok": False, "msg": "Motivo inválido"}), 400

    codigo_motivo = _normalizar_codigo_dominio(motivo_normalizado.get("Codigo"))
    if codigo_motivo in {"OUTROS", "OUTRO_MOTIVO"} and len(descricao) < 2:
        return jsonify({"ok": False, "msg": "Descreva o motivo"}), 400

    snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

    sql_card = text(f"""
        SELECT
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.IDEmpresaProprietaria,
            c.IDDimKanbanMotivoEncerramento,
            {_sql_select_empresa_relacionada_card('c')}
        FROM {TABELA_CARD} c
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.Ativo = 1;
    """)

    row = db.session.execute(sql_card, {"id_card": id_card}).mappings().first()

    if not row:
        return jsonify({"ok": False, "msg": "Card não encontrado ou já inativo"}), 404

    if int(row["IDDimKanban"]) != id_kanban:
        return jsonify({"ok": False, "msg": "Card fora do escopo do usuário"}), 403

    id_fase_atual = int(row["IDDimKanbanFaseAtual"] or 0)
    id_fase_para_movimento = 9
    id_empresa_card = row.get("IDEmpresaProprietaria")
    id_motivo_encerramento = int(motivo_normalizado.get("IDDimKanbanMotivoEncerramento") or 0)
    motivo_texto = str(motivo_normalizado.get("Descricao") or "").strip()

    observacao_inativacao = f"[INATIVADO] Motivo: {motivo_texto}" + (f" | {descricao}" if descricao else "")

    id_status_inativacao = _resolver_id_status_card_movimento(card_inativado=True)
    status_inativacao_texto = "CANCELADO"

    """
    Corrige o NameError:
    esse fluxo não gera sincronização de contato/contrato,
    então deixo explícito como None.
    """
    sincronizacao_contato_contrato = None
    sincronizacao_reservas = None

    try:
        campos_update = ["Ativo = 0"]
        params_update = {
            "id_usuario": id_usuario,
            "id_card": id_card,
        }

        if _coluna_existe(TABELA_CARD, "InativadoEm"):
            campos_update.append("InativadoEm = GETDATE()")

        if _coluna_existe(TABELA_CARD, "InativadoPor"):
            campos_update.append("InativadoPor = :id_usuario")

        if _coluna_existe(TABELA_CARD, "StatusCard"):
            campos_update.append("StatusCard = :status_inativacao")
            params_update["status_inativacao"] = status_inativacao_texto[:100]

        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_inativacao is not None:
            campos_update.append("IDDimKanbanStatusCard = :id_status_inativacao")
            params_update["id_status_inativacao"] = int(id_status_inativacao)

        if _coluna_existe(TABELA_CARD, "IDDimKanbanMotivoEncerramento"):
            campos_update.append("IDDimKanbanMotivoEncerramento = :id_motivo_encerramento")
            params_update["id_motivo_encerramento"] = int(id_motivo_encerramento)

        if _coluna_existe(TABELA_CARD, "MotivoEncerramentoObs"):
            campos_update.append("MotivoEncerramentoObs = :motivo_obs")
            params_update["motivo_obs"] = (descricao[:2000] if descricao else observacao_inativacao[:2000])

        if _coluna_existe(TABELA_CARD, "EncerradoEm"):
            campos_update.append("EncerradoEm = ISNULL(EncerradoEm, GETDATE())")

        if _coluna_existe(TABELA_CARD, "EncerradoPor"):
            campos_update.append("EncerradoPor = ISNULL(EncerradoPor, :id_usuario)")

        if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
            campos_update.append("AtualizadoEm = GETDATE()")

        if _coluna_existe(TABELA_CARD, "AtualizadoPor"):
            campos_update.append("AtualizadoPor = :id_usuario")

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET {', '.join(campos_update)}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1;
        """)

        resultado_upd = db.session.execute(sql_upd, params_update)

        if int(getattr(resultado_upd, "rowcount", 0) or 0) <= 0:
            raise RuntimeError("Nenhuma linha foi atualizada ao inativar o card.")

        id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
            id_kanban=id_kanban,
            id_empresa_padrao=id_empresa_card,
        )

        sql_ins_mov = text(f"""
            INSERT INTO {TABELA_CARD_MOVIMENTO}
            (
                IDFatoKanbanCard,
                IDFaseDe,
                IDFasePara,
                MovidoEm,
                MovidoPor,
                Observacao,
                IDEmpresaProprietaria,
                IDDimKanbanTag,
                IDDimKanbanStatusCard,
                IDDimKanban
            )
            OUTPUT INSERTED.IDFatoKanbanCardMovimento
            VALUES
            (
                :id_card,
                :id_fase_de,
                :id_fase_para,
                GETDATE(),
                :movido_por,
                :obs,
                :id_empresa,
                NULL,
                :id_status_card,
                :id_kanban
            );
        """)

        params_mov = {
            "id_card": id_card,
            "id_fase_de": id_fase_atual,
            "id_fase_para": id_fase_para_movimento,
            "movido_por": id_usuario,
            "obs": observacao_inativacao[:2000],
            "id_empresa": id_empresa_movimento,
            "id_status_card": int(id_status_inativacao) if id_status_inativacao is not None else None,
            "id_kanban": int(id_kanban),
        }

        row_mov = db.session.execute(sql_ins_mov, params_mov).mappings().first()

        row_hist_enc = _registrar_historico_encerramento_card(
            id_card=id_card,
            id_motivo_encerramento=id_motivo_encerramento,
            nome_motivo=motivo_texto,
            id_fase=id_fase_para_movimento,
            id_usuario=id_usuario,
            observacoes=descricao,
        )

        _registrar_status_historico_card(
            id_card=int(id_card),
            id_fase=int(id_fase_para_movimento),
            id_status_card=int(id_status_inativacao) if id_status_inativacao is not None else None,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
        )

        row_observacao = _registrar_observacao_historica_card(
            id_card=id_card,
            texto_observacao=observacao_inativacao,
            id_usuario=id_usuario,
            id_status_card=id_status_inativacao,
            id_fase=id_fase_para_movimento,
        )

        _remover_tag_em_atendimento_do_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_usuario=int(id_usuario),
        )

        sincronizacao_reservas = _sincronizar_reservas_painel_faces_kanban(
            id_card=int(id_card),
            titulo_card=str(card_escopo.get("Titulo") or "").strip(),
            id_empresa_relacionada=_obter_id_empresa_relacionada_card(card_escopo),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
            cancelar_todas=True,
        )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_INATIVADO",
            id_fase_de=id_fase_atual,
            id_fase_para=id_fase_para_movimento,
            motivo=motivo_texto,
            observacao=descricao or observacao_inativacao,
            tabela_origem=TABELA_HISTORICO_ENCERRAMENTO_CARD,
            id_registro_origem=int(row_hist_enc.get("IDFatoDimHistoricoEncerramentoCard") or 0),
            payload_antes=snapshot_antes,
            payload_depois=snapshot_depois,
        )

        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

        _emitir_evento_kanban(
            id_kanban,
            "card_inativado",
            {
                "id_card": id_card,
                "id_fase_de": id_fase_atual,
                "id_fase_para": id_fase_para_movimento,
                "motivo": motivo_texto,
                "motivo_codigo": motivo_normalizado.get("Codigo"),
                "id_motivo_encerramento": id_motivo_encerramento,
                "descricao": descricao or None,
                "sincronizacao_reservas": sincronizacao_reservas,
                "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
                "id_movimento": int(row_mov.get("IDFatoKanbanCardMovimento") or 0) if row_mov else None,
                "id_observacao": int(row_observacao.get("IDFatoKanbanCardObservacoes") or 0) if row_observacao else None,
            },
        )

        current_app.logger.info(
            "KANBAN: card inativado com sucesso. id_card=%s id_usuario=%s motivo=%s codigo=%s id_motivo=%s",
            id_card,
            id_usuario,
            motivo_texto,
            motivo_normalizado.get("Codigo"),
            id_motivo_encerramento,
        )

        return jsonify(
            {
                "ok": True,
                "motivo": motivo_texto,
                "motivo_codigo": motivo_normalizado.get("Codigo"),
                "id_motivo_encerramento": id_motivo_encerramento,
                "sincronizacao_reservas": sincronizacao_reservas,
                "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
                "id_movimento": int(row_mov.get("IDFatoKanbanCardMovimento") or 0) if row_mov else None,
                "id_observacao": int(row_observacao.get("IDFatoKanbanCardObservacoes") or 0) if row_observacao else None,
            }
        )

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao inativar card: {str(exc)}"}), 500









def _obter_tag_por_nome(id_kanban: int, nome_tag: str, *, somente_ativa: bool = True) -> dict[str, Any] | None:
    nome_limpo = str(nome_tag or "").strip()
    if not id_kanban or not nome_limpo:
        return None

    filtro_ativo = "AND ISNULL(t.Ativo, 1) = 1" if somente_ativa else ""

    sql = text(f"""
        SELECT TOP (1)
            t.IDDimKanbanTag,
            t.IDDimKanban,
            t.NomeTag,
            t.TipoTag,
            t.CorHex,
            ISNULL(t.Ativo, 1) AS Ativo,
            t.IDEmpresaProprietaria
        FROM [Kanban].[Silver].[DimKanbanTag] t
        WHERE t.IDDimKanban = :id_kanban
          AND UPPER(LTRIM(RTRIM(ISNULL(t.NomeTag, '')))) = UPPER(LTRIM(RTRIM(:nome_tag)))
          {filtro_ativo}
        ORDER BY t.IDDimKanbanTag ASC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "nome_tag": nome_limpo,
        },
    ).mappings().first()

    return dict(row) if row else None






@kanban_bp.route("/api/fases/<int:id_fase>/inativar", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_fase_inativar(id_fase: int):
    id_usuario = _assert_login()
    fase_escopo = _obter_fase_autorizada(id_fase)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(fase_escopo.get("IDDimKanban") or 0)

    sql_fase = text(f"""
        SELECT f.IDDimKanbanFase, f.IDDimKanban
        FROM {TABELA_KANBAN_FASE} f
        WHERE f.IDDimKanbanFase = :id_fase
          AND f.Ativo = 1;
    """)
    row = db.session.execute(sql_fase, {"id_fase": id_fase}).mappings().first()
    if not row:
        return jsonify({"ok": False, "msg": "Fase não encontrada ou já inativa"}), 404
    if int(row["IDDimKanban"]) != id_kanban:
        return jsonify({"ok": False, "msg": "Fase fora do escopo do usuário"}), 403

    quantidade_cards_ativos = _contar_cards_ativos_fase(id_fase)
    if quantidade_cards_ativos > 0:
        return jsonify(
            {
                "ok": False,
                "msg": "Não é permitido inativar uma fase que ainda possui cards ativos.",
                "QuantidadeCardsAtivos": quantidade_cards_ativos,
            }
        ), 409

    try:
        campos = ["Ativo = 0", "InativadoEm = GETDATE()", "InativadoPor = :id_usuario"]
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoEm"):
            campos.append("AtualizadoEm = GETDATE()")
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoPor"):
            campos.append("AtualizadoPor = :id_usuario")

        sql_upd = text(f"""
            UPDATE {TABELA_KANBAN_FASE}
            SET {', '.join(campos)}
            WHERE IDDimKanbanFase = :id_fase
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_fase": id_fase})
        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
        _emitir_evento_kanban(id_kanban, "fase_inativada", {"id_fase": id_fase})
        return jsonify({"ok": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar fase id_fase=%s", id_fase)
        return jsonify({"ok": False, "msg": f"Erro ao inativar fase: {str(exc)}"}), 500



@kanban_bp.route("/api/kanbans/<int:id_kanban>/inativar", methods=["POST"])
@login_required
@limiter.limit("30/minute")
def api_kanban_inativar(id_kanban: int):
    id_usuario = _assert_login()
    kanban = _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    quantidade_cards_ativos = _contar_cards_ativos_kanban(id_kanban)
    if quantidade_cards_ativos > 0:
        return jsonify(
            {
                "ok": False,
                "msg": "Não é permitido inativar o kanban enquanto existirem cards ativos.",
                "QuantidadeCardsAtivos": quantidade_cards_ativos,
            }
        ), 409

    try:
        sql_upd = text(f"""
            UPDATE {TABELA_KANBAN}
            SET Ativo = 0,
                InativadoEm = GETDATE(),
                InativadoPor = :id_usuario
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_kanban": id_kanban})

        campos_fase = [
            "Ativo = 0",
            "InativadoEm = ISNULL(InativadoEm, GETDATE())",
            "InativadoPor = ISNULL(InativadoPor, :id_usuario)",
        ]
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoEm"):
            campos_fase.append("AtualizadoEm = GETDATE()")
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoPor"):
            campos_fase.append("AtualizadoPor = :id_usuario")

        sql_upd_fases = text(f"""
            UPDATE {TABELA_KANBAN_FASE}
            SET {', '.join(campos_fase)}
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd_fases, {"id_usuario": id_usuario, "id_kanban": id_kanban})

        db.session.commit()
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
        _emitir_evento_kanban(
            id_kanban,
            "kanban_inativado",
            {"IDDimKanban": id_kanban, "NomeKanban": kanban.get("NomeKanban")},
        )
        return jsonify({"ok": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar kanban id_kanban=%s", id_kanban)
        return jsonify({"ok": False, "msg": f"Erro ao inativar kanban: {str(exc)}"}), 500














def _sql_join_resumo_paineis_card(alias_card: str, alias_resumo: str = "rp") -> str:
    return f"""
        OUTER APPLY (
            SELECT
                COUNT(1) AS QuantidadePaineisVinculados,
                COUNT(DISTINCT TRY_CONVERT(int, pf.IDDimPaineisEuromidia)) AS QuantidadePaineisUnicos,
                CAST(
                    SUM(
                        COALESCE(
                            TRY_CONVERT(decimal(18, 2), pf.ValorVendaFinal),
                            TRY_CONVERT(decimal(18, 2), pf.NovoValor),
                            TRY_CONVERT(decimal(18, 2), pf.ValorTabela),
                            0
                        )
                    )
                    AS decimal(18, 2)
                ) AS ValorTotalPaineis
            FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
            WHERE pf.IDFatoKanbanCard = {alias_card}.IDFatoKanbanCard
              AND ISNULL(pf.Ativo, 1) = 1
        ) {alias_resumo}
    """













def _executar_sql_mapeado(sql: str, parametros: dict | None = None) -> list[dict]:
    resultado = db.session.execute(text(sql), parametros or {})
    return [dict(linha) for linha in resultado.mappings().all()]


"""Eu limpo texto de filtro para evitar espaços sobrando."""
def _normalizar_texto_filtro(texto: str | None) -> str:
    return " ".join((texto or "").strip().split())


"""Eu busco as fases disponíveis para montar o filtro da tela."""
def _listar_fases_historico_cards(id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        f.IDDimKanbanFase AS id_fase,
        f.IDDimKanban AS id_kanban,
        f.NomeFase AS nome_fase,
        f.OrdemFase AS ordem_fase,
        f.TipoFase AS tipo_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(f.CorHex, ''))), '') AS cor_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(f.CorTextoHex, ''))), '') AS cor_texto_fase
    FROM [Kanban].[Silver].[DimKanbanFase] f
    WHERE ISNULL(f.Ativo, 1) = 1
      AND EXISTS (
            SELECT 1
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.IDDimKanban = f.IDDimKanban
      )
    ORDER BY
        f.IDDimKanban,
        ISNULL(f.OrdemFase, 999999),
        f.NomeFase
    """
    return _executar_sql_mapeado(
        sql,
        {"id_empresa_proprietaria": id_empresa_proprietaria},
    )



def _listar_cards_resumo_historico(
    id_empresa_proprietaria: int,
    termo_busca: str = "",
    id_fase: int | None = None,
    status_card: str | None = None,
    somente_ativos: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> list[dict]:
    termo_busca = _normalizar_texto_filtro(termo_busca)

    try:
        offset = max(int(offset or 0), 0)
    except Exception:
        offset = 0

    try:
        limit = int(limit or 10)
    except Exception:
        limit = 10

    limit = max(1, min(limit, 100))
    termo_like = f"%{termo_busca}%"

    sql = """
    SELECT
        c.IDFatoKanbanCard AS id_card,
        c.Titulo AS titulo,
        c.Descricao AS descricao,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.EncerradoEm AS encerrado_em,
        c.Ativo AS ativo,
        c.StatusCard AS status_card,
        c.IDDimKanbanStatusCard AS id_status_card,
        c.IDEmpresa AS id_empresa_relacionada,
        c.IDVendedor AS id_vendedor,
        c.IDVendedorUsuario AS id_vendedor_usuario,
        c.IDDimUsuarios AS id_usuario_criador,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,

        NULLIF(LTRIM(RTRIM(ISNULL(emp.RazaoSocial, ''))), '') AS razao_social_empresa_relacionada,
        NULLIF(LTRIM(RTRIM(ISNULL(emp.NomeFantasia, ''))), '') AS nome_fantasia_empresa_relacionada,
        NULLIF(LTRIM(RTRIM(ISNULL(emp.CNPJ, ''))), '') AS cnpj_empresa_relacionada,

        fase.NomeFase AS nome_fase_atual,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorHex, ''))), '') AS cor_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorTextoHex, ''))), '') AS cor_texto_fase,

        usuario.NomeUsuario AS nome_usuario_responsavel,

        ISNULL(obs.total_observacoes, 0) AS total_observacoes,
        ISNULL(mov.total_movimentacoes, 0) AS total_movimentacoes,
        ISNULL(tag.total_tags_ativas, 0) AS total_tags_ativas,
        ISNULL(item.total_itens_ativos, 0) AS total_itens_ativos,
        ISNULL(preco.total_alteracoes_preco, 0) AS total_alteracoes_preco,
        atividade.ultima_atividade_em AS ultima_atividade_em

    FROM [Kanban].[Silver].[FatoKanbanCard] c

    LEFT JOIN [Integracao].[Silver].[DimEmpresas] emp
        ON emp.IDEmpresa = c.IDEmpresa

    LEFT JOIN [Kanban].[Silver].[DimKanbanFase] fase
        ON fase.IDDimKanbanFase = c.IDDimKanbanFaseAtual
       AND fase.IDDimKanban = c.IDDimKanban

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario
        ON usuario.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
       AND usuario.IDEmpresaProprietaria = c.IDEmpresaProprietaria

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_observacoes,
            MAX(o.CriadoEm) AS ultima_observacao_em
        FROM [Kanban].[Silver].[FatoKanbanCardObservacoes] o
        WHERE o.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND o.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) obs

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_movimentacoes,
            MAX(m.MovidoEm) AS ultimo_movimento_em
        FROM [Kanban].[Silver].[FatoKanbanCardMovimento] m
        WHERE m.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND m.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) mov

    OUTER APPLY (
        SELECT
            SUM(CASE WHEN t.RemovidoEm IS NULL THEN 1 ELSE 0 END) AS total_tags_ativas,
            MAX(COALESCE(t.RemovidoEm, t.AplicadoEm)) AS ultimo_evento_tag_em
        FROM [Kanban].[Silver].[FatoKanbanCardTag] t
        WHERE t.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND t.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) tag

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN ISNULL(i.Ativo, 1) = 1 AND i.RemovidoEm IS NULL THEN 1
                    ELSE 0
                END
            ) AS total_itens_ativos,
            MAX(COALESCE(i.RemovidoEm, i.DataAtualizacao, i.CriadoEm)) AS ultimo_item_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) item

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ) AS total_alteracoes_preco,
            MAX(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN COALESCE(i.DataAtualizacao, i.CriadoEm)
                    ELSE NULL
                END
            ) AS ultima_alteracao_preco_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) preco

    OUTER APPLY (
        SELECT
            MAX(v.data_evento) AS ultima_atividade_em
        FROM (
            VALUES
                (c.CriadoEm),
                (c.AtualizadoEm),
                (c.EncerradoEm),
                (obs.ultima_observacao_em),
                (mov.ultimo_movimento_em),
                (tag.ultimo_evento_tag_em),
                (item.ultimo_item_em),
                (preco.ultima_alteracao_preco_em)
        ) v(data_evento)
    ) atividade

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND (:id_fase IS NULL OR c.IDDimKanbanFaseAtual = :id_fase)
      AND (:status_card IS NULL OR LTRIM(RTRIM(ISNULL(c.StatusCard, ''))) = :status_card)
      AND (:somente_ativos = 0 OR ISNULL(c.Ativo, 1) = 1)
      AND (
            :termo_busca = ''
            OR ISNULL(c.Titulo, '') LIKE :termo_like
            OR ISNULL(c.Descricao, '') LIKE :termo_like
            OR CAST(c.IDFatoKanbanCard AS VARCHAR(30)) LIKE :termo_like
            OR CAST(ISNULL(c.IDEmpresa, '') AS VARCHAR(30)) LIKE :termo_like
            OR ISNULL(emp.RazaoSocial, '') LIKE :termo_like
            OR ISNULL(emp.NomeFantasia, '') LIKE :termo_like
            OR ISNULL(emp.CNPJ, '') LIKE :termo_like
          )

    ORDER BY
        COALESCE(atividade.ultima_atividade_em, c.AtualizadoEm, c.CriadoEm) DESC,
        c.IDFatoKanbanCard DESC

    OFFSET :offset ROWS
    FETCH NEXT :limit ROWS ONLY
    """

    return _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_fase": id_fase,
            "status_card": status_card,
            "somente_ativos": 1 if somente_ativos else 0,
            "termo_busca": termo_busca,
            "termo_like": termo_like,
            "offset": int(offset),
            "limit": int(limit),
        },
    )






"""Eu busco os status existentes para montar o filtro da tela."""
def _listar_status_historico_cards(id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT DISTINCT
        LTRIM(RTRIM(c.StatusCard)) AS status_card
    FROM [Kanban].[Silver].[FatoKanbanCard] c
    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND NULLIF(LTRIM(RTRIM(c.StatusCard)), '') IS NOT NULL
    ORDER BY
        LTRIM(RTRIM(c.StatusCard))
    """
    return _executar_sql_mapeado(
        sql,
        {"id_empresa_proprietaria": id_empresa_proprietaria},
    )






"""Eu busco a lista resumida de cards para a tela de histórico com paginação e fase correta."""
def _listar_cards_resumo_historico(
    id_empresa_proprietaria: int,
    termo_busca: str = "",
    id_fase: int | None = None,
    status_card: str | None = None,
    somente_ativos: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> list[dict]:
    termo_busca = _normalizar_texto_filtro(termo_busca)

    try:
        offset = max(int(offset or 0), 0)
    except Exception:
        offset = 0

    try:
        limit = int(limit or 10)
    except Exception:
        limit = 10

    limit = max(1, min(limit, 100))
    termo_like = f"%{termo_busca}%"

    sql = """
    SELECT
        c.IDFatoKanbanCard AS id_card,
        c.Titulo AS titulo,
        c.Descricao AS descricao,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.EncerradoEm AS encerrado_em,
        c.Ativo AS ativo,
        c.StatusCard AS status_card,
        c.IDDimKanbanStatusCard AS id_status_card,
        c.IDEmpresa AS id_empresa_relacionada,
        c.IDVendedor AS id_vendedor,
        c.IDVendedorUsuario AS id_vendedor_usuario,
        c.IDDimUsuarios AS id_usuario_criador,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,

        fase.NomeFase AS nome_fase_atual,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorHex, ''))), '') AS cor_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorTextoHex, ''))), '') AS cor_texto_fase,

        usuario.NomeUsuario AS nome_usuario_responsavel,

        ISNULL(obs.total_observacoes, 0) AS total_observacoes,
        ISNULL(mov.total_movimentacoes, 0) AS total_movimentacoes,
        ISNULL(tag.total_tags_ativas, 0) AS total_tags_ativas,
        ISNULL(item.total_itens_ativos, 0) AS total_itens_ativos,
        ISNULL(preco.total_alteracoes_preco, 0) AS total_alteracoes_preco,
        atividade.ultima_atividade_em AS ultima_atividade_em

    FROM [Kanban].[Silver].[FatoKanbanCard] c

    LEFT JOIN [Kanban].[Silver].[DimKanbanFase] fase
        ON fase.IDDimKanbanFase = c.IDDimKanbanFaseAtual

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario
        ON usuario.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
       AND usuario.IDEmpresaProprietaria = c.IDEmpresaProprietaria

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_observacoes,
            MAX(o.CriadoEm) AS ultima_observacao_em
        FROM [Kanban].[Silver].[FatoKanbanCardObservacoes] o
        WHERE o.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND o.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) obs

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_movimentacoes,
            MAX(m.MovidoEm) AS ultimo_movimento_em
        FROM [Kanban].[Silver].[FatoKanbanCardMovimento] m
        WHERE m.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND m.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) mov

    OUTER APPLY (
        SELECT
            SUM(CASE WHEN t.RemovidoEm IS NULL THEN 1 ELSE 0 END) AS total_tags_ativas,
            MAX(COALESCE(t.RemovidoEm, t.AplicadoEm)) AS ultimo_evento_tag_em
        FROM [Kanban].[Silver].[FatoKanbanCardTag] t
        WHERE t.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND t.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) tag

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN ISNULL(i.Ativo, 1) = 1 AND i.RemovidoEm IS NULL THEN 1
                    ELSE 0
                END
            ) AS total_itens_ativos,
            MAX(COALESCE(i.RemovidoEm, i.DataAtualizacao, i.CriadoEm)) AS ultimo_item_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) item

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ) AS total_alteracoes_preco,
            MAX(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN COALESCE(i.DataAtualizacao, i.CriadoEm)
                    ELSE NULL
                END
            ) AS ultima_alteracao_preco_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) preco

    OUTER APPLY (
        SELECT
            MAX(v.data_evento) AS ultima_atividade_em
        FROM (
            VALUES
                (c.CriadoEm),
                (c.AtualizadoEm),
                (c.EncerradoEm),
                (obs.ultima_observacao_em),
                (mov.ultimo_movimento_em),
                (tag.ultimo_evento_tag_em),
                (item.ultimo_item_em),
                (preco.ultima_alteracao_preco_em)
        ) v(data_evento)
    ) atividade

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND (:id_fase IS NULL OR c.IDDimKanbanFaseAtual = :id_fase)
      AND (:status_card IS NULL OR LTRIM(RTRIM(ISNULL(c.StatusCard, ''))) = :status_card)
      AND (:somente_ativos = 0 OR ISNULL(c.Ativo, 1) = 1)
      AND (
            :termo_busca = ''
            OR ISNULL(c.Titulo, '') LIKE :termo_like
            OR ISNULL(c.Descricao, '') LIKE :termo_like
            OR CAST(c.IDFatoKanbanCard AS VARCHAR(30)) LIKE :termo_like
            OR CAST(ISNULL(c.IDEmpresa, '') AS VARCHAR(30)) LIKE :termo_like
          )

    ORDER BY
        COALESCE(atividade.ultima_atividade_em, c.AtualizadoEm, c.CriadoEm) DESC,
        c.IDFatoKanbanCard DESC

    OFFSET :offset ROWS
    FETCH NEXT :limit ROWS ONLY
    """

    return _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_fase": id_fase,
            "status_card": status_card,
            "somente_ativos": 1 if somente_ativos else 0,
            "termo_busca": termo_busca,
            "termo_like": termo_like,
            "offset": int(offset),
            "limit": int(limit),
        },
    )





def _contar_cards_resumo_historico(
    id_empresa_proprietaria: int,
    termo_busca: str = "",
    id_fase: int | None = None,
    status_card: str | None = None,
    somente_ativos: bool = True,
) -> int:
    termo_busca = _normalizar_texto_filtro(termo_busca)
    termo_like = f"%{termo_busca}%"

    sql = """
    SELECT
        COUNT(1) AS total
    FROM [Kanban].[Silver].[FatoKanbanCard] c

    LEFT JOIN [Integracao].[Silver].[DimEmpresas] emp
        ON emp.IDEmpresa = c.IDEmpresa

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND (:id_fase IS NULL OR c.IDDimKanbanFaseAtual = :id_fase)
      AND (:status_card IS NULL OR LTRIM(RTRIM(ISNULL(c.StatusCard, ''))) = :status_card)
      AND (:somente_ativos = 0 OR ISNULL(c.Ativo, 1) = 1)
      AND (
            :termo_busca = ''
            OR ISNULL(c.Titulo, '') LIKE :termo_like
            OR ISNULL(c.Descricao, '') LIKE :termo_like
            OR CAST(c.IDFatoKanbanCard AS VARCHAR(30)) LIKE :termo_like
            OR CAST(ISNULL(c.IDEmpresa, '') AS VARCHAR(30)) LIKE :termo_like
            OR ISNULL(emp.RazaoSocial, '') LIKE :termo_like
            OR ISNULL(emp.NomeFantasia, '') LIKE :termo_like
            OR ISNULL(emp.CNPJ, '') LIKE :termo_like
          )
    """

    linhas = _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_fase": id_fase,
            "status_card": status_card,
            "somente_ativos": 1 if somente_ativos else 0,
            "termo_busca": termo_busca,
            "termo_like": termo_like,
        },
    )

    if not linhas:
        return 0

    try:
        return int(linhas[0].get("total") or 0)
    except Exception:
        return 0





@kanban_bp.route("/historico-cards", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def historico_cards_lista():
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    termo_busca = _normalizar_texto_filtro(request.args.get("q") or "")
    status_card = (request.args.get("status") or "").strip() or None

    try:
        id_fase = int(request.args.get("id_fase") or "0")
        if id_fase <= 0:
            id_fase = None
    except Exception:
        id_fase = None

    try:
        page = int(request.args.get("page") or "1")
    except Exception:
        page = 1

    per_page = 10
    page = max(1, page)
    offset = (page - 1) * per_page

    # Regra nova:
    # histórico deve mostrar TODOS os cards, inclusive removidos/inativados.
    # Por isso não leio mais request.args.get("somente_ativos").
    somente_ativos = False

    total = _contar_cards_resumo_historico(
        id_empresa_proprietaria=id_empresa_proprietaria,
        termo_busca=termo_busca,
        id_fase=id_fase,
        status_card=status_card,
        somente_ativos=somente_ativos,
    )

    total_pages = max(1, (total + per_page - 1) // per_page)

    if page > total_pages:
        page = total_pages
        offset = (page - 1) * per_page

    cards = _listar_cards_resumo_historico(
        id_empresa_proprietaria=id_empresa_proprietaria,
        termo_busca=termo_busca,
        id_fase=id_fase,
        status_card=status_card,
        somente_ativos=somente_ativos,
        offset=offset,
        limit=per_page,
    )

    fases = _listar_fases_historico_cards(id_empresa_proprietaria)
    opcoes_status = _listar_status_historico_cards(id_empresa_proprietaria)

    filtros = {
        "q": termo_busca,
        "id_fase": id_fase,
        "status": status_card or "",
        "somente_ativos": False,
        "per_page": per_page,
    }

    inicio = 0 if total == 0 else offset + 1
    fim = min(offset + per_page, total)

    paginacao = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "inicio": inicio,
        "fim": fim,
    }

    return render_template(
        "kanban/historico_cards_lista.html",
        cards=cards,
        fases=fases,
        opcoes_status=opcoes_status,
        filtros=filtros,
        paginacao=paginacao,
        total_cards=total,
    )












def _normalizar_payload_historico(payload) -> str:
    if payload is None:
        return ""

    if isinstance(payload, str):
        texto = payload.strip()
        return texto

    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload).strip()


"""Eu tento padronizar a data para ordenar a timeline corretamente."""
def _normalizar_data_evento_historico(valor):
    if valor is None:
        return None

    if isinstance(valor, datetime):
        return valor

    try:
        return datetime.fromisoformat(str(valor))
    except Exception:
        return None



def _buscar_cabecalho_historico_card(id_card: int, id_empresa_proprietaria: int) -> dict | None:
    sql = """
    SELECT TOP 1
        c.IDFatoKanbanCard AS id_card,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,
        c.Titulo AS titulo,
        c.Descricao AS descricao,
        c.StatusCard AS status_card,
        c.IDDimKanbanStatusCard AS id_status_card,
        c.Ativo AS ativo,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.EncerradoEm AS encerrado_em,
        c.IDEmpresa AS id_empresa_relacionada,
        c.IDVendedor AS id_vendedor,
        c.IDVendedorUsuario AS id_vendedor_usuario,
        c.IDDimUsuarios AS id_usuario_criador,
        c.IDDimKanbanMotivoEncerramento AS id_motivo_encerramento,
        c.MotivoEncerramentoObs AS motivo_encerramento_obs,

        fase.NomeFase AS nome_fase_atual,
        fase.CorHex AS cor_fase,
        fase.CorTextoHex AS cor_texto_fase,

        usuario.NomeUsuario AS nome_usuario_responsavel,
        motivo.NomeMotivo AS nome_motivo_encerramento

    FROM [Kanban].[Silver].[FatoKanbanCard] c

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase,
            f.CorHex,
            f.CorTextoHex
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = c.IDDimKanbanFaseAtual
        ORDER BY
            CASE
                WHEN f.IDDimKanban = c.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, c.IDEmpresaProprietaria) = c.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = c.IDDimKanban THEN 1
                WHEN ISNULL(f.IDEmpresaProprietaria, c.IDEmpresaProprietaria) = c.IDEmpresaProprietaria THEN 2
                ELSE 3
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = c.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria IS NULL THEN 1
                ELSE 2
            END,
            u.IDDimUsuarios
    ) usuario

    LEFT JOIN [Kanban].[Silver].[DimKanbanMotivoEncerramento] motivo
        ON motivo.IDDimKanbanMotivoEncerramento = c.IDDimKanbanMotivoEncerramento

    WHERE c.IDFatoKanbanCard = :id_card
      AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
    """
    linhas = _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )
    return linhas[0] if linhas else None





"""Eu busco as movimentações de fase do card."""
def _buscar_movimentacoes_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        m.IDFatoKanbanCardMovimento AS id_movimento,
        m.IDFatoKanbanCard AS id_card,
        m.IDFaseDe AS id_fase_de,
        m.IDFasePara AS id_fase_para,
        m.MovidoEm AS movido_em,
        m.MovidoPor AS id_usuario,
        m.Observacao AS observacao,
        m.IDDimKanbanTag AS id_tag,
        m.IDDimKanbanStatusCard AS id_status_card,
        m.IDDimKanban AS id_kanban,
        m.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        COALESCE(
            fase_de.NomeFase,
            CASE
                WHEN m.IDFaseDe IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), m.IDFaseDe))
                ELSE NULL
            END
        ) AS nome_fase_de,

        COALESCE(
            fase_para.NomeFase,
            CASE
                WHEN m.IDFasePara IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), m.IDFasePara))
                ELSE NULL
            END
        ) AS nome_fase_para,

        usuario.NomeUsuario AS nome_usuario

    FROM [Kanban].[Silver].[FatoKanbanCardMovimento] m

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = m.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = m.IDFaseDe
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, m.IDEmpresaProprietaria) = m.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_de

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = m.IDFasePara
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, m.IDEmpresaProprietaria) = m.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_para

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = m.MovidoPor
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = m.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    WHERE m.IDFatoKanbanCard = :id_card

    ORDER BY
        m.MovidoEm DESC,
        m.IDFatoKanbanCardMovimento DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )


"""Eu busco as observações do card em ordem da mais recente para a mais antiga."""
def _buscar_observacoes_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        o.IDFatoKanbanCardObservacoes AS id_observacao,
        o.Observacao AS observacao,
        o.IDFatoKanbanCard AS id_card,
        o.IDDimKanbanStatusCard AS id_status_card,
        o.IDDimKanbanFase AS id_fase,
        o.IDDimUsuarios AS id_usuario,
        o.CriadoEm AS criado_em,
        o.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        COALESCE(
            fase.NomeFase,
            CASE
                WHEN o.IDDimKanbanFase IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), o.IDDimKanbanFase))
                ELSE NULL
            END
        ) AS nome_fase,

        usuario.NomeUsuario AS nome_usuario

    FROM [Kanban].[Silver].[FatoKanbanCardObservacoes] o

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = o.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = o.IDDimKanbanFase
        ORDER BY
            CASE
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, o.IDEmpresaProprietaria) = o.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = card_aut.IDDimKanban THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = o.IDDimUsuarios
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = o.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    WHERE o.IDFatoKanbanCard = :id_card

    ORDER BY
        o.CriadoEm DESC,
        o.IDFatoKanbanCardObservacoes DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )


"""Eu busco o histórico de status do card."""
def _buscar_status_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        s.IDDimKanbanCardTagHistorico AS id_status_historico,
        s.IDFatoKanbanCard AS id_card,
        s.IDDimKanbanStatusCard AS id_status_card,
        s.IDDimKanbanFase AS id_fase,
        s.IDDimUsuarios AS id_usuario,
        s.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        COALESCE(
            fase.NomeFase,
            CASE
                WHEN s.IDDimKanbanFase IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), s.IDDimKanbanFase))
                ELSE NULL
            END
        ) AS nome_fase,

        usuario.NomeUsuario AS nome_usuario,
        status_card.CodigoStatus AS codigo_status,
        status_card.NomeExibicao AS nome_status

    FROM [Kanban].[Silver].[FatoKanbanCardStatusHistorico] s

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = s.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = s.IDDimKanbanFase
        ORDER BY
            CASE
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, s.IDEmpresaProprietaria) = s.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = card_aut.IDDimKanban THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = s.IDDimUsuarios
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = s.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    LEFT JOIN [Kanban].[Silver].[DimKanbanStatusCard] status_card
        ON status_card.IDDimKanbanStatusCard = s.IDDimKanbanStatusCard

    WHERE s.IDFatoKanbanCard = :id_card

    ORDER BY
        s.IDDimKanbanCardTagHistorico DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )



"""Eu busco o histórico de encerramento do card."""
def _buscar_encerramento_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        e.IDFatoDimHistoricoEncerramentoCard AS id_historico_encerramento,
        e.NomeMotivo AS nome_motivo,
        e.IDDimKanbanMotivoEncerramento AS id_motivo_encerramento,
        e.IDDimKanbanFase AS id_fase,
        e.IDFatoKanbanCard AS id_card,
        e.IDDimUsuarios AS id_usuario,
        e.DataAtualizacao AS data_atualizacao,
        e.Observacoes AS observacoes,

        COALESCE(
            fase.NomeFase,
            CASE
                WHEN e.IDDimKanbanFase IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), e.IDDimKanbanFase))
                ELSE NULL
            END
        ) AS nome_fase,

        usuario.NomeUsuario AS nome_usuario

    FROM [Kanban].[Silver].[FatoDimHistoricoEncerramentoCard] e

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = e.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = e.IDDimKanbanFase
        ORDER BY
            CASE
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = card_aut.IDDimKanban THEN 1
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 2
                ELSE 3
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = e.IDDimUsuarios
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria IS NULL THEN 1
                ELSE 2
            END,
            u.IDDimUsuarios
    ) usuario

    WHERE e.IDFatoKanbanCard = :id_card

    ORDER BY
        e.DataAtualizacao DESC,
        e.IDFatoDimHistoricoEncerramentoCard DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )





"""Eu busco os logs técnicos do card."""
def _buscar_logs_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        l.IDFatoKanbanCardLog AS id_log,
        l.IDFatoKanbanCard AS id_card,
        l.IDDimKanban AS id_kanban,
        l.IDEmpresaRelacionada AS id_empresa_relacionada,
        l.IDUsuarioAcao AS id_usuario_acao,
        l.TipoEvento AS tipo_evento,
        l.SubtipoEvento AS subtipo_evento,
        l.OcorridoEm AS ocorrido_em,
        l.IDFaseDe AS id_fase_de,
        l.IDFasePara AS id_fase_para,
        l.IDDimKanbanMotivoAcao AS id_motivo_acao,
        l.TabelaOrigem AS tabela_origem,
        l.IDRegistroOrigem AS id_registro_origem,
        l.TextoLivre AS texto_livre,
        l.PayloadAntes AS payload_antes,
        l.PayloadDepois AS payload_depois,
        l.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        usuario.NomeUsuario AS nome_usuario_acao,

        COALESCE(
            fase_de.NomeFase,
            CASE
                WHEN l.IDFaseDe IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), l.IDFaseDe))
                ELSE NULL
            END
        ) AS nome_fase_de,

        COALESCE(
            fase_para.NomeFase,
            CASE
                WHEN l.IDFasePara IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), l.IDFasePara))
                ELSE NULL
            END
        ) AS nome_fase_para

    FROM [Kanban].[Silver].[FatoKanbanCardLog] l

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = l.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = l.IDUsuarioAcao
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = l.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = l.IDFaseDe
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, l.IDEmpresaProprietaria) = l.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_de

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = l.IDFasePara
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, l.IDEmpresaProprietaria) = l.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_para

    WHERE l.IDFatoKanbanCard = :id_card

    ORDER BY
        l.OcorridoEm DESC,
        l.IDFatoKanbanCardLog DESC
    """
    linhas = _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )

    for linha in linhas:
        linha["payload_antes_texto"] = _normalizar_payload_historico(linha.get("payload_antes"))
        linha["payload_depois_texto"] = _normalizar_payload_historico(linha.get("payload_depois"))

    return linhas




def _decimal_para_float_seguro(valor: Any) -> float | None:

    """Eu tento converter qualquer número do banco para float sem estourar a tela."""
    if valor in (None, ""):
        return None

    try:
        return float(valor)
    except Exception:
        try:
            return float(Decimal(str(valor)))
        except Exception:
            return None





def _buscar_historico_precos_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    """
    Eu busco o histórico real de negociação de preços.

    Regra:
    - pendências NÃO entram aqui; pendência fica em FatoAprovaPreco;
    - histórico vem de FatoKanbanNegociacaoPreco;
    - se o card estiver vinculado a contrato, também trago negociações do contrato vinculado;
    - filtro por empresa proprietária para não misturar histórico de outro contexto.
    """

    sql = f"""
    ;WITH ContratosDoCard AS (
        SELECT DISTINCT
            TRY_CONVERT(int, i.IDFatoControleContratoEuromidia) AS IDFatoControleContratosEuromidia
        FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
        WHERE i.IDFatoKanbanCard = :id_card
          AND TRY_CONVERT(int, i.IDFatoControleContratoEuromidia) IS NOT NULL
          AND ISNULL(i.BitAtivo, 1) = 1

        UNION

        SELECT DISTINCT
            TRY_CONVERT(int, np_card.IDFatoControleContratosEuromidia) AS IDFatoControleContratosEuromidia
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np_card
        WHERE np_card.IDFatoKanbanCard = :id_card
          AND np_card.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND TRY_CONVERT(int, np_card.IDFatoControleContratosEuromidia) IS NOT NULL

        UNION

        SELECT DISTINCT
            TRY_CONVERT(int, ap_card.IDFatoControleContratosEuromidia) AS IDFatoControleContratosEuromidia
        FROM {TABELA_CARD_APROVA_PRECO} ap_card
        WHERE ap_card.IDFatoKanbanCard = :id_card
          AND ap_card.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND TRY_CONVERT(int, ap_card.IDFatoControleContratosEuromidia) IS NOT NULL
    ),
    NegociacoesBase AS (
        SELECT
            np.*,
            CASE
                WHEN np.IDFatoKanbanCard = :id_card THEN 'CARD'
                ELSE 'CONTRATO'
            END AS OrigemHistorico,
            ROW_NUMBER() OVER (
                PARTITION BY
                    np.IDFatoKanbanNegociacaoPreco
                ORDER BY
                    COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino) DESC,
                    np.IDFatoKanbanNegociacaoPreco DESC
            ) AS rn_registro
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        WHERE np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND (
                np.IDFatoKanbanCard = :id_card
                OR EXISTS (
                    SELECT 1
                    FROM ContratosDoCard cc
                    WHERE cc.IDFatoControleContratosEuromidia = TRY_CONVERT(int, np.IDFatoControleContratosEuromidia)
                )
          )
    )
    SELECT
        np.IDFatoKanbanNegociacaoPreco AS id_negociacao_preco,
        np.IDFatoKanbanCard AS id_card,
        np.IDEmpresa AS id_empresa_relacionada,
        np.IDDimPaineisEuromidia AS id_painel,
        np.IDDimFacesPaineis AS id_face,
        np.IDFatoControleContratosEuromidia AS id_contrato,
        np.OrigemHistorico AS origem_historico,

        cli.RazaoSocial AS razao_social_cliente,
        cli.NomeFantasia AS nome_fantasia_cliente,

        COALESCE(
            NULLIF(LTRIM(RTRIM(cli.NomeFantasia)), ''),
            NULLIF(LTRIM(RTRIM(cli.RazaoSocial)), ''),
            NULLIF(LTRIM(RTRIM(contrato.RazaoSocial)), ''),
            NULLIF(LTRIM(RTRIM(contrato.MarcaExibida)), ''),
            CONCAT('Empresa #', CAST(COALESCE(np.IDEmpresa, card_aut.IDEmpresa) AS VARCHAR(30)))
        ) AS nome_cliente_exibicao,

        np.IDDimKanbanFase AS id_fase,
        fase.NomeFase AS nome_fase,
        COALESCE(fase.CorHex, '#E5E7EB') AS cor_fase,
        COALESCE(fase.CorTextoHex, '#111827') AS cor_texto_fase,

        np.IDDimUsuarios AS id_usuario_solicitante,
        usu_sol.NomeUsuario AS nome_usuario_solicitante,
        usu_sol.Email AS email_usuario_solicitante,

        np.IDDimUsuariosAprovacaoPreco AS id_usuario_aprovador,
        usu_apr.NomeUsuario AS nome_usuario_aprovador,
        usu_apr.Email AS email_usuario_aprovador,

        np.DataPrecoProposto AS data_preco_proposto,
        np.DataAprovacaoPreco AS data_aprovacao_preco,
        np.PeriodoInicio AS periodo_inicio,
        np.PeriodoTermino AS periodo_termino,

        COALESCE(
            np.DataAprovacaoPreco,
            np.DataPrecoProposto,
            np.PeriodoInicio,
            np.PeriodoTermino
        ) AS data_referencia_preco,

        np.CustoAtual AS custo_original,
        np.PrecoAtual AS preco_original,
        np.MargemAtual AS margem_original,

        np.CustoProposto AS custo_proposto,
        np.PrecoProposto AS preco_proposto,
        np.MargemProposta AS margem_proposta,
        np.DescontoProposto AS desconto_proposto,

        np.PrecoAprovado AS preco_aplicado,
        np.DescontoAprovado AS desconto_aprovado,

        np.ObservacoesProposta AS observacoes_proposta,
        np.ObservacoesAprovacao AS observacoes_aprovacao,

        np.BitAditivoContrato AS bit_aditivo_contrato,
        np.BitAutorizacaoDiretoria AS bit_autorizacao_diretoria,
        np.BitAutorizacaoCoordenador AS bit_autorizacao_coordenador,

        COALESCE(pf.CodPonto, item_contrato.CodPonto) AS cod_ponto,
        COALESCE(pf.CodFace, item_contrato.CodFace) AS cod_face,
        COALESCE(pf.TipoPainel, item_contrato.Tipo) AS tipo_painel,

        COALESCE(
            pf.ValorTabela,
            np.PrecoAtual,
            item_contrato.FaturamentoLiquidoMensal,
            item_contrato.TotalLiquidoContratoAGBRCTACORDO
        ) AS valor_tabela,

        COALESCE(
            np.PrecoProposto,
            pf.NovoValor,
            np.PrecoAprovado
        ) AS novo_valor,

        np.DescontoProposto AS percentual_desconto,

        COALESCE(
            np.PrecoAprovado,
            np.PrecoProposto,
            pf.ValorVendaFinal,
            np.PrecoAtual,
            item_contrato.FaturamentoLiquidoMensal
        ) AS valor_venda_final,

        CASE
            WHEN COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual) IS NOT NULL
             AND COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela) IS NOT NULL
            THEN COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual)
                 - COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela)
            ELSE pf.MargemValor
        END AS margem_valor,

        COALESCE(
            np.MargemProposta,
            CASE
                WHEN COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual, 0) > 0
                 AND COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela) IS NOT NULL
                THEN (
                    (
                        COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual)
                        - COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela)
                    )
                    / COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual)
                ) * 100.0
                ELSE pf.MargemPercentual
            END
        ) AS margem_percentual,

        CASE
            WHEN np.PrecoAprovado IS NOT NULL
              OR np.IDDimUsuariosAprovacaoPreco IS NOT NULL
              OR np.DataAprovacaoPreco IS NOT NULL
            THEN 'APROVADO'
            ELSE 'NEGOCIADO'
        END AS status_negociacao,

        CAST(1 AS bit) AS ativo,
        CAST(NULL AS DATETIME) AS removido_em

    FROM NegociacoesBase np

    INNER JOIN {TABELA_CARD} card_aut
        ON card_aut.IDFatoKanbanCard = :id_card
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    LEFT JOIN {TABELA_KANBAN_FASE} fase
        ON fase.IDDimKanbanFase = np.IDDimKanbanFase
       AND (
            fase.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR fase.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu_sol
        ON usu_sol.IDDimUsuarios = np.IDDimUsuarios
       AND (
            usu_sol.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR usu_sol.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu_apr
        ON usu_apr.IDDimUsuarios = np.IDDimUsuariosAprovacaoPreco
       AND (
            usu_apr.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR usu_apr.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN {TABELA_CONTROLE_CONTRATOS} contrato
        ON contrato.IDFatoControleContratosEuromidia = np.IDFatoControleContratosEuromidia

    LEFT JOIN {TABELA_EMPRESAS} cli
        ON cli.IDEmpresa = COALESCE(np.IDEmpresa, card_aut.IDEmpresa, contrato.IDEmpresa)
       AND (
            cli.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR cli.IDEmpresaProprietaria IS NULL
       )

    OUTER APPLY (
        SELECT TOP (1)
            pf_hist.CodPonto,
            pf_hist.CodFace,
            pf_hist.TipoPainel,
            pf_hist.CustoTabela,
            pf_hist.ValorTabela,
            pf_hist.NovoValor,
            pf_hist.PercentualDesconto,
            pf_hist.ValorVendaFinal,
            pf_hist.MargemValor,
            pf_hist.MargemPercentual,
            pf_hist.Ativo,
            pf_hist.RemovidoEm,
            pf_hist.CriadoEm,
            pf_hist.DataAtualizacao
        FROM {TABELA_CARD_PAINEL_FACE} pf_hist
        WHERE pf_hist.IDFatoKanbanCard = np.IDFatoKanbanCard
          AND ISNULL(pf_hist.IDDimPaineisEuromidia, 0) = ISNULL(np.IDDimPaineisEuromidia, 0)
          AND ISNULL(pf_hist.IDDimFacesPaineis, 0) = ISNULL(np.IDDimFacesPaineis, 0)
        ORDER BY
            CASE
                WHEN ISNULL(pf_hist.Ativo, 1) = 1
                 AND pf_hist.RemovidoEm IS NULL
                THEN 0
                ELSE 1
            END,
            COALESCE(pf_hist.DataAtualizacao, pf_hist.CriadoEm, pf_hist.RemovidoEm) DESC,
            pf_hist.IDFatoKanbanCardPainelFace DESC
    ) pf

    OUTER APPLY (
        SELECT TOP (1)
            i.CodPonto,
            i.CodFace,
            i.Tipo,
            i.FaturamentoLiquidoMensal,
            i.TotalLiquidoContratoAGBRCTACORDO
        FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
        WHERE i.IDFatoControleContratoEuromidia = np.IDFatoControleContratosEuromidia
          AND ISNULL(i.BitAtivo, 1) = 1
          AND (
                ISNULL(i.IDPainelEuromidia, 0) = ISNULL(np.IDDimPaineisEuromidia, 0)
             OR ISNULL(i.IDDimFacesPaineis, 0) = ISNULL(np.IDDimFacesPaineis, 0)
          )
        ORDER BY
            i.IDFatoControleContratosItensEuromidia DESC
    ) item_contrato

    WHERE np.rn_registro = 1

    ORDER BY
        COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino) DESC,
        np.IDFatoKanbanNegociacaoPreco DESC;
    """

    return _executar_sql_mapeado(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )







def _montar_resumo_historico_precos(registros: list[dict]) -> dict:
    """Eu monto os KPIs da tela para ficar mais didática e rápida de ler."""
    total = len(registros)
    aprovadas = 0
    pendentes = 0
    com_desconto_proposto = 0
    com_desconto_aprovado = 0
    soma_desconto_proposto = 0.0
    soma_desconto_aprovado = 0.0
    qtd_desconto_proposto = 0
    qtd_desconto_aprovado = 0

    for item in registros:
        status_negociacao = str(item.get("status_negociacao") or "").strip().upper()
        if status_negociacao == "APROVADO":
            aprovadas += 1
        else:
            pendentes += 1

        desconto_proposto = _decimal_para_float_seguro(item.get("desconto_proposto"))
        desconto_aprovado = _decimal_para_float_seguro(item.get("desconto_aprovado"))

        if desconto_proposto not in (None, 0.0):
            com_desconto_proposto += 1
            soma_desconto_proposto += float(desconto_proposto)
            qtd_desconto_proposto += 1

        if desconto_aprovado not in (None, 0.0):
            com_desconto_aprovado += 1
            soma_desconto_aprovado += float(desconto_aprovado)
            qtd_desconto_aprovado += 1

    media_desconto_proposto = (
        soma_desconto_proposto / qtd_desconto_proposto
        if qtd_desconto_proposto > 0 else None
    )
    media_desconto_aprovado = (
        soma_desconto_aprovado / qtd_desconto_aprovado
        if qtd_desconto_aprovado > 0 else None
    )

    return {
        "total": total,
        "aprovadas": aprovadas,
        "pendentes": pendentes,
        "com_desconto_proposto": com_desconto_proposto,
        "com_desconto_aprovado": com_desconto_aprovado,
        "media_desconto_proposto": media_desconto_proposto,
        "media_desconto_aprovado": media_desconto_aprovado,
    }


@kanban_bp.route("/historico-precos/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def historico_precos_visualizacao(id_card: int):
    """Eu renderizo a tela didática de histórico de preços do card."""
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    card = _buscar_cabecalho_historico_card(id_card, id_empresa_proprietaria)
    if not card:
        abort(404)

    registros = _buscar_historico_precos_card(id_card, id_empresa_proprietaria)
    resumo = _montar_resumo_historico_precos(registros)
    cliente_referencia = registros[0] if registros else None

    return render_template(
        "kanban/historico_precos_visualizacao.html",
        card=card,
        registros=registros,
        resumo=resumo,
        cliente_referencia=cliente_referencia,
    )

































"""Eu monto os contadores-resumo do histórico do card."""
def _montar_resumo_historico_card(
    movimentacoes: list[dict],
    observacoes: list[dict],
    itens: list[dict],
    historico_precos: list[dict],
    tags: list[dict],
    status_historico: list[dict],
    encerramentos: list[dict],
    logs: list[dict],
) -> dict:
    total_tags_ativas = sum(
        1
        for item in tags
        if not item.get("removido_em")
    )

    total_itens_ativos = sum(
        1
        for item in itens
        if bool(item.get("ativo")) and not item.get("removido_em")
    )

    return {
        "total_movimentacoes": len(movimentacoes),
        "total_observacoes": len(observacoes),
        "total_itens": len(itens),
        "total_itens_ativos": total_itens_ativos,
        "total_tags": len(tags),
        "total_tags_ativas": total_tags_ativas,
        "total_status": len(status_historico),
        "total_encerramentos": len(encerramentos),
        "total_logs": len(logs),
        "total_precos_alterados": len(historico_precos),
    }







def _listar_cards_com_historico_precos(id_empresa_proprietaria: int) -> list[dict]:
    """Eu listo os cards que possuem histórico de preços, trazendo contagem e última movimentação."""
    sql = """
    SELECT
        c.IDFatoKanbanCard AS id_card,
        c.Titulo AS titulo,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.Ativo AS ativo,
        c.IDEmpresa AS id_empresa,

        COUNT(p.IDFatoKanbanNegociacaoPreco) AS total_registros_preco,
        MAX(COALESCE(p.DataAprovacaoPreco, p.DataPrecoProposto)) AS ultima_movimentacao_preco

    FROM [Kanban].[Silver].[FatoKanbanCard] c
    INNER JOIN [Kanban].[Silver].[FatoKanbanNegociacaoPreco] p
        ON p.IDFatoKanbanCard = c.IDFatoKanbanCard

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria

    GROUP BY
        c.IDFatoKanbanCard,
        c.Titulo,
        c.IDDimKanban,
        c.IDDimKanbanFaseAtual,
        c.CriadoEm,
        c.AtualizadoEm,
        c.Ativo,
        c.IDEmpresa

    ORDER BY
        MAX(COALESCE(p.DataAprovacaoPreco, p.DataPrecoProposto)) DESC,
        c.IDFatoKanbanCard DESC;
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )







def _montar_timeline_historico_card(
    cabecalho: dict,
    movimentacoes: list[dict],
    observacoes: list[dict],
    itens: list[dict],
    tags: list[dict],
    status_historico: list[dict],
    encerramentos: list[dict],
    logs: list[dict],
) -> list[dict]:
    timeline = []

    if cabecalho and cabecalho.get("criado_em"):
        timeline.append(
            {
                "tipo_evento": "CRIACAO",
                "data_evento": cabecalho.get("criado_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(cabecalho.get("criado_em")),
                "titulo": "Card criado",
                "descricao": f"Card criado com o título '{cabecalho.get('titulo') or 'Sem título'}'.",
                "usuario": cabecalho.get("nome_usuario_responsavel") or "",
                "icone": "🆕",
            }
        )

    for item in movimentacoes:
        timeline.append(
            {
                "tipo_evento": "MOVIMENTACAO",
                "data_evento": item.get("movido_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("movido_em")),
                "titulo": "Mudança de fase",
                "descricao": f"{item.get('nome_fase_de') or 'Sem fase'} → {item.get('nome_fase_para') or 'Sem fase'}",
                "usuario": item.get("nome_usuario") or "",
                "icone": "🔁",
                "dados": item,
            }
        )

    for item in observacoes:
        timeline.append(
            {
                "tipo_evento": "OBSERVACAO",
                "data_evento": item.get("criado_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("criado_em")),
                "titulo": "Observação adicionada",
                "descricao": item.get("observacao") or "",
                "usuario": item.get("nome_usuario") or "",
                "icone": "📝",
                "dados": item,
            }
        )

    for item in itens:
        data_referencia = item.get("atualizado_em") or item.get("criado_em") or item.get("removido_em")
        descricao_item = f"{item.get('cod_ponto') or '-'} / {item.get('cod_face') or '-'}"

        if item.get("removido_em"):
            titulo = "Item removido"
            icone = "🗑️"
            data_evento = item.get("removido_em")
        elif item.get("novo_valor") is not None or item.get("percentual_desconto") is not None or item.get("valor_venda_final") is not None:
            titulo = "Preço do item alterado"
            icone = "💰"
            data_evento = data_referencia
        else:
            titulo = "Item vinculado ao card"
            icone = "🧩"
            data_evento = data_referencia

        timeline.append(
            {
                "tipo_evento": "ITEM",
                "data_evento": data_evento,
                "data_evento_ordenacao": _normalizar_data_evento_historico(data_evento),
                "titulo": titulo,
                "descricao": descricao_item,
                "usuario": "",
                "icone": icone,
                "dados": item,
            }
        )

    for item in tags:
        if item.get("removido_em"):
            timeline.append(
                {
                    "tipo_evento": "TAG_REMOVIDA",
                    "data_evento": item.get("removido_em"),
                    "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("removido_em")),
                    "titulo": "Tag removida",
                    "descricao": item.get("nome_tag") or "Tag sem nome",
                    "usuario": item.get("nome_usuario_removeu") or "",
                    "icone": "🏷️",
                    "dados": item,
                }
            )

        if item.get("aplicado_em"):
            timeline.append(
                {
                    "tipo_evento": "TAG_APLICADA",
                    "data_evento": item.get("aplicado_em"),
                    "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("aplicado_em")),
                    "titulo": "Tag aplicada",
                    "descricao": item.get("nome_tag") or "Tag sem nome",
                    "usuario": item.get("nome_usuario_aplicou") or "",
                    "icone": "🏷️",
                    "dados": item,
                }
            )

    for item in status_historico:
        timeline.append(
            {
                "tipo_evento": "STATUS",
                "data_evento": None,
                "data_evento_ordenacao": None,
                "titulo": "Registro de status",
                "descricao": f"{item.get('nome_status') or item.get('codigo_status') or ('Status ID ' + str(item.get('id_status_card') or '-'))} na fase {item.get('nome_fase') or 'Sem fase'}",
                "usuario": item.get("nome_usuario") or "",
                "icone": "📌",
                "dados": item,
            }
        )

    for item in encerramentos:
        timeline.append(
            {
                "tipo_evento": "ENCERRAMENTO",
                "data_evento": item.get("data_atualizacao"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("data_atualizacao")),
                "titulo": "Card encerrado",
                "descricao": item.get("nome_motivo") or "Encerramento sem motivo informado",
                "usuario": item.get("nome_usuario") or "",
                "icone": "⛔",
                "dados": item,
            }
        )

    for item in logs:
        timeline.append(
            {
                "tipo_evento": "LOG",
                "data_evento": item.get("ocorrido_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("ocorrido_em")),
                "titulo": item.get("tipo_evento") or "Log",
                "descricao": item.get("texto_livre") or item.get("subtipo_evento") or "",
                "usuario": item.get("nome_usuario_acao") or "",
                "icone": "📜",
                "dados": item,
            }
        )

    timeline.sort(
        key=lambda item: (
            item.get("data_evento_ordenacao") is None,
            item.get("data_evento_ordenacao") or datetime.min,
        ),
        reverse=True,
    )

    return timeline












"""Eu busco o histórico de tags do card."""
def _buscar_tags_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        ct.IDFatoKanbanCardTag AS id_card_tag,
        ct.IDFatoKanbanCard AS id_card,
        ct.IDDimKanbanTag AS id_tag,
        ct.AplicadoEm AS aplicado_em,
        ct.AplicadoPor AS aplicado_por,
        ct.RemovidoEm AS removido_em,
        ct.RemovidoPor AS removido_por,
        ct.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        tag.NomeTag AS nome_tag,
        tag.TipoTag AS tipo_tag,
        tag.CorHex AS cor_tag,
        tag.Icone AS icone_tag,

        usuario_aplicou.NomeUsuario AS nome_usuario_aplicou,
        usuario_removeu.NomeUsuario AS nome_usuario_removeu

    FROM [Kanban].[Silver].[FatoKanbanCardTag] ct

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = ct.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    LEFT JOIN [Kanban].[Silver].[DimKanbanTag] tag
        ON tag.IDDimKanbanTag = ct.IDDimKanbanTag

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario_aplicou
        ON usuario_aplicou.IDDimUsuarios = ct.AplicadoPor
       AND (
            usuario_aplicou.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria
            OR usuario_aplicou.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario_removeu
        ON usuario_removeu.IDDimUsuarios = ct.RemovidoPor
       AND (
            usuario_removeu.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria
            OR usuario_removeu.IDEmpresaProprietaria IS NULL
       )

    WHERE ct.IDFatoKanbanCard = :id_card

    ORDER BY
        COALESCE(ct.RemovidoEm, ct.AplicadoEm) DESC,
        ct.IDFatoKanbanCardTag DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )












@kanban_bp.route("/historico-card/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def historico_card_visualizacao(id_card: int):
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    cabecalho = _buscar_cabecalho_historico_card(id_card, id_empresa_proprietaria)
    if not cabecalho:
        abort(404)

    movimentacoes = _buscar_movimentacoes_historico_card(id_card, id_empresa_proprietaria)
    observacoes = _buscar_observacoes_historico_card(id_card, id_empresa_proprietaria)
    itens = _buscar_itens_historico_card(id_card, id_empresa_proprietaria)
    historico_precos = _buscar_historico_precos_card(id_card, id_empresa_proprietaria)
    tags = _buscar_tags_historico_card(id_card, id_empresa_proprietaria)
    status_historico = _buscar_status_historico_card(id_card, id_empresa_proprietaria)
    encerramentos = _buscar_encerramento_historico_card(id_card, id_empresa_proprietaria)
    logs = _buscar_logs_historico_card(id_card, id_empresa_proprietaria)

    resumo = _montar_resumo_historico_card(
        movimentacoes=movimentacoes,
        observacoes=observacoes,
        itens=itens,
        historico_precos=historico_precos,
        tags=tags,
        status_historico=status_historico,
        encerramentos=encerramentos,
        logs=logs,
    )

    timeline = _montar_timeline_historico_card(
        cabecalho=cabecalho,
        movimentacoes=movimentacoes,
        observacoes=observacoes,
        itens=itens,
        tags=tags,
        status_historico=status_historico,
        encerramentos=encerramentos,
        logs=logs,
    )

    return render_template(
        "kanban/historico_card_visualizacao.html",
        card=cabecalho,
        resumo=resumo,
        timeline=timeline,
        movimentacoes=movimentacoes,
        observacoes=observacoes,
        itens=itens,
        historico_precos=historico_precos,
        tags=tags,
        status_historico=status_historico,
        encerramentos=encerramentos,
        logs=logs,
    )







def _sql_join_usuario_relacionado_card(alias_card: str = "c", alias_usuario: str = "usuario") -> str:
    nome_coluna = _nome_coluna_usuario_relacionado_card()
    if nome_coluna:
        return f"""
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] {alias_usuario}
          ON {alias_usuario}.IDDimUsuarios = {alias_card}.{nome_coluna}
         AND ({alias_usuario}.IDEmpresaProprietaria = {alias_card}.IDEmpresaProprietaria OR {alias_usuario}.IDEmpresaProprietaria IS NULL)
        """.strip()

    return f"""
    LEFT JOIN [Integracao].[Silver].[DimUsuarios] {alias_usuario}
      ON 1 = 0
    """.strip()




def _sql_select_nome_usuario_relacionado_card(alias_usuario: str = "usuario") -> str:
    return f"NULLIF(LTRIM(RTRIM(ISNULL({alias_usuario}.NomeUsuario, ''))), '') AS NomeUsuarioResponsavel"




def _obter_vendedores_kanban(id_kanban: int) -> list[dict[str, Any]]:
    nome_coluna_usuario = _nome_coluna_usuario_relacionado_card()
    if not nome_coluna_usuario:
        return []

    sql_vendedores = text(f"""
        SELECT DISTINCT
            usuario.IDDimUsuarios,
            NULLIF(LTRIM(RTRIM(ISNULL(usuario.NomeUsuario, ''))), '') AS NomeUsuario
        FROM {TABELA_CARD} c
        INNER JOIN [Integracao].[Silver].[DimUsuarios] usuario
            ON usuario.IDDimUsuarios = c.{nome_coluna_usuario}
           AND (usuario.IDEmpresaProprietaria = c.IDEmpresaProprietaria OR usuario.IDEmpresaProprietaria IS NULL)
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          AND NULLIF(LTRIM(RTRIM(ISNULL(usuario.NomeUsuario, ''))), '') IS NOT NULL
        ORDER BY NomeUsuario ASC;
    """)
    vendedores = db.session.execute(sql_vendedores, {"id_kanban": int(id_kanban)}).mappings().all()
    return _rows_para_dicts(vendedores)









def _calcular_preco_final_aprovacao_diretoria(
    *,
    preco_tabela: Any,
    novo_valor: Any,
    percentual_desconto: Any,
    valor_venda_final: Any,
) -> Decimal | None:
    """Eu resolvo o preço final efetivo da negociação para decidir se exige diretoria."""
    novo_valor_dec = _valor_decimal(novo_valor)
    if novo_valor_dec is not None:
        return novo_valor_dec

    valor_venda_final_dec = _valor_decimal(valor_venda_final)
    if valor_venda_final_dec is not None:
        return valor_venda_final_dec

    preco_tabela_dec = _valor_decimal(preco_tabela)
    percentual_desconto_dec = _valor_decimal(percentual_desconto)

    if percentual_desconto_dec is not None and preco_tabela_dec is not None:
        return preco_tabela_dec * (Decimal("1") - (percentual_desconto_dec / Decimal("100")))

    return preco_tabela_dec







def _obter_desconto_maximo_usuario_ativo(id_usuario: int | None) -> Decimal:
    """
    Eu busco o limite máximo de desconto ativo para o usuário.

    Regra:
    - uso apenas BitAtivo = 1;
    - se existir mais de uma linha ativa, pego a mais recente;
    - se não existir permissão ativa, retorno 0 por segurança.
    """

    try:
        id_usuario_int = int(id_usuario or 0)
    except Exception:
        id_usuario_int = 0

    if id_usuario_int <= 0:
        return Decimal("0")

    sql = text(f"""
        SELECT TOP (1)
            DescontoMaximo
        FROM {TABELA_PERMISSAO_DESCONTO}
        WHERE IDDimUsuarios = :id_usuario
          AND ISNULL(BitAtivo, 0) = 1
        ORDER BY
            DataAtualizado DESC,
            IDDimKanbanPermissaoDesconto DESC;
    """)

    valor = db.session.execute(
        sql,
        {"id_usuario": id_usuario_int},
    ).scalar()

    desconto_maximo = _valor_decimal(valor)

    if desconto_maximo is None:
        return Decimal("0")

    return desconto_maximo


def _calcular_desconto_percentual_estado(estado: Mapping[str, Any] | dict[str, Any] | None) -> Decimal | None:
    """
    Eu calculo o desconto real do item do card.

    Prioridade:
    1) se PercentualDesconto veio preenchido, uso ele;
    2) senão, calculo usando ValorTabela e preço final;
    3) preço final pode vir de NovoValor, ValorVendaFinal ou ValorTabela com desconto.
    """

    if not estado:
        return None

    desconto_informado = _valor_decimal(estado.get("PercentualDesconto"))

    if desconto_informado is not None:
        if desconto_informado < 0:
            return Decimal("0")
        return desconto_informado

    valor_tabela = _valor_decimal(estado.get("ValorTabela"))

    if valor_tabela is None or valor_tabela <= 0:
        return None

    preco_final = _calcular_preco_final_aprovacao_diretoria(
        preco_tabela=estado.get("ValorTabela"),
        novo_valor=estado.get("NovoValor"),
        percentual_desconto=estado.get("PercentualDesconto"),
        valor_venda_final=estado.get("ValorVendaFinal"),
    )

    if preco_final is None:
        return None

    desconto_calculado = ((valor_tabela - preco_final) / valor_tabela) * Decimal("100")

    if desconto_calculado < 0:
        return Decimal("0")

    return desconto_calculado


def _estado_precisa_aprovacao_diretoria(
    estado: Mapping[str, Any] | dict[str, Any] | None,
    *,
    id_usuario: int | None = None,
) -> bool:
    """
    Compatibilidade de nome:
    - mantenho o nome antigo para não quebrar chamadas existentes;
    - mas a regra agora é permissão de desconto do usuário.

    Regra nova:
    - se DescontoProposto/PercentualDesconto for maior que DescontoMaximo ativo do usuário,
      precisa aprovação.
    """

    if not estado:
        return False

    id_usuario_resolvido = int(
        id_usuario
        or _obter_id_dim_usuario_logado()
        or _id_usuario()
        or 0
    )

    desconto_maximo = _obter_desconto_maximo_usuario_ativo(id_usuario_resolvido)
    desconto_atual = _calcular_desconto_percentual_estado(estado)

    if desconto_atual is None:
        return False

    return desconto_atual > desconto_maximo


def _estados_precisam_aprovacao_diretoria(
    estados: list[dict[str, Any]] | None,
    *,
    id_usuario: int | None = None,
) -> bool:
    id_usuario_resolvido = int(
        id_usuario
        or _obter_id_dim_usuario_logado()
        or _id_usuario()
        or 0
    )

    return any(
        _estado_precisa_aprovacao_diretoria(
            estado,
            id_usuario=id_usuario_resolvido,
        )
        for estado in (estados or [])
    )


def _card_precisa_aprovacao_diretoria_por_estado_atual(
    id_card: int,
    *,
    id_usuario: int | None = None,
) -> bool:
    return _estados_precisam_aprovacao_diretoria(
        _listar_estado_atual_negociacao_card(int(id_card)),
        id_usuario=id_usuario,
    )





def _sincronizar_tag_aprovacao_diretoria_card(
    *,
    id_card: int,
    id_kanban: int,
    estados_atuais: list[dict[str, Any]] | None,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
) -> bool:
    """
    Eu sincronizo as tags de aprovação de desconto do card.

    Regra correta:
    - busca o DescontoMaximo ativo do usuário em Kanban.Silver.DimKanbanPermissaoDesconto;
    - se algum painel/face do card tiver desconto acima do permitido, aplica a tag "Aprovação Desconto";
    - se não precisar mais de aprovação, remove a tag "Aprovação Desconto";
    - se existir preço aprovado compatível com o preço atual, aplica "Desconto Aprovado";
    - se não existir aprovação compatível, remove "Desconto Aprovado".
    """

    id_card_int = int(id_card or 0)
    id_kanban_int = int(id_kanban or 0)
    id_usuario_int = int(id_usuario or 0)
    id_empresa_prop_int = int(id_empresa_proprietaria or 0)

    if id_card_int <= 0:
        return False

    if id_kanban_int <= 0:
        return False

    if id_usuario_int <= 0:
        id_usuario_int = int(_obter_id_dim_usuario_logado() or _id_usuario() or 0)

    if id_empresa_prop_int <= 0:
        id_empresa_prop_int = int(_obter_id_empresa_proprietaria_usuario_logado() or _id_empresa_usuario() or 0)

    def _desconto_maximo_usuario() -> Decimal:
        sql = text("""
            SELECT TOP (1)
                TRY_CONVERT(decimal(19, 6), DescontoMaximo) AS DescontoMaximo
            FROM [Kanban].[Silver].[DimKanbanPermissaoDesconto]
            WHERE IDDimUsuarios = :id_usuario
              AND ISNULL(BitAtivo, 0) = 1
            ORDER BY
                DataAtualizado DESC,
                IDDimKanbanPermissaoDesconto DESC;
        """)

        valor = db.session.execute(
            sql,
            {"id_usuario": id_usuario_int},
        ).scalar()

        desconto_maximo = _valor_decimal(valor)

        if desconto_maximo is None:
            return Decimal("0")

        return desconto_maximo

    def _calcular_desconto_estado(estado: Mapping[str, Any] | dict[str, Any] | None) -> Decimal | None:
        if not estado:
            return None

        desconto_informado = _valor_decimal(estado.get("PercentualDesconto"))

        if desconto_informado is not None:
            if desconto_informado < 0:
                return Decimal("0")
            return desconto_informado

        preco_tabela = _valor_decimal(estado.get("ValorTabela"))

        if preco_tabela is None or preco_tabela <= 0:
            return None

        preco_final = _calcular_preco_final_aprovacao_diretoria(
            preco_tabela=estado.get("ValorTabela"),
            novo_valor=estado.get("NovoValor"),
            percentual_desconto=estado.get("PercentualDesconto"),
            valor_venda_final=estado.get("ValorVendaFinal"),
        )

        if preco_final is None:
            return None

        desconto_calculado = ((preco_tabela - preco_final) / preco_tabela) * Decimal("100")

        if desconto_calculado < 0:
            return Decimal("0")

        return desconto_calculado

    desconto_maximo = _desconto_maximo_usuario()

    precisa_aprovacao = False
    tem_desconto_aprovado_ativo = False

    for estado in (estados_atuais or []):
        desconto_estado = _calcular_desconto_estado(estado)

        if desconto_estado is None:
            continue

        if desconto_estado <= desconto_maximo:
            continue

        if _estado_tem_aprovacao_compativel_para_preco_atual(
            id_card=id_card_int,
            estado=estado,
            id_empresa_proprietaria=id_empresa_prop_int,
        ):
            tem_desconto_aprovado_ativo = True
        else:
            precisa_aprovacao = True

    tag_aprovacao = _obter_tag_por_nome(
        id_kanban_int,
        NOME_TAG_APROVACAO_DESCONTO,
        somente_ativa=True,
    )

    if precisa_aprovacao and not tag_aprovacao:
        raise RuntimeError(
            f"A tag automática '{NOME_TAG_APROVACAO_DESCONTO}' não está cadastrada/ativa para este kanban."
        )

    if precisa_aprovacao and tag_aprovacao:
        _aplicar_tag_no_card(
            id_card=id_card_int,
            id_tag=int(tag_aprovacao.get("IDDimKanbanTag") or 0),
            id_usuario=id_usuario_int,
            id_empresa_proprietaria=(
                int(tag_aprovacao.get("IDEmpresaProprietaria") or id_empresa_prop_int or 0) or None
            ),
        )

        _remover_tag_por_nome_card(
            id_card=id_card_int,
            id_kanban=id_kanban_int,
            nome_tag=NOME_TAG_DESCONTO_APROVADO,
            id_usuario=id_usuario_int,
        )

        return True

    if tag_aprovacao:
        _remover_tag_do_card(
            id_card=id_card_int,
            id_tag=int(tag_aprovacao.get("IDDimKanbanTag") or 0),
            id_usuario=id_usuario_int,
        )

    if tem_desconto_aprovado_ativo:
        _aplicar_tag_por_nome_card(
            id_card=id_card_int,
            id_kanban=id_kanban_int,
            nome_tag=NOME_TAG_DESCONTO_APROVADO,
            id_usuario=id_usuario_int,
            id_empresa_proprietaria=id_empresa_prop_int,
        )
    else:
        _remover_tag_por_nome_card(
            id_card=id_card_int,
            id_kanban=id_kanban_int,
            nome_tag=NOME_TAG_DESCONTO_APROVADO,
            id_usuario=id_usuario_int,
        )

    return False









def _normalizar_texto_health_check(valor: Any) -> str:
    """Eu normalizo texto para comparação sem acento, sem espaços duplicados e em minúsculo."""
    if valor is None:
        return ""

    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = " ".join(texto.strip().lower().split())
    return texto







def _montar_dados_vazios_health_check() -> dict[str, Any]:
    """Eu devolvo a estrutura exata esperada pelo template do health check."""
    return {
        "titulo_painel": "Health Check Comercial",
        "atualizado_em": "",
        "periodo_referencia": "",
        "kpis": {
            "novos_contratos": 0,
            "novos_contratos_delta": "",
            "aditivos": 0,
            "aditivos_delta": "",
            "cancelamentos": 0,
            "cancelamentos_delta": "",
            "clientes_atendidos": 0,
            "clientes_atendidos_delta": "",
            "segmentos_atendidos": 0,
            "segmentos_atendidos_delta": "",
            "perdas_preco": 0,
            "perdas_preco_delta": "",
            "perdas_concorrente": 0,
            "perdas_concorrente_delta": "",
            "perdas_falta_painel": 0,
            "perdas_falta_painel_delta": "",
            "descontos_mes": 0,
            "descontos_mes_delta": "",
            "media_desconto": "0,00%",
            "media_desconto_delta": "",
        },
        "resumo_financeiro": {
            "receita_total": "—",
            "receita_total_delta": "",
            "receita_perdida": "—",
            "receita_perdida_delta": "",
            "ticket_medio": "—",
            "ticket_medio_delta": "",
        },
        "series_temporais_mercado": [],
        "segmentos_novos": [],
        "segmentos_aditivos": [],
        "segmentos_cancelamentos": [],
        "desconto_por_segmento": [],
        "vendedores_por_segmento": [],
        "vendedores_mais_desconto": [],
        "ultimas_atualizacoes": [],
    }



def _formatar_moeda_health_check(valor: Any) -> str:
    """Eu formato número como moeda BRL para exibição no painel."""
    try:
        decimal_valor = Decimal(str(valor or 0))
    except Exception:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def _formatar_percentual_health_check(valor: Any) -> str:
    """Eu formato número percentual no padrão brasileiro com duas casas."""
    try:
        decimal_valor = Decimal(str(valor or 0))
    except Exception:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{texto}%"


def _montar_placeholders_sql(prefixo: str, valores: list[Any]) -> tuple[str, dict[str, Any]]:
    """Eu monto placeholders nomeados para cláusula IN parametrizada no SQL Server."""
    parametros: dict[str, Any] = {}
    placeholders: list[str] = []

    for indice, valor in enumerate(valores):
        nome_parametro = f"{prefixo}_{indice}"
        parametros[nome_parametro] = valor
        placeholders.append(f":{nome_parametro}")

    return ", ".join(placeholders), parametros


def _obter_periodo_health_check() -> tuple[datetime, datetime, str]:
    """Eu resolvo o período de referência do painel usando querystring ou mês atual."""
    agora = datetime.now()

    try:
        ano = int(request.args.get("ano") or agora.year)
    except Exception:
        ano = agora.year

    try:
        mes = int(request.args.get("mes") or agora.month)
    except Exception:
        mes = agora.month

    mes = max(1, min(12, mes))
    ano = max(2020, min(2100, ano))

    inicio = datetime(ano, mes, 1)

    if mes == 12:
        fim = datetime(ano + 1, 1, 1)
    else:
        fim = datetime(ano, mes + 1, 1)

    referencia = inicio.strftime("%m/%Y")
    return inicio, fim, referencia


def _obter_mapa_tags_health_check(id_kanban: int, id_empresa_proprietaria: int) -> dict[str, list[int]]:
    """Eu carrego as tags ativas do kanban e devolvo um mapa nome_normalizado -> ids."""
    sql = text("""
        SELECT
            IDDimKanbanTag,
            NomeTag
        FROM [Kanban].[Silver].[DimKanbanTag]
        WHERE IDDimKanban = :id_kanban
          AND IDEmpresaProprietaria = :id_empresa_proprietaria
          AND Ativo = 1;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().all()

    mapa: dict[str, list[int]] = {}

    for row in rows:
        nome_normalizado = _normalizar_texto_health_check(row.get("NomeTag"))
        id_tag = int(row.get("IDDimKanbanTag") or 0)

        if not nome_normalizado or not id_tag:
            continue

        mapa.setdefault(nome_normalizado, []).append(id_tag)

    return mapa


def _obter_mapa_motivos_health_check(id_empresa_proprietaria: int) -> dict[str, list[int]]:
    """Eu carrego os motivos de encerramento e devolvo um mapa nome_normalizado -> ids."""
    sql = text(f"""
        SELECT
            IDDimKanbanMotivoEncerramento,
            NomeMotivo
        FROM {TABELA_MOTIVO_ENCERRAMENTO_CARD}
        WHERE IDEmpresaProprietaria = :id_empresa_proprietaria;
    """)

    rows = db.session.execute(
        sql,
        {"id_empresa_proprietaria": int(id_empresa_proprietaria)},
    ).mappings().all()

    mapa: dict[str, list[int]] = {}

    for row in rows:
        nome_normalizado = _normalizar_texto_health_check(row.get("NomeMotivo"))
        id_motivo = int(row.get("IDDimKanbanMotivoEncerramento") or 0)

        if not nome_normalizado or not id_motivo:
            continue

        mapa.setdefault(nome_normalizado, []).append(id_motivo)

    return mapa


def _resolver_ids_por_alias_health_check(
    mapa_ids_por_nome: dict[str, list[int]],
    aliases: list[str],
) -> list[int]:
    """Eu resolvo ids a partir dos aliases configurados para cada conceito do painel."""
    ids: list[int] = []

    for alias in aliases:
        chave = _normalizar_texto_health_check(alias)
        ids.extend(mapa_ids_por_nome.get(chave, []))

    ids_unicos = sorted({int(item) for item in ids if item})
    return ids_unicos


def _contar_cards_tag_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags: list[int],
) -> int:
    """Eu conto cards distintos por tags aplicadas no período."""
    if not ids_tags:
        return 0

    placeholders, parametros_ids = _montar_placeholders_sql("id_tag", ids_tags)

    sql = text(f"""
        SELECT COUNT(DISTINCT ct.IDFatoKanbanCard) AS Quantidade
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        INNER JOIN [Kanban].[Silver].[FatoKanbanCard] c
            ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ct.IDDimKanbanTag IN ({placeholders})
          AND ct.AplicadoEm >= :inicio_periodo
          AND ct.AplicadoEm < :fim_periodo;
    """)

    parametros = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
    }
    parametros.update(parametros_ids)

    row = db.session.execute(sql, parametros).mappings().first() or {}
    return int(row.get("Quantidade") or 0)


def _contar_cancelamentos_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
) -> int:
    """Eu conto cards encerrados no período."""
    sql = text("""
        SELECT COUNT(DISTINCT c.IDFatoKanbanCard) AS Quantidade
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND c.EncerradoEm >= :inicio_periodo
          AND c.EncerradoEm < :fim_periodo
          AND (
                ISNULL(c.Ativo, 1) = 0
                OR c.IDDimKanbanMotivoEncerramento IS NOT NULL
          );
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    return int(row.get("Quantidade") or 0)


def _contar_perdas_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags: list[int],
    ids_motivos: list[int],
) -> int:
    """Eu conto cards perdidos por motivo ou por tag sem duplicar o mesmo card."""
    subconsultas: list[str] = []
    parametros: dict[str, Any] = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
    }

    if ids_tags:
        placeholders_tags, parametros_tags = _montar_placeholders_sql("id_tag_perda", ids_tags)
        subconsultas.append(f"""
            SELECT DISTINCT ct.IDFatoKanbanCard AS IDFatoKanbanCard
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN [Kanban].[Silver].[FatoKanbanCard] c
                ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ct.IDDimKanbanTag IN ({placeholders_tags})
              AND ct.AplicadoEm >= :inicio_periodo
              AND ct.AplicadoEm < :fim_periodo
        """)
        parametros.update(parametros_tags)

    if ids_motivos:
        placeholders_motivos, parametros_motivos = _montar_placeholders_sql("id_motivo_perda", ids_motivos)
        subconsultas.append(f"""
            SELECT DISTINCT c.IDFatoKanbanCard AS IDFatoKanbanCard
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.IDDimKanbanMotivoEncerramento IN ({placeholders_motivos})
              AND c.EncerradoEm >= :inicio_periodo
              AND c.EncerradoEm < :fim_periodo
        """)
        parametros.update(parametros_motivos)

    if not subconsultas:
        return 0

    sql = text(f"""
        SELECT COUNT(DISTINCT base.IDFatoKanbanCard) AS Quantidade
        FROM (
            {" UNION ".join(subconsultas)}
        ) base;
    """)

    row = db.session.execute(sql, parametros).mappings().first() or {}
    return int(row.get("Quantidade") or 0)


















def _deslocar_primeiro_dia_mes_health_check(data_base: datetime, quantidade_meses: int) -> datetime:
    """Eu ando meses para frente ou para trás e devolvo sempre o primeiro dia do mês."""
    total_meses = (data_base.year * 12 + (data_base.month - 1)) + int(quantidade_meses)
    ano = total_meses // 12
    mes = (total_meses % 12) + 1
    return datetime(ano, mes, 1)


def _formatar_valor_card_serie_health_check(valor: Any, tipo_valor: str) -> str:
    """Eu formato o valor-resumo que aparece no card da série temporal."""
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    if tipo_valor == "percentual":
        return _formatar_percentual_health_check(decimal_valor)

    if decimal_valor == decimal_valor.to_integral_value():
        return str(int(decimal_valor))

    texto = f"{decimal_valor:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _obter_media_desconto_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
) -> Decimal:
    """Eu calculo a média de desconto aprovada/proposta do mês."""
    sql = text(f"""
        SELECT
            AVG(
                TRY_CONVERT(
                    decimal(18, 4),
                    COALESCE(
                        NULLIF(np.DescontoAprovado, 0),
                        NULLIF(np.DescontoProposto, 0)
                    )
                )
            ) AS MediaDesconto
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
          AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0;
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    return _valor_decimal(row.get("MediaDesconto")) or Decimal("0")


def _montar_card_serie_temporal_health_check(
    *,
    titulo: str,
    subtitulo: str,
    tipo_valor: str,
    pontos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Eu transformo pontos mensais em um card pronto para o template e para o Chart.js."""
    labels: list[str] = []
    valores: list[float] = []
    datas: list[str] = []

    for ponto in pontos:
        decimal_valor = _valor_decimal(ponto.get("valor")) or Decimal("0")
        labels.append(str(ponto.get("label") or "—"))
        datas.append(str(ponto.get("data") or "—"))
        valores.append(float(decimal_valor))

    if not valores:
        labels = ["—"]
        datas = ["—"]
        valores = [0.0]

    ultimo_valor = valores[-1]
    minimo_valor = min(valores)
    maximo_valor = max(valores)

    return {
        "titulo": titulo,
        "subtitulo": subtitulo,
        "tipo_valor": tipo_valor,
        "labels": labels,
        "valores": valores,
        "total_pontos": len(valores),
        "ultima_data": datas[-1],
        "ultimo_valor_texto": _formatar_valor_card_serie_health_check(ultimo_valor, tipo_valor),
        "minimo_valor_texto": _formatar_valor_card_serie_health_check(minimo_valor, tipo_valor),
        "maximo_valor_texto": _formatar_valor_card_serie_health_check(maximo_valor, tipo_valor),
    }


def _obter_series_temporais_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo_referencia: datetime,
    ids_tag_novo_contrato: list[int],
    ids_tag_aditivo: list[int],
    ids_tags_perdas: list[int],
    ids_motivos_perdas: list[int],
    quantidade_meses: int = 12,
) -> list[dict[str, Any]]:
    """Eu monto as séries temporais mensais dos últimos N meses para o health check."""
    quantidade_meses = max(1, int(quantidade_meses))
    inicio_primeiro_mes = _deslocar_primeiro_dia_mes_health_check(
        inicio_periodo_referencia,
        -(quantidade_meses - 1),
    )

    pontos_novos: list[dict[str, Any]] = []
    pontos_aditivos: list[dict[str, Any]] = []
    pontos_cancelamentos: list[dict[str, Any]] = []
    pontos_perdas: list[dict[str, Any]] = []
    pontos_media_desconto: list[dict[str, Any]] = []

    for deslocamento in range(quantidade_meses):
        inicio_mes = _deslocar_primeiro_dia_mes_health_check(inicio_primeiro_mes, deslocamento)
        fim_mes = _deslocar_primeiro_dia_mes_health_check(inicio_mes, 1)

        label_mes = inicio_mes.strftime("%m/%y")
        data_mes = inicio_mes.strftime("%m/%Y")

        valor_novos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
            ids_tags=ids_tag_novo_contrato,
        )

        valor_aditivos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
            ids_tags=ids_tag_aditivo,
        )

        valor_cancelamentos = _contar_cancelamentos_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
        )

        valor_perdas = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
            ids_tags=ids_tags_perdas,
            ids_motivos=ids_motivos_perdas,
        )

        valor_media_desconto = _obter_media_desconto_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
        )

        pontos_novos.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_novos,
        })
        pontos_aditivos.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_aditivos,
        })
        pontos_cancelamentos.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_cancelamentos,
        })
        pontos_perdas.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_perdas,
        })
        pontos_media_desconto.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_media_desconto,
        })

    return [
        _montar_card_serie_temporal_health_check(
            titulo="Novos contratos",
            subtitulo="Quantidade mensal dos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_novos,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Aditivos",
            subtitulo="Quantidade mensal dos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_aditivos,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Cancelamentos",
            subtitulo="Quantidade mensal dos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_cancelamentos,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Perdas comerciais",
            subtitulo="Perdas por tag ou motivo nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_perdas,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Média de desconto",
            subtitulo="Desconto médio mensal aprovado/proposto nos últimos 12 meses.",
            tipo_valor="percentual",
            pontos=pontos_media_desconto,
        ),
    ]










def _obter_clientes_e_segmentos_atendidos_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
) -> tuple[int, int]:
    """Eu conto clientes e segmentos distintos atendidos no período usando Classe do CNAE."""
    sql = text(f"""
        SELECT
            COUNT(DISTINCT c.IDEmpresa) AS QuantidadeClientes,
            COUNT(DISTINCT NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')) AS QuantidadeSegmentos
        FROM {TABELA_CARD} c
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND c.IDEmpresa IS NOT NULL
          AND (
                (c.CriadoEm >= :inicio_periodo AND c.CriadoEm < :fim_periodo)
                OR (c.AtualizadoEm >= :inicio_periodo AND c.AtualizadoEm < :fim_periodo)
                OR (c.EncerradoEm >= :inicio_periodo AND c.EncerradoEm < :fim_periodo)
          );
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    return (
        int(row.get("QuantidadeClientes") or 0),
        int(row.get("QuantidadeSegmentos") or 0),
    )


def _obter_resumo_financeiro_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags_perdas: list[int],
    ids_motivos_perdas: list[int],
) -> dict[str, str]:
    """Eu calculo receita total, receita perdida e ticket médio do período."""
    sql_receita = text(f"""
        SELECT
            SUM(
                COALESCE(
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoAprovado), 0),
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoProposto), 0),
                    0
                )
            ) AS ReceitaTotal,
            AVG(
                COALESCE(
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoAprovado), 0),
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoProposto), 0),
                    NULL
                )
            ) AS TicketMedio
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo;
    """)

    row_receita = db.session.execute(
        sql_receita,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    subconsultas: list[str] = []
    parametros_perda: dict[str, Any] = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
    }

    if ids_tags_perdas:
        placeholders_tags, parametros_tags = _montar_placeholders_sql("id_tag_perda_receita", ids_tags_perdas)
        subconsultas.append(f"""
            SELECT DISTINCT c.IDFatoKanbanCard
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ct.IDDimKanbanTag IN ({placeholders_tags})
              AND ct.AplicadoEm >= :inicio_periodo
              AND ct.AplicadoEm < :fim_periodo
        """)
        parametros_perda.update(parametros_tags)

    if ids_motivos_perdas:
        placeholders_motivos, parametros_motivos = _montar_placeholders_sql("id_motivo_perda_receita", ids_motivos_perdas)
        subconsultas.append(f"""
            SELECT DISTINCT c.IDFatoKanbanCard
            FROM {TABELA_CARD} c
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.IDDimKanbanMotivoEncerramento IN ({placeholders_motivos})
              AND c.EncerradoEm >= :inicio_periodo
              AND c.EncerradoEm < :fim_periodo
        """)
        parametros_perda.update(parametros_motivos)

    receita_perdida = Decimal("0")

    if subconsultas:
        sql_perdida = text(f"""
            WITH cards_perdidos AS (
                {" UNION ".join(subconsultas)}
            )
            SELECT
                SUM(
                    COALESCE(
                        TRY_CONVERT(decimal(18, 2), pf.ValorVendaFinal),
                        TRY_CONVERT(decimal(18, 2), pf.NovoValor),
                        TRY_CONVERT(decimal(18, 2), pf.ValorTabela),
                        0
                    )
                ) AS ReceitaPerdida
            FROM cards_perdidos cp
            LEFT JOIN [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
                ON pf.IDFatoKanbanCard = cp.IDFatoKanbanCard
               AND ISNULL(pf.Ativo, 1) = 1
               AND pf.RemovidoEm IS NULL;
        """)

        row_perdida = db.session.execute(sql_perdida, parametros_perda).mappings().first() or {}
        receita_perdida = Decimal(str(row_perdida.get("ReceitaPerdida") or 0))

    receita_total = Decimal(str(row_receita.get("ReceitaTotal") or 0))
    ticket_medio = row_receita.get("TicketMedio")

    return {
        "receita_total": _formatar_moeda_health_check(receita_total),
        "receita_total_delta": "",
        "receita_perdida": _formatar_moeda_health_check(receita_perdida),
        "receita_perdida_delta": "",
        "ticket_medio": _formatar_moeda_health_check(ticket_medio or 0),
        "ticket_medio_delta": "",
    }


def _obter_segmentos_por_evento_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags: list[int] | None = None,
    usar_cancelamentos: bool = False,
    limite: int = 10,
) -> list[dict[str, Any]]:
    """Eu monto ranking de segmentos por novos contratos, aditivos ou cancelamentos."""
    ids_tags = ids_tags or []

    parametros: dict[str, Any] = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
        "limite": int(limite),
    }

    if usar_cancelamentos:
        sql = text(f"""
            SELECT TOP (:limite)
                NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS NomeSegmento,
                COUNT(DISTINCT c.IDFatoKanbanCard) AS Quantidade
            FROM {TABELA_CARD} c
            LEFT JOIN {TABELA_EMPRESAS} emp
                ON emp.IDEmpresa = c.IDEmpresa
               AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
            LEFT JOIN {TABELA_CNAES} seg
                ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
                 = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.EncerradoEm >= :inicio_periodo
              AND c.EncerradoEm < :fim_periodo
              AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
            GROUP BY NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
            ORDER BY Quantidade DESC, NomeSegmento ASC;
        """)
    else:
        if not ids_tags:
            return []

        placeholders, parametros_tags = _montar_placeholders_sql("id_tag_segmento", ids_tags)
        parametros.update(parametros_tags)

        sql = text(f"""
            SELECT TOP (:limite)
                NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS NomeSegmento,
                COUNT(DISTINCT ct.IDFatoKanbanCard) AS Quantidade
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
            LEFT JOIN {TABELA_EMPRESAS} emp
                ON emp.IDEmpresa = c.IDEmpresa
               AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
            LEFT JOIN {TABELA_CNAES} seg
                ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
                 = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ct.IDDimKanbanTag IN ({placeholders})
              AND ct.AplicadoEm >= :inicio_periodo
              AND ct.AplicadoEm < :fim_periodo
              AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
            GROUP BY NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
            ORDER BY Quantidade DESC, NomeSegmento ASC;
        """)

    rows = db.session.execute(sql, parametros).mappings().all()

    return [
        {
            "nome": row.get("NomeSegmento"),
            "descricao": "Segmento (Classe CNAE)",
            "valor": int(row.get("Quantidade") or 0),
        }
        for row in rows
    ]


def _obter_desconto_por_segmento_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    limite: int = 20,
) -> list[dict[str, Any]]:
    """Eu monto a tabela de quantidade e média de desconto por segmento."""
    sql = text(f"""
        SELECT TOP (:limite)
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS Segmento,
            COUNT(DISTINCT np.IDFatoKanbanNegociacaoPreco) AS Quantidade,
            AVG(
                TRY_CONVERT(
                    decimal(18, 4),
                    COALESCE(
                        NULLIF(np.DescontoAprovado, 0),
                        NULLIF(np.DescontoProposto, 0)
                    )
                )
            ) AS MediaDesconto
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
          AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0
          AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
        GROUP BY NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
        ORDER BY Quantidade DESC, Segmento ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
            "limite": int(limite),
        },
    ).mappings().all()

    return [
        {
            "segmento": row.get("Segmento"),
            "quantidade": int(row.get("Quantidade") or 0),
            "media": _formatar_percentual_health_check(row.get("MediaDesconto") or 0),
            "observacao": "Média calculada a partir de desconto proposto/aprovado no período.",
        }
        for row in rows
    ]


def _obter_vendedores_por_segmento_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags_fechamento: list[int],
    limite: int = 15,
) -> list[dict[str, Any]]:
    """Eu monto o ranking de vendedor por segmento usando novos contratos e aditivos."""
    if not ids_tags_fechamento:
        return []

    placeholders, parametros_tags = _montar_placeholders_sql("id_tag_fechamento", ids_tags_fechamento)

    sql = text(f"""
        SELECT TOP (:limite)
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)))
            ) AS NomeVendedor,
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS Segmento,
            COUNT(DISTINCT ct.IDFatoKanbanCard) AS Quantidade
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
           AND (usu.IDEmpresaProprietaria = c.IDEmpresaProprietaria OR usu.IDEmpresaProprietaria IS NULL)
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ct.IDDimKanbanTag IN ({placeholders})
          AND ct.AplicadoEm >= :inicio_periodo
          AND ct.AplicadoEm < :fim_periodo
          AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
        GROUP BY
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)))
            ),
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
        ORDER BY Quantidade DESC, NomeVendedor ASC, Segmento ASC;
    """)

    parametros = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
        "limite": int(limite),
    }
    parametros.update(parametros_tags)

    rows = db.session.execute(sql, parametros).mappings().all()

    return [
        {
            "vendedor": row.get("NomeVendedor"),
            "segmento": row.get("Segmento"),
            "quantidade": int(row.get("Quantidade") or 0),
        }
        for row in rows
    ]


def _obter_vendedores_mais_desconto_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    limite: int = 15,
) -> list[dict[str, Any]]:
    """Eu monto o ranking de vendedores que mais dão desconto por segmento."""
    sql = text(f"""
        SELECT TOP (:limite)
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, np.IDDimUsuarios, c.IDDimUsuarios)))
            ) AS NomeVendedor,
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS Segmento,
            COUNT(DISTINCT np.IDFatoKanbanNegociacaoPreco) AS Quantidade,
            AVG(
                TRY_CONVERT(
                    decimal(18, 4),
                    COALESCE(
                        NULLIF(np.DescontoAprovado, 0),
                        NULLIF(np.DescontoProposto, 0)
                    )
                )
            ) AS MediaDesconto
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, np.IDDimUsuarios, c.IDDimUsuarios)
           AND (usu.IDEmpresaProprietaria = c.IDEmpresaProprietaria OR usu.IDEmpresaProprietaria IS NULL)
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
          AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0
          AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
        GROUP BY
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, np.IDDimUsuarios, c.IDDimUsuarios)))
            ),
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
        ORDER BY Quantidade DESC, MediaDesconto DESC, NomeVendedor ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
            "limite": int(limite),
        },
    ).mappings().all()

    return [
        {
            "vendedor": row.get("NomeVendedor"),
            "segmento": row.get("Segmento"),
            "quantidade": int(row.get("Quantidade") or 0),
            "media_desconto": _formatar_percentual_health_check(row.get("MediaDesconto") or 0),
        }
        for row in rows
    ]







def _deslocar_mes_health_check(data_base: datetime, quantidade_meses: int) -> datetime:
    """Eu ando meses para frente ou para trás e devolvo sempre o primeiro dia do mês."""
    total_meses = (data_base.year * 12 + (data_base.month - 1)) + int(quantidade_meses)
    ano = total_meses // 12
    mes = (total_meses % 12) + 1
    return datetime(ano, mes, 1)


def _formatar_numero_br_health_check(valor: Any, casas: int = 2) -> str:
    """Eu formato número no padrão brasileiro."""
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _formatar_valor_card_serie_health_check(valor: Any, tipo_valor: str) -> str:
    """Eu formato o valor-resumo exibido nos cards dos gráficos."""
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    if tipo_valor == "percentual":
        return _formatar_percentual_health_check(decimal_valor)

    if decimal_valor == decimal_valor.to_integral_value():
        return str(int(decimal_valor))

    return _formatar_numero_br_health_check(decimal_valor, 2)


def _montar_card_serie_temporal_health_check(
    *,
    titulo: str,
    subtitulo: str,
    tipo_valor: str,
    pontos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Eu transformo os pontos da série em payload pronto para o template."""
    labels: list[str] = []
    valores: list[float] = []
    datas: list[str] = []

    for ponto in pontos:
        data_texto = str(ponto.get("data_label") or "—")
        valor_decimal = _valor_decimal(ponto.get("valor"))
        if valor_decimal is None:
            valor_decimal = Decimal("0")

        labels.append(data_texto)
        datas.append(data_texto)
        valores.append(float(valor_decimal))

    if not valores:
        labels = ["—"]
        datas = ["—"]
        valores = [0.0]

    ultimo_valor = valores[-1]
    minimo_valor = min(valores)
    maximo_valor = max(valores)

    return {
        "titulo": titulo,
        "subtitulo": subtitulo,
        "tipo_valor": tipo_valor,
        "labels": labels,
        "valores": valores,
        "total_pontos": len(valores),
        "ultima_data": datas[-1],
        "ultimo_valor_texto": _formatar_valor_card_serie_health_check(ultimo_valor, tipo_valor),
        "minimo_valor_texto": _formatar_valor_card_serie_health_check(minimo_valor, tipo_valor),
        "maximo_valor_texto": _formatar_valor_card_serie_health_check(maximo_valor, tipo_valor),
    }


def _obter_pontos_serie_variacao_health_check(
    *,
    nome_tabela: str,
    coluna_data: str,
    coluna_valor: str,
    inicio_periodo: datetime,
) -> list[dict[str, Any]]:
    """Eu busco os pontos diários da série temporal dos últimos 12 meses."""
    sql = text(f"""
        SELECT
            CAST([{coluna_data}] AS date) AS DataReferencia,
            TRY_CONVERT(decimal(18, 6), [{coluna_valor}]) AS ValorVariacao
        FROM {nome_tabela}
        WHERE [{coluna_data}] >= :inicio_periodo
          AND [{coluna_data}] IS NOT NULL
          AND [{coluna_valor}] IS NOT NULL
        ORDER BY [{coluna_data}] ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "inicio_periodo": inicio_periodo,
        },
    ).mappings().all()

    pontos: list[dict[str, Any]] = []
    for row in rows:
        data_ref = row.get("DataReferencia")
        valor = row.get("ValorVariacao")

        if data_ref is None:
            continue

        if hasattr(data_ref, "strftime"):
            data_label = data_ref.strftime("%d/%m/%y")
        else:
            data_label = str(data_ref)

        pontos.append(
            {
                "data_label": data_label,
                "valor": _valor_decimal(valor) or Decimal("0"),
            }
        )

    return pontos


def _obter_series_temporais_mercado_health_check() -> list[dict[str, Any]]:
    """Eu monto as séries temporais reais dos indicadores de mercado e OOH."""
    hoje = datetime.now()
    inicio_periodo = _deslocar_mes_health_check(datetime(hoje.year, hoje.month, 1), -11)

    pontos_industrial = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[DataMining].[Silver].[FatoCotacaoDiariaIndiceIndustrial]",
        coluna_data="DataCotacao",
        coluna_valor="VarBRL",
        inicio_periodo=inicio_periodo,
    )

    pontos_imobiliario = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[DataMining].[Silver].[FatoCotacaoDiariaIndiceImobiliario]",
        coluna_data="DataCotacao",
        coluna_valor="VarBRL",
        inicio_periodo=inicio_periodo,
    )

    pontos_consumo = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[DataMining].[Silver].[FatoCotacaoDiariaIndiceConsumo]",
        coluna_data="DataCotacao",
        coluna_valor="VarBRL",
        inicio_periodo=inicio_periodo,
    )

    pontos_ooh = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[Integracao].[Silver].[FatoIndiceOOHDiario]",
        coluna_data="Data",
        coluna_valor="VariacaoPercent",
        inicio_periodo=inicio_periodo,
    )

    pontos_ooh_global = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[Integracao].[Silver].[FatoIndiceOOHGlobal]",
        coluna_data="Data",
        coluna_valor="VariacaoPercent",
        inicio_periodo=inicio_periodo,
    )

    return [
        _montar_card_serie_temporal_health_check(
            titulo="Índice Industrial",
            subtitulo="Variação diária do índice industrial nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_industrial,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice Imobiliário",
            subtitulo="Variação diária do índice imobiliário nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_imobiliario,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice Consumo",
            subtitulo="Variação diária do índice de consumo nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_consumo,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice OOH",
            subtitulo="Variação percentual diária do índice OOH nos últimos 12 meses.",
            tipo_valor="percentual",
            pontos=pontos_ooh,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice OOH Global",
            subtitulo="Variação percentual diária do índice OOH global nos últimos 12 meses.",
            tipo_valor="percentual",
            pontos=pontos_ooh_global,
        ),
    ]






@kanban_bp.route("/health-check-comercial", methods=["GET"])
@login_required
def health_check_comercial():
    """Eu alimento o dashboard do health check comercial com dados reais do mês."""
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    try:
        id_kanban = int(request.args.get("id_kanban") or HEALTH_CHECK_KANBAN_PADRAO)
    except Exception:
        id_kanban = HEALTH_CHECK_KANBAN_PADRAO

    _obter_kanban_autorizado(id_kanban)

    dados = _montar_dados_vazios_health_check()

    try:
        inicio_periodo, fim_periodo, periodo_referencia = _obter_periodo_health_check()

        mapa_tags = _obter_mapa_tags_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
        )
        mapa_motivos = _obter_mapa_motivos_health_check(
            id_empresa_proprietaria=id_empresa_proprietaria,
        )

        ids_tag_novo_contrato = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["novo_contrato"],
        )
        ids_tag_aditivo = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["aditivo"],
        )
        ids_tag_perda_preco = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["perda_preco"],
        )
        ids_tag_perda_concorrente = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["perda_concorrente"],
        )
        ids_tag_perda_falta_painel = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["perda_falta_painel"],
        )

        ids_motivo_perda_preco = _resolver_ids_por_alias_health_check(
            mapa_motivos,
            MAPA_MOTIVOS_HEALTH_CHECK["perda_preco"],
        )
        ids_motivo_perda_concorrente = _resolver_ids_por_alias_health_check(
            mapa_motivos,
            MAPA_MOTIVOS_HEALTH_CHECK["perda_concorrente"],
        )
        ids_motivo_perda_falta_painel = _resolver_ids_por_alias_health_check(
            mapa_motivos,
            MAPA_MOTIVOS_HEALTH_CHECK["perda_falta_painel"],
        )

        novos_contratos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_novo_contrato,
        )

        aditivos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_aditivo,
        )

        cancelamentos = _contar_cancelamentos_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
        )

        perdas_preco = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_perda_preco,
            ids_motivos=ids_motivo_perda_preco,
        )

        perdas_concorrente = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_perda_concorrente,
            ids_motivos=ids_motivo_perda_concorrente,
        )

        perdas_falta_painel = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_perda_falta_painel,
            ids_motivos=ids_motivo_perda_falta_painel,
        )

        clientes_atendidos, segmentos_atendidos = _obter_clientes_e_segmentos_atendidos_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
        )

        sql_descontos = text(f"""
            SELECT
                COUNT(DISTINCT np.IDFatoKanbanNegociacaoPreco) AS Quantidade,
                AVG(
                    TRY_CONVERT(
                        decimal(18, 4),
                        COALESCE(
                            NULLIF(np.DescontoAprovado, 0),
                            NULLIF(np.DescontoProposto, 0)
                        )
                    )
                ) AS MediaDesconto
            FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
               AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            WHERE c.IDDimKanban = :id_kanban
              AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
              AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
              AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0;
        """)

        row_descontos = db.session.execute(
            sql_descontos,
            {
                "id_kanban": int(id_kanban),
                "id_empresa_proprietaria": int(id_empresa_proprietaria),
                "inicio_periodo": inicio_periodo,
                "fim_periodo": fim_periodo,
            },
        ).mappings().first() or {}

        descontos_mes = int(row_descontos.get("Quantidade") or 0)
        media_desconto = row_descontos.get("MediaDesconto") or 0

        ids_tags_fechamento = sorted(set(ids_tag_novo_contrato + ids_tag_aditivo))
        ids_tags_todas_perdas = sorted(
            set(ids_tag_perda_preco + ids_tag_perda_concorrente + ids_tag_perda_falta_painel)
        )
        ids_motivos_todas_perdas = sorted(
            set(ids_motivo_perda_preco + ids_motivo_perda_concorrente + ids_motivo_perda_falta_painel)
        )

        dados["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        dados["periodo_referencia"] = periodo_referencia

        dados["kpis"]["novos_contratos"] = novos_contratos
        dados["kpis"]["aditivos"] = aditivos
        dados["kpis"]["cancelamentos"] = cancelamentos
        dados["kpis"]["clientes_atendidos"] = clientes_atendidos
        dados["kpis"]["segmentos_atendidos"] = segmentos_atendidos
        dados["kpis"]["perdas_preco"] = perdas_preco
        dados["kpis"]["perdas_concorrente"] = perdas_concorrente
        dados["kpis"]["perdas_falta_painel"] = perdas_falta_painel
        dados["kpis"]["descontos_mes"] = descontos_mes
        dados["kpis"]["media_desconto"] = _formatar_percentual_health_check(media_desconto)

        dados["series_temporais_mercado"] = _obter_series_temporais_mercado_health_check()

        dados["resumo_financeiro"] = _obter_resumo_financeiro_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags_perdas=ids_tags_todas_perdas,
            ids_motivos_perdas=ids_motivos_todas_perdas,
        )

        dados["segmentos_novos"] = _obter_segmentos_por_evento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_novo_contrato,
            usar_cancelamentos=False,
            limite=10,
        )

        dados["segmentos_aditivos"] = _obter_segmentos_por_evento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_aditivo,
            usar_cancelamentos=False,
            limite=10,
        )

        dados["segmentos_cancelamentos"] = _obter_segmentos_por_evento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            usar_cancelamentos=True,
            limite=10,
        )

        dados["desconto_por_segmento"] = _obter_desconto_por_segmento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            limite=20,
        )

        dados["vendedores_por_segmento"] = _obter_vendedores_por_segmento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags_fechamento=ids_tags_fechamento,
            limite=15,
        )

        dados["vendedores_mais_desconto"] = _obter_vendedores_mais_desconto_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            limite=15,
        )

    except Exception as exc:
        current_app.logger.exception(
            "Erro ao montar health check comercial | id_kanban=%s",
            id_kanban,
        )
        dados["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        dados["ultimas_atualizacoes"] = [
            {
                "texto": "Falha ao carregar uma ou mais métricas do painel.",
                "meta": str(exc),
            }
        ]

    return render_template(
        "kanban/health_check_comercial.html",
        dados=dados,
    )





def _formatar_decimal_br(valor: Any, casas: int = 2) -> str:
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")




def _aplicar_tag_por_nome_card(
    *,
    id_card: int,
    id_kanban: int,
    nome_tag: str,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
) -> bool:
    tag = _obter_tag_por_nome(int(id_kanban), str(nome_tag or "").strip(), somente_ativa=True)
    if not tag:
        return False

    return _aplicar_tag_no_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=(
            int(tag.get("IDEmpresaProprietaria") or id_empresa_proprietaria or 0) or None
        ),
    )




def _remover_tag_por_nome_card(
    *,
    id_card: int,
    id_kanban: int,
    nome_tag: str,
    id_usuario: int,
) -> bool:
    tag = _obter_tag_por_nome(int(id_kanban), str(nome_tag or "").strip(), somente_ativa=False)
    if not tag:
        return False

    return _remover_tag_do_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
    )





def _preco_final_estado_negociacao(estado: Mapping[str, Any] | dict[str, Any] | None) -> Decimal | None:
    if not estado:
        return None

    return _calcular_preco_final_aprovacao_diretoria(
        preco_tabela=estado.get("ValorTabela"),
        novo_valor=estado.get("NovoValor"),
        percentual_desconto=estado.get("PercentualDesconto"),
        valor_venda_final=estado.get("ValorVendaFinal"),
    )




def _estado_tem_aprovacao_compativel_para_preco_atual(
    *,
    id_card: int,
    estado: Mapping[str, Any] | dict[str, Any],
    id_empresa_proprietaria: int,
) -> bool:
    if not estado:
        return False

    preco_final_estado = _preco_final_estado_negociacao(estado)
    if preco_final_estado is None:
        return False

    id_painel = int(estado.get("IDDimPaineisEuromidia") or 0)
    id_face = int(estado.get("IDDimFacesPaineis") or 0)

    filtro_painel_face = ""
    parametros = {
        "id_card": int(id_card),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
    }

    if id_painel > 0:
        filtro_painel_face += "\n      AND ISNULL(np.IDDimPaineisEuromidia, 0) = :id_painel"
        parametros["id_painel"] = id_painel

    if id_face > 0:
        filtro_painel_face += "\n      AND ISNULL(np.IDDimFacesPaineis, 0) = :id_face"
        parametros["id_face"] = id_face

    sql = text(f"""
        SELECT TOP (1)
            np.PrecoAprovado,
            np.DataAprovacaoPreco,
            np.IDFatoKanbanNegociacaoPreco
        FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco] np
        WHERE np.IDFatoKanbanCard = :id_card
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND np.PrecoAprovado IS NOT NULL
          AND np.DataAprovacaoPreco IS NOT NULL
          {filtro_painel_face}
        ORDER BY
            np.DataAprovacaoPreco DESC,
            np.IDFatoKanbanNegociacaoPreco DESC;
    """)

    row = db.session.execute(sql, parametros).mappings().first()
    if not row:
        return False

    preco_aprovado = _valor_decimal(row.get("PrecoAprovado"))
    if preco_aprovado is None:
        return False

    diferenca = abs(preco_aprovado - preco_final_estado)
    return diferenca <= Decimal("0.01")





def _avaliar_tags_aprovacao_desconto_card(
    *,
    id_card: int,
    estados_atuais: list[dict[str, Any]] | None,
    id_empresa_proprietaria: int,
    id_usuario: int | None = None,
) -> dict[str, bool]:
    precisa_aprovacao_pendente = False
    tem_desconto_aprovado_ativo = False

    id_usuario_resolvido = int(
        id_usuario
        or _obter_id_dim_usuario_logado()
        or _id_usuario()
        or 0
    )

    for estado in (estados_atuais or []):
        if not _estado_precisa_aprovacao_diretoria(
            estado,
            id_usuario=id_usuario_resolvido,
        ):
            continue

        if _estado_tem_aprovacao_compativel_para_preco_atual(
            id_card=int(id_card),
            estado=estado,
            id_empresa_proprietaria=int(id_empresa_proprietaria),
        ):
            tem_desconto_aprovado_ativo = True
        else:
            precisa_aprovacao_pendente = True

    return {
        "precisa_aprovacao_pendente": bool(precisa_aprovacao_pendente),
        "tem_desconto_aprovado_ativo": bool(tem_desconto_aprovado_ativo),
    }





def _reconciliar_tags_aprovacao_desconto_pendentes(
    *,
    id_empresa_proprietaria: int,
    id_usuario: int,
    id_kanban: int | None = None,
) -> dict[str, int]:
    """
    Recalcula as tags automáticas de aprovação/desconto para cards já existentes.

    Correção importante:
    - a tela pode ser aberta pelo Admin;
    - mas a regra de desconto precisa usar o usuário solicitante do desconto;
    - nunca uso o limite do Admin para decidir se a pendência do vendedor ainda existe.
    """
    filtro_kanban = ""
    parametros: dict[str, object] = {
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
    }

    if id_kanban not in (None, "", 0):
        filtro_kanban = "AND c.IDDimKanban = :id_kanban"
        parametros["id_kanban"] = int(id_kanban)

    sql = text(f"""
        SELECT DISTINCT
            c.IDFatoKanbanCard,
            c.IDDimKanban
        FROM {TABELA_CARD} c
        WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ISNULL(c.Ativo, 1) = 1
          {filtro_kanban}
          AND (
                EXISTS (
                    SELECT 1
                    FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
                    WHERE np.IDFatoKanbanCard = c.IDFatoKanbanCard
                      AND np.IDEmpresaProprietaria = c.IDEmpresaProprietaria
                )
                OR EXISTS (
                    SELECT 1
                    FROM {TABELA_CARD_APROVA_PRECO} ap
                    WHERE ap.IDFatoKanbanCard = c.IDFatoKanbanCard
                      AND ap.IDEmpresaProprietaria = c.IDEmpresaProprietaria
                      AND ap.PrecoAprovado IS NULL
                      AND ap.DataAprovacaoPreco IS NULL
                )
          );
    """)

    rows = db.session.execute(sql, parametros).mappings().all()

    cards_avaliados = 0
    cards_com_erro = 0

    for row in rows:
        id_card = int(row.get("IDFatoKanbanCard") or 0)
        id_kanban_card = int(row.get("IDDimKanban") or 0)

        if id_card <= 0 or id_kanban_card <= 0:
            continue

        cards_avaliados += 1

        try:
            id_usuario_solicitante = _resolver_id_usuario_solicitante_desconto_card(
                id_card=id_card,
                id_usuario_fallback=int(id_usuario),
            )

            estados_atuais = _listar_estado_atual_negociacao_card(id_card)

            _sincronizar_tag_aprovacao_diretoria_card(
                id_card=id_card,
                id_kanban=id_kanban_card,
                estados_atuais=estados_atuais,
                id_usuario=int(id_usuario_solicitante or id_usuario),
                id_empresa_proprietaria=int(id_empresa_proprietaria),
            )

        except Exception:
            cards_com_erro += 1
            current_app.logger.exception(
                "Falha ao reconciliar tags automáticas de aprovação de desconto. id_card=%s",
                id_card,
            )

    return {
        "cards_avaliados": int(cards_avaliados),
        "cards_com_erro": int(cards_com_erro),
    }






def _materializar_negociacao_preco_pendente_card(
    *,
    id_card: int,
    id_empresa_proprietaria: int,
    id_usuario: int | None = None,
) -> dict[str, Any]:
    """
    Eu garanto que o card tenha linha real em FatoAprovaPreco
    quando o desconto informado ultrapassa o limite permitido para o usuário.

    Regra correta:
    - FatoAprovaPreco guarda pendências de aprovação.
    - FatoKanbanNegociacaoPreco guarda histórico de preços.
    - Esta função NÃO grava no histórico.
    - A aprovação é exigida quando DescontoProposto > DescontoMaximo ativo do usuário.
    - DescontoMaximo vem de Kanban.Silver.DimKanbanPermissaoDesconto.
    - A comparação é feita por Card + Painel + Face.
    """

    id_card_int = int(id_card or 0)
    id_empresa_prop_int = int(id_empresa_proprietaria or 0)
    id_usuario_resolvido = int(id_usuario or _obter_id_dim_usuario_logado() or _id_usuario() or 0)

    if id_card_int <= 0 or id_empresa_prop_int <= 0:
        return {
            "ok": False,
            "materializado": False,
            "motivo": "parametros_invalidos",
            "id_card": id_card_int,
        }

    if id_usuario_resolvido <= 0:
        return {
            "ok": False,
            "materializado": False,
            "motivo": "usuario_nao_resolvido",
            "id_card": id_card_int,
        }

    if not _objeto_existe(TABELA_CARD_APROVA_PRECO):
        return {
            "ok": False,
            "materializado": False,
            "motivo": "tabela_fato_aprova_preco_nao_existe",
            "id_card": id_card_int,
        }

    sql_card = text(f"""
        SELECT TOP (1)
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.IDEmpresa,
            c.IDEmpresaProprietaria,
            c.Ativo
        FROM {TABELA_CARD} c
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ISNULL(c.Ativo, 1) = 1;
    """)

    card = db.session.execute(
        sql_card,
        {
            "id_card": id_card_int,
            "id_empresa_proprietaria": id_empresa_prop_int,
        },
    ).mappings().first()

    if not card:
        return {
            "ok": False,
            "materializado": False,
            "motivo": "card_nao_encontrado_ou_inativo",
            "id_card": id_card_int,
        }

    id_kanban = int(card.get("IDDimKanban") or 0)
    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0) or None
    id_empresa_relacionada = int(card.get("IDEmpresa") or 0) or None

    id_empresa_proprietaria_aprovacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=id_empresa_prop_int,
    )

    estados_atuais = _listar_estado_atual_negociacao_card(id_card_int)

    if not estados_atuais:
        return {
            "ok": True,
            "materializado": False,
            "motivo": "card_sem_paineis_faces_ativos",
            "id_card": id_card_int,
        }

    def _desconto_maximo_usuario() -> Decimal:
        sql = text(f"""
            SELECT TOP (1)
                TRY_CONVERT(decimal(19, 6), DescontoMaximo) AS DescontoMaximo
            FROM {TABELA_PERMISSAO_DESCONTO}
            WHERE IDDimUsuarios = :id_usuario
              AND ISNULL(BitAtivo, 0) = 1
            ORDER BY
                DataAtualizado DESC,
                IDDimKanbanPermissaoDesconto DESC;
        """)

        valor = db.session.execute(
            sql,
            {"id_usuario": id_usuario_resolvido},
        ).scalar()

        desconto_maximo = _valor_decimal(valor)

        if desconto_maximo is None:
            return Decimal("0")

        return desconto_maximo

    def _calcular_desconto_estado(estado: Mapping[str, Any] | dict[str, Any] | None) -> Decimal | None:
        if not estado:
            return None

        desconto_informado = _valor_decimal(estado.get("PercentualDesconto"))

        if desconto_informado is not None:
            if desconto_informado < 0:
                return Decimal("0")
            return desconto_informado

        preco_tabela = _valor_decimal(estado.get("ValorTabela"))

        if preco_tabela is None or preco_tabela <= 0:
            return None

        preco_final = _calcular_preco_final_aprovacao_diretoria(
            preco_tabela=estado.get("ValorTabela"),
            novo_valor=estado.get("NovoValor"),
            percentual_desconto=estado.get("PercentualDesconto"),
            valor_venda_final=estado.get("ValorVendaFinal"),
        )

        if preco_final is None:
            return None

        desconto_calculado = ((preco_tabela - preco_final) / preco_tabela) * Decimal("100")

        if desconto_calculado < 0:
            return Decimal("0")

        return desconto_calculado

    desconto_maximo = _desconto_maximo_usuario()

    estados_que_exigem_aprovacao: list[dict[str, Any]] = []

    for estado in estados_atuais or []:
        desconto_estado = _calcular_desconto_estado(estado)

        if desconto_estado is None:
            continue

        if desconto_estado <= desconto_maximo:
            continue

        tem_aprovacao_compativel = _estado_tem_aprovacao_compativel_para_preco_atual(
            id_card=id_card_int,
            estado=estado,
            id_empresa_proprietaria=int(id_empresa_proprietaria_aprovacao),
        )

        if tem_aprovacao_compativel:
            continue

        estados_que_exigem_aprovacao.append(dict(estado))

    precisa_aprovacao = bool(estados_que_exigem_aprovacao)

    if not precisa_aprovacao:
        sql_limpar_pendencias = text(f"""
            DELETE FROM {TABELA_CARD_APROVA_PRECO}
            WHERE IDFatoKanbanCard = :id_card
              AND IDEmpresaProprietaria = :id_empresa_proprietaria
              AND PrecoAprovado IS NULL
              AND DataAprovacaoPreco IS NULL;
        """)

        pendencias_removidas = db.session.execute(
            sql_limpar_pendencias,
            {
                "id_card": id_card_int,
                "id_empresa_proprietaria": int(id_empresa_proprietaria_aprovacao),
            },
        ).rowcount

        if id_kanban > 0:
            _sincronizar_tag_aprovacao_diretoria_card(
                id_card=id_card_int,
                id_kanban=id_kanban,
                estados_atuais=estados_atuais,
                id_usuario=id_usuario_resolvido,
                id_empresa_proprietaria=int(id_empresa_proprietaria_aprovacao),
            )

        return {
            "ok": True,
            "materializado": False,
            "motivo": "estado_atual_nao_exige_aprovacao",
            "id_card": id_card_int,
            "id_usuario": id_usuario_resolvido,
            "desconto_maximo_usuario": float(desconto_maximo),
            "pendencias_removidas": int(pendencias_removidas or 0),
        }

    sql_qtd_antes = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD_APROVA_PRECO}
        WHERE IDFatoKanbanCard = :id_card
          AND IDEmpresaProprietaria = :id_empresa_proprietaria
          AND PrecoAprovado IS NULL
          AND DataAprovacaoPreco IS NULL;
    """)

    qtd_pendentes_antes = int(
        db.session.execute(
            sql_qtd_antes,
            {
                "id_card": id_card_int,
                "id_empresa_proprietaria": int(id_empresa_proprietaria_aprovacao),
            },
        ).scalar()
        or 0
    )

    select_id_contrato = (
        "TRY_CONVERT(int, c.IDFatoControleContratosEuromidia)"
        if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia")
        else (
            "TRY_CONVERT(int, c.IDFatoControleContratoEuromidia)"
            if _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia")
            else "CAST(NULL AS int)"
        )
    )

    select_bit_aditivo = (
        "TRY_CONVERT(bit, c.BitAditivo)"
        if _coluna_existe(TABELA_CARD, "BitAditivo")
        else "CAST(0 AS bit)"
    )

    sql_materializar = text(f"""
        ;WITH PermissaoUsuario AS (
            SELECT TOP (1)
                TRY_CONVERT(decimal(19, 6), DescontoMaximo) AS DescontoMaximo
            FROM {TABELA_PERMISSAO_DESCONTO}
            WHERE IDDimUsuarios = :id_usuario
              AND ISNULL(BitAtivo, 0) = 1
            ORDER BY
                DataAtualizado DESC,
                IDDimKanbanPermissaoDesconto DESC
        ),
        BaseOperacional AS (
            SELECT
                pf.IDFatoKanbanCardPainelFace,
                TRY_CONVERT(int, pf.IDFatoKanbanCard) AS IDFatoKanbanCard,
                TRY_CONVERT(int, pf.IDDimPaineisEuromidia) AS IDDimPaineisEuromidia,
                TRY_CONVERT(int, pf.IDDimFacesPaineis) AS IDDimFacesPaineis,
                TRY_CONVERT(int, pf.IDDimTabelaPrecosEuromidia) AS IDDimTabelaPrecosEuromidia,

                TRY_CONVERT(decimal(19, 2), pf.CustoTabela) AS CustoAtual,
                TRY_CONVERT(decimal(19, 2), pf.ValorTabela) AS PrecoAtual,
                TRY_CONVERT(decimal(19, 2), pf.NovoValor) AS NovoValor,
                TRY_CONVERT(decimal(19, 6), pf.PercentualDesconto) AS PercentualDesconto,
                TRY_CONVERT(decimal(19, 2), pf.ValorVendaFinal) AS ValorVendaFinal,

                TRY_CONVERT(date, pf.DataInicio) AS PeriodoInicio,
                TRY_CONVERT(date, pf.DataFim) AS PeriodoTermino,

                {select_id_contrato} AS IDFatoControleContratosEuromidia,
                {select_bit_aditivo} AS BitAditivoContrato
            FROM {TABELA_CARD_PAINEL_FACE} pf
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = pf.IDFatoKanbanCard
            WHERE pf.IDFatoKanbanCard = :id_card
              AND ISNULL(pf.Ativo, 1) = 1
        ),
        BaseCalculada AS (
            SELECT
                b.*,

                PrecoProposto =
                    COALESCE(
                        b.NovoValor,
                        b.ValorVendaFinal,
                        CASE
                            WHEN b.PercentualDesconto IS NOT NULL
                             AND b.PrecoAtual IS NOT NULL
                            THEN
                                TRY_CONVERT(
                                    decimal(19, 2),
                                    b.PrecoAtual * (
                                        CAST(1 AS decimal(19, 6))
                                        - (
                                            b.PercentualDesconto
                                            / CAST(100 AS decimal(19, 6))
                                        )
                                    )
                                )
                            ELSE b.PrecoAtual
                        END
                    ),

                DescontoProposto =
                    COALESCE(
                        b.PercentualDesconto,
                        CASE
                            WHEN b.PrecoAtual IS NOT NULL
                             AND b.PrecoAtual > 0
                             AND COALESCE(b.NovoValor, b.ValorVendaFinal) IS NOT NULL
                            THEN
                                TRY_CONVERT(
                                    decimal(19, 6),
                                    (
                                        (
                                            b.PrecoAtual
                                            - COALESCE(b.NovoValor, b.ValorVendaFinal)
                                        )
                                        / b.PrecoAtual
                                    ) * 100
                                )
                            ELSE NULL
                        END
                    ),

                MargemAtual =
                    CASE
                        WHEN b.PrecoAtual IS NOT NULL
                         AND b.PrecoAtual > 0
                         AND b.CustoAtual IS NOT NULL
                        THEN
                            TRY_CONVERT(
                                decimal(19, 2),
                                ((b.PrecoAtual - b.CustoAtual) / b.PrecoAtual) * 100
                            )
                        ELSE NULL
                    END
            FROM BaseOperacional b
        ),
        Fonte AS (
            SELECT
                :id_usuario AS IDDimUsuarios,
                :id_empresa_proprietaria_aprovacao AS IDEmpresaProprietaria,
                b.IDDimTabelaPrecosEuromidia,
                :id_empresa_relacionada AS IDEmpresa,
                b.IDFatoKanbanCard,
                :id_fase_atual AS IDDimKanbanFase,
                CAST(NULL AS int) AS IDDimKanbanStatusCard,
                b.IDFatoControleContratosEuromidia,
                b.BitAditivoContrato,
                :observacoes_proposta AS ObservacoesProposta,
                b.IDDimPaineisEuromidia,
                b.IDDimFacesPaineis,
                b.CustoAtual,
                b.PrecoAtual,
                b.MargemAtual,
                b.CustoAtual AS CustoAtualRateado,
                b.PrecoAtual AS PrecoAtualRateado,
                b.MargemAtual AS MargemAtualRateado,
                b.CustoAtual AS CustoProposto,
                b.PrecoProposto,
                CASE
                    WHEN b.PrecoProposto IS NOT NULL
                     AND b.PrecoProposto > 0
                     AND b.CustoAtual IS NOT NULL
                    THEN
                        TRY_CONVERT(
                            decimal(19, 2),
                            ((b.PrecoProposto - b.CustoAtual) / b.PrecoProposto) * 100
                        )
                    ELSE NULL
                END AS MargemProposta,
                b.CustoAtual AS CustoPropostoRateado,
                b.PrecoProposto AS PrecoPropostoRateado,
                TRY_CONVERT(float, b.DescontoProposto) AS DescontoProposto,
                b.PeriodoInicio,
                b.PeriodoTermino,
                CAST(1 AS bit) AS BitAutorizacaoDiretoria,
                CAST(0 AS bit) AS BitAutorizacaoCoordenador
            FROM BaseCalculada b
            OUTER APPLY (
                SELECT TOP (1)
                    DescontoMaximo
                FROM PermissaoUsuario
            ) permissao
            WHERE ISNULL(b.DescontoProposto, 0) > ISNULL(permissao.DescontoMaximo, 0)
              AND b.IDFatoKanbanCard IS NOT NULL
              AND b.IDDimPaineisEuromidia IS NOT NULL
              AND b.IDDimFacesPaineis IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1
                    FROM {TABELA_CARD_NEGOCIACAO_PRECO} h
                    WHERE h.IDFatoKanbanCard = b.IDFatoKanbanCard
                      AND h.IDEmpresaProprietaria = :id_empresa_proprietaria_aprovacao
                      AND ISNULL(h.IDDimPaineisEuromidia, 0) = ISNULL(b.IDDimPaineisEuromidia, 0)
                      AND ISNULL(h.IDDimFacesPaineis, 0) = ISNULL(b.IDDimFacesPaineis, 0)
                      AND h.PrecoAprovado IS NOT NULL
                      AND h.DataAprovacaoPreco IS NOT NULL
                      AND ABS(
                            TRY_CONVERT(decimal(19, 2), h.PrecoAprovado)
                            - TRY_CONVERT(decimal(19, 2), b.PrecoProposto)
                          ) <= 0.01
              )
        )
        MERGE {TABELA_CARD_APROVA_PRECO} AS alvo
        USING Fonte AS fonte
           ON alvo.IDFatoKanbanCard = fonte.IDFatoKanbanCard
          AND alvo.IDEmpresaProprietaria = fonte.IDEmpresaProprietaria
          AND ISNULL(alvo.IDDimPaineisEuromidia, 0) = ISNULL(fonte.IDDimPaineisEuromidia, 0)
          AND ISNULL(alvo.IDDimFacesPaineis, 0) = ISNULL(fonte.IDDimFacesPaineis, 0)
          AND alvo.PrecoAprovado IS NULL
          AND alvo.DataAprovacaoPreco IS NULL

        WHEN MATCHED THEN
            UPDATE SET
                alvo.IDDimUsuarios = fonte.IDDimUsuarios,
                alvo.IDDimTabelaPrecosEuromidia = fonte.IDDimTabelaPrecosEuromidia,
                alvo.IDEmpresa = fonte.IDEmpresa,
                alvo.IDDimKanbanFase = fonte.IDDimKanbanFase,
                alvo.IDDimKanbanStatusCard = fonte.IDDimKanbanStatusCard,
                alvo.IDFatoControleContratosEuromidia = fonte.IDFatoControleContratosEuromidia,
                alvo.BitAditivoContrato = fonte.BitAditivoContrato,
                alvo.ObservacoesProposta = fonte.ObservacoesProposta,
                alvo.CustoAtual = fonte.CustoAtual,
                alvo.PrecoAtual = fonte.PrecoAtual,
                alvo.MargemAtual = fonte.MargemAtual,
                alvo.CustoAtualRateado = fonte.CustoAtualRateado,
                alvo.PrecoAtualRateado = fonte.PrecoAtualRateado,
                alvo.MargemAtualRateado = fonte.MargemAtualRateado,
                alvo.DataPrecoProposto = GETDATE(),
                alvo.CustoProposto = fonte.CustoProposto,
                alvo.PrecoProposto = fonte.PrecoProposto,
                alvo.MargemProposta = fonte.MargemProposta,
                alvo.CustoPropostoRateado = fonte.CustoPropostoRateado,
                alvo.PrecoPropostoRateado = fonte.PrecoPropostoRateado,
                alvo.DescontoProposto = fonte.DescontoProposto,
                alvo.PeriodoInicio = fonte.PeriodoInicio,
                alvo.PeriodoTermino = fonte.PeriodoTermino,
                alvo.BitAutorizacaoDiretoria = fonte.BitAutorizacaoDiretoria,
                alvo.BitAutorizacaoCoordenador = fonte.BitAutorizacaoCoordenador

        WHEN NOT MATCHED BY TARGET THEN
            INSERT
            (
                IDDimUsuarios,
                IDEmpresaProprietaria,
                IDDimTabelaPrecosEuromidia,
                IDEmpresa,
                IDFatoKanbanCard,
                IDDimKanbanFase,
                IDDimKanbanStatusCard,
                IDFatoControleContratosEuromidia,
                BitAditivoContrato,
                ObservacoesProposta,
                IDDimPaineisEuromidia,
                IDDimFacesPaineis,
                CustoAtual,
                PrecoAtual,
                MargemAtual,
                CustoAtualRateado,
                PrecoAtualRateado,
                MargemAtualRateado,
                DataPrecoProposto,
                CustoProposto,
                PrecoProposto,
                MargemProposta,
                CustoPropostoRateado,
                PrecoPropostoRateado,
                DescontoProposto,
                PeriodoInicio,
                PeriodoTermino,
                IDDimUsuariosAprovacaoPreco,
                DataAprovacaoPreco,
                PrecoAprovado,
                DescontoAprovado,
                ObservacoesAprovacao,
                BitAutorizacaoDiretoria,
                BitAutorizacaoCoordenador
            )
            VALUES
            (
                fonte.IDDimUsuarios,
                fonte.IDEmpresaProprietaria,
                fonte.IDDimTabelaPrecosEuromidia,
                fonte.IDEmpresa,
                fonte.IDFatoKanbanCard,
                fonte.IDDimKanbanFase,
                fonte.IDDimKanbanStatusCard,
                fonte.IDFatoControleContratosEuromidia,
                fonte.BitAditivoContrato,
                fonte.ObservacoesProposta,
                fonte.IDDimPaineisEuromidia,
                fonte.IDDimFacesPaineis,
                fonte.CustoAtual,
                fonte.PrecoAtual,
                fonte.MargemAtual,
                fonte.CustoAtualRateado,
                fonte.PrecoAtualRateado,
                fonte.MargemAtualRateado,
                GETDATE(),
                fonte.CustoProposto,
                fonte.PrecoProposto,
                fonte.MargemProposta,
                fonte.CustoPropostoRateado,
                fonte.PrecoPropostoRateado,
                fonte.DescontoProposto,
                fonte.PeriodoInicio,
                fonte.PeriodoTermino,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                fonte.BitAutorizacaoDiretoria,
                fonte.BitAutorizacaoCoordenador
            );
    """)

    resultado_materializacao = db.session.execute(
        sql_materializar,
        {
            "id_card": id_card_int,
            "id_usuario": id_usuario_resolvido,
            "id_empresa_proprietaria_aprovacao": int(id_empresa_proprietaria_aprovacao),
            "id_empresa_relacionada": id_empresa_relacionada,
            "id_fase_atual": id_fase_atual,
            "observacoes_proposta": "Desconto Aguardando Aprovação",
        },
    )

    sql_remover_pendencias_que_nao_exigem_mais = text(f"""
        DELETE ap
        FROM {TABELA_CARD_APROVA_PRECO} ap
        WHERE ap.IDFatoKanbanCard = :id_card
          AND ap.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ap.PrecoAprovado IS NULL
          AND ap.DataAprovacaoPreco IS NULL
          AND NOT EXISTS (
                SELECT 1
                FROM {TABELA_CARD_PAINEL_FACE} pf
                OUTER APPLY (
                    SELECT TOP (1)
                        TRY_CONVERT(decimal(19, 6), p.DescontoMaximo) AS DescontoMaximo
                    FROM {TABELA_PERMISSAO_DESCONTO} p
                    WHERE p.IDDimUsuarios = :id_usuario
                      AND ISNULL(p.BitAtivo, 0) = 1
                    ORDER BY
                        p.DataAtualizado DESC,
                        p.IDDimKanbanPermissaoDesconto DESC
                ) permissao
                OUTER APPLY (
                    SELECT
                        DescontoProposto =
                            COALESCE(
                                TRY_CONVERT(decimal(19, 6), pf.PercentualDesconto),
                                CASE
                                    WHEN TRY_CONVERT(decimal(19, 2), pf.ValorTabela) IS NOT NULL
                                     AND TRY_CONVERT(decimal(19, 2), pf.ValorTabela) > 0
                                     AND COALESCE(
                                            TRY_CONVERT(decimal(19, 2), pf.NovoValor),
                                            TRY_CONVERT(decimal(19, 2), pf.ValorVendaFinal)
                                         ) IS NOT NULL
                                    THEN
                                        TRY_CONVERT(
                                            decimal(19, 6),
                                            (
                                                (
                                                    TRY_CONVERT(decimal(19, 2), pf.ValorTabela)
                                                    - COALESCE(
                                                        TRY_CONVERT(decimal(19, 2), pf.NovoValor),
                                                        TRY_CONVERT(decimal(19, 2), pf.ValorVendaFinal)
                                                      )
                                                )
                                                / TRY_CONVERT(decimal(19, 2), pf.ValorTabela)
                                            ) * 100
                                        )
                                    ELSE NULL
                                END
                            )
                ) calc
                WHERE pf.IDFatoKanbanCard = ap.IDFatoKanbanCard
                  AND ISNULL(pf.Ativo, 1) = 1
                  AND ISNULL(TRY_CONVERT(int, pf.IDDimPaineisEuromidia), 0) = ISNULL(ap.IDDimPaineisEuromidia, 0)
                  AND ISNULL(TRY_CONVERT(int, pf.IDDimFacesPaineis), 0) = ISNULL(ap.IDDimFacesPaineis, 0)
                  AND ISNULL(calc.DescontoProposto, 0) > ISNULL(permissao.DescontoMaximo, 0)
          );
    """)

    resultado_limpeza = db.session.execute(
        sql_remover_pendencias_que_nao_exigem_mais,
        {
            "id_card": id_card_int,
            "id_empresa_proprietaria": int(id_empresa_proprietaria_aprovacao),
            "id_usuario": id_usuario_resolvido,
        },
    )

    qtd_pendentes_depois = int(
        db.session.execute(
            sql_qtd_antes,
            {
                "id_card": id_card_int,
                "id_empresa_proprietaria": int(id_empresa_proprietaria_aprovacao),
            },
        ).scalar()
        or 0
    )

    if id_kanban > 0:
        _sincronizar_tag_aprovacao_diretoria_card(
            id_card=id_card_int,
            id_kanban=id_kanban,
            estados_atuais=estados_atuais,
            id_usuario=id_usuario_resolvido,
            id_empresa_proprietaria=int(id_empresa_proprietaria_aprovacao),
        )

    return {
        "ok": True,
        "materializado": qtd_pendentes_depois > qtd_pendentes_antes,
        "motivo": "aprovacao_preco_materializada_em_fato_aprova_preco",
        "id_card": id_card_int,
        "id_usuario": id_usuario_resolvido,
        "desconto_maximo_usuario": float(desconto_maximo),
        "qtd_pendentes_antes": qtd_pendentes_antes,
        "qtd_pendentes_depois": qtd_pendentes_depois,
        "linhas_merge_afetadas": int(getattr(resultado_materializacao, "rowcount", 0) or 0),
        "linhas_limpeza_afetadas": int(getattr(resultado_limpeza, "rowcount", 0) or 0),
    }




def _materializar_negociacoes_preco_pendentes_empresa(
    *,
    id_empresa_proprietaria: int,
    id_usuario: int,
    limite_cards: int = 300,
) -> dict[str, int]:
    """
    Eu varro cards candidatos e materializo pendências em FatoAprovaPreco.

    Correção importante:
    - esta rotina normalmente roda quando o Admin abre a tela;
    - o Admin só pode enxergar/aprovar;
    - o limite usado para decidir se precisa aprovação é o limite do usuário que solicitou o desconto;
    - portanto a busca inicial não compara contra o limite do Admin.
    """
    id_empresa_prop_int = int(id_empresa_proprietaria or 0)
    id_usuario_int = int(id_usuario or 0)

    if id_empresa_prop_int <= 0 or id_usuario_int <= 0:
        return {
            "cards_avaliados": 0,
            "cards_materializados": 0,
            "cards_com_erro": 0,
        }

    sql = text(f"""
        ;WITH CardsPorEstadoOperacional AS (
            SELECT DISTINCT
                c.IDFatoKanbanCard,
                c.IDDimKanban
            FROM {TABELA_CARD} c
            INNER JOIN {TABELA_CARD_PAINEL_FACE} pf
                ON pf.IDFatoKanbanCard = c.IDFatoKanbanCard
               AND ISNULL(pf.Ativo, 1) = 1
            OUTER APPLY (
                SELECT
                    COALESCE(
                        TRY_CONVERT(decimal(19, 6), pf.PercentualDesconto),
                        CASE
                            WHEN TRY_CONVERT(decimal(19, 2), pf.ValorTabela) IS NOT NULL
                             AND TRY_CONVERT(decimal(19, 2), pf.ValorTabela) > 0
                             AND COALESCE(
                                    TRY_CONVERT(decimal(19, 2), pf.NovoValor),
                                    TRY_CONVERT(decimal(19, 2), pf.ValorVendaFinal)
                                 ) IS NOT NULL
                            THEN
                                TRY_CONVERT(
                                    decimal(19, 6),
                                    (
                                        (
                                            TRY_CONVERT(decimal(19, 2), pf.ValorTabela)
                                            - COALESCE(
                                                TRY_CONVERT(decimal(19, 2), pf.NovoValor),
                                                TRY_CONVERT(decimal(19, 2), pf.ValorVendaFinal)
                                              )
                                        )
                                        / TRY_CONVERT(decimal(19, 2), pf.ValorTabela)
                                    ) * 100
                                )
                            ELSE NULL
                        END
                    ) AS DescontoProposto
            ) calc
            WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ISNULL(c.Ativo, 1) = 1
              AND ISNULL(calc.DescontoProposto, 0) > 0
        ),
        CardsComAprovacaoPendente AS (
            SELECT DISTINCT
                c.IDFatoKanbanCard,
                c.IDDimKanban
            FROM {TABELA_CARD} c
            INNER JOIN {TABELA_CARD_APROVA_PRECO} ap
                ON ap.IDFatoKanbanCard = c.IDFatoKanbanCard
               AND ap.IDEmpresaProprietaria = c.IDEmpresaProprietaria
               AND ap.PrecoAprovado IS NULL
               AND ap.DataAprovacaoPreco IS NULL
            WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ISNULL(c.Ativo, 1) = 1
        ),
        CardsComTagAprovacao AS (
            SELECT DISTINCT
                c.IDFatoKanbanCard,
                c.IDDimKanban
            FROM {TABELA_CARD} c
            INNER JOIN [Kanban].[Silver].[FatoKanbanCardTag] ct
                ON ct.IDFatoKanbanCard = c.IDFatoKanbanCard
               AND ct.RemovidoEm IS NULL
            INNER JOIN [Kanban].[Silver].[DimKanbanTag] t
                ON t.IDDimKanbanTag = ct.IDDimKanbanTag
               AND ISNULL(t.Ativo, 1) = 1
            WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ISNULL(c.Ativo, 1) = 1
              AND UPPER(LTRIM(RTRIM(ISNULL(t.NomeTag, '')))) = UPPER(LTRIM(RTRIM(:nome_tag_aprovacao)))
        ),
        Candidatos AS (
            SELECT IDFatoKanbanCard, IDDimKanban FROM CardsPorEstadoOperacional
            UNION
            SELECT IDFatoKanbanCard, IDDimKanban FROM CardsComAprovacaoPendente
            UNION
            SELECT IDFatoKanbanCard, IDDimKanban FROM CardsComTagAprovacao
        )
        SELECT TOP (:limite_cards)
            IDFatoKanbanCard,
            IDDimKanban
        FROM Candidatos
        ORDER BY IDFatoKanbanCard DESC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_empresa_proprietaria": id_empresa_prop_int,
            "nome_tag_aprovacao": NOME_TAG_APROVACAO_DESCONTO,
            "limite_cards": int(limite_cards or 300),
        },
    ).mappings().all()

    cards_avaliados = 0
    cards_materializados = 0
    cards_com_erro = 0

    for row in rows:
        id_card = int(row.get("IDFatoKanbanCard") or 0)

        if id_card <= 0:
            continue

        cards_avaliados += 1

        try:
            id_usuario_solicitante = _resolver_id_usuario_solicitante_desconto_card(
                id_card=id_card,
                id_usuario_fallback=id_usuario_int,
            )

            resultado = _materializar_negociacao_preco_pendente_card(
                id_card=id_card,
                id_empresa_proprietaria=id_empresa_prop_int,
                id_usuario=int(id_usuario_solicitante or id_usuario_int),
            )

            if resultado.get("materializado"):
                cards_materializados += 1

        except Exception:
            cards_com_erro += 1
            current_app.logger.exception(
                "APROVACAO_PRECO | erro ao materializar pendência em FatoAprovaPreco | id_card=%s",
                id_card,
            )

    return {
        "cards_avaliados": int(cards_avaliados),
        "cards_materializados": int(cards_materializados),
        "cards_com_erro": int(cards_com_erro),
    }



@kanban_bp.route("/aprovacao-preco", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def lista_aprovacao_preco():
    id_usuario = _assert_login()
    _exigir_admin_aprovacao_desconto()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    try:
        resultado_materializacao = _materializar_negociacoes_preco_pendentes_empresa(
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_usuario=int(id_usuario),
            limite_cards=300,
        )

        resultado_reconciliacao = _reconciliar_tags_aprovacao_desconto_pendentes(
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_usuario=int(id_usuario),
        )

        db.session.commit()

        current_app.logger.info(
            "APROVACAO_PRECO_LISTA | FatoAprovaPreco | materializacao=%s | reconciliacao=%s",
            resultado_materializacao,
            resultado_reconciliacao,
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "APROVACAO_PRECO_LISTA | falha ao materializar/reconciliar pendências antes da listagem"
        )

    cards = _buscar_cards_lista_aprovacao_preco(int(id_empresa_proprietaria))

    return render_template(
        "kanban/lista_aprovacao_preco.html",
        cards=cards,
        nome_tag=NOME_TAG_APROVACAO_DESCONTO,
    )




def _buscar_cards_lista_aprovacao_preco(id_empresa_proprietaria: int) -> list[dict[str, Any]]:
    """
    Eu busco a lista de aprovação lendo somente FatoAprovaPreco.

    Regra:
    - tela /kanban/aprovacao-preco mostra apenas pendências;
    - pendência = linha em FatoAprovaPreco sem PrecoAprovado e sem DataAprovacaoPreco;
    - FatoKanbanNegociacaoPreco não entra aqui.
    """

    def _expr_texto_coluna(tabela: str, alias: str, coluna: str, tamanho: int = 300) -> str:
        if _coluna_existe(tabela, coluna):
            return f"NULLIF(LTRIM(RTRIM(CONVERT(nvarchar({int(tamanho)}), {alias}.{coluna}))), '')"
        return f"CAST(NULL AS nvarchar({int(tamanho)}))"

    expr_card_nome_empresa = _expr_texto_coluna(TABELA_CARD, "c", "NomeEmpresa", 300)

    expr_empresa_card_nome_fantasia = _expr_texto_coluna(TABELA_EMPRESAS, "empresa_card", "NomeFantasia", 300)
    expr_empresa_card_razao_social = _expr_texto_coluna(TABELA_EMPRESAS, "empresa_card", "RazaoSocial", 300)
    expr_empresa_card_cnpj = _expr_texto_coluna(TABELA_EMPRESAS, "empresa_card", "CNPJ", 40)

    expr_empresa_ap_nome_fantasia = _expr_texto_coluna(TABELA_EMPRESAS, "empresa_ap", "NomeFantasia", 300)
    expr_empresa_ap_razao_social = _expr_texto_coluna(TABELA_EMPRESAS, "empresa_ap", "RazaoSocial", 300)
    expr_empresa_ap_cnpj = _expr_texto_coluna(TABELA_EMPRESAS, "empresa_ap", "CNPJ", 40)

    expr_contrato_razao_social = _expr_texto_coluna(TABELA_CONTROLE_CONTRATOS, "contrato", "RazaoSocial", 300)
    expr_contrato_marca = _expr_texto_coluna(TABELA_CONTROLE_CONTRATOS, "contrato", "MarcaExibida", 300)
    expr_contrato_cnpj = _expr_texto_coluna(TABELA_CONTROLE_CONTRATOS, "contrato", "CNPJ", 40)

    expr_usuario_nome = _expr_texto_coluna("[Integracao].[Silver].[DimUsuarios]", "usu", "NomeUsuario", 300)

    sql = text(f"""
        ;WITH Pendencias AS (
            SELECT
                ap.*,
                ROW_NUMBER() OVER (
                    PARTITION BY ap.IDFatoKanbanCard
                    ORDER BY
                        COALESCE(ap.DataPrecoProposto, ap.PeriodoInicio, ap.PeriodoTermino) DESC,
                        ap.IDFatoAprovaPreco DESC
                ) AS rn_card
            FROM {TABELA_CARD_APROVA_PRECO} ap
            INNER JOIN {TABELA_CARD} c_aut
                ON c_aut.IDFatoKanbanCard = ap.IDFatoKanbanCard
               AND c_aut.IDEmpresaProprietaria = :id_empresa_proprietaria
            WHERE ap.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ap.PrecoAprovado IS NULL
              AND ap.DataAprovacaoPreco IS NULL
              AND ISNULL(c_aut.Ativo, 1) = 1
        )
        SELECT
            c.IDFatoKanbanCard AS id_card,
            c.IDDimKanban AS id_kanban,
            c.Titulo AS titulo,
            c.CriadoEm AS criado_em,
            c.AtualizadoEm AS atualizado_em,

            COALESCE(c.IDEmpresa, ap.IDEmpresa) AS id_empresa,

            fase.NomeFase AS nome_fase,

            COALESCE(
                {expr_empresa_card_nome_fantasia},
                {expr_empresa_card_razao_social},
                {expr_empresa_ap_nome_fantasia},
                {expr_empresa_ap_razao_social},
                {expr_card_nome_empresa},
                {expr_contrato_razao_social},
                {expr_contrato_marca},
                N'Empresa não identificada'
            ) AS nome_empresa,

            COALESCE(
                {expr_empresa_card_razao_social},
                {expr_empresa_ap_razao_social},
                {expr_card_nome_empresa},
                {expr_contrato_razao_social},
                {expr_contrato_marca}
            ) AS razao_social,

            COALESCE(
                {expr_empresa_card_nome_fantasia},
                {expr_empresa_ap_nome_fantasia},
                {expr_card_nome_empresa},
                {expr_contrato_marca}
            ) AS nome_fantasia,

            COALESCE(
                {expr_empresa_card_cnpj},
                {expr_empresa_ap_cnpj},
                {expr_contrato_cnpj}
            ) AS cnpj,

            COALESCE(
                {expr_usuario_nome},
                N'—'
            ) AS nome_usuario_responsavel,

            ap.IDFatoAprovaPreco AS id_aprova_preco,
            ap.IDFatoAprovaPreco AS id_negociacao_preco,
            ap.DataPrecoProposto AS data_preco_proposto,
            ap.CustoAtual AS custo_atual,
            ap.PrecoAtual AS preco_atual,
            ap.PrecoProposto AS preco_proposto,
            ap.DescontoProposto AS desconto_proposto,
            ap.MargemProposta AS margem_proposta,
            ap.IDDimUsuarios AS id_usuario_solicitante,

            CAST(0 AS bit) AS bit_sem_linha_negociacao

        FROM Pendencias ap

        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = ap.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = :id_empresa_proprietaria

        LEFT JOIN {TABELA_KANBAN_FASE} fase
            ON fase.IDDimKanbanFase = COALESCE(ap.IDDimKanbanFase, c.IDDimKanbanFaseAtual)
           AND (
                fase.IDEmpresaProprietaria = c.IDEmpresaProprietaria
                OR fase.IDEmpresaProprietaria IS NULL
           )

        LEFT JOIN {TABELA_EMPRESAS} empresa_card
            ON empresa_card.IDEmpresa = c.IDEmpresa

        LEFT JOIN {TABELA_EMPRESAS} empresa_ap
            ON empresa_ap.IDEmpresa = ap.IDEmpresa

        LEFT JOIN {TABELA_CONTROLE_CONTRATOS} contrato
            ON contrato.IDFatoControleContratosEuromidia = ap.IDFatoControleContratosEuromidia

        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios, ap.IDDimUsuarios)

        WHERE ap.rn_card = 1

        ORDER BY
            COALESCE(ap.DataPrecoProposto, c.AtualizadoEm, c.CriadoEm) DESC,
            c.IDFatoKanbanCard DESC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().all()

    return _rows_para_dicts(rows)












def _buscar_cabecalho_aprovacao_preco(id_card: int, id_empresa_proprietaria: int) -> dict[str, Any] | None:
    """
    Eu busco o cabeçalho da tela de aprovação de preço com os dados cadastrais da empresa.

    Ajuste principal:
    - uso joins diretos nas chaves já numéricas;
    - não uso TRY_CONVERT/LTRIM/RTRIM para montar a tela;
    - resolvo a empresa na prioridade correta: card -> pendência -> solicitação -> contrato;
    - trago CNAE, CNPJ, SubClasse/Classificação, Nome Fantasia, Origem, Porte,
      Tipo Cliente e Situação Cadastral para o template aprovacao_preco_detalhe.html.
    """

    def _expr_int_coluna(tabela: str, alias: str, coluna: str) -> str:
        if _coluna_existe(tabela, coluna):
            return f"{alias}.{coluna}"
        return "CAST(NULL AS int)"

    def _expr_bit_coluna(tabela: str, alias: str, coluna: str) -> str:
        if _coluna_existe(tabela, coluna):
            return f"{alias}.{coluna}"
        return "CAST(NULL AS bit)"

    def _expr_texto_coluna(tabela: str, alias: str, coluna: str, tamanho: int = 300) -> str:
        if _coluna_existe(tabela, coluna):
            return f"CAST({alias}.{coluna} AS nvarchar({int(tamanho)}))"
        return f"CAST(NULL AS nvarchar({int(tamanho)}))"

    id_empresa_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDEmpresa")
    id_empresa_relacionada_expr = _expr_int_coluna(TABELA_CARD, "c", "IDEmpresaRelacionadaCard")
    id_empresa_agencia_expr = _expr_int_coluna(TABELA_CARD, "c", "IDEmpresaAgencia")
    id_empresa_bureau_expr = _expr_int_coluna(TABELA_CARD, "c", "IDEmpresaBureau")
    id_cnae_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDDimCnaes")
    id_origem_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDDimOrigemAtendimento")
    id_tipo_cliente_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDDimTipoCliente")

    if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia"):
        id_contrato_card_expr = "c.IDFatoControleContratosEuromidia"
    elif _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia"):
        id_contrato_card_expr = "c.IDFatoControleContratoEuromidia"
    else:
        id_contrato_card_expr = "CAST(NULL AS int)"

    nome_empresa_card_expr = _expr_texto_coluna(TABELA_CARD, "c", "NomeEmpresa", 300)
    telefone_card_expr = _expr_texto_coluna(TABELA_CARD, "c", "Telefone", 80)
    email_card_expr = _expr_texto_coluna(TABELA_CARD, "c", "Email", 300)
    marca_card_expr = _expr_texto_coluna(TABELA_CARD, "c", "Marca", 300)

    bit_cliente_direto_expr = _expr_bit_coluna(TABELA_CARD, "c", "BitClienteDireto")
    bit_agencia_expr = _expr_bit_coluna(TABELA_CARD, "c", "BitAgencia")
    bit_planejador_expr = _expr_bit_coluna(TABELA_CARD, "c", "BitPlanejador")
    bit_aditivo_expr = _expr_bit_coluna(TABELA_CARD, "c", "BitAditivo")
    bit_contrato_novo_expr = _expr_bit_coluna(TABELA_CARD, "c", "BitContratoNovo")

    id_usuario_expr = "COALESCE("
    candidatos_usuario = []
    for coluna_usuario in (
        "IDVendedorUsuario",
        "IDDimUsuarios",
        "IDUsuarioCriacao",
        "CriadoPorIDDimUsuarios",
    ):
        if _coluna_existe(TABELA_CARD, coluna_usuario):
            candidatos_usuario.append(f"c.{coluna_usuario}")

    candidatos_usuario.extend([
        "ap.IDDimUsuarios",
        "sol.IDDimUsuariosCriacao",
        "CAST(NULL AS int)",
    ])
    id_usuario_expr += ", ".join(candidatos_usuario)
    id_usuario_expr += ")"

    nome_origem_card_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_card", "NomeOrigemAtendimento", 200)
    desc_origem_card_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_card", "Descricao", 200)
    nome_origem_emp_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_emp", "NomeOrigemAtendimento", 200)
    desc_origem_emp_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_emp", "Descricao", 200)

    tipo_cliente_desc_expr = _expr_texto_coluna(TABELA_TIPO_CLIENTE_DESCONTO, "tipo_cliente", "Descricao", 200)
    tipo_cliente_nome_expr = _expr_texto_coluna(TABELA_TIPO_CLIENTE_DESCONTO, "tipo_cliente", "NomeTipoCliente", 200)

    sql = text(f"""
        ;WITH PendenciaCard AS (
            SELECT TOP (1)
                ap.*
            FROM {TABELA_CARD_APROVA_PRECO} ap
            WHERE ap.IDFatoKanbanCard = :id_card
              AND ap.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ap.PrecoAprovado IS NULL
              AND ap.DataAprovacaoPreco IS NULL
            ORDER BY
                ap.DataPrecoProposto DESC,
                ap.IDFatoAprovaPreco DESC
        ),
        SolicitacaoCard AS (
            SELECT TOP (1)
                sol_base.*
            FROM {TABELA_SOLICITACAO_CONTRATO} sol_base
            WHERE sol_base.IDFatoKanbanCard = :id_card
              AND sol_base.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ISNULL(sol_base.BitAtivo, 1) = 1
            ORDER BY
                sol_base.DataAtualizacao DESC,
                sol_base.IDFatoSolicitacaoContratoEuromidia DESC
        )
        SELECT TOP (1)
            c.IDFatoKanbanCard AS id_card,
            c.IDFatoKanbanCard AS IDFatoKanbanCard,
            c.IDDimKanban AS id_kanban,
            c.IDDimKanban AS IDDimKanban,
            c.IDDimKanbanFaseAtual AS id_fase_atual,
            c.IDDimKanbanFaseAtual AS IDDimKanbanFaseAtual,
            c.Titulo AS titulo,
            c.Titulo AS Titulo,
            c.Descricao AS descricao,
            c.Descricao AS Descricao,
            c.CriadoEm AS criado_em,
            c.CriadoEm AS CriadoEm,
            c.AtualizadoEm AS atualizado_em,
            c.AtualizadoEm AS AtualizadoEm,
            c.Ativo AS ativo,
            c.Ativo AS Ativo,
            c.IDEmpresaProprietaria AS id_empresa_proprietaria,
            c.IDEmpresaProprietaria AS IDEmpresaProprietaria,

            COALESCE({id_empresa_relacionada_expr}, {id_empresa_card_expr}, ap.IDEmpresa, sol.IDEmpresa, contrato.IDEmpresa) AS id_empresa,
            COALESCE({id_empresa_relacionada_expr}, {id_empresa_card_expr}, ap.IDEmpresa, sol.IDEmpresa, contrato.IDEmpresa) AS IDEmpresa,
            COALESCE({id_empresa_relacionada_expr}, {id_empresa_card_expr}, ap.IDEmpresa, sol.IDEmpresa, contrato.IDEmpresa) AS id_empresa_relacionada,
            COALESCE({id_empresa_relacionada_expr}, {id_empresa_card_expr}, ap.IDEmpresa, sol.IDEmpresa, contrato.IDEmpresa) AS IDEmpresaRelacionadaCard,

            {id_empresa_agencia_expr} AS IDEmpresaAgencia,
            {id_empresa_bureau_expr} AS IDEmpresaBureau,

            COALESCE(ap.IDFatoControleContratosEuromidia, sol.IDFatoControleContratosEuromidia, {id_contrato_card_expr}, contrato.IDFatoControleContratosEuromidia) AS id_contrato,
            COALESCE(ap.IDFatoControleContratosEuromidia, sol.IDFatoControleContratosEuromidia, {id_contrato_card_expr}, contrato.IDFatoControleContratosEuromidia) AS IDFatoControleContratosEuromidia,

            fase.NomeFase AS nome_fase,
            fase.NomeFase AS NomeFase,
            fase.CorHex AS cor_fase,
            fase.CorTextoHex AS cor_texto_fase,

            usu.NomeUsuario AS nome_usuario_responsavel,
            usu.NomeUsuario AS NomeUsuarioResponsavel,
            usu.Email AS email_usuario_responsavel,

            COALESCE(emp.NomeFantasia, emp.RazaoSocial, {nome_empresa_card_expr}, sol.MarcaExibida, contrato.MarcaExibida, c.Titulo, N'Empresa não identificada') AS nome_empresa,
            COALESCE(emp.NomeFantasia, emp.RazaoSocial, {nome_empresa_card_expr}, sol.MarcaExibida, contrato.MarcaExibida, c.Titulo, N'Empresa não identificada') AS NomeEmpresa,

            COALESCE(emp.RazaoSocial, {nome_empresa_card_expr}, sol.RazaoSocial, contrato.RazaoSocial, c.Titulo) AS razao_social,
            COALESCE(emp.RazaoSocial, {nome_empresa_card_expr}, sol.RazaoSocial, contrato.RazaoSocial, c.Titulo) AS EmpresaRazaoSocial,
            emp.RazaoSocial AS RazaoSocial,

            COALESCE(emp.NomeFantasia, {nome_empresa_card_expr}, {marca_card_expr}, sol.MarcaExibida, contrato.MarcaExibida) AS nome_fantasia,
            COALESCE(emp.NomeFantasia, {nome_empresa_card_expr}, {marca_card_expr}, sol.MarcaExibida, contrato.MarcaExibida) AS NomeFantasia,

            COALESCE(emp.CNPJ, sol.CNPJ, contrato.CNPJ) AS cnpj,
            COALESCE(emp.CNPJ, sol.CNPJ, contrato.CNPJ) AS EmpresaCNPJ,
            COALESCE(emp.CNPJ, sol.CNPJ, contrato.CNPJ) AS CNPJ,

            COALESCE(emp.CNAE, cnae_card.cnaepadrao) AS cnae_empresa,
            COALESCE(emp.CNAE, cnae_card.cnaepadrao) AS EmpresaCNAE,
            COALESCE(emp.CNAE, cnae_card.cnaepadrao) AS CNAE,

            COALESCE(cnae_emp.Descricao, cnae_card.Descricao, emp.DescricaoCnae) AS descricao_cnae,
            COALESCE(cnae_emp.Descricao, cnae_card.Descricao, emp.DescricaoCnae) AS DescricaoCnae,
            COALESCE(cnae_emp.Descricao, cnae_card.Descricao, emp.DescricaoCnae) AS CnaeDescricao,

            COALESCE(cnae_emp.Classe, cnae_card.Classe) AS classe_cnae,
            COALESCE(cnae_emp.Classe, cnae_card.Classe) AS EmpresaClasse,
            COALESCE(cnae_emp.Classe, cnae_card.Classe) AS CnaeClasse,

            COALESCE(cnae_emp.Setor, cnae_card.Setor) AS setor_cnae,
            COALESCE(cnae_emp.Setor, cnae_card.Setor) AS EmpresaSetor,
            COALESCE(cnae_emp.Setor, cnae_card.Setor) AS CnaeSetor,

            COALESCE(cnae_emp.MacroSetor, cnae_card.MacroSetor) AS macro_setor,
            COALESCE(cnae_emp.MacroSetor, cnae_card.MacroSetor) AS MacroSetor,
            COALESCE(cnae_emp.MacroSetor, cnae_card.MacroSetor) AS CnaeMacroSetor,

            COALESCE(cnae_emp.SubClasse, cnae_card.SubClasse) AS subclasse_cnae,
            COALESCE(cnae_emp.SubClasse, cnae_card.SubClasse) AS SubClasse,
            COALESCE(cnae_emp.SubClasse, cnae_card.SubClasse) AS CnaeSubClasse,

            COALESCE(cnae_emp.ClassificacaoMacro, cnae_card.ClassificacaoMacro) AS classificacao_macro,
            COALESCE(cnae_emp.ClassificacaoMacro, cnae_card.ClassificacaoMacro) AS ClassificacaoMacro,
            COALESCE(cnae_emp.ClassificacaoMacro, cnae_card.ClassificacaoMacro) AS CnaeClassificacaoMacro,

            COALESCE(cnae_emp.ScoreSetor, cnae_card.ScoreSetor) AS score_setor,
            COALESCE(cnae_emp.ScoreSetor, cnae_card.ScoreSetor) AS ScoreSetor,
            COALESCE(cnae_emp.Hex, cnae_card.Hex) AS hex_cnae,
            COALESCE(cnae_emp.Hex, cnae_card.Hex) AS CnaeHex,

            COALESCE(emp.Email, {email_card_expr}) AS email_empresa,
            COALESCE(emp.Email, {email_card_expr}) AS Email,
            COALESCE(emp.TelefoneContato1, emp.TelefoneContato2, {telefone_card_expr}) AS telefone_empresa,
            COALESCE(emp.TelefoneContato1, emp.TelefoneContato2, {telefone_card_expr}) AS Telefone,
            emp.TelefoneContato1,
            emp.TelefoneContato2,

            emp.UF AS uf,
            emp.UF AS UF,
            emp.CEP AS cep,
            emp.CEP AS CEP,
            emp.Pais AS pais,
            emp.Pais AS Pais,
            emp.Bairro AS bairro,
            emp.Bairro AS Bairro,
            emp.Numero AS numero,
            emp.Numero AS Numero,
            emp.Municipio AS municipio,
            emp.Municipio AS Municipio,
            emp.Logradouro AS logradouro,
            emp.Logradouro AS Logradouro,
            emp.Complemento AS complemento,
            emp.Complemento AS Complemento,
            emp.DescricaoTipoLogradouro,
            emp.Latitude AS latitude,
            emp.Latitude AS Latitude,
            emp.Longitude AS longitude,
            emp.Longitude AS Longitude,

            emp.Porte AS porte,
            emp.Porte AS Porte,
            emp.CodigoPorte,
            emp.NaturezaJuridica AS natureza_juridica,
            emp.NaturezaJuridica AS NaturezaJuridica,
            emp.CapitalSocial AS capital_social,
            emp.CapitalSocial AS CapitalSocial,
            emp.DataInicioAtividades AS data_inicio_atividades,
            emp.DataInicioAtividades AS DataInicioAtividades,
            emp.DataSituacaoCadastral,
            emp.DescricaoSituacaoCadastral AS situacao_cadastral,
            emp.DescricaoSituacaoCadastral AS DescricaoSituacaoCadastral,
            emp.DescricaoSituacaoCadastral AS SituacaoCadastral,
            emp.DescricaoMotivoSituacaoCadastral,
            emp.IdentificadorMatrizFilial,
            emp.DescricaoIdentificadorMatrizFilial,

            COALESCE({id_origem_card_expr}, emp.IDDimOrigemAtendimento) AS IDDimOrigemAtendimento,
            COALESCE({nome_origem_card_expr}, {desc_origem_card_expr}, {nome_origem_emp_expr}, {desc_origem_emp_expr}) AS NomeOrigemAtendimento,
            COALESCE({nome_origem_card_expr}, {desc_origem_card_expr}, {nome_origem_emp_expr}, {desc_origem_emp_expr}) AS OrigemAtendimento,

            {id_tipo_cliente_card_expr} AS IDDimTipoCliente,
            COALESCE({tipo_cliente_desc_expr}, {tipo_cliente_nome_expr}) AS TipoClienteDesconto,
            COALESCE({tipo_cliente_desc_expr}, {tipo_cliente_nome_expr}) AS TipoCliente,
            COALESCE({tipo_cliente_desc_expr}, {tipo_cliente_nome_expr}) AS NomeTipoCliente,

            {bit_cliente_direto_expr} AS BitClienteDireto,
            {bit_agencia_expr} AS BitAgencia,
            {bit_planejador_expr} AS BitPlanejador,
            {bit_aditivo_expr} AS BitAditivo,
            {bit_contrato_novo_expr} AS BitContratoNovo,

            CASE
                WHEN {bit_aditivo_expr} = 1 THEN 'ADITIVO'
                WHEN {bit_contrato_novo_expr} = 1 THEN 'NOVO CONTRATO'
                ELSE sol.TipoSolicitacao
            END AS TipoSolicitacao,

            contrato.NumeroContrato AS numero_contrato,
            contrato.NumeroPrevia AS numero_previa,
            contrato.Referencia AS referencia_contrato,

            ap.IDFatoAprovaPreco AS id_aprova_preco,
            ap.IDFatoAprovaPreco AS id_negociacao_preco,
            ap.DataPrecoProposto AS data_preco_proposto,
            ap.CustoAtual AS custo_atual,
            ap.PrecoAtual AS preco_atual,
            ap.PrecoProposto AS preco_proposto,
            ap.DescontoProposto AS desconto_proposto,
            ap.MargemProposta AS margem_proposta,
            ap.IDDimUsuarios AS id_usuario_solicitante

        FROM {TABELA_CARD} c

        LEFT JOIN PendenciaCard ap
            ON 1 = 1

        LEFT JOIN SolicitacaoCard sol
            ON 1 = 1

        LEFT JOIN {TABELA_CONTROLE_CONTRATOS} contrato
            ON contrato.IDFatoControleContratosEuromidia = COALESCE(
                ap.IDFatoControleContratosEuromidia,
                sol.IDFatoControleContratosEuromidia,
                {id_contrato_card_expr}
            )

        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = COALESCE(
                {id_empresa_relacionada_expr},
                {id_empresa_card_expr},
                ap.IDEmpresa,
                sol.IDEmpresa,
                contrato.IDEmpresa
            )

        LEFT JOIN {TABELA_CNAES} cnae_emp
            ON cnae_emp.cnaepadrao = emp.CNAE

        LEFT JOIN {TABELA_CNAES} cnae_card
            ON cnae_card.IDDimCnaes = {id_cnae_card_expr}

        LEFT JOIN {TABELA_KANBAN_FASE} fase
            ON fase.IDDimKanbanFase = c.IDDimKanbanFaseAtual
           AND fase.IDDimKanban = c.IDDimKanban

        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = {id_usuario_expr}

        LEFT JOIN {TABELA_ORIGEM_ATENDIMENTO} origem_card
            ON origem_card.IDDimOrigemAtendimento = {id_origem_card_expr}

        LEFT JOIN {TABELA_ORIGEM_ATENDIMENTO} origem_emp
            ON origem_emp.IDDimOrigemAtendimento = emp.IDDimOrigemAtendimento

        LEFT JOIN {TABELA_TIPO_CLIENTE_DESCONTO} tipo_cliente
            ON tipo_cliente.IDDimTipoCliente = {id_tipo_cliente_card_expr}

        WHERE c.IDFatoKanbanCard = :id_card
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ISNULL(c.Ativo, 1) = 1;
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    if not row:
        return None

    card = dict(row)
    partes_endereco = [
        card.get("DescricaoTipoLogradouro"),
        card.get("Logradouro"),
        card.get("Numero"),
        card.get("Complemento"),
        card.get("Bairro"),
        card.get("Municipio"),
        card.get("UF"),
        card.get("CEP"),
    ]
    card["endereco_completo"] = " - ".join(str(parte) for parte in partes_endereco if parte not in (None, "")) or None

    return card



def _buscar_itens_pendentes_aprovacao_preco(id_card: int, id_empresa_proprietaria: int) -> list[dict[str, Any]]:
    """
    Eu busco os itens pendentes lendo somente FatoAprovaPreco.

    Importante:
    - mantenho id_negociacao_preco como alias de IDFatoAprovaPreco
      para não quebrar o HTML/JS atual.
    """

    sql = text(f"""
        SELECT
            ap.IDFatoAprovaPreco AS id_aprova_preco,
            ap.IDFatoAprovaPreco AS id_negociacao_preco,
            ap.IDFatoKanbanCard AS id_card,
            ap.IDDimPaineisEuromidia AS id_painel,
            ap.IDDimFacesPaineis AS id_face,

            ap.DataPrecoProposto AS data_preco_proposto,
            ap.PeriodoInicio AS periodo_inicio,
            ap.PeriodoTermino AS periodo_termino,
            ap.ObservacoesProposta AS observacoes_proposta,

            ap.CustoAtual AS custo_atual,
            ap.PrecoAtual AS preco_atual,
            ap.PrecoProposto AS preco_proposto,
            ap.MargemProposta AS margem_proposta,
            ap.DescontoProposto AS desconto_proposto,

            ap.IDDimUsuarios AS id_usuario_solicitante,
            usu.NomeUsuario AS nome_usuario_solicitante,

            COALESCE(pf.CodPonto, item_contrato.CodPonto) AS cod_ponto,
            COALESCE(pf.CodFace, item_contrato.CodFace) AS cod_face,
            COALESCE(pf.TipoPainel, item_contrato.Tipo) AS tipo_painel,

            pf.CustoTabela AS custo_tabela_operacional,
            pf.ValorTabela AS valor_tabela_operacional,
            pf.NovoValor AS novo_valor_operacional,
            pf.PercentualDesconto AS percentual_desconto_operacional,
            pf.ValorVendaFinal AS valor_venda_final_operacional,
            pf.MargemPercentual AS margem_percentual_operacional,

            'fato_aprova_preco' AS origem_dados

        FROM {TABELA_CARD_APROVA_PRECO} ap

        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = ap.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = :id_empresa_proprietaria

        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = ap.IDDimUsuarios
           AND (
                usu.IDEmpresaProprietaria = ap.IDEmpresaProprietaria
                OR usu.IDEmpresaProprietaria IS NULL
           )

        OUTER APPLY (
            SELECT TOP (1)
                pf1.CodPonto,
                pf1.CodFace,
                pf1.TipoPainel,
                pf1.CustoTabela,
                pf1.ValorTabela,
                pf1.NovoValor,
                pf1.PercentualDesconto,
                pf1.ValorVendaFinal,
                pf1.MargemPercentual
            FROM {TABELA_CARD_PAINEL_FACE} pf1
            WHERE pf1.IDFatoKanbanCard = ap.IDFatoKanbanCard
              AND ISNULL(pf1.Ativo, 1) = 1
              AND ISNULL(pf1.IDDimPaineisEuromidia, 0) = ISNULL(ap.IDDimPaineisEuromidia, 0)
              AND ISNULL(pf1.IDDimFacesPaineis, 0) = ISNULL(ap.IDDimFacesPaineis, 0)
            ORDER BY
                COALESCE(pf1.DataAtualizacao, pf1.CriadoEm, GETDATE()) DESC,
                pf1.IDFatoKanbanCardPainelFace DESC
        ) pf

        OUTER APPLY (
            SELECT TOP (1)
                i.CodPonto,
                i.CodFace,
                i.Tipo
            FROM {TABELA_CONTROLE_CONTRATOS_ITENS} i
            WHERE i.IDFatoControleContratoEuromidia = ap.IDFatoControleContratosEuromidia
              AND ISNULL(i.BitAtivo, 1) = 1
              AND (
                    ISNULL(i.IDPainelEuromidia, 0) = ISNULL(ap.IDDimPaineisEuromidia, 0)
                 OR ISNULL(i.IDDimFacesPaineis, 0) = ISNULL(ap.IDDimFacesPaineis, 0)
              )
            ORDER BY
                i.IDFatoControleContratosItensEuromidia DESC
        ) item_contrato

        WHERE ap.IDFatoKanbanCard = :id_card
          AND ap.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ap.PrecoAprovado IS NULL
          AND ap.DataAprovacaoPreco IS NULL
          AND ISNULL(c.Ativo, 1) = 1

        ORDER BY
            ap.DataPrecoProposto DESC,
            ap.IDFatoAprovaPreco DESC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().all()

    return _rows_para_dicts(rows)








def _inserir_nota_aprovacao_desconto_card(
    *,
    id_card: int,
    id_empresa_proprietaria: int,
    id_empresa_relacionada: int | None,
    id_usuario: int,
    texto_nota: str,
) -> dict[str, Any] | None:
    if not _objeto_existe(TABELA_CARD_NOTA):
        return None

    sql = text(f"""
        INSERT INTO {TABELA_CARD_NOTA}
        (
            IDFatoKanbanCard,
            TipoNota,
            Texto,
            CriadoEm,
            CriadoPor,
            IDEmpresaProprietaria,
            IDEmpresa
        )
        OUTPUT
            INSERTED.IDFatoKanbanCardNota,
            INSERTED.IDFatoKanbanCard,
            INSERTED.TipoNota,
            INSERTED.Texto,
            INSERTED.CriadoEm,
            INSERTED.CriadoPor,
            INSERTED.IDEmpresaProprietaria,
            INSERTED.IDEmpresa
        VALUES
        (
            :id_card,
            :tipo_nota,
            :texto,
            GETDATE(),
            :id_usuario,
            :id_empresa_proprietaria,
            :id_empresa_relacionada
        );
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "tipo_nota": TIPO_NOTA_APROVACAO_DESCONTO,
            "texto": str(texto_nota or "").strip()[:2000],
            "id_usuario": int(id_usuario),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_empresa_relacionada": int(id_empresa_relacionada or 0) or None,
        },
    ).mappings().first()

    return dict(row) if row else None




def _atualizar_item_operacional_aprovado(
    *,
    negociacao: Mapping[str, Any] | dict[str, Any],
    preco_aprovado: Decimal,
    desconto_aprovado_percentual: Decimal | None,
    id_usuario: int,
) -> int:
    id_card = int(negociacao.get("IDFatoKanbanCard") or 0)
    id_painel = int(negociacao.get("IDDimPaineisEuromidia") or 0)
    id_face = int(negociacao.get("IDDimFacesPaineis") or 0)

    custo_base = _valor_decimal(
        negociacao.get("CustoProposto")
        if negociacao.get("CustoProposto") not in (None, "")
        else negociacao.get("CustoAtual")
    ) or Decimal("0")

    margem_valor = preco_aprovado - custo_base
    margem_percentual = None
    if preco_aprovado > 0:
        margem_percentual = (margem_valor / preco_aprovado) * Decimal("100")

    params = {
        "id_card": id_card,
        "preco_aprovado": preco_aprovado,
        "desconto_aprovado": desconto_aprovado_percentual,
        "margem_valor": margem_valor,
        "margem_percentual": margem_percentual,
        "id_usuario": int(id_usuario),
    }

    where_extra = ""
    if id_painel > 0:
        where_extra += "\n          AND ISNULL(IDDimPaineisEuromidia, 0) = :id_painel"
        params["id_painel"] = id_painel
    if id_face > 0:
        where_extra += "\n          AND ISNULL(IDDimFacesPaineis, 0) = :id_face"
        params["id_face"] = id_face

    sql = text(f"""
        UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
        SET
            NovoValor = :preco_aprovado,
            PercentualDesconto = :desconto_aprovado,
            ValorVendaFinal = :preco_aprovado,
            MargemValor = :margem_valor,
            MargemPercentual = :margem_percentual,
            DataAtualizacao = GETDATE(),
            IDUsuario = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND ISNULL(Ativo, 1) = 1
          {where_extra};
    """)

    resultado = db.session.execute(sql, params)
    rowcount = int(getattr(resultado, "rowcount", 0) or 0)

    if rowcount > 0:
        return rowcount

    sql_fallback = text("""
        UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
        SET
            NovoValor = :preco_aprovado,
            PercentualDesconto = :desconto_aprovado,
            ValorVendaFinal = :preco_aprovado,
            MargemValor = :margem_valor,
            MargemPercentual = :margem_percentual,
            DataAtualizacao = GETDATE(),
            IDUsuario = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND ISNULL(Ativo, 1) = 1
          AND (
                SELECT COUNT(1)
                FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf2
                WHERE pf2.IDFatoKanbanCard = :id_card
                  AND ISNULL(pf2.Ativo, 1) = 1
          ) = 1;
    """)
    resultado_fallback = db.session.execute(sql_fallback, params)
    return int(getattr(resultado_fallback, "rowcount", 0) or 0)











def _buscar_negociacao_preco_para_aprovacao(
    *,
    id_card: int,
    id_negociacao_preco: int,
    id_empresa_proprietaria: int,
) -> dict[str, Any] | None:
    """
    Compatibilidade de nome.

    O front ainda envia id_negociacao_preco, mas enquanto está pendente
    esse ID representa IDFatoAprovaPreco.
    """

    sql = text(f"""
        SELECT TOP (1)
            ap.*,
            ap.IDFatoAprovaPreco AS IDFatoAprovaPreco,
            ap.IDFatoAprovaPreco AS IDFatoKanbanNegociacaoPreco
        FROM {TABELA_CARD_APROVA_PRECO} ap
        WHERE ap.IDFatoAprovaPreco = :id_aprova_preco
          AND ap.IDFatoKanbanCard = :id_card
          AND ap.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ap.PrecoAprovado IS NULL
          AND ap.DataAprovacaoPreco IS NULL;
    """)

    row = db.session.execute(
        sql,
        {
            "id_aprova_preco": int(id_negociacao_preco),
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    return dict(row) if row else None




















def _buscar_empresa_completa_aprovacao_preco(
    *,
    id_card: int,
    id_empresa_proprietaria: int,
    id_empresa_preferencial: int | None = None,
) -> dict[str, Any] | None:
    """
    Eu busco os dados completos da empresa para a tela de aprovação de preço.

    Regra:
    - não faço conversões nem tratamento pesado na query;
    - resolvo IDEmpresa por prioridade: parâmetro -> card -> pendência -> solicitação -> contrato;
    - trago DimEmpresas + DimCnaes + origem + tipo de cliente direto para o template.
    """

    def _expr_int_coluna(tabela: str, alias: str, coluna: str) -> str:
        if _coluna_existe(tabela, coluna):
            return f"{alias}.{coluna}"
        return "CAST(NULL AS int)"

    def _expr_texto_coluna(tabela: str, alias: str, coluna: str, tamanho: int = 300) -> str:
        if _coluna_existe(tabela, coluna):
            return f"CAST({alias}.{coluna} AS nvarchar({int(tamanho)}))"
        return f"CAST(NULL AS nvarchar({int(tamanho)}))"

    id_empresa_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDEmpresa")
    id_empresa_relacionada_expr = _expr_int_coluna(TABELA_CARD, "c", "IDEmpresaRelacionadaCard")
    id_cnae_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDDimCnaes")
    id_origem_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDDimOrigemAtendimento")
    id_tipo_cliente_card_expr = _expr_int_coluna(TABELA_CARD, "c", "IDDimTipoCliente")

    if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia"):
        id_contrato_card_expr = "c.IDFatoControleContratosEuromidia"
    elif _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia"):
        id_contrato_card_expr = "c.IDFatoControleContratoEuromidia"
    else:
        id_contrato_card_expr = "CAST(NULL AS int)"

    nome_origem_card_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_card", "NomeOrigemAtendimento", 200)
    desc_origem_card_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_card", "Descricao", 200)
    nome_origem_emp_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_emp", "NomeOrigemAtendimento", 200)
    desc_origem_emp_expr = _expr_texto_coluna(TABELA_ORIGEM_ATENDIMENTO, "origem_emp", "Descricao", 200)

    tipo_cliente_desc_expr = _expr_texto_coluna(TABELA_TIPO_CLIENTE_DESCONTO, "tipo_cliente", "Descricao", 200)
    tipo_cliente_nome_expr = _expr_texto_coluna(TABELA_TIPO_CLIENTE_DESCONTO, "tipo_cliente", "NomeTipoCliente", 200)

    id_empresa_preferencial_param = (
        int(id_empresa_preferencial)
        if id_empresa_preferencial not in (None, "", 0)
        else None
    )

    sql = text(f"""
        ;WITH CardBase AS (
            SELECT TOP (1)
                c.*
            FROM {TABELA_CARD} c
            WHERE c.IDFatoKanbanCard = :id_card
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
        ),
        PendenciaCard AS (
            SELECT TOP (1)
                ap.*
            FROM {TABELA_CARD_APROVA_PRECO} ap
            WHERE ap.IDFatoKanbanCard = :id_card
              AND ap.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ap.PrecoAprovado IS NULL
              AND ap.DataAprovacaoPreco IS NULL
            ORDER BY
                ap.DataPrecoProposto DESC,
                ap.IDFatoAprovaPreco DESC
        ),
        SolicitacaoCard AS (
            SELECT TOP (1)
                sol_base.*
            FROM {TABELA_SOLICITACAO_CONTRATO} sol_base
            WHERE sol_base.IDFatoKanbanCard = :id_card
              AND sol_base.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ISNULL(sol_base.BitAtivo, 1) = 1
            ORDER BY
                sol_base.DataAtualizacao DESC,
                sol_base.IDFatoSolicitacaoContratoEuromidia DESC
        ),
        ResolverEmpresa AS (
            SELECT
                COALESCE(
                    :id_empresa_preferencial,
                    {id_empresa_relacionada_expr},
                    {id_empresa_card_expr},
                    ap.IDEmpresa,
                    sol.IDEmpresa,
                    contrato.IDEmpresa
                ) AS IDEmpresaResolvida
            FROM CardBase c
            LEFT JOIN PendenciaCard ap ON 1 = 1
            LEFT JOIN SolicitacaoCard sol ON 1 = 1
            LEFT JOIN {TABELA_CONTROLE_CONTRATOS} contrato
                ON contrato.IDFatoControleContratosEuromidia = COALESCE(
                    ap.IDFatoControleContratosEuromidia,
                    sol.IDFatoControleContratosEuromidia,
                    {id_contrato_card_expr}
                )
        )
        SELECT TOP (1)
            emp.IDEmpresa,
            emp.IDEmpresaProprietaria,
            emp.CNPJ,
            emp.UF,
            emp.CEP,
            emp.CodigoPorte,
            emp.Pais,
            emp.Email,
            emp.Porte,
            emp.Bairro,
            emp.Numero,
            emp.TelefoneContato1,
            emp.TelefoneContato2,
            emp.Municipio,
            emp.Logradouro,
            emp.CNAE,
            emp.Complemento,
            emp.RazaoSocial,
            emp.NomeFantasia,
            emp.CapitalSocial,
            emp.NaturezaJuridica,
            emp.DescricaoCnae,
            emp.DataInicioAtividades,
            emp.DataSituacaoEspecial,
            emp.DataOpcaoPeloSimples,
            emp.DataSituacaoCadastral,
            emp.DataExclusaoSimples,
            emp.IdentificadorMatrizFilial,
            emp.DescricaoSituacaoCadastral,
            emp.DescricaoMotivoSituacaoCadastral,
            emp.DescricaoIdentificadorMatrizFilial,
            emp.DescricaoTipoLogradouro,
            emp.DataAtualizacao,
            emp.Latitude,
            emp.Longitude,
            emp.BitCliente,
            emp.BitClienteDireto,
            emp.IDDimOrigemAtendimento,

            COALESCE(cnae_emp.IDDimCnaes, cnae_card.IDDimCnaes) AS IDDimCnaes,
            COALESCE(cnae_emp.cnaepadrao, cnae_card.cnaepadrao) AS cnaepadrao,
            COALESCE(cnae_emp.Descricao, cnae_card.Descricao, emp.DescricaoCnae) AS CnaeDescricao,
            COALESCE(cnae_emp.Classe, cnae_card.Classe) AS CnaeClasse,
            COALESCE(cnae_emp.Setor, cnae_card.Setor) AS CnaeSetor,
            COALESCE(cnae_emp.MacroSetor, cnae_card.MacroSetor) AS CnaeMacroSetor,
            COALESCE(cnae_emp.SubClasse, cnae_card.SubClasse) AS CnaeSubClasse,
            COALESCE(cnae_emp.ClassificacaoMacro, cnae_card.ClassificacaoMacro) AS CnaeClassificacaoMacro,
            COALESCE(cnae_emp.ScoreSetor, cnae_card.ScoreSetor) AS ScoreSetor,
            COALESCE(cnae_emp.Hex, cnae_card.Hex) AS CnaeHex,

            COALESCE({id_origem_card_expr}, emp.IDDimOrigemAtendimento) AS IDDimOrigemAtendimentoFinal,
            COALESCE({nome_origem_card_expr}, {desc_origem_card_expr}, {nome_origem_emp_expr}, {desc_origem_emp_expr}) AS NomeOrigemAtendimento,

            {id_tipo_cliente_card_expr} AS IDDimTipoCliente,
            COALESCE({tipo_cliente_desc_expr}, {tipo_cliente_nome_expr}) AS TipoClienteDesconto,
            COALESCE({tipo_cliente_desc_expr}, {tipo_cliente_nome_expr}) AS TipoCliente,
            COALESCE({tipo_cliente_desc_expr}, {tipo_cliente_nome_expr}) AS NomeTipoCliente

        FROM ResolverEmpresa r
        INNER JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = r.IDEmpresaResolvida
        LEFT JOIN CardBase c
            ON 1 = 1
        LEFT JOIN {TABELA_CNAES} cnae_emp
            ON cnae_emp.cnaepadrao = emp.CNAE
        LEFT JOIN {TABELA_CNAES} cnae_card
            ON cnae_card.IDDimCnaes = {id_cnae_card_expr}
        LEFT JOIN {TABELA_ORIGEM_ATENDIMENTO} origem_card
            ON origem_card.IDDimOrigemAtendimento = {id_origem_card_expr}
        LEFT JOIN {TABELA_ORIGEM_ATENDIMENTO} origem_emp
            ON origem_emp.IDDimOrigemAtendimento = emp.IDDimOrigemAtendimento
        LEFT JOIN {TABELA_TIPO_CLIENTE_DESCONTO} tipo_cliente
            ON tipo_cliente.IDDimTipoCliente = {id_tipo_cliente_card_expr}
        WHERE r.IDEmpresaResolvida IS NOT NULL;
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_empresa_preferencial": id_empresa_preferencial_param,
        },
    ).mappings().first()

    if not row:
        return None

    empresa = dict(row)

    partes_endereco = [
        empresa.get("DescricaoTipoLogradouro"),
        empresa.get("Logradouro"),
        empresa.get("Numero"),
        empresa.get("Complemento"),
        empresa.get("Bairro"),
        empresa.get("Municipio"),
        empresa.get("UF"),
        empresa.get("CEP"),
    ]

    empresa["endereco_completo"] = " - ".join(
        str(parte)
        for parte in partes_endereco
        if parte not in (None, "")
    ) or None

    return empresa




@kanban_bp.route("/aprovacao-preco/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def aprovacao_preco_detalhe(id_card: int):
    id_usuario = _assert_login()
    _exigir_admin_aprovacao_desconto()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    card_escopo = _obter_card_autorizado(int(id_card))
    if not card_escopo:
        abort(404)

    id_kanban = int(card_escopo.get("IDDimKanban") or card_escopo.get("id_kanban") or 0)

    try:
        id_usuario_solicitante = _resolver_id_usuario_solicitante_desconto_card(
            id_card=int(id_card),
            id_usuario_fallback=int(id_usuario),
        )

        resultado_materializacao = _materializar_negociacao_preco_pendente_card(
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_usuario=int(id_usuario_solicitante or id_usuario),
        )

        db.session.commit()

        current_app.logger.info(
            "APROVACAO_PRECO_DETALHE | materializacao_ok | id_card=%s | id_usuario_solicitante=%s | resultado=%s",
            int(id_card),
            int(id_usuario_solicitante or id_usuario),
            resultado_materializacao,
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "APROVACAO_PRECO_DETALHE | falha ao materializar pendência | id_card=%s",
            int(id_card),
        )

    try:
        card_cabecalho = _buscar_cabecalho_aprovacao_preco(
            int(id_card),
            int(id_empresa_proprietaria),
        )
    except Exception:
        current_app.logger.exception(
            "APROVACAO_PRECO_DETALHE | falha ao buscar cabeçalho enriquecido | id_card=%s",
            int(id_card),
        )
        card_cabecalho = None

    card = dict(card_cabecalho or card_escopo or {})

    id_empresa_preferencial = (
        card.get("IDEmpresa")
        or card.get("id_empresa")
        or card.get("IDEmpresaRelacionadaCard")
        or card.get("id_empresa_relacionada")
        or card_escopo.get("IDEmpresa")
        or card_escopo.get("IDEmpresaRelacionadaCard")
    )

    try:
        empresa = _buscar_empresa_completa_aprovacao_preco(
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_empresa_preferencial=int(id_empresa_preferencial) if id_empresa_preferencial not in (None, "", 0) else None,
        )
    except Exception:
        current_app.logger.exception(
            "APROVACAO_PRECO_DETALHE | falha ao buscar empresa completa | id_card=%s | id_empresa_preferencial=%s",
            int(id_card),
            id_empresa_preferencial,
        )
        empresa = None

    if empresa:
        card["id_empresa"] = empresa.get("IDEmpresa")
        card["IDEmpresa"] = empresa.get("IDEmpresa")
        card["IDEmpresaRelacionadaCard"] = empresa.get("IDEmpresa")

        card["nome_empresa"] = (
            empresa.get("NomeFantasia")
            or empresa.get("RazaoSocial")
            or card.get("NomeEmpresa")
            or card.get("EmpresaRazaoSocial")
            or card.get("Titulo")
            or "Empresa não identificada"
        )
        card["NomeEmpresa"] = card["nome_empresa"]

        card["razao_social"] = empresa.get("RazaoSocial") or card.get("razao_social")
        card["EmpresaRazaoSocial"] = card.get("razao_social")
        card["RazaoSocial"] = card.get("razao_social")

        card["nome_fantasia"] = empresa.get("NomeFantasia") or card.get("nome_fantasia")
        card["NomeFantasia"] = card.get("nome_fantasia")

        card["cnpj"] = empresa.get("CNPJ") or card.get("cnpj")
        card["EmpresaCNPJ"] = card.get("cnpj")
        card["CNPJ"] = card.get("cnpj")

        card["cnae_empresa"] = empresa.get("CNAE") or empresa.get("cnaepadrao") or card.get("cnae_empresa")
        card["EmpresaCNAE"] = card.get("cnae_empresa")
        card["CNAE"] = card.get("cnae_empresa")

        card["descricao_cnae"] = empresa.get("CnaeDescricao") or empresa.get("DescricaoCnae") or card.get("descricao_cnae")
        card["DescricaoCnae"] = card.get("descricao_cnae")
        card["CnaeDescricao"] = card.get("descricao_cnae")

        card["classe_cnae"] = empresa.get("CnaeClasse") or card.get("classe_cnae")
        card["EmpresaClasse"] = card.get("classe_cnae")
        card["CnaeClasse"] = card.get("classe_cnae")

        card["setor_cnae"] = empresa.get("CnaeSetor") or card.get("setor_cnae")
        card["EmpresaSetor"] = card.get("setor_cnae")
        card["CnaeSetor"] = card.get("setor_cnae")

        card["macro_setor"] = empresa.get("CnaeMacroSetor") or card.get("macro_setor")
        card["MacroSetor"] = card.get("macro_setor")
        card["CnaeMacroSetor"] = card.get("macro_setor")

        card["subclasse_cnae"] = empresa.get("CnaeSubClasse") or card.get("subclasse_cnae")
        card["SubClasse"] = card.get("subclasse_cnae")
        card["CnaeSubClasse"] = card.get("subclasse_cnae")

        card["classificacao_macro"] = empresa.get("CnaeClassificacaoMacro") or card.get("classificacao_macro")
        card["ClassificacaoMacro"] = card.get("classificacao_macro")
        card["CnaeClassificacaoMacro"] = card.get("classificacao_macro")

        card["score_setor"] = empresa.get("ScoreSetor") or card.get("score_setor")
        card["ScoreSetor"] = card.get("score_setor")
        card["hex_cnae"] = empresa.get("CnaeHex") or card.get("hex_cnae")
        card["CnaeHex"] = card.get("hex_cnae")

        card["email_empresa"] = empresa.get("Email") or card.get("email_empresa")
        card["Email"] = card.get("email_empresa")

        card["telefone_empresa"] = empresa.get("TelefoneContato1") or empresa.get("TelefoneContato2") or card.get("telefone_empresa")
        card["Telefone"] = card.get("telefone_empresa")
        card["TelefoneContato1"] = empresa.get("TelefoneContato1") or card.get("TelefoneContato1")
        card["TelefoneContato2"] = empresa.get("TelefoneContato2") or card.get("TelefoneContato2")

        card["municipio"] = empresa.get("Municipio") or card.get("municipio")
        card["Municipio"] = card.get("municipio")

        card["uf"] = empresa.get("UF") or card.get("uf")
        card["UF"] = card.get("uf")

        card["bairro"] = empresa.get("Bairro") or card.get("bairro")
        card["Bairro"] = card.get("bairro")

        card["logradouro"] = empresa.get("Logradouro") or card.get("logradouro")
        card["Logradouro"] = card.get("logradouro")

        card["numero"] = empresa.get("Numero") or card.get("numero")
        card["Numero"] = card.get("numero")

        card["cep"] = empresa.get("CEP") or card.get("cep")
        card["CEP"] = card.get("cep")

        card["complemento"] = empresa.get("Complemento") or card.get("complemento")
        card["Complemento"] = card.get("complemento")

        card["endereco_completo"] = empresa.get("endereco_completo") or card.get("endereco_completo")

        card["latitude"] = empresa.get("Latitude") or card.get("latitude")
        card["Latitude"] = card.get("latitude")
        card["longitude"] = empresa.get("Longitude") or card.get("longitude")
        card["Longitude"] = card.get("longitude")

        card["porte"] = empresa.get("Porte") or card.get("porte")
        card["Porte"] = card.get("porte")

        card["capital_social"] = empresa.get("CapitalSocial") or card.get("capital_social")
        card["CapitalSocial"] = card.get("capital_social")

        card["natureza_juridica"] = empresa.get("NaturezaJuridica") or card.get("natureza_juridica")
        card["NaturezaJuridica"] = card.get("natureza_juridica")

        card["situacao_cadastral"] = empresa.get("DescricaoSituacaoCadastral") or card.get("situacao_cadastral")
        card["DescricaoSituacaoCadastral"] = card.get("situacao_cadastral")
        card["SituacaoCadastral"] = card.get("situacao_cadastral")
        card["DescricaoMotivoSituacaoCadastral"] = empresa.get("DescricaoMotivoSituacaoCadastral") or card.get("DescricaoMotivoSituacaoCadastral")

        card["data_inicio_atividades"] = empresa.get("DataInicioAtividades") or card.get("data_inicio_atividades")
        card["DataInicioAtividades"] = card.get("data_inicio_atividades")

        card["IDDimOrigemAtendimento"] = empresa.get("IDDimOrigemAtendimentoFinal") or empresa.get("IDDimOrigemAtendimento") or card.get("IDDimOrigemAtendimento")
        card["NomeOrigemAtendimento"] = empresa.get("NomeOrigemAtendimento") or card.get("NomeOrigemAtendimento")
        card["OrigemAtendimento"] = card.get("NomeOrigemAtendimento") or card.get("OrigemAtendimento")

        card["IDDimTipoCliente"] = empresa.get("IDDimTipoCliente") or card.get("IDDimTipoCliente")
        card["TipoClienteDesconto"] = empresa.get("TipoClienteDesconto") or empresa.get("TipoCliente") or empresa.get("NomeTipoCliente") or card.get("TipoClienteDesconto")
        card["TipoCliente"] = card.get("TipoClienteDesconto")
        card["NomeTipoCliente"] = card.get("TipoClienteDesconto")

    else:
        nome_empresa = (
            card.get("NomeEmpresa")
            or card.get("EmpresaRazaoSocial")
            or card.get("RazaoSocial")
            or card.get("Titulo")
            or "Empresa não identificada"
        )

        card["nome_empresa"] = nome_empresa
        card["NomeEmpresa"] = nome_empresa
        card["razao_social"] = card.get("EmpresaRazaoSocial") or card.get("RazaoSocial") or nome_empresa
        card["EmpresaRazaoSocial"] = card["razao_social"]
        card["nome_fantasia"] = card.get("NomeFantasia")
        card["cnpj"] = card.get("EmpresaCNPJ") or card.get("CNPJ")
        card["EmpresaCNPJ"] = card["cnpj"]
        card["cnae_empresa"] = card.get("EmpresaCNAE") or card.get("CNAE")
        card["classe_cnae"] = card.get("EmpresaClasse") or card.get("CnaeClasse")
        card["setor_cnae"] = card.get("EmpresaSetor") or card.get("CnaeSetor")
        card["macro_setor"] = card.get("MacroSetor") or card.get("CnaeMacroSetor")
        card["subclasse_cnae"] = card.get("SubClasse") or card.get("CnaeSubClasse")
        card["classificacao_macro"] = card.get("ClassificacaoMacro") or card.get("CnaeClassificacaoMacro")
        card["situacao_cadastral"] = card.get("DescricaoSituacaoCadastral") or card.get("SituacaoCadastral")

    card["id_card"] = int(card.get("id_card") or card.get("IDFatoKanbanCard") or id_card)
    card["IDFatoKanbanCard"] = int(card.get("IDFatoKanbanCard") or card.get("id_card") or id_card)

    card["id_kanban"] = int(card.get("id_kanban") or card.get("IDDimKanban") or id_kanban or 0)
    card["IDDimKanban"] = int(card.get("IDDimKanban") or card.get("id_kanban") or id_kanban or 0)

    card["titulo"] = card.get("titulo") or card.get("Titulo")
    card["Titulo"] = card.get("Titulo") or card.get("titulo")

    card["nome_fase"] = card.get("nome_fase") or card.get("NomeFase") or card.get("FaseAtual")
    card["NomeFase"] = card.get("NomeFase") or card.get("nome_fase")

    card["nome_usuario_responsavel"] = (
        card.get("nome_usuario_responsavel")
        or card.get("NomeUsuarioResponsavel")
        or card.get("NomeUsuario")
        or "Não informado"
    )
    card["NomeUsuarioResponsavel"] = card["nome_usuario_responsavel"]

    card["TipoSolicitacao"] = (
        card.get("TipoSolicitacao")
        or card.get("tipo_contrato")
        or card.get("TipoContrato")
        or (
            "ADITIVO"
            if bool(card.get("BitAditivo"))
            else "NOVO CONTRATO"
            if bool(card.get("BitContratoNovo"))
            else None
        )
    )

    card["TipoClienteDesconto"] = (
        card.get("TipoClienteDesconto")
        or card.get("TipoCliente")
        or card.get("NomeTipoCliente")
        or (
            "Cliente Direto"
            if int(card.get("BitClienteDireto") or 0) == 1
            else "Agência"
            if int(card.get("BitAgencia") or 0) == 1
            else "Planejador"
            if int(card.get("BitPlanejador") or 0) == 1
            else None
        )
    )

    try:
        pendencias = _buscar_itens_pendentes_aprovacao_preco(
            int(id_card),
            int(id_empresa_proprietaria),
        )
    except Exception:
        current_app.logger.exception(
            "APROVACAO_PRECO_DETALHE | falha ao buscar pendências | id_card=%s",
            int(id_card),
        )
        pendencias = []

    try:
        historico_precos = _buscar_historico_precos_card(
            int(id_card),
            int(id_empresa_proprietaria),
        )
    except Exception:
        current_app.logger.exception(
            "APROVACAO_PRECO_DETALHE | falha ao buscar histórico de preços | id_card=%s",
            int(id_card),
        )
        historico_precos = []

    try:
        tags_ativas = _obter_tags_do_card(int(id_card))
    except Exception:
        current_app.logger.exception(
            "APROVACAO_PRECO_DETALHE | falha ao buscar tags | id_card=%s",
            int(id_card),
        )
        tags_ativas = []

    current_app.logger.info(
        "APROVACAO_PRECO_DETALHE | render | id_card=%s | id_empresa=%s | nome_fantasia=%s | cnpj=%s | cnae=%s | subclasse=%s | classificacao=%s | origem=%s | porte=%s | tipo_cliente=%s | situacao=%s | pendencias=%s",
        int(id_card),
        card.get("id_empresa") or card.get("IDEmpresa") or card.get("IDEmpresaRelacionadaCard"),
        card.get("nome_fantasia") or card.get("NomeFantasia"),
        card.get("cnpj") or card.get("EmpresaCNPJ"),
        card.get("cnae_empresa") or card.get("EmpresaCNAE"),
        card.get("subclasse_cnae") or card.get("SubClasse") or card.get("CnaeSubClasse"),
        card.get("classificacao_macro") or card.get("ClassificacaoMacro") or card.get("CnaeClassificacaoMacro"),
        card.get("OrigemAtendimento") or card.get("NomeOrigemAtendimento"),
        card.get("porte") or card.get("Porte"),
        card.get("TipoClienteDesconto") or card.get("TipoCliente"),
        card.get("situacao_cadastral") or card.get("DescricaoSituacaoCadastral"),
        len(pendencias or []),
    )

    return render_template(
        "kanban/aprovacao_preco_detalhe.html",
        card=card,
        empresa=card,
        pendencias=pendencias,
        historico_precos=historico_precos,
        tags_ativas=tags_ativas,
    )



@kanban_bp.route("/api/aprovacao-preco/<int:id_card>/aprovar", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_aprovacao_preco_aprovar(id_card: int):
    id_usuario = _assert_login()
    _exigir_admin_aprovacao_desconto()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()
    card_escopo = _obter_card_autorizado(int(id_card))
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    payload = request.get_json(silent=True) or {}

    try:
        id_aprova_preco = int(
            payload.get("id_aprova_preco")
            or payload.get("id_negociacao_preco")
            or 0
        )
    except Exception:
        id_aprova_preco = 0

    preco_aprovado = _valor_decimal(payload.get("preco_aprovado"))
    observacoes_aprovacao = (payload.get("observacoes_aprovacao") or "").strip()

    if id_aprova_preco <= 0:
        return jsonify({"ok": False, "msg": "ID da pendência de aprovação é obrigatório."}), 400

    if preco_aprovado is None or preco_aprovado <= 0:
        return jsonify({"ok": False, "msg": "Informe um preço aprovado válido."}), 400

    pendencia = _buscar_negociacao_preco_para_aprovacao(
        id_card=int(id_card),
        id_negociacao_preco=int(id_aprova_preco),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )

    if not pendencia:
        return jsonify({"ok": False, "msg": "Pendência de aprovação de preço não encontrada."}), 404

    id_usuario_solicitante_aprovacao = int(pendencia.get("IDDimUsuarios") or id_usuario or 0)

    preco_base_desconto = _valor_decimal(pendencia.get("PrecoAtual"))

    if preco_base_desconto is None or preco_base_desconto <= 0:
        preco_base_desconto = _valor_decimal(pendencia.get("PrecoProposto"))

    custo_base = _valor_decimal(
        pendencia.get("CustoProposto")
        if pendencia.get("CustoProposto") not in (None, "")
        else pendencia.get("CustoAtual")
    ) or Decimal("0")

    desconto_aprovado_percentual = None
    desconto_valor = None

    if preco_base_desconto is not None and preco_base_desconto > 0:
        desconto_valor = preco_base_desconto - preco_aprovado
        desconto_aprovado_percentual = (desconto_valor / preco_base_desconto) * Decimal("100")

    margem_valor = preco_aprovado - custo_base
    margem_percentual = None

    if preco_aprovado > 0:
        margem_percentual = (margem_valor / preco_aprovado) * Decimal("100")

    etapa = "inicio"
    snapshot_preco_praticado = None
    resultado_movimento = None
    id_historico_negociacao_preco = None

    try:
        etapa = "mover_aprovacao_preco_para_historico"
        resultado_movimento = _mover_aprovacao_preco_para_historico(
            id_aprova_preco=int(id_aprova_preco),
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_usuario_aprovador=int(id_usuario),
            preco_aprovado=preco_aprovado,
            desconto_aprovado=desconto_aprovado_percentual,
            observacoes_aprovacao=observacoes_aprovacao,
        )

        id_historico_negociacao_preco = int(
            resultado_movimento.get("id_historico_negociacao_preco") or 0
        ) or None

        if not id_historico_negociacao_preco:
            raise RuntimeError(
                "A aprovação foi processada, mas o ID histórico da negociação não foi retornado."
            )

        etapa = "update_operacional"
        _atualizar_item_operacional_aprovado(
            negociacao=pendencia,
            preco_aprovado=preco_aprovado,
            desconto_aprovado_percentual=desconto_aprovado_percentual,
            id_usuario=int(id_usuario),
        )

        etapa = "sincronizar_snapshot_preco_praticado"
        snapshot_preco_praticado = _sincronizar_aprovacao_preco_no_snapshot_preco_praticado(
            id_card=int(id_card),
            id_usuario_aprovacao=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            negociacao=pendencia,
            preco_aprovado=preco_aprovado,
            desconto_aprovado_percentual=desconto_aprovado_percentual,
            margem_percentual=margem_percentual,
        )

        if not snapshot_preco_praticado or not snapshot_preco_praticado.get("ok"):
            motivo_snapshot = (
                snapshot_preco_praticado.get("motivo")
                if isinstance(snapshot_preco_praticado, dict)
                else "snapshot_preco_praticado_aprovacao_preco_nao_retorno_ok"
            )
            raise RuntimeError(
                "Falha ao sincronizar a foto do preço praticado na aprovação do preço. "
                f"Motivo: {motivo_snapshot}"
            )

        etapa = "sincronizar_tags"
        estados_atuais = _listar_estado_atual_negociacao_card(int(id_card))

        _sincronizar_tag_aprovacao_diretoria_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            estados_atuais=estados_atuais,
            id_usuario=int(id_usuario_solicitante_aprovacao or id_usuario),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
        )

        nome_usuario = (
            getattr(current_user, "NomeUsuario", None)
            or getattr(current_user, "nome", None)
            or f"Usuário #{int(id_usuario)}"
        )

        texto_nota = (
            f"Desconto aprovado: "
            f"{_formatar_decimal_br(desconto_aprovado_percentual, 2) if desconto_aprovado_percentual is not None else '0,00'}% | "
            f"Valor do desconto: R$ {_formatar_decimal_br(desconto_valor, 2) if desconto_valor is not None else '0,00'} | "
            f"Preço aprovado: R$ {_formatar_decimal_br(preco_aprovado, 2)} | "
            f"Margem aprovada: {_formatar_decimal_br(margem_percentual, 2) if margem_percentual is not None else '0,00'}% | "
            f"Aprovado por: {nome_usuario}"
        )

        if observacoes_aprovacao:
            texto_nota += f" | Observações: {observacoes_aprovacao}"

        etapa = "gravar_nota_tabela_oficial"
        _inserir_nota_aprovacao_desconto_card(
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_empresa_relacionada=int(pendencia.get("IDEmpresa") or 0) or None,
            id_usuario=int(id_usuario),
            texto_nota=texto_nota,
        )

        etapa = "espelhar_nota_observacoes"
        _registrar_observacao_historica_card(
            id_card=int(id_card),
            texto_observacao=texto_nota,
            id_usuario=int(id_usuario),
        )

        etapa = "registrar_log"
        snapshot_depois = _obter_snapshot_card_log(int(id_card), incluir_inativo=True)

        _registrar_log_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_usuario_acao=int(id_usuario),
            tipo_evento="CARD_PRECO_APROVADO",
            subtipo_evento="APROVACAO_DESCONTO",
            observacao=texto_nota,
            tabela_origem=TABELA_CARD_NEGOCIACAO_PRECO,
            id_registro_origem=int(id_historico_negociacao_preco),
            payload_depois=snapshot_depois,
        )

        etapa = "commit"
        db.session.commit()

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Erro ao aprovar preço do card. etapa=%s id_card=%s id_aprova_preco=%s",
            etapa,
            id_card,
            id_aprova_preco,
        )
        return jsonify({"ok": False, "msg": f"Erro ao aprovar preço: {str(exc)}"}), 500

    _invalidar_kanban(
        id_emp=int(id_empresa_proprietaria),
        id_kanban=int(id_kanban),
        id_card=int(id_card),
    )

    _emitir_evento_kanban(
        int(id_kanban),
        "card_atualizado",
        {"id_card": int(id_card)},
    )

    return jsonify(
        {
            "ok": True,
            "msg": "Preço aprovado com sucesso.",
            "id_card": int(id_card),
            "id_aprova_preco_removido": int(id_aprova_preco),
            "id_negociacao_preco": int(id_historico_negociacao_preco),
            "resultado_movimento": resultado_movimento,
            "tags": _obter_tags_do_card(int(id_card)),
            "nota": texto_nota,
            "snapshot_preco_praticado": snapshot_preco_praticado,
            "historico_precos_url": url_for("kanban.historico_precos_visualizacao", id_card=int(id_card)),
            "historico_card_url": url_for("kanban.historico_card_visualizacao", id_card=int(id_card)),
        }
    )






def _normalizar_cnpj(valor: Any) -> str:
    """Eu removo qualquer caractere não numérico do CNPJ."""
    return re.sub(r"\D+", "", str(valor or ""))[:14]


def _formatar_cnpj(valor: Any) -> str:
    """Eu devolvo o CNPJ mascarado quando ele tiver 14 dígitos."""
    cnpj = _normalizar_cnpj(valor)
    if len(cnpj) != 14:
        return str(valor or "")
    return f"{cnpj[0:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:14]}"


def _texto_ou_none(valor: Any, tamanho_maximo: int | None = None) -> str | None:
    """Eu normalizo textos opcionais e corto no tamanho da coluna quando necessário."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    if tamanho_maximo is not None:
        texto = texto[: int(tamanho_maximo)]
    return texto


def _somente_digitos(valor: Any) -> str:
    """Eu removo qualquer caractere que não seja número."""
    return re.sub(r"\D+", "", str(valor or ""))


def _normalizar_telefone_card(valor: Any, tamanho_maximo: int = 30) -> str | None:
    """Eu salvo o telefone do card somente com DDD + número."""
    telefone = _somente_digitos(valor)
    if not telefone:
        return None
    return telefone[: int(tamanho_maximo)]


def _obter_dados_card_para_contato_contrato(id_card: int) -> dict[str, Any] | None:
    if int(id_card or 0) <= 0:
        return None

    col_id_empresa = (
        'c.IDEmpresa AS IDEmpresa,'
        if _coluna_existe(TABELA_CARD, 'IDEmpresa')
        else 'CAST(NULL AS int) AS IDEmpresa,'
    )
    col_id_empresa_prop = (
        'c.IDEmpresaProprietaria AS IDEmpresaProprietaria,'
        if _coluna_existe(TABELA_CARD, 'IDEmpresaProprietaria')
        else 'CAST(NULL AS int) AS IDEmpresaProprietaria,'
    )
    col_telefone = (
        'c.Telefone AS Telefone,'
        if _coluna_existe(TABELA_CARD, 'Telefone')
        else 'CAST(NULL AS varchar(30)) AS Telefone,'
    )
    col_email = (
        'c.Email AS Email,'
        if _coluna_existe(TABELA_CARD, 'Email')
        else 'CAST(NULL AS nvarchar(200)) AS Email,'
    )
    col_id_contrato = (
        'c.IDFatoControleContratosEuromidia AS IDFatoControleContratosEuromidia'
        if _coluna_existe(TABELA_CARD, 'IDFatoControleContratosEuromidia')
        else 'CAST(NULL AS int) AS IDFatoControleContratosEuromidia'
    )

    row = db.session.execute(
        text(
            f"""
            SELECT TOP 1
                c.IDFatoKanbanCard,
                {col_id_empresa}
                {col_id_empresa_prop}
                {col_telefone}
                {col_email}
                {col_id_contrato}
            FROM {TABELA_CARD} c
            WHERE c.IDFatoKanbanCard = :id_card
            """
        ),
        {'id_card': int(id_card)},
    ).mappings().first()

    return dict(row) if row else None



def _upsert_dim_contatos_contrato_por_card(
    *,
    id_card: int,
    id_empresa: int | None = None,
    id_empresa_proprietaria: int | None = None,
    id_fato_controle_contratos: int | None = None,
) -> dict[str, Any]:
    if not _objeto_existe(TABELA_CONTATOS_CONTRATO):
        return {'ok': False, 'motivo': 'tabela_dim_contatos_contrato_ausente'}

    dados_card = _obter_dados_card_para_contato_contrato(int(id_card))
    if not dados_card:
        return {'ok': False, 'motivo': 'card_nao_encontrado'}

    id_empresa_final = _int_ou_none(id_empresa) or _resolver_id_empresa_principal_por_tipo_cliente(dados_card)
    id_empresa_proprietaria_final = _int_ou_none(id_empresa_proprietaria) or _int_ou_none(dados_card.get('IDEmpresaProprietaria'))
    id_contrato_final = _int_ou_none(id_fato_controle_contratos) or _int_ou_none(dados_card.get('IDFatoControleContratosEuromidia'))
    telefone = _normalizar_telefone_card(dados_card.get('Telefone'), 30)
    email = _texto_ou_none(dados_card.get('Email'), 200)

    if id_empresa_final in (None, 0) and not telefone and not email:
        return {'ok': False, 'motivo': 'card_sem_empresa_e_sem_contato'}

    row_existente = db.session.execute(
        text(f"""
            SELECT TOP 1 IDDimContatosContrato
            FROM {TABELA_CONTATOS_CONTRATO}
            WHERE IDFatoKanbanCard = :id_card
            ORDER BY IDDimContatosContrato DESC
        """),
        {'id_card': int(id_card)},
    ).mappings().first()

    if row_existente and row_existente.get('IDDimContatosContrato') not in (None, '', 0):
        id_contato = int(row_existente['IDDimContatosContrato'])
        db.session.execute(
            text(f"""
                UPDATE {TABELA_CONTATOS_CONTRATO}
                   SET Telefone = :telefone,
                       Email = :email,
                       IDFatoControleContratosEuromidia = COALESCE(:id_fato_controle_contratos, IDFatoControleContratosEuromidia),
                       IDEmpresa = COALESCE(:id_empresa, IDEmpresa),
                       IDEmpresaProprietaria = COALESCE(:id_empresa_proprietaria, IDEmpresaProprietaria),
                       IDFatoKanbanCard = :id_card
                 WHERE IDDimContatosContrato = :id_contato
            """),
            {
                'telefone': telefone,
                'email': email,
                'id_fato_controle_contratos': id_contrato_final,
                'id_empresa': id_empresa_final,
                'id_empresa_proprietaria': id_empresa_proprietaria_final,
                'id_card': int(id_card),
                'id_contato': id_contato,
            },
        )
        return {
            'ok': True,
            'acao': 'atualizado',
            'IDDimContatosContrato': id_contato,
            'IDFatoControleContratosEuromidia': id_contrato_final,
            'IDEmpresa': id_empresa_final,
        }

    row_novo = db.session.execute(
        text(f"""
            INSERT INTO {TABELA_CONTATOS_CONTRATO}
            (
                Telefone,
                Email,
                IDFatoControleContratosEuromidia,
                IDEmpresa,
                IDEmpresaProprietaria,
                IDFatoKanbanCard
            )
            OUTPUT INSERTED.IDDimContatosContrato AS id_contato
            VALUES
            (
                :telefone,
                :email,
                :id_fato_controle_contratos,
                :id_empresa,
                :id_empresa_proprietaria,
                :id_card
            )
        """),
        {
            'telefone': telefone,
            'email': email,
            'id_fato_controle_contratos': id_contrato_final,
            'id_empresa': id_empresa_final,
            'id_empresa_proprietaria': id_empresa_proprietaria_final,
            'id_card': int(id_card),
        },
    ).mappings().first()

    return {
        'ok': True,
        'acao': 'inserido',
        'IDDimContatosContrato': int(row_novo.get('id_contato') or 0) if row_novo else None,
        'IDFatoControleContratosEuromidia': id_contrato_final,
        'IDEmpresa': id_empresa_final,
    }



def _empresa_existe_por_id(id_empresa: int | None) -> bool:
    """Eu valido se a empresa existe na DimEmpresas."""
    if id_empresa in (None, 0):
        return False

    sql = text(f"""
        SELECT 1
        FROM {TABELA_EMPRESAS}
        WHERE IDEmpresa = :id_empresa;
    """)
    return bool(db.session.execute(sql, {"id_empresa": int(id_empresa)}).scalar())


def _resolver_campos_complementares_novo_contrato(
    *,
    usar_novo_contrato: bool,
    id_empresa_agencia: Any,
    marca: Any,
    telefone: Any,
    email: Any,
) -> dict[str, Any]:
    """
    Eu resolvo os campos comerciais do card.

    Observação importante:
    - apesar do nome histórico da função falar "novo contrato", Marca, Telefone
      e Email não podem ser apagados quando o fluxo é Aditivo;
    - esses campos pertencem ao atendimento/card e devem ser persistidos nos dois fluxos.
    """
    id_empresa_agencia_int = _int_ou_none(id_empresa_agencia)
    if id_empresa_agencia not in (None, "", 0) and id_empresa_agencia_int is None:
        raise ValueError("Agência inválida.")

    if id_empresa_agencia_int is not None and not _empresa_existe_por_id(id_empresa_agencia_int):
        raise ValueError("Agência não encontrada.")

    return {
        "id_empresa_agencia": id_empresa_agencia_int,
        "marca": _texto_ou_none(marca, 100),
        "telefone": _normalizar_telefone_card(telefone, 30),
        "email": _texto_ou_none(email, 200),
    }



def _resolver_ids_empresas_card_por_tipo_cliente(
    *,
    id_tipo_cliente: Any,
    id_empresa_principal: Any,
    id_empresa_agencia: Any = None,
    id_empresa_bureau: Any = None,
    id_empresa_cliente_direto: Any = None,
) -> dict[str, Any]:
    """
    Regra correta de persistência do card:
    - IDEmpresa = sempre a empresa principal informada no primeiro campo
    - IDEmpresaAgencia = agência informada
    - IDEmpresaBureau = bureau informado

    Observação importante:
    - id_empresa_cliente_direto continua sendo validado e retornado,
      mas NÃO sobrescreve IDEmpresa.
    - hoje não existe coluna própria para persistir cliente direto sem
      conflitar com a empresa principal.
    """
    id_tipo = _int_ou_none(id_tipo_cliente)
    id_principal = _int_ou_none(id_empresa_principal)
    id_agencia = _int_ou_none(id_empresa_agencia)
    id_bureau = _int_ou_none(id_empresa_bureau)
    id_cliente_direto = _int_ou_none(id_empresa_cliente_direto)

    validacoes = (
        ("empresa principal", id_empresa_principal, id_principal),
        ("agência", id_empresa_agencia, id_agencia),
        ("bureau", id_empresa_bureau, id_bureau),
        ("cliente direto", id_empresa_cliente_direto, id_cliente_direto),
    )

    for nome_campo, valor_bruto, valor_int in validacoes:
        if valor_bruto not in (None, "", 0) and valor_int is None:
            raise ValueError(f"{nome_campo.capitalize()} inválido(a).")
        if valor_int is not None and not _empresa_existe_por_id(valor_int):
            raise ValueError(f"{nome_campo.capitalize()} não encontrado(a).")

    return {
        "id_empresa_card": id_principal,
        "id_empresa_agencia_card": id_agencia,
        "id_empresa_bureau_card": id_bureau,
        "id_empresa_principal": id_principal,
        "id_empresa_cliente_direto": id_cliente_direto,
        "id_tipo_cliente": id_tipo,
    }






def _resolver_id_empresa_principal_por_tipo_cliente(
    card: Mapping[str, Any] | dict[str, Any] | None,
) -> int | None:
    """
    Regra correta:
    - a empresa principal do card é sempre IDEmpresa.
    - agência e bureau são complementares, nunca substituem a principal.
    """
    if not card:
        return None

    return (
        _int_ou_none(card.get("IDEmpresa"))
        or _int_ou_none(card.get("IDEmpresaRelacionadaCard"))
        or _int_ou_none(card.get("IDEmpresaAgencia"))
        or _int_ou_none(card.get("IDEmpresaBureau"))
    )







def _resolver_empresas_snapshot_solicitacao_do_card(
    *,
    id_card: int,
    id_empresa_principal: Any = None,
) -> dict[str, Any]:
    """
    Resolve as empresas do snapshot da solicitação com a regra correta:
    - IDEmpresa = empresa principal
    - IDEmpresaAgencia = agência
    - IDEmpresaBureau = bureau
    """
    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe, dict) else {}
    card = card if isinstance(card, dict) else {}

    id_empresa_principal_int = _int_ou_none(id_empresa_principal) or _int_ou_none(card.get("IDEmpresa"))
    id_empresa_agencia_int = _int_ou_none(card.get("IDEmpresaAgencia"))
    id_empresa_bureau_int = _int_ou_none(card.get("IDEmpresaBureau"))
    id_tipo_cliente_int = _int_ou_none(
        card.get("IDDimTipoCliente") or card.get("IDDimKanbanTipoClienteDesconto")
    )

    def _buscar_empresa(id_empresa: Any) -> dict[str, Any] | None:
        id_empresa_int = _int_ou_none(id_empresa)
        if id_empresa_int in (None, 0):
            return None

        row = db.session.execute(
            text(
                f"""
                SELECT TOP (1)
                    e.IDEmpresa,
                    e.CNPJ,
                    e.RazaoSocial
                FROM {TABELA_EMPRESAS} e
                WHERE e.IDEmpresa = :id_empresa;
                """
            ),
            {"id_empresa": int(id_empresa_int)},
        ).mappings().first()

        return dict(row) if row else None

    empresa_principal = _buscar_empresa(id_empresa_principal_int)
    empresa_agencia = _buscar_empresa(id_empresa_agencia_int)
    empresa_bureau = _buscar_empresa(id_empresa_bureau_int)

    return {
        "id_tipo_cliente": id_tipo_cliente_int,
        "id_empresa_principal": id_empresa_principal_int,
        "id_empresa_agencia": id_empresa_agencia_int,
        "id_empresa_bureau": id_empresa_bureau_int,
        "empresa_principal": empresa_principal,
        "empresa_agencia": empresa_agencia,
        "empresa_bureau": empresa_bureau,
    }








def _validar_preenchimento_empresas_fase_4(
    *,
    id_tipo_cliente: Any,
    id_empresa_principal: Any,
    id_empresa_agencia: Any = None,
    id_empresa_bureau: Any = None,
    id_empresa_cliente_direto: Any = None,
    contexto: str = "salvar o card na fase 4",
) -> None:
    id_tipo = _int_ou_none(id_tipo_cliente)
    id_principal = _int_ou_none(id_empresa_principal)

    if id_tipo is None:
        raise ValueError(f"Para {contexto}, informe o Tipo de cliente.")

    mapa_nomes = {
        1: "Planejador de Mídia",
        2: "Cliente Direto",
        3: "Agência de Publicidade",
        4: "Bureau",
    }
    nome_tipo = mapa_nomes.get(id_tipo, "Empresa")

    if id_principal is None:
        raise ValueError(f"Para {contexto}, informe a empresa principal do tipo {nome_tipo}.")

    # Regra de negócio:
    # Agência de Publicidade, Bureau e Cliente Direto complementar são opcionais.
    # Na fase 4 eu obrigo apenas:
    # 1) Tipo de cliente
    # 2) empresa principal correspondente ao tipo selecionado
    return None


def _int_ou_none(valor: Any) -> int | None:
    """Eu converto números inteiros opcionais."""
    if valor in (None, ""):
        return None
    try:
        return int(str(valor).strip())
    except Exception:
        return None


def _bigint_ou_none(valor: Any) -> int | None:
    """Eu converto bigint opcional."""
    if valor in (None, ""):
        return None
    texto = str(valor).strip().replace(".", "").replace(",", "")
    if not texto:
        return None
    try:
        return int(texto)
    except Exception:
        return None


def _decimal_ou_none(valor: Any) -> Decimal | None:
    """Eu converto decimal opcional aceitando vírgula ou ponto."""
    if valor in (None, ""):
        return None
    texto = str(valor).strip().replace(",", ".")
    if not texto:
        return None
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _bit_ou_none(valor: Any) -> int | None:
    """Eu converto valores lógicos para bit do SQL Server."""
    if valor in (None, ""):
        return None

    if isinstance(valor, bool):
        return 1 if valor else 0

    texto = str(valor).strip().lower()
    if texto in {"1", "true", "t", "sim", "s", "yes", "y"}:
        return 1
    if texto in {"0", "false", "f", "nao", "não", "n", "no"}:
        return 0

    return None


def _serializar_valor_json_empresa(valor: Any) -> Any:
    """Eu converto tipos do SQL Server para JSON seguro."""
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(valor, date):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def _serializar_empresa(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Eu transformo a linha da empresa em dicionário JSON."""
    if not row:
        return None

    dados = {}
    for chave, valor in dict(row).items():
        if chave == "BitCliente" and valor is not None:
            dados[chave] = bool(valor)
            continue
        dados[chave] = _serializar_valor_json_empresa(valor)

    if dados.get("CNPJ"):
        dados["CNPJ"] = _formatar_cnpj(dados["CNPJ"])

    return dados


def _cache_delete_safe(chave: str) -> None:
    """Eu removo uma chave de cache sem estourar erro no fluxo principal."""
    try:
        cache.delete(chave)
    except Exception:
        pass


def _chave_cache_empresa_cadastro_por_id(id_empresa: int) -> str:
    """Eu monto a chave de cache da consulta completa da empresa por IDEmpresa."""
    return _chave_cache_json("kanban:api:empresa_cadastro:id", int(id_empresa or 0))


def _chave_cache_empresa_cadastro_por_cnpj(cnpj_normalizado: str) -> str:
    """Eu monto a chave de cache da consulta completa da empresa por CNPJ limpo."""
    return _chave_cache_json("kanban:api:empresa_cadastro:cnpj", _normalizar_cnpj(cnpj_normalizado))


def _invalidar_cache_empresa_cadastro(id_empresa: int | None = None, cnpj_normalizado: str | None = None) -> None:
    """Eu invalido os caches do cadastro completo da empresa por ID e por CNPJ."""
    if id_empresa:
        _cache_delete_safe(_chave_cache_empresa_cadastro_por_id(int(id_empresa)))

    cnpj_limpo = _normalizar_cnpj(cnpj_normalizado)
    if cnpj_limpo:
        _cache_delete_safe(_chave_cache_empresa_cadastro_por_cnpj(cnpj_limpo))


def _listar_empresas_proprietarias_para_cadastro() -> list[dict[str, Any]]:
    """Eu listo empresas proprietárias ativas para preencher o select do modal."""
    chave = _chave_cache_json("kanban:api:empresas_proprietarias:lista")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    sql = text(f"""
        SELECT
            IDEmpresaProprietaria,
            RazaoSocial,
            CNPJ
        FROM {TABELA_EMPRESAS_PROPRIETARIAS}
        WHERE ISNULL(BitAtivo, 1) = 1
        ORDER BY RazaoSocial ASC;
    """)

    rows = db.session.execute(sql).mappings().all()
    lista = []
    for row in rows:
        item = dict(row)
        item["CNPJ"] = _formatar_cnpj(item.get("CNPJ"))
        lista.append(item)

    _cache_json_set(chave, lista, TIMEOUT_CACHE_LONGO)
    return lista


def _buscar_empresa_completa_por_id(id_empresa: int) -> dict[str, Any] | None:
    """Eu busco a empresa completa pelo IDEmpresa usando cache curto."""
    id_empresa = int(id_empresa or 0)
    if id_empresa <= 0:
        return None

    chave = _chave_cache_empresa_cadastro_por_id(id_empresa)
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, dict):
        return em_cache

    sql = text(f"""
        SELECT TOP 1
            e.IDEmpresa,
            e.IDEmpresaProprietaria,
            e.CNPJ,
            e.UF,
            e.CEP,
            e.CodigoPorte,
            e.Pais,
            e.Email,
            e.Porte,
            e.Bairro,
            e.Numero,
            e.TelefoneContato1,
            e.Municipio,
            e.Logradouro,
            e.CNAE,
            e.Complemento,
            e.RazaoSocial,
            e.NomeFantasia,
            e.CapitalSocial,
            e.TelefoneContato2,
            e.NaturezaJuridica,
            e.DescricaoCnae,
            e.DataInicioAtividades,
            e.DataSituacaoEspecial,
            e.DataOpcaoPeloSimples,
            e.DataSituacaoCadastral,
            e.DataExclusaoSimples,
            e.IdentificadorMatrizFilial,
            e.DescricaoSituacaoCadastral,
            e.DescricaoMotivoSituacaoCadastral,
            e.DescricaoIdentificadorMatrizFilial,
            e.DescricaoTipoLogradouro,
            e.DataAtualizacao,
            e.Latitude,
            e.Longitude,
            e.BitCliente,
            c.Setor,
            c.Classe
        FROM {TABELA_EMPRESAS} e
        LEFT JOIN {TABELA_CNAES} c
          ON c.cnaepadrao = e.CNAE
        WHERE e.IDEmpresa = :id_empresa;
    """)

    row = db.session.execute(sql, {"id_empresa": id_empresa}).mappings().first()
    empresa = _serializar_empresa(row)
    if empresa:
        _cache_json_set(chave, empresa, TIMEOUT_CACHE_CURTO)
        cnpj_limpo = _normalizar_cnpj(empresa.get("CNPJ"))
        if cnpj_limpo:
            _cache_json_set(_chave_cache_empresa_cadastro_por_cnpj(cnpj_limpo), empresa, TIMEOUT_CACHE_CURTO)
    return empresa





def _buscar_empresa_completa_por_cnpj_normalizado(cnpj_normalizado: str) -> dict[str, Any] | None:
    """Eu busco a empresa pelo CNPJ já limpo, comparando diretamente com a coluna."""
    cnpj_normalizado = _normalizar_cnpj(cnpj_normalizado)
    if len(cnpj_normalizado) != 14:
        return None

    chave = _chave_cache_json("kanban:api:empresa_cadastro:cnpj", cnpj_normalizado)
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, dict):
        return em_cache

    sql = text(f"""
        SELECT TOP 1
            e.IDEmpresa,
            e.IDEmpresaProprietaria,
            e.CNPJ,
            e.UF,
            e.CEP,
            e.CodigoPorte,
            e.Pais,
            e.Email,
            e.Porte,
            e.Bairro,
            e.Numero,
            e.TelefoneContato1,
            e.Municipio,
            e.Logradouro,
            e.CNAE,
            e.Complemento,
            e.RazaoSocial,
            e.NomeFantasia,
            e.CapitalSocial,
            e.TelefoneContato2,
            e.NaturezaJuridica,
            e.DescricaoCnae,
            e.DataInicioAtividades,
            e.DataSituacaoEspecial,
            e.DataOpcaoPeloSimples,
            e.DataSituacaoCadastral,
            e.DataExclusaoSimples,
            e.IdentificadorMatrizFilial,
            e.DescricaoSituacaoCadastral,
            e.DescricaoMotivoSituacaoCadastral,
            e.DescricaoIdentificadorMatrizFilial,
            e.DescricaoTipoLogradouro,
            e.DataAtualizacao,
            e.Latitude,
            e.Longitude,
            e.BitCliente,
            c.Setor,
            c.Classe
        FROM {TABELA_EMPRESAS} e
        LEFT JOIN {TABELA_CNAES} c
          ON c.cnaepadrao = e.CNAE
        WHERE e.CNPJ = :cnpj
        ORDER BY e.DataAtualizacao DESC, e.IDEmpresa DESC;
    """)

    row = db.session.execute(sql, {"cnpj": cnpj_normalizado}).mappings().first()
    empresa = _serializar_empresa(row)

    if empresa:
        _cache_json_set(chave, empresa, TIMEOUT_CACHE_CURTO)

    return empresa


def _mapear_empresa_minha_receita(dados_api: dict[str, Any], cnpj_consultado: str) -> dict[str, Any] | None:
    """Eu converto o payload da API Minha Receita para o formato da DimEmpresas."""
    if not isinstance(dados_api, dict):
        return None

    cnpj_api = _normalizar_cnpj(dados_api.get("cnpj") or cnpj_consultado)
    if len(cnpj_api) != 14:
        return None

    porte_valor = dados_api.get("porte")
    if isinstance(porte_valor, dict):
        porte_texto = porte_valor.get("descricao") or porte_valor.get("porte")
    else:
        porte_texto = porte_valor

    natureza_valor = dados_api.get("natureza_juridica")
    if isinstance(natureza_valor, dict):
        natureza_texto = natureza_valor.get("descricao") or natureza_valor.get("nome")
    else:
        natureza_texto = natureza_valor

    retorno = {
        "IDEmpresa": None,
        "IDEmpresaProprietaria": None,
        "CNPJ": _formatar_cnpj(cnpj_api),
        "UF": _texto_ou_none(dados_api.get("uf"), 3),
        "CEP": _texto_ou_none(dados_api.get("cep"), 10),
        "CodigoPorte": _int_ou_none(
            dados_api.get("codigo_porte")
            or (porte_valor.get("codigo") if isinstance(porte_valor, dict) else None)
        ),
        "Pais": _texto_ou_none(dados_api.get("pais") or "Brasil", 50),
        "Email": _texto_ou_none(dados_api.get("email"), 100),
        "Porte": _texto_ou_none(porte_texto, 200),
        "Bairro": _texto_ou_none(dados_api.get("bairro"), 100),
        "Numero": _texto_ou_none(dados_api.get("numero"), 20),
        "TelefoneContato1": _texto_ou_none(
            dados_api.get("ddd_telefone_1")
            or dados_api.get("telefone")
            or dados_api.get("telefone_1"),
            20,
        ),
        "Municipio": _texto_ou_none(dados_api.get("municipio"), 100),
        "Logradouro": _texto_ou_none(dados_api.get("logradouro"), 150),
        "CNAE": _texto_ou_none(
            dados_api.get("cnae_fiscal")
            or dados_api.get("cnae")
            or dados_api.get("atividade_principal"),
            20,
        ),
        "Complemento": _texto_ou_none(dados_api.get("complemento"), 100),
        "RazaoSocial": _texto_ou_none(
            dados_api.get("razao_social")
            or dados_api.get("nome_empresarial")
            or dados_api.get("razao"),
            150,
        ),
        "NomeFantasia": _texto_ou_none(dados_api.get("nome_fantasia"), 150),
        "CapitalSocial": _bigint_ou_none(dados_api.get("capital_social")),
        "TelefoneContato2": _texto_ou_none(
            dados_api.get("ddd_telefone_2")
            or dados_api.get("telefone_2"),
            20,
        ),
        "NaturezaJuridica": _texto_ou_none(natureza_texto, 100),
        "DescricaoCnae": _texto_ou_none(
            dados_api.get("cnae_fiscal_descricao")
            or dados_api.get("descricao_cnae")
            or dados_api.get("atividade_principal_descricao"),
            150,
        ),
        "DataInicioAtividades": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_inicio_atividade"))
        ),
        "DataSituacaoEspecial": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_situacao_especial"))
        ),
        "DataOpcaoPeloSimples": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_opcao_pelo_simples"))
        ),
        "DataSituacaoCadastral": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_situacao_cadastral"))
        ),
        "DataExclusaoSimples": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_exclusao_simples"))
        ),
        "IdentificadorMatrizFilial": _int_ou_none(
            dados_api.get("identificador_matriz_filial")
            or dados_api.get("matriz_filial")
        ),
        "DescricaoSituacaoCadastral": _texto_ou_none(
            dados_api.get("descricao_situacao_cadastral")
            or dados_api.get("situacao_cadastral"),
            20,
        ),
        "DescricaoMotivoSituacaoCadastral": _texto_ou_none(
            dados_api.get("descricao_motivo_situacao_cadastral")
            or dados_api.get("motivo_situacao_cadastral"),
            20,
        ),
        "DescricaoIdentificadorMatrizFilial": _texto_ou_none(
            dados_api.get("descricao_identificador_matriz_filial"),
            20,
        ),
        "DescricaoTipoLogradouro": _texto_ou_none(
            dados_api.get("descricao_tipo_logradouro")
            or dados_api.get("tipo_logradouro"),
            20,
        ),
        "DataAtualizacao": None,
        "Latitude": _serializar_valor_json_empresa(_decimal_ou_none(dados_api.get("latitude"))),
        "Longitude": _serializar_valor_json_empresa(_decimal_ou_none(dados_api.get("longitude"))),
        "BitCliente": False,
        "Setor": None,
        "Classe": None,
    }

    return retorno




def _buscar_empresa_na_api_minha_receita(cnpj_normalizado: str) -> dict[str, Any] | None:
    """Eu consulto a API externa somente quando não encontro o CNPJ no banco."""
    cnpj_normalizado = _normalizar_cnpj(cnpj_normalizado)
    if len(cnpj_normalizado) != 14:
        return None

    chave = _chave_cache_json("kanban:api:minha_receita:cnpj", cnpj_normalizado)
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, dict):
        return em_cache

    try:
        resposta = requests.get(
            f"{URL_API_MINHA_RECEITA.rstrip('/')}/{cnpj_normalizado}",
            timeout=(2, 4),
            headers={"Accept": "application/json"},
        )
    except Exception as erro:
        current_app.logger.warning(
            "KANBAN EMPRESA: falha ao consultar Minha Receita para CNPJ=%s erro=%s",
            cnpj_normalizado,
            erro,
        )
        return None

    if not resposta.ok:
        current_app.logger.warning(
            "KANBAN EMPRESA: Minha Receita retornou HTTP=%s para CNPJ=%s",
            resposta.status_code,
            cnpj_normalizado,
        )
        return None

    try:
        dados_api = resposta.json()
    except Exception as erro:
        current_app.logger.warning(
            "KANBAN EMPRESA: falha ao converter JSON da Minha Receita para CNPJ=%s erro=%s",
            cnpj_normalizado,
            erro,
        )
        return None

    empresa = _mapear_empresa_minha_receita(dados_api, cnpj_normalizado)
    if empresa:
        _cache_json_set(chave, empresa, TIMEOUT_CACHE_MEDIO)

    return empresa


def _invalidar_cache_empresas(id_empresa: int | None = None, cnpj_normalizado: str | None = None) -> None:
    """Eu invalido os caches principais de empresa usados no modal do card."""
    _cache_delete_safe(_chave_cache_json("kanban:api:empresas:lista"))
    _cache_delete_safe(_chave_cache_json("kanban:api:empresas_proprietarias:lista"))
    _invalidar_cache_empresa_cadastro(id_empresa=id_empresa, cnpj_normalizado=cnpj_normalizado)


@kanban_bp.route("/api/empresas-proprietarias", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_empresas_proprietarias_lista():
    _assert_login()
    return jsonify(
        {
            "ok": True,
            "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
        }
    )


@kanban_bp.route("/api/empresas/cadastro", methods=["GET", "POST"])
@login_required
@limiter.limit("120/minute")
def api_empresa_cadastro_compat():
    """Rota única para consulta por IDEmpresa ou por CNPJ, e compatibilidade com POST antigo."""
    _assert_login()

    if request.method == "POST":
        return api_empresa_cadastro_salvar()

    id_empresa = _int_ou_none(request.args.get("id_empresa")) or 0
    cnpj_normalizado = _normalizar_cnpj(request.args.get("cnpj"))

    if id_empresa > 0:
        return api_empresa_cadastro_por_id(id_empresa)

    if len(cnpj_normalizado) == 14:
        return api_empresa_cadastro_buscar_por_cnpj()

    return jsonify(
        {
            "ok": False,
            "erro": "Informe id_empresa ou cnpj para consultar o cadastro da empresa.",
        }
    ), 400


@kanban_bp.route("/api/empresas/cadastro/<int:id_empresa>", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_empresa_cadastro_por_id(id_empresa: int):
    _assert_login()

    empresa = _buscar_empresa_completa_por_id(id_empresa)
    if not empresa:
        return jsonify({"ok": False, "erro": "Empresa não encontrada."}), 404

    return jsonify(
        {
            "ok": True,
            "origem": "banco",
            "empresa": empresa,
            "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
        }
    )



@kanban_bp.route("/api/empresas/cadastro/buscar-por-cnpj", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_empresa_cadastro_buscar_por_cnpj():
    _assert_login()

    try:
        cnpj_normalizado = _normalizar_cnpj(request.args.get("cnpj"))
        if len(cnpj_normalizado) != 14:
            return jsonify(
                {
                    "ok": True,
                    "encontrado": False,
                    "origem": None,
                    "empresa": None,
                    "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
                }
            )

        empresa_banco = _buscar_empresa_completa_por_cnpj_normalizado(cnpj_normalizado)
        if empresa_banco:
            return jsonify(
                {
                    "ok": True,
                    "encontrado": True,
                    "origem": "banco",
                    "empresa": empresa_banco,
                    "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
                }
            )

        empresa_api = _buscar_empresa_na_api_minha_receita(cnpj_normalizado)
        if empresa_api:
            return jsonify(
                {
                    "ok": True,
                    "encontrado": True,
                    "origem": "minha_receita",
                    "empresa": empresa_api,
                    "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
                }
            )

        return jsonify(
            {
                "ok": True,
                "encontrado": False,
                "origem": None,
                "empresa": None,
                "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
            }
        )

    except Exception as erro:
        current_app.logger.exception(
            "KANBAN EMPRESA: erro ao consultar cadastro por CNPJ. cnpj=%s",
            request.args.get("cnpj"),
        )
        return jsonify(
            {
                "ok": False,
                "erro": f"Erro interno ao consultar empresa: {erro}",
            }
        ), 500




    
@kanban_bp.route("/api/empresas/cadastro/salvar", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_empresa_cadastro_salvar():
    _assert_login()
    id_empresa_usuario = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}

    cnpj_normalizado = _normalizar_cnpj(payload.get("CNPJ"))
    if len(cnpj_normalizado) != 14:
        return jsonify({"ok": False, "erro": "CNPJ é obrigatório e deve ter 14 dígitos."}), 400

    id_empresa_informado = _int_ou_none(payload.get("IDEmpresa")) or 0

    dados_sql = {
        "IDEmpresa": id_empresa_informado or None,
        "IDEmpresaProprietaria": _int_ou_none(payload.get("IDEmpresaProprietaria")) or id_empresa_usuario,
        "CNPJ": cnpj_normalizado,
        "UF": _texto_ou_none(payload.get("UF"), 3),
        "CEP": _texto_ou_none(payload.get("CEP"), 10),
        "CodigoPorte": _int_ou_none(payload.get("CodigoPorte")),
        "Pais": _texto_ou_none(payload.get("Pais"), 50),
        "Email": _texto_ou_none(payload.get("Email"), 100),
        "Porte": _texto_ou_none(payload.get("Porte"), 200),
        "Bairro": _texto_ou_none(payload.get("Bairro"), 100),
        "Numero": _texto_ou_none(payload.get("Numero"), 20),
        "TelefoneContato1": _texto_ou_none(payload.get("TelefoneContato1"), 20),
        "Municipio": _texto_ou_none(payload.get("Municipio"), 100),
        "Logradouro": _texto_ou_none(payload.get("Logradouro"), 150),
        "CNAE": _texto_ou_none(payload.get("CNAE"), 20),
        "Complemento": _texto_ou_none(payload.get("Complemento"), 100),
        "RazaoSocial": _texto_ou_none(payload.get("RazaoSocial"), 150),
        "NomeFantasia": _texto_ou_none(payload.get("NomeFantasia"), 150),
        "CapitalSocial": _bigint_ou_none(payload.get("CapitalSocial")),
        "TelefoneContato2": _texto_ou_none(payload.get("TelefoneContato2"), 20),
        "NaturezaJuridica": _texto_ou_none(payload.get("NaturezaJuridica"), 100),
        "DescricaoCnae": _texto_ou_none(payload.get("DescricaoCnae"), 150),
        "DataInicioAtividades": _para_data_sql_ou_none(payload.get("DataInicioAtividades")),
        "DataSituacaoEspecial": _para_data_sql_ou_none(payload.get("DataSituacaoEspecial")),
        "DataOpcaoPeloSimples": _para_data_sql_ou_none(payload.get("DataOpcaoPeloSimples")),
        "DataSituacaoCadastral": _para_data_sql_ou_none(payload.get("DataSituacaoCadastral")),
        "DataExclusaoSimples": _para_data_sql_ou_none(payload.get("DataExclusaoSimples")),
        "IdentificadorMatrizFilial": _int_ou_none(payload.get("IdentificadorMatrizFilial")),
        "DescricaoSituacaoCadastral": _texto_ou_none(payload.get("DescricaoSituacaoCadastral"), 20),
        "DescricaoMotivoSituacaoCadastral": _texto_ou_none(payload.get("DescricaoMotivoSituacaoCadastral"), 20),
        "DescricaoIdentificadorMatrizFilial": _texto_ou_none(payload.get("DescricaoIdentificadorMatrizFilial"), 20),
        "DescricaoTipoLogradouro": _texto_ou_none(payload.get("DescricaoTipoLogradouro"), 20),
        "Latitude": _decimal_ou_none(payload.get("Latitude")),
        "Longitude": _decimal_ou_none(payload.get("Longitude")),
        "BitCliente": _bit_ou_none(payload.get("BitCliente")),
    }

    sql_lock_cnpj = text(f"""
        SELECT TOP 1 IDEmpresa
        FROM {TABELA_EMPRESAS} WITH (UPDLOCK, HOLDLOCK)
        WHERE CNPJ = :cnpj;
    """)

    sql_lock_id = text(f"""
        SELECT TOP 1 IDEmpresa
        FROM {TABELA_EMPRESAS} WITH (UPDLOCK, HOLDLOCK)
        WHERE IDEmpresa = :id_empresa;
    """)

    try:
        row_existente_cnpj = db.session.execute(
            sql_lock_cnpj,
            {"cnpj": cnpj_normalizado},
        ).mappings().first()

        row_existente_id = None
        if not row_existente_cnpj and id_empresa_informado:
            row_existente_id = db.session.execute(
                sql_lock_id,
                {"id_empresa": id_empresa_informado},
            ).mappings().first()

        id_empresa_alvo = 0
        if row_existente_cnpj:
            id_empresa_alvo = int(row_existente_cnpj.get("IDEmpresa") or 0)
        elif row_existente_id:
            id_empresa_alvo = int(row_existente_id.get("IDEmpresa") or 0)

        if id_empresa_alvo > 0:
            dados_sql["IDEmpresa"] = id_empresa_alvo

            sql_update = text(f"""
                UPDATE {TABELA_EMPRESAS}
                   SET IDEmpresaProprietaria = :IDEmpresaProprietaria,
                       CNPJ = :CNPJ,
                       UF = :UF,
                       CEP = :CEP,
                       CodigoPorte = :CodigoPorte,
                       Pais = :Pais,
                       Email = :Email,
                       Porte = :Porte,
                       Bairro = :Bairro,
                       Numero = :Numero,
                       TelefoneContato1 = :TelefoneContato1,
                       Municipio = :Municipio,
                       Logradouro = :Logradouro,
                       CNAE = :CNAE,
                       Complemento = :Complemento,
                       RazaoSocial = :RazaoSocial,
                       NomeFantasia = :NomeFantasia,
                       CapitalSocial = :CapitalSocial,
                       TelefoneContato2 = :TelefoneContato2,
                       NaturezaJuridica = :NaturezaJuridica,
                       DescricaoCnae = :DescricaoCnae,
                       DataInicioAtividades = :DataInicioAtividades,
                       DataSituacaoEspecial = :DataSituacaoEspecial,
                       DataOpcaoPeloSimples = :DataOpcaoPeloSimples,
                       DataSituacaoCadastral = :DataSituacaoCadastral,
                       DataExclusaoSimples = :DataExclusaoSimples,
                       IdentificadorMatrizFilial = :IdentificadorMatrizFilial,
                       DescricaoSituacaoCadastral = :DescricaoSituacaoCadastral,
                       DescricaoMotivoSituacaoCadastral = :DescricaoMotivoSituacaoCadastral,
                       DescricaoIdentificadorMatrizFilial = :DescricaoIdentificadorMatrizFilial,
                       DescricaoTipoLogradouro = :DescricaoTipoLogradouro,
                       Latitude = :Latitude,
                       Longitude = :Longitude,
                       BitCliente = :BitCliente,
                       DataAtualizacao = GETDATE()
                 WHERE IDEmpresa = :IDEmpresa;
            """)

            db.session.execute(sql_update, dados_sql)

        else:
            sql_insert = text(f"""
                INSERT INTO {TABELA_EMPRESAS} (
                    IDEmpresaProprietaria,
                    CNPJ,
                    UF,
                    CEP,
                    CodigoPorte,
                    Pais,
                    Email,
                    Porte,
                    Bairro,
                    Numero,
                    TelefoneContato1,
                    Municipio,
                    Logradouro,
                    CNAE,
                    Complemento,
                    RazaoSocial,
                    NomeFantasia,
                    CapitalSocial,
                    TelefoneContato2,
                    NaturezaJuridica,
                    DescricaoCnae,
                    DataInicioAtividades,
                    DataSituacaoEspecial,
                    DataOpcaoPeloSimples,
                    DataSituacaoCadastral,
                    DataExclusaoSimples,
                    IdentificadorMatrizFilial,
                    DescricaoSituacaoCadastral,
                    DescricaoMotivoSituacaoCadastral,
                    DescricaoIdentificadorMatrizFilial,
                    DescricaoTipoLogradouro,
                    DataAtualizacao,
                    Latitude,
                    Longitude,
                    BitCliente
                )
                OUTPUT INSERTED.IDEmpresa
                VALUES (
                    :IDEmpresaProprietaria,
                    :CNPJ,
                    :UF,
                    :CEP,
                    :CodigoPorte,
                    :Pais,
                    :Email,
                    :Porte,
                    :Bairro,
                    :Numero,
                    :TelefoneContato1,
                    :Municipio,
                    :Logradouro,
                    :CNAE,
                    :Complemento,
                    :RazaoSocial,
                    :NomeFantasia,
                    :CapitalSocial,
                    :TelefoneContato2,
                    :NaturezaJuridica,
                    :DescricaoCnae,
                    :DataInicioAtividades,
                    :DataSituacaoEspecial,
                    :DataOpcaoPeloSimples,
                    :DataSituacaoCadastral,
                    :DataExclusaoSimples,
                    :IdentificadorMatrizFilial,
                    :DescricaoSituacaoCadastral,
                    :DescricaoMotivoSituacaoCadastral,
                    :DescricaoIdentificadorMatrizFilial,
                    :DescricaoTipoLogradouro,
                    GETDATE(),
                    :Latitude,
                    :Longitude,
                    :BitCliente
                );
            """)

            novo_id = db.session.execute(sql_insert, dados_sql).scalar()
            id_empresa_alvo = int(novo_id or 0)

            if id_empresa_alvo <= 0:
                raise RuntimeError("O INSERT da empresa não retornou um IDEmpresa válido.")

        db.session.commit()

    except Exception as erro:
        db.session.rollback()
        current_app.logger.exception("KANBAN EMPRESA: erro ao salvar cadastro de empresa.")
        return jsonify({"ok": False, "erro": f"Erro ao salvar empresa: {erro}"}), 500

    _invalidar_cache_empresas(id_empresa=id_empresa_alvo, cnpj_normalizado=cnpj_normalizado)

    empresa_salva = _buscar_empresa_completa_por_id(id_empresa_alvo)
    if not empresa_salva:
        return jsonify({"ok": False, "erro": "Empresa salva, mas não foi possível reler o cadastro."}), 500

    return jsonify(
        {
            "ok": True,
            "empresa": empresa_salva,
            "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
        }
    )










@kanban_bp.route("/api/ocupacao/calendario", methods=["GET"])
@login_required
def api_kanban_ocupacao_calendario():
    cod_face = (request.args.get("cod_face") or request.args.get("codface") or "").strip().upper()
    mes_ref = (request.args.get("mes_ref") or "").strip()
    meses = request.args.get("meses", 24)

    try:
        meses = int(meses)
    except Exception:
        meses = 24

    if meses <= 0:
        meses = 24

    if not cod_face:
        return jsonify({"ok": False, "erro": "cod_face obrigatório"}), 400

    sql = text("""
        DECLARE @CodFace varchar(20) = UPPER(LTRIM(RTRIM(:cod_face)));

        DECLARE @Inicio date =
        CASE
            WHEN :mes_ref IS NOT NULL AND LEN(:mes_ref) = 7
            THEN TRY_CONVERT(date, CONCAT(:mes_ref, '-01'))
            ELSE DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)
        END;

        IF @Inicio IS NULL
            SET @Inicio = DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1);

        DECLARE @Fim date = DATEADD(MONTH, :meses, @Inicio);

        DECLARE @CodPonto int =
        (
            SELECT TOP (1) fo.CodPonto
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
            WHERE fo.CodFace = @CodFace
              AND fo.CodPonto IS NOT NULL
            ORDER BY fo.DataAtualizacao DESC
        );

        ;WITH
        Painel AS (
            SELECT TOP (1)
                p.CodPonto,
                TipoPainel = p.Tipo,
                QuantidadeFaces = NULLIF(p.QuantidadeFaces, 0),
                BitAtivo = COALESCE(p.BitAtivo, 1)
            FROM [Integracao].[Silver].[DimPaineisEuromidia] p
            WHERE p.CodPonto = @CodPonto
        ),
        Capacidade AS (
            SELECT
                CodPonto = (SELECT CodPonto FROM Painel),
                TipoPainel = COALESCE((SELECT TipoPainel FROM Painel), 'DESCONHECIDO'),
                BitAtivo = COALESCE((SELECT BitAtivo FROM Painel), 0),

                EhDigital =
                    CASE
                        WHEN COALESCE((SELECT TipoPainel FROM Painel), '') LIKE '%DIGITAL%' THEN 1
                        ELSE 0
                    END,

                CapacidadeSlots =
                    CASE
                        WHEN COALESCE((SELECT TipoPainel FROM Painel), '') LIKE '%DIGITAL%'
                        THEN COALESCE((SELECT QuantidadeFaces FROM Painel), 16)
                        ELSE 1
                    END
        ),
        OcupacoesBase AS (
            SELECT
                fo.CodFace,
                DataInicio = fo.DataInicio,
                DataFim = fo.DataFim,
                SpanQtd = fo.SpanQtd,
                Cota = fo.Cota,
                NumeroContrato = fo.NumeroContrato,
                NumeroPrevia = fo.NumeroPrevia,
                DataAtualizacao = fo.DataAtualizacao
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
            WHERE UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = @CodFace
              AND fo.DataInicio IS NOT NULL
              AND fo.DataFim IS NOT NULL
              AND fo.CanceladoEm IS NULL
              AND fo.Status IN ('ATIVO', 'RESERVADO')
        ),
        OcupacoesDedup AS (
            SELECT *
            FROM (
                SELECT
                    b.*,
                    rn = ROW_NUMBER() OVER (
                        PARTITION BY
                            b.CodFace,
                            b.DataInicio,
                            b.DataFim,
                            ISNULL(b.NumeroContrato, ''),
                            ISNULL(b.NumeroPrevia, '')
                        ORDER BY b.DataAtualizacao DESC
                    )
                FROM OcupacoesBase b
            ) x
            WHERE x.rn = 1
        ),
        OcupacoesValidas AS (
            SELECT
                d.DataInicio,
                d.DataFim,
                SlotsConsumidos =
                    CASE
                        WHEN (SELECT EhDigital FROM Capacidade) = 1
                        THEN COALESCE(NULLIF(d.SpanQtd, 0), 1)
                        ELSE 1
                    END
            FROM OcupacoesDedup d
        ),
        UsoPorDia AS (
            SELECT
                c.[Data],
                SlotsOcupados =
                    CASE
                        WHEN cap.EhDigital = 1
                        THEN COALESCE(SUM(o.SlotsConsumidos), 0)
                        ELSE CASE WHEN COUNT(o.SlotsConsumidos) > 0 THEN 1 ELSE 0 END
                    END
            FROM [Integracao].[Silver].[DimCalendario] c
            CROSS JOIN Capacidade cap
            LEFT JOIN OcupacoesValidas o
                   ON c.[Data] >= o.DataInicio
                  AND c.[Data] <= o.DataFim
            WHERE c.[Data] >= @Inicio
              AND c.[Data] < @Fim
            GROUP BY c.[Data], cap.EhDigital
        )
        SELECT
            Data = CONVERT(varchar(10), c.[Data], 23),
            CodPonto = cap.CodPonto,
            TipoPainel = cap.TipoPainel,
            EhDigital = cap.EhDigital,
            CapacidadeSlots = cap.CapacidadeSlots,
            SlotsOcupados = u.SlotsOcupados,

            SlotsDisponiveis =
                CASE
                    WHEN cap.CapacidadeSlots - u.SlotsOcupados < 0 THEN 0
                    ELSE cap.CapacidadeSlots - u.SlotsOcupados
                END,

            OcupacaoPct =
                CASE
                    WHEN cap.CapacidadeSlots > 0
                    THEN CAST(u.SlotsOcupados * 100.0 / cap.CapacidadeSlots AS decimal(9,2))
                    ELSE NULL
                END,

            DiaDisponivel =
                CASE
                    WHEN cap.BitAtivo = 0 THEN 0
                    WHEN (cap.CapacidadeSlots - u.SlotsOcupados) > 0 THEN 1
                    ELSE 0
                END,

            StatusDia =
                CASE
                    WHEN cap.BitAtivo = 0 THEN 'INDISPONIVEL'
                    WHEN cap.EhDigital = 0 AND u.SlotsOcupados = 0 THEN 'DISPONIVEL'
                    WHEN cap.EhDigital = 0 AND u.SlotsOcupados = 1 THEN 'OCUPADO'
                    WHEN cap.EhDigital = 1 AND u.SlotsOcupados = 0 THEN 'LIVRE'
                    WHEN cap.EhDigital = 1 AND u.SlotsOcupados < cap.CapacidadeSlots THEN 'PARCIAL'
                    ELSE 'LOTADO'
                END
        FROM [Integracao].[Silver].[DimCalendario] c
        JOIN UsoPorDia u
          ON u.[Data] = c.[Data]
        CROSS JOIN Capacidade cap
        WHERE c.[Data] >= @Inicio
          AND c.[Data] < @Fim
        ORDER BY c.[Data];
    """)

    try:
        rows = db.session.execute(
            sql,
            {
                "cod_face": cod_face,
                "mes_ref": mes_ref,
                "meses": meses,
            },
        ).all()
    except Exception as erro:
        current_app.logger.exception(
            "KANBAN OCUPACAO: erro ao consultar calendário da face %s.",
            cod_face,
        )
        return jsonify(
            {
                "ok": False,
                "erro": f"Não foi possível consultar a ocupação da face {cod_face}: {erro}",
            }
        ), 500

    calendario = {}
    for r in rows:
        chave = (r.Data or "").strip()
        if not chave:
            continue

        calendario[chave] = {
            "status": (r.StatusDia or "").strip(),
            "disp": int(r.SlotsDisponiveis or 0),
            "cap": int(r.CapacidadeSlots or 0),
            "ocup": int(r.SlotsOcupados or 0),
            "pct": float(r.OcupacaoPct) if r.OcupacaoPct is not None else None,
            "dia_disponivel": int(r.DiaDisponivel or 0),
            "eh_digital": int(r.EhDigital or 0),
            "codponto": int(r.CodPonto) if r.CodPonto is not None else None,
            "tipo": (r.TipoPainel or "").strip(),
        }

    return jsonify({"ok": True, "cal": calendario})








def _sql_select_tipo_cliente_desconto_card(alias_card: str = "c") -> str:
    colunas: list[str] = []

    for nome_coluna in ("BitClienteDireto", "BitAgencia", "BitPlanejador"):
        if _coluna_existe(TABELA_CARD, nome_coluna):
            colunas.append(f"{alias_card}.{nome_coluna} AS {nome_coluna}")
        else:
            colunas.append(f"CAST(0 AS bit) AS {nome_coluna}")

    return ",\n            ".join(colunas)



def _obter_tipos_cliente_desconto(*, incluir_inativos: bool = False) -> list[dict[str, Any]]:
    chave = _chave_cache_json(
        "kanban:dominio:tipo_cliente_desconto",
        ID_EMPRESA_PROPRIETARIA_CONTRATOS,
        incluir_inativos,
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    sql_where = """
        WHERE IDEmpresaProprietaria = :id_empresa
    """
    if not incluir_inativos:
        sql_where += """
          AND ISNULL(BitAtivo, 1) = 1
        """

    sql = text(f"""
        SELECT
            IDDimTipoCliente AS IDDimKanbanTipoClienteDesconto,
            NomeTipoCliente AS TipoCliente,
            IDEmpresaProprietaria,
            ISNULL(BitAtivo, 1) AS BitAtivo
        FROM {TABELA_TIPO_CLIENTE_DESCONTO}
        {sql_where}
        ORDER BY NomeTipoCliente ASC, IDDimTipoCliente ASC;
    """)

    rows = db.session.execute(
        sql,
        {"id_empresa": int(ID_EMPRESA_PROPRIETARIA_CONTRATOS)},
    ).mappings().all()

    resultado: list[dict[str, Any]] = []
    for row in rows:
        try:
            id_tipo = int(row.get("IDDimKanbanTipoClienteDesconto") or 0)
        except Exception:
            id_tipo = 0

        if not id_tipo:
            continue

        nome_tipo = str(row.get("TipoCliente") or "").strip()
        if not nome_tipo:
            continue

        if id_tipo not in {2, 3, 4}:
            continue

        resultado.append(
            {
                "IDDimKanbanTipoClienteDesconto": id_tipo,
                "TipoCliente": nome_tipo,
                "IDEmpresaProprietaria": int(row.get("IDEmpresaProprietaria") or 0),
                "BitAtivo": int(row.get("BitAtivo") or 0),
            }
        )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_LONGO)
    return resultado



def _sql_select_id_origem_atendimento_card(alias_card: str = "c") -> str:
    if _coluna_existe(TABELA_CARD, "IDDimOrigemAtendimento"):
        return f"{alias_card}.IDDimOrigemAtendimento AS IDDimOrigemAtendimento,"
    return "CAST(NULL AS int) AS IDDimOrigemAtendimento,"


def _obter_origens_atendimento(*, incluir_inativos: bool = False) -> list[dict[str, Any]]:
    chave = _chave_cache_json(
        "kanban:dominio:origem_atendimento",
        ID_EMPRESA_PROPRIETARIA_CONTRATOS,
        incluir_inativos,
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    sql_where = """
        WHERE IDEmpresaProprietaria = :id_empresa
    """
    if not incluir_inativos:
        sql_where += """
          AND ISNULL(BitAtivo, 1) = 1
        """

    sql = text(f"""
        SELECT
            IDDimOrigemAtendimento,
            NomeOrigemAtendimento,
            IDEmpresaProprietaria,
            ISNULL(BitAtivo, 1) AS BitAtivo
        FROM {TABELA_ORIGEM_ATENDIMENTO}
        {sql_where}
        ORDER BY NomeOrigemAtendimento ASC, IDDimOrigemAtendimento ASC;
    """)

    rows = db.session.execute(
        sql,
        {"id_empresa": int(ID_EMPRESA_PROPRIETARIA_CONTRATOS)},
    ).mappings().all()

    resultado: list[dict[str, Any]] = []
    for row in rows:
        try:
            id_origem = int(row.get("IDDimOrigemAtendimento") or 0)
        except Exception:
            id_origem = 0

        if not id_origem:
            continue

        nome_origem = str(row.get("NomeOrigemAtendimento") or "").strip()
        if not nome_origem:
            continue

        resultado.append(
            {
                "IDDimOrigemAtendimento": id_origem,
                "NomeOrigemAtendimento": nome_origem,
                "IDEmpresaProprietaria": int(row.get("IDEmpresaProprietaria") or 0),
                "BitAtivo": int(row.get("BitAtivo") or 0),
            }
        )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_LONGO)
    return resultado


def _resolver_id_origem_atendimento_do_card(card: Mapping[str, Any] | dict[str, Any] | None) -> int | None:
    if not card:
        return None

    for nome_campo in (
        "IDDimOrigemAtendimento",
        "IDOrigemAtendimento",
    ):
        try:
            valor = int(card.get(nome_campo) or 0)
            if valor > 0:
                return valor
        except Exception:
            pass

    return None


def _obter_id_origem_atendimento_atual_do_card(id_card: int) -> int | None:
    if not _coluna_existe(TABELA_CARD, "IDDimOrigemAtendimento"):
        return None

    sql = text(f"""
        SELECT TOP (1)
            IDDimOrigemAtendimento
        FROM {TABELA_CARD}
        WHERE IDFatoKanbanCard = :id_card;
    """)

    valor = db.session.execute(sql, {"id_card": int(id_card)}).scalar()
    try:
        valor_int = int(valor or 0)
    except Exception:
        valor_int = 0

    return valor_int or None


def _aplicar_origem_atendimento_no_card_dict(card_dict: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card_dict, dict):
        return card_dict

    id_empresa_relacionada = (
        card_dict.get("IDEmpresaRelacionadaCard")
        or card_dict.get("IDEmpresa")
        or card_dict.get("IDEmpresaRelacionada")
    )

    relacionamento = _obter_relacionamento_empresa_proprietaria(
        id_empresa=_int_ou_none(id_empresa_relacionada),
        id_empresa_proprietaria=ID_EMPRESA_PROPRIETARIA_CONTRATOS,
    )

    id_origem = None
    if relacionamento:
        id_origem = _int_ou_none(relacionamento.get("IDDimOrigemAtendimento"))

    if id_origem is None:
        id_origem = _resolver_id_origem_atendimento_do_card(card_dict)

    mapa_origens = {
        int(item.get("IDDimOrigemAtendimento") or 0): str(item.get("NomeOrigemAtendimento") or "").strip()
        for item in _obter_origens_atendimento(incluir_inativos=True)
        if int(item.get("IDDimOrigemAtendimento") or 0) > 0
    }

    nome_origem = mapa_origens.get(id_origem, "") if id_origem else ""
    card_dict["IDDimOrigemAtendimento"] = id_origem
    card_dict["OrigemAtendimento"] = nome_origem
    card_dict["NomeOrigemAtendimento"] = nome_origem

    return card_dict





def _resolver_id_tipo_cliente_desconto_por_bits(card: Mapping[str, Any] | dict[str, Any] | None) -> int | None:
    if not card:
        return None

    for nome_campo in (
        "IDDimTipoCliente",
        "IDDimKanbanTipoClienteDesconto",
        "IDTipoClienteDesconto",
    ):
        try:
            valor = int(card.get(nome_campo) or 0)
            if valor > 0:
                return valor
        except Exception:
            pass

    def _bit(nome_campo: str) -> int:
        try:
            return int(card.get(nome_campo) or 0)
        except Exception:
            return 0

    if _bit("BitPlanejador") == 1:
        return 1
    if _bit("BitClienteDireto") == 1:
        return 2
    if _bit("BitAgencia") == 1:
        return 3

    try:
        if int(card.get("IDEmpresaBureau") or 0) > 0 and int(card.get("IDDimTipoCliente") or card.get("IDDimKanbanTipoClienteDesconto") or 0) == 4:
            return 4
    except Exception:
        pass

    return None




def _montar_bits_tipo_cliente_desconto(id_tipo_cliente_desconto: Any) -> dict[str, int]:
    try:
        id_tipo = int(id_tipo_cliente_desconto) if id_tipo_cliente_desconto not in (None, "", 0) else None
    except Exception:
        id_tipo = None

    return {
        "BitPlanejador": 1 if id_tipo == 1 else 0,
        "BitClienteDireto": 1 if id_tipo == 2 else 0,
        "BitAgencia": 1 if id_tipo == 3 else 0,
    }


def _nome_coluna_tipo_cliente_card() -> str | None:
    for nome_coluna in ("IDDimTipoCliente", "IDDimKanbanTipoClienteDesconto"):
        if _coluna_existe(TABELA_CARD, nome_coluna):
            return nome_coluna
    return None


def _obter_cnae_por_id(id_dim_cnaes: Any) -> dict[str, Any] | None:
    try:
        id_cnae = int(id_dim_cnaes or 0)
    except Exception:
        id_cnae = 0

    if id_cnae <= 0:
        return None

    sql = text(f"""
        SELECT TOP (1)
            IDDimCnaes,
            cnaepadrao,
            Descricao,
            Classe,
            Setor,
            MacroSetor,
            SubClasse
        FROM {TABELA_CNAES}
        WHERE IDDimCnaes = :id_dim_cnaes;
    """)

    row = db.session.execute(sql, {"id_dim_cnaes": int(id_cnae)}).mappings().first()
    return dict(row) if row else None


def _sincronizar_tag_plano_midia_por_fase(
    *,
    id_card: int,
    id_fase_atual: int,
    id_usuario: int,
    id_empresa_proprietaria: int,
) -> dict[str, Any]:
    resultado = {
        "id_tag": int(ID_TAG_PLANO_MIDIA),
        "fase": int(id_fase_atual or 0),
        "adicionada": False,
        "removida": False,
        "deve_ter_tag": int(id_fase_atual or 0) in FASES_COM_TAG_PLANO_MIDIA,
    }

    if resultado["deve_ter_tag"]:
        resultado["adicionada"] = _aplicar_tag_no_card(
            id_card=int(id_card),
            id_tag=int(ID_TAG_PLANO_MIDIA),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
        )
    else:
        resultado["removida"] = _remover_tag_do_card(
            id_card=int(id_card),
            id_tag=int(ID_TAG_PLANO_MIDIA),
            id_usuario=int(id_usuario),
        )

    return resultado



def _aplicar_tipo_cliente_desconto_no_card_dict(card_dict: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card_dict, dict):
        return card_dict

    id_empresa_relacionada = (
        card_dict.get("IDEmpresaRelacionadaCard")
        or card_dict.get("IDEmpresa")
        or card_dict.get("IDEmpresaRelacionada")
    )

    relacionamento = _obter_relacionamento_empresa_proprietaria(
        id_empresa=_int_ou_none(id_empresa_relacionada),
        id_empresa_proprietaria=ID_EMPRESA_PROPRIETARIA_CONTRATOS,
    )

    id_tipo = None
    if relacionamento:
        id_tipo = _int_ou_none(relacionamento.get("IDDimTipoCliente"))

    if id_tipo is None:
        id_tipo = _resolver_id_tipo_cliente_desconto_por_bits(card_dict)

    mapa_tipos = {
        int(item.get("IDDimKanbanTipoClienteDesconto") or 0): str(item.get("TipoCliente") or "").strip()
        for item in _obter_tipos_cliente_desconto(incluir_inativos=True)
        if int(item.get("IDDimKanbanTipoClienteDesconto") or 0) > 0
    }

    bits_tipo = _montar_bits_tipo_cliente_desconto(id_tipo)
    card_dict.update(bits_tipo)
    card_dict["IDDimKanbanTipoClienteDesconto"] = id_tipo
    card_dict["IDDimTipoCliente"] = id_tipo
    card_dict["TipoClienteDesconto"] = mapa_tipos.get(id_tipo, "") if id_tipo else ""
    card_dict["PossuiRelacionamentoEmpresaProprietaria"] = bool(relacionamento)
    card_dict["DimRelacionamentoEmpresa"] = (
        int(relacionamento.get("DimRelacionamentoEmpresa"))
        if relacionamento and relacionamento.get("DimRelacionamentoEmpresa") not in (None, "", 0)
        else None
    )

    return card_dict







@kanban_bp.route("/api/kanbans/<int:id_kanban>/cards", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_criar(id_kanban: int):
    etapa = "inicio"
    novo_id = None

    try:
        etapa = "validar_login"
        id_usuario = _assert_login()
        id_emp = _id_empresa_usuario_or_403()
        _obter_kanban_autorizado(id_kanban)

        etapa = "ler_payload"
        payload = request.get_json(silent=True) or {}

        titulo = (payload.get("titulo") or "").strip()
        descricao = payload.get("descricao")
        id_fase = int(payload.get("id_fase") or 0)
        id_empresa_relacionada = payload.get("id_empresa")
        id_tipo_cliente_desconto = payload.get("id_tipo_cliente_desconto") if "id_tipo_cliente_desconto" in payload else None
        tipo_cliente_desconto_informado = "id_tipo_cliente_desconto" in payload
        id_origem_atendimento = payload.get("id_origem_atendimento") if "id_origem_atendimento" in payload else None
        origem_atendimento_informada = "id_origem_atendimento" in payload

        id_contrato_existente = payload.get("id_contrato_existente")
        tipo_contrato_card = payload.get("tipo_contrato_card")
        cod_ponto_contrato = payload.get("cod_ponto_contrato")
        cod_face_contrato = payload.get("cod_face_contrato")

        # Variáveis efetivas usadas no fluxo de criação.
        # No update elas podem receber fallback do card já salvo; na criação ainda não existe card,
        # então elas devem começar exatamente com o que veio do payload.
        cod_ponto_contrato_payload = cod_ponto_contrato
        cod_face_contrato_payload = cod_face_contrato

        id_empresa_agencia = payload.get("id_empresa_agencia")
        id_empresa_bureau = payload.get("id_empresa_bureau")
        id_empresa_cliente_direto = payload.get("id_empresa_cliente_direto")
        marca_card = payload.get("marca")
        telefone_card = payload.get("telefone")
        email_card = payload.get("email")
        id_dim_cnaes = payload.get("id_dim_cnaes") if "id_dim_cnaes" in payload else None
        nome_empresa_card = payload.get("nome_empresa") if "nome_empresa" in payload else None
        painel_faces_payload = payload.get("painel_faces")
        solicitacao_contrato_payload = payload.get("solicitacao_contrato") if isinstance(payload.get("solicitacao_contrato"), dict) else None

        if len(titulo) < 2:
            return jsonify({"ok": False, "msg": "Título inválido"}), 400

        if not id_fase:
            return jsonify({"ok": False, "msg": "Fase obrigatória"}), 400

        if not _validar_fase_do_kanban(id_kanban, id_fase):
            return jsonify({"ok": False, "msg": "Fase inválida para este kanban"}), 400

        etapa = "validar_empresa_relacionada"
        id_empresa_relacionada_int = None
        if id_empresa_relacionada not in (None, ""):
            try:
                id_empresa_relacionada_int = int(id_empresa_relacionada)
            except Exception:
                return jsonify({"ok": False, "msg": "Empresa inválida"}), 400

            sql_emp = text(f"""
                SELECT 1
                FROM {TABELA_EMPRESAS}
                WHERE IDEmpresa = :id_empresa;
            """)
            empresa_existe = db.session.execute(
                sql_emp,
                {"id_empresa": id_empresa_relacionada_int},
            ).scalar()

            if not empresa_existe:
                return jsonify({"ok": False, "msg": "Empresa não encontrada"}), 400

        etapa = "resolver_contexto_tipo_contrato"
        contexto_tipo_contrato = _resolver_contexto_tipo_contrato_payload(
            id_empresa=id_empresa_relacionada_int,
            id_contrato_existente=id_contrato_existente,
            tipo_contrato_card=tipo_contrato_card,
        )

        contrato_existente = _validar_contrato_empresa(
            id_empresa=contexto_tipo_contrato["id_empresa"],
            id_contrato_existente=contexto_tipo_contrato["id_contrato_existente"],
        )

        validacao_ponto_face = _validar_ponto_face_contrato(
            id_contrato_existente=contexto_tipo_contrato["id_contrato_existente"],
            cod_ponto=cod_ponto_contrato_payload,
            cod_face=cod_face_contrato_payload,
        )

        campos_complementares_novo_contrato = _resolver_campos_complementares_novo_contrato(
            usar_novo_contrato=str(contexto_tipo_contrato.get("tipo_contrato") or "").upper() == TIPO_SOLICITACAO_NOVO,
            id_empresa_agencia=id_empresa_agencia,
            marca=marca_card,
            telefone=telefone_card,
            email=email_card,
        )

        etapa = "validar_tipo_cliente"
        id_tipo_cliente_desconto_int = None
        if tipo_cliente_desconto_informado and id_tipo_cliente_desconto not in (None, "", 0):
            try:
                id_tipo_cliente_desconto_int = int(id_tipo_cliente_desconto)
            except Exception:
                return jsonify({"ok": False, "msg": "Tipo de cliente inválido"}), 400

            tipos_validos = {
                int(item.get("IDDimKanbanTipoClienteDesconto") or 0)
                for item in _obter_tipos_cliente_desconto()
            }

            if id_tipo_cliente_desconto_int not in tipos_validos:
                return jsonify({"ok": False, "msg": "Tipo de cliente inválido"}), 400

        etapa = "resolver_empresas_relacionadas"
        empresas_relacionadas_card = _resolver_ids_empresas_card_por_tipo_cliente(
            id_tipo_cliente=id_tipo_cliente_desconto_int,
            id_empresa_principal=id_empresa_relacionada_int,
            id_empresa_agencia=id_empresa_agencia,
            id_empresa_bureau=id_empresa_bureau,
            id_empresa_cliente_direto=id_empresa_cliente_direto,
        )

        if int(id_fase or 0) == 4:
            _validar_preenchimento_empresas_fase_4(
                id_tipo_cliente=id_tipo_cliente_desconto_int,
                id_empresa_principal=empresas_relacionadas_card.get("id_empresa_principal"),
                id_empresa_agencia=empresas_relacionadas_card.get("id_empresa_agencia_card"),
                id_empresa_bureau=empresas_relacionadas_card.get("id_empresa_bureau_card"),
                id_empresa_cliente_direto=empresas_relacionadas_card.get("id_empresa_cliente_direto"),
                contexto="criar o card já na fase 4",
            )

        id_origem_atendimento_int = None
        if origem_atendimento_informada and id_origem_atendimento not in (None, "", 0):
            try:
                id_origem_atendimento_int = int(id_origem_atendimento)
            except Exception:
                return jsonify({"ok": False, "msg": "Origem de atendimento inválida"}), 400

            origens_validas = {
                int(item.get("IDDimOrigemAtendimento") or 0)
                for item in _obter_origens_atendimento()
            }

            if id_origem_atendimento_int not in origens_validas:
                return jsonify({"ok": False, "msg": "Origem de atendimento inválida"}), 400

        etapa = "validar_segmento"
        id_dim_cnaes_int = None
        if id_dim_cnaes not in (None, "", 0):
            try:
                id_dim_cnaes_int = int(id_dim_cnaes)
            except Exception:
                return jsonify({"ok": False, "msg": "Segmento inválido"}), 400

            if not _obter_cnae_por_id(id_dim_cnaes_int):
                return jsonify({"ok": False, "msg": "Segmento inválido"}), 400

        etapa = "resolver_nome_empresa"
        nome_empresa_card_txt = str(nome_empresa_card or "").strip() or None
        id_empresa_nome_base = empresas_relacionadas_card.get("id_empresa_card") or id_empresa_relacionada_int
        if id_empresa_nome_base not in (None, "", 0) and not nome_empresa_card_txt:
            sql_nome_empresa = text(f"""
                SELECT TOP (1) RazaoSocial
                FROM {TABELA_EMPRESAS}
                WHERE IDEmpresa = :id_empresa;
            """)
            nome_empresa_card_txt = db.session.execute(
                sql_nome_empresa,
                {"id_empresa": int(id_empresa_nome_base)},
            ).scalar()

        etapa = "resolver_relacionamento_empresa"
        relacionamento_empresa_atual = None
        id_tipo_cliente_relacionamento_final = None
        id_origem_atendimento_relacionamento_final = None

        id_empresa_relacionamento = empresas_relacionadas_card.get("id_empresa_card") or id_empresa_relacionada_int
        if id_empresa_relacionamento not in (None, "", 0):
            relacionamento_empresa_atual = _obter_relacionamento_empresa_proprietaria(
                id_empresa=int(id_empresa_relacionamento),
                id_empresa_proprietaria=int(ID_EMPRESA_PROPRIETARIA_CONTRATOS),
            )

            if tipo_cliente_desconto_informado:
                id_tipo_cliente_relacionamento_final = id_tipo_cliente_desconto_int
            else:
                id_tipo_cliente_relacionamento_final = (
                    int(relacionamento_empresa_atual.get("IDDimTipoCliente"))
                    if relacionamento_empresa_atual
                    and relacionamento_empresa_atual.get("IDDimTipoCliente") not in (None, "", 0)
                    else None
                )

            if origem_atendimento_informada:
                id_origem_atendimento_relacionamento_final = id_origem_atendimento_int
            else:
                id_origem_atendimento_relacionamento_final = (
                    int(relacionamento_empresa_atual.get("IDDimOrigemAtendimento"))
                    if relacionamento_empresa_atual
                    and relacionamento_empresa_atual.get("IDDimOrigemAtendimento") not in (None, "", 0)
                    else None
                )

        mapa_bits_tipo_cliente = _montar_bits_tipo_cliente_desconto(id_tipo_cliente_desconto_int)
        nome_coluna_empresa = _nome_coluna_empresa_relacionada_card()
        coluna_id_dim_usuarios_existe = _coluna_existe(TABELA_CARD, "IDDimUsuarios")
        coluna_iddimkanbanorigem_existe = _coluna_existe(TABELA_CARD, "IDDimKanbanOrigem")

        status_card_inicial = _obter_status_card_para_fase(id_fase)
        id_status_card_inicial = _obter_id_status_card_por_codigo(status_card_inicial)

        etapa = "montar_insert_card"
        colunas = [
            "IDDimKanban",
            "IDDimKanbanFaseAtual",
            "Titulo",
            "Descricao",
            "IDVendedorUsuario",
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
            ":id_usuario",
            ":status_card_inicial",
            "GETDATE()",
            "1",
            ":id_emp",
        ]
        params = {
            "id_kanban": id_kanban,
            "id_fase": id_fase,
            "titulo": titulo[:200],
            "descricao": descricao,
            "id_usuario": int(id_usuario),
            "id_emp": id_emp,
            "status_card_inicial": status_card_inicial,
        }

        if nome_coluna_empresa:
            colunas.append(nome_coluna_empresa)
            valores.append(":id_empresa_relacionada")
            params["id_empresa_relacionada"] = empresas_relacionadas_card.get("id_empresa_card")

        if _coluna_existe(TABELA_CARD, "BitClienteDireto"):
            colunas.append("BitClienteDireto")
            valores.append(":bit_cliente_direto")
            params["bit_cliente_direto"] = int(mapa_bits_tipo_cliente["BitClienteDireto"])

        if _coluna_existe(TABELA_CARD, "BitAgencia"):
            colunas.append("BitAgencia")
            valores.append(":bit_agencia")
            params["bit_agencia"] = int(mapa_bits_tipo_cliente["BitAgencia"])

        if _coluna_existe(TABELA_CARD, "BitPlanejador"):
            colunas.append("BitPlanejador")
            valores.append(":bit_planejador")
            params["bit_planejador"] = int(mapa_bits_tipo_cliente["BitPlanejador"])

        if _coluna_existe(TABELA_CARD, "BitAditivo"):
            colunas.append("BitAditivo")
            valores.append(":bit_aditivo")
            params["bit_aditivo"] = int(contexto_tipo_contrato["bit_aditivo"])

        if _coluna_existe(TABELA_CARD, "BitContratoNovo"):
            colunas.append("BitContratoNovo")
            valores.append(":bit_contrato_novo")
            params["bit_contrato_novo"] = int(contexto_tipo_contrato["bit_contrato_novo"])

        if _coluna_existe(TABELA_CARD, "IDFatoControleContratosEuromidia"):
            colunas.append("IDFatoControleContratosEuromidia")
            valores.append(":id_contrato_vinculado")
            params["id_contrato_vinculado"] = contexto_tipo_contrato["id_contrato_existente"]

        elif _coluna_existe(TABELA_CARD, "IDFatoControleContratoEuromidia"):
            colunas.append("IDFatoControleContratoEuromidia")
            valores.append(":id_contrato_vinculado")
            params["id_contrato_vinculado"] = contexto_tipo_contrato["id_contrato_existente"]

        if _coluna_existe(TABELA_CARD, "CodPontoContrato"):
            colunas.append("CodPontoContrato")
            valores.append(":cod_ponto_contrato")
            params["cod_ponto_contrato"] = validacao_ponto_face.get("cod_ponto")

        if _coluna_existe(TABELA_CARD, "CodFaceContrato"):
            colunas.append("CodFaceContrato")
            valores.append(":cod_face_contrato")
            params["cod_face_contrato"] = validacao_ponto_face.get("cod_face")

        if _coluna_existe(TABELA_CARD, "IDEmpresaAgencia"):
            colunas.append("IDEmpresaAgencia")
            valores.append(":id_empresa_agencia")
            params["id_empresa_agencia"] = empresas_relacionadas_card.get("id_empresa_agencia_card")

        if _coluna_existe(TABELA_CARD, "IDEmpresaBureau"):
            colunas.append("IDEmpresaBureau")
            valores.append(":id_empresa_bureau")
            params["id_empresa_bureau"] = empresas_relacionadas_card.get("id_empresa_bureau_card")

        if _coluna_existe(TABELA_CARD, "Marca"):
            colunas.append("Marca")
            valores.append(":marca")
            params["marca"] = campos_complementares_novo_contrato.get("marca")

        if _coluna_existe(TABELA_CARD, "Telefone"):
            colunas.append("Telefone")
            valores.append(":telefone")
            params["telefone"] = campos_complementares_novo_contrato.get("telefone")

        if _coluna_existe(TABELA_CARD, "Email"):
            colunas.append("Email")
            valores.append(":email")
            params["email"] = campos_complementares_novo_contrato.get("email")

        if coluna_id_dim_usuarios_existe:
            colunas.append("IDDimUsuarios")
            valores.append(":id_usuario")

        if _coluna_existe(TABELA_CARD, "IDDimKanbanTipoClienteDesconto"):
            colunas.append("IDDimKanbanTipoClienteDesconto")
            valores.append(":id_tipo_cliente_desconto")
            params["id_tipo_cliente_desconto"] = id_tipo_cliente_desconto_int

        if _coluna_existe(TABELA_CARD, "IDDimTipoCliente"):
            colunas.append("IDDimTipoCliente")
            valores.append(":id_tipo_cliente_card")
            params["id_tipo_cliente_card"] = id_tipo_cliente_desconto_int

        if _coluna_existe(TABELA_CARD, "IDDimCnaes"):
            colunas.append("IDDimCnaes")
            valores.append(":id_dim_cnaes")
            params["id_dim_cnaes"] = id_dim_cnaes_int

        if _coluna_existe(TABELA_CARD, "NomeEmpresa"):
            colunas.append("NomeEmpresa")
            valores.append(":nome_empresa")
            params["nome_empresa"] = nome_empresa_card_txt

        if _coluna_existe(TABELA_CARD, "IDDimOrigemAtendimento"):
            colunas.append("IDDimOrigemAtendimento")
            valores.append(":id_origem_atendimento")
            params["id_origem_atendimento"] = id_origem_atendimento_int

        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_card_inicial is not None:
            colunas.append("IDDimKanbanStatusCard")
            valores.append(":id_status_card_inicial")
            params["id_status_card_inicial"] = int(id_status_card_inicial)

        if coluna_iddimkanbanorigem_existe:
            colunas.append("IDDimKanbanOrigem")
            valores.append("NULL")

        sql_insert = f"""
            INSERT INTO {TABELA_CARD}
                ({', '.join(colunas)})
            OUTPUT INSERTED.IDFatoKanbanCard
            VALUES
                ({', '.join(valores)});
        """

        etapa = "executar_insert_card"
        novo_id = db.session.execute(text(sql_insert), params).scalar()
        if not novo_id:
            raise RuntimeError("O INSERT não retornou IDFatoKanbanCard.")

        etapa = "sincronizar_tipo_contrato"
        aplicar_tags_automaticas_tipo_contrato = not (
            int(id_fase) == 1
            and _normalizar_tipo_contrato_card(contexto_tipo_contrato["tipo_contrato"]) == TIPO_SOLICITACAO_NOVO
        )

        sincronizacao_contato_contrato = None

        sincronizacao_tipo = _sincronizar_tipo_contrato_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            id_fase_atual=int(id_fase),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
            tipo_contrato=str(contexto_tipo_contrato["tipo_contrato"]),
            id_contrato_existente=contexto_tipo_contrato["id_contrato_existente"],
            cod_ponto_contrato=validacao_ponto_face.get("cod_ponto"),
            cod_face_contrato=validacao_ponto_face.get("cod_face"),
            aplicar_tags=aplicar_tags_automaticas_tipo_contrato,
        )

        sincronizacao_tag_plano_midia = _sincronizar_tag_plano_midia_por_fase(
            id_card=int(novo_id),
            id_fase_atual=int(id_fase),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
        )

        etapa = "salvar_paineis_vinculados"
        vinculos_preparados: list[dict[str, object]] = []
        sincronizacao_reservas = {"criadas": 0, "canceladas": 0, "mantidas": 0}

        if isinstance(painel_faces_payload, list):
            vinculos_preparados = _preparar_vinculos_painel_faces(
                painel_faces_payload,
                int(id_emp),
            )

            for ordem_rel, vinculo in enumerate(vinculos_preparados, start=1):
                db.session.execute(
                    text(
                        f"""
                        INSERT INTO {TABELA_CARD_PAINEL_FACE} (
                            IDFatoKanbanCard,
                            Ordem,
                            IDDimPaineisEuromidia,
                            IDDimFacesPaineis,
                            CodPonto,
                            CodFace,
                            TipoPainel,
                            AnoCusto,
                            CustoTabela,
                            IDDimTabelaPrecosEuromidia,
                            PeriodoExibicao,
                            ExibicoesDia,
                            ValorTabela,
                            Tabela,
                            PoliticaTrocas,
                            ValorTroca,
                            NovoValor,
                            PercentualDesconto,
                            ValorVendaFinal,
                            MargemValor,
                            MargemPercentual,
                            DataInicio,
                            DataFim,
                            Ativo
                        )
                        VALUES (
                            :id_card,
                            :ordem,
                            :id_painel,
                            :id_face,
                            :cod_ponto,
                            :cod_face,
                            :tipo_painel,
                            :ano_custo,
                            :custo_tabela,
                            :id_tabela_preco,
                            :periodo_exibicao,
                            :exibicoes_dia,
                            :valor_tabela,
                            :tabela,
                            :politica_trocas,
                            :valor_troca,
                            :novo_valor,
                            :percentual_desconto,
                            :valor_venda_final,
                            :margem_valor,
                            :margem_percentual,
                            :data_inicio,
                            :data_fim,
                            1
                        );
                        """
                    ),
                    {
                        "id_card": int(novo_id),
                        "ordem": int(ordem_rel),
                        "id_painel": int(vinculo.get("IDDimPaineisEuromidia") or vinculo.get("id_painel") or 0) or None,
                        "id_face": int(vinculo.get("IDDimFacesPaineis") or vinculo.get("id_face") or 0) or None,
                        "cod_ponto": vinculo.get("CodPonto") or vinculo.get("cod_ponto"),
                        "cod_face": vinculo.get("CodFace") or vinculo.get("cod_face"),
                        "tipo_painel": vinculo.get("TipoPainel") or vinculo.get("tipo_painel"),
                        "ano_custo": vinculo.get("AnoCusto") or vinculo.get("ano_custo"),
                        "custo_tabela": vinculo.get("CustoTabela") or vinculo.get("custo_tabela"),
                        "id_tabela_preco": vinculo.get("IDDimTabelaPrecosEuromidia") or vinculo.get("id_tabela_preco") or vinculo.get("id_preco"),
                        "periodo_exibicao": vinculo.get("PeriodoExibicao") or vinculo.get("periodo_exibicao"),
                        "exibicoes_dia": vinculo.get("ExibicoesDia") or vinculo.get("exibicoes_dia"),
                        "valor_tabela": vinculo.get("ValorTabela") or vinculo.get("valor_tabela"),
                        "tabela": vinculo.get("Tabela") or vinculo.get("tabela"),
                        "politica_trocas": vinculo.get("PoliticaTrocas") or vinculo.get("politica_trocas"),
                        "valor_troca": vinculo.get("ValorTroca") or vinculo.get("valor_troca"),
                        "novo_valor": vinculo.get("NovoValor") or vinculo.get("novo_valor"),
                        "percentual_desconto": vinculo.get("PercentualDesconto") or vinculo.get("percentual_desconto"),
                        "valor_venda_final": vinculo.get("ValorVendaFinal") or vinculo.get("valor_venda_final"),
                        "margem_valor": vinculo.get("MargemValor") or vinculo.get("margem_valor"),
                        "margem_percentual": vinculo.get("MargemPercentual") or vinculo.get("margem_percentual"),
                        "data_inicio": _para_data_sql_ou_none(vinculo.get("DataInicio") or vinculo.get("data_inicio")),
                        "data_fim": _para_data_sql_ou_none(vinculo.get("DataFim") or vinculo.get("data_fim")),
                    },
                )

        etapa = "sincronizar_reservas"
        if int(id_fase or 0) == 4:
            sincronizacao_reservas = _sincronizar_reservas_painel_faces_kanban(
                id_card=int(novo_id),
                titulo_card=titulo,
                id_empresa_relacionada=empresas_relacionadas_card.get("id_empresa_card") or id_empresa_relacionada_int,
                id_usuario=int(id_usuario),
                id_empresa_proprietaria=int(id_emp),
                painel_faces_payload=painel_faces_payload if isinstance(painel_faces_payload, list) else None,
                vinculos_preparados=vinculos_preparados,
            )

        etapa = "sincronizar_snapshot_solicitacao"
        snapshot_solicitacao = _sincronizar_snapshot_solicitacao_contrato_do_card(
            id_card=int(novo_id),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
            id_empresa_relacionada=id_empresa_relacionada_int,
            tipo_contrato=str(contexto_tipo_contrato["tipo_contrato"]),
            id_contrato_existente=contexto_tipo_contrato["id_contrato_existente"],
            cod_ponto_contrato=validacao_ponto_face.get("cod_ponto"),
            cod_face_contrato=validacao_ponto_face.get("cod_face"),
            descricao_card=descricao,
            contrato_existente=contrato_existente,
            id_tipo_cliente=id_tipo_cliente_desconto_int,
        )

        etapa = "sincronizar_item_contrato"
        sincronizacao_item_contrato = {
            "sincronizado": False,
            "quantidade_itens_controle_contrato_atualizados": 0,
            "motivo_itens_controle_contrato": None,
        }

        if contexto_tipo_contrato["id_contrato_existente"] not in (None, "", 0):
            cod_ponto_salvo = str(validacao_ponto_face.get("cod_ponto") or "").strip()
            cod_face_salva = str(validacao_ponto_face.get("cod_face") or "").strip().upper()

            if not cod_ponto_salvo:
                sincronizacao_item_contrato["motivo_itens_controle_contrato"] = "card_sem_cod_ponto_contrato"
            elif not cod_face_salva:
                sincronizacao_item_contrato["motivo_itens_controle_contrato"] = "card_sem_cod_face_contrato"
            else:
                quantidade_itens_controle_contrato_atualizados = _atualizar_card_nos_itens_contrato_euromidia(
                    id_empresa=int(id_empresa_relacionada_int) if id_empresa_relacionada_int not in (None, "", 0) else None,
                    id_contrato=int(contexto_tipo_contrato["id_contrato_existente"]),
                    cod_ponto=cod_ponto_salvo,
                    cod_face=cod_face_salva,
                    id_card=int(novo_id),
                )

                sincronizacao_item_contrato["sincronizado"] = True
                sincronizacao_item_contrato["quantidade_itens_controle_contrato_atualizados"] = int(
                    quantidade_itens_controle_contrato_atualizados or 0
                )

                if int(quantidade_itens_controle_contrato_atualizados or 0) <= 0:
                    raise RuntimeError(
                        "Nenhum item de FatoControleContratosItensEuromidia foi atualizado com o "
                        "IDFatoKanbanCard ao salvar o card recém-criado. "
                        "Verifique contrato + CodPonto + CodFace gravados no card."
                    )

        etapa = "sincronizar_relacionamento_empresa_tipo_cliente"
        relacionamento_empresa_tipo_cliente = None
        if id_empresa_relacionada_int not in (None, "", 0):
            relacionamento_empresa_tipo_cliente = _garantir_relacionamento_empresa_tipo_cliente(
                id_empresa=int(id_empresa_relacionada_int),
                id_empresa_proprietaria=int(ID_EMPRESA_PROPRIETARIA_CONTRATOS),
                id_dim_tipo_cliente=id_tipo_cliente_relacionamento_final,
                id_dim_origem_atendimento=id_origem_atendimento_relacionamento_final,
            )

        etapa = "registrar_negociacao_preco"
        observacoes_proposta_negociacao = None
        if isinstance(solicitacao_contrato_payload, dict):
            observacoes_proposta_negociacao = (
                solicitacao_contrato_payload.get("ObservacoesProposta")
                or solicitacao_contrato_payload.get("observacoes_proposta")
                or solicitacao_contrato_payload.get("observacoes")
            )

        _registrar_negociacao_preco_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            id_fase_atual=int(id_fase) if id_fase else None,
            status_card=status_card_inicial,
            id_empresa_relacionada=empresas_relacionadas_card.get("id_empresa_card") or id_empresa_relacionada_int,
            vinculos_preparados=vinculos_preparados,
            observacoes_proposta=observacoes_proposta_negociacao,
        )

        etapa = "sincronizar_tag_aprovacao_diretoria"
        estados_atuais_aprovacao_desconto = _listar_estado_atual_negociacao_card(int(novo_id))
        _sincronizar_tag_aprovacao_diretoria_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            estados_atuais=estados_atuais_aprovacao_desconto,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
        )

        etapa = "garantir_tag_em_atendimento"
        _garantir_tag_em_atendimento_no_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=id_emp,
            falhar_se_nao_existir=True,
        )

        etapa = "registrar_logs"
        snapshot_depois = _obter_snapshot_card_log(int(novo_id), incluir_inativo=True)

        observacao_log = (
            "Card criado via quadro Kanban"
            f" | tipo_contrato={sincronizacao_tipo.get('tipo_contrato')}"
            f" | id_contrato_existente={contexto_tipo_contrato.get('id_contrato_existente') or 'NULL'}"
            f" | cod_ponto_contrato={validacao_ponto_face.get('cod_ponto') or 'NULL'}"
            f" | cod_face_contrato={validacao_ponto_face.get('cod_face') or 'NULL'}"
            f" | id_tipo_cliente={id_tipo_cliente_relacionamento_final if id_tipo_cliente_relacionamento_final not in (None, '', 0) else 'NULL'}"
            f" | id_origem_atendimento={id_origem_atendimento_relacionamento_final if id_origem_atendimento_relacionamento_final not in (None, '', 0) else (id_origem_atendimento_int if id_origem_atendimento_int not in (None, '', 0) else 'NULL')}"
        )

        _registrar_log_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            id_empresa_proprietaria=int(id_emp),
            id_usuario_acao=int(id_usuario),
            tipo_evento="CARD_CRIADO",
            subtipo_evento="CRIACAO",
            id_fase_de=None,
            id_fase_para=int(id_fase) if id_fase else None,
            observacao=observacao_log[:2000],
            payload_antes=None,
            payload_depois=snapshot_depois,
        )

        _registrar_status_historico_card(
            id_card=int(novo_id),
            id_fase=int(id_fase),
            id_status_card=id_status_card_inicial,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=id_emp,
        )

        etapa = "commit"
        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=int(novo_id))
        detalhe = _obter_card_detalhe_payload(int(novo_id))

        _emitir_evento_kanban(
            id_kanban,
            "card_criado",
            {
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
                "tipo_contrato": sincronizacao_tipo,
                "contrato_existente": contrato_existente,
                "snapshot_solicitacao": snapshot_solicitacao,
                "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
                "sincronizacao_item_contrato": sincronizacao_item_contrato,
                "sincronizacao_reservas": sincronizacao_reservas,
            },
        )

        return jsonify(
            {
                "ok": True,
                "msg": (
                    f"Card criado com sucesso. {int(sincronizacao_reservas.get('criadas') or 0)} reserva(s) criada(s)."
                    if int(sincronizacao_reservas.get('criadas') or 0) > 0
                    else "Card criado com sucesso."
                ),
                "id": int(novo_id),
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
                "tipo_contrato": sincronizacao_tipo,
                "contrato_existente": contrato_existente,
                "snapshot_solicitacao": snapshot_solicitacao,
                "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
                "sincronizacao_item_contrato": sincronizacao_item_contrato,
                "sincronizacao_reservas": sincronizacao_reservas,
                "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
            }
        ), 201

    except ValueError as exc:
        db.session.rollback()
        current_app.logger.exception("Erro de validação ao criar card. etapa=%s", etapa)
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao criar card no kanban %s. etapa=%s", id_kanban, etapa)
        return jsonify({"ok": False, "msg": f"Erro ao criar card: {str(exc)}"}), 500






@kanban_bp.route("/api/cards/<int:id_card>", methods=["PUT"])
@login_required
@limiter.limit("120/minute")
def api_card_atualizar(id_card: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    def _valor_vinculo(vinculo: dict, *chaves: str):
        """
        Eu busco o primeiro campo preenchido no dicionário do vínculo.

        Motivo:
        - alguns fluxos usam chave em português/minúsculo;
        - outros usam o nome real da coluna;
        - não posso usar apenas "or", porque valor 0 também pode ser válido em alguns campos.
        """
        for chave in chaves:
            valor = vinculo.get(chave)
            if valor is None:
                continue
            if isinstance(valor, str) and not valor.strip():
                continue
            return valor
        return None

    try:
        card_atual = _obter_card_autorizado(id_card)
        if not card_atual:
            return jsonify({"ok": False, "msg": "Card não encontrado."}), 404

        id_kanban = int(card_atual.get("IDDimKanban") or 0)
        id_fase_atual = int(card_atual.get("IDDimKanbanFaseAtual") or 0)

        payload = request.get_json(silent=True) or {}

        titulo = (payload.get("titulo") or "").strip()
        descricao = payload.get("descricao")
        id_empresa_relacionada = payload.get("id_empresa")
        id_tipo_cliente_desconto = payload.get("id_tipo_cliente_desconto") if "id_tipo_cliente_desconto" in payload else None
        tipo_cliente_desconto_informado = "id_tipo_cliente_desconto" in payload
        id_origem_atendimento = payload.get("id_origem_atendimento") if "id_origem_atendimento" in payload else None
        origem_atendimento_informada = "id_origem_atendimento" in payload
        versao_concorrencia = payload.get("versao_concorrencia")
        painel_faces_payload = payload.get("painel_faces")

        id_contrato_existente = payload.get("id_contrato_existente")
        tipo_contrato_card = payload.get("tipo_contrato_card")
        cod_ponto_contrato = payload.get("cod_ponto_contrato")
        cod_face_contrato = payload.get("cod_face_contrato")
        id_empresa_agencia = payload.get("id_empresa_agencia")
        id_empresa_bureau = payload.get("id_empresa_bureau")
        id_empresa_cliente_direto = payload.get("id_empresa_cliente_direto")
        marca_card = payload.get("marca")
        telefone_card = payload.get("telefone")
        email_card = payload.get("email")
        solicitacao_contrato_payload = payload.get("solicitacao_contrato") if isinstance(payload.get("solicitacao_contrato"), dict) else None
        id_dim_cnaes = payload.get("id_dim_cnaes") if "id_dim_cnaes" in payload else None
        segmento_informado = "id_dim_cnaes" in payload
        nome_empresa_card = payload.get("nome_empresa") if "nome_empresa" in payload else None

        if not titulo:
            return jsonify({"ok": False, "msg": "Título do card é obrigatório."}), 400

        has_versao = _card_tem_versao_concorrencia()
        versao_concorrencia_bytes = None

        if has_versao:
            versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia)
            if not versao_concorrencia_bytes:
                detalhe_atual = _obter_card_detalhe_payload(id_card)
                return jsonify(
                    {
                        "ok": False,
                        "msg": "Versão de concorrência inválida ou ausente.",
                        "card_atual": detalhe_atual.get("card"),
                    }
                ), 409

        sql_campos_card_atual = text(f"""
            SELECT TOP (1)
                IDDimTipoCliente,
                IDDimOrigemAtendimento,
                IDDimCnaes,
                IDEmpresa
            FROM {TABELA_CARD}
            WHERE IDFatoKanbanCard = :id_card;
        """)
        campos_card_atual = db.session.execute(
            sql_campos_card_atual,
            {"id_card": int(id_card)},
        ).mappings().first() or {}

        id_tipo_cliente_atual_card = int(campos_card_atual.get("IDDimTipoCliente") or 0) or None
        id_origem_atendimento_atual_card = int(campos_card_atual.get("IDDimOrigemAtendimento") or 0) or None
        id_dim_cnaes_atual_card = int(campos_card_atual.get("IDDimCnaes") or 0) or None
        id_empresa_atual_card = int(campos_card_atual.get("IDEmpresa") or 0) or None

        id_empresa_relacionada_int = int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None

        id_tipo_cliente_desconto_int = None
        if tipo_cliente_desconto_informado:
            if id_tipo_cliente_desconto not in (None, "", 0):
                try:
                    id_tipo_cliente_desconto_int = int(id_tipo_cliente_desconto)
                except Exception:
                    return jsonify({"ok": False, "msg": "Tipo de cliente inválido."}), 400

                tipos_validos = {
                    int(item.get("IDDimKanbanTipoClienteDesconto") or item.get("IDDimTipoCliente") or 0)
                    for item in _obter_tipos_cliente_desconto()
                }

                if id_tipo_cliente_desconto_int not in tipos_validos:
                    return jsonify({"ok": False, "msg": "Tipo de cliente inválido."}), 400
            else:
                id_tipo_cliente_desconto_int = None

        id_tipo_cliente_final_card = (
            id_tipo_cliente_desconto_int
            if tipo_cliente_desconto_informado
            else id_tipo_cliente_atual_card
        )

        if id_tipo_cliente_final_card in (None, 0):
            return jsonify({"ok": False, "msg": "Tipo de cliente é obrigatório."}), 400

        id_origem_atendimento_int = None
        if origem_atendimento_informada:
            if id_origem_atendimento not in (None, "", 0):
                try:
                    id_origem_atendimento_int = int(id_origem_atendimento)
                except Exception:
                    return jsonify({"ok": False, "msg": "Origem de atendimento inválida."}), 400
            else:
                id_origem_atendimento_int = None

        id_origem_atendimento_final_card = (
            id_origem_atendimento_int
            if origem_atendimento_informada
            else id_origem_atendimento_atual_card
        )

        id_dim_cnaes_int = None
        if segmento_informado:
            if id_dim_cnaes not in (None, "", 0):
                try:
                    id_dim_cnaes_int = int(id_dim_cnaes)
                except Exception:
                    return jsonify({"ok": False, "msg": "Segmento inválido."}), 400

                if not _obter_cnae_por_id(id_dim_cnaes_int):
                    return jsonify({"ok": False, "msg": "Segmento inválido."}), 400
            else:
                id_dim_cnaes_int = None

        id_dim_cnaes_final_card = (
            id_dim_cnaes_int
            if segmento_informado
            else id_dim_cnaes_atual_card
        )

        id_empresa_principal_final = id_empresa_relacionada_int or id_empresa_atual_card

        empresas_relacionadas_card = _resolver_ids_empresas_card_por_tipo_cliente(
            id_tipo_cliente=id_tipo_cliente_final_card,
            id_empresa_principal=id_empresa_principal_final,
            id_empresa_agencia=id_empresa_agencia,
            id_empresa_bureau=id_empresa_bureau,
            id_empresa_cliente_direto=id_empresa_cliente_direto,
        )

        if int(id_fase_atual or 0) == 4:
            _validar_preenchimento_empresas_fase_4(
                id_tipo_cliente=id_tipo_cliente_final_card,
                id_empresa_principal=empresas_relacionadas_card.get("id_empresa_principal"),
                id_empresa_agencia=empresas_relacionadas_card.get("id_empresa_agencia_card"),
                id_empresa_bureau=empresas_relacionadas_card.get("id_empresa_bureau_card"),
                id_empresa_cliente_direto=empresas_relacionadas_card.get("id_empresa_cliente_direto"),
                contexto="salvar o card na fase 4",
            )

        nome_empresa_card_txt = str(nome_empresa_card or "").strip() or None
        id_empresa_nome_base = empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final

        if id_empresa_nome_base not in (None, "", 0) and not nome_empresa_card_txt:
            sql_nome_empresa = text(f"""
                SELECT TOP (1) RazaoSocial
                FROM {TABELA_EMPRESAS}
                WHERE IDEmpresa = :id_empresa;
            """)
            nome_empresa_card_txt = db.session.execute(
                sql_nome_empresa,
                {"id_empresa": int(id_empresa_nome_base)},
            ).scalar()

        relacionamento_empresa_atual = None
        id_tipo_cliente_relacionamento_final = None
        id_origem_atendimento_relacionamento_final = None

        id_empresa_relacionamento = empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final
        if id_empresa_relacionamento not in (None, "", 0):
            relacionamento_empresa_atual = _obter_relacionamento_empresa_proprietaria(
                id_empresa=int(id_empresa_relacionamento),
                id_empresa_proprietaria=int(ID_EMPRESA_PROPRIETARIA_CONTRATOS),
            )

            id_tipo_cliente_relacionamento_final = id_tipo_cliente_final_card
            id_origem_atendimento_relacionamento_final = id_origem_atendimento_final_card

        id_contrato_existente_payload = id_contrato_existente
        tipo_contrato_card_payload = tipo_contrato_card
        cod_ponto_contrato_payload = cod_ponto_contrato
        cod_face_contrato_payload = cod_face_contrato

        # Proteção contra o segundo salvamento perder Aditivo:
        # se o front vier sem contrato, mas o card já estava vinculado a um
        # contrato existente, eu preservo o contexto atual do banco.
        # Isso também cobre o caso em que o combobox visual cai para "Novo Contrato"
        # e manda tipo_contrato_card=NOVO_CONTRATO indevidamente.
        payload_veio_sem_contrato = id_contrato_existente_payload in (None, "", 0)
        id_contrato_atual_card = _int_ou_none(card_atual.get("IDFatoControleContratosEuromidia"))
        bit_aditivo_atual_card = bool(_int_ou_none(card_atual.get("BitAditivo")) or 0)

        if payload_veio_sem_contrato and id_contrato_atual_card:
            id_contrato_existente_payload = id_contrato_atual_card

        if id_contrato_atual_card and bit_aditivo_atual_card and payload_veio_sem_contrato:
            tipo_contrato_card_payload = TIPO_SOLICITACAO_ADITIVO
        elif not str(tipo_contrato_card_payload or "").strip() and id_contrato_atual_card:
            tipo_contrato_card_payload = TIPO_SOLICITACAO_ADITIVO if bit_aditivo_atual_card else tipo_contrato_card_payload

        if cod_ponto_contrato_payload in (None, "") and str(card_atual.get("CodPontoContrato") or "").strip():
            cod_ponto_contrato_payload = card_atual.get("CodPontoContrato")

        if cod_face_contrato_payload in (None, "") and str(card_atual.get("CodFaceContrato") or "").strip():
            cod_face_contrato_payload = card_atual.get("CodFaceContrato")

        contexto_tipo_contrato = _resolver_contexto_tipo_contrato_payload(
            id_empresa=empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final,
            id_contrato_existente=id_contrato_existente_payload,
            tipo_contrato_card=tipo_contrato_card_payload,
        )

        id_contrato_existente_final = (
            int(contexto_tipo_contrato.get("id_contrato_existente"))
            if contexto_tipo_contrato.get("id_contrato_existente") not in (None, "", 0)
            else None
        )

        contrato_existente = _validar_contrato_empresa(
            id_empresa=contexto_tipo_contrato["id_empresa"],
            id_contrato_existente=contexto_tipo_contrato["id_contrato_existente"],
        )

        validacao_ponto_face = _validar_ponto_face_contrato(
            id_contrato_existente=contexto_tipo_contrato["id_contrato_existente"],
            cod_ponto=cod_ponto_contrato,
            cod_face=cod_face_contrato,
        )

        campos_complementares_novo_contrato = _resolver_campos_complementares_novo_contrato(
            usar_novo_contrato=str(contexto_tipo_contrato.get("tipo_contrato") or "").upper() == TIPO_SOLICITACAO_NOVO,
            id_empresa_agencia=id_empresa_agencia,
            marca=marca_card,
            telefone=telefone_card,
            email=email_card,
        )

        snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        campos_update: list[str] = []
        parametros_update: dict[str, object] = {
            "id_card": int(id_card),
            "titulo": titulo[:300],
            "descricao": descricao,
        }

        if _coluna_existe(TABELA_CARD, "Titulo"):
            campos_update.append("Titulo = :titulo")

        if _coluna_existe(TABELA_CARD, "Descricao"):
            campos_update.append("Descricao = :descricao")

        nome_coluna_empresa = _nome_coluna_empresa_relacionada_card()
        if nome_coluna_empresa:
            campos_update.append(f"{nome_coluna_empresa} = :id_empresa_relacionada")
            parametros_update["id_empresa_relacionada"] = empresas_relacionadas_card.get("id_empresa_card")

        if _coluna_existe(TABELA_CARD, "BitAditivo"):
            campos_update.append("BitAditivo = :bit_aditivo_contrato")
            parametros_update["bit_aditivo_contrato"] = int(contexto_tipo_contrato["bit_aditivo"])

        if _coluna_existe(TABELA_CARD, "BitContratoNovo"):
            campos_update.append("BitContratoNovo = :bit_contrato_novo")
            parametros_update["bit_contrato_novo"] = int(contexto_tipo_contrato["bit_contrato_novo"])

        if _coluna_existe(TABELA_CARD, "IDDimTipoCliente"):
            campos_update.append("IDDimTipoCliente = :id_tipo_cliente_card")
            parametros_update["id_tipo_cliente_card"] = id_tipo_cliente_final_card

        if _coluna_existe(TABELA_CARD, "IDDimTipoClienteDesconto"):
            campos_update.append("IDDimTipoClienteDesconto = :id_tipo_cliente_desconto")
            parametros_update["id_tipo_cliente_desconto"] = id_tipo_cliente_final_card

        if _coluna_existe(TABELA_CARD, "IDDimOrigemAtendimento"):
            campos_update.append("IDDimOrigemAtendimento = :id_origem_atendimento")
            parametros_update["id_origem_atendimento"] = id_origem_atendimento_final_card

        if _coluna_existe(TABELA_CARD, "IDDimCnaes"):
            campos_update.append("IDDimCnaes = :id_dim_cnaes")
            parametros_update["id_dim_cnaes"] = id_dim_cnaes_final_card

        if _coluna_existe(TABELA_CARD, "NomeEmpresa"):
            campos_update.append("NomeEmpresa = :nome_empresa_card")
            parametros_update["nome_empresa_card"] = nome_empresa_card_txt

        if _coluna_existe(TABELA_CARD, "Marca"):
            campos_update.append("Marca = :marca_card")
            parametros_update["marca_card"] = campos_complementares_novo_contrato.get("marca")

        if _coluna_existe(TABELA_CARD, "Telefone"):
            campos_update.append("Telefone = :telefone_card")
            parametros_update["telefone_card"] = campos_complementares_novo_contrato.get("telefone")

        if _coluna_existe(TABELA_CARD, "Email"):
            campos_update.append("Email = :email_card")
            parametros_update["email_card"] = campos_complementares_novo_contrato.get("email")

        empresas_relacionadas_sql = _montar_campos_empresas_relacionadas_card_sql(
            empresas_relacionadas_card,
            id_tipo_cliente=id_tipo_cliente_final_card,
        )
        campos_update.extend(empresas_relacionadas_sql["campos"])
        parametros_update.update(empresas_relacionadas_sql["parametros"])

        _anexar_campos_vinculo_contrato_card(
            campos_sql=campos_update,
            parametros=parametros_update,
            id_contrato_existente=(
                contexto_tipo_contrato["id_contrato_existente"]
                if str(contexto_tipo_contrato.get("tipo_contrato") or "").upper() == TIPO_SOLICITACAO_ADITIVO
                else None
            ),
            cod_ponto_contrato=(
                validacao_ponto_face.get("cod_ponto")
                if str(contexto_tipo_contrato.get("tipo_contrato") or "").upper() == TIPO_SOLICITACAO_ADITIVO
                else None
            ),
            cod_face_contrato=(
                validacao_ponto_face.get("cod_face")
                if str(contexto_tipo_contrato.get("tipo_contrato") or "").upper() == TIPO_SOLICITACAO_ADITIVO
                else None
            ),
        )

        if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
            campos_update.append("AtualizadoEm = GETDATE()")

        if not campos_update:
            return jsonify({"ok": False, "msg": "Nenhum campo disponível para atualização do card."}), 400

        output_versao = ", INSERTED.VersaoConcorrencia AS VersaoConcorrencia" if has_versao else ""
        where_versao = "AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""

        if has_versao:
            parametros_update["versao_concorrencia"] = versao_concorrencia_bytes

        sql_upd = text(
            f"""
            UPDATE {TABELA_CARD}
               SET {', '.join(campos_update)}
            OUTPUT
                INSERTED.IDFatoKanbanCard,
                INSERTED.IDDimKanban,
                INSERTED.IDDimKanbanFaseAtual,
                INSERTED.StatusCard,
                INSERTED.IDEmpresaProprietaria
                {output_versao}
             WHERE IDFatoKanbanCard = :id_card
               AND Ativo = 1
               {where_versao};
            """
        )

        row_upd = db.session.execute(sql_upd, parametros_update).mappings().first()
        if not row_upd:
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return jsonify(
                {
                    "ok": False,
                    "msg": "O card foi alterado por outra operação. Recarregue a tela e tente novamente.",
                    "card_atual": detalhe_atual.get("card"),
                }
            ), 409

        sincronizacao_tipo = _sincronizar_tipo_contrato_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_fase_atual=int(id_fase_atual),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
            tipo_contrato=str(contexto_tipo_contrato["tipo_contrato"]),
            id_contrato_existente=contexto_tipo_contrato["id_contrato_existente"],
            cod_ponto_contrato=validacao_ponto_face.get("cod_ponto"),
            cod_face_contrato=validacao_ponto_face.get("cod_face"),
            aplicar_tags=True,
        )

        sincronizacao_tag_plano_midia = _sincronizar_tag_plano_midia_por_fase(
            id_card=int(id_card),
            id_fase_atual=int(id_fase_atual),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
        )

        sincronizacao_item_contrato = {
            "sincronizado": False,
            "quantidade_itens_controle_contrato_atualizados": 0,
            "motivo_itens_controle_contrato": None,
        }

        if contexto_tipo_contrato["id_contrato_existente"] not in (None, "", 0):
            cod_ponto_salvo = str(validacao_ponto_face.get("cod_ponto") or "").strip()
            cod_face_salva = str(validacao_ponto_face.get("cod_face") or "").strip().upper()

            if not cod_ponto_salvo:
                sincronizacao_item_contrato["motivo_itens_controle_contrato"] = "card_sem_cod_ponto_contrato"
            elif not cod_face_salva:
                sincronizacao_item_contrato["motivo_itens_controle_contrato"] = "card_sem_cod_face_contrato"
            else:
                quantidade_itens_controle_contrato_atualizados = _atualizar_card_nos_itens_contrato_euromidia(
                    id_empresa=int(empresas_relacionadas_card.get("id_empresa_card") or 0) or None,
                    id_contrato=int(contexto_tipo_contrato["id_contrato_existente"]),
                    cod_ponto=cod_ponto_salvo,
                    cod_face=cod_face_salva,
                    id_card=int(id_card),
                )

                sincronizacao_item_contrato["sincronizado"] = True
                sincronizacao_item_contrato["quantidade_itens_controle_contrato_atualizados"] = int(
                    quantidade_itens_controle_contrato_atualizados or 0
                )

                if int(quantidade_itens_controle_contrato_atualizados or 0) <= 0:
                    raise RuntimeError(
                        "Nenhum item de FatoControleContratosItensEuromidia foi atualizado com o "
                        "IDFatoKanbanCard ao salvar o card. "
                        "Verifique contrato + CodPonto + CodFace gravados no card."
                    )

        vinculos_preparados: list[dict[str, object]] = []
        sincronizacao_reservas = {"criadas": 0, "canceladas": 0, "mantidas": 0}
        correcao_estado_preco_card = {
            "ok": True,
            "linhas_avaliadas": 0,
            "linhas_corrigidas": 0,
            "motivo": "nao_executado_ainda",
        }

        if isinstance(painel_faces_payload, list):
            vinculos_preparados = _preparar_vinculos_painel_faces(
                painel_faces_payload,
                int(id_emp),
                id_card=int(id_card),
                id_contrato_existente=id_contrato_existente_final,
            )

            db.session.execute(
                text(
                    f"""
                    UPDATE {TABELA_CARD_PAINEL_FACE}
                       SET Ativo = 0,
                           DataAtualizacao = GETDATE()
                     WHERE IDFatoKanbanCard = :id_card
                       AND ISNULL(Ativo, 1) = 1;
                    """
                ),
                {"id_card": int(id_card)},
            )

            for ordem_rel, vinculo in enumerate(vinculos_preparados, start=1):
                db.session.execute(
                    text(
                        f"""
                        INSERT INTO {TABELA_CARD_PAINEL_FACE} (
                            IDFatoKanbanCard,
                            Ordem,
                            IDDimPaineisEuromidia,
                            IDDimFacesPaineis,
                            CodPonto,
                            CodFace,
                            TipoPainel,
                            AnoCusto,
                            CustoTabela,
                            IDDimTabelaPrecosEuromidia,
                            PeriodoExibicao,
                            ExibicoesDia,
                            ValorTabela,
                            Tabela,
                            PoliticaTrocas,
                            ValorTroca,
                            NovoValor,
                            PercentualDesconto,
                            ValorVendaFinal,
                            MargemValor,
                            MargemPercentual,
                            DataInicio,
                            DataFim,
                            Ativo
                        )
                        VALUES (
                            :id_card,
                            :ordem,
                            :id_painel,
                            :id_face,
                            :cod_ponto,
                            :cod_face,
                            :tipo_painel,
                            :ano_custo,
                            :custo_tabela,
                            :id_tabela_preco,
                            :periodo_exibicao,
                            :exibicoes_dia,
                            :valor_tabela,
                            :tabela,
                            :politica_trocas,
                            :valor_troca,
                            :novo_valor,
                            :percentual_desconto,
                            :valor_venda_final,
                            :margem_valor,
                            :margem_percentual,
                            :data_inicio,
                            :data_fim,
                            1
                        );
                        """
                    ),
                    {
                        "id_card": int(id_card),
                        "ordem": int(ordem_rel),
                        "id_painel": (
                            int(_valor_vinculo(vinculo, "IDDimPaineisEuromidia", "id_painel") or 0)
                            or None
                        ),
                        "id_face": (
                            int(_valor_vinculo(vinculo, "IDDimFacesPaineis", "id_dim_face", "id_face") or 0)
                            or None
                        ),
                        "cod_ponto": _valor_vinculo(vinculo, "CodPonto", "cod_ponto"),
                        "cod_face": _valor_vinculo(vinculo, "CodFace", "cod_face"),
                        "tipo_painel": _valor_vinculo(vinculo, "TipoPainel", "tipo_painel"),
                        "ano_custo": _valor_vinculo(vinculo, "AnoCusto", "ano_custo"),
                        "custo_tabela": _valor_vinculo(vinculo, "CustoTabela", "custo_tabela"),
                        "id_tabela_preco": _valor_vinculo(
                            vinculo,
                            "IDDimTabelaPrecosEuromidia",
                            "id_tabela_preco",
                            "id_preco",
                        ),
                        "periodo_exibicao": _valor_vinculo(vinculo, "PeriodoExibicao", "periodo_exibicao"),
                        "exibicoes_dia": _valor_vinculo(vinculo, "ExibicoesDia", "exibicoes_dia"),
                        "valor_tabela": _valor_vinculo(vinculo, "ValorTabela", "valor_tabela"),
                        "tabela": _valor_vinculo(vinculo, "Tabela", "tabela"),
                        "politica_trocas": _valor_vinculo(vinculo, "PoliticaTrocas", "politica_trocas"),
                        "valor_troca": _valor_vinculo(vinculo, "ValorTroca", "valor_troca"),
                        "novo_valor": _valor_vinculo(vinculo, "NovoValor", "novo_valor"),
                        "percentual_desconto": _valor_vinculo(
                            vinculo,
                            "PercentualDesconto",
                            "percentual_desconto",
                        ),
                        "valor_venda_final": _valor_vinculo(
                            vinculo,
                            "ValorVendaFinal",
                            "valor_venda_final",
                        ),
                        "margem_valor": _valor_vinculo(vinculo, "MargemValor", "margem_valor"),
                        "margem_percentual": _valor_vinculo(vinculo, "MargemPercentual", "margem_percentual"),
                        "data_inicio": _para_data_sql_ou_none(
                            _valor_vinculo(vinculo, "DataInicio", "data_inicio")
                        ),
                        "data_fim": _para_data_sql_ou_none(
                            _valor_vinculo(vinculo, "DataFim", "data_fim")
                        ),
                    },
                )

        correcao_estado_preco_card = _corrigir_estado_operacional_preco_card(
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_emp),
            id_contrato_existente=id_contrato_existente_final,
        )

        if int(id_fase_atual or 0) == 4 or _card_tem_reservas_ativas_kanban(int(id_card)):
            sincronizacao_reservas = _sincronizar_reservas_painel_faces_kanban(
                id_card=int(id_card),
                titulo_card=titulo,
                id_empresa_relacionada=empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final,
                id_usuario=int(id_usuario),
                id_empresa_proprietaria=int(id_emp),
                painel_faces_payload=painel_faces_payload if isinstance(painel_faces_payload, list) else None,
                vinculos_preparados=vinculos_preparados,
            )

        snapshot_solicitacao = _sincronizar_ativacao_solicitacao_por_fase_do_card(
            id_card=int(id_card),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_emp),
            dados_formulario_solicitacao=solicitacao_contrato_payload,
            tipo_contrato_fallback=contexto_tipo_contrato.get("tipo_contrato"),
            id_contrato_existente_fallback=contexto_tipo_contrato.get("id_contrato_existente"),
            cod_ponto_contrato_fallback=validacao_ponto_face.get("cod_ponto"),
            cod_face_contrato_fallback=validacao_ponto_face.get("cod_face"),
            id_empresa_relacionada_fallback=empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final,
        )

        sincronizacao_contato_contrato = None
        if int(id_fase_atual or 0) == 4:
            sincronizacao_contato_contrato = _upsert_dim_contatos_contrato_por_card(
                id_card=int(id_card),
                id_empresa=empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final,
                id_empresa_proprietaria=int(id_emp),
                id_fato_controle_contratos=contexto_tipo_contrato.get("id_contrato_existente"),
            )

        if contexto_tipo_contrato["tipo_contrato"] in {TIPO_SOLICITACAO_ADITIVO, TIPO_SOLICITACAO_NOVO}:
            motivo_snapshot = snapshot_solicitacao.get("motivo") or "sincronizacao_solicitacao_nao_realizada"
            if (
                not snapshot_solicitacao.get("sincronizado")
                and motivo_snapshot != "nenhum_item_snapshot_montado"
            ):
                raise RuntimeError(
                    "Falha ao sincronizar a solicitação de contrato após salvar o card. "
                    f"Motivo: {motivo_snapshot}"
                )

        relacionamento_empresa_tipo_cliente = None
        id_empresa_para_relacionamento = empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final
        if id_empresa_para_relacionamento not in (None, "", 0):
            relacionamento_empresa_tipo_cliente = _garantir_relacionamento_empresa_tipo_cliente(
                id_empresa=int(id_empresa_para_relacionamento),
                id_empresa_proprietaria=int(ID_EMPRESA_PROPRIETARIA_CONTRATOS),
                id_dim_tipo_cliente=id_tipo_cliente_relacionamento_final,
                id_dim_origem_atendimento=id_origem_atendimento_relacionamento_final,
            )

        observacoes_proposta_negociacao = None
        if isinstance(solicitacao_contrato_payload, dict):
            observacoes_proposta_negociacao = (
                solicitacao_contrato_payload.get("ObservacoesProposta")
                or solicitacao_contrato_payload.get("observacoes_proposta")
                or solicitacao_contrato_payload.get("observacoes")
            )

        sql_qtd_negociacao = text(f"""
            SELECT COUNT(1)
            FROM {TABELA_CARD_NEGOCIACAO_PRECO}
            WHERE IDFatoKanbanCard = :id_card;
        """)

        qtd_negociacao_antes = int(
            db.session.execute(
                sql_qtd_negociacao,
                {"id_card": int(id_card)},
            ).scalar()
            or 0
        )

        _registrar_negociacao_preco_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_fase_atual=int(row_upd.get("IDDimKanbanFaseAtual") or id_fase_atual or 0) or None,
            status_card=str(row_upd.get("StatusCard") or card_atual.get("StatusCard") or "").strip() or None,
            id_empresa_relacionada=empresas_relacionadas_card.get("id_empresa_card") or id_empresa_principal_final,
            vinculos_preparados=vinculos_preparados,
            observacoes_proposta=observacoes_proposta_negociacao,
        )

        qtd_negociacao_depois = int(
            db.session.execute(
                sql_qtd_negociacao,
                {"id_card": int(id_card)},
            ).scalar()
            or 0
        )

        linhas_historico_negociacao_inseridas = max(
            qtd_negociacao_depois - qtd_negociacao_antes,
            0,
        )

        resultado_historico_negociacao_preco = {
            "ok": True,
            "qtd_antes": int(qtd_negociacao_antes),
            "qtd_depois": int(qtd_negociacao_depois),
            "linhas_inseridas": int(linhas_historico_negociacao_inseridas),
            "motivo": "historico_negociacao_preco_sincronizado_por_funcao_unica_sem_insert_duplicado",
        }

        current_app.logger.warning(
            "NEGOCIACAO_PRECO_HISTORICO | id_card=%s | linhas_inseridas=%s | qtd_antes=%s | qtd_depois=%s",
            int(id_card),
            int(linhas_historico_negociacao_inseridas),
            int(qtd_negociacao_antes),
            int(qtd_negociacao_depois),
        )

        estados_atuais_aprovacao_desconto = _listar_estado_atual_negociacao_card(int(id_card))

        id_usuario_solicitante_aprovacao = _resolver_id_usuario_solicitante_desconto_card(
            id_card=int(id_card),
            id_usuario_fallback=int(id_usuario),
        )

        if not id_usuario_solicitante_aprovacao:
            id_usuario_solicitante_aprovacao = int(id_usuario)

        id_empresa_proprietaria_aprovacao = _resolver_id_empresa_proprietaria_movimento(
            id_kanban=int(id_kanban),
            id_empresa_padrao=int(id_emp),
        )

        _sincronizar_tag_aprovacao_diretoria_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            estados_atuais=estados_atuais_aprovacao_desconto,
            id_usuario=int(id_usuario_solicitante_aprovacao),
            id_empresa_proprietaria=int(id_empresa_proprietaria_aprovacao),
        )

        resultado_aprovacao_preco = _materializar_negociacao_preco_pendente_card(
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_emp),
            id_usuario=int(id_usuario_solicitante_aprovacao),
        )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        observacao_log = (
            "Card atualizado via Kanban"
            f" | tipo_contrato={sincronizacao_tipo.get('tipo_contrato')}"
            f" | id_contrato_existente={contexto_tipo_contrato.get('id_contrato_existente')}"
            f" | cod_ponto={validacao_ponto_face.get('cod_ponto')}"
            f" | cod_face={validacao_ponto_face.get('cod_face')}"
            f" | id_empresa={id_empresa_principal_final if id_empresa_principal_final not in (None, '', 0) else 'NULL'}"
            f" | id_empresa_agencia={campos_complementares_novo_contrato.get('id_empresa_agencia') if campos_complementares_novo_contrato.get('id_empresa_agencia') not in (None, '', 0) else 'NULL'}"
            f" | marca={campos_complementares_novo_contrato.get('marca') or 'NULL'}"
            f" | telefone={campos_complementares_novo_contrato.get('telefone') or 'NULL'}"
            f" | email={campos_complementares_novo_contrato.get('email') or 'NULL'}"
            f" | id_tipo_cliente={id_tipo_cliente_relacionamento_final if id_tipo_cliente_relacionamento_final not in (None, '', 0) else 'NULL'}"
            f" | id_origem_atendimento={id_origem_atendimento_relacionamento_final if id_origem_atendimento_relacionamento_final not in (None, '', 0) else 'NULL'}"
            f" | id_dim_cnaes={id_dim_cnaes_final_card if id_dim_cnaes_final_card not in (None, '', 0) else 'NULL'}"
            f" | historico_negociacao_preco_linhas={resultado_historico_negociacao_preco.get('linhas_inseridas')}"
            f" | correcao_estado_preco_linhas={correcao_estado_preco_card.get('linhas_corrigidas')}"
            f" | aprovacao_preco_materializado={resultado_aprovacao_preco.get('materializado')}"
        )

        _registrar_log_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_empresa_proprietaria=int(id_emp),
            id_usuario_acao=int(id_usuario),
            tipo_evento="CARD_ATUALIZADO",
            subtipo_evento="EDICAO",
            id_fase_de=int(id_fase_atual) if id_fase_atual else None,
            id_fase_para=int(row_upd.get("IDDimKanbanFaseAtual") or id_fase_atual or 0) or None,
            observacao=observacao_log[:2000],
            payload_antes=snapshot_antes,
            payload_depois=snapshot_depois,
        )

        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

        detalhe = _obter_card_detalhe_payload(id_card)

        _emitir_evento_kanban(
            id_kanban,
            "card_atualizado",
            {
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
                "tipo_contrato": sincronizacao_tipo,
                "contrato_existente": contrato_existente,
                "snapshot_solicitacao": snapshot_solicitacao,
                "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
                "sincronizacao_item_contrato": sincronizacao_item_contrato,
                "relacionamento_empresa_tipo_cliente": relacionamento_empresa_tipo_cliente,
                "sincronizacao_tag_plano_midia": sincronizacao_tag_plano_midia,
                "sincronizacao_reservas": sincronizacao_reservas,
                "historico_negociacao_preco": resultado_historico_negociacao_preco,
                "correcao_estado_preco_card": correcao_estado_preco_card,
                "aprovacao_preco": resultado_aprovacao_preco,
            },
        )

        return jsonify(
            {
                "ok": True,
                "msg": (
                    f"Card atualizado com sucesso. {int(sincronizacao_reservas.get('criadas') or 0)} reserva(s) criada(s)."
                    if int(sincronizacao_reservas.get("criadas") or 0) > 0
                    else "Card atualizado com sucesso."
                ),
                "reservas_criadas": int(sincronizacao_reservas.get("criadas") or 0),
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
                "tipo_contrato": sincronizacao_tipo,
                "contrato_existente": contrato_existente,
                "snapshot_solicitacao": snapshot_solicitacao,
                "sincronizacao_contato_contrato": sincronizacao_contato_contrato,
                "sincronizacao_item_contrato": sincronizacao_item_contrato,
                "relacionamento_empresa_tipo_cliente": relacionamento_empresa_tipo_cliente,
                "sincronizacao_tag_plano_midia": sincronizacao_tag_plano_midia,
                "sincronizacao_reservas": sincronizacao_reservas,
                "historico_negociacao_preco": resultado_historico_negociacao_preco,
                "correcao_estado_preco_card": correcao_estado_preco_card,
                "aprovacao_preco": resultado_aprovacao_preco,
            }
        )

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao atualizar card: {str(exc)}"}), 500