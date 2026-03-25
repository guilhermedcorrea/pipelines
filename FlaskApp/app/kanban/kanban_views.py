import json
from collections.abc import Mapping
from typing import Any
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text
from ..extensions import cache, db, limiter, socketio



"""Kanban Euromidia Comercial"""


kanban_bp = Blueprint("kanban", __name__)


STATUS_CARD_VALIDOS = ("ATIVO", "CONCLUIDO", "PERDIDO", "CANCELADO")
TIPOS_FASE_VALIDOS = ("ATIVA", "SUCESSO", "PERDA")
TIPOS_TAG_VALIDOS = ("OPERACIONAL", "FINANCEIRA", "INFORMATIVA")
MOTIVOS_INATIVACAO_CARD = {"Desistencia", "Preço", "Apenas Informações", "Outro Motivo"}
NAMESPACE_SOCKET_KANBAN = "/kanban"
TIMEOUT_CACHE_CURTO = 20
TIMEOUT_CACHE_MEDIO = 60
TIMEOUT_CACHE_LONGO = 300
LIMITE_CARDS_POR_FASE = 100















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






def _rowversion_para_hex(valor: Any) -> str | None:
    if valor is None:
        return None

    if isinstance(valor, memoryview):
        valor = valor.tobytes()

    if isinstance(valor, (bytes, bytearray)):
        return bytes(valor).hex().upper()

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





def _rowversion_para_hex(valor) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, memoryview):
        valor = valor.tobytes()
    if isinstance(valor, (bytes, bytearray)):
        return bytes(valor).hex().upper()
    return None








def _sala_kanban(id_kanban: int) -> str:
    return f"kanban:{int(id_kanban)}"



def _obter_celery_app() -> Any | None:
    return current_app.extensions.get("celery")



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
    payload_final = {**payload, "id_kanban": int(id_kanban)}

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



def _chave_cache_json(prefixo: str, *partes: Any) -> str:
    partes_str = [prefixo]
    for parte in partes:
        if isinstance(parte, (dict, list, tuple)):
            partes_str.append(json.dumps(parte, sort_keys=True, ensure_ascii=False))
        else:
            partes_str.append(str(parte))
    return "|".join(partes_str)



def _cache_json_get(chave: str) -> dict[str, Any] | list[Any] | None:
    valor = cache.get(chave)
    return valor if isinstance(valor, (dict, list)) else None



def _cache_json_set(chave: str, valor: dict[str, Any] | list[Any], timeout: int) -> None:
    cache.set(chave, valor, timeout=timeout)



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
        FROM [Kanban].[Silver].[DimKanban] k
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
    sql = text(f"""
        SELECT
            f.IDDimKanbanFase,
            f.IDDimKanban,
            f.NomeFase,
            f.OrdemFase,
            f.TipoFase,
            f.Ativo,
            k.IDEmpresaProprietaria,
            k.BitPrincipal
        FROM [Kanban].[Silver].[DimKanbanFase] f
        JOIN [Kanban].[Silver].[DimKanban] k
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



def _obter_card_autorizado(id_card: int, *, incluir_inativo: bool = False) -> dict[str, Any]:
    id_emp = _id_empresa_usuario_or_403()
    filtro_ativo = "" if incluir_inativo else "AND c.Ativo = 1"
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
            k.BitPrincipal,
            k.IDEmpresaProprietaria AS IDEmpresaDoKanban
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        JOIN [Kanban].[Silver].[DimKanban] k
          ON k.IDDimKanban = c.IDDimKanban
        WHERE c.IDFatoKanbanCard = :id_card
          AND k.IDEmpresaProprietaria = :id_emp
          AND k.Ativo = 1
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_card": id_card, "id_emp": id_emp}).mappings().first()
    if not row:
        abort(403, "Você não tem permissão para acessar este card")
    return dict(row)



def _obter_fases_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_fases = text("""
        SELECT
            IDDimKanbanFase,
            NomeFase,
            OrdemFase,
            TipoFase,
            Ativo
        FROM [Kanban].[Silver].[DimKanbanFase]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1
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
    sql_cards = text("""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDEmpresaProprietaria,
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] e
          ON e.IDEmpresa = c.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimCnaes] cn
          ON cn.cnaepadrao = e.CNAE
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          AND c.StatusCard IN ('ATIVO', 'CONCLUIDO', 'PERDIDO', 'CANCELADO')
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
            c.IDFatoKanbanCard DESC;
    """)
    cards = db.session.execute(sql_cards, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(cards)



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


def _calcular_margens_comerciais(custo: Any, valor_tabela: Any, novo_valor: Any, percentual_desconto: Any) -> dict[str, Any]:
    custo_dec = _valor_decimal(custo) or Decimal('0')
    valor_tabela_dec = _valor_decimal(valor_tabela)
    novo_valor_dec = _valor_decimal(novo_valor)
    percentual_dec = _valor_decimal(percentual_desconto)

    valor_final: Decimal | None = None
    percentual_aplicado: Decimal | None = None

    if novo_valor_dec is not None:
        valor_final = novo_valor_dec
    elif percentual_dec is not None and valor_tabela_dec is not None:
        percentual_aplicado = percentual_dec
        valor_final = valor_tabela_dec * (Decimal('1') - (percentual_dec / Decimal('100')))
    else:
        valor_final = valor_tabela_dec

    margem_valor: Decimal | None = None
    margem_percentual: Decimal | None = None
    if valor_final is not None:
        margem_valor = valor_final - custo_dec
        if valor_final != 0:
            margem_percentual = (margem_valor / valor_final) * Decimal('100')

    return {
        "Custo": float(custo_dec) if custo is not None else None,
        "ValorTabela": float(valor_tabela_dec) if valor_tabela_dec is not None else None,
        "NovoValor": float(novo_valor_dec) if novo_valor_dec is not None else None,
        "PercentualDesconto": float(percentual_aplicado if percentual_aplicado is not None else percentual_dec) if (percentual_aplicado is not None or percentual_dec is not None) else None,
        "ValorVendaFinal": float(valor_final) if valor_final is not None else None,
        "MargemValor": float(margem_valor) if margem_valor is not None else None,
        "MargemPercentual": float(margem_percentual) if margem_percentual is not None else None,
    }


def _listar_paineis_vinculados_card(id_card: int) -> list[dict[str, Any]]:
    sql = text("""
        SELECT
            r.IDFatoKanbanCardPainelFace,
            r.Ordem,
            r.IDDimPaineisEuromidia,
            r.IDDimFacesPaineis,
            r.CodPonto,
            r.CodFace,
            r.TipoPainel,
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
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.QuantidadeFaces
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] r
        LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] p
          ON TRY_CONVERT(int, p.IDDimPaineisEuromidia) = TRY_CONVERT(int, r.IDDimPaineisEuromidia)
        WHERE r.IDFatoKanbanCard = :id_card
          AND r.Ativo = 1
        ORDER BY r.Ordem ASC, r.IDFatoKanbanCardPainelFace ASC;
    """)
    rows = db.session.execute(sql, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)

def _obter_card_detalhe_payload(id_card: int) -> dict[str, Any]:
    card_escopo = _obter_card_autorizado(id_card)
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    sql = text("""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.Descricao,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.VersaoConcorrencia,
            c.IDDimKanbanOrigem,
            c.IDDimKanbanMotivoEncerramento,
            c.MotivoEncerramentoObs,
            c.IDEmpresaProprietaria,
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] e
          ON e.IDEmpresa = c.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimCnaes] cn
          ON cn.cnaepadrao = e.CNAE
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.Ativo = 1;
    """)
    card = db.session.execute(sql, {"id_card": id_card}).mappings().first()
    if not card:
        abort(404, "Card não encontrado")

    card_dict = dict(card)

    valor_versao = card_dict.pop("VersaoConcorrencia", None)

    if isinstance(valor_versao, memoryview):
        valor_versao = valor_versao.tobytes()

    if isinstance(valor_versao, (bytes, bytearray)):
        card_dict["VersaoConcorrenciaHex"] = bytes(valor_versao).hex().upper()
    else:
        card_dict["VersaoConcorrenciaHex"] = None

    sql_tags = text("""
        SELECT
            t.IDDimKanbanTag,
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
    tags = db.session.execute(sql_tags, {"id_card": id_card}).mappings().all()

    sql_notas = text("""
        SELECT
            IDFatoKanbanCardNota,
            TipoNota,
            Texto,
            CriadoEm,
            CriadoPor
        FROM [Kanban].[Silver].[FatoKanbanCardNota]
        WHERE IDFatoKanbanCard = :id_card
        ORDER BY CriadoEm DESC;
    """)
    notas = db.session.execute(sql_notas, {"id_card": id_card}).mappings().all()

    return {
        "ok": True,
        "card": card_dict,
        "kanban_cfg": _obter_cfg_kanban(id_kanban),
        "tags": _rows_para_dicts(tags),
        "notas": _rows_para_dicts(notas),
        "paineis_vinculados": _listar_paineis_vinculados_card(id_card),
    }



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

    chave = _chave_cache_json(
        "kanban:api:dados",
        id_emp,
        id_kanban,
        limite_inicial_por_fase,
        _versao_empresa(id_emp),
        _versao_kanban(id_kanban),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    fases_base = _obter_fases_kanban(id_kanban)
    tags_catalogo = _obter_tags_kanban(id_kanban)
    paineis_catalogo = _obter_paineis_catalogo() if kanban_cfg["MostrarPainelFaceNoCard"] else []

    sql_totais = text("""
        SELECT
            c.IDDimKanbanFaseAtual AS IDDimKanbanFase,
            COUNT(1) AS QuantidadeCardsTotal
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          AND c.StatusCard IN ('ATIVO', 'CONCLUIDO', 'PERDIDO', 'CANCELADO')
        GROUP BY c.IDDimKanbanFaseAtual;
    """)
    rows_totais = db.session.execute(sql_totais, {"id_kanban": id_kanban}).mappings().all()
    mapa_totais_por_fase = {
        int(r["IDDimKanbanFase"]): int(r["QuantidadeCardsTotal"] or 0)
        for r in rows_totais
        if r.get("IDDimKanbanFase") is not None
    }

    sql_cards_iniciais = text("""
        ;WITH CardsOrdenados AS (
            SELECT
                c.IDFatoKanbanCard,
                c.IDDimKanban,
                c.IDDimKanbanFaseAtual,
                c.Titulo,
                c.StatusCard,
                c.CriadoEm,
                c.AtualizadoEm,
                c.VersaoConcorrencia,
                c.IDEmpresaProprietaria,
                e.RazaoSocial AS EmpresaRazaoSocial,
                e.CNPJ AS EmpresaCNPJ,
                e.CNAE AS EmpresaCNAE,
                cn.Classe AS EmpresaClasse,
                cn.Setor AS EmpresaSetor,
                ROW_NUMBER() OVER (
                    PARTITION BY c.IDDimKanbanFaseAtual
                    ORDER BY
                        CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
                        c.IDFatoKanbanCard DESC
                ) AS RowNumFase
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            LEFT JOIN [Integracao].[Silver].[DimEmpresas] e
              ON e.IDEmpresa = c.IDEmpresaProprietaria
            LEFT JOIN [Integracao].[Silver].[DimCnaes] cn
              ON cn.cnaepadrao = e.CNAE
            WHERE c.IDDimKanban = :id_kanban
              AND c.Ativo = 1
              AND c.StatusCard IN ('ATIVO', 'CONCLUIDO', 'PERDIDO', 'CANCELADO')
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
            EmpresaRazaoSocial,
            EmpresaCNPJ,
            EmpresaCNAE,
            EmpresaClasse,
            EmpresaSetor,
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
    ids_cards_iniciais: list[int] = []
    mapa_carregados_por_fase: dict[int, int] = {}

    for row in rows_cards_iniciais:
        card_dict = dict(row)
        card_dict["VersaoConcorrenciaHex"] = _rowversion_para_hex(card_dict.pop("VersaoConcorrencia", None))
        card_dict.pop("RowNumFase", None)

        id_card = int(card_dict.get("IDFatoKanbanCard") or 0)
        id_fase = int(card_dict.get("IDDimKanbanFaseAtual") or 0)

        if id_card:
            ids_cards_iniciais.append(id_card)

        mapa_carregados_por_fase[id_fase] = mapa_carregados_por_fase.get(id_fase, 0) + 1
        cards_iniciais.append(card_dict)

    card_tags_iniciais: list[dict[str, Any]] = []

    if ids_cards_iniciais:
        params_card_tags = {f"id_card_{i}": int(v) for i, v in enumerate(ids_cards_iniciais)}
        placeholders = ", ".join(f":id_card_{i}" for i in range(len(ids_cards_iniciais)))

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
        "card_tags": card_tags_iniciais,
        "paineis": paineis_catalogo,
        "limit_inicial_por_fase": limite_inicial_por_fase,
        "carga_parcial": True,
    }

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

    if not id_fase or not _validar_fase_do_kanban(id_kanban, id_fase):
        return jsonify({"ok": False, "msg": "Fase inválida para este kanban"}), 400

    chave = _chave_cache_json(
        "kanban:api:cards:fase",
        id_emp,
        id_kanban,
        id_fase,
        offset,
        limit,
        _versao_kanban(id_kanban),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql_total = text("""
        SELECT COUNT(1)
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          AND c.StatusCard IN ('ATIVO', 'CONCLUIDO', 'PERDIDO', 'CANCELADO');
    """)
    total = int(db.session.execute(sql_total, {"id_kanban": id_kanban, "id_fase": id_fase}).scalar() or 0)

    sql_cards = text("""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDEmpresaProprietaria,
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] e
          ON e.IDEmpresa = c.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimCnaes] cn
          ON cn.cnaepadrao = e.CNAE
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          AND c.StatusCard IN ('ATIVO', 'CONCLUIDO', 'PERDIDO', 'CANCELADO')
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
            c.IDFatoKanbanCard DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY;
    """)
    cards = db.session.execute(
        sql_cards,
        {
            "id_kanban": id_kanban,
            "id_fase": id_fase,
            "offset": offset,
            "limit": limit,
        },
    ).mappings().all()

    payload = {
        "ok": True,
        "id_kanban": id_kanban,
        "id_fase": id_fase,
        "offset": offset,
        "limit": limit,
        "total": total,
        "cards": _rows_para_dicts(cards),
    }
    _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)
    return jsonify(payload)


@kanban_bp.route("/api/cards/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("180/minute")
def api_card_detalhe(id_card: int):
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    card_escopo = _obter_card_autorizado(id_card)
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    chave = _chave_cache_json(
        "kanban:api:card:detalhe",
        id_emp,
        id_kanban,
        id_card,
        _versao_kanban(id_kanban),
        _versao_card(id_card),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    payload = _obter_card_detalhe_payload(id_card)
    _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)
    return jsonify(payload)


@kanban_bp.route("/api/empresas", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_empresas_lista():
    _assert_login()

    chave = _chave_cache_json("kanban:api:empresas:lista")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT TOP 500
            e.IDEmpresa,
            e.RazaoSocial,
            e.CNPJ,
            e.CNAE,
            cn.Classe,
            cn.Setor
        FROM [Integracao].[Silver].[DimEmpresas] e
        LEFT JOIN [Integracao].[Silver].[DimCnaes] cn
          ON cn.cnaepadrao = e.CNAE
        WHERE e.RazaoSocial IS NOT NULL
          AND LTRIM(RTRIM(e.RazaoSocial)) <> ''
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
    if len(q) < 2:
        return jsonify({"ok": True, "empresas": []})

    q_digits = "".join([c for c in q if c.isdigit()])
    chave = _chave_cache_json("kanban:api:empresas:buscar", q.lower(), q_digits)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT TOP 25
            e.IDEmpresa,
            e.RazaoSocial,
            e.CNPJ,
            e.CNAE,
            c.Classe,
            c.Setor
        FROM [Integracao].[Silver].[DimEmpresas] e
        LEFT JOIN [Integracao].[Silver].[DimCnaes] c
          ON c.cnaepadrao = e.CNAE
        WHERE (e.RazaoSocial LIKE :q_like)
           OR (e.CNPJ LIKE :q_like)
           OR (:q_digits <> '' AND REPLACE(REPLACE(REPLACE(e.CNPJ,'.',''),'/',''),'-','') LIKE :q_digits_like)
        ORDER BY
            CASE WHEN e.RazaoSocial LIKE :q_like_inicio THEN 0 ELSE 1 END,
            e.RazaoSocial ASC;
    """)
    empresas = db.session.execute(
        sql,
        {
            "q_like": f"%{q}%",
            "q_like_inicio": f"{q}%",
            "q_digits": q_digits,
            "q_digits_like": f"%{q_digits}%",
        },
    ).mappings().all()

    payload = {"ok": True, "empresas": _rows_para_dicts(empresas)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_MEDIO)
    return jsonify(payload)


@kanban_bp.route("/api/paineis", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_paineis_lista():
    _assert_login()
    return jsonify({"ok": True, "paineis": _obter_paineis_catalogo()})


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
          AND LTRIM(RTRIM(f.CodFace)) <> ''
        GROUP BY f.CodFace, f.Face
        ORDER BY
            CASE WHEN f.Face IS NULL OR LTRIM(RTRIM(f.Face)) = '' THEN 1 ELSE 0 END,
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
                TRY_CONVERT(int, f.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
                OR (
                    TRY_CONVERT(int, f.CodPonto) = TRY_CONVERT(int, :cod_ponto)
                    AND UPPER(LTRIM(RTRIM(ISNULL(f.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
                )
              )
        GROUP BY f.IDDimFacesPaineis, f.CodFace, f.Face, f.Tipo
        ORDER BY
            CASE WHEN f.Face IS NULL OR LTRIM(RTRIM(f.Face)) = '' THEN 1 ELSE 0 END,
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
    tipo = (payload.get("tipo") or "ATIVA").strip().upper()
    ordem = payload.get("ordem")

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da fase inválido"}), 400
    if tipo not in TIPOS_FASE_VALIDOS:
        return jsonify({"ok": False, "msg": "TipoFase inválido"}), 400

    if ordem is None:
        sql_max = text("""
            SELECT ISNULL(MAX(OrdemFase), 0) + 1
            FROM [Kanban].[Silver].[DimKanbanFase]
            WHERE IDDimKanban = :id_kanban;
        """)
        ordem = int(db.session.execute(sql_max, {"id_kanban": id_kanban}).scalar() or 1)
    else:
        ordem = int(ordem)

    sql = text("""
        INSERT INTO [Kanban].[Silver].[DimKanbanFase]
            (IDDimKanban, NomeFase, OrdemFase, TipoFase, Ativo, CriadoEm, IDUsuario, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDDimKanbanFase
        VALUES
            (:id_kanban, :nome, :ordem, :tipo, 1, GETDATE(), :id_usuario, :id_emp);
    """)
    novo_id = db.session.execute(
        sql,
        {
            "id_kanban": id_kanban,
            "nome": nome[:100],
            "ordem": ordem,
            "tipo": tipo,
            "id_usuario": id_usuario,
            "id_emp": id_emp,
        },
    ).scalar()

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
        },
    )

    return jsonify({"ok": True, "IDDimKanbanFase": int(novo_id)})


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


@kanban_bp.route("/api/kanbans/<int:id_kanban>/cards", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_criar(id_kanban: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    _obter_kanban_autorizado(id_kanban)

    payload = request.get_json(silent=True) or {}
    titulo = (payload.get("titulo") or "").strip()
    id_fase = int(payload.get("id_fase") or 0)

    if len(titulo) < 2:
        return jsonify({"ok": False, "msg": "Título inválido"}), 400
    if not id_fase:
        return jsonify({"ok": False, "msg": "Fase obrigatória"}), 400
    if not _validar_fase_do_kanban(id_kanban, id_fase):
        return jsonify({"ok": False, "msg": "Fase inválida para este kanban"}), 400

    sql = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanCard]
            (IDDimKanban, IDDimKanbanFaseAtual, Titulo, Descricao,
             IDCliente, IDVendedorUsuario, IDDimKanbanOrigem,
             StatusCard, CriadoEm, Ativo, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDFatoKanbanCard
        VALUES
            (:id_kanban, :id_fase, :titulo, NULL,
             NULL, :id_usuario, NULL,
             'ATIVO', GETDATE(), 1, :id_emp);
    """)
    novo_id = db.session.execute(
        sql,
        {
            "id_kanban": id_kanban,
            "id_fase": id_fase,
            "titulo": titulo[:200],
            "id_usuario": id_usuario,
            "id_emp": id_emp,
        },
    ).scalar()

    db.session.commit()
    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=int(novo_id))

    detalhe = _obter_card_detalhe_payload(int(novo_id))
    _emitir_evento_kanban(
        id_kanban,
        "card_criado",
        {
            "card": detalhe["card"],
            "id_fase": id_fase,
            "tags": detalhe["tags"],
            "notas": detalhe["notas"],
        },
    )

    return jsonify({"ok": True, "IDFatoKanbanCard": int(novo_id), "card": detalhe["card"]})




@kanban_bp.route("/api/cards/<int:id_card>", methods=["PUT"])
@login_required
@limiter.limit("180/minute")
def api_card_atualizar(id_card: int):
    _assert_login()
    card_escopo = _obter_card_autorizado(id_card)
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)
    id_emp = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}

    versao_concorrencia_hex = (payload.get("versao_concorrencia") or "").strip()
    versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia_hex)
    if not versao_concorrencia_bytes:
        return jsonify(
            {
                "ok": False,
                "codigo": "VERSAO_OBRIGATORIA",
                "msg": "Versão de concorrência do card é obrigatória e deve estar em hexadecimal.",
            }
        ), 400

    titulo = (payload.get("titulo") or "").strip()
    descricao = payload.get("descricao")
    status = (payload.get("status") or "").strip().upper()
    id_empresa = payload.get("id_empresa")
    painel_faces_payload = payload.get("painel_faces") if "painel_faces" in payload else None
    painel_faces_informado = "painel_faces" in payload

    if titulo and len(titulo) < 2:
        return jsonify({"ok": False, "msg": "Título inválido"}), 400

    if painel_faces_informado and painel_faces_payload is None:
        painel_faces_payload = []
    if painel_faces_informado and not isinstance(painel_faces_payload, list):
        return jsonify({"ok": False, "msg": "painel_faces deve ser uma lista"}), 400

    campos: list[str] = []
    params: dict[str, Any] = {
        "id_card": id_card,
        "id_kanban": id_kanban,
        "versao_concorrencia": versao_concorrencia_bytes,
    }

    if titulo:
        campos.append("Titulo = :titulo")
        params["titulo"] = titulo[:200]

    if descricao is not None:
        campos.append("Descricao = :descricao")
        params["descricao"] = descricao

    if status:
        if status not in STATUS_CARD_VALIDOS:
            return jsonify({"ok": False, "msg": "StatusCard inválido"}), 400
        campos.append("StatusCard = :status")
        params["status"] = status
        if status in ("CONCLUIDO", "PERDIDO", "CANCELADO"):
            campos.append("EncerradoEm = ISNULL(EncerradoEm, GETDATE())")

    if id_empresa is not None:
        id_empresa_str = str(id_empresa).strip()
        if id_empresa_str == "":
            id_empresa_int = None
        else:
            try:
                id_empresa_int = int(id_empresa)
            except Exception:
                return jsonify({"ok": False, "msg": "Empresa inválida"}), 400

            sql_emp = text("""
                SELECT 1
                FROM [Integracao].[Silver].[DimEmpresas]
                WHERE IDEmpresa = :id_empresa;
            """)
            ok_emp = db.session.execute(sql_emp, {"id_empresa": id_empresa_int}).scalar()
            if not ok_emp:
                return jsonify({"ok": False, "msg": "Empresa não encontrada"}), 400

        campos.append("IDEmpresaProprietaria = :id_empresa")
        params["id_empresa"] = id_empresa_int

    if not campos and not painel_faces_informado:
        detalhe = _obter_card_detalhe_payload(id_card)
        return jsonify({"ok": True, "card": detalhe["card"], "paineis_vinculados": detalhe.get("paineis_vinculados", [])})

    try:
        sql_lock_card = text("""
            DECLARE @resultado INT;
            EXEC @resultado = sp_getapplock
                @Resource = :recurso,
                @LockMode = 'Exclusive',
                @LockOwner = 'Transaction',
                @LockTimeout = :timeout_ms;
            SELECT @resultado;
        """)

        codigo_lock = db.session.execute(
            sql_lock_card,
            {
                "recurso": f"kanban:card:{int(id_card)}",
                "timeout_ms": 10000,
            },
        ).scalar()

        try:
            codigo_lock = int(codigo_lock)
        except Exception:
            codigo_lock = -999

        if codigo_lock < 0:
            db.session.rollback()
            return jsonify(
                {
                    "ok": False,
                    "codigo": "LOCK_TIMEOUT",
                    "msg": "O card está sendo alterado por outra operação neste momento. Tente novamente.",
                }
            ), 409

        if campos:
            sql = text(f"""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET {', '.join(campos)},
                    AtualizadoEm = GETDATE()
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.AtualizadoEm,
                    INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimKanban = :id_kanban
                  AND Ativo = 1
                  AND VersaoConcorrencia = :versao_concorrencia;
            """)
        else:
            sql = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET AtualizadoEm = GETDATE()
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.AtualizadoEm,
                    INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimKanban = :id_kanban
                  AND Ativo = 1
                  AND VersaoConcorrencia = :versao_concorrencia;
            """)

        row_atualizada = db.session.execute(sql, params).mappings().first()

        if not row_atualizada:
            db.session.rollback()
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return jsonify(
                {
                    "ok": False,
                    "codigo": "CONFLITO_CONCORRENCIA",
                    "msg": "Este card foi alterado por outro usuário. Recarregue antes de salvar novamente.",
                    "card_atual": detalhe_atual["card"],
                }
            ), 409

        if painel_faces_informado:
            sql_inativar_rel = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
                SET Ativo = 0,
                    RemovidoEm = GETDATE(),
                    RemovidoPor = :id_usuario,
                    DataAtualizacao = GETDATE()
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1;
            """)
            db.session.execute(sql_inativar_rel, {"id_card": id_card, "id_usuario": _id_usuario()})

            ordem_rel = 1
            for item in (painel_faces_payload or []):
                if not isinstance(item, dict):
                    continue

                id_painel_item = int(item.get("id_painel") or 0)
                cod_face_item = _normalizar_texto(item.get("cod_face"))

                if not id_painel_item and not cod_face_item:
                    continue
                if not id_painel_item:
                    return jsonify({"ok": False, "msg": "Painel é obrigatório em cada vinculação"}), 400
                if not cod_face_item:
                    return jsonify({"ok": False, "msg": "Face é obrigatória em cada vinculação"}), 400

                painel_item = _obter_painel_por_id(id_painel_item)
                if not painel_item:
                    return jsonify({"ok": False, "msg": f"Painel {id_painel_item} não encontrado"}), 400

                face_item = _resolver_face_do_painel(id_painel_item, cod_face_item)
                if not face_item:
                    return jsonify({"ok": False, "msg": f"A face {cod_face_item} não pertence ao painel selecionado"}), 400

                custo_item = _obter_custo_por_codponto(int(painel_item.get("CodPonto") or 0))

                id_preco_item = item.get("id_preco")
                if id_preco_item in ("", None):
                    id_preco_item = None
                else:
                    try:
                        id_preco_item = int(id_preco_item)
                    except Exception:
                        return jsonify({"ok": False, "msg": "Preço selecionado inválido"}), 400

                preco_item = None
                if id_preco_item:
                    preco_item = _obter_preco_por_id(
                        id_preco=id_preco_item,
                        id_painel=id_painel_item,
                        id_dim_face=int(face_item.get("IDDimFacesPaineis") or 0) if face_item.get("IDDimFacesPaineis") is not None else None,
                        tipo_painel=_normalizar_texto(painel_item.get("Tipo")),
                    )
                    if not preco_item:
                        return jsonify({"ok": False, "msg": f"O preço selecionado não é válido para o painel/face informado ({cod_face_item})"}), 400

                novo_valor_item = _valor_decimal(item.get("novo_valor"))
                percentual_item = _valor_decimal(item.get("percentual_desconto"))
                if novo_valor_item is not None and percentual_item is not None:
                    percentual_item = None

                metricas = _calcular_margens_comerciais(
                    custo_item.get("Valor") if custo_item else None,
                    preco_item.get("Valor") if preco_item else None,
                    novo_valor_item,
                    percentual_item,
                )

                sql_ins_rel = text("""
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
                            IDUsuario,
                            IDEmpresaProprietaria
                        )
                    VALUES
                        (
                            :id_card,
                            :ordem,
                            :id_painel,
                            :id_dim_face,
                            :cod_ponto,
                            :cod_face,
                            :tipo_painel,
                            :ano_custo,
                            :custo_tabela,
                            :id_preco,
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
                            :id_usuario,
                            :id_empresa
                        );
                """)
                db.session.execute(
                    sql_ins_rel,
                    {
                        "id_card": id_card,
                        "ordem": ordem_rel,
                        "id_painel": int(id_painel_item),
                        "id_dim_face": int(face_item.get("IDDimFacesPaineis") or 0) if face_item.get("IDDimFacesPaineis") is not None else None,
                        "cod_ponto": int(painel_item.get("CodPonto") or 0) if painel_item.get("CodPonto") is not None else None,
                        "cod_face": cod_face_item,
                        "tipo_painel": _normalizar_texto(painel_item.get("Tipo")) or None,
                        "ano_custo": int(custo_item.get("Ano") or 0) if custo_item and custo_item.get("Ano") is not None else None,
                        "custo_tabela": metricas.get("Custo"),
                        "id_preco": int(preco_item.get("IDDimTabelaPrecosEuromidia") or 0) if preco_item else None,
                        "periodo_exibicao": preco_item.get("PeriodoExibicao") if preco_item else None,
                        "exibicoes_dia": int(preco_item.get("ExibicoesDia") or 0) if preco_item and preco_item.get("ExibicoesDia") is not None else None,
                        "valor_tabela": metricas.get("ValorTabela"),
                        "tabela": preco_item.get("Tabela") if preco_item else None,
                        "politica_trocas": preco_item.get("PoliticaTrocas") if preco_item else None,
                        "valor_troca": _decimal_para_float(preco_item.get("ValorTroca")) if preco_item else None,
                        "novo_valor": metricas.get("NovoValor"),
                        "percentual_desconto": metricas.get("PercentualDesconto"),
                        "valor_venda_final": metricas.get("ValorVendaFinal"),
                        "margem_valor": metricas.get("MargemValor"),
                        "margem_percentual": metricas.get("MargemPercentual"),
                        "id_usuario": _id_usuario(),
                        "id_empresa": id_emp,
                    },
                )
                ordem_rel += 1

        db.session.commit()

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao atualizar card: {str(exc)}"}), 500

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    detalhe = _obter_card_detalhe_payload(id_card)

    _emitir_evento_kanban(
        id_kanban,
        "card_atualizado",
        {
            "id_card": id_card,
            "card": detalhe["card"],
            "tags": detalhe["tags"],
            "notas": detalhe["notas"],
        },
    )

    return jsonify({"ok": True, "card": detalhe["card"], "paineis_vinculados": detalhe.get("paineis_vinculados", [])})






@kanban_bp.route("/api/cards/<int:id_card>/mover", methods=["POST"])
@login_required
@limiter.limit("300/minute")
def api_card_mover(id_card: int):
    id_usuario = _assert_login()
    card_escopo = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}

    versao_concorrencia_hex = (payload.get("versao_concorrencia") or "").strip()
    versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia_hex)
    if not versao_concorrencia_bytes:
        return jsonify(
            {
                "ok": False,
                "codigo": "VERSAO_OBRIGATORIA",
                "msg": "Versão de concorrência do card é obrigatória e deve estar em hexadecimal.",
            }
        ), 400

    try:
        id_fase_para = int(payload.get("id_fase_para") or 0)
    except Exception:
        return jsonify({"ok": False, "msg": "Fase destino inválida"}), 400

    observacao = (payload.get("observacao") or "").strip()
    posicao = (payload.get("posicao") or "LAST").strip().upper()

    if not id_fase_para:
        return jsonify({"ok": False, "msg": "Fase destino obrigatória"}), 400

    if posicao not in {"LAST"}:
        return jsonify({"ok": False, "msg": "Posição inválida"}), 400

    try:
        sql_cols = text("""
            SELECT
                MAX(CASE WHEN c.name = 'OrdemNaFase' THEN 1 ELSE 0 END) AS HasOrdemNaFase,
                MAX(CASE WHEN c.name = 'AtualizadoEm' THEN 1 ELSE 0 END) AS HasAtualizadoEm,
                MAX(CASE WHEN c.name = 'VersaoConcorrencia' THEN 1 ELSE 0 END) AS HasVersaoConcorrencia
            FROM sys.columns c
            WHERE c.object_id = OBJECT_ID('[Kanban].[Silver].[FatoKanbanCard]');
        """)
        cols = db.session.execute(sql_cols).mappings().first() or {}

        has_ordem = bool(cols.get("HasOrdemNaFase"))
        has_atualizado = bool(cols.get("HasAtualizadoEm"))
        has_versao = bool(cols.get("HasVersaoConcorrencia"))

        if not has_versao:
            return jsonify(
                {
                    "ok": False,
                    "msg": "A coluna VersaoConcorrencia ainda não existe em [Kanban].[Silver].[FatoKanbanCard].",
                }
            ), 500

        sql_lock_card = text("""
            DECLARE @resultado INT;
            EXEC @resultado = sp_getapplock
                @Resource = :recurso,
                @LockMode = 'Exclusive',
                @LockOwner = 'Transaction',
                @LockTimeout = :timeout_ms;
            SELECT @resultado;
        """)

        codigo_lock_card = db.session.execute(
            sql_lock_card,
            {
                "recurso": f"kanban:card:{int(id_card)}",
                "timeout_ms": 10000,
            },
        ).scalar()

        try:
            codigo_lock_card = int(codigo_lock_card)
        except Exception:
            codigo_lock_card = -999

        if codigo_lock_card < 0:
            db.session.rollback()
            return jsonify(
                {
                    "ok": False,
                    "codigo": "LOCK_TIMEOUT_CARD",
                    "msg": "O card está sendo movimentado por outra operação neste momento. Tente novamente.",
                }
            ), 409

        sql_atual = text("""
            SELECT
                IDDimKanban,
                IDDimKanbanFaseAtual
            FROM [Kanban].[Silver].[FatoKanbanCard] WITH (UPDLOCK, ROWLOCK)
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1;
        """)
        row = db.session.execute(sql_atual, {"id_card": id_card}).mappings().first()
        if not row:
            db.session.rollback()
            return jsonify({"ok": False, "msg": "Card não encontrado"}), 404

        if int(row["IDDimKanban"]) != int(card_escopo.get("IDDimKanban") or 0):
            db.session.rollback()
            return jsonify({"ok": False, "msg": "Card fora do escopo do usuário"}), 403

        id_kanban = int(row["IDDimKanban"])
        id_fase_de = int(row["IDDimKanbanFaseAtual"])

        if not _validar_fase_do_kanban(id_kanban, id_fase_para):
            db.session.rollback()
            return jsonify({"ok": False, "msg": "Fase destino inválida"}), 400

        if id_fase_de == id_fase_para:
            db.session.rollback()
            detalhe = _obter_card_detalhe_payload(id_card)
            return jsonify(
                {
                    "ok": True,
                    "id_card": id_card,
                    "id_fase_de": id_fase_de,
                    "id_fase_para": id_fase_para,
                    "ordem_na_fase": detalhe["card"].get("OrdemNaFase"),
                    "card": detalhe["card"],
                }
            )

        sql_lock_fase = text("""
            DECLARE @resultado INT;
            EXEC @resultado = sp_getapplock
                @Resource = :recurso,
                @LockMode = 'Exclusive',
                @LockOwner = 'Transaction',
                @LockTimeout = :timeout_ms;
            SELECT @resultado;
        """)

        codigo_lock_fase = db.session.execute(
            sql_lock_fase,
            {
                "recurso": f"kanban:kanban:{int(id_kanban)}:fase:{int(id_fase_para)}",
                "timeout_ms": 10000,
            },
        ).scalar()

        try:
            codigo_lock_fase = int(codigo_lock_fase)
        except Exception:
            codigo_lock_fase = -999

        if codigo_lock_fase < 0:
            db.session.rollback()
            return jsonify(
                {
                    "ok": False,
                    "codigo": "LOCK_TIMEOUT_FASE",
                    "msg": "A fase de destino está sendo reorganizada por outra operação neste momento. Tente novamente.",
                }
            ), 409

        proxima_ordem: int | None = None

        if has_ordem:
            sql_next_ordem = text("""
                SELECT ISNULL(MAX(fc.OrdemNaFase), 0) + 1 AS ProximaOrdem
                FROM [Kanban].[Silver].[FatoKanbanCard] fc WITH (UPDLOCK, HOLDLOCK)
                WHERE fc.IDDimKanban = :id_kanban
                  AND fc.IDDimKanbanFaseAtual = :id_fase_para
                  AND fc.Ativo = 1;
            """)
            proxima_ordem = db.session.execute(
                sql_next_ordem,
                {"id_kanban": id_kanban, "id_fase_para": id_fase_para},
            ).scalar()

            try:
                proxima_ordem = int(proxima_ordem or 1)
            except Exception:
                proxima_ordem = 1

        if has_ordem and has_atualizado:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET IDDimKanbanFaseAtual = :id_fase_para,
                    OrdemNaFase = :ordem_na_fase,
                    AtualizadoEm = GETDATE()
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual,
                    INSERTED.OrdemNaFase,
                    INSERTED.AtualizadoEm,
                    INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1
                  AND VersaoConcorrencia = :versao_concorrencia;
            """)
            row_upd = db.session.execute(
                sql_upd,
                {
                    "id_fase_para": id_fase_para,
                    "ordem_na_fase": proxima_ordem,
                    "id_card": id_card,
                    "versao_concorrencia": versao_concorrencia_bytes,
                },
            ).mappings().first()

        elif has_ordem and not has_atualizado:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET IDDimKanbanFaseAtual = :id_fase_para,
                    OrdemNaFase = :ordem_na_fase
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual,
                    INSERTED.OrdemNaFase,
                    INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1
                  AND VersaoConcorrencia = :versao_concorrencia;
            """)
            row_upd = db.session.execute(
                sql_upd,
                {
                    "id_fase_para": id_fase_para,
                    "ordem_na_fase": proxima_ordem,
                    "id_card": id_card,
                    "versao_concorrencia": versao_concorrencia_bytes,
                },
            ).mappings().first()

        elif (not has_ordem) and has_atualizado:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET IDDimKanbanFaseAtual = :id_fase_para,
                    AtualizadoEm = GETDATE()
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual,
                    INSERTED.AtualizadoEm,
                    INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1
                  AND VersaoConcorrencia = :versao_concorrencia;
            """)
            row_upd = db.session.execute(
                sql_upd,
                {
                    "id_fase_para": id_fase_para,
                    "id_card": id_card,
                    "versao_concorrencia": versao_concorrencia_bytes,
                },
            ).mappings().first()

        else:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET IDDimKanbanFaseAtual = :id_fase_para
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual,
                    INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1
                  AND VersaoConcorrencia = :versao_concorrencia;
            """)
            row_upd = db.session.execute(
                sql_upd,
                {
                    "id_fase_para": id_fase_para,
                    "id_card": id_card,
                    "versao_concorrencia": versao_concorrencia_bytes,
                },
            ).mappings().first()

        if not row_upd:
            db.session.rollback()
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return jsonify(
                {
                    "ok": False,
                    "codigo": "CONFLITO_CONCORRENCIA",
                    "msg": "Este card foi alterado ou movido por outro usuário. Recarregue antes de tentar novamente.",
                    "card_atual": detalhe_atual["card"],
                }
            ), 409

        sql_ins = text("""
            INSERT INTO [Kanban].[Silver].[FatoKanbanCardMovimento]
                (IDFatoKanbanCard, IDFaseDe, IDFasePara, MovidoEm, MovidoPor, Observacao, IDEmpresaProprietaria)
            VALUES
                (:id_card, :id_fase_de, :id_fase_para, GETDATE(), :movido_por, :obs, :id_empresa);
        """)
        db.session.execute(
            sql_ins,
            {
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "movido_por": id_usuario,
                "obs": observacao[:2000] if observacao else None,
                "id_empresa": card_escopo.get("IDEmpresaProprietaria"),
            },
        )

        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
        detalhe = _obter_card_detalhe_payload(id_card)

        _emitir_evento_kanban(
            id_kanban,
            "card_movido",
            {
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem if has_ordem else None,
                "card": detalhe["card"],
                "tags": detalhe["tags"],
                "notas": detalhe["notas"],
            },
        )

        return jsonify(
            {
                "ok": True,
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem if has_ordem else None,
                "card": detalhe["card"],
            }
        )

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
    tipo = (payload.get("tipo") or "OPERACIONAL").strip().upper()
    cor = (payload.get("cor_hex") or "").strip()
    icone = (payload.get("icone") or "").strip()

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da tag inválido"}), 400
    if tipo not in TIPOS_TAG_VALIDOS:
        return jsonify({"ok": False, "msg": "TipoTag inválido"}), 400
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
    payload = request.get_json(silent=True) or {}

    id_tag = int(payload.get("id_tag") or 0)
    if not id_tag:
        return jsonify({"ok": False, "msg": "Tag obrigatória"}), 400

    sql_tag = text("""
        SELECT t.IDDimKanbanTag
        FROM [Kanban].[Silver].[DimKanbanTag] t
        WHERE t.IDDimKanbanTag = :id_tag
          AND t.IDDimKanban = :id_kanban
          AND t.Ativo = 1;
    """)
    tag_ok = db.session.execute(
        sql_tag,
        {"id_tag": id_tag, "id_kanban": id_kanban},
    ).scalar()
    if not tag_ok:
        return jsonify({"ok": False, "msg": "Tag inválida para este card"}), 400

    sql_dup = text("""
        SELECT 1
        FROM [Kanban].[Silver].[FatoKanbanCardTag]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)
    existe = db.session.execute(sql_dup, {"id_card": id_card, "id_tag": id_tag}).scalar()
    if existe:
        return jsonify({"ok": True})

    sql = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
            (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
        VALUES
            (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa);
    """)
    db.session.execute(
        sql,
        {
            "id_card": id_card,
            "id_tag": id_tag,
            "id_usuario": id_usuario,
            "id_empresa": card.get("IDEmpresaProprietaria"),
        },
    )
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    _emitir_evento_kanban(
        id_kanban,
        "card_tag_adicionada",
        {"id_card": id_card, "id_tag": id_tag},
    )

    return jsonify({"ok": True})


@kanban_bp.route("/api/cards/<int:id_card>/tags/<int:id_tag>", methods=["DELETE"])
@login_required
@limiter.limit("180/minute")
def api_card_tag_remover(id_card: int, id_tag: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)

    sql = text("""
        UPDATE [Kanban].[Silver].[FatoKanbanCardTag]
        SET RemovidoEm = GETDATE(),
            RemovidoPor = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)
    db.session.execute(sql, {"id_card": id_card, "id_tag": id_tag, "id_usuario": id_usuario})
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    _emitir_evento_kanban(
        id_kanban,
        "card_tag_removida",
        {"id_card": id_card, "id_tag": id_tag},
    )

    return jsonify({"ok": True})


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
    tipo = (payload.get("tipo") or "OBS").strip().upper()

    if len(texto) < 2:
        return jsonify({"ok": False, "msg": "Texto da nota inválido"}), 400

    sql = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardNota]
            (IDFatoKanbanCard, TipoNota, Texto, CriadoEm, CriadoPor, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDFatoKanbanCardNota, INSERTED.CriadoEm
        VALUES
            (:id_card, :tipo, :texto, GETDATE(), :criado_por, :id_empresa);
    """)
    row_nota = db.session.execute(
        sql,
        {
            "id_card": id_card,
            "tipo": tipo[:50],
            "texto": texto,
            "criado_por": id_usuario,
            "id_empresa": card.get("IDEmpresaProprietaria"),
        },
    ).mappings().first()
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    nota_payload = {
        "IDFatoKanbanCardNota": int(row_nota.get("IDFatoKanbanCardNota") or 0) if row_nota else None,
        "TipoNota": tipo[:50],
        "Texto": texto,
        "CriadoPor": id_usuario,
        "CriadoEm": row_nota.get("CriadoEm") if row_nota else None,
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
    motivo = (payload.get("motivo") or "").strip()
    descricao = (payload.get("descricao") or "").strip()

    if motivo not in MOTIVOS_INATIVACAO_CARD:
        return jsonify({"ok": False, "msg": "Motivo inválido"}), 400
    if motivo == "Outro Motivo" and len(descricao) < 2:
        return jsonify({"ok": False, "msg": "Descreva o motivo"}), 400

    sql_card = text("""
        SELECT IDDimKanban, IDDimKanbanFaseAtual, IDEmpresaProprietaria
        FROM [Kanban].[Silver].[FatoKanbanCard]
        WHERE IDFatoKanbanCard = :id_card
          AND Ativo = 1;
    """)
    row = db.session.execute(sql_card, {"id_card": id_card}).mappings().first()
    if not row:
        return jsonify({"ok": False, "msg": "Card não encontrado ou já inativo"}), 404
    if int(row["IDDimKanban"]) != id_kanban:
        return jsonify({"ok": False, "msg": "Card fora do escopo do usuário"}), 403

    id_fase_atual = int(row["IDDimKanbanFaseAtual"])
    id_empresa_card = row.get("IDEmpresaProprietaria")

    try:
        sql_upd = text("""
            UPDATE [Kanban].[Silver].[FatoKanbanCard]
            SET Ativo = 0,
                InativadoEm = GETDATE(),
                InativadoPor = :id_usuario
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_card": id_card})

        sql_ins = text("""
            INSERT INTO [Kanban].[Silver].[FatoKanbanCardMovimento]
                (IDFatoKanbanCard, IDFaseDe, IDFasePara, MovidoEm, MovidoPor, Observacao, IDEmpresaProprietaria)
            VALUES
                (:id_card, :id_fase_de, NULL, GETDATE(), :movido_por, :obs, :id_empresa);
        """)
        obs = f"[INATIVADO] Motivo: {motivo}" + (f" | {descricao}" if descricao else "")
        db.session.execute(
            sql_ins,
            {
                "id_card": id_card,
                "id_fase_de": id_fase_atual,
                "movido_por": id_usuario,
                "obs": obs[:2000],
                "id_empresa": id_empresa_card,
            },
        )

        db.session.commit()
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
        _emitir_evento_kanban(
            id_kanban,
            "card_inativado",
            {"id_card": id_card, "id_fase_de": id_fase_atual, "motivo": motivo, "descricao": descricao or None},
        )
        return jsonify({"ok": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao inativar card: {str(exc)}"}), 500


@kanban_bp.route("/api/fases/<int:id_fase>/inativar", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_fase_inativar(id_fase: int):
    id_usuario = _assert_login()
    fase_escopo = _obter_fase_autorizada(id_fase)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(fase_escopo.get("IDDimKanban") or 0)

    sql_fase = text("""
        SELECT f.IDDimKanbanFase, f.IDDimKanban
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = :id_fase
          AND f.Ativo = 1;
    """)
    row = db.session.execute(sql_fase, {"id_fase": id_fase}).mappings().first()
    if not row:
        return jsonify({"ok": False, "msg": "Fase não encontrada ou já inativa"}), 404
    if int(row["IDDimKanban"]) != id_kanban:
        return jsonify({"ok": False, "msg": "Fase fora do escopo do usuário"}), 403

    try:
        sql_upd = text("""
            UPDATE [Kanban].[Silver].[DimKanbanFase]
            SET Ativo = 0,
                InativadoEm = GETDATE(),
                InativadoPor = :id_usuario
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

    try:
        sql_upd = text("""
            UPDATE [Kanban].[Silver].[DimKanban]
            SET Ativo = 0,
                InativadoEm = GETDATE(),
                InativadoPor = :id_usuario
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_kanban": id_kanban})

        sql_upd_fases = text("""
            UPDATE [Kanban].[Silver].[DimKanbanFase]
            SET Ativo = 0,
                InativadoEm = ISNULL(InativadoEm, GETDATE()),
                InativadoPor = ISNULL(InativadoPor, :id_usuario)
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
