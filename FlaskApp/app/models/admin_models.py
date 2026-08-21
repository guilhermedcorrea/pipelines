from ..extensions import db
from sqlalchemy import Numeric,text
from datetime import datetime
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER



class FatoMovimentoFinanceiroEmpresas(db.Model):
    __tablename__ = "FatoMovimentoFinanceiroEmpresas"
    __table_args__ = {"schema": "Silver"}

    IDFatoMovimentoFinanceiroEmpresas = db.Column(db.BigInteger, primary_key=True, autoincrement=True)

    Sistema = db.Column(db.String(20), nullable=False)

    IDmovimentacaoFinanceira = db.Column(db.BigInteger, nullable=True)

    nCodTitulo = db.Column(db.String(300), nullable=True)

    IDEmpresaProprietaria = db.Column(db.Integer, nullable=True)

    DataCompetencia = db.Column(db.Date, nullable=True)
    DataVencimento = db.Column(db.Date, nullable=True)

    Status = db.Column(db.String(30), nullable=True)

    Categoria = db.Column(db.String(200), nullable=True)
    Nivel1 = db.Column(db.String(200), nullable=True)

    DataPagamento = db.Column(db.Date, nullable=True)

    Tipo = db.Column(db.String(200), nullable=True)

    ReferenciaPedidoOS = db.Column(db.String(300), nullable=True)

    Movimento = db.Column(db.String(50), nullable=True)

    Valor = db.Column(db.Numeric(18, 2), nullable=True)






class FatoMovimentoFinanceiroGranatumMidia(db.Model):
    __tablename__ = "FatoMovimentoFinanceiroMidia"
    __table_args__ = {"schema": "Silver"}

    IDFatoMovimentoFinanceiroGranatumMidia = db.Column(
        db.Integer,
        primary_key=True,
        autoincrement=True
    )

    DataVencimento = db.Column(db.Date, nullable=True)          
    DataPagamento = db.Column(db.Date, nullable=True)          
    DataCompetencia = db.Column(db.Date, nullable=True)        

    Valor = db.Column(Numeric(18, 2), nullable=True)           

    Categoria = db.Column(db.Unicode(200), nullable=True)      
    Movimento = db.Column(db.String(30), nullable=True)        

    ClienteFornecedor = db.Column(db.Unicode(300), nullable=True)   
    DocumentoClienteFornecedor = db.Column(db.String(50), nullable=True) 

    CentroCustoLucro = db.Column(db.Unicode(500), nullable=True)   
    Periodicidade = db.Column(db.String(50), nullable=True)       

    Conta = db.Column(db.Unicode(100), nullable=True)            
    FormaPagamento = db.Column(db.Unicode(100), nullable=True)     

    TipoMovimento = db.Column(db.String(70), nullable=True)       
    NumeroParcela = db.Column(db.Integer, nullable=True)          

    DataAtualizacao = db.Column(db.DateTime, nullable=True)        
    Referencia = db.Column(db.String(300), nullable=True)         






class FatoMovimentacaoFinanceiraOmie(db.Model):
    __tablename__ = "FatoMovimentacaoFinanceiroOmie"
    __table_args__ = {"schema": "Silver"}

    IDMovimentacaoFinanceiro = db.Column(db.Integer, primary_key=True, nullable=False)  

    IDEmpresaProprietaria = db.Column(db.Integer, nullable=False)  

    nCodTitulo = db.Column(db.BigInteger, nullable=True)           
    cCodIntTitulo = db.Column(db.String(80), nullable=True)        
    cNumTitulo = db.Column(db.String(40), nullable=True)         

    dDtEmissao = db.Column(db.Date, nullable=True)               
    dDtVenc = db.Column(db.Date, nullable=True)                   
    dDtPrevisao = db.Column(db.Date, nullable=True)               
    dDtPagamento = db.Column(db.Date, nullable=True)              

    nCodCliente = db.Column(db.BigInteger, nullable=True)         
    cCPFCNPJCliente = db.Column(db.String(30), nullable=True)     

    nCodCtr = db.Column(db.BigInteger, nullable=True)             
    cNumCtr = db.Column(db.String(30), nullable=True)            

    nCodOS = db.Column(db.BigInteger, nullable=True)              
    cNumOS = db.Column(db.String(20), nullable=True)              

    nCodCC = db.Column(db.BigInteger, nullable=True)              
    cStatus = db.Column(db.String(120), nullable=True)            

    cNatureza = db.Column(db.String(1), nullable=True)            
    cNaturezaDescricao = db.Column(db.String(23), nullable=False) 

    cTipo = db.Column(db.String(20), nullable=True)               
    cTipoDescricao = db.Column(db.Unicode(150), nullable=True)    

    cOperacao = db.Column(db.String(10), nullable=True)           
    cOperacaoDescricao = db.Column(db.String(30), nullable=False)  

    cNumDocFiscal = db.Column(db.String(40), nullable=True)       
    cCodCateg = db.Column(db.String(40), nullable=True)            
    Nivel1 = db.Column(db.String(150), nullable=True)            

    cNumParcela = db.Column(db.String(15), nullable=True)         

    nValorTitulo = db.Column(Numeric(18, 4), nullable=True)       
    nValorPIS = db.Column(Numeric(18, 4), nullable=True)
    cRetPIS = db.Column(db.String(1), nullable=True)             
    nValorCOFINS = db.Column(Numeric(18, 4), nullable=True)
    cRetCOFINS = db.Column(db.String(1), nullable=True)
    nValorCSLL = db.Column(Numeric(18, 4), nullable=True)
    cRetCSLL = db.Column(db.String(1), nullable=True)
    nValorIR = db.Column(Numeric(18, 4), nullable=True)
    cRetIR = db.Column(db.String(1), nullable=True)
    nValorISS = db.Column(Numeric(18, 4), nullable=True)
    cRetISS = db.Column(db.String(1), nullable=True)
    nValorINSS = db.Column(Numeric(18, 4), nullable=True)
    cRetINSS = db.Column(db.String(1), nullable=True)

    nCodProjeto = db.Column(db.BigInteger, nullable=True)        

    observacao = db.Column(db.Text, nullable=True)                

    cCodVendedor = db.Column(db.BigInteger, nullable=True)        
    nCodComprador = db.Column(db.BigInteger, nullable=True)       

    cCodigoBarras = db.Column(db.String(100), nullable=True)       
    cNSU = db.Column(db.String(120), nullable=True)               
    nCodNF = db.Column(db.BigInteger, nullable=True)             

    dDtRegistro = db.Column(db.Date, nullable=True)              
    cNumBoleto = db.Column(db.String(50), nullable=True)          
    cChaveNFe = db.Column(db.String(60), nullable=True)            

    cOrigem = db.Column(db.String(10), nullable=True)             
    cOrigemDescricao = db.Column(db.String(53), nullable=False)    

    nCodTitRepet = db.Column(db.BigInteger, nullable=True)         
    cGrupo = db.Column(db.String(40), nullable=True)              

    nCodMovCC = db.Column(db.BigInteger, nullable=True)            
    nValorMovCC = db.Column(Numeric(18, 4), nullable=True)         
    nCodMovCCRepet = db.Column(db.BigInteger, nullable=True)     

    nCodBaixa = db.Column(db.BigInteger, nullable=True)           
    dDtCredito = db.Column(db.Date, nullable=True)                 
    dDtConcilia = db.Column(db.Date, nullable=True)                
    cHrConcilia = db.Column(db.Time, nullable=True)               
    cUsConcilia = db.Column(db.String(30), nullable=True)          

    dDtInc = db.Column(db.Date, nullable=True)                   
    cHrInc = db.Column(db.Time, nullable=True)                     
    cUsInc = db.Column(db.String(30), nullable=True)               

    dDtAlt = db.Column(db.Date, nullable=True)                    
    cHrAlt = db.Column(db.Time, nullable=True)                     
    cUsAlt = db.Column(db.String(30), nullable=True)              

    cLiquidado = db.Column(db.String(1), nullable=True)            

    nValPago = db.Column(Numeric(18, 4), nullable=True)
    nValAberto = db.Column(Numeric(18, 4), nullable=True)
    nDesconto = db.Column(Numeric(18, 4), nullable=True)
    nJuros = db.Column(Numeric(18, 4), nullable=True)
    nMulta = db.Column(Numeric(18, 4), nullable=True)
    nValLiquido = db.Column(Numeric(18, 4), nullable=True)

    DataHoraCarga = db.Column(db.DateTime, nullable=False)        

    IdChaveOmie = db.Column(db.BigInteger, nullable=True)       

    TipoLancamento = db.Column(db.String(10), nullable=True) 
    TipoLancamentoDescricao = db.Column(db.String(87), nullable=False)  

    EventKey = db.Column(db.String(200), nullable=True)           
    DocumentoKey = db.Column(db.String(200), nullable=True)        

    ClienteRazaoSocial = db.Column(db.Unicode(200), nullable=True)    
    ClienteNomeFantasia = db.Column(db.Unicode(200), nullable=True)    
    ClienteCnpjCpf = db.Column(db.Unicode(30), nullable=True)         
    ClienteEmail = db.Column(db.Unicode(600), nullable=True)         
    ClienteCidade = db.Column(db.Unicode(120), nullable=True)        
    ClienteEstado = db.Column(db.Unicode(10), nullable=True)
    Tipo = db.Column(db.String(20), nullable=True)            



class DimEmpresaProprietaria(db.Model):
    __tablename__ = "EmpresaProprietaria"
    __table_args__ = {"schema": "dbo"}

    IDEmpresaProprietaria = db.Column(
        db.Integer,
        primary_key=True,
        autoincrement=True
    )

    RazaoSocial = db.Column(db.Unicode(200), nullable=True)     
    CNPJ = db.Column(db.String(40), nullable=True)              
    CNAE = db.Column(db.String(40), nullable=True)              
    DescricaoCnae = db.Column(db.Unicode(100), nullable=True)   
    Logo = db.Column(db.Unicode(500), nullable=True)         
    BitAtivo = db.Column(db.Boolean, nullable=True)            







class PedidoOmie(db.Model):
    __tablename__ = "PedidoOmie"
    __table_args__ = {"schema": "dbo"}

    IDPedidoOmie = db.Column(db.Integer, primary_key=True, autoincrement=True)

    NumeroPedidoOmie = db.Column(db.String(100), nullable=True)
    CodigoPedidoOmie = db.Column(db.BigInteger, nullable=False)

    CodigoClienteOmie = db.Column(db.BigInteger, nullable=True)
    CodigoParcelaOmie = db.Column(db.String(50), nullable=True)
    OrigemPedido = db.Column(db.String(50), nullable=True)

    DataPrevisao = db.Column(db.Date, nullable=True)
    EtapaOmie = db.Column(db.BigInteger, nullable=True)

    QtdeParcelas = db.Column(db.Integer, nullable=True)
    QuantidadeItens = db.Column(db.Integer, nullable=True)

    Bloqueado = db.Column(db.String(1), nullable=True)  
    CodVendedorOmie = db.Column(db.BigInteger, nullable=True)

    CodigoCategoria = db.Column(db.String(50), nullable=True)
    CodigoContaCorrente = db.Column(db.BigInteger, nullable=True)

    ConsumidorFinal = db.Column(db.String(1), nullable=True)

    DadosAdicionaisNF = db.Column(db.String(1000), nullable=True)
    EnviarEmail = db.Column(db.String(1), nullable=True)  
    EnviarPIX = db.Column(db.String(1), nullable=True)   

    NumeroPedidoCliente = db.Column(db.String(100), nullable=True)
    UtilizarEmails = db.Column(db.String(2000), nullable=True)

    CodigoTransportadora = db.Column(db.BigInteger, nullable=True)
    EspecieVolumes = db.Column(db.String(100), nullable=True)
    ModalidadeFrete = db.Column(db.String(5), nullable=True)

    PesoBrutoFrete = db.Column(db.Numeric(18, 3), nullable=True)
    PesoLiquidoFrete = db.Column(db.Numeric(18, 3), nullable=True)
    QuantidadeVolumes = db.Column(db.Integer, nullable=True)

    NaoExportacao = db.Column(db.String(1), nullable=True)  

    BaseCalculoICMS = db.Column(db.Numeric(18, 4), nullable=True)
    ValorIPI = db.Column(db.Numeric(18, 4), nullable=True)
    ValorCofins = db.Column(db.Numeric(18, 4), nullable=True)
    ValorICMS = db.Column(db.Numeric(18, 4), nullable=True)
    ValorMercadorias = db.Column(db.Numeric(18, 4), nullable=True)
    ValorPIS = db.Column(db.Numeric(18, 4), nullable=True)
    ValorTotalPedido = db.Column(db.Numeric(18, 4), nullable=True)

    Autorizado = db.Column(db.String(1), nullable=True)  
    cImpAPI = db.Column(db.String(1), nullable=True)    

    Faturado = db.Column(db.String(1), nullable=True)        
    Cancelado = db.Column(db.String(1), nullable=True)        
    Denegado = db.Column(db.String(1), nullable=True)          
    Devolvido = db.Column(db.String(1), nullable=True)         
    DevolvidoParcial = db.Column(db.String(1), nullable=True)  

    dAlt = db.Column(db.Date, nullable=True)
    dFat = db.Column(db.Date, nullable=True)
    dInc = db.Column(db.Date, nullable=True)

    hAlt = db.Column(db.Time, nullable=True)
    hFat = db.Column(db.Time, nullable=True)
    hInc = db.Column(db.Time, nullable=True)

    uAlt = db.Column(db.String(40), nullable=True)
    uFat = db.Column(db.String(40), nullable=True)
    uInc = db.Column(db.String(40), nullable=True)

    IDEmpresaProprietaria = db.Column(db.Integer, nullable=True)

    DataCriacaoRegistro = db.Column(db.DateTime, nullable=False)
    DataAtualizacaoRegistro = db.Column(db.DateTime, nullable=False)

    NomeEtapaOmie = db.Column(db.Unicode(80), nullable=True) 

   
    Itens = db.relationship(
        "PedidoOmieItem",
        back_populates="Pedido",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PedidoOmieItem(db.Model):
    __tablename__ = "PedidoOmieItem"
    __table_args__ = {"schema": "dbo"}

    IDPedidoOmieItem = db.Column(db.Integer, primary_key=True, autoincrement=True)

    IDPedidoOmie = db.Column(
        db.Integer,
        db.ForeignKey("dbo.PedidoOmie.IDPedidoOmie", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    NumeroItem = db.Column(db.Integer, nullable=True)

    CodigoItemOmie = db.Column(db.BigInteger, nullable=True)
    CodigoItemIntegracao = db.Column(db.String(100), nullable=True)

    SimplesNacional = db.Column(db.String(1), nullable=True)
    CodigoCategoriaItem = db.Column(db.String(50), nullable=True)
    CodigoLocalEstoque = db.Column(db.BigInteger, nullable=True)

    DadosAdicionaisItem = db.Column(db.String(1000), nullable=True)

    ItemPedidoCompra = db.Column(db.Integer, nullable=True)

    NaoGerarFinanceiro = db.Column(db.String(1), nullable=True)   
    NaoMovimentarEstoque = db.Column(db.String(1), nullable=True)  
    NaoSomarTotal = db.Column(db.String(1), nullable=True)    

    NumeroPedidoCompra = db.Column(db.String(100), nullable=True)

    PesoBrutoItem = db.Column(db.Numeric(18, 3), nullable=True)
    PesoLiquidoItem = db.Column(db.Numeric(18, 3), nullable=True)

    CFOP = db.Column(db.String(10), nullable=True)
    CNPJFabricante = db.Column(db.String(20), nullable=True)

    CodigoProduto = db.Column(db.String(100), nullable=True)
    CodigoProdutoOmie = db.Column(db.BigInteger, nullable=True)

    CodigoTabelaPreco = db.Column(db.Integer, nullable=True)

    DescricaoProduto = db.Column(db.String(200), nullable=True)

    EAN = db.Column(db.String(50), nullable=True)
    IndicadorEscala = db.Column(db.String(10), nullable=True)
    MotivoICMSDesonerado = db.Column(db.String(100), nullable=True)

    NCM = db.Column(db.String(20), nullable=True)

    PercentualDesconto = db.Column(db.Numeric(9, 4), nullable=True)

    Quantidade = db.Column(db.Numeric(18, 4), nullable=True)
    Reservado = db.Column(db.String(1), nullable=True) 

    TipoDesconto = db.Column(db.String(20), nullable=True)
    Unidade = db.Column(db.String(10), nullable=True)

    ValorDeducao = db.Column(db.Numeric(18, 4), nullable=True)
    ValorDesconto = db.Column(db.Numeric(18, 4), nullable=True)
    ValorICMSDesonerado = db.Column(db.Numeric(18, 4), nullable=True)

    ValorMercadoria = db.Column(db.Numeric(18, 4), nullable=True)
    ValorTotal = db.Column(db.Numeric(18, 4), nullable=True)
    ValorUnitario = db.Column(db.Numeric(18, 6), nullable=True)

    AliqICMS = db.Column(db.Numeric(9, 4), nullable=True)
    BaseICMS = db.Column(db.Numeric(18, 4), nullable=True)
    ValorICMSItem = db.Column(db.Numeric(18, 4), nullable=True)

    AliqIPI = db.Column(db.Numeric(9, 4), nullable=True)
    BaseIPI = db.Column(db.Numeric(18, 4), nullable=True)
    ValorIPIItem = db.Column(db.Numeric(18, 4), nullable=True)

    AliqPIS = db.Column(db.Numeric(9, 4), nullable=True)
    BasePIS = db.Column(db.Numeric(18, 4), nullable=True)
    ValorPISItem = db.Column(db.Numeric(18, 4), nullable=True)

    AliqCOFINS = db.Column(db.Numeric(9, 4), nullable=True)
    BaseCOFINS = db.Column(db.Numeric(18, 4), nullable=True)
    ValorCOFINSItem = db.Column(db.Numeric(18, 4), nullable=True)

    DataCriacaoRegistro = db.Column(db.DateTime, nullable=False)
    DataAtualizacaoRegistro = db.Column(db.DateTime, nullable=False)

  
    Pedido = db.relationship("PedidoOmie", back_populates="Itens", lazy="select")







class OrdemServicosOmie(db.Model):
    __tablename__ = "OrdemServicosOmie"
    __table_args__ = {"schema": "dbo"}

    IDOrdemServicosOmie = db.Column(db.Integer, primary_key=True, autoincrement=True)

    IDEmpresaProprietaria = db.Column(db.Integer, nullable=False)

    Cabecalho_cCodIntCli = db.Column(db.Unicode(120), nullable=True)
    Cabecalho_cCodIntOS = db.Column(db.Unicode(120), nullable=True)
    Cabecalho_cCodParc = db.Column(db.Unicode(20), nullable=True)
    Cabecalho_cEtapa = db.Column(db.Unicode(20), nullable=True)
    Cabecalho_cNumOS = db.Column(db.Unicode(50), nullable=True)

    Cabecalho_dDtPrevisao = db.Column(db.Date, nullable=True)

    Cabecalho_nCodCli = db.Column(db.BigInteger, nullable=True)
    Cabecalho_nCodOS = db.Column(db.BigInteger, nullable=True)
    Cabecalho_nCodVend = db.Column(db.BigInteger, nullable=True)

    Cabecalho_nQtdeParc = db.Column(db.Integer, nullable=True)

    Cabecalho_nValorTotal = db.Column(db.Numeric(19, 4), nullable=True)
    Cabecalho_nValorTotalImpRet = db.Column(db.Numeric(19, 4), nullable=True)

    InfoAdic_cCidPrestServ = db.Column(db.Unicode(200), nullable=True)
    InfoAdic_cCodART = db.Column(db.Unicode(80), nullable=True)
    InfoAdic_cCodCateg = db.Column(db.Unicode(60), nullable=True)
    InfoAdic_cCodObra = db.Column(db.Unicode(80), nullable=True)
    InfoAdic_cContato = db.Column(db.Unicode(200), nullable=True)

    InfoAdic_cDadosAdicNF = db.Column(db.Unicode(2000), nullable=True)

    InfoAdic_cNumContrato = db.Column(db.Unicode(120), nullable=True)
    InfoAdic_cNumPedido = db.Column(db.Unicode(120), nullable=True)
    InfoAdic_cNumRecibo = db.Column(db.Unicode(60), nullable=True)

    InfoAdic_dDataRps = db.Column(db.Date, nullable=True)

    InfoAdic_nCodCC = db.Column(db.BigInteger, nullable=True)
    InfoAdic_nCodProj = db.Column(db.BigInteger, nullable=True)

    Email_cEnvBoleto = db.Column(db.Unicode(5), nullable=True)
    Email_cEnvLink = db.Column(db.Unicode(5), nullable=True)
    Email_cEnvPix = db.Column(db.Unicode(5), nullable=True)
    Email_cEnvRecibo = db.Column(db.Unicode(5), nullable=True)
    Email_cEnvViaUnica = db.Column(db.Unicode(5), nullable=True)
    Email_cEnviarPara = db.Column(db.Unicode(320), nullable=True)

    Observacoes_cObsOS = db.Column(db.Unicode(2000), nullable=True)

    InfoCadastro_cAmbiente = db.Column(db.Unicode(10), nullable=True)
    InfoCadastro_cCancelada = db.Column(db.Unicode(5), nullable=True)
    InfoCadastro_cFaturada = db.Column(db.Unicode(5), nullable=True)

    InfoCadastro_cHrAlt = db.Column(db.Unicode(20), nullable=True)
    InfoCadastro_cHrCanc = db.Column(db.Unicode(20), nullable=True)
    InfoCadastro_cHrFat = db.Column(db.Unicode(20), nullable=True)
    InfoCadastro_cHrInc = db.Column(db.Unicode(20), nullable=True)

    InfoCadastro_cOrigem = db.Column(db.Unicode(20), nullable=True)

    InfoCadastro_dDtAlt = db.Column(db.Date, nullable=True)
    InfoCadastro_dDtCanc = db.Column(db.Date, nullable=True)
    InfoCadastro_dDtFat = db.Column(db.Date, nullable=True)
    InfoCadastro_dDtInc = db.Column(db.Date, nullable=True)

    InfoCadastro_uAlt = db.Column(db.Unicode(80), nullable=True)
    InfoCadastro_uInc = db.Column(db.Unicode(80), nullable=True)

    Parcela1_dDtVenc = db.Column(db.Date, nullable=True)
    Parcela1_nDias = db.Column(db.Integer, nullable=True)
    Parcela1_nParcela = db.Column(db.Integer, nullable=True)

    Parcela1_nPercentual = db.Column(db.Numeric(9, 4), nullable=True)
    Parcela1_nValor = db.Column(db.Numeric(19, 4), nullable=True)

   
    JsonCompleto = db.Column(db.Text, nullable=True)

    
    DataCargaUtc = db.Column(db.DateTime, nullable=False)






class DimCalendario(db.Model):
    __tablename__ = "DimCalendario"
    __table_args__ = {"schema": "Silver"}

    data = db.Column("Data", db.Date, primary_key=True, nullable=False)

    ano = db.Column("Ano", db.Integer, nullable=False)
    mes = db.Column("Mes", db.SmallInteger, nullable=False)      
    dia = db.Column("Dia", db.SmallInteger, nullable=False)    
    ano_mes = db.Column("AnoMes", db.Integer, nullable=False)

    trimestre = db.Column("Trimestre", db.SmallInteger, nullable=False)     
    dia_semana_iso = db.Column("DiaSemanaISO", db.SmallInteger, nullable=False) 

    eh_fim_de_semana = db.Column("EhFimDeSemana", db.Boolean, nullable=False) 
    semana_ano_iso = db.Column("SemanaAnoISO", db.SmallInteger, nullable=False) 

    inicio_semana = db.Column("InicioSemana", db.Date, nullable=False)
    fim_semana = db.Column("FimSemana", db.Date, nullable=False)

    quinzena = db.Column("Quinzena", db.SmallInteger, nullable=False)       
    inicio_quinzena = db.Column("InicioQuinzena", db.Date, nullable=False)
    fim_quinzena = db.Column("FimQuinzena", db.Date, nullable=False)

    bi_semana_numero = db.Column("BiSemanaNumero", db.Integer, nullable=False)
    inicio_bi_semana = db.Column("InicioBiSemana", db.Date, nullable=False)
    fim_bi_semana = db.Column("FimBiSemana", db.Date, nullable=False)

    def __repr__(self) -> str:
        return f"<DimCalendario data={self.data} ano_mes={self.ano_mes}>"







class DimProdutoAuvo(db.Model):
    __tablename__ = "DimProdutoAuvo"
    __table_args__ = {"schema": "Silver"}

   
    id_dim_produto_auvo = db.Column("IDDimProdutoAuvo", db.Integer, primary_key=True, autoincrement=True)

  
    produto_id_auvo = db.Column("ProdutoIdAuvo",UNIQUEIDENTIFIER,nullable=False,unique=True,index=True,)

    external_id = db.Column("ExternalId", db.Unicode(100), nullable=True)
    codigo = db.Column("Codigo", db.BigInteger, nullable=True)

    nome = db.Column("Nome", db.Unicode(400), nullable=True)
    descricao = db.Column("Descricao", db.Unicode(1000), nullable=True)

    category_id = db.Column("CategoryId", db.Integer, nullable=True)
    associated_equipment_id = db.Column("AssociatedEquipmentId", db.Integer, nullable=True)

    unitario_valor_texto = db.Column("UnitarioValorTexto", db.Unicode(50), nullable=True)
    unitario_custo_texto = db.Column("UnitarioCustoTexto", db.Unicode(50), nullable=True)

    unitario_valor = db.Column("UnitarioValor", db.Numeric(18, 2), nullable=True)
    unitario_custo = db.Column("UnitarioCusto", db.Numeric(18, 2), nullable=True)

    minimum_stock = db.Column("MinimumStock", db.Numeric(18, 4), nullable=True)
    total_stock = db.Column("TotalStock", db.Numeric(18, 4), nullable=True)

    ativo = db.Column("Ativo", db.Boolean, nullable=True)

    id_auvo = db.Column("IdAuvo", db.BigInteger, nullable=True)

    product_specifications_json = db.Column("ProductSpecificationsJson", db.UnicodeText, nullable=True)
    employees_stock_json = db.Column("EmployeesStockJson", db.UnicodeText, nullable=True)
    uri_attachments_json = db.Column("UriAttachmentsJson", db.UnicodeText, nullable=True)

   
    criado_em = db.Column("CriadoEm",db.DateTime, nullable=False, server_default=text("sysdatetime()"),)

    id_dim_produto = db.Column("IDDimProduto", db.Integer, nullable=True)

    bit_ativo = db.Column("BitAtivo", db.Boolean, nullable=True)

    def __repr__(self) -> str:
        return f"<DimProdutoAuvo id={self.id_dim_produto_auvo} produto_id_auvo={self.produto_id_auvo}>"








class DimRecorrencia(db.Model):
    __tablename__ = "DimRecorrencia"
    __table_args__ = {"schema": "Silver"}
    IDDimRecorrencia = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDEmpresa = db.Column(db.Integer, nullable=True, index=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=True, index=True)
    Frequencia12M = db.Column(db.Integer, nullable=True)
    ClasseFrequencia = db.Column(db.String(2), nullable=True, index=True)
    DataUltimaAquisicao = db.Column(db.Date, nullable=True)
    DiasDesdeUltimaAquisicao = db.Column(db.Integer, nullable=True)
    ClasseRecencia = db.Column(db.Unicode(80), nullable=True, index=True)
    DataAtualizacao = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<DimRecorrencia IDDimRecorrencia={self.IDDimRecorrencia} "
            f"IDEmpresa={self.IDEmpresa} "
            f"ClasseFrequencia={self.ClasseFrequencia} "
            f"ClasseRecencia={self.ClasseRecencia}>"
        )
    






class DimPublicoAlvo(db.Model):
    __tablename__ = "DimPublicoAlvo"
    __table_args__ = {"schema": "Silver"}

    IDDimPublicoAlvo = db.Column(db.Integer, primary_key=True, autoincrement=True)

    NomePerfil = db.Column(db.Unicode(100), nullable=True)
    TipoUsoTerritorio = db.Column(db.Unicode(100), nullable=True)
    FaixaEconomica = db.Column(db.Unicode(100), nullable=True)
    TipoDemanda = db.Column(db.Unicode(100), nullable=True)
    Descricao = db.Column(db.Unicode(500), nullable=True)

    BitAtivo = db.Column(db.Boolean, nullable=True)

    def __repr__(self) -> str:
        return f"<DimPublicoAlvo IDDimPublicoAlvo={self.IDDimPublicoAlvo} NomePerfil={self.NomePerfil!r}>"
    






