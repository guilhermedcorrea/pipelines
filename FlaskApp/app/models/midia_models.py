from ..extensions import db
from sqlalchemy import Text, Date, Numeric
from sqlalchemy.dialects.mssql import DATETIME2
from sqlalchemy.orm import foreign




class DimPaineisMidia(db.Model):
    __tablename__ = "DimPaineisMidia"
    __table_args__ = ({"schema": "Silver"},)

    IDDimPaineisMidia = db.Column(db.Integer, primary_key=True, autoincrement=True)

    DataAtualizacao = db.Column(
        DATETIME2,
        nullable=False,
        server_default=db.func.getdate(),
    )

    CodPonto = db.Column(db.Integer, nullable=True)
    QuantidadeFaces = db.Column(db.Integer, nullable=True)

    Tipo = db.Column(db.Unicode(80), nullable=True)
    Cidade = db.Column(db.Unicode(150), nullable=True)
    UF = db.Column(db.String(5), nullable=True)

    Logradouro = db.Column(db.Unicode(200), nullable=True)
    Sentido = db.Column(db.Unicode(200), nullable=True)
    Bairro = db.Column(db.Unicode(100), nullable=True)
    Referencia = db.Column(db.Unicode(200), nullable=True)
    Numero = db.Column(db.Unicode(20), nullable=True)
    CEP = db.Column(db.String(30), nullable=True)

    Latitude = db.Column(db.Numeric(9, 6), nullable=True)
    Longitude = db.Column(db.Numeric(9, 6), nullable=True)

    FormatoLxA = db.Column(db.String(20), nullable=True)
    FormatoLonaAcabadaLxAm = db.Column(db.String(20), nullable=True)

    AreaTotalm = db.Column(db.Float, nullable=True)

    BitIluminado = db.Column(db.Boolean, nullable=True)
    Restricoes = db.Column(db.Unicode(200), nullable=True)
    TipoSolo = db.Column(db.Unicode(10), nullable=True)

    DataInstalacao = db.Column(db.Date, nullable=True)
    DataRetirada = db.Column(db.Date, nullable=True)

    Exibidora = db.Column(db.Unicode(60), nullable=True)

    BitProprio = db.Column(db.Boolean, nullable=True)
    BitAtivo = db.Column(db.Boolean, nullable=True)
    BitAluguel = db.Column(db.Boolean, nullable=True)
    BitEnergia = db.Column(db.Boolean, nullable=True)
    BitInternet = db.Column(db.Boolean, nullable=True)

    IDProduto = db.Column(db.Integer, nullable=True)

  
    faces_itens = db.relationship(
                "DimFacesPaineis",
                back_populates="painel",
                lazy="selectin",
                foreign_keys="DimFacesPaineis.IDDimPaineisMidia",
            )


   
    ocupacoes = db.relationship(
        "FatoOcupacaoPaineisMidia",
        back_populates="painel",
        lazy="selectin",
        foreign_keys="FatoOcupacaoPaineisMidia.IDPainelMidia",
    )

   
    contratos_itens = db.relationship(
        "FatoControleContratosItensMidia",
        back_populates="painel",
        lazy="selectin",
        foreign_keys="FatoControleContratosItensMidia.IDPainelMidia",
    )

  
    contratos_itens_por_codponto = db.relationship(
        "FatoControleContratosItensMidia",
        primaryjoin="DimPaineisMidia.CodPonto==foreign(FatoControleContratosItensMidia.CodPonto)",
        viewonly=True,
        lazy="selectin",
    )

    ocupacoes_por_codponto = db.relationship(
        "FatoOcupacaoPaineisMidia",
        primaryjoin="DimPaineisMidia.CodPonto==foreign(FatoOcupacaoPaineisMidia.CodPonto)",
        viewonly=True,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<DimPaineisMidia "
            f"IDDimPaineisMidia={self.IDDimPaineisMidia} "
            f"CodPonto={self.CodPonto} UF={self.UF} QuantidadeFaces={self.QuantidadeFaces} Ativo={self.BitAtivo}>"
        )



class DimFacesPaineis(db.Model):
    __tablename__ = "DimFacesPaineis"
    __table_args__ = ({"schema": "Silver"},)

    IDDimFacesPaineis = db.Column(db.Integer, primary_key=True, autoincrement=True)

    CodPonto = db.Column(db.Integer, nullable=True)
    Face = db.Column(db.String(5), nullable=True)
    CodFace = db.Column(db.String(20), nullable=True)
    Tipo = db.Column(db.Unicode(70), nullable=True)

    
    IDDimPaineisMidia = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimPaineisMidia.IDDimPaineisMidia"),
        nullable=True,
        index=True,
    )

  
    painel = db.relationship(
        "DimPaineisMidia",
        back_populates="faces_itens",
        foreign_keys="DimFacesPaineis.IDDimPaineisMidia",
        lazy="joined",
    )


    contratos_itens = db.relationship(
        "FatoControleContratosItensMidia",
        back_populates="dim_face",
        lazy="selectin",
        foreign_keys="FatoControleContratosItensMidia.IDDimFacesPaineis",
    )

    def __repr__(self) -> str:
        return (
            f"<DimFacesPaineis "
            f"ID={self.IDDimFacesPaineis} CodPonto={self.CodPonto} "
            f"Face={self.Face} CodFace={self.CodFace} "
            f"IDDimPaineisMidia={self.IDDimPaineisMidia}>"
        )




class Vendedores(db.Model):
    __tablename__ = "Vendedores"
    __table_args__ = {"schema": "dbo"}

    IDVendedor = db.Column(db.Integer, primary_key=True, autoincrement=False)
    NomeVendedor = db.Column(db.String(250), nullable=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=True)
    Meta = db.Column(Numeric(19, 4), nullable=True)
    BitAtivo = db.Column(db.Boolean, nullable=True)

    ocupacoes = db.relationship(
        "FatoOcupacaoPaineisMidia",
        back_populates="vendedor_rel",
        lazy="selectin",
        foreign_keys="FatoOcupacaoPaineisMidia.IDVendedor",
    )

    contratos_itens = db.relationship(
        "FatoControleContratosItensMidia",
        back_populates="vendedor_rel",
        lazy="selectin",
        foreign_keys="FatoControleContratosItensMidia.IDVendedor",
    )

    def __repr__(self) -> str:
        return f"<Vendedores IDVendedor={self.IDVendedor} NomeVendedor='{self.NomeVendedor}' Ativo={self.BitAtivo}>"









from sqlalchemy import Index, text


from sqlalchemy.dialects.mssql import NVARCHAR, VARCHAR, DATETIME2, DATE, INTEGER



class FatoOcupacaoPaineisMidia(db.Model):
    __tablename__ = "FatoOcupacaoPaineisMidia"
    __table_args__ = ({"schema": "Silver"},)

    IDFatoOcupacaoPaineisMidia = db.Column(db.Integer,primary_key=True, autoincrement=True)
    DataAtualizacao = db.Column(DATETIME2(0), nullable=False,server_default=text("SYSDATETIME()"))
    Referencia = db.Column(VARCHAR(64),nullable=False)
    CodPonto = db.Column(db.Integer,nullable=False)
    CodFace = db.Column(NVARCHAR(100),nullable=False)
    IDPainelMidia = db.Column(db.Integer,db.ForeignKey("Silver.DimPaineisMidia.IDDimPaineisMidia"),nullable=True,)
    Origem = db.Column(VARCHAR(20),nullable=False)
    Status = db.Column(VARCHAR(20),nullable=False)
    DataInicio = db.Column(DATE,nullable=False)
    DataFim = db.Column(DATE,nullable=False)
    LoopInicio = db.Column(db.Integer,nullable=True)
    LoopFim = db.Column(db.Integer,nullable=True)
    SpanQtd = db.Column(db.Integer,nullable=True)
    Cota = db.Column(db.Integer,nullable=True)
    MarcaExibida = db.Column(NVARCHAR(200),nullable=True)
    Vendedor = db.Column(NVARCHAR(200),nullable=True)
    IDVendedor = db.Column(db.Integer, db.ForeignKey("dbo.Vendedores.IDVendedor"), nullable=True)
    IDCliente = db.Column(db.Integer, db.ForeignKey("Silver.DimEmpresas.IDEmpresa"), nullable=True)
    IDFatoControleContratos = db.Column(db.Integer,nullable=True)
    NumeroContrato = db.Column(NVARCHAR(150),nullable=True)
    NumeroPrevia = db.Column(NVARCHAR(150),nullable=True)
    TextoOriginal = db.Column( NVARCHAR(None),nullable=True)
    CriadoEm = db.Column(DATETIME2(0),nullable=False,server_default=text("SYSDATETIME()"))
    CriadoPorIDUsuario = db.Column(db.Integer,nullable=False)
    ExpiraEm = db.Column(DATETIME2(0),nullable=True)
    CanceladoEm = db.Column( DATETIME2(0),nullable=True )
    CanceladoPorIDUsuario = db.Column(db.Integer,nullable=True )
    Observacao = db.Column(NVARCHAR(250),nullable=True)
    Dias = db.Column(db.Integer,nullable=True)
    painel = db.relationship(
        "DimPaineisMidia",
        back_populates="ocupacoes",
        lazy="joined",
        foreign_keys=[IDPainelMidia],
    )

    vendedor_rel = db.relationship(
        "Vendedores",
        back_populates="ocupacoes",
        lazy="joined",
        foreign_keys=[IDVendedor],
    )

    cliente = db.relationship(
        "DimEmpresas",
        back_populates="ocupacoes",
        lazy="joined",
        foreign_keys=[IDCliente],
    )

    contratos_itens_por_codponto = db.relationship(
        "FatoControleContratosItensMidia",
        primaryjoin="FatoOcupacaoPaineisMidia.CodPonto==foreign(FatoControleContratosItensMidia.CodPonto)",
        viewonly=True,
        lazy="selectin",
    )

    contratos_itens_por_codponto_codface = db.relationship(
        "FatoControleContratosItensMidia",
        primaryjoin=(
            "and_("
            "FatoOcupacaoPaineisMidia.CodPonto==foreign(FatoControleContratosItensMidia.CodPonto), "
            "FatoOcupacaoPaineisMidia.CodFace==foreign(FatoControleContratosItensMidia.CodFace)"
            ")"
        ),
        viewonly=True,
        lazy="selectin",
    )

    def __repr__(self):
        return (
            f"<FatoOcupacaoPaineisMidia "
            f"ID={self.IDFatoOcupacaoPaineisMidia} "
            f"CodPonto={self.CodPonto} CodFace={self.CodFace} "
            f"{self.DataInicio}..{self.DataFim} Status={self.Status}>"
        )









class FatoControleContratosMidia(db.Model):
    __tablename__ = "FatoControleContratosMidia"
    __table_args__ = ({"schema": "Silver"},)

    IDFatoControleContratosMidia = db.Column(db.Integer, primary_key=True, autoincrement=True)

    DataAtualizacao = db.Column(DATETIME2, nullable=False)
    Referencia = db.Column(db.String(64), nullable=False)

    NumeroContrato = db.Column(db.String(150), nullable=True)
    NumeroPrevia = db.Column(db.String(150), nullable=True)

    CNPJ = db.Column(db.String(20), nullable=True)
    DataAssinaturaRenovacao = db.Column(db.Date, nullable=True)
    IDTrimestre = db.Column(db.String(20), nullable=True)

    DataLancamento = db.Column(db.Date, nullable=True)
    RazaoSocial = db.Column(db.Unicode(200), nullable=True)
    CPF = db.Column(db.String(20), nullable=True)

    MarcaExibida = db.Column(db.Unicode(100), nullable=True)
    Vendedor = db.Column(db.Unicode(100), nullable=True)

    TipoDocumento = db.Column(db.Unicode(70), nullable=True)
    Origem = db.Column(db.Unicode(10), nullable=True)

    SDR = db.Column(db.String(20), nullable=True)

    Agencia = db.Column(db.Unicode(100), nullable=True)
    CnpjAgencia = db.Column(db.String(20), nullable=True)

    Bureau = db.Column(db.Unicode(100), nullable=True)
    CnpjBureau = db.Column(db.String(20), nullable=True)

    Intermediario = db.Column(db.Unicode(100), nullable=True)
    CnpjIntermediario = db.Column(db.String(20), nullable=True)

    QuantidadePontos = db.Column(db.Integer, nullable=True)
    QuantidadeFaces = db.Column(db.Integer, nullable=True)

    TotalFaturamentoBrutoMensal = db.Column(db.Numeric(19, 2), nullable=True)
    TotalPercentualPermuta = db.Column(db.Numeric(5, 2), nullable=True)
    TotalCotaOportunidade = db.Column(db.Numeric(19, 2), nullable=True)
    TotalValorPermuta = db.Column(db.Numeric(19, 2), nullable=True)
    TotalFaturamentoLiquidoPermuta = db.Column(db.Numeric(19, 2), nullable=True)

    TotalBrutoContrato = db.Column(db.Numeric(19, 2), nullable=True)
    TotalLiquidoContratoAGBRCTACORDO = db.Column(db.Numeric(19, 2), nullable=True)
    TotalLiquidoContratoAGBRVENDGERCOOR = db.Column(db.Numeric(19, 2), nullable=True)

    TotalPercentualAgencia = db.Column(db.Numeric(5, 2), nullable=True)
    TotalValorMensalAgencia = db.Column(db.Numeric(19, 2), nullable=True)

    TotalPercentualBureau = db.Column(db.Numeric(5, 2), nullable=True)
    TotalValorBureauMensal = db.Column(db.Numeric(19, 2), nullable=True)

    TotalPercentualCartaAcordo = db.Column(db.Numeric(5, 2), nullable=True)
    TotalValorCartaAcordoMensal = db.Column(db.Numeric(19, 2), nullable=True)

    TotalValorOutrasComissoes = db.Column(db.Numeric(19, 2), nullable=True)
    TotalFaturamentoLiquidoMensal = db.Column(db.Numeric(19, 2), nullable=True)

    TotalPercentualComissaoVendedor = db.Column(db.Numeric(5, 2), nullable=True)
    TotalValorVendedor = db.Column(db.Numeric(19, 2), nullable=True)
    ValorVendedorTotal = db.Column(db.Numeric(19, 2), nullable=True)

    TotalPercentualComissaoCoordenacao = db.Column(db.Numeric(5, 2), nullable=True)
    IDCategoriaMarca = db.Column(db.Integer, nullable=True)
    IDDimStatusContratos = db.Column(db.Integer, nullable=True)
    BitAtivo = db.Column(db.Boolean)


    itens = db.relationship(
        "FatoControleContratosItensMidia",
        back_populates="contrato",
        lazy="selectin",
        foreign_keys="FatoControleContratosItensMidia.IDFatoControleContratoMidia",
    )

    IDEmpresa = db.Column(db.Integer, db.ForeignKey("Silver.DimEmpresas.IDEmpresa"), nullable=True)

    empresa = db.relationship(
        "DimEmpresas",
        back_populates="contratos",
        lazy="joined",
        foreign_keys=[IDEmpresa],
    )

    def __repr__(self) -> str:
        return (
            f"<FatoControleContratosMidia "
            f"ID={self.IDFatoControleContratosMidia} "
            f"Referencia={self.Referencia!r}>"
        )




class FatoControleContratosItensMidia(db.Model):
    __tablename__ = "FatoControleContratosItensMidia"
    __table_args__ = ({"schema": "Silver"},)

    IDFatoControleContratosItensMidia = db.Column(db.Integer, primary_key=True, autoincrement=True)

    IDFatoControleContratoMidia = db.Column(
        db.Integer,
        db.ForeignKey("Silver.FatoControleContratosMidia.IDFatoControleContratosMidia"),
        nullable=True,
    )

    DataAtualizacao = db.Column(DATETIME2, nullable=False)
    Referencia = db.Column(db.String(64), nullable=False)

    NumeroContrato = db.Column(db.String(150), nullable=True)
    NumeroPrevia = db.Column(db.String(150), nullable=True)
    CNPJ = db.Column(db.String(20), nullable=True)

    CodPonto = db.Column(db.Integer, nullable=True)
    CodFace = db.Column(db.String(20), nullable=True)

    DataLancamento = db.Column(db.Date, nullable=True)
    Cota = db.Column(db.Integer, nullable=True)

    CidadeExibicao = db.Column(db.Unicode(100), nullable=True)
    Tipo = db.Column(db.Unicode(70), nullable=True)
    Origem = db.Column(db.Unicode(10), nullable=True)

    EmpresaEuro = db.Column(db.Unicode(100), nullable=True)
    CnpjExibibora = db.Column(db.String(20), nullable=True)

    TipoDocumento = db.Column(db.Unicode(70), nullable=True)
    RazaoSocial = db.Column(db.Unicode(200), nullable=True)
    CPF = db.Column(db.String(20), nullable=True)

    MarcaExibida = db.Column(db.Unicode(100), nullable=True)
    Vendedor = db.Column(db.Unicode(100), nullable=True)
    SDR = db.Column(db.String(20), nullable=True)

    Agencia = db.Column(db.Unicode(100), nullable=True)
    CnpjAgencia = db.Column(db.String(20), nullable=True)

    Bureau = db.Column(db.Unicode(100), nullable=True)
    CnpjBureau = db.Column(db.String(20), nullable=True)

    Intermediario = db.Column(db.Unicode(100), nullable=True)
    CnpjIntermediario = db.Column(db.String(20), nullable=True)

    DataAssinaturaRenovacao = db.Column(db.Date, nullable=True)
    IDTrimestre = db.Column(db.String(20), nullable=True)

    TexmpoExposicao = db.Column(db.Integer, nullable=True)
    DataInicioPrevisto = db.Column(db.Date, nullable=True)
    DataTerminoPrevisto = db.Column(db.Date, nullable=True)
    InicioRenovacao = db.Column(db.String(2), nullable=True)

    FaturamentoBrutoMensal = db.Column(db.Numeric(19, 2), nullable=True)
    PercentualPermuta = db.Column(db.Numeric(5, 2), nullable=True)
    CotaOportunidade = db.Column(db.Numeric(19, 2), nullable=True)
    ValorPermuta = db.Column(db.Numeric(19, 2), nullable=True)
    FaturamentoLiquidoPermuta = db.Column(db.Numeric(19, 2), nullable=True)

    NumeroParcelas = db.Column(db.Integer, nullable=True)
    DataInicioVencimento = db.Column(db.Date, nullable=True)

    TotalBrutoContrato = db.Column(db.Numeric(19, 2), nullable=True)
    TotalLiquidoContratoAGBRCTACORDO = db.Column(db.Numeric(19, 2), nullable=True)
    TotalLiquidoContratoAGBRVENDGERCOOR = db.Column(db.Numeric(19, 2), nullable=True)

    PercentualAgencia = db.Column(db.Numeric(5, 2), nullable=True)
    ValorMensalAgencia = db.Column(db.Numeric(19, 2), nullable=True)

    PercentualBureau = db.Column(db.Numeric(5, 2), nullable=True)
    ValorBureauMensal = db.Column(db.Numeric(19, 2), nullable=True)

    PercentualCartaAcordo = db.Column(db.Numeric(5, 2), nullable=True)
    ValorCartaAcordoMensal = db.Column(db.Numeric(19, 2), nullable=True)

    ValorOutrasComissoes = db.Column(db.Numeric(19, 2), nullable=True)
    FaturamentoLiquidoMensal = db.Column(db.Numeric(19, 2), nullable=True)

    PercentualComissaoVendedor = db.Column(db.Numeric(5, 2), nullable=True)
    ValorVendedor = db.Column(db.Numeric(19, 2), nullable=True)
    ValorVendedorTotal = db.Column(db.Numeric(19, 2), nullable=True)

    PercentualComissaoCoordenacao = db.Column(db.Numeric(5, 2), nullable=True)
    ValorCoordenador = db.Column(db.Numeric(19, 2), nullable=True)
    ValorCoordenadorTotal = db.Column(db.Numeric(19, 2), nullable=True)

    PercentualComissaoGerencia = db.Column(db.Numeric(5, 2), nullable=True)
    ValorGerencia = db.Column(db.Numeric(19, 2), nullable=True)
    ValorGerenciaTotal = db.Column(db.Numeric(19, 2), nullable=True)

    AtivoCancelamento = db.Column(db.String(2), nullable=True)

    FaturamentoLiquidoFinalMensal = db.Column(db.Numeric(19, 2), nullable=True)
    ComissaoGerenciaNordeste = db.Column(db.Numeric(5, 2), nullable=True)

    Faturamento = db.Column(db.Numeric(19, 2), nullable=True)

    DataCancelamento = db.Column(db.Date, nullable=True)
    OBS = db.Column(db.Unicode(150), nullable=True)

    IDVendedor = db.Column(db.Integer, db.ForeignKey("dbo.Vendedores.IDVendedor"), nullable=True)

    
    IDPainelMidia = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimPaineisMidia.IDDimPaineisMidia"),
        nullable=True,
    )

  
    IDDimFacesPaineis = db.Column(
        db.Integer,
        db.ForeignKey("Silver.DimFacesPaineis.IDDimFacesPaineis"),
        nullable=True,
    )

    contrato = db.relationship(
        "FatoControleContratosMidia",
        back_populates="itens",
        lazy="joined",
        foreign_keys=[IDFatoControleContratoMidia],
    )

    vendedor_rel = db.relationship(
        "Vendedores",
        back_populates="contratos_itens",
        lazy="joined",
        foreign_keys=[IDVendedor],
    )

    painel = db.relationship(
        "DimPaineisMidia",
        back_populates="contratos_itens",
        lazy="joined",
        foreign_keys=[IDPainelMidia],
    )

    dim_face = db.relationship(
        "DimFacesPaineis",
        back_populates="contratos_itens",
        lazy="joined",
        foreign_keys=[IDDimFacesPaineis],
    )

    painel_por_codponto = db.relationship(
        "DimPaineisMidia",
        primaryjoin="DimPaineisMidia.CodPonto==foreign(FatoControleContratosItensMidia.CodPonto)",
        viewonly=True,
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<FatoControleContratosItensMidia "
            f"ID={self.IDFatoControleContratosItensMidia} "
            f"Referencia={self.Referencia!r}>"
        )





class DimEmpresas(db.Model):
    __tablename__ = "DimEmpresas"
    __table_args__ = ({"schema": "Silver"},)
    IDEmpresa = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=True)
    CNPJ = db.Column(db.String(20), nullable=True)
    UF = db.Column(db.String(3), nullable=True)
    CEP = db.Column(db.String(10), nullable=True)
    CodigoPorte = db.Column(db.Integer, nullable=True)
    Pais = db.Column(db.Unicode(50), nullable=True)
    Email = db.Column(db.Unicode(100), nullable=True)
    Porte = db.Column(db.String(20), nullable=True)
    Bairro = db.Column(db.Unicode(100), nullable=True)
    Numero = db.Column(db.Unicode(20), nullable=True)
    TelefoneContato1 = db.Column(db.String(20), nullable=True)
    Municipio = db.Column(db.Unicode(100), nullable=True)
    Logradouro = db.Column(db.Unicode(150), nullable=True)
    CNAE = db.Column(db.String(20), nullable=True)
    Complemento = db.Column(db.Unicode(100), nullable=True)
    RazaoSocial = db.Column(db.Unicode(150), nullable=True)
    NomeFantasia = db.Column(db.Unicode(150), nullable=True)
    CapitalSocial = db.Column(db.BigInteger, nullable=True)
    TelefoneContato2 = db.Column(db.String(20), nullable=True)
    NaturezaJuridica = db.Column(db.Unicode(100), nullable=True)
    DescricaoCnae = db.Column(db.Unicode(150), nullable=True)
    DataInicioAtividades = db.Column(db.Date, nullable=True)
    DataSituacaoEspecial = db.Column(db.Date, nullable=True)
    DataOpcaoPeloSimples = db.Column(db.Date, nullable=True)
    DataSituacaoCadastral = db.Column(db.Date, nullable=True)
    DataExclusaoSimples = db.Column(db.Date, nullable=True)
    IdentificadorMatrizFilial = db.Column(db.Integer, nullable=True)
    DescricaoSituacaoCadastral = db.Column(db.String(20), nullable=True)
    DescricaoMotivoSituacaoCadastral = db.Column(db.String(20), nullable=True)
    DescricaoIdentificadorMatrizFilial = db.Column(db.String(20), nullable=True)
    DescricaoTipoLogradouro = db.Column(db.Unicode(20), nullable=True)
    DataAtualizacao = db.Column(DATETIME2, nullable=False)
    BitCliente = db.Column(db.Boolean, nullable=True)

    ocupacoes = db.relationship(
        "FatoOcupacaoPaineisMidia",
        back_populates="cliente",
        lazy="selectin",
        foreign_keys="FatoOcupacaoPaineisMidia.IDCliente",
    )

    contratos = db.relationship(
        "FatoControleContratosMidia",
        back_populates="empresa",
        lazy="selectin",
        foreign_keys="FatoControleContratosMidia.IDEmpresa",
    )

    def __repr__(self) -> str:
        return f"<DimEmpresas IDEmpresa={self.IDEmpresa} CNPJ={self.CNPJ!r} RazaoSocial={self.RazaoSocial!r}>"





class DimCustoMensalPainel(db.Model):
    __tablename__ = "DimCustoMensalPainel"
    __table_args__ = ({"schema": "Silver"},)

    IDCustoMensalPainel = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CodPonto = db.Column(db.Integer, nullable=True)
    Ano = db.Column(db.Integer, nullable=True)
    Mes = db.Column(db.Integer, nullable=True)
    DataAtualizacao = db.Column(db.Date, nullable=True)
    ValorMensal = db.Column(db.Numeric(19, 2), nullable=True)




class DimMargemPaineisMidia(db.Model):
    __tablename__ = "DimMargemPaineisMidia"
    __table_args__ = {"schema": "Silver"}

    IDDimMargemPaineisMidia = db.Column(db.Integer, primary_key=True, autoincrement=True)

    IDFatoControleContratoMidia = db.Column(db.Integer, nullable=True)

    DataLancamento = db.Column(db.Date, nullable=True)
    DataAssinaturaRenovacao = db.Column(db.Date, nullable=True)

    DataInicioPrevisto = db.Column(db.String(50), nullable=True)
    DataTerminoPrevisto = db.Column(db.String(50), nullable=True)

    Tipo = db.Column(db.String(200), nullable=True)

    TempoExibicaoDias = db.Column(db.Integer, nullable=True)
    QuantidadeParcelas = db.Column(db.Integer, nullable=True)

    NumeroContrato = db.Column(db.String(50), nullable=True)
    NumeroPrevia = db.Column(db.String(50), nullable=True)

    CodFace = db.Column(db.String(50), nullable=True)
    CodPonto = db.Column(db.Integer, nullable=True)

    Cota_txt = db.Column(db.Integer, nullable=True)
    Cota_int = db.Column(db.Integer, nullable=True)

    NumFaces = db.Column(db.Integer, nullable=True)

    TipoProd = db.Column(db.String(200), nullable=True)

    FaturamentoLiquidoMensalFinal = db.Column(db.Numeric(19, 4), nullable=True)

    Ano = db.Column(db.Integer, nullable=True)
    Mes = db.Column(db.Integer, nullable=True)

    ValorMensal = db.Column(db.Numeric(19, 4), nullable=True)
    FatorCapacidade = db.Column(db.Numeric(19, 6), nullable=True)

    CustoMensalAlocado = db.Column(db.Numeric(19, 4), nullable=True)

    MargemR_LiquidaFinal = db.Column(db.Numeric(19, 4), nullable=True)

    MargemPct_LiquidaFinal = db.Column(db.Numeric(10, 2), nullable=True)

    DataInicioPrevisto_dt = db.Column(db.Date, nullable=True)
    DataTerminoPrevisto_dt = db.Column(db.Date, nullable=True)









class DimCnaes(db.Model):
    __tablename__ = "DimCnaes"
    __table_args__ = {"schema": "Silver"}

    IDDimCnaes = db.Column(db.Integer, primary_key=True, autoincrement=True)

    cnaepadrao = db.Column(db.Integer, nullable=True)
    Descricao = db.Column(db.Unicode(600), nullable=True)
    Classe = db.Column(db.Unicode(100), nullable=True)
    Setor = db.Column(db.Unicode(600), nullable=True)

    DependeCredito = db.Column(db.Boolean, nullable=True)
    ConsumoDiscricionario = db.Column(db.Boolean, nullable=True)
    InsumoImportado = db.Column(db.Boolean, nullable=True)
    Exportador = db.Column(db.Boolean, nullable=True)
    PoderRepassePreco = db.Column(db.Boolean, nullable=True)

    MacroSetor = db.Column(db.Unicode(200), nullable=True)
    SubClasse = db.Column(db.Unicode(100), nullable=True)

    IDDimPublicoAlvo = db.Column(db.Integer, nullable=True)
    ScoreSetor = db.Column(db.Numeric(5, 2), nullable=True)
    ClassificacaoMacro = db.Column(db.Unicode(100), nullable=True)
    DataAtualizacao = db.Column(db.DateTime, nullable=True)
    Hex = db.Column(db.CHAR(7), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DimCnaes "
            f"IDDimCnaes={self.IDDimCnaes} "
            f"cnaepadrao={self.cnaepadrao} "
            f"Classe={self.Classe!r} "
            f"Setor={self.Setor!r} "
            f"ScoreSetor={self.ScoreSetor} "
            f"ClassificacaoMacro={self.ClassificacaoMacro!r}>"
        )






class DimClassificacacaoClientes(db.Model):
    __tablename__ = "DimClassificacacaoClientes"
    __table_args__ = {"schema": "Silver"}

    IDEmpresa = db.Column(db.Integer, primary_key=True, nullable=False)

    CNPJ = db.Column(db.String(20), nullable=True)
    RazaoSocial = db.Column(db.Unicode(150), nullable=True)

    ClasseValor = db.Column(db.String(16), nullable=False)
    DescricaoClasseValor = db.Column(db.String(36), nullable=False)

    QtdContratos = db.Column(db.Integer, nullable=True)
    QtdPontos = db.Column(db.Integer, nullable=True)
    QtdFaces = db.Column(db.Integer, nullable=True)

    TotalCotasCompradas = db.Column(db.BigInteger, nullable=True)
    TempoTotalExibicao = db.Column(db.BigInteger, nullable=True)

    TipoEscalaOperacional = db.Column(db.String(12), nullable=False)
    DescricaoTipoEscalaOperacional = db.Column(db.String(54), nullable=False)

    CNAE = db.Column(db.String(20), nullable=True)
    Porte = db.Column(db.String(20), nullable=True)

    DescricaoCNAE = db.Column(db.Unicode(600), nullable=True)
    ClasseCNAE = db.Column(db.Unicode(100), nullable=True)
    SetorCNAE = db.Column(db.Unicode(600), nullable=True)
    MacroSetorCNAE = db.Column(db.Unicode(200), nullable=True)

    DependeCredito = db.Column(db.Boolean, nullable=True)
    ConsumoDiscricionario = db.Column(db.Boolean, nullable=True)
    InsumoImportado = db.Column(db.Boolean, nullable=True)
    Exportador = db.Column(db.Boolean, nullable=True)
    PoderRepassePreco = db.Column(db.Boolean, nullable=True)

    CapitalSocial = db.Column(db.Numeric(18, 2), nullable=True)

    ClasseEstrutural = db.Column(db.String(18), nullable=False)

    QtdCidadesCliente = db.Column(db.Integer, nullable=True)
    QtdUFCliente = db.Column(db.Integer, nullable=True)
    QtdCidadesPainel = db.Column(db.Integer, nullable=True)
    QtdCEPPainel = db.Column(db.Integer, nullable=True)

    ItensMesmaCidade = db.Column(db.Integer, nullable=True)
    TotalItens = db.Column(db.Integer, nullable=True)

    PercItensMesmaCidade = db.Column(db.Numeric(9, 6), nullable=True)

    ClasseGeo = db.Column(db.String(8), nullable=False)
    ClassePotencial = db.Column(db.String(71), nullable=False)

    ReceitaTotal = db.Column(db.Numeric(38, 13), nullable=True)
    PercReceitaAcumulada = db.Column(db.Numeric(9, 6), nullable=True)

    IDCluster = db.Column(db.Integer, nullable=True)

    DataUltimaCompra = db.Column(db.Date, nullable=True)
    DiasDesdeUltimaCompra = db.Column(db.Integer, nullable=True)

    ValorUltimaCompra = db.Column(db.Numeric(18, 2), nullable=True)
    Receita12M = db.Column(db.Numeric(18, 2), nullable=True)

    TierImportancia = db.Column(db.String(12), nullable=True)
    ScoreImportancia = db.Column(db.SmallInteger, nullable=True)
    NivelPremium = db.Column(db.SmallInteger, nullable=True)

    IDLead = db.Column(db.Integer, nullable=True)
    Origem = db.Column(db.Unicode(100), nullable=True)

    BitComprasOutrasEmpresas = db.Column(db.Boolean, nullable=True)

    IDDimPublicoAlvo = db.Column(db.Integer, nullable=True)
    IDDimRecorrencia = db.Column(db.Integer, nullable=True)
    IDDimClasseSocial = db.Column(db.Integer, nullable=True)

    ClusterGrupoCliente = db.Column(db.Unicode(200), nullable=True)
    ClusterID = db.Column(db.Integer, nullable=True)
    ScoreRetornoCluster = db.Column(db.Numeric(18, 6), nullable=True)
    ScorePerfilEmpresa = db.Column(db.Numeric(18, 6), nullable=True)
    ClassificacaoPerfilEmpresa = db.Column(db.Unicode(200), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DimClassificacacaoClientes "
            f"IDEmpresa={self.IDEmpresa} "
            f"CNPJ={self.CNPJ!r} "
            f"ClasseValor={self.ClasseValor!r} "
            f"ClusterGrupoCliente={self.ClusterGrupoCliente!r}>"
        )








class DimCustoPainel(db.Model):
    __tablename__ = "DimCustoPainel"
    __table_args__ = {"schema": "Silver"}
        
   

    IDDimCustoPainel = db.Column(db.Integer, primary_key=True, autoincrement=True)

    Ano = db.Column(db.Integer, nullable=False)
    CodPonto = db.Column(db.Integer, nullable=False)

    Origem = db.Column(db.String(10), nullable=False)

    Valor = db.Column(db.Numeric(18, 2), nullable=False)

    DataCarga = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSDATETIME()"),
    )

    def __repr__(self) -> str:
        return (
            f"<DimCustoPainel ID={self.IDDimCustoPainel} "
            f"Ano={self.Ano} CodPonto={self.CodPonto} Origem={self.Origem} Valor={self.Valor}>"
        )




class DimCheckinHistorico(db.Model):
    __tablename__ = "DimCheckinHistorico"
    __table_args__ = ({"schema": "Silver"},)

    IDDimCheckinHistorico = db.Column(db.Integer, primary_key=True, autoincrement=True)
    DataAtualizacao = db.Column(DATETIME2(0), nullable=False, server_default=db.text("SYSDATETIME()"))
    DataChekin = db.Column(db.Date, nullable=False)

    IDEmpresa = db.Column(db.Integer, nullable=True)
    CNPJ = db.Column(db.String(20), nullable=True)
    RazaoSocial = db.Column(db.Unicode(200), nullable=True)

    IDFatoControleContratosMidia = db.Column(db.Integer, nullable=True)

    CodPonto = db.Column(db.Integer, nullable=False)
    CodFace = db.Column(db.Unicode(100), nullable=False)
    TipoPainel = db.Column(db.Unicode(100), nullable=True)
    TipoFace = db.Column(db.Unicode(100), nullable=True)

    NomeArquivoOriginal = db.Column(db.Unicode(760), nullable=True)
    NomeArquivoSalvo = db.Column(db.Unicode(760), nullable=False)

    CaminhoImagemPainel = db.Column(db.Unicode(1500), nullable=True)
    CaminhoImagemFundo = db.Column(db.Unicode(1500), nullable=True)
    CaminhoImagemUpload = db.Column(db.Unicode(1500), nullable=False)
    CaminhoImagemGerada = db.Column(db.Unicode(1500), nullable=False)

    UrlImagemUpload = db.Column(db.Unicode(500), nullable=True)
    UrlImagemGerada = db.Column(db.Unicode(500), nullable=True)

    BitChekin = db.Column(db.Boolean, nullable=False, server_default=db.text("0"))
    DataConfirmacao = db.Column(DATETIME2(0), nullable=True)

    IDUsuarioCriacao = db.Column(db.Integer, nullable=True)
    IDUsuarioConfirmacao = db.Column(db.Integer, nullable=True)

    Observacao = db.Column(db.Unicode(500), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DimCheckinHistorico "
            f"ID={self.IDDimCheckinHistorico} "
            f"CodPonto={self.CodPonto} "
            f"CodFace={self.CodFace!r} "
            f"BitChekin={self.BitChekin}>"
        )



class DimStatusContratos(db.Model):
    __tablename__ = "DimStatusContratos"
    __table_args__ = {"schema": "Silver"}

    IDDimStatusContratos = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Status = db.Column(db.String(100), nullable=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f"<DimStatusContratos IDDimStatusContratos={self.IDDimStatusContratos} Status={self.Status!r}>"