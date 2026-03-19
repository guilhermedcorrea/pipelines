from flask import Blueprint, render_template, request, jsonify, abort, current_app,redirect,url_for
from flask_login import login_required, current_user
from sqlalchemy import text
from ..extensions import db, limiter
from collections.abc import Mapping



kanban_bp = Blueprint("kanban", __name__)





def _id_empresa_usuario() -> int:
  
    return int(getattr(current_user, "IDEmpresaProprietaria", 0) or 0)


@kanban_bp.route("/atendimento", methods=["GET"])
@login_required
def atendimento_redirect():
    _assert_login()

    id_emp = _id_empresa_usuario()
    if not id_emp:
        abort(403, "Usuário sem IDEmpresaProprietaria definida")

  
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



def _rows_para_dicts(rows):
    """Converte lista/iterável de RowMapping (ou Mapping) em lista de dict puro."""
    return [dict(r) if isinstance(r, Mapping) else r for r in (rows or [])]

def _row_para_dict(row):
    """Converte um único RowMapping em dict puro."""
    return dict(row) if isinstance(row, Mapping) else row





def _id_usuario() -> int:
    """Retorna o ID do usuário no seu formato (IDDimUsuarios)."""
    return int(getattr(current_user, "IDDimUsuarios", 0) or 0)


def _assert_login() -> int:
    """Garante que tem usuário logado e retorna o IDDimUsuarios."""
    if not getattr(current_user, "is_authenticated", False):
        abort(401)
    uid = _id_usuario()
    if not uid:
        abort(401)
    return uid


def _log_debug_usuario():
    """Log de debug sem estourar por causa de método/propriedade."""
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




@kanban_bp.route("/kanbans", methods=["GET"])
@login_required
def kanbans_lista():
    _log_debug_usuario()
    _assert_login()

    sql = text("""
        SELECT
            IDDimKanban,
            NomeKanban,
            Descricao,
            Ativo,
            CriadoEm
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
        ORDER BY CriadoEm DESC;
    """)
    kanbans = db.session.execute(sql).mappings().all()
    return render_template("kanban/kanbans_lista.html", kanbans=kanbans)



@kanban_bp.route("/<int:id_kanban>", methods=["GET"])
@login_required
def kanban_view(id_kanban: int):
    _assert_login()

    try:
        sql = text("""
            SELECT
                IDDimKanban,
                NomeKanban,
                Descricao
            FROM [Kanban].[Silver].[DimKanban]
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)

        row = db.session.execute(sql, {"id_kanban": id_kanban}).mappings().first()
        if not row:
            abort(404, "Kanban não encontrado")

        kanban = dict(row)

        return render_template("kanban/kanban_view.html", kanban=kanban)

    except Exception as e:
        current_app.logger.exception("Erro no kanban_view id_kanban=%s: %s", id_kanban, e)
        return render_template("erros/500.html"), 500






@kanban_bp.route("/<int:id_kanban>/tags", methods=["GET"])
@login_required
def kanban_tags_view(id_kanban: int):
    _assert_login()

    sql_kanban = text("""
        SELECT IDDimKanban, NomeKanban
        FROM [Kanban].[Silver].[DimKanban]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    kanban = db.session.execute(sql_kanban, {"id_kanban": id_kanban}).mappings().first()
    if not kanban:
        abort(404, "Kanban não encontrado")

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
def api_kanbans_listar():
    _assert_login()

    sql = text("""
        SELECT IDDimKanban, NomeKanban, Descricao, Ativo, CriadoEm
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
        ORDER BY CriadoEm DESC;
    """)
    rows = db.session.execute(sql).mappings().all()
    return jsonify({"ok": True, "kanbans": list(rows)})



@kanban_bp.route("/api/kanbans", methods=["POST"])
@login_required
def api_kanban_criar():
    id_usuario = _assert_login()

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
            (:nome, :descricao, 1, GETDATE(), :id_usuario, NULL);
    """)
    novo_id = db.session.execute(sql, {
        "nome": nome[:100],
        "descricao": descricao[:3000] if descricao else None,
        "id_usuario": id_usuario,
    }).scalar()

    db.session.commit()
    return jsonify({"ok": True, "IDDimKanban": int(novo_id)})



@kanban_bp.route("/api/kanbans/<int:id_kanban>/dados", methods=["GET"])
@login_required
def api_kanban_dados(id_kanban: int):
    _assert_login()

    from collections.abc import Mapping

    def _rows_para_dicts(rows):
        """Converte lista/iterável de RowMapping (ou Mapping) em lista de dict puro."""
        return [dict(r) if isinstance(r, Mapping) else r for r in (rows or [])]


    sql_kanban = text("""
        SELECT
            IDDimKanban,
            IDEmpresaProprietaria,
            BitPrincipal
        FROM [Kanban].[Silver].[DimKanban]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    k = db.session.execute(sql_kanban, {"id_kanban": id_kanban}).mappings().first()
    if not k:
        return jsonify({"ok": False, "msg": "Kanban não encontrado"}), 404

    try:
        id_emp_prop = int(k.get("IDEmpresaProprietaria") or 0)
    except Exception:
        id_emp_prop = 0

    try:
        bit_principal = int(k.get("BitPrincipal") or 0)
    except Exception:
        bit_principal = 0

    mostrar_painel_face_no_card = (id_emp_prop == 3) and (bit_principal == 1)

    kanban_cfg = {
        "IDDimKanban": int(k.get("IDDimKanban") or id_kanban),
        "IDEmpresaProprietaria": k.get("IDEmpresaProprietaria"),
        "BitPrincipal": k.get("BitPrincipal"),
        "MostrarPainelFaceNoCard": bool(mostrar_painel_face_no_card),
    }


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


    sql_cards = text("""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,

            c.IDEmpresaProprietaria,

            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ       AS EmpresaCNPJ,
            e.CNAE       AS EmpresaCNAE,

            cn.Classe    AS EmpresaClasse,
            cn.Setor     AS EmpresaSetor

        FROM [Kanban].[Silver].[FatoKanbanCard] c
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] e
          ON e.IDEmpresa = c.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimCnaes] cn
          ON cn.cnaepadrao = e.CNAE

        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          AND c.StatusCard IN ('ATIVO','CONCLUIDO','PERDIDO','CANCELADO')
        ORDER BY c.CriadoEm DESC;
    """)
    cards = db.session.execute(sql_cards, {"id_kanban": id_kanban}).mappings().all()


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


    paineis = []
    if mostrar_painel_face_no_card:
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
            ORDER BY
                p.UF ASC,
                p.Cidade ASC,
                p.Tipo ASC,
                p.CodPonto ASC;
        """)
        paineis = db.session.execute(sql_paineis).mappings().all()

    return jsonify({
        "ok": True,
        "kanban_cfg": dict(kanban_cfg),
        "fases": _rows_para_dicts(fases),
        "cards": _rows_para_dicts(cards),
        "tags": _rows_para_dicts(tags),
        "card_tags": _rows_para_dicts(card_tags),
        "paineis": _rows_para_dicts(paineis),
    })






@kanban_bp.route("/api/empresas", methods=["GET"])
@login_required
def api_empresas_lista():
    _assert_login()

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
        WHERE
            e.RazaoSocial IS NOT NULL
            AND LTRIM(RTRIM(e.RazaoSocial)) <> ''
        ORDER BY e.RazaoSocial ASC;
    """)

    rows = db.session.execute(sql).mappings().all()
    return jsonify({"ok": True, "empresas": [dict(r) for r in rows]})







@kanban_bp.route("/api/kanbans/<int:id_kanban>/fases", methods=["POST"])
@login_required
def api_fase_criar(id_kanban: int):
    id_usuario = _assert_login()

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or "").strip()
    tipo = (payload.get("tipo") or "ATIVA").strip().upper()
    ordem = payload.get("ordem")

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da fase inválido"}), 400
    if tipo not in ("ATIVA", "SUCESSO", "PERDA"):
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

 
    sql_exists = text("""
        SELECT 1
        FROM [Kanban].[Silver].[DimKanban]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    ok = db.session.execute(sql_exists, {"id_kanban": id_kanban}).scalar()
    if not ok:
        return jsonify({"ok": False, "msg": "Kanban não encontrado"}), 404

    sql = text("""
        INSERT INTO [Kanban].[Silver].[DimKanbanFase]
            (IDDimKanban, NomeFase, OrdemFase, TipoFase, Ativo, CriadoEm, IDUsuario, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDDimKanbanFase
        VALUES
            (:id_kanban, :nome, :ordem, :tipo, 1, GETDATE(), :id_usuario, NULL);
    """)
    novo_id = db.session.execute(sql, {
        "id_kanban": id_kanban,
        "nome": nome[:100],
        "ordem": ordem,
        "tipo": tipo,
        "id_usuario": id_usuario
    }).scalar()

    db.session.commit()
    return jsonify({"ok": True, "IDDimKanbanFase": int(novo_id)})



@kanban_bp.route("/api/fases/reordenar", methods=["POST"])
@login_required
def api_fases_reordenar():
    _assert_login()

    payload = request.get_json(silent=True) or {}
    fases = payload.get("fases") or []

    if not isinstance(fases, list) or not fases:
        return jsonify({"ok": False, "msg": "Payload inválido"}), 400

    sql = text("""
        UPDATE [Kanban].[Silver].[DimKanbanFase]
        SET OrdemFase = :ordem
        WHERE IDDimKanbanFase = :id_fase;
    """)

    for item in fases:
        id_fase = int(item.get("id") or 0)
        ordem = int(item.get("ordem") or 0)
        if not id_fase or not ordem:
            continue
        db.session.execute(sql, {"id_fase": id_fase, "ordem": ordem})

    db.session.commit()
    return jsonify({"ok": True})




@kanban_bp.route("/api/kanbans/<int:id_kanban>/cards", methods=["POST"])
@login_required
def api_card_criar(id_kanban: int):
    id_usuario = _assert_login()

    payload = request.get_json(silent=True) or {}
    titulo = (payload.get("titulo") or "").strip()
    id_fase = int(payload.get("id_fase") or 0)

    if len(titulo) < 2:
        return jsonify({"ok": False, "msg": "Título inválido"}), 400
    if not id_fase:
        return jsonify({"ok": False, "msg": "Fase obrigatória"}), 400

  
    sql_val = text("""
        SELECT 1
        FROM [Kanban].[Silver].[DimKanbanFase]
        WHERE IDDimKanbanFase = :id_fase
          AND IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    ok = db.session.execute(sql_val, {"id_fase": id_fase, "id_kanban": id_kanban}).scalar()
    if not ok:
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
             'ATIVO', GETDATE(), 1, NULL);
    """)
    novo_id = db.session.execute(sql, {
        "id_kanban": id_kanban,
        "id_fase": id_fase,
        "titulo": titulo[:200],
        "id_usuario": id_usuario
    }).scalar()

    db.session.commit()
    return jsonify({"ok": True, "IDFatoKanbanCard": int(novo_id)})




@kanban_bp.route("/api/cards/<int:id_card>", methods=["GET"])
@login_required
def api_card_detalhe(id_card: int):
    _assert_login()

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
            c.IDDimKanbanOrigem,
            c.IDDimKanbanMotivoEncerramento,
            c.MotivoEncerramentoObs,

            c.IDEmpresaProprietaria,

            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ        AS EmpresaCNPJ,
            e.CNAE        AS EmpresaCNAE,

            cn.Classe     AS EmpresaClasse,
            cn.Setor      AS EmpresaSetor

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
        return jsonify({"ok": False, "msg": "Card não encontrado"}), 404


    try:
        id_kanban = int(card.get("IDDimKanban") or 0)
    except Exception:
        id_kanban = 0

    sql_cfg = text("""
        SELECT
            IDEmpresaProprietaria,
            BitPrincipal
        FROM [Kanban].[Silver].[DimKanban]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    cfg = db.session.execute(sql_cfg, {"id_kanban": id_kanban}).mappings().first() or {}

    try:
        id_emp_prop = int(cfg.get("IDEmpresaProprietaria") or 0)
    except Exception:
        id_emp_prop = 0

    try:
        bit_principal = int(cfg.get("BitPrincipal") or 0)
    except Exception:
        bit_principal = 0

    mostrar_painel_face_no_card = (id_emp_prop == 3) and (bit_principal == 1)

    kanban_cfg = {
        "IDDimKanban": id_kanban,
        "IDEmpresaProprietaria": cfg.get("IDEmpresaProprietaria"),
        "BitPrincipal": cfg.get("BitPrincipal"),
        "MostrarPainelFaceNoCard": bool(mostrar_painel_face_no_card),
    }

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

    return jsonify({
        "ok": True,
        "card": dict(card),
        "kanban_cfg": dict(kanban_cfg),
        "tags": [dict(t) for t in tags],
        "notas": [dict(n) for n in notas]
    })





@kanban_bp.route("/api/cards/<int:id_card>", methods=["PUT"])
@login_required
def api_card_atualizar(id_card: int):
    _assert_login()

    payload = request.get_json(silent=True) or {}

    titulo = (payload.get("titulo") or "").strip()
    descricao = payload.get("descricao")
    status = (payload.get("status") or "").strip().upper()

   
    id_empresa = payload.get("id_empresa")

    if titulo and len(titulo) < 2:
        return jsonify({"ok": False, "msg": "Título inválido"}), 400

    campos = []
    params = {"id_card": id_card}

    if titulo:
        campos.append("Titulo = :titulo")
        params["titulo"] = titulo[:200]

    if descricao is not None:
        campos.append("Descricao = :descricao")
        params["descricao"] = descricao

    if status:
        if status not in ("ATIVO", "CONCLUIDO", "PERDIDO", "CANCELADO"):
            return jsonify({"ok": False, "msg": "StatusCard inválido"}), 400
        campos.append("StatusCard = :status")
        params["status"] = status
        if status in ("CONCLUIDO", "PERDIDO", "CANCELADO"):
            campos.append("EncerradoEm = ISNULL(EncerradoEm, GETDATE())")

    
    if id_empresa is not None:
       
        id_empresa_str = str(id_empresa).strip() if id_empresa is not None else ""
        if id_empresa_str == "" or id_empresa is None:
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

    if not campos:
        return jsonify({"ok": True})

    sql = text(f"""
        UPDATE [Kanban].[Silver].[FatoKanbanCard]
        SET {", ".join(campos)},
            AtualizadoEm = GETDATE()
        WHERE IDFatoKanbanCard = :id_card
          AND Ativo = 1;
    """)

    db.session.execute(sql, params)
    db.session.commit()
    return jsonify({"ok": True})




@kanban_bp.route("/api/cards/<int:id_card>/mover", methods=["POST"])
@login_required
def api_card_mover(id_card: int):
    id_usuario = _assert_login()

    payload = request.get_json(silent=True) or {}
    id_fase_para = int(payload.get("id_fase_para") or 0)
    observacao = (payload.get("observacao") or "").strip()

    if not id_fase_para:
        return jsonify({"ok": False, "msg": "Fase destino obrigatória"}), 400

    try:
  
        sql_cols = text("""
            SELECT
                MAX(CASE WHEN c.name = 'OrdemNaFase'  THEN 1 ELSE 0 END) AS HasOrdemNaFase,
                MAX(CASE WHEN c.name = 'AtualizadoEm' THEN 1 ELSE 0 END) AS HasAtualizadoEm
            FROM sys.columns c
            WHERE c.object_id = OBJECT_ID('[Kanban].[Silver].[FatoKanbanCard]');
        """)
        cols = db.session.execute(sql_cols).mappings().first() or {}
        has_ordem = bool(cols.get("HasOrdemNaFase"))
        has_atualizado = bool(cols.get("HasAtualizadoEm"))


        sql_atual = text("""
            SELECT
                IDDimKanban,
                IDDimKanbanFaseAtual
            FROM [Kanban].[Silver].[FatoKanbanCard]
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1;
        """)
        row = db.session.execute(sql_atual, {"id_card": id_card}).mappings().first()
        if not row:
            return jsonify({"ok": False, "msg": "Card não encontrado"}), 404

        id_kanban = int(row["IDDimKanban"])
        id_fase_de = int(row["IDDimKanbanFaseAtual"])

        if id_fase_de == id_fase_para:
            return jsonify({"ok": True})

    
        sql_val = text("""
            SELECT 1
            FROM [Kanban].[Silver].[DimKanbanFase]
            WHERE IDDimKanbanFase = :id_fase
              AND IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        ok = db.session.execute(sql_val, {"id_fase": id_fase_para, "id_kanban": id_kanban}).scalar()
        if not ok:
            return jsonify({"ok": False, "msg": "Fase destino inválida"}), 400


        proxima_ordem = None
        if has_ordem:
            sql_next_ordem = text("""
                SELECT ISNULL(MAX(fc.OrdemNaFase), 0) + 1 AS ProximaOrdem
                FROM [Kanban].[Silver].[FatoKanbanCard] fc WITH (UPDLOCK, HOLDLOCK)
                WHERE fc.IDDimKanban = :id_kanban
                  AND fc.IDDimKanbanFaseAtual = :id_fase_para
                  AND fc.Ativo = 1;
            """)
            proxima_ordem = db.session.execute(sql_next_ordem, {
                "id_kanban": id_kanban,
                "id_fase_para": id_fase_para
            }).scalar()

            try:
                proxima_ordem = int(proxima_ordem or 1)
            except Exception:
                proxima_ordem = 1

 
        if has_ordem and has_atualizado:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET
                    IDDimKanbanFaseAtual = :id_fase_para,
                    OrdemNaFase = :ordem_na_fase,
                    AtualizadoEm = GETDATE()
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1;
            """)
            db.session.execute(sql_upd, {
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem,
                "id_card": id_card
            })

        elif has_ordem and not has_atualizado:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET
                    IDDimKanbanFaseAtual = :id_fase_para,
                    OrdemNaFase = :ordem_na_fase
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1;
            """)
            db.session.execute(sql_upd, {
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem,
                "id_card": id_card
            })

        elif (not has_ordem) and has_atualizado:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET
                    IDDimKanbanFaseAtual = :id_fase_para,
                    AtualizadoEm = GETDATE()
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1;
            """)
            db.session.execute(sql_upd, {
                "id_fase_para": id_fase_para,
                "id_card": id_card
            })

        else:
            sql_upd = text("""
                UPDATE [Kanban].[Silver].[FatoKanbanCard]
                SET
                    IDDimKanbanFaseAtual = :id_fase_para
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1;
            """)
            db.session.execute(sql_upd, {
                "id_fase_para": id_fase_para,
                "id_card": id_card
            })

  
        sql_ins = text("""
            INSERT INTO [Kanban].[Silver].[FatoKanbanCardMovimento]
                (IDFatoKanbanCard, IDFaseDe, IDFasePara, MovidoEm, MovidoPor, Observacao, IDEmpresaProprietaria)
            VALUES
                (:id_card, :id_fase_de, :id_fase_para, GETDATE(), :movido_por, :obs, NULL);
        """)
        db.session.execute(sql_ins, {
            "id_card": id_card,
            "id_fase_de": id_fase_de,
            "id_fase_para": id_fase_para,
            "movido_por": id_usuario,
            "obs": (observacao[:2000] if observacao else None),
        })

        db.session.commit()
        return jsonify({
            "ok": True,
            "id_card": id_card,
            "id_fase_de": id_fase_de,
            "id_fase_para": id_fase_para,
            "ordem_na_fase": proxima_ordem if has_ordem else None
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "msg": f"Erro ao mover card: {str(e)}"}), 500



@kanban_bp.route("/api/kanbans/<int:id_kanban>/tags", methods=["POST"])
@login_required
def api_tag_criar(id_kanban: int):
    id_usuario = _assert_login()
    payload = request.get_json(silent=True) or {}

    nome = (payload.get("nome") or "").strip()
    tipo = (payload.get("tipo") or "OPERACIONAL").strip().upper()
    cor = (payload.get("cor_hex") or "").strip()
    icone = (payload.get("icone") or "").strip()

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da tag inválido"}), 400
    if tipo not in ("OPERACIONAL", "FINANCEIRA", "INFORMATIVA"):
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
             1, GETDATE(), :id_usuario, NULL);
    """)
    novo_id = db.session.execute(sql, {
        "id_kanban": id_kanban,
        "nome": nome[:100],
        "tipo": tipo,
        "cor": cor if cor else None,
        "icone": icone[:50] if icone else None,
        "id_usuario": id_usuario,
    }).scalar()

    db.session.commit()
    return jsonify({"ok": True, "IDDimKanbanTag": int(novo_id)})


@kanban_bp.route("/api/cards/<int:id_card>/tags", methods=["POST"])
@login_required
def api_card_tag_adicionar(id_card: int):
    id_usuario = _assert_login()
    payload = request.get_json(silent=True) or {}

    id_tag = int(payload.get("id_tag") or 0)
    if not id_tag:
        return jsonify({"ok": False, "msg": "Tag obrigatória"}), 400

   
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
            (:id_card, :id_tag, GETDATE(), :id_usuario, NULL);
    """)
    db.session.execute(sql, {"id_card": id_card, "id_tag": id_tag, "id_usuario": id_usuario})
    db.session.commit()
    return jsonify({"ok": True})




@kanban_bp.route("/api/cards/<int:id_card>/tags/<int:id_tag>", methods=["DELETE"])
@login_required
def api_card_tag_remover(id_card: int, id_tag: int):
    id_usuario = _assert_login()

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
    return jsonify({"ok": True})


@kanban_bp.route("/api/cards/<int:id_card>/notas", methods=["POST"])
@login_required
def api_card_nota_criar(id_card: int):
    id_usuario = _assert_login()

    payload = request.get_json(silent=True) or {}
    texto = (payload.get("texto") or "").strip()
    tipo = (payload.get("tipo") or "OBS").strip().upper()

    if len(texto) < 2:
        return jsonify({"ok": False, "msg": "Texto da nota inválido"}), 400

    sql = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardNota]
            (IDFatoKanbanCard, TipoNota, Texto, CriadoEm, CriadoPor, IDEmpresaProprietaria)
        VALUES
            (:id_card, :tipo, :texto, GETDATE(), :criado_por, NULL);
    """)
    db.session.execute(sql, {
        "id_card": id_card,
        "tipo": tipo[:50],
        "texto": texto,
        "criado_por": id_usuario,
    })
    db.session.commit()
    return jsonify({"ok": True})






@kanban_bp.route("/api/empresas/buscar", methods=["GET"])
@login_required
def api_empresas_buscar():
    _assert_login()

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": True, "empresas": []})

   
    q_digits = "".join([c for c in q if c.isdigit()])

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
        WHERE
            (e.RazaoSocial LIKE :q_like)
            OR (e.CNPJ LIKE :q_like)
            OR (:q_digits <> '' AND REPLACE(REPLACE(REPLACE(e.CNPJ,'.',''),'/',''),'-','') LIKE :q_digits_like)
        ORDER BY
            CASE WHEN e.RazaoSocial LIKE :q_like_inicio THEN 0 ELSE 1 END,
            e.RazaoSocial ASC;
    """)

    empresas = db.session.execute(sql, {
        "q_like": f"%{q}%",
        "q_like_inicio": f"{q}%",
        "q_digits": q_digits,
        "q_digits_like": f"%{q_digits}%",
    }).mappings().all()

    return jsonify({"ok": True, "empresas": [dict(r) for r in empresas]})






@kanban_bp.route("/api/cards/<int:id_card>/inativar", methods=["POST"])
@login_required
def api_card_inativar(id_card: int):
    id_usuario = _assert_login()

    payload = request.get_json(silent=True) or {}
    motivo = (payload.get("motivo") or "").strip()
    descricao = (payload.get("descricao") or "").strip()

    motivos_validos = {"Desistencia", "Preço", "Apenas Informações", "Outro Motivo"}
    if motivo not in motivos_validos:
        return jsonify({"ok": False, "msg": "Motivo inválido"}), 400

    if motivo == "Outro Motivo" and len(descricao) < 2:
        return jsonify({"ok": False, "msg": "Descreva o motivo"}), 400

  
    sql_card = text("""
        SELECT
            IDDimKanban,
            IDDimKanbanFaseAtual,
            IDEmpresaProprietaria
        FROM [Kanban].[Silver].[FatoKanbanCard]
        WHERE IDFatoKanbanCard = :id_card
          AND Ativo = 1;
    """)
    row = db.session.execute(sql_card, {"id_card": id_card}).mappings().first()
    if not row:
        return jsonify({"ok": False, "msg": "Card não encontrado ou já inativo"}), 404

    id_kanban = int(row["IDDimKanban"])
    id_fase_atual = int(row["IDDimKanbanFaseAtual"])
    id_empresa = row.get("IDEmpresaProprietaria", None)

    try:
   
        sql_upd = text("""
            UPDATE [Kanban].[Silver].[FatoKanbanCard]
            SET
                Ativo = 0,
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
        db.session.execute(sql_ins, {
            "id_card": id_card,
            "id_fase_de": id_fase_atual,
            "movido_por": id_usuario,
            "obs": obs[:2000],
            "id_empresa": id_empresa
        })

        db.session.commit()
        return jsonify({"ok": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "msg": f"Erro ao inativar card: {str(e)}"}), 500



@kanban_bp.route("/api/fases/<int:id_fase>/inativar", methods=["POST"])
@login_required
def api_fase_inativar(id_fase: int):
    id_usuario = _assert_login()

 
    sql_fase = text("""
        SELECT
            f.IDDimKanbanFase,
            f.IDDimKanban
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = :id_fase
          AND f.Ativo = 1;
    """)
    row = db.session.execute(sql_fase, {"id_fase": id_fase}).mappings().first()
    if not row:
        return jsonify({"ok": False, "msg": "Fase não encontrada ou já inativa"}), 404

    try:
       
        sql_upd = text("""
            UPDATE [Kanban].[Silver].[DimKanbanFase]
            SET
                Ativo = 0,
                InativadoEm = GETDATE(),
                InativadoPor = :id_usuario
            WHERE IDDimKanbanFase = :id_fase
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_fase": id_fase})

  

        db.session.commit()
        return jsonify({"ok": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "msg": f"Erro ao inativar fase: {str(e)}"}), 500



@kanban_bp.route("/api/kanbans/<int:id_kanban>/inativar", methods=["POST"])
@login_required
def api_kanban_inativar(id_kanban: int):
    id_usuario = _assert_login()

   
    sql_k = text("""
        SELECT IDDimKanban
        FROM [Kanban].[Silver].[DimKanban]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    ok = db.session.execute(sql_k, {"id_kanban": id_kanban}).scalar()
    if not ok:
        return jsonify({"ok": False, "msg": "Kanban não encontrado ou já inativo"}), 404

    try:
       
        sql_upd = text("""
            UPDATE [Kanban].[Silver].[DimKanban]
            SET
                Ativo = 0,
                InativadoEm = GETDATE(),
                InativadoPor = :id_usuario
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_kanban": id_kanban})

      
        sql_upd_fases = text("""
            UPDATE [Kanban].[Silver].[DimKanbanFase]
            SET
                Ativo = 0,
                InativadoEm = ISNULL(InativadoEm, GETDATE()),
                InativadoPor = ISNULL(InativadoPor, :id_usuario)
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd_fases, {"id_usuario": id_usuario, "id_kanban": id_kanban})

    

        db.session.commit()
        return jsonify({"ok": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "msg": f"Erro ao inativar kanban: {str(e)}"}), 500








@kanban_bp.route("/api/paineis", methods=["GET"])
@login_required
def api_paineis_lista():
    _assert_login()

    """
    Retorna painéis para seleção no card (apenas para testes).
    Exibe: CodPonto | Tipo | Logradouro (e outros campos úteis).
    """

    sql = text("""
        SELECT 
            p.IDDimPaineisEuromidia,
            p.CodPonto,
            p.Tipo,
            p.Logradouro,
            p.Numero,
            p.Bairro,
            p.Cidade,
            p.UF,
            p.CEP,
            p.Referencia,
            p.QuantidadeFaces
        FROM [Integracao].[Silver].[DimPaineisEuromidia] p
        WHERE
            p.CodPonto IS NOT NULL
            AND LTRIM(RTRIM(p.CodPonto)) <> ''
            AND p.BitAtivo = 1
        ORDER BY
            p.Cidade ASC,
            p.UF ASC,
            p.Tipo ASC,
            p.CodPonto ASC;
    """)

    rows = db.session.execute(sql).mappings().all()
    return jsonify({"ok": True, "paineis": [dict(r) for r in rows]})









@kanban_bp.route("/api/paineis/<int:cod_ponto>/faces", methods=["GET"])
@login_required
def api_faces_por_painel(cod_ponto: int):
    _assert_login()

 
    cod_ponto = int(cod_ponto or 0)
    if cod_ponto <= 0:
        return jsonify({"ok": False, "msg": "CodPonto inválido"}), 400

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
    except Exception as e:
        return jsonify({
            "ok": False,
            "msg": "Erro ao consultar faces do painel",
            "erro": str(e)
        }), 500

    faces = []
    for r in rows:
        codface = (r.get("CodFace") or "").strip()
        face = (r.get("Face") or "").strip()

        if not codface:
            continue

      
        if face:
            label = f"Face {face} • CodFace {codface}"
        else:
            label = f"CodFace {codface}"

        faces.append({
            "CodFace": codface,  
            "Face": face or None,
            "Label": label
        })

    return jsonify({
        "ok": True,
        "cod_ponto": cod_ponto,
        "total": len(faces),
        "faces": faces
    })
