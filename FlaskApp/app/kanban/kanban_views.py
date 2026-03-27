import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text
from flask_socketio import disconnect, emit, join_room, leave_room
from ..extensions import cache, db, limiter, socketio



"""Kanban Euromidia Comercial"""


kanban_bp = Blueprint("kanban", __name__)


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





def _resolver_id_empresa_proprietaria_movimento(id_kanban: int, id_empresa_padrao: int) -> int:
    """
    Regra de negócio:
    - se o kanban for o principal IDDimKanban = 1, o movimento deve ser gravado com IDEmpresaProprietaria = 3
    - caso contrário, uso a empresa proprietária padrão do card/usuário
    """
    if int(id_kanban or 0) == 1:
        return 3

    return int(id_empresa_padrao or 0)












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
        }

        if linha["Ordem"] is None:
            linha["Ordem"] = ordem_padrao

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
            IDEmpresaProprietaria
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
            :id_empresa_proprietaria
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

    for chave in ("IDEmpresaRelacionadaCard", "IDEmpresa", "IDCliente", "IDEmpresaRelacionada"):
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
    row = db.session.execute(sql, {"id_card": id_card, "id_emp": id_emp}).mappings().first()
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
    cod_face: str,
    data_inicio: date,
    data_fim: date,
    marcador_observacao: str,
) -> bool:
    sql = text("""
        SELECT TOP 1 1
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
        WHERE UPPER(LTRIM(RTRIM(COALESCE(CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
          AND CanceladoEm IS NULL
          AND Status IN ('ATIVO', 'RESERVADO')
          AND TRY_CONVERT(date, DataInicio) = :data_inicio
          AND TRY_CONVERT(date, DataFim) = :data_fim
          AND COALESCE(Observacao, '') LIKE :marcador_prefixo
    """)

    existe = db.session.execute(
        sql,
        {
            "cod_face": str(cod_face or "").strip(),
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "marcador_prefixo": f"{marcador_observacao}%",
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

        marcador_observacao = f"[KANBAN_CARD={int(id_card)}][COD_FACE={cod_face}]"

        if _reserva_kanban_ja_existe(
            cod_face=cod_face,
            data_inicio=data_inicio,
            data_fim=data_fim,
            marcador_observacao=marcador_observacao,
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
                'KANBAN',
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










def _preparar_vinculos_painel_faces(painel_faces_payload: list[Any], id_empresa_proprietaria: int) -> list[dict[str, Any]]:
    vinculos_preparados: list[dict[str, Any]] = []

    for ordem_rel, item in enumerate((painel_faces_payload or []), start=1):
        if not isinstance(item, dict):
            raise ValueError("Cada item de painel_faces deve ser um objeto")

        id_painel_item = int(item.get("id_painel") or 0)
        cod_face_item = _normalizar_texto(item.get("cod_face"))

        if not id_painel_item:
            raise ValueError("Painel é obrigatório em cada vinculação")
        if not cod_face_item:
            raise ValueError("Face é obrigatória em cada vinculação")

        painel_item = _obter_painel_por_id(id_painel_item)
        if not painel_item:
            raise ValueError(f"Painel {id_painel_item} não encontrado")

        face_item = _resolver_face_do_painel(id_painel_item, cod_face_item)
        if not face_item:
            raise ValueError(f"A face {cod_face_item} não pertence ao painel selecionado")

        custo_item = _obter_custo_por_codponto(int(painel_item.get("CodPonto") or 0))

        id_preco_item = item.get("id_preco")
        if id_preco_item in ("", None):
            id_preco_item = None
        else:
            try:
                id_preco_item = int(id_preco_item)
            except Exception as exc:
                raise ValueError("Preço selecionado inválido") from exc

        preco_item = None
        if id_preco_item:
            preco_item = _obter_preco_por_id(
                id_preco=id_preco_item,
                id_painel=id_painel_item,
                id_dim_face=int(face_item.get("IDDimFacesPaineis") or 0) if face_item.get("IDDimFacesPaineis") is not None else None,
                tipo_painel=_normalizar_texto(painel_item.get("Tipo")),
            )
            if not preco_item:
                raise ValueError(
                    f"O preço selecionado não é válido para o painel/face informado ({cod_face_item})"
                )

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

        vinculos_preparados.append(
            {
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
                "id_empresa": int(id_empresa_proprietaria),
            }
        )

    return vinculos_preparados




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
            pf.MargemPercentual
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
        WHERE pf.IDFatoKanbanCard = :id_card
          AND ISNULL(pf.Ativo, 1) = 1
        ORDER BY
            ISNULL(pf.Ordem, 0),
            pf.IDFatoKanbanCardPainelFace;
    """)

    rows = db.session.execute(sql, {"id_card": int(id_card)}).mappings().all()
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

    def _montar_insert_dinamico(valores: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        colunas: list[str] = []
        marcadores: list[str] = []
        parametros: dict[str, Any] = {}

        def adicionar(nome_coluna: str, nome_parametro: str, valor: Any, usar_getdate: bool = False) -> None:
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
        adicionar("IDFatoControleContratosEuromidia", "id_controle_contrato", None)
        adicionar("BitAditivoContrato", "bit_aditivo", 0)
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

        adicionar("PeriodoInicio", "periodo_inicio", None)
        adicionar("PeriodoTermino", "periodo_termino", None)

        adicionar("IDDimUsuariosAprovacaoPreco", "id_usuario_aprovacao", None)
        adicionar("DataAprovacaoPreco", "data_aprovacao", None)
        adicionar("PrecoAprovado", "preco_aprovado", None)
        adicionar("DescontoAprovado", "desconto_aprovado", None)
        adicionar("ObservacoesAprovacao", "observacoes_aprovacao", None)
        adicionar("BitAutorizacaoDiretoria", "bit_autorizacao_diretoria", 0)

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

    id_status_card = _obter_id_status_card_por_codigo(status_card)
    id_usuario_atual = _id_usuario()

    id_empresa_proprietaria_negociacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=_id_empresa_usuario_or_403(),
    )

   
    estados_atuais = _listar_estado_atual_negociacao_card(int(id_card))
    if not estados_atuais:
        return

    chaves_processadas: set[tuple[int, int]] = set()

    for estado in estados_atuais:
        id_painel = _para_int_ou_none(estado.get("IDDimPaineisEuromidia"))
        id_face = _para_int_ou_none(estado.get("IDDimFacesPaineis"))

        if not id_painel or not id_face:
            continue

        chave_painel_face = (int(id_painel), int(id_face))

       
        if chave_painel_face in chaves_processadas:
            continue

        chaves_processadas.add(chave_painel_face)

        id_tabela_preco = _para_int_ou_none(estado.get("IDDimTabelaPrecosEuromidia"))

        custo_atual = _para_decimal_ou_none(estado.get("CustoTabela"))
        preco_atual = _para_decimal_ou_none(estado.get("ValorTabela"))
        margem_atual = _calcular_margem_percentual_negociacao(
            custo=custo_atual,
            preco=preco_atual,
        )

        novo_valor = _para_decimal_ou_none(estado.get("NovoValor"))
        percentual_desconto = _para_decimal_ou_none(estado.get("PercentualDesconto"))
        valor_venda_final = _para_decimal_ou_none(estado.get("ValorVendaFinal"))

       
        preco_proposto = novo_valor
        if preco_proposto is None:
            preco_proposto = valor_venda_final
        if preco_proposto is None:
            preco_proposto = preco_atual

        custo_proposto = custo_atual
        margem_proposta = _calcular_margem_percentual_negociacao(
            custo=custo_proposto,
            preco=preco_proposto,
        )

        tem_operacao_comercial = any(
            _tem_valor_informado(valor)
            for valor in (
                id_tabela_preco,
                novo_valor,
                percentual_desconto,
                valor_venda_final,
                preco_atual,
            )
        )

        if not tem_operacao_comercial:
            continue

        ultima_negociacao = _buscar_ultima_negociacao_preco_card(
            id_card=int(id_card),
            id_painel=int(id_painel),
            id_face=int(id_face),
        )

        assinatura_atual = _montar_assinatura_negociacao_preco(
            id_tabela_preco=id_tabela_preco,
            custo_atual=custo_atual,
            preco_atual=preco_atual,
            margem_atual=margem_atual,
            custo_proposto=custo_proposto,
            preco_proposto=preco_proposto,
            margem_proposta=margem_proposta,
            desconto_proposto=percentual_desconto,
        )

        assinatura_ultima = None
        if ultima_negociacao:
            assinatura_ultima = _montar_assinatura_negociacao_preco(
                id_tabela_preco=ultima_negociacao.get("IDDimTabelaPrecosEuromidia"),
                custo_atual=ultima_negociacao.get("CustoAtual"),
                preco_atual=ultima_negociacao.get("PrecoAtual"),
                margem_atual=ultima_negociacao.get("MargemAtual"),
                custo_proposto=ultima_negociacao.get("CustoProposto"),
                preco_proposto=ultima_negociacao.get("PrecoProposto"),
                margem_proposta=ultima_negociacao.get("MargemProposta"),
                desconto_proposto=ultima_negociacao.get("DescontoProposto"),
            )

       
        if assinatura_ultima is not None and assinatura_atual == assinatura_ultima:
            continue

        valores_insert = {
            "id_usuario": id_usuario_atual,
            "id_empresa_proprietaria": id_empresa_proprietaria_negociacao,
            "id_tabela_preco": id_tabela_preco,
            "id_empresa_relacionada": _para_int_ou_none(id_empresa_relacionada),
            "id_card": int(id_card),
            "id_fase_atual": _para_int_ou_none(id_fase_atual),
            "id_status_card": _para_int_ou_none(id_status_card),
            "observacoes_proposta": observacoes_proposta,
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
        }

        sql_insert, parametros_insert = _montar_insert_dinamico(valores_insert)
        db.session.execute(text(sql_insert), parametros_insert)



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
            p.QuantidadeFaces,
            rv.DataInicioReserva,
            rv.DataFimReserva
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] r
        LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] p
          ON TRY_CONVERT(int, p.IDDimPaineisEuromidia) = TRY_CONVERT(int, r.IDDimPaineisEuromidia)
        OUTER APPLY (
            SELECT TOP 1
                CONVERT(varchar(10), fo.DataInicio, 23) AS DataInicioReserva,
                CONVERT(varchar(10), fo.DataFim, 23) AS DataFimReserva
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
            WHERE UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = UPPER(LTRIM(RTRIM(COALESCE(r.CodFace, ''))))
              AND fo.CanceladoEm IS NULL
              AND fo.Status IN ('ATIVO', 'RESERVADO')
              AND COALESCE(fo.Observacao, '') LIKE (
                    '[KANBAN_CARD=' + CONVERT(varchar(20), :id_card) + '][COD_FACE=' + UPPER(LTRIM(RTRIM(COALESCE(r.CodFace, '')))) + ']%'
                  )
            ORDER BY fo.CriadoEm DESC, fo.DataAtualizacao DESC
        ) rv
        WHERE r.IDFatoKanbanCard = :id_card
          AND r.Ativo = 1
        ORDER BY r.Ordem ASC, r.IDFatoKanbanCardPainelFace ASC;
    """)
    rows = db.session.execute(sql, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)







def _obter_card_detalhe_payload(id_card: int) -> dict[str, Any]:
    print(f"[KANBAN][_obter_card_detalhe_payload] INICIO id_card={id_card}")
    current_app.logger.info("KANBAN: _obter_card_detalhe_payload iniciado. id_card=%s", id_card)

    card_escopo = _obter_card_autorizado(id_card)
    print(f"[KANBAN][_obter_card_detalhe_payload] card_escopo={card_escopo!r}")

    id_kanban = int(card_escopo.get("IDDimKanban") or 0)
    print(f"[KANBAN][_obter_card_detalhe_payload] id_kanban={id_kanban}")

    """
    Leio a versão diretamente da coluna real.
    Aqui eu não dependo de _card_tem_versao_concorrencia(),
    porque a tabela FatoKanbanCard já foi validada e possui essa coluna,
    e o card 36 já mostrou valor real no banco.
    """

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
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.Ativo = 1;
    """)

    card = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()

    if not card:
        abort(404, "Card não encontrado")

    card_dict = dict(card)

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

    """
    Primeiro tento converter o valor bruto retornado pelo driver.
    Se o pyodbc / SQLAlchemy devolver bytes, bytearray ou memoryview,
    _rowversion_para_hex deve resolver.
    """
    versao_hex = _rowversion_para_hex(valor_versao_bruta)

    """
    Se a conversão do bruto falhar, uso o valor já convertido pelo SQL Server.
    Isso evita perder a versão por incompatibilidade de tipo no driver.
    """
    if not versao_hex:
        versao_hex = _normalizar_hex_sql(valor_versao_hex_sql)

    """
    Só tento gerar/forçar uma versão se realmente não consegui ler nenhuma.
    Em cenário normal isso nem deveria acontecer, porque a linha já existe
    e já possui VersaoConcorrencia no banco.
    """
    if not versao_hex:
        print(
            f"[KANBAN][_obter_card_detalhe_payload] versão não lida no SELECT. "
            f"Tentando garantir versão para id_card={id_card}"
        )
        current_app.logger.warning(
            "KANBAN: versão do card não veio do SELECT. Tentando garantir versão. id_card=%s",
            id_card,
        )
        versao_hex = _garantir_versao_concorrencia_card(
            id_card=id_card,
            id_kanban=id_kanban,
        )

    print(f"[KANBAN][_obter_card_detalhe_payload] versao_hex_final={versao_hex!r}")
    current_app.logger.info(
        "KANBAN: detalhe card id=%s | versao_hex_final=%r",
        id_card,
        versao_hex,
    )

    """
    O front procura a versão em mais de um nome.
    Então eu preencho todos os aliases para não depender de um nome só.
    """
    card_dict["VersaoConcorrenciaHex"] = versao_hex
    card_dict["VersaoConcorrencia"] = versao_hex
    card_dict["versaoConcorrencia"] = versao_hex
    card_dict["versao_concorrencia"] = versao_hex

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

    retorno = {
        "ok": True,
        "card": card_dict,
        "kanban_cfg": _obter_cfg_kanban(id_kanban),
        "tags": _rows_para_dicts(tags),
        "notas": _rows_para_dicts(notas),
        "paineis_vinculados": paineis_vinculados,
        "painel_faces": paineis_vinculados,
        "painelFaces": paineis_vinculados,
    }

    print(
        f"[KANBAN][_obter_card_detalhe_payload] FIM id_card={id_card} "
        f"versao={retorno['card'].get('VersaoConcorrenciaHex')!r}"
    )

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
    paineis_catalogo = _obter_paineis_catalogo() if kanban_cfg["MostrarPainelFaceNoCard"] else []

    sql_totais = text(f"""
        SELECT
            c.IDDimKanbanFaseAtual AS IDDimKanbanFase,
            COUNT(1) AS QuantidadeCardsTotal
        FROM {TABELA_CARD} c
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
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
                {_sql_select_empresa_relacionada_card('c')},
                {_sql_select_usuario_relacionado_card('c')},
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
            FROM {TABELA_CARD} c
            {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
            WHERE c.IDDimKanban = :id_kanban
              AND c.Ativo = 1
              {_sql_filtro_status_card_visiveis('c')}
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
            IDEmpresaRelacionadaCard,
            IDUsuarioRelacionadoCard,
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

    sql_total = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD} c
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')};
    """)
    total = int(db.session.execute(sql_total, {"id_kanban": id_kanban, "id_fase": id_fase}).scalar() or 0)

    sql_cards = text(f"""
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
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
            c.IDFatoKanbanCard DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY;
    """)
    rows_cards = db.session.execute(
        sql_cards,
        {
            "id_kanban": id_kanban,
            "id_fase": id_fase,
            "offset": offset,
            "limit": limit,
        },
    ).mappings().all()

    cards = []
    for row in rows_cards:
        card = dict(row)
        card["VersaoConcorrenciaHex"] = _rowversion_para_hex(card.pop("VersaoConcorrencia", None))
        cards.append(card)

    payload = {
        "ok": True,
        "id_kanban": id_kanban,
        "id_fase": id_fase,
        "offset": offset,
        "limit": limit,
        "total": total,
        "cards": cards,
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

    if not isinstance(payload, dict):
        payload = {"ok": True}

    card_payload = payload.get("card")
    if not isinstance(card_payload, dict):
        card_payload = {}

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

    payload["card"] = card_payload

    if "painel_faces" not in payload:
        payload["painel_faces"] = payload.get("paineis_vinculados", []) or []

    if "painelFaces" not in payload:
        payload["painelFaces"] = payload.get("painel_faces", []) or []

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





@kanban_bp.route("/api/kanbans/<int:id_kanban>/cards", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_criar(id_kanban: int):
    etapa = "inicio"
    novo_id = None

    try:
        print("\n" + "=" * 120)
        print(f"[KANBAN][CRIAR_CARD] INICIO id_kanban={id_kanban}")
        print(f"[KANBAN][CRIAR_CARD] method={request.method} path={request.path}")
        print(f"[KANBAN][CRIAR_CARD] content_type={request.content_type}")
        print(f"[KANBAN][CRIAR_CARD] is_json={request.is_json}")

        etapa = "autenticacao"
        id_usuario = _assert_login()
        id_emp = _id_empresa_usuario_or_403()
        _obter_kanban_autorizado(id_kanban)
        print(f"[KANBAN][CRIAR_CARD] autenticacao_ok id_usuario={id_usuario} id_emp={id_emp}")

        etapa = "payload"
        payload = request.get_json(silent=True) or {}
        print(f"[KANBAN][CRIAR_CARD] payload_bruto={payload}")

        titulo = (payload.get("titulo") or "").strip()
        descricao = payload.get("descricao")
        id_fase = int(payload.get("id_fase") or 0)
        id_empresa_relacionada = payload.get("id_empresa")

        print(
            "[KANBAN][CRIAR_CARD] payload_tratado "
            f"titulo={titulo!r} "
            f"descricao={descricao!r} "
            f"id_fase={id_fase!r} "
            f"id_empresa_relacionada={id_empresa_relacionada!r}"
        )

        etapa = "validacao_titulo"
        if len(titulo) < 2:
            print("[KANBAN][CRIAR_CARD] ERRO validacao_titulo: titulo invalido")
            return jsonify({"ok": False, "msg": "Título inválido"}), 400

        etapa = "validacao_fase_obrigatoria"
        if not id_fase:
            print("[KANBAN][CRIAR_CARD] ERRO validacao_fase_obrigatoria: fase ausente")
            return jsonify({"ok": False, "msg": "Fase obrigatória"}), 400

        etapa = "validacao_fase_kanban"
        fase_valida = _validar_fase_do_kanban(id_kanban, id_fase)
        print(f"[KANBAN][CRIAR_CARD] fase_valida={fase_valida}")
        if not fase_valida:
            print("[KANBAN][CRIAR_CARD] ERRO validacao_fase_kanban: fase invalida para o kanban")
            return jsonify({"ok": False, "msg": "Fase inválida para este kanban"}), 400

        etapa = "validacao_empresa_relacionada"
        id_empresa_relacionada_int = None
        if id_empresa_relacionada not in (None, ""):
            try:
                id_empresa_relacionada_int = int(id_empresa_relacionada)
                print(f"[KANBAN][CRIAR_CARD] id_empresa_relacionada_int={id_empresa_relacionada_int}")
            except Exception as exc:
                print(f"[KANBAN][CRIAR_CARD] ERRO empresa invalida ao converter: {exc}")
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

            print(f"[KANBAN][CRIAR_CARD] empresa_existe={empresa_existe}")

            if not empresa_existe:
                print("[KANBAN][CRIAR_CARD] ERRO empresa nao encontrada")
                return jsonify({"ok": False, "msg": "Empresa não encontrada"}), 400

        etapa = "descobrir_colunas_dinamicas"
        nome_coluna_empresa = _nome_coluna_empresa_relacionada_card()

        coluna_id_dim_usuarios_existe = _coluna_existe(TABELA_CARD, "IDDimUsuarios")
        coluna_iddimkanbanorigem_existe = _coluna_existe(TABELA_CARD, "IDDimKanbanOrigem")

        print(
            "[KANBAN][CRIAR_CARD] colunas_dinamicas "
            f"nome_coluna_empresa={nome_coluna_empresa!r} "
            f"coluna_id_dim_usuarios_existe={coluna_id_dim_usuarios_existe} "
            f"coluna_iddimkanbanorigem_existe={coluna_iddimkanbanorigem_existe}"
        )

        etapa = "montagem_insert"
        print(f"[KANBAN][CRIAR_CARD] id_usuario_logado={id_usuario}")
        print("[KANBAN][CRIAR_CARD] vou gravar id_usuario em IDVendedorUsuario e, se existir, também em IDDimUsuarios")

        status_card_inicial = _obter_status_card_para_fase(id_fase)
        id_status_card_inicial = _obter_id_status_card_por_codigo(status_card_inicial)

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
            params["id_empresa_relacionada"] = id_empresa_relacionada_int

        if coluna_id_dim_usuarios_existe:
            colunas.append("IDDimUsuarios")
            valores.append(":id_dim_usuarios")
            params["id_dim_usuarios"] = int(id_usuario)

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

        print(f"[KANBAN][CRIAR_CARD] tabela_card={TABELA_CARD}")
        print(f"[KANBAN][CRIAR_CARD] colunas_insert={colunas}")
        print(f"[KANBAN][CRIAR_CARD] valores_insert={valores}")
        print(f"[KANBAN][CRIAR_CARD] params_insert={params}")
        print(f"[KANBAN][CRIAR_CARD] sql_insert=\n{sql_insert}")

        etapa = "executar_insert"
        novo_id = db.session.execute(text(sql_insert), params).scalar()
        print(f"[KANBAN][CRIAR_CARD] novo_id={novo_id}")

        if not novo_id:
            raise RuntimeError("O INSERT não retornou IDFatoKanbanCard.")

        etapa = "aplicar_tag_em_atendimento"
        _garantir_tag_em_atendimento_no_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=id_emp,
            falhar_se_nao_existir=True,
        )

        etapa = "snapshot_depois"
        snapshot_depois = _obter_snapshot_card_log(int(novo_id), incluir_inativo=True)
        print(f"[KANBAN][CRIAR_CARD] snapshot_depois={snapshot_depois}")

        etapa = "registrar_log_card"
        _registrar_log_card(
            id_card=int(novo_id),
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_CRIADO",
            id_fase_para=id_fase,
            payload_depois=snapshot_depois,
            observacao="Card criado no kanban.",
            tabela_origem=TABELA_CARD,
            id_registro_origem=int(novo_id),
        )
        print("[KANBAN][CRIAR_CARD] log_card_ok")

        etapa = "commit"
        db.session.commit()
        print("[KANBAN][CRIAR_CARD] commit_ok")

    except Exception as exc:
        db.session.rollback()
        print(
            f"[KANBAN][CRIAR_CARD] ERRO antes_pos_processamento "
            f"etapa={etapa} novo_id={novo_id} erro={repr(exc)}"
        )
        current_app.logger.exception(
            "Erro ao criar card no kanban. etapa=%s id_kanban=%s novo_id=%s",
            etapa,
            id_kanban,
            novo_id,
        )
        return jsonify(
            {
                "ok": False,
                "msg": f"Erro ao criar card na etapa '{etapa}': {str(exc)}",
                "etapa": etapa,
                "novo_id": int(novo_id) if novo_id else None,
            }
        ), 500

    try:
        etapa = "invalidar_cache"
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=int(novo_id))
        print("[KANBAN][CRIAR_CARD] invalidar_cache_ok")

        etapa = "obter_detalhe"
        detalhe = _obter_card_detalhe_payload(int(novo_id))
        print(
            f"[KANBAN][CRIAR_CARD] detalhe_keys="
            f"{list(detalhe.keys()) if isinstance(detalhe, dict) else type(detalhe)}"
        )
        print(
            f"[KANBAN][CRIAR_CARD] detalhe_card="
            f"{detalhe.get('card') if isinstance(detalhe, dict) else None}"
        )

        etapa = "emitir_socket"
        payload_socket = {
            "card": detalhe["card"],
            "id_fase": id_fase,
            "tags": detalhe["tags"],
            "notas": detalhe["notas"],
            "paineis_vinculados": detalhe.get("paineis_vinculados", []),
        }
        print(f"[KANBAN][CRIAR_CARD] payload_socket={payload_socket}")

        _emitir_evento_kanban(
            id_kanban,
            "card_criado",
            payload_socket,
        )
        print("[KANBAN][CRIAR_CARD] emitir_socket_ok")

        print(f"[KANBAN][CRIAR_CARD] SUCESSO id_card={novo_id}")
        print("=" * 120 + "\n")

        return jsonify(
            {
                "ok": True,
                "IDFatoKanbanCard": int(novo_id),
                "card": detalhe["card"],
            }
        )

    except Exception as exc:
        print(
            f"[KANBAN][CRIAR_CARD] ERRO pos_commit "
            f"etapa={etapa} novo_id={novo_id} erro={repr(exc)}"
        )
        current_app.logger.exception(
            "Card criado, mas houve falha no pos-processamento. etapa=%s id_kanban=%s novo_id=%s",
            etapa,
            id_kanban,
            novo_id,
        )

        return jsonify(
            {
                "ok": True,
                "IDFatoKanbanCard": int(novo_id),
                "msg": f"Card criado com sucesso, mas houve falha após gravar na etapa '{etapa}': {str(exc)}",
                "etapa_pos_commit": etapa,
            }
        ), 200






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
) -> bool:
    """
    Decide se houve mudança real na negociação de preço.

    Fundamento:
    - histórico não deve crescer por reenvio do mesmo payload
    - comparação decimal precisa ser normalizada para evitar falso positivo por formato
    - a decisão deve ser feita por assinatura comercial estável, não por texto bruto do front
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
    )

    return assinatura_atual != assinatura_ultima





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
    Grava histórico de negociação de preço sem duplicar linha quando nada mudou.

    Fundamento desta versão:
    - a fonte da verdade é a tabela operacional FatoKanbanCardPainelFace já salva nesta transação
    - o histórico FatoKanbanNegociacaoPreco só recebe novo registro quando a assinatura comercial mudou
    - salvar o card novamente, sem alterar tabela/preço/desconto/margem, não gera nova linha
    """

    if not vinculos_preparados:
        return

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

    def _calcular_margem_percentual(custo: Any, preco: Any) -> Decimal | None:
        custo_dec = _valor_decimal(custo)
        preco_dec = _valor_decimal(preco)

        if custo_dec is None:
            return None
        if preco_dec in (None, Decimal("0")):
            return None

        return ((preco_dec - custo_dec) / preco_dec) * Decimal("100")

    def _montar_insert_dinamico(valores: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        colunas: list[str] = []
        marcadores: list[str] = []
        parametros: dict[str, Any] = {}

        def adicionar(nome_coluna: str, nome_parametro: str, valor: Any, usar_getdate: bool = False) -> None:
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
        adicionar("IDFatoControleContratosEuromidia", "id_controle_contrato", None)
        adicionar("BitAditivoContrato", "bit_aditivo", 0)
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

        adicionar("PeriodoInicio", "periodo_inicio", None)
        adicionar("PeriodoTermino", "periodo_termino", None)

        adicionar("IDDimUsuariosAprovacaoPreco", "id_usuario_aprovacao", None)
        adicionar("DataAprovacaoPreco", "data_aprovacao", None)
        adicionar("PrecoAprovado", "preco_aprovado", None)
        adicionar("DescontoAprovado", "desconto_aprovado", None)
        adicionar("ObservacoesAprovacao", "observacoes_aprovacao", None)
        adicionar("BitAutorizacaoDiretoria", "bit_autorizacao_diretoria", 0)

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

    id_status_card = _obter_id_status_card_por_codigo(status_card)
    id_usuario_atual = _id_usuario()
    id_empresa_proprietaria_negociacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=_id_empresa_usuario_or_403(),
    )

    estados_atuais = _listar_estado_atual_negociacao_card(int(id_card))
    if not estados_atuais:
        return

    chaves_processadas: set[tuple[int, int]] = set()

    for estado in estados_atuais:
        id_painel = _para_int_ou_none(estado.get("IDDimPaineisEuromidia"))
        id_face = _para_int_ou_none(estado.get("IDDimFacesPaineis"))
        if not id_painel or not id_face:
            continue

        chave_painel_face = (int(id_painel), int(id_face))
        if chave_painel_face in chaves_processadas:
            continue
        chaves_processadas.add(chave_painel_face)

        id_tabela_preco = _para_int_ou_none(estado.get("IDDimTabelaPrecosEuromidia"))
        custo_atual = _para_decimal_ou_none(estado.get("CustoTabela"))
        preco_atual = _para_decimal_ou_none(estado.get("ValorTabela"))
        margem_atual = _calcular_margem_percentual(custo_atual, preco_atual)

        novo_valor = _para_decimal_ou_none(estado.get("NovoValor"))
        percentual_desconto = _para_decimal_ou_none(estado.get("PercentualDesconto"))
        valor_venda_final = _para_decimal_ou_none(estado.get("ValorVendaFinal"))

        preco_proposto = novo_valor
        if preco_proposto is None:
            preco_proposto = valor_venda_final
        if preco_proposto is None:
            preco_proposto = preco_atual

        custo_proposto = custo_atual
        margem_proposta = _calcular_margem_percentual(custo_proposto, preco_proposto)

        tem_operacao_comercial = any(
            _tem_valor_informado(valor)
            for valor in (
                id_tabela_preco,
                novo_valor,
                percentual_desconto,
                valor_venda_final,
                preco_atual,
            )
        )
        if not tem_operacao_comercial:
            continue

        ultima_negociacao = _buscar_ultima_negociacao_preco_card(
            id_card=int(id_card),
            id_painel=int(id_painel),
            id_face=int(id_face),
        )

        if not _negociacao_preco_foi_alterada(
            ultima_negociacao,
            id_tabela_preco=id_tabela_preco,
            custo_atual=custo_atual,
            preco_atual=preco_atual,
            margem_atual_percentual=margem_atual,
            custo_proposto=custo_proposto,
            preco_proposto=preco_proposto,
            margem_proposta_percentual=margem_proposta,
            desconto_proposto=percentual_desconto,
        ):
            continue

        valores_insert = {
            "id_usuario": id_usuario_atual,
            "id_empresa_proprietaria": id_empresa_proprietaria_negociacao,
            "id_tabela_preco": id_tabela_preco,
            "id_empresa_relacionada": _para_int_ou_none(id_empresa_relacionada),
            "id_card": int(id_card),
            "id_fase_atual": _para_int_ou_none(id_fase_atual),
            "id_status_card": _para_int_ou_none(id_status_card),
            "observacoes_proposta": observacoes_proposta,
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
        }

        sql_insert, parametros_insert = _montar_insert_dinamico(valores_insert)
        db.session.execute(text(sql_insert), parametros_insert)





@kanban_bp.route("/api/cards/<int:id_card>", methods=["PUT"])
@login_required
@limiter.limit("120/minute")
def api_card_atualizar(id_card: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()

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
        versao_concorrencia = payload.get("versao_concorrencia")
        painel_faces_payload = payload.get("painel_faces")

        if not titulo:
            return jsonify({"ok": False, "msg": "Título do card é obrigatório."}), 400

        has_versao = _card_tem_versao_concorrencia()
        versao_concorrencia_bytes = None

        if has_versao:
            versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia)
            if not versao_concorrencia_bytes:
                detalhe_atual = _obter_card_detalhe_payload(id_card)
                return (
                    jsonify(
                        {
                            "ok": False,
                            "msg": "Versão de concorrência inválida ou ausente.",
                            "card_atual": detalhe_atual.get("card"),
                        }
                    ),
                    409,
                )

        snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        campos_update: list[str] = []
        params_update: dict[str, Any] = {
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
            params_update["id_empresa_relacionada"] = (
                int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None
            )

        if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
            campos_update.append("AtualizadoEm = GETDATE()")

        output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
        where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""

        if has_versao:
            params_update["versao_concorrencia"] = versao_concorrencia_bytes

        if not campos_update:
            return jsonify({"ok": False, "msg": "Nenhum campo disponível para atualização no card."}), 400

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET {', '.join(campos_update)}
            OUTPUT
                INSERTED.IDFatoKanbanCard,
                INSERTED.IDDimKanban,
                INSERTED.IDDimKanbanFaseAtual,
                INSERTED.Titulo,
                INSERTED.Descricao,
                INSERTED.StatusCard,
                INSERTED.IDEmpresaProprietaria{output_versao}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1{where_versao};
        """)

        row_upd = db.session.execute(sql_upd, params_update).mappings().first()

        if not row_upd:
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return (
                jsonify(
                    {
                        "ok": False,
                        "msg": "Este card foi alterado por outro usuário. Reabra o card antes de salvar novamente.",
                        "card_atual": detalhe_atual.get("card"),
                    }
                ),
                409,
            )

        vinculos_preparados: list[dict[str, Any]] = []
        reservas_criadas = 0

        if isinstance(painel_faces_payload, list):

            def _assinatura_estado_operacional(estados: list[dict[str, Any]]) -> tuple:
                def _n_int(valor: Any) -> int | None:
                    if valor in (None, ""):
                        return None
                    try:
                        return int(valor)
                    except Exception:
                        return None

                def _n_dec(valor: Any, casas: str = "0.0001") -> Decimal | None:
                    dec = _valor_decimal(valor)
                    if dec is None:
                        return None
                    try:
                        return dec.quantize(Decimal(casas))
                    except Exception:
                        return dec

                assinatura: list[tuple] = []

                for estado in estados:
                    assinatura.append(
                        (
                            _n_int(estado.get("IDDimPaineisEuromidia")),
                            _n_int(estado.get("IDDimFacesPaineis")),
                            _n_int(estado.get("IDDimTabelaPrecosEuromidia")),
                            _n_dec(estado.get("CustoTabela")),
                            _n_dec(estado.get("ValorTabela")),
                            _n_dec(estado.get("NovoValor")),
                            _n_dec(estado.get("PercentualDesconto")),
                            _n_dec(estado.get("ValorVendaFinal")),
                            _n_dec(estado.get("MargemValor")),
                            _n_dec(estado.get("MargemPercentual")),
                        )
                    )

                return tuple(
                    sorted(
                        assinatura,
                        key=lambda item: tuple((parte is None, str(parte)) for parte in item),
                    )
                )

            estado_operacional_antes = _listar_estado_atual_negociacao_card(int(id_card))

            vinculos_preparados = _preparar_vinculos_painel_faces(
                painel_faces_payload=painel_faces_payload,
                id_empresa_proprietaria=id_emp,
            )

            _salvar_vinculos_painel_face_card(
                id_card=id_card,
                vinculos_preparados=vinculos_preparados,
                id_usuario=id_usuario,
                id_empresa_proprietaria=id_emp,
            )

            estado_operacional_depois = _listar_estado_atual_negociacao_card(int(id_card))

            houve_alteracao_operacional = (
                _assinatura_estado_operacional(estado_operacional_antes)
                != _assinatura_estado_operacional(estado_operacional_depois)
            )

            if houve_alteracao_operacional:
                _registrar_negociacao_preco_card(
                    id_card=id_card,
                    id_kanban=id_kanban,
                    id_fase_atual=id_fase_atual,
                    status_card=row_upd.get("StatusCard") or card_atual.get("StatusCard"),
                    id_empresa_relacionada=(
                        int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None
                    ),
                    vinculos_preparados=vinculos_preparados,
                )

            reservas_criadas = _criar_reservas_painel_faces_kanban(
                id_card=int(id_card),
                titulo_card=titulo,
                id_empresa_relacionada=(
                    int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None
                ),
                painel_faces_payload=painel_faces_payload,
                vinculos_preparados=vinculos_preparados,
                id_usuario=int(id_usuario),
                id_empresa_proprietaria=int(id_emp),
            )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_ATUALIZADO",
            observacao="Card atualizado pelo modal.",
            tabela_origem=TABELA_CARD,
            id_registro_origem=int(id_card),
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
                "id_card": id_card,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
            },
        )

        return jsonify(
            {
                "ok": True,
                "msg": (
                    f"Card atualizado com sucesso. {int(reservas_criadas)} reserva(s) criada(s)."
                    if int(reservas_criadas or 0) > 0
                    else "Card atualizado com sucesso."
                ),
                "reservas_criadas": int(reservas_criadas or 0),
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
            }
        )

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao atualizar card: {str(exc)}"}), 500





@kanban_bp.route("/api/cards/<int:id_card>/mover", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_mover(id_card: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    try:
        row = _obter_card_autorizado(id_card)
        if not row:
            return jsonify({"ok": False, "msg": "Card não encontrado."}), 404

        id_kanban = int(row.get("IDDimKanban") or 0)
        id_fase_de = int(row.get("IDDimKanbanFaseAtual") or 0)

        payload = request.get_json(silent=True) or {}
        id_fase_para = int(payload.get("id_fase_para") or 0)
        posicao = str(payload.get("posicao") or "LAST").strip().upper()
        observacao = (payload.get("observacao") or "").strip()
        nota_movimento = (payload.get("nota_movimento") or "").strip()
        versao_concorrencia = payload.get("versao_concorrencia")

        if not id_fase_para:
            return jsonify({"ok": False, "msg": "Fase de destino inválida."}), 400

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
            return jsonify({"ok": False, "msg": "Fase de destino não encontrada."}), 404

        snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        has_ordem = _coluna_existe(TABELA_CARD, "OrdemNaFase")
        has_atualizado = _coluna_existe(TABELA_CARD, "AtualizadoEm")
        has_versao = _card_tem_versao_concorrencia()

        versao_concorrencia_bytes = None
        if has_versao:
            versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia)
            if not versao_concorrencia_bytes:
                detalhe_atual = _obter_card_detalhe_payload(id_card)
                return (
                    jsonify(
                        {
                            "ok": False,
                            "msg": "Versão de concorrência inválida ou ausente.",
                            "card_atual": detalhe_atual.get("card"),
                        }
                    ),
                    409,
                )

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
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return (
                jsonify(
                    {
                        "ok": False,
                        "msg": "Este card foi alterado ou movido por outro usuário. Recarregue antes de tentar novamente.",
                        "card_atual": detalhe_atual.get("card"),
                    }
                ),
                409,
            )

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
        {
            "id_tag": id_tag,
            "id_kanban": id_kanban,
        },
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
    existe = db.session.execute(
        sql_dup,
        {
            "id_card": id_card,
            "id_tag": id_tag,
        },
    ).scalar()

    alterou = False

    if not existe:
        sql_insert = text("""
            INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
                (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
            VALUES
                (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa);
        """)
        db.session.execute(
            sql_insert,
            {
                "id_card": id_card,
                "id_tag": id_tag,
                "id_usuario": id_usuario,
                "id_empresa": card.get("IDEmpresaProprietaria"),
            },
        )
        db.session.commit()
        alterou = True

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    detalhe = _obter_card_detalhe_payload(id_card)

    if alterou:
        _emitir_evento_kanban(
            id_kanban,
            "card_tag_adicionada",
            {
                "id_card": id_card,
                "id_tag": id_tag,
                "id_usuario_acao": id_usuario,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
            },
        )

    return jsonify(
        {
            "ok": True,
            "id_card": id_card,
            "id_tag": id_tag,
            "card": detalhe.get("card"),
            "tags": detalhe.get("tags", []),
        }
    )




@kanban_bp.route("/api/cards/<int:id_card>/tags/<int:id_tag>", methods=["DELETE"])
@login_required
@limiter.limit("180/minute")
def api_card_tag_remover(id_card: int, id_tag: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)

    tag_em_atendimento = _obter_tag_em_atendimento(id_kanban)
    if tag_em_atendimento and int(tag_em_atendimento.get("IDDimKanbanTag") or 0) == int(id_tag):
        return jsonify({
            "ok": False,
            "msg": "A tag 'Em Atendimento' é automática e só pode ser removida quando o card for concluído ou removido do kanban.",
        }), 400

    alterou = _remover_tag_do_card(
        id_card=int(id_card),
        id_tag=int(id_tag),
        id_usuario=int(id_usuario),
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
    print(f"[KANBAN][api_card_inativar] INICIO id_card={id_card}")

    id_usuario = _assert_login()
    print(f"[KANBAN][api_card_inativar] id_usuario={id_usuario}")

    card_escopo = _obter_card_autorizado(id_card)
    print(f"[KANBAN][api_card_inativar] card_escopo={card_escopo!r}")

    id_emp = _id_empresa_usuario_or_403()
    print(f"[KANBAN][api_card_inativar] id_emp={id_emp}")

    id_kanban = int(card_escopo.get("IDDimKanban") or 0)
    print(f"[KANBAN][api_card_inativar] id_kanban={id_kanban}")

    payload = request.get_json(silent=True) or {}
    print(f"[KANBAN][api_card_inativar] payload={payload!r}")

    motivo_informado = payload.get("motivo")
    descricao = (payload.get("descricao") or "").strip()

    print(
        f"[KANBAN][api_card_inativar] motivo_informado={motivo_informado!r} descricao={descricao!r}"
    )

    motivo_normalizado = _normalizar_motivo_encerramento_card(motivo_informado)
    print(f"[KANBAN][api_card_inativar] motivo_normalizado={motivo_normalizado!r}")

    if not motivo_normalizado:
        print("[KANBAN][api_card_inativar] motivo inválido -> retorno 400")
        return jsonify({"ok": False, "msg": "Motivo inválido"}), 400

    codigo_motivo = _normalizar_codigo_dominio(motivo_normalizado.get("Codigo"))
    if codigo_motivo in {"OUTROS", "OUTRO_MOTIVO"} and len(descricao) < 2:
        print("[KANBAN][api_card_inativar] motivo OUTROS sem descrição suficiente -> retorno 400")
        return jsonify({"ok": False, "msg": "Descreva o motivo"}), 400

    print("[KANBAN][api_card_inativar] capturando snapshot_antes")
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

    print(f"[KANBAN][api_card_inativar] executando SQL card ativo id_card={id_card}")
    row = db.session.execute(sql_card, {"id_card": id_card}).mappings().first()
    print(f"[KANBAN][api_card_inativar] row={row!r}")

    if not row:
        print("[KANBAN][api_card_inativar] card não encontrado ou já inativo -> retorno 404")
        return jsonify({"ok": False, "msg": "Card não encontrado ou já inativo"}), 404

    if int(row["IDDimKanban"]) != id_kanban:
        print(
            f"[KANBAN][api_card_inativar] card fora do escopo -> row.IDDimKanban={row['IDDimKanban']} id_kanban={id_kanban}"
        )
        return jsonify({"ok": False, "msg": "Card fora do escopo do usuário"}), 403

    id_fase_atual = int(row["IDDimKanbanFaseAtual"] or 0)
    id_fase_para_movimento = 9
    id_empresa_card = row.get("IDEmpresaProprietaria")
    id_motivo_encerramento = int(motivo_normalizado.get("IDDimKanbanMotivoEncerramento") or 0)
    motivo_texto = str(motivo_normalizado.get("Descricao") or "").strip()

    observacao_inativacao = f"[INATIVADO] Motivo: {motivo_texto}" + (f" | {descricao}" if descricao else "")
    print(
        "[KANBAN][api_card_inativar] dados básicos -> "
        f"id_fase_atual={id_fase_atual} id_fase_para_movimento={id_fase_para_movimento} "
        f"id_empresa_card={id_empresa_card} "
        f"id_motivo_encerramento={id_motivo_encerramento} "
        f"motivo_texto={motivo_texto!r} observacao_inativacao={observacao_inativacao!r}"
    )

    id_status_inativacao = _resolver_id_status_card_movimento(card_inativado=True)
    status_inativacao_texto = "CANCELADO"

    print(
        f"[KANBAN][api_card_inativar] id_status_inativacao={id_status_inativacao!r} "
        f"status_inativacao_texto={status_inativacao_texto!r}"
    )

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

        print(f"[KANBAN][api_card_inativar] campos_update={campos_update!r}")
        print(f"[KANBAN][api_card_inativar] params_update={params_update!r}")

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET {', '.join(campos_update)}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1;
        """)

        print("[KANBAN][api_card_inativar] executando UPDATE do card")
        resultado_upd = db.session.execute(sql_upd, params_update)
        print(
            f"[KANBAN][api_card_inativar] UPDATE executado rowcount={getattr(resultado_upd, 'rowcount', None)}"
        )

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
        print(f"[KANBAN][api_card_inativar] params_mov={params_mov!r}")

        print("[KANBAN][api_card_inativar] inserindo movimento")
        row_mov = db.session.execute(sql_ins_mov, params_mov).mappings().first()
        print(f"[KANBAN][api_card_inativar] row_mov={row_mov!r}")

        print("[KANBAN][api_card_inativar] inserindo histórico de encerramento")
        row_hist_enc = _registrar_historico_encerramento_card(
            id_card=id_card,
            id_motivo_encerramento=id_motivo_encerramento,
            nome_motivo=motivo_texto,
            id_fase=id_fase_atual,
            id_usuario=id_usuario,
            observacoes=descricao,
        )
        print(f"[KANBAN][api_card_inativar] row_hist_enc={row_hist_enc!r}")

        row_observacao = _registrar_observacao_historica_card(
            id_card=id_card,
            texto_observacao=observacao_inativacao,
            id_usuario=id_usuario,
            id_status_card=id_status_inativacao,
            id_fase=id_fase_atual,
        )
        print(f"[KANBAN][api_card_inativar] row_observacao={row_observacao!r}")

        _remover_tag_em_atendimento_do_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_usuario=int(id_usuario),
        )

        print("[KANBAN][api_card_inativar] capturando snapshot_depois")
        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        print("[KANBAN][api_card_inativar] registrando log do card")
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
        print("[KANBAN][api_card_inativar] log registrado com sucesso")

        print("[KANBAN][api_card_inativar] commit")
        db.session.commit()

        print("[KANBAN][api_card_inativar] invalidando cache/kanban")
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

        print("[KANBAN][api_card_inativar] emitindo evento socket")
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
            },
        )

        print(
            f"[KANBAN][api_card_inativar] SUCESSO id_card={id_card} motivo={motivo_texto!r} codigo={motivo_normalizado.get('Codigo')!r}"
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
            }
        )
    except Exception as exc:
        print(f"[KANBAN][api_card_inativar] ERRO -> {exc}")
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

