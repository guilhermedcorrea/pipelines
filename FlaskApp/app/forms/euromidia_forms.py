from flask_wtf import FlaskForm
from wtforms import (
    StringField, SelectField,DateField, IntegerField, DecimalField, BooleanField,
    SubmitField,HiddenField,PasswordField,TextAreaField)
from wtforms.validators import DataRequired, Optional, Length, NumberRange, ValidationError,Regexp,Email






REGEX_UF = r"^[A-Za-z]{2}$"
REGEX_CNPJ_14 = r"^\d{14}$"

class FormCadastroCliente(FlaskForm):

    cnpj = StringField(
        "CNPJ (14 dígitos, só números)",
        validators=[
            Optional(),
            Regexp(REGEX_CNPJ_14, message="CNPJ deve ter 14 dígitos (somente números)."),
        ],
        render_kw={"inputmode": "numeric", "autocomplete": "off", "placeholder": "00000000000000"},
    )

    razao_social = StringField("Razão Social", validators=[Optional(), Length(max=250)])
    nome_fantasia = StringField("Nome Fantasia", validators=[Optional(), Length(max=250)])
    porte = StringField("Porte", validators=[Optional(), Length(max=80)])

    email = StringField("Email", validators=[Optional(), Email(message="Email inválido."), Length(max=200)])
    telefone = StringField("Telefone", validators=[Optional(), Length(max=60)])

    cnae = StringField("CNAE", validators=[Optional(), Length(max=30)])
    descricao_cnae = StringField("Descrição CNAE", validators=[Optional(), Length(max=600)])

    logradouro = StringField("Logradouro", validators=[Optional(), Length(max=200)])
    numero = StringField("Número", validators=[Optional(), Length(max=20)])
    complemento = StringField("Complemento", validators=[Optional(), Length(max=200)])

    cidade = StringField("Cidade", validators=[Optional(), Length(max=120)])
    estado = StringField("Estado (texto)", validators=[Optional(), Length(max=120)])

    uf = StringField(
        "UF",
        validators=[Optional(), Regexp(REGEX_UF, message="UF deve ter 2 letras."), Length(max=2)],
        render_kw={"maxlength": "2"},
    )

   
    capital_social = DecimalField(
        "Capital Social",
        validators=[Optional()],
        places=2,
        rounding=None,
        render_kw={"inputmode": "decimal", "placeholder": "Ex: 10000.00"},
    )

    bit_situacao_cadastral = BooleanField("Situação Cadastral (BitSituacaoCadastral)")
    descricao_matriz_filial = StringField("Descrição Matriz/Filial", validators=[Optional(), Length(max=120)])
    identificador_matriz_filial = StringField("Identificador Matriz/Filial", validators=[Optional(), Length(max=80)])

    bit_ativo = BooleanField("Ativo (BitAtivo)")

    id_empresa_proprietaria = IntegerField(
        "ID Empresa Proprietária",
        validators=[Optional()],
        render_kw={"inputmode": "numeric"},
    )

    submit = SubmitField("Salvar")






class CadastroContratoManualForm(FlaskForm):
   
    numero_contrato = StringField(
        "Número do Contrato (opcional)",
        validators=[Optional(), Length(max=50, message="Máximo 50 caracteres.")],
    )

    numero_previa = StringField(
        "Número da Prévia (opcional)",
        validators=[Optional(), Length(max=50, message="Máximo 50 caracteres.")],
    )

    data_lancamento = DateField(
        "Data de Lançamento",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Informe a data de lançamento.")],
    )

    cidade_exibicao = StringField(
        "Cidade de Exibição (opcional)",
        validators=[Optional(), Length(max=200, message="Máximo 200 caracteres.")],
    )

    tipo_documento = SelectField(
        "Tipo de Documento",
        choices=[
            ("MANUAL", "MANUAL"),
            ("CONTRATO", "CONTRATO"),
            ("PREVIA", "PRÉVIA"),
        ],
        validators=[DataRequired(message="Selecione o tipo de documento.")],
    )

    origem = SelectField(
        "Origem",
        choices=[
            ("MANUAL", "MANUAL"),
            ("SISTEMA", "SISTEMA"),
        ],
        validators=[DataRequired(message="Selecione a origem.")],
    )

   
    cliente_id = SelectField(
        "Cliente",
        coerce=int,
        choices=[],
        validators=[DataRequired(message="Selecione um cliente.")],
    )


    cod_ponto = SelectField(
        "CodPonto",
        coerce=int,
        choices=[],
        validators=[DataRequired(message="Selecione o CodPonto.")],
    )

    cod_face = SelectField(
        "CodFace",
        coerce=str,
        choices=[],
        validators=[DataRequired(message="Selecione o CodFace.")],
    )

    data_inicio_previsto = DateField(
        "Data Início Previsto",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Informe a data de início.")],
    )

    data_termino_previsto = DateField(
        "Data Término Previsto",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Informe a data de término.")],
    )

    loop_tipo = SelectField(
        "Loop / Tipo (digital)",
        coerce=str,
        choices=[],
        validators=[DataRequired(message="Selecione o loop/tipo.")],
    )

    cota = IntegerField(
        "Cota",
        validators=[DataRequired(message="Informe a cota."), NumberRange(min=1, max=9999, message="Cota inválida.")],
        default=1,
    )

    quantidade_parcelas = IntegerField(
        "Quantidade de Parcelas (opcional)",
        validators=[Optional(), NumberRange(min=1, max=9999, message="Parcelas inválidas.")],
    )

  
    faturamento_bruto_mensal = DecimalField(
        "Faturamento Bruto Mensal (opcional)",
        places=2,
        rounding=None,
        validators=[Optional(), NumberRange(min=0, message="Não pode ser negativo.")],
    )

    faturamento_liquido_mensal = DecimalField(
        "Faturamento Líquido Mensal (opcional)",
        places=2,
        rounding=None,
        validators=[Optional(), NumberRange(min=0, message="Não pode ser negativo.")],
    )

    permuta = SelectField(
        "Permuta?",
        choices=[("0", "Não"), ("1", "Sim")],
        validators=[DataRequired(message="Selecione permuta.")],
        default="0",
    )

    valor_permuta = DecimalField(
        "Valor Permuta (opcional)",
        places=2,
        rounding=None,
        validators=[Optional(), NumberRange(min=0, message="Não pode ser negativo.")],
    )

    vendedor_id = SelectField(
        "Vendedor",
        coerce=int,
        choices=[],
        validators=[DataRequired(message="Selecione um vendedor.")],
    )

 
    sdr = StringField(
        "SDR (opcional)",
        validators=[Optional(), Length(max=200, message="Máximo 200 caracteres.")],
    )

    submit = SubmitField("Salvar")


    def validate_data_termino_previsto(self, field):
        if self.data_inicio_previsto.data and field.data:
            if field.data < self.data_inicio_previsto.data:
                raise ValidationError("Data término não pode ser menor que data início.")

    def validate_valor_permuta(self, field):
    
        if (self.permuta.data or "0") == "1":
            if field.data is None:
                raise ValidationError("Se for permuta, informe o valor da permuta.")
            try:
                if float(field.data) <= 0:
                    raise ValidationError("Se for permuta, o valor deve ser maior que zero.")
            except Exception:
                raise ValidationError("Valor de permuta inválido.")








class CheckoutAcaoForm(FlaskForm):

    pass


class CheckoutRemoverCodPontoForm(FlaskForm):

    codponto = HiddenField(validators=[Optional()])


class CheckoutLimparForm(FlaskForm):

    pass


class CheckoutBuscarContratoForm(FlaskForm):

    contrato_id = StringField(validators=[Optional()])


class CheckoutBuscarClienteForm(FlaskForm):
 
    cliente_id = StringField(validators=[Optional()])






class FormUsuarioNovo(FlaskForm):
    """Form para criação de usuário (inclui senha)."""

    nome = StringField(
        "Nome",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Nome do usuário"},
    )

    email = StringField(
        "Email",
        validators=[DataRequired(message="Email é obrigatório."), Email(message="Email inválido."), Length(max=200)],
        render_kw={"placeholder": "email@dominio.com"},
    )

    id_perfil = SelectField(
        "Perfil",
        coerce=int,
        validators=[DataRequired(message="Perfil é obrigatório.")],
        choices=[],
    )

    ativo = SelectField(
        "Ativo",
        choices=[("1", "Sim"), ("0", "Não")],
        validators=[DataRequired(message="Ativo é obrigatório.")],
    )

    senha = PasswordField(
        "Senha",
        validators=[DataRequired(message="Senha é obrigatória."), Length(min=6, message="Senha deve ter no mínimo 6 caracteres.")],
        render_kw={"placeholder": "mínimo 6 caracteres"},
    )


class FormUsuarioEditar(FlaskForm):
    """Form para edição de usuário (sem senha)."""

    nome = StringField(
        "Nome",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Nome do usuário"},
    )

    email = StringField(
        "Email",
        validators=[DataRequired(message="Email é obrigatório."), Email(message="Email inválido."), Length(max=200)],
        render_kw={"placeholder": "email@dominio.com"},
    )

    id_perfil = SelectField(
        "Perfil",
        coerce=int,
        validators=[DataRequired(message="Perfil é obrigatório.")],
        choices=[],
    )

    ativo = SelectField(
        "Ativo",
        choices=[("1", "Sim"), ("0", "Não")],
        validators=[DataRequired(message="Ativo é obrigatório.")],
    )


class FormTrocarSenha(FlaskForm):
    """Form para trocar senha (somente no modo editar)."""

    senha = PasswordField(
        "Nova senha",
        validators=[DataRequired(message="Senha é obrigatória."), Length(min=6, message="Senha deve ter no mínimo 6 caracteres.")],
        render_kw={"placeholder": "mínimo 6 caracteres"},
    )


class FormPermissaoExtraUpsert(FlaskForm):
    """Form para conceder/revogar permissão extra (upsert)."""

    id_permissao = SelectField(
        "Permissão",
        coerce=int,
        validators=[DataRequired(message="Permissão é obrigatória.")],
        choices=[],
    )

    tipo = SelectField(
        "Tipo",
        choices=[("CONCEDER", "CONCEDER"), ("REVOGAR", "REVOGAR")],
        validators=[DataRequired(message="Tipo é obrigatório.")],
    )

    data_expiracao = DateField(
        "Expira em",
        format="%Y-%m-%d",
        validators=[Optional()],
    )

    observacao = StringField(
        "Observação",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "opcional"},
    )


class FormPermissaoExtraRemover(FlaskForm):
    """Form para remover permissão extra (por IDDimPermissoes)."""

    id_permissao = HiddenField(validators=[DataRequired(message="Permissão inválida.")])











class ReservaOcupacaoForm(FlaskForm):
 
    cod_ponto = HiddenField(validators=[DataRequired(message="cod_ponto obrigatório")])
    cod_face = StringField(validators=[DataRequired(message="cod_face obrigatório"), Length(max=100)])
    cota = IntegerField(validators=[DataRequired(message="cota obrigatório")])

    data_inicio = StringField(validators=[DataRequired(message="data_inicio obrigatório")])
    data_fim = StringField(validators=[DataRequired(message="data_fim obrigatório")])

    prazo_dias = IntegerField(validators=[Optional()])

    id_cliente = IntegerField(validators=[Optional()])
    id_vendedor = IntegerField(validators=[Optional()])
    id_fato_controle_contratos = StringField(validators=[Optional(), Length(max=50)])

    numero_contrato = StringField(validators=[Optional(), Length(max=150)])
    numero_previa = StringField(validators=[Optional(), Length(max=150)])

    observacao = TextAreaField(validators=[Optional(), Length(max=250)])
