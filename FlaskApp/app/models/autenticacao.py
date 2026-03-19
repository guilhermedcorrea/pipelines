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

 
    perfil = db.relationship("DimPerfilUsuario", back_populates="usuarios", lazy="joined")

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
        
        if not codigo_permissao:
            return False

        codigo = str(codigo_permissao).strip().upper()
        agora = datetime.now()

      
        perfil_ok = bool(getattr(self.perfil, "BitAtivo", False))
        perfil_tem = False

        if perfil_ok and self.perfil and self.perfil.permissoes:
            for p in self.perfil.permissoes:
                if bool(getattr(p, "BitAtivo", True)) and (p.CodigoPermissao or "").strip().upper() == codigo:
                    perfil_tem = True
                    break


        excecao_concede = False
        excecao_revoga = False

        for row in self.permissoes_extras or []:
            perm = row.permissao
            if not perm or not bool(getattr(perm, "BitAtivo", True)):
                continue

            if (perm.CodigoPermissao or "").strip().upper() != codigo:
                continue

            if row.DataExpiracao is not None and row.DataExpiracao <= agora:
                continue

            tipo = (row.TipoAtribuicao or "").strip().upper()
            if tipo == "REVOGAR":
                excecao_revoga = True
            elif tipo == "CONCEDER":
                excecao_concede = True

        if excecao_revoga:
            return False
        if excecao_concede:
            return True

        return bool(perfil_tem)




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
