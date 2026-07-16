from ..extensions import db
from sqlalchemy.sql import func
from datetime import datetime
from sqlalchemy import text


class ShempoModel(db.Model):
    """Base de todos os models legados armazenados no banco Shempo."""

    __abstract__ = True
    __bind_key__ = "shempo"


class Familia(ShempoModel):
    __tablename__ = 'Familias'
    FamiliaID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeFamilia = db.Column(db.String(40))
    BitAtivo = db.Column(db.Boolean)
    DescricaoFamilia = db.Column(db.String)


class Pmv(ShempoModel):
    __tablename__ = 'Pmv'
    PmvID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomePMV = db.Column(db.String(50))
    FamiliaID = db.Column(db.Integer)
    EmpresaID = db.Column(db.Integer)
    Classe = db.Column(db.String(20))
    BitAtivo = db.Column(db.Boolean)


class Produto(ShempoModel):
    __tablename__ = 'Produto'
    IDItem = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ReferenciaExterna = db.Column(db.Integer)
    IDCategoriaProduto = db.Column(db.Integer)
    FamiliaID = db.Column(db.Integer)
    NomeProduto = db.Column(db.String(200))
    NomeTecnico = db.Column(db.String)
    Modelo = db.Column(db.String)
    Descricao = db.Column(db.String)
    BitAtivo = db.Column(db.Boolean)
    IDAtivo = db.Column(db.Integer)
    PmvID = db.Column(db.Integer)
    ClassificaAtivo = db.Column(db.Boolean)
    Chassi = db.Column(db.String)
    Renavam = db.Column(db.String)
    BitPMV = db.Column(db.Boolean)
    IDDepartamento = db.Column(db.Integer)
    ControlaLote = db.Column(db.Boolean)
    ControlaNumerodeSerie = db.Column(db.Boolean)
    PrazoMedioRecebimento = db.Column(db.Integer)
    IDEstadoItem = db.Column(db.Integer)



class Caracteristica(ShempoModel):
    __tablename__ = 'Caracteristicas'
    IDCaracteristica = db.Column(db.Integer, primary_key=True, autoincrement=True)
    FamiliaID = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    Caracteristica = db.Column(db.String(30))
    BitAtivo = db.Column(db.Boolean)
    Valor = db.Column(db.String)
    IDCategoria = db.Column(db.Integer)
    BitFiltro = db.Column(db.Boolean)



class PontosEuro(ShempoModel):
    __tablename__ = 'PontosEuro'
    EuroID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeEstoque = db.Column(db.String(50))
    EmpresaID = db.Column(db.Integer)
    CodPonto = db.Column(db.Integer)
    Municipio = db.Column(db.String(50))
    UF = db.Column(db.String(3))
    TipoLogradouro = db.Column(db.String(50))
    Logradouro = db.Column(db.String(100))
    Numero = db.Column(db.Integer)
    Cep = db.Column(db.String(20))
    Bairro = db.Column(db.String(40))
    Referencia = db.Column(db.String(60))
    Faces = db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean)


class ShempoTable(ShempoModel):
    __tablename__ = 'Shempo'
    ShempoId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomePonto = db.Column(db.String(50))
    Municipio = db.Column(db.String(50))
    EmpresaID = db.Column(db.Integer)
    UF = db.Column(db.String(3))
    TipoLogradouro = db.Column(db.String(50))
    Logradouro = db.Column(db.String(100))
    Numero = db.Column(db.Integer)
    Cep = db.Column(db.String(20))
    Bairro = db.Column(db.String(40))
    Referencia = db.Column(db.String(60))
    Faces = db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean)



class Saldo(ShempoModel):
    __tablename__ = 'Saldo'
    IDSaldo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Quantidade = db.Column(db.Integer)
    DataAtualizacao = db.Column(db.DateTime, server_default=func.getdate(), onupdate=func.getdate())
    IDItem = db.Column(db.Integer)
    ProprietarioID = db.Column(db.Integer)



class CategoriasProdutos(ShempoModel):
    __tablename__ = 'CategoriasProdutos'
    IDCategoria = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeCategoria = db.Column(db.String(30))
    BitAtivo = db.Column(db.Boolean)



class Empresa(ShempoModel):
    __tablename__ = 'Empresas'
    EmpresaID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeEmpresa = db.Column(db.String(255), nullable=False)
    CNPJ = db.Column(db.String(18), unique=True, nullable=False)
    BitAtivo = db.Column(db.Boolean, nullable=False, default=True)
    ReferenciaExternaEmpresa = db.Column(db.String(255))
    CidadeEmpresa = db.Column(db.String(255))
    UF = db.Column(db.String(2))
    CEP  = db.Column(db.String)
    Numero  = db.Column(db.String)
    ENDERECO = db.Column(db.String(255))
    BAIRRO = db.Column(db.String(255))
    CNAE = db.Column(db.Integer)
    COMPLEMENTO = db.Column(db.String(255))
    CodigoPorte = db.Column(db.Integer)
    NomeFantasia = db.Column(db.String(255))
    CapitalSocial = db.Column(db.Numeric(18, 2))
    Telefone = db.Column(db.String(50))
    NaturezaJuridica = db.Column(db.String(255))
    SituacaoEspecial = db.Column(db.String(255))
    SituacaoCadastral = db.Column(db.Integer)
    CodigoNaturezaJuridica = db.Column(db.Integer)
    DataInicioAtividade = db.Column(db.Date)
    IdentificadorMatrizFilial = db.Column(db.Integer)
    QualificacaoResponsavel = db.Column(db.Integer)
    DescricaoSituacaoCadastral = db.Column(db.String(255))
    MotivoSituacaoCadastral = db.Column(db.String(255))
    DescricaoIdentificadorMatrizFilial = db.Column(db.String(255))
  



class Usuario(ShempoModel):
    __tablename__ = 'Usuarios'
    IDUsuario = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeUsuario = db.Column(db.String(50))
    EmailUsuario = db.Column(db.String(50))
    Senha = db.Column(db.String(50))
    BitAtivo = db.Column(db.Boolean)



class Movimentacao(ShempoModel):
    __tablename__ = "Movimentacao"
    IDMovimentacao = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDUsuario = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    Quantidade = db.Column(db.Integer)
    IDProprietarioOrigem = db.Column(db.Integer)
    CodPontoOrigem = db.Column(db.Integer)
    IDProprietarioDestino = db.Column(db.Integer)
    CodPontoDestino = db.Column(db.Integer)
    NomeMovimentacao = db.Column(db.String)
    DataMovimentacao = db.Column(db.DateTime, default=datetime.utcnow)
    TipoEstoqueOrigem = db.Column(db.String)
    TipoEstoqueDestino = db.Column(db.String)
    NumeroLoteOrigem = db.Column(db.String)
    NumeroLoteDestino = db.Column(db.String)
    IDTipoEstoqueOrigem = db.Column(db.Integer)
    IDTipoEstoqueDestino = db.Column(db.Integer)




class OS(ShempoModel):
    __tablename__ = 'OS'
    IDOs = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Data = db.Column(db.DateTime)
    Endereco = db.Column(db.String(100))
    EmpresaID = db.Column(db.Integer)
    IDFuncionario = db.Column(db.Integer)
    StatusOS = db.Column(db.String(10))
    ProprietarioID = db.Column(db.Integer)



class OsItens(ShempoModel):
    __tablename__ = 'OsItens'
    IDOSIten = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer)
    IDOs = db.Column(db.Integer)
    IDMovimentacao = db.Column(db.Integer)
    Quantidade = db.Column(db.Integer)
    ValorParcela  = db.Column(db.Float)
    SequenciaItem = db.Column(db.Integer)
    Placa = db.Column(db.String)
    ObservacoesServico = db.Column(db.String)
    CodigoInternoOS = db.Column(db.Integer)
    NumeroOsOmie = db.Column(db.Integer)
    NumeroMedicao = db.Column(db.Integer)



class Ativo(ShempoModel):
    __tablename__ = 'Ativos'
    IDAtivo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ReferenciaExterna = db.Column(db.Integer)
    Chassi = db.Column(db.String(50))
    Renavam = db.Column(db.String(30))
    IDProjeto = db.Column(db.Integer)
    IDEmpresa = db.Column(db.Integer)
    NomeAtivo = db.Column(db.String(30))
    PlacaAtual = db.Column(db.String)
    IDEmpresaProprietaria = db.Column(db.Integer)
    IDFabricante = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    EnderecoAtivo = db.Column(db.String)
    CEP =  db.Column(db.String)
    Cidade =  db.Column(db.String)
    UF = db.Column(db.String)
    IDPedidoAtivo = db.Column(db.Integer)
    AuvoID = db.Column(db.Integer)
    BitMedicao = db.Column(db.Boolean)
    IDAtivoAuvo = db.Column(db.Integer)
    CodigoContratoOmie = db.Column(db.Integer)
    IDContrato = db.Column(db.Integer)
    IDOperacao = db.Column(db.Integer)



class Projeto(ShempoModel):
    __tablename__ = 'Projetos'
    IDProjeto = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeProjeto = db.Column(db.String(200), nullable=False)
    CidadeProjeto = db.Column(db.String(100))
    UF = db.Column(db.String(3))
    ReferenciaExternaProjeto = db.Column(db.Integer)
    IDTabelaPReco = db.Column(db.Integer)
    IDEmpresa = db.Column(db.Integer)
    IDEmpresaVendedora = db.Column(db.Integer)
    IDContrato = db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean)
    DataInicioProjeto = db.Column(db.DateTime)
    DataConclusaoProjeto = db.Column(db.DateTime)

    def __repr__(self):
        return f"<Projeto ID={self.IDProjeto} Nome={self.NomeProjeto}>"



class Funcionario(ShempoModel):
    __tablename__ = 'Funcionarios'
    IDFuncionario = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeFuncionario = db.Column(db.String(50), nullable=False)
    Telefone = db.Column(db.String(20))
    BitAtivo = db.Column(db.Boolean, nullable=False)



class Estoques(ShempoModel):
    __tablename__ = 'Estoques'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer)
    ReferenciaExterna = db.Column(db.Integer)
    IDProprietario = db.Column(db.Integer)
    NomeEstoque = db.Column(db.String(50))
    Quantidade = db.Column(db.Integer)
    Municipio = db.Column(db.String(50))
    UF = db.Column(db.String(3))
    TipoLogradouro = db.Column(db.String(50))
    Logradouro = db.Column(db.String(200))
    Numero = db.Column(db.String(15))
    Cep = db.Column(db.String(30))
    Bairro = db.Column(db.String(50))
    Referencia = db.Column(db.String(100))
    Faces = db.Column(db.Integer)




class EstoqueEuro(ShempoModel):
    __tablename__ = 'EstoqueEuro'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    EuroID = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    CodPonto = db.Column(db.Integer)
    Saldo = db.Column(db.Integer)
    EstoqueMinimo = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)




class EstoqueShempo(ShempoModel):
    __tablename__ = 'EstoqueShempo'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ShempoId = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    CodPonto = db.Column(db.Integer)
    Saldo = db.Column(db.Integer)
    EstoqueMinimo = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)


class EstoqueMatriz(ShempoModel):
    __tablename__ = 'EstoqueMatriz'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer)
    CodPonto = db.Column(db.Integer)
    Saldo = db.Column(db.Integer)
    EstoqueMinimo = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)



class Departamento(ShempoModel):
    __tablename__ = 'Departamentos'
    IDDepartamento = db.Column(db.Integer, primary_key=True)
    NomeDepartamento = db.Column(db.String(100), nullable=False)
    BitAtivo = db.Column(db.Boolean, nullable=False, default=True)

    def __repr__(self):
        return f"<Departamento {self.NomeDepartamento}>"
    

class GruposCompativeis(ShempoModel):
    __tablename__ = 'GruposCompativeis'
    IDGrupo = db.Column(db.Integer, primary_key=True)
    IDItem = db.Column(db.Integer, nullable=False)
    PmvID = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return f"<GruposCompativeis IDGrupo={self.IDGrupo}, IDItem={self.IDItem}, PmvID={self.PmvID}>"



class EstoqueManutencaoInterna(ShempoModel):
    __tablename__ = 'EstoqueManutencaoInterna'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CodPonto = db.Column(db.Integer)
    Saldo = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    EstoqueMinimo = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)


class EstoqueContainer(ShempoModel):
    __tablename__ = 'EstoqueContainer'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CodPonto = db.Column(db.Integer)
    Saldo = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    EstoqueMinimo = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)


class EstoqueManutencaoExterna(ShempoModel):
    __tablename__ = 'EstoqueManutencaoExterna'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CodPonto = db.Column(db.Integer)
    Saldo = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    EstoqueMinimo = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)



class DiagramaProduto(ShempoModel):
    __tablename__ = 'DiagramaProduto'
    IDDiagrama = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItemA = db.Column(db.Integer, nullable=False)
    IDItemB = db.Column(db.Integer, nullable=False)
    Posicao = db.Column(db.String(2))
    IDItemMaster = db.Column(db.Integer, nullable=False)
    PmvID = db.Column(db.Integer)
    IDCaboLigacao = db.Column(db.Integer)
    IDCaboLigacao2 = db.Column(db.Integer)

    def __repr__(self):
        return f"<DiagramaProduto {self.IDDiagrama}: {self.IDItemA} - {self.IDItemB}, Posicao={self.Posicao}, Master={self.IDItemMaster}>"  
    



class Pedidos(ShempoModel):
    __tablename__ = 'Pedidos'
    IDPedido = db.Column(db.Integer, primary_key=True, autoincrement=True)
    OPInicial = db.Column(db.Integer, nullable=True)
    IDProjeto = db.Column(db.Integer, nullable=True)
    IDEmpresaCompradora = db.Column(db.Integer, nullable=True)
    IDEmpresaVendedora = db.Column(db.Integer, nullable=True)
    Valor = db.Column(db.Float, nullable=True)
    DataPedido = db.Column(db.DateTime, default=datetime.utcnow)
    ReferenciaPedido = db.Column(db.Integer)
    IDFuncionario = db.Column(db.Integer)
    OSAuvo = db.Column(db.BigInteger)
    TipoPedido = db.Column(db.String)
    Estatus = db.Column(db.String)
    DataPrometido = db.Column(db.DateTime)
    IDCard = db.Column(db.Integer)
    IDKanban = db.Column(db.Integer)
    BitCard = db.Column(db.Boolean, nullable=False, default=True)
    NomeCard = db.Column(db.String)
    IDTarefaAtribuida = db.Column(db.Integer)
    IDCardVinculado = db.Column(db.Integer)
    IDKanbanVinculado  = db.Column(db.Integer)
    IDStatusPedido = db.Column(db.Integer)
    IDNotaDebito = db.Column(db.Integer)
    IDUsuario = db.Column(db.Integer)
    IDTabelaPreco = db.Column(db.Integer)
    IDPedidoOrigem = db.Column(db.Integer)
    Total = db.Column(db.Float)
    IDPedidoPai = db.Column(db.Integer)
    DataAgendado = db.Column(db.DateTime)


    def __repr__(self):
        return f'<Pedido {self.IDPedido} - Valor: {self.Valor}>'




class PedidoItens(ShempoModel):
    __tablename__ = 'PedidoItens'
    IDPedidoIten = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDPedido = db.Column(db.Integer, db.ForeignKey('Pedidos.IDPedido'), nullable=False)
    IDItem = db.Column(db.Integer, nullable=True)
    Quantidade = db.Column(db.Integer, nullable=True)
    ValorUnitario = db.Column(db.Float, nullable=True)
    NumeroLote = db.Column(db.String)
    NumerodeSerie = db.Column(db.String)
    IDTipoEstoque = db.Column(db.Integer)
    CodPonto = db.Column(db.Integer)
    IDEstoque = db.Column(db.Integer)
    BitFabrica = db.Column(db.Boolean, nullable=False, default=True)
    IDOperacao = db.Column(db.Integer)
    BitConjunto = db.Column(db.Boolean)
    ValorTotal = db.Column(db.Float, nullable=True)
    


    def __repr__(self):
        return f'<PedidoItem {self.IDPedidoIten} - Pedido: {self.IDPedido}>'
    


class EstoqueLotes(ShempoModel):
    __tablename__ = 'EstoqueLotes'
    IDLote = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer)
    IDEstoque = db.Column(db.Integer)
    NumeroLote = db.Column(db.String(100))
    NumerodeSerie = db.Column(db.String(150))
    Quantidade = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)
    CodPonto = db.Column(db.Integer)

    DataEntrada = db.Column(db.DateTime, server_default=text('GETDATE()'))
    
    def __repr__(self):
        return (f"<EstoqueLotes(IDLote={self.IDLote}, IDItem={self.IDItem}, IDEstoque={self.IDEstoque}, "
                f"NumeroLote='{self.NumeroLote}', NumerodeSerie='{self.NumerodeSerie}', "
                f"Quantidade={self.Quantidade}, DataEntrada={self.DataEntrada})>")
    



class PedidoLotes(ShempoModel):
    __tablename__ = 'PedidoLotes'

    IDPedidoLote = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDPedido = db.Column(db.Integer, nullable=False)
    IDPedidoIten = db.Column(db.Integer, nullable=False)
    IDItem = db.Column(db.Integer, nullable=False)
    IDEstoque = db.Column(db.Integer, nullable=False)
    NumeroLote = db.Column(db.String(100), nullable=False)
    Quantidade = db.Column(db.Integer, nullable=False)
    DataRegistro = db.Column(db.DateTime, server_default=func.getdate())
    IDTipoEstoque = db.Column(db.Integer)

    def __repr__(self):
        return (f"<PedidoLotes(IDPedidoLote={self.IDPedidoLote}, IDPedido={self.IDPedido}, "
                f"IDPedidoIten={self.IDPedidoIten}, IDItem={self.IDItem}, "
                f"NumeroLote='{self.NumeroLote}', Quantidade={self.Quantidade})>")
    



class EstoqueSerie(ShempoModel):
    __tablename__ = 'EstoqueSerie'

    IDSerie = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer, nullable=False)
    NumeroSerie = db.Column(db.String(100), nullable=False)
    NumeroLote = db.Column(db.String(100))
    IDEstoque = db.Column(db.Integer, nullable=False)
    TipoEstoque = db.Column(db.String(50), nullable=False)
    CodPonto = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)
    DataEntrada = db.Column(db.DateTime, default=datetime.now)
    DataSaida = db.Column(db.DateTime)



class PedidoItemSerie(ShempoModel):
    __tablename__ = 'PedidoItemSerie'

    IDPedidoSerie = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDPedido = db.Column(db.Integer, nullable=False)
    IDPedidoIten = db.Column(db.Integer, nullable=False)
    IDItem = db.Column(db.Integer, nullable=False)
    IDSerie = db.Column(db.Integer, nullable=False)
    NumeroSerie = db.Column(db.String(100), nullable=False)
    NumeroLote = db.Column(db.String(100))
    IDEstoque = db.Column(db.Integer, nullable=False)
    TipoEstoque = db.Column(db.String(60), nullable=False)
    DataVinculo = db.Column(db.DateTime, default=datetime.now)
    IDTipoEstoque = db.Column(db.Integer)
    CodPonto  = db.Column(db.Integer)



class EntradasProdutos(ShempoModel):
    __tablename__ = 'EntradasProdutos'
    IDEntrada = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer)
    Quantidade = db.Column(db.Integer)
    DataEntrada = db.Column(db.DateTime, default=datetime.now)
    NumeroNF = db.Column(db.Integer)
    IDEstoque = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)
    BitEtiqueta = db.Column(db.Boolean)
    IDSerie  = db.Column(db.Integer)
    BitConcluido = db.Column(db.Boolean)

    def __repr__(self):
        return f'<EntradaProduto ID={self.IDEntrada}, Item={self.IDItem}, Quantidade={self.Quantidade}>'
    



class TipoEstoque(ShempoModel):
    __tablename__ = 'TipoEstoque'

    IDTipoEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeEstoque = db.Column(db.String(100), nullable=False, unique=True)

    def __repr__(self):
        return f'<TipoEstoque {self.IDTipoEstoque} - {self.NomeEstoque}>'




class EstoqueEuroMatriz(ShempoModel):
    __tablename__ = 'EstoqueEuroMatriz'
    IDEstoque = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer, nullable=False)
    EuroID = db.Column(db.Integer, nullable=False)
    NumeroLote = db.Column(db.String(100), nullable=True)
    Firmware = db.Column(db.String(100), nullable=True)
    CodPonto = db.Column(db.Integer, nullable=True)
    Quantidade = db.Column(db.Integer, nullable=False)
    EstoqueMinimo = db.Column(db.Integer)





class VersaoFirmewire(ShempoModel):
    __tablename__ = 'VersaoFirmewire'

    FirmewireID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer, nullable=False)
    CodPonto = db.Column(db.Integer, nullable=True)
    PmvID = db.Column(db.Integer, nullable=True)
    Firmware= db.Column(db.String(30), nullable=True)
    IDEstoque = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f"<VersaoFirmewire id={self.FirmewireID} item={self.IDItem} firmware={self.Firmware}>"






class ImagensProdutos(ShempoModel):
    __tablename__ = 'ImagensProdutos'

    IDImagem = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeArquivo = db.Column(db.String, nullable=True)   
    CaminhoArquivo = db.Column(db.String, nullable=True)    
    IDItem = db.Column(db.Integer, nullable=True)
    Ordem = db.Column(db.Integer)    
    def __repr__(self):
        return f"<ImagemProduto {self.IDImagem} - {self.NomeArquivo}>"





class StatusPedido(ShempoModel):
    __tablename__ = 'StatusPedido'
    IDStatusPedido = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeStatusPedido = db.Column(db.String)
    


class ObservacoesPedidos(ShempoModel):
    __tablename__ = 'ObservacoesPedidos'
    IDObservacao = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDPedido = db.Column(db.Integer)
    Observacao = db.Column(db.String)
    IDUsuario = db.Column(db.Integer)
    DataObservacao = db.Column(db.DateTime, default=datetime.now)



class AnexosPedidos(ShempoModel):
    __tablename__ = 'AnexosPedidos'
    IDAnexo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeArquivo = db.Column(db.String)
    CaminhoArquivo = db.Column(db.String)
    TipoArquivo = db.Column(db.String)
    DataUpload = db.Column(db.DateTime, default=datetime.now)
    IDUsuario = db.Column(db.Integer)
    Ordem = db.Column(db.Integer)
    IDPedido = db.Column(db.Integer)



class TabelaPrecos(ShempoModel):
    __tablename__ = 'TabelaPRecos' 
    IDTabelaPreco = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeTabelaPReco = db.Column(db.String(30), nullable=False)
    ReferenciaTabelaPreco = db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean, default=True)




class PrecosProdutos(ShempoModel):
    __tablename__ = 'PrecosProdutos'
    IDPreco = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ReferenciaTabelaPReco = db.Column(db.Integer)
    IDItem = db.Column(db.Integer, nullable=False)
    TipoItem = db.Column(db.String(10), nullable=True)
    Unidade = db.Column(db.String(10), nullable=True)
    IDTabelaPreco = db.Column(db.Integer)
    EstadoItem = db.Column(db.String(20), nullable=True)
    InicioVigencia = db.Column(db.DateTime, default=datetime.utcnow)
    FimVigencia = db.Column(db.DateTime, nullable=True)
    Custo = db.Column(db.Float, nullable=True)
    ValorItem = db.Column(db.Float, nullable=False)
    ReferenciaProduto = db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean)
    IDEstadoItem = db.Column(db.Integer)




class ProdutoComposicao(ShempoModel):
    __tablename__ = 'ProdutoComposicao'
    IDProdutoPai = db.Column(db.Integer, primary_key=True)
    IDItem  = db.Column(db.Integer, primary_key=True)
    Quantidade = db.Column(db.Integer)



class NotaDebito(ShempoModel):
    __tablename__ = 'NotaDebito'
    IDNotaDebito = db.Column(db.Integer, primary_key=True, autoincrement=True)
    OPInicial = db.Column(db.Integer)
    IDProjeto = db.Column(db.Integer)
    IDEmpresaCompradora = db.Column(db.Integer)
    IDEmpresaVendedora = db.Column(db.Integer)
    Valor = db.Column(db.Float)
    DataPedido = db.Column(db.DateTime)
    DataNotaDebito = db.Column(db.DateTime, default=func.getdate())
    ReferenciaPedido = db.Column(db.Integer)
    IDFuncionario = db.Column(db.Integer)
    OSAuvo = db.Column(db.Integer)
    TipoPedido = db.Column(db.String(30))
    IDStatusPedido = db.Column(db.Integer)
    Estatus = db.Column(db.String(30))
    DataPrometido = db.Column(db.DateTime)
    IDUsuario = db.Column(db.Integer)
    IDCard = db.Column(db.Integer)
    IDKanban = db.Column(db.Integer)
    BitCard = db.Column(db.Boolean)
    IDTarefaAtribuida = db.Column(db.Integer)
    IDTabelaPreco = db.Column(db.Integer)



class NotaDebitoItem(ShempoModel):
    __tablename__ = 'NotaDebitoItens'
    NotaDebitoItens = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDNotaDebito = db.Column(db.Integer)
    IDPedido = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    Quantidade = db.Column(db.Integer)
    ValorUnitario = db.Column(db.Float)
    NumeroLote = db.Column(db.String(100))
    NumerodeSerie = db.Column(db.String(100))
    IDTipoEstoque = db.Column(db.Integer)
    CodPonto = db.Column(db.Integer)
    IDEstoque = db.Column(db.Integer)
    BitFabrica = db.Column(db.Boolean)
    IDOperacao = db.Column(db.Integer)
    BitConjunto = db.Column(db.Boolean)
    IDNotaDebito = db.Column(db.Integer)



class EstadoItem(ShempoModel):
    __tablename__ = 'EstadoItem'
    IDEstadoItem = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeEstadoItem = db.Column(db.String)



class ComposicaoAtivo(ShempoModel):
    __tablename__ = 'ComposicaoAtivo'
    IDComposicaoAtivo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDAtivo = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    Quantidade = db.Column(db.Integer)
    NumeroLote = db.Column(db.String)
    NumeroSerie  = db.Column(db.String)
    CodPonto = db.Column(db.Integer)
    IDTipoEstoque = db.Column(db.Integer)
    IDProdutoPai = db.Column(db.Integer)



class MovimentacaoAtivo(ShempoModel):
    __tablename__ = 'MovimentacaoAtivo'
    IDMovimentacaoAtivo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDProjetoOrigem = db.Column(db.Integer)
    IDProjetoDestino = db.Column(db.Integer)
    IDAtivo =  db.Column(db.Integer)
    DataMovimento =  db.Column(db.Integer)
    IDUsuario =  db.Column(db.Integer)
    IDOperacaoDestino =  db.Column(db.Integer)
    IDOperacaoOrigem  =  db.Column(db.Integer)



class TipoOperacao(ShempoModel):
    __tablename__ = 'TipoOperacao'
    IDOperacao = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeOperacao  = db.Column(db.String)



class MovimentacaoPecas(ShempoModel):
    __tablename__ = 'MovimentacaoPecas'
    IDMovimentacao  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    DataMovimentacao = db.Column(db.DateTime, default=func.getdate())
    IDItem = db.Column(db.Integer)
    NomeProduto = db.Column(db.String)
    NumeroLote = db.Column(db.String)
    NumeroSerie = db.Column(db.String)
    IDOrigem = db.Column(db.Integer)
    TipoOrigem = db.Column(db.String)
    IDDestino = db.Column(db.Integer)
    TipoDestino = db.Column(db.String)
    Quantidade = db.Column(db.Integer)
    IDOperacao = db.Column(db.Integer)
    NomeOperacao = db.Column(db.String)
    Usuario = db.Column(db.String)
    IDTipoEstoque = db.Column(db.Integer)
    IDUsuario = db.Column(db.Integer)




class PedidoAtivo(ShempoModel):
    __tablename__ = 'PedidoAtivo'
    IDPedidoAtivo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDAtivo = db.Column(db.Integer)
    IDPedido = db.Column(db.Integer)




class ClienteAuvo(ShempoModel):
    __tablename__ = 'ClienteAuvo'
    IDClienteAuvo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDGrupoAuvo = db.Column(db.Integer)
    CNPJ = db.Column(db.String)
    NomeGrupo = db.Column(db.String)
    IDProjeto = db.Column(db.Integer)
    EmpresaID = db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean)
   


class AtivoAuvo(ShempoModel):
    __tablename__ = 'AtivoAuvo'
    IDAtivoAuvo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    AuvoID = db.Column(db.Integer)
    IDAtivo = db.Column(db.Integer)
    Endereco = db.Column(db.String)
    Latitude =  db.Column(db.String)
    Longitude = db.Column(db.String)
    Cidade =  db.Column(db.String)
    UF = db.Column(db.String)
    GroupID = db.Column(db.Integer)
    EmpresaAuvo = db.Column(db.Integer)
    CEP = db.Column(db.String)
    BitAtivo = db.Column(db.Boolean)
    EmpresaID = db.Column(db.Integer)
    DataAlterado = db.Column(db.DateTime)



class ClienteEmpresa(ShempoModel):
    __tablename__ = 'ClienteEmpresa'
    IDClienteEmpresa = db.Column(db.Integer, primary_key=True, autoincrement=True)
    EmpresaID = db.Column(db.Integer)
    IDProjeto = db.Column(db.Integer)



class Medicao(ShempoModel):
    __tablename__ = 'Medicoes'
    IDMedicao = db.Column(db.Integer, primary_key=True)
    IDContrato = db.Column(db.Integer)
    Aditivo = db.Column(db.Integer)
    IDProjeto = db.Column(db.Integer)
    NomeProjeto = db.Column(db.String(200))
    EmpresaID  = db.Column(db.Integer)
    NomeEmpresa  = db.Column(db.String(200))
    IDEmpresaVendedora  = db.Column(db.Integer)
    NomeEmpresaVendedora = db.Column(db.String(200))
    PeriodoProporcional = db.Column(db.Float)
    Subtotal = db.Column(db.Float)
    ValorTotal  = db.Column(db.Float)
    DataEmissao = db.Column(db.DateTime)
    AuvoID = db.Column(db.Integer)
    GroupID  = db.Column(db.Integer)
    NumeroMedicao  = db.Column(db.Integer)
    IDTemplate  = db.Column(db.Integer)
    IDstatusMedicao = db.Column(db.Integer)
    ReferenciaCobranca  = db.Column(db.Integer)
    IDUsuario = db.Column(db.Integer)
    IDordemOmie = db.Column(db.Integer)
    ReferenciaContratoOmie  = db.Column(db.Integer)
    BitEmitirNF = db.Column(db.Boolean)
    OsOmie = db.Column(db.Integer)
    IDOperacao  = db.Column(db.Integer)
    BitGerarOrdem = db.Column(db.Boolean)
    DataAlterado = db.Column(db.DateTime)
  

    def __repr__(self):
        return f'<Medicao {self.IDMedicao}>'



class StatusMedicao(ShempoModel):
    __tablename__ = 'StatusMedicao'
    IDstatusMedicao = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeStatus = db.Column(db.String(100))



class Contrato(ShempoModel):
    __tablename__ = 'Contratos'
    IDContrato = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ReferenciaExterna = db.Column(db.Integer)
    Aditivo = db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean)
    ClienteID = db.Column(db.Integer)
    IDTipoContrato = db.Column(db.Integer)
    EmpresaID = db.Column(db.Integer)
    DataVigenciaContrato = db.Column(db.DateTime)
    DataFinalVigenciaContrato = db.Column(db.DateTime)
    ContratoAssinado = db.Column(db.Boolean)
    ValorContrato = db.Column(db.Float)
    DataContrato = db.Column(db.DateTime)
    CidadeLocalObra = db.Column(db.String(100))
    UF = db.Column(db.String(3))
    IDVendedor = db.Column(db.Integer)
    PedidoCliente = db.Column(db.String)
    ReferenciaContrato = db.Column(db.String(100))
    Seguro = db.Column(db.Boolean)
    Medicao = db.Column(db.Boolean)
    ResponsavelMedicao = db.Column(db.String(80))
    DiaPadraoMedicao = db.Column(db.Integer)
    MetodoCobranca = db.Column(db.String(70))
    DiaPadraoFaturamento = db.Column(db.Integer)
    Observacao = db.Column(db.String(900))
    FinalPeriodoMedicao = db.Column(db.DateTime)
    Contrato = db.Column(db.Text)
    FinalIndefinido = db.Column(db.Boolean)
    DiasAteConclusao = db.Column(db.Integer)
    IDProjeto  = db.Column(db.Integer)
    Parcelas = db.Column(db.Integer)
    ParcelaAtual = db.Column(db.Integer)
    MesAnoParcela = db.Column(db.String)
    ParcelasRestantes = db.Column(db.Integer)
    InicioMedicao = db.Column(db.Integer)
    FinalMedicao = db.Column(db.Integer)
    DataAlterado = db.Column(db.DateTime)


class Vendedor(ShempoModel):
    __tablename__ = 'Vendedores'
    IDVendedor = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeVendedor = db.Column(db.String(500))
    BitAtivo = db.Column(db.Boolean)


class TipoContrato(ShempoModel):
    __tablename__ = 'TipoContrato'
    IDTipoContrato = db.Column(db.Integer, primary_key=True, autoincrement=True)
    TipoContrato = db.Column(db.String)



class AprovarMedicao(ShempoModel):
    __tablename__ = 'AprovarMedicoes'
    IDMedicaoAprovacao = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDContrato = db.Column(db.Integer)
    Aditivo = db.Column(db.Integer)
    IDProjeto = db.Column(db.Integer)
    NomeProjeto = db.Column(db.String(200))
    EmpresaID = db.Column(db.Integer)
    NomeEmpresa = db.Column(db.String(200))
    IDEmpresaVendedora = db.Column(db.Integer)
    NomeEmpresaVendedora = db.Column(db.String(200))
    DataEmissao = db.Column(db.DateTime)
    AuvoID = db.Column(db.Integer)
    GroupID = db.Column(db.Integer)
    IDTemplate = db.Column(db.Integer)
    IDstatusMedicao = db.Column(db.Integer)
    ReferenciaCobranca = db.Column(db.Integer)
    IDUsuario = db.Column(db.Integer)
    Observacoes = db.Column(db.Text)
    ReferenciaContratoOmie = db.Column(db.Integer)
    BitEmitirNF = db.Column(db.Boolean)
    OsOmie = db.Column(db.Integer)
    NumeroMedicao = db.Column(db.Integer)
    Subtotal = db.Column(db.Float)
    ValorTotal  = db.Column(db.Float)
    IDOperacao = db.Column(db.Integer)




class MedicoesAprovada(ShempoModel):
    __tablename__ = 'MedicoesAprovadas'
    IDMedicaoAprovada = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NumeroMedicao = db.Column(db.Integer)
    Valor = db.Column(db.Float)
    QuantidadeEquipamentos = db.Column(db.Integer)
    DataAprovacao = db.Column(db.DateTime)
    IDUsuario = db.Column(db.Integer)
    Situacao = db.Column(db.String(100))





class EtiquetaProduto(ShempoModel):
    __tablename__ = 'EtiquetaProduto'
    IDEtiqueta = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer)
    NomeArquivo = db.Column(db.String)
    CaminhoArquivo = db.Column(db.String)
    DataCriado = db.Column(db.DateTime)
    IDEntrada  = db.Column(db.Integer)



class MedicoesItens(ShempoModel):
    __tablename__ = 'MedicoesItens'

    IDMedicaoItens  = db.Column(db.Integer, primary_key=True)
    IDItem = db.Column(db.Integer)
    IDAtivo = db.Column(db.Integer)
    PlacaEquipamento = db.Column(db.String(30))
    DataInicipPeriodoMedicao = db.Column(db.DateTime)
    DataFinalPeriodoMedicao = db.Column(db.DateTime)
    DataMobilizacao = db.Column(db.DateTime)
    PeriodoProporcional  = db.Column(db.Float)
    ValorUnitario = db.Column(db.Float)
    AcrescimosDescontos = db.Column(db.Float)
    DataEmissao = db.Column(db.DateTime)
    NumeroDiasReferencia = db.Column(db.Integer)
    NumeroDiasMedicao = db.Column(db.Integer)
    AuvoID = db.Column(db.Integer)
    GroupID = db.Column(db.Integer)
    NomeAtivo  = db.Column(db.String(200))
    SubNumeroMedicao  = db.Column(db.Integer)
    NumeroMedicao  = db.Column(db.Integer)
    IDMedicao = db.Column(db.Integer)
    IDEquipamentoAuvo  = db.Column(db.Integer)
    ValorCalculado = db.Column(db.Float)

    def __repr__(self):
        return f'<MedicaoItem {self.IDMedicaoItens}>'

    



class AprovarMedicoesItens(ShempoModel):
    __tablename__ = 'AprovarMedicoesItens'
    IDAprovarMedicaoItens = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDItem = db.Column(db.Integer)
    IDAtivo = db.Column(db.Integer)
    PlacaEquipamento = db.Column(db.String(30))
    DataInicipPeriodoMedicao = db.Column(db.DateTime)
    DataFinalPeriodoMedicao = db.Column(db.DateTime)
    DataMobilizacao = db.Column(db.DateTime)
    PeriodoProporcional = db.Column(db.Integer)
    ValorUnitario = db.Column(db.Float)
    AcrescimosDescontos = db.Column(db.Float)
    DataEmissao = db.Column(db.DateTime)
    NumeroDiasReferencia = db.Column(db.Integer)
    NumeroDiasMedicao = db.Column(db.Integer)
    AuvoID = db.Column(db.Integer)
    GroupID = db.Column(db.Integer)
    NomeAtivo = db.Column(db.String(200))
    Observacoes = db.Column(db.Text)
    SubNumeroMedicao  = db.Column(db.Integer)
    IDMedicaoAprovacao = db.Column(db.Integer)
    NumeroMedicao = db.Column(db.Integer)
    IDEquipamentoAuvo = db.Column(db.Integer)
    ValorCalculado = db.Column(db.Float)
    def __repr__(self):
        return f'<AprovarMedicoesItens {self.IDAprovarMedicaoItens}>'





class OmieOS(ShempoModel):
    __tablename__ = 'OmieOS'
    IDOs = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CodigoClienteOmie = db.Column(db.Integer)
    CodigoInternoOS = db.Column(db.Integer)
    Etapa = db.Column(db.Integer)
    dataPRevisao = db.Column(db.DateTime)
    CodigoInternoOsOmie = db.Column(db.Integer)
    NumeroOsOmie = db.Column(db.Integer)
    Parcela = db.Column(db.Integer)
    ValorTotal = db.Column(db.Float)
    DadosAdicionais = db.Column(db.String(500))
    NumeroPedido = db.Column(db.Integer)
    BitFaturada = db.Column(db.Boolean)
    BitCancelada = db.Column(db.Boolean)
    Origem = db.Column(db.String(30))
    DataVencimento = db.Column(db.DateTime)
    Percentual = db.Column(db.Float)
    BitValorIntegral = db.Column(db.Boolean)
    CodigoCategoriaServico = db.Column(db.String(20))
    DataCriado = db.Column(db.DateTime, default=datetime.utcnow)
    IDContrato = db.Column(db.Integer)
    EmpresaID = db.Column(db.Integer)
    IDOperacao = db.Column(db.Integer)
    IDTipoOperacao = db.Column(db.Integer)
    NumeroRecibo = db.Column(db.Integer)
    NumeroMedicao = db.Column(db.Integer)
    BitEmitirLancamento = db.Column(db.Boolean)





class ContratoOmie(ShempoModel):
    __tablename__ = 'ContratoOmie'
    IDContratoOmie = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CodigoContratoOmie= db.Column(db.String(50), nullable=False)
    ReferenciaContratoOmie = db.Column(db.String(100), nullable=False)
    IDProjeto= db.Column(db.Integer)
    IDContrato= db.Column(db.Integer)
    EmpresaID= db.Column(db.Integer)
    IDEmpresaOmie= db.Column(db.Integer)
    BitAtivo = db.Column(db.Boolean, nullable=False, default=True)
    VigenciaInicial= db.Column(db.DateTime)
    VigenciaFinal = db.Column(db.DateTime)
    DiaFaturamento = db.Column(db.Integer)
    ValorMensal = db.Column(db.Float)
    ValorTotal = db.Column(db.Float)
    EmpresaProprietariaOmie = db.Column(db.String)
    DiaPadraoMedicao = db.Column(db.Integer)
    IDEmpresaProprietaria = db.Column(db.Integer)
    IDGrupoAuvo = db.Column(db.Integer)
    IDVendedor = db.Column(db.Integer)
    IDProjetoOmie  = db.Column(db.Integer)
    IDCategoriaFinanceira = db.Column(db.Integer)




class FluxoMedicao(ShempoModel):
    __tablename__ = 'FluxoMedicao'
    IDFluxo = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CodigoContratoOmie = db.Column(db.Integer, nullable=False)
    IDContrato = db.Column(db.Integer, nullable=False)
    EmpresaID = db.Column(db.Integer, nullable=False)
    NumeroMedicao = db.Column(db.Integer, nullable=True)
    IDMedicao = db.Column(db.Integer, nullable=True)
    BitEmitir= db.Column(db.Boolean, default=False, nullable=False)
    BitAprovacaoInterna = db.Column(db.Boolean, default=False, nullable=False)
    BitAprovacaoCliente = db.Column(db.Boolean, default=False, nullable=False)
    BitAguardandoFaturamento= db.Column(db.Boolean, default=False, nullable=False)
    BitOrdemCriada = db.Column(db.Boolean, default=False, nullable=False)
    BitFaturada= db.Column(db.Boolean, default=False, nullable=False)
    BitRecebido = db.Column(db.Boolean, default=False, nullable=False)
    BitMedicaoConcluida = db.Column(db.Boolean, default=False, nullable=False)
    BitAtrasado = db.Column(db.Boolean, default=False, nullable=False)
    DataAlterado = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    BitCancelado = db.Column(db.Boolean, default=False, nullable=False)
    BitMedicaoEmitida =db.Column(db.Boolean, default=False, nullable=False)
    def __repr__(self):
        return (f"<FluxoMedicao IDFluxo={self.IDFluxo} "
                f"ContratoOmie={self.CodigoContratoOmie} "
                f"NumeroMedicao={self.NumeroMedicao}>")





class LancamentoOmie(ShempoModel):
    __tablename__ = 'LancamentoOmie'
    IDLancamentoOmie = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NumeroMedicao = db.Column(db.Integer)
    EmpresaID = db.Column(db.Integer)
    IDContrato = db.Column(db.Integer)
    IDProjeto = db.Column(db.Integer)
    CodigoCategoria = db.Column(db.String)
    CodigoClienteOmie = db.Column(db.Integer)
    TipoDocumento = db.Column(db.String)
    DataEmissao = db.Column(db.DateTime)
    DataRegistro = db.Column(db.DateTime)
    DataVencimento =db.Column(db.DateTime)
    IDContaCorrente = db.Column(db.Integer)
    CodigoOsOmie = db.Column(db.Integer)
    NumeroDocumento = db.Column(db.Integer)
    NumeroPedido =db.Column(db.Integer)
    NumeroParcela =db.Column(db.String)
    Operacao = db.Column(db.String)
    Status = db.Column(db.String)
    Valor = db.Column(db.Float)
    CodigoLancamentoOmie = db.Column(db.Integer)




class PagamentoMedicoesAtrasados(ShempoModel):
    __tablename__ = 'PagamentoMedicoesAtrasados'
    IDMedicaoAtrasada = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NumeroMedicao = db.Column(db.Integer)
    IDContrato = db.Column(db.Integer)
    DataInicioPeriodoMedicao = db.Column(db.DateTime)
    DataFinalPeriodoMedicao = db.Column(db.DateTime)
    DiaFaturamento = db.Column(db.Integer)
    DiasEmAtraso = db.Column(db.Integer)



class ContratoOmieItens(ShempoModel):
    __tablename__ = 'ContratoOmieItens'
    IDContratoItens = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDContratoOmie = db.Column(db.Integer)
    CodigoContratoOmie =db.Column(db.Integer)
    IDContrato = db.Column(db.Integer)
    IDItem = db.Column(db.Integer)
    IDAtivo = db.Column(db.Integer)
    Valor = db.Column(db.Float)
    Quantidade = db.Column(db.Integer)
    IDEquipamentoAuvo = db.Column(db.Integer)



class ClienteOmie(ShempoModel):
    __tablename__ = 'ClienteOmie'
    IDClienteOmie = db.Column(db.Integer, primary_key=True, autoincrement=True)
    EmpresaID  = db.Column(db.Integer)
    CNPJ = db.Column(db.String)
    RazaoSocial  = db.Column(db.String)
    BitAtivo = db.Column(db.Boolean, default=False, nullable=False)
    ReferenciaClienteOmie  = db.Column(db.Integer)
    IDEmpresaProprietaria = db.Column(db.Integer)



class EmpresaProprietaria(ShempoModel):
    __tablename__ = 'EmpresaProprietaria'
    IDEmpresaProprietaria = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeEmpresaProprietaria = db.Column(db.String)
    BitAtivo = db.Column(db.Boolean, default=False, nullable=False)
    



class LogMedicoes(ShempoModel):
    __tablename__ = 'LogMedicoes'
    IDLogMedicao = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NumeroMedicao = db.Column(db.Integer, nullable=False)
    IDOperacaoAnterior = db.Column(db.Integer, nullable=True)
    IDOperacaoAtual = db.Column(db.Integer, nullable=False)
    IDUsuario = db.Column(db.Integer, nullable=False)
    DataAlteracao = db.Column(db.DateTime, nullable=False, server_default=db.text('GETDATE()'))


class VendedoresOmie(ShempoModel):
    __tablename__ = 'VendedoresOmie'
    IDVendedor = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=False)
    CodigoVendedorOmie = db.Column(db.Integer, nullable=False)
    Email = db.Column(db.String)
    BitAtivo = db.Column(db.Boolean, default=False, nullable=False)
    NomeVendedor = db.Column(db.String)
    VisualizaPedido = db.Column(db.Boolean, default=False, nullable=False)


class CategoriafinanceiraOmie(ShempoModel):
    __tablename__ = 'CategoriafinanceiraOmie'
    IDCategoriaFinanceira = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=False)
    Codigo = db.Column(db.String)
    CodigoDRE = db.Column(db.String)
    ContaDespesa = db.Column(db.String)
    ContaInativa = db.Column(db.String)
    ContaReceita = db.Column(db.String)
    DescricaoDRE = db.Column(db.String)
    NaoExibirDre = db.Column(db.Boolean, default=False, nullable=False)
    NivelDRE = db.Column(db.Integer, nullable=False)
    TotalizaDRE = db.Column(db.Integer, nullable=False)
    DefinidoPeloUsuario = db.Column(db.Integer, nullable=False)
    Descricao = db.Column(db.String)
    DescricaoPadrao = db.Column(db.String)



class ProjetosOmie(ShempoModel):
    __tablename__ = 'ProjetosOmie'
    IDProjetoOmie = db.Column(db.Integer, primary_key=True, autoincrement=True)
    IDEmpresaProprietaria = db.Column(db.Integer, nullable=False)
    CodigoProjetoOmie = db.Column(db.Integer, nullable=False)
    BitAtivo = db.Column(db.Boolean, default=False, nullable=False)
    DataAlterado = db.Column(db.DateTime)
    DataIncluso = db.Column(db.DateTime)
    UserAlt = db.Column(db.String)
    UserInc = db.Column(db.String)
    NomeProjetoOmie = db.Column(db.String)
    IDProjeto = db.Column(db.Integer, nullable=False)
