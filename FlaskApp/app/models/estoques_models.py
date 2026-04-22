from datetime import datetime

from sqlalchemy import UniqueConstraint, func, select

from ..extensions import db


class FatoProduto(db.Model):
    __tablename__ = "FatoProdutos"
    __table_args__ = (
        UniqueConstraint(
            "ReferenciaProduto",
            name="UQ_Silver_FatoProdutos_ReferenciaProduto",
        ),
        {"schema": "Silver"},
    )

    id_fato_produto = db.Column("IDFatoProduto", db.BigInteger, primary_key=True)
    referencia_produto = db.Column("ReferenciaProduto", db.String(100), nullable=False)
    descricao = db.Column("Descricao", db.String(255), nullable=False)
    descricao_resumida = db.Column("DescricaoResumida", db.String(255), nullable=True)
    codigo_produto_integracao = db.Column(
        "CodigoProdutoIntegracao", db.String(100), nullable=True
    )
    codigo_interno = db.Column("CodigoInterno", db.String(100), nullable=True)
    unidade = db.Column("Unidade", db.String(20), nullable=True)
    ncm = db.Column("Ncm", db.String(20), nullable=True)
    ean = db.Column("Ean", db.String(50), nullable=True)
    marca = db.Column("Marca", db.String(100), nullable=True)
    modelo = db.Column("Modelo", db.String(100), nullable=True)
    tipo_item = db.Column("TipoItem", db.String(20), nullable=True)
    codigo_familia = db.Column("CodigoFamilia", db.BigInteger, nullable=True)
    descricao_familia = db.Column("DescricaoFamilia", db.String(255), nullable=True)
    origem_imposto = db.Column("OrigemImposto", db.String(20), nullable=True)
    bit_ativo = db.Column("BitAtivo", db.Boolean, nullable=False, default=True)
    origem_cadastro = db.Column("OrigemCadastro", db.String(30), nullable=False)
    criterio_criacao = db.Column("CriterioCriacao", db.String(100), nullable=True)
    observacao_interna = db.Column("ObservacaoInterna", db.String(1000), nullable=True)
    hash_cadastro_base = db.Column("HashCadastroBase", db.String(64), nullable=True)
    data_cadastro_utc = db.Column(
        "DataCadastroUtc",
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    data_ultima_atualizacao_utc = db.Column(
        "DataUltimaAtualizacaoUtc",
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self) -> str:
        return (
            f"<FatoProduto id={self.id_fato_produto} "
            f"referencia='{self.referencia_produto}' descricao='{self.descricao}'>"
        )


class FatoProdutoEmpresa(db.Model):
    __tablename__ = "FatoProdutoEmpresa"
    __table_args__ = ({"schema": "Silver"},)

    id_fato_produto_empresa = db.Column("IDFatoProdutoEmpresa", db.BigInteger, primary_key=True)
    id_fato_produto = db.Column("IDFatoProduto", db.BigInteger, nullable=False, index=True)
    id_empresa_proprietaria = db.Column("IDEmpresaProprietaria", db.BigInteger, nullable=False, index=True)
    bit_ativo = db.Column("BitAtivo", db.Boolean, nullable=False, default=True)
    produto_principal_na_empresa = db.Column("ProdutoPrincipalNaEmpresa", db.Boolean, nullable=True)
    data_cadastro_utc = db.Column("DataCadastroUtc", db.DateTime, nullable=False)
    data_ultima_atualizacao_utc = db.Column("DataUltimaAtualizacaoUtc", db.DateTime, nullable=False)


class FatoProdutoOmieVinculo(db.Model):
    __tablename__ = "FatoProdutoOmieVinculo"
    __table_args__ = ({"schema": "Silver"},)

    id_fato_produto_omie_vinculo = db.Column("IDFatoProdutoOmieVinculo", db.BigInteger, primary_key=True)
    id_fato_produto = db.Column("IDFatoProduto", db.BigInteger, nullable=False, index=True)
    id_empresa_proprietaria = db.Column("IDEmpresaProprietaria", db.BigInteger, nullable=False, index=True)
    codigo_produto_omie = db.Column("CodigoProdutoOmie", db.String(100), nullable=True)
    codigo_produto_integracao = db.Column("CodigoProdutoIntegracao", db.String(100), nullable=True)
    codigo_interno_omie = db.Column("CodigoInternoOmie", db.String(100), nullable=True)
    codigo_omie = db.Column("CodigoOmie", db.String(100), nullable=True)
    descricao_omie = db.Column("DescricaoOmie", db.String(255), nullable=True)
    unidade_omie = db.Column("UnidadeOmie", db.String(20), nullable=True)
    ncm_omie = db.Column("NcmOmie", db.String(20), nullable=True)
    ean_omie = db.Column("EanOmie", db.String(50), nullable=True)
    marca_omie = db.Column("MarcaOmie", db.String(100), nullable=True)
    modelo_omie = db.Column("ModeloOmie", db.String(100), nullable=True)
    tipo_item_omie = db.Column("TipoItemOmie", db.String(20), nullable=True)
    codigo_familia_omie = db.Column("CodigoFamiliaOmie", db.BigInteger, nullable=True)
    descricao_familia_omie = db.Column("DescricaoFamiliaOmie", db.String(255), nullable=True)
    origem_imposto_omie = db.Column("OrigemImpostoOmie", db.String(20), nullable=True)
    hash_conteudo_omie = db.Column("HashConteudoOmie", db.String(64), nullable=True)
    bit_ativo = db.Column("BitAtivo", db.Boolean, nullable=False, default=True)
    data_cadastro_utc = db.Column("DataCadastroUtc", db.DateTime, nullable=False)
    data_ultima_atualizacao_utc = db.Column("DataUltimaAtualizacaoUtc", db.DateTime, nullable=False)


class FatoOmieEstoque(db.Model):
    __tablename__ = "FatoOmieEstoque"
    __table_args__ = ({"schema": "Bronze"},)

    id_fato_omie_estoque = db.Column("IDFatoOmieEstoque", db.BigInteger, primary_key=True)
    id_fato_produtos_omie = db.Column("IDFatoProdutosOmie", db.BigInteger, nullable=True)
    id_empresa_proprietaria = db.Column("IDEmpresaProprietaria", db.BigInteger, nullable=False, index=True)
    nome_ambiente = db.Column("NomeAmbiente", db.String(255), nullable=True)
    codigo_produto = db.Column("CodigoProduto", db.String(100), nullable=True)
    codigo_produto_integracao = db.Column("CodigoProdutoIntegracao", db.String(100), nullable=True, index=True)
    codigo = db.Column("Codigo", db.String(100), nullable=True)
    descricao = db.Column("Descricao", db.String(255), nullable=True)
    codigo_local_estoque = db.Column("CodigoLocalEstoque", db.String(100), nullable=True)
    codigo_status = db.Column("CodigoStatus", db.String(100), nullable=True)
    descricao_status = db.Column("DescricaoStatus", db.String(255), nullable=True)
    saldo = db.Column("Saldo", db.Numeric(18, 4), nullable=True)
    cmc = db.Column("Cmc", db.Numeric(18, 4), nullable=True)
    pendente = db.Column("Pendente", db.Numeric(18, 4), nullable=True)
    estoque_minimo = db.Column("EstoqueMinimo", db.Numeric(18, 4), nullable=True)
    reservado = db.Column("Reservado", db.Numeric(18, 4), nullable=True)
    fisico = db.Column("Fisico", db.Numeric(18, 4), nullable=True)
    preco_unitario = db.Column("PrecoUnitario", db.Numeric(18, 4), nullable=True)
    data_posicao = db.Column("DataPosicao", db.DateTime, nullable=True)
    apenas_saldo = db.Column("ApenasSaldo", db.Boolean, nullable=True)
    payload_posicao_json = db.Column("PayloadPosicaoJson", db.Text, nullable=True)
    payload_origem_json = db.Column("PayloadOrigemJson", db.Text, nullable=True)
    hash_conteudo = db.Column("HashConteudo", db.String(64), nullable=True)
    data_carga_utc = db.Column("DataCargaUtc", db.DateTime, nullable=True)
    data_ultima_atualizacao_utc = db.Column("DataUltimaAtualizacaoUtc", db.DateTime, nullable=True)


class DimEmpresaProprietaria(db.Model):
    __tablename__ = "EmpresaProprietaria"
    __table_args__ = {
        "schema": "dbo",
        "extend_existing": True,
    }

    id_dim_empresa_proprietaria = db.Column("IDEmpresaProprietaria", db.Integer, primary_key=True)
    razao_social = db.Column("RazaoSocial", db.String(200), nullable=True)
    cnpj = db.Column("CNPJ", db.String(40), nullable=True)
    regime_tributario = db.Column("RegimeTributario", db.String(80), nullable=True)
    cnae = db.Column("CNAE", db.String(4), nullable=True)
    logo = db.Column("Logo", db.String(500), nullable=True)
    descricao_cnae = db.Column("DescricaoCnae", db.String(100), nullable=True)
    bit_ativo = db.Column("BitAtivo", db.Boolean, nullable=True, default=True)

    @property
    def nome_curto(self) -> str:
        return (self.razao_social or "").strip().split(" ")[0] if self.razao_social else ""


def subquery_razao_social_produto():
    return (
        select(DimEmpresaProprietaria.razao_social)
        .join(
            FatoProdutoEmpresa,
            FatoProdutoEmpresa.id_empresa_proprietaria == DimEmpresaProprietaria.id_dim_empresa_proprietaria,
        )
        .where(FatoProdutoEmpresa.id_fato_produto == FatoProduto.id_fato_produto)
        .order_by(DimEmpresaProprietaria.razao_social.asc())
        .limit(1)
        .scalar_subquery()
    )


def subquery_total_empresas_produto():
    return (
        select(func.count(func.distinct(FatoProdutoEmpresa.id_empresa_proprietaria)))
        .where(FatoProdutoEmpresa.id_fato_produto == FatoProduto.id_fato_produto)
        .scalar_subquery()
    )
