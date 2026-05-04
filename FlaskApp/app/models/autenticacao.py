from ..extensions import db
from sqlalchemy import Text, Date, DateTime, Numeric,Integer
from datetime import datetime
from flask_login import UserMixin
from ..extensions import db, login_manager





class DimPerfilUsuario(db.Model):
    __tablename__ = "DimPerfilUsuario"
    __table_args__ = {"schema": "Silver"}

    IDDimPerfilUsuario = db.Column(db.Integer, primary_key=True, autoincrement=True)

    NomePerfil = db.Column(db.Unicode(100), nullable=True)
    Descricao = db.Column(db.Unicode(500), nullable=True)

    BitAtivo = db.Column(db.Boolean, nullable=True)

    DataCriacao = db.Column(db.DateTime, nullable=True)
    DataAtualizacao = db.Column(db.DateTime, nullable=True)

    usuarios = db.relationship("DimUsuarios", back_populates="perfil", lazy="selectin")
    permissoes = db.relationship(
        "DimPermissoes",
        secondary="Silver.PermissoesPerfil",
        back_populates="perfis",
        lazy="selectin",
    )




class DimPermissoes(db.Model):
    __tablename__ = "DimPermissoes"
    __table_args__ = {"schema": "Silver"}

    IDDimPermissoes = db.Column(db.Integer, primary_key=True, autoincrement=True)

    CodigoPermissao = db.Column(db.String(120), nullable=False, unique=True)
    Descricao = db.Column(db.Unicode(500), nullable=True)

    BitAtivo = db.Column(db.Boolean, nullable=False, server_default=db.text("1"))

    DataCriacao = db.Column(db.DateTime, nullable=False, server_default=db.text("GETDATE()"))
    DataAtualizacao = db.Column(db.DateTime, nullable=True)

    
    perfis = db.relationship(
        "DimPerfilUsuario",
        secondary="Silver.PermissoesPerfil",
        back_populates="permissoes",
        lazy="selectin",
    )







class DimUsuarios(db.Model, UserMixin):
    __tablename__ = "DimUsuarios"
    __table_args__ = {"schema": "Silver"}

    IDDimUsuarios = db.Column(db.Integer, primary_key=True, autoincrement=True)

    IDDimPerfilUsuario = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimPerfilUsuario.IDDimPerfilUsuario"),
        nullable=False,
    )

    NomeUsuario = db.Column(db.Unicode(200), nullable=True)
    Email = db.Column(db.Unicode(200), nullable=True)
    HashSenha = db.Column(db.Unicode(800), nullable=True)

    BitAtivo = db.Column(db.Boolean, nullable=True)

    CreatedAt = db.Column(db.DateTime, nullable=True)
    UpdateAt = db.Column(db.DateTime, nullable=True)
    UltimoLogin = db.Column(db.DateTime, nullable=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=True)

    perfil = db.relationship(
        "DimPerfilUsuario",
        back_populates="usuarios",
        lazy="joined",
    )

    permissoes_extras = db.relationship(
        "PermissoesUsuario",
        foreign_keys="PermissoesUsuario.IDDimUsuarios",
        back_populates="usuario",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    def get_id(self):
        return str(self.IDDimUsuarios)

    def is_active(self):
        return bool(self.BitAtivo)

    def has_permission(self, codigo_permissao: str) -> bool:
        """has_permission
        Eu verifico se o usuário possui uma permissão efetiva.

        A regra é:

        1. O perfil do usuário concede permissões padrão.
        2. A tabela PermissoesUsuario pode conceder permissões extras.
        3. A tabela PermissoesUsuario pode revogar permissões específicas.
        4. ADMIN_TUDO funciona como permissão coringa.
        5. Permissões expiradas são ignoradas.
        6. REVOGAR tem prioridade sobre CONCEDER e sobre o perfil.
        """

        if not codigo_permissao:
            return False

        codigo = str(codigo_permissao).strip().upper()

        if not codigo:
            return False

        agora = datetime.now()

        codigos_perfil = set()
        codigos_concedidos = set()
        codigos_revogados = set()

        perfil = getattr(self, "perfil", None)
        perfil_ativo = bool(getattr(perfil, "BitAtivo", False))

        if perfil and perfil_ativo:
            for permissao in getattr(perfil, "permissoes", None) or []:
                if not permissao:
                    continue

                if not bool(getattr(permissao, "BitAtivo", True)):
                    continue

                codigo_perfil = (getattr(permissao, "CodigoPermissao", "") or "").strip().upper()

                if codigo_perfil:
                    codigos_perfil.add(codigo_perfil)

        for row in getattr(self, "permissoes_extras", None) or []:
            permissao = getattr(row, "permissao", None)

            if not permissao:
                continue

            if not bool(getattr(permissao, "BitAtivo", True)):
                continue

            data_expiracao = getattr(row, "DataExpiracao", None)

            if data_expiracao is not None and data_expiracao <= agora:
                continue

            codigo_extra = (getattr(permissao, "CodigoPermissao", "") or "").strip().upper()
            tipo_atribuicao = (getattr(row, "TipoAtribuicao", "") or "").strip().upper()

            if not codigo_extra:
                continue

            if tipo_atribuicao == "REVOGAR":
                codigos_revogados.add(codigo_extra)

            elif tipo_atribuicao == "CONCEDER":
                codigos_concedidos.add(codigo_extra)

        if codigo in codigos_revogados:
            return False

        if codigo in codigos_concedidos:
            return True

        admin_tudo_revogado = "ADMIN_TUDO" in codigos_revogados
        admin_tudo_por_perfil = "ADMIN_TUDO" in codigos_perfil
        admin_tudo_por_excecao = "ADMIN_TUDO" in codigos_concedidos

        if not admin_tudo_revogado and (admin_tudo_por_perfil or admin_tudo_por_excecao):
            return True

        return codigo in codigos_perfil

    def has_any_permission(self, *codigos_permissao) -> bool:
        """has_any_permission
        Eu verifico se o usuário possui pelo menos uma permissão da lista.
        Isso ajuda em menus onde mais de uma permissão pode liberar acesso.
        """

        for codigo in codigos_permissao or []:
            if self.has_permission(codigo):
                return True

        return False

    def is_admin_total(self) -> bool:
        """is_admin_total
        Eu verifico se o usuário tem acesso administrativo total.
        """

        return self.has_permission("ADMIN_TUDO")








class PermissoesPerfil(db.Model):
    __tablename__ = "PermissoesPerfil"
    __table_args__ = {"schema": "Silver"}

    IDDimPerfilUsuario = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimPerfilUsuario.IDDimPerfilUsuario"),
        primary_key=True,
        nullable=False,
    )

    IDDimPermissoes = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimPermissoes.IDDimPermissoes"),
        primary_key=True,
        nullable=False,
    )

    DataCriacao = db.Column(db.DateTime, nullable=False, server_default=db.text("GETDATE()"))


class PermissoesUsuario(db.Model):
    __tablename__ = "PermissoesUsuario"
    __table_args__ = {"schema": "Silver"}

    IDDimUsuarios = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimUsuarios.IDDimUsuarios"),
        primary_key=True,
        nullable=False,
    )

    IDDimPermissoes = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimPermissoes.IDDimPermissoes"),
        primary_key=True,
        nullable=False,
    )

    TipoAtribuicao = db.Column(db.Unicode(20), nullable=False, server_default=db.text("'CONCEDER'"))
    DataExpiracao = db.Column(db.DateTime, nullable=True)

    DataCriacao = db.Column(db.DateTime, nullable=False, server_default=db.text("GETDATE()"))
    DataAtualizacao = db.Column(db.DateTime, nullable=True)

    CriadoPorIDDimUsuarios = db.Column(db.Integer, nullable=True)
    Observacao = db.Column(db.Unicode(500), nullable=True)

  
    usuario = db.relationship("DimUsuarios", back_populates="permissoes_extras", lazy="joined")
    permissao = db.relationship("DimPermissoes", lazy="joined")



@login_manager.user_loader
def load_user(user_id: str):
   
    if not user_id:
        return None
    try:
        uid = int(user_id)
    except Exception:
        return None

    return (
        db.session.query(DimUsuarios)
        .filter(DimUsuarios.IDDimUsuarios == uid)
        .first()
    )







