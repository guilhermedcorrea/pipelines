from __future__ import annotations
import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pendulum

try:
    from airflow.sdk import dag, task, get_current_context
except ImportError:
    from airflow.decorators import dag, task
    from airflow.operators.python import get_current_context

try:
    from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
except ImportError:
    from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from sqlalchemy import text
from sqlalchemy.engine import Engine

try:
    from dags._libs.auditoria_task import (
        adicionar_observacao,
        adicionar_validacao,
        criar_resumo_auditoria,
        definir_amostra,
        publicar_resumo_auditoria,
        registrar_erro_no_resumo,
    )
except Exception:
    class _ResumoAuditoriaFallback:
        """Resumo mínimo para o DAG continuar sendo importado mesmo sem a lib de auditoria."""
        def __init__(
            self,
            nome_amigavel: str,
            descricao_etapa: str,
            origem_dados: str | None = None,
            destino_dados: str | None = None,
        ) -> None:
            self.nome_amigavel = nome_amigavel
            self.descricao_etapa = descricao_etapa
            self.origem_dados = origem_dados
            self.destino_dados = destino_dados
            self.status = "PENDING"
            self.metricas_extras: dict[str, Any] = {}
            self.validacoes: list[dict[str, Any]] = []
            self.observacoes: list[str] = []
            self.amostra: list[dict[str, Any]] = []
            self.linhas_lidas = 0
            self.linhas_inseridas = 0
            self.erro: str | None = None

    def criar_resumo_auditoria(
        nome_amigavel: str,
        descricao_etapa: str,
        origem_dados: str | None = None,
        destino_dados: str | None = None,
    ) -> _ResumoAuditoriaFallback:
        return _ResumoAuditoriaFallback(
            nome_amigavel=nome_amigavel,
            descricao_etapa=descricao_etapa,
            origem_dados=origem_dados,
            destino_dados=destino_dados,
        )

    def adicionar_observacao(resumo: _ResumoAuditoriaFallback, observacao: str) -> None:
        resumo.observacoes.append(observacao)

    def adicionar_validacao(
        resumo: _ResumoAuditoriaFallback,
        nome: str,
        status: str,
        detalhe: str,
    ) -> None:
        resumo.validacoes.append({"nome": nome, "status": status, "detalhe": detalhe})

    def definir_amostra(
        resumo: _ResumoAuditoriaFallback,
        amostra: list[dict[str, Any]],
        limite: int = 10,
    ) -> None:
        resumo.amostra = amostra[:limite]

    def publicar_resumo_auditoria(resumo: _ResumoAuditoriaFallback) -> None:
        logger.info(
            "Auditoria | etapa=%s | status=%s | origem=%s | destino=%s | metricas=%s",
            resumo.nome_amigavel,
            resumo.status,
            resumo.origem_dados,
            resumo.destino_dados,
            resumo.metricas_extras,
        )

    def registrar_erro_no_resumo(resumo: _ResumoAuditoriaFallback, erro: Exception) -> None:
        resumo.erro = repr(erro)


logger = logging.getLogger(__name__)



DAG_ID_PADRAO = "pipeline_controle_contratos_euromidia"
DAG_ID = os.getenv("AIRFLOW_DAG_CONTROLE_CONTRATOS", DAG_ID_PADRAO).strip() or DAG_ID_PADRAO
FUSO_HORARIO = "America/Sao_Paulo"
CRON_AGENDAMENTO = "0 8,11,15,18 * * *"

CONN_ID_SQL_SERVER = "mssql_integracao"

DAG_ID_PRIORIDADE_RESERVAS_PADRAO = "pipeline_prioridade_reservas"
DAG_ID_PRIORIDADE_RESERVAS = (
    os.getenv("AIRFLOW_DAG_PRIORIDADE_RESERVAS", DAG_ID_PRIORIDADE_RESERVAS_PADRAO).strip()
    or DAG_ID_PRIORIDADE_RESERVAS_PADRAO
)


AIRFLOW_TRIGGER_CONTROLE_CONTRATOS_HABILITADO = os.getenv("AIRFLOW_TRIGGER_CONTROLE_CONTRATOS_HABILITADO", "1").strip()
AIRFLOW_API_BASE_URL_CONFIGURADA = os.getenv("AIRFLOW_API_BASE_URL", "").strip()
AIRFLOW_API_TIMEOUT_SEGUNDOS_CONFIGURADO = os.getenv("AIRFLOW_API_TIMEOUT_SEGUNDOS", "").strip()


RCLONE_REMOTE_CONTROLE_CONTRATOS = os.getenv(
    "RCLONE_REMOTE_CONTROLE_CONTRATOS",
    "sharepoint_basedados",
)
RCLONE_PASTA_CONTROLE_CONTRATOS = os.getenv(
    "RCLONE_PASTA_CONTROLE_CONTRATOS",
    "01- Controle de Contratos",
)
NOME_ARQUIVO_EXCEL_CONTROLE_CONTRATOS = os.getenv(
    "NOME_ARQUIVO_EXCEL_CONTROLE_CONTRATOS",
    "Copia-Controle de Contratos Euromidia.xlsm",
)
RCLONE_ARQUIVO_ORIGEM = os.getenv(
    "RCLONE_ARQUIVO_ORIGEM_CONTROLE_CONTRATOS",
    f"{RCLONE_REMOTE_CONTROLE_CONTRATOS}:{RCLONE_PASTA_CONTROLE_CONTRATOS}/{NOME_ARQUIVO_EXCEL_CONTROLE_CONTRATOS}",
)


PASTA_SHAREPOINT_CONTAINER = Path(
    os.getenv(
        "PASTA_BASE_DADOS_CONTRATOS_CONTAINER",
        "/opt/airflow/Dados/SharePoint/BaseDados/Base Dados - 01- Controle de Contratos",
    )
)

PASTA_CARGA_CONTAINER = Path(
    os.getenv(
        "PASTA_CARGA_CONTROLE_CONTRATOS_CONTAINER",
        "/opt/airflow/Dados/Euromidia/Comercial/CargasSQL/ControleContratosEuromidia",
    )
)

CAMINHO_ARQUIVO_EXCEL = Path(
    os.getenv(
        "CAMINHO_CONTROLE_CONTRATOS_XLSM",
        str(PASTA_SHAREPOINT_CONTAINER / NOME_ARQUIVO_EXCEL_CONTROLE_CONTRATOS),
    )
)

NOME_ABA_EXCEL = os.getenv("NOME_ABA_EXCEL_CONTROLE_CONTRATOS", "CTR").strip() or "CTR"

BANCO_STAGE_CONTROLE_CONTRATOS = os.getenv(
    "BANCO_STAGE_CONTROLE_CONTRATOS",
    "Integracao",
)

TABELA_STAGE = os.getenv(
    "TABELA_STAGE_CONTROLE_CONTRATOS",
    f"{BANCO_STAGE_CONTROLE_CONTRATOS}.dbo.df_fatocontrolecontratos",
)

mapeamento_colunas = {
    "DATA DO LANÇAMENTO": "DataLancamento",
    "COTA (Exato)": "Cota",
    "PONTO": "CodPonto",
    "CÓDIGO E FACE": "CodFace",
    "CIDADE DA EXIBIÇÃO": "CidadeExibicao",
    "TIPO": "Tipo",
    "ORIGEM ": "Origem",
    "EMPRESA EURO": "EmpresaEuro",
    "CNPJ DA EXIBIDORA (EUROMIDIA)": "CnpjExibibora",
    "TIPO DE DOCUMENTO": "TipoDocumento",
    "NÚMERO DO CONTRATO / AUTORIZAÇÃO": "NumeroContrato",
    "Nº PRÉVIA LOGYCWARE": "NumeroPrevia",
    "RAZÃO SOCIAL / NOME": "RazaoSocial",
    "CNPJ": "CNPJ",
    "CPF": "CPF",
    "MARCA EXIBIDA": "MarcaExibida",
    "VENDEDOR": "Vendedor",
    "SDR (Sales Development Representative)": "SDR",
    "AGÊNCIA": "Agencia",
    "CNPJ AGÊNCIA": "CnpjAgencia",
    "BUREAU": "Bureau",
    "CNPJ BUREAU": "CnpjBureau",
    "INTERMEDIÁRIO": "Intermediario",
    "CNPJ INTERMEDIÁRIO": "CnpjIntermediario",
    "DATA DE ASSINATURA/RENOVAÇÃO (EMISSÃO)": "DataAssinaturaRenovacao",
    "ID. TRIMESTRE": "IDTrimestre",
    "TEMPO DE EXPOSIÇÃO [DIAS]": "TexmpoExposicao",
    "DATA DE INÍCIO PREVISTO": "DataInicioPrevisto",
    "DATA DE TÉRMINO PREVISTO": "DataTerminoPrevisto",
    "INÍCIO/RENOVAÇÃO": "InicioRenovacao",
    "FATURAMENTO BRUTO MENSAL": "FaturamentoBrutoMensal",
    "% PERMUTA": "PercentualPermuta",
    "COTA DE OPORTUNIDADE?": "CotaOportunidade",
    "VALOR PERMUTA": "ValorPermuta",
    "FATURAMENTO LÍQ. (- PERMUTA)": "FaturamentoLiquidoPermuta",
    "Nº DE PARCELAS": "NumeroParcelas",
    "DATA DO 1º VENCIMENTO": "DataInicioVencimento",
    "TOTAL BRUTO DO CONTRATO": "TotalBrutoContrato",
    "TOTAL LÍQUIDO DO CONTRATO (-AG, -BR, -CT ACORDO)": "TotalLiquidoContratoAGBRCTACORDO",
    "TOTAL LÍQUIDO DO CONTRATO (- AG, - BR, -VEND, - GER ,-COOR)": "TotalLiquidoContratoAGBRVENDGERCOOR",
    "% AGÊNCIA": "PercentualAgencia",
    "VALOR DA AGÊNCIA (MENSAL)": "ValorMensalAgencia",
    "% BUREAU": "PercentualBureau",
    "VALOR BUREAU (MENSAL)": "ValorBureauMensal",
    "% CARTA ACORDO": "PercentualCartaAcordo",
    "VALOR CARTA ACORDO (MENSAL)": "ValorCartaAcordoMensal",
    "VALOR OUTRAS COMISSÕES": "ValorOutrasComissoes",
    "FATURAMENTO LÍQUIDO MENSAL": "FaturamentoLiquidoMensal",
    "% COMISSÃO VENDEDOR": "PercentualComissaoVendedor",
    "VALOR VENDEDOR": "ValorVendedor",
    "VALOR VENDEDOR TOTAL": "ValorVendedorTotal",
    "%COMISSÃO COORDENAÇÃO": "PercentualComissaoCoordenacao",
    "VALOR COORDENADOR": "ValorCoordenador",
    "VALOR COORDENADOR TOTAL": "ValorCoordenadorTotal",
    "% COMISSÃO GERÊNCIA": "PercentualComissaoGerencia",
    "VALOR GERÊNCIA": "ValorGerencia",
    "VALOR GERÊNCIA TOTAL": "ValorGerenciaTotal",
    "ATIVO / CANCELAMENTO": "AtivoCancelamento",
    "FATURAMENTO LÍQUIDO FINAL MENSAL": "FaturamentoLiquidoFinalMensal",
    "COMISSÃO GERÊNCIA NORDESTE": "ComissaoGerenciaNordeste",
    "FATURAMENTO": "Faturamento",
    "DATA DE CANCELAMENTO": "DataCancelamento",
    "OBS": "OBS",
    "OBS:": "OBS",
    "OBSERVAÇÃO": "OBS",
    "OBSERVACOES": "OBS",
    "OBSERVAÇÕES": "OBS",
}

def lista_unica_preservando_ordem(valores: list[str]) -> list[str]:
    """Remove nomes duplicados preservando a primeira ocorrência.

    Isso evita criar a stage SQL com colunas repetidas quando várias variações
    do Excel apontam para o mesmo destino, por exemplo: OBS, OBS:, OBSERVAÇÃO.
    """
    vistos: set[str] = set()
    saida: list[str] = []

    for valor in valores:
        nome = str(valor or "").strip()
        if not nome or nome in vistos:
            continue
        vistos.add(nome)
        saida.append(nome)

    return saida


ORDEM_COLUNAS_SAIDA = lista_unica_preservando_ordem(list(mapeamento_colunas.values()) + ["OBS"])

schema_overrides: dict[str, Any] = {}  


def obter_engine_sql_server() -> Engine:
    """Obtém a engine SQL Server via hook centralizado do Airflow.

    Importo o hook dentro da função para evitar que uma falha de path em plugins
    derrube o parse do DAG inteiro no Airflow. Se o hook estiver ausente, a task
    falha de forma explícita na execução, mas o DAG continua aparecendo na UI.
    """
    from hooks.BancodeDados.SqlServer import HookSqlServer

    hook_sql = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
    return hook_sql.obter_engine()


def normalizar_valor_auditoria(valor: Any) -> Any:
    """Normaliza valores para exibição no painel de auditoria."""
    if valor is None:
        return None

    if isinstance(valor, (datetime, date)):
        return str(valor)

    try:
        if pd.isna(valor):
            return None
    except Exception:
        pass

    return valor


def df_para_amostra(
    df: pd.DataFrame,
    limite: int = 5,
    colunas: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Converte DataFrame pandas para amostra amigável no painel."""
    if df is None or df.empty:
        return []

    base = df
    if colunas:
        colunas_existentes = [col for col in colunas if col in df.columns]
        if colunas_existentes:
            base = df.loc[:, colunas_existentes]

    linhas = base.head(limite).to_dict(orient="records")

    return [
        {chave: normalizar_valor_auditoria(valor) for chave, valor in linha.items()}
        for linha in linhas
    ]

def consultar_amostra_sql(
    engine: Engine,
    sql: str,
    parametros: dict[str, Any] | None = None,
    limite: int = 5,
) -> list[dict[str, Any]]:
    """Consulta amostra diretamente no SQL Server para auditoria."""
    with engine.begin() as conn:
        resultado = conn.execute(text(sql), parametros or {})
        linhas = resultado.mappings().fetchall()

    amostra = []
    for linha in linhas[:limite]:
        amostra.append(
            {
                chave: normalizar_valor_auditoria(valor)
                for chave, valor in dict(linha).items()
            }
        )
    return amostra


def _somente_existentes(nomes: list[str], existentes: set[str]) -> list[str]:
    """Mantida por compatibilidade com versões antigas do pipeline."""
    return [n for n in nomes if n in existentes]


def _norm_text(valor: Any) -> str:
    if valor is None:
        return "SEM"

    try:
        if pd.isna(valor):
            return "SEM"
    except Exception:
        pass

    texto = str(valor).strip()
    if not texto:
        return "SEM"

    texto = " ".join(texto.split())
    return texto.upper()


def _only_digits(valor: Any) -> str:
    if valor is None:
        return "SEM"

    try:
        if pd.isna(valor):
            return "SEM"
    except Exception:
        pass

    texto = str(valor)
    if texto.endswith(".0"):
        texto = texto[:-2]

    digitos = "".join(ch for ch in texto if ch.isdigit())
    return digitos if digitos else "SEM"


def _norm_date(valor: Any) -> str:
    if valor is None:
        return "SEM"

    try:
        if pd.isna(valor):
            return "SEM"
    except Exception:
        pass

    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d")

    if isinstance(valor, datetime):
        return valor.date().strftime("%Y-%m-%d")

    texto = str(valor).strip()
    if not texto:
        return "SEM"

    return texto


def _to_base36(numero: int) -> str:
    if numero == 0:
        return "0"

    alfabeto = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    saida = []

    while numero > 0:
        numero, resto = divmod(numero, 36)
        saida.append(alfabeto[resto])

    return "".join(reversed(saida))


def _hash_base36_16(assinatura: str) -> str:
    digest = hashlib.sha256(assinatura.encode("utf-8")).digest()
    inteiro = int.from_bytes(digest, byteorder="big", signed=False)
    base36 = _to_base36(inteiro)
    return base36[:16]


def normalizar_nome_coluna_excel(nome_coluna: Any) -> str:
    """Normaliza o cabeçalho da planilha para casar mesmo com espaços invisíveis."""
    if nome_coluna is None:
        return ""

    texto = str(nome_coluna)
    texto = (
        texto.replace("\u00A0", " ")
        .replace("\u200B", "")
        .replace("\u200C", "")
        .replace("\u200D", "")
        .replace("\ufeff", "")
    )
    texto = " ".join(texto.strip().upper().split())
    return texto


def limpar_texto_valor(valor: Any) -> str | None:
    """Limpa textos vindos do Excel preservando valor nulo quando estiver vazio."""
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except Exception:
        pass

    texto = str(valor)
    texto = (
        texto.replace("\u00A0", " ")
        .replace("\u200B", "")
        .replace("\u200C", "")
        .replace("\u200D", "")
        .replace("\ufeff", "")
        .strip()
    )

    if texto == "":
        return None

    return texto




def valor_pandas_vazio(valor: Any) -> bool:
    """Retorna True quando um valor do pandas deve ser tratado como vazio."""
    if valor is None:
        return True

    try:
        if pd.isna(valor):
            return True
    except Exception:
        pass

    if isinstance(valor, str) and valor.strip() == "":
        return True

    return False


def consolidar_colunas_duplicadas_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Consolida colunas duplicadas preservando a primeira informação preenchida.

    A aba CTR pode trazer variações como OBS, OBS:, OBSERVAÇÃO e OBSERVAÇÕES.
    Todas são mapeadas para a coluna técnica OBS. Sem consolidar, o DataFrame
    fica com várias colunas chamadas OBS e o CREATE TABLE da stage falha no
    SQL Server porque uma tabela não pode ter nomes de coluna repetidos.

    Regra aplicada:
    - mantém a primeira coluna com aquele nome;
    - quando a primeira está vazia, preenche com o primeiro valor não vazio das
      colunas duplicadas seguintes;
    - devolve um DataFrame com nomes de colunas únicos.
    """
    if df is None or df.empty:
        return df

    nomes_colunas = [str(coluna) for coluna in df.columns]

    if len(nomes_colunas) == len(set(nomes_colunas)):
        return df

    series_por_nome: dict[str, pd.Series] = {}
    ordem_colunas: list[str] = []

    for indice, nome_coluna in enumerate(nomes_colunas):
        serie_atual = df.iloc[:, indice]

        if nome_coluna not in series_por_nome:
            series_por_nome[nome_coluna] = serie_atual.copy()
            ordem_colunas.append(nome_coluna)
            continue

        serie_base = series_por_nome[nome_coluna]
        mascara_base_vazia = serie_base.map(valor_pandas_vazio)
        series_por_nome[nome_coluna] = serie_base.where(~mascara_base_vazia, serie_atual)

    return pd.DataFrame(
        {nome_coluna: series_por_nome[nome_coluna] for nome_coluna in ordem_colunas},
        index=df.index,
    )


def parse_data_br_pandas(valor: Any) -> str | None:
    """Converte datas brasileiras, ISO, datetime e serial Excel para YYYY-MM-DD."""
    valor_limpo = limpar_texto_valor(valor)

    if valor_limpo is None:
        return None

    if isinstance(valor, datetime):
        return valor.date().strftime("%Y-%m-%d")

    if isinstance(valor, date):
        return valor.strftime("%Y-%m-%d")

    numero_serial = None

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        numero_serial = float(valor)
    else:
        texto_numero = valor_limpo.strip()
        if "," in texto_numero:
            texto_numero = texto_numero.replace(".", "").replace(",", ".")
        try:
            numero_serial = float(texto_numero)
        except Exception:
            numero_serial = None

    if numero_serial is not None and 1 <= numero_serial <= 80000:
        try:
            data_excel = pd.Timestamp("1899-12-30") + pd.to_timedelta(int(numero_serial), unit="D")
            return data_excel.date().strftime("%Y-%m-%d")
        except Exception:
            pass

    formatos = [
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ]

    for formato in formatos:
        try:
            return datetime.strptime(valor_limpo, formato).date().strftime("%Y-%m-%d")
        except Exception:
            pass

    data_convertida = pd.to_datetime(valor_limpo, errors="coerce", dayfirst=True)
    if pd.isna(data_convertida):
        return None

    return data_convertida.date().strftime("%Y-%m-%d")


def parse_numero_br_pandas(valor: Any) -> float | None:
    """Converte número brasileiro para float."""
    valor_limpo = limpar_texto_valor(valor)

    if valor_limpo is None:
        return None

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        try:
            return float(valor)
        except Exception:
            return None

    texto = valor_limpo.replace("R$", "").replace('"', "").replace(" ", "").strip()

    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    try:
        return float(texto)
    except Exception:
        return None


def parse_inteiro_pandas(valor: Any) -> int | None:
    """Converte inteiro vindo do Excel."""
    numero = parse_numero_br_pandas(valor)
    if numero is None:
        return None

    try:
        return int(numero)
    except Exception:
        return None


def parse_id_trimestre_pandas(valor: Any) -> str | None:
    texto = limpar_texto_valor(valor)
    if texto is None:
        return None

    texto = " ".join(texto.split())
    texto = texto.replace("TRI", "Tri").replace("tri", "Tri")
    return texto


def parse_cnpj_pandas(valor: Any) -> str | None:
    """Remove pontuação de CNPJ/CPF preservando apenas dígitos."""
    valor_limpo = limpar_texto_valor(valor)

    if valor_limpo is None:
        return None

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        try:
            valor_limpo = str(int(valor))
        except Exception:
            valor_limpo = str(valor)

    if valor_limpo.endswith(".0"):
        valor_limpo = valor_limpo[:-2]

    digitos = "".join(ch for ch in valor_limpo if ch.isdigit())
    return digitos or None


def localizar_caminho_excel_controle_contratos() -> Path:
    """Localiza o arquivo original do controle de contratos dentro do container."""
    nome_arquivo_excel = NOME_ARQUIVO_EXCEL_CONTROLE_CONTRATOS

    caminho_env = os.getenv("CAMINHO_CONTROLE_CONTRATOS_XLSM")
    candidatos: list[Path] = []

    if caminho_env:
        candidatos.append(Path(caminho_env))

    candidatos.extend(
        [
            CAMINHO_ARQUIVO_EXCEL,
            PASTA_SHAREPOINT_CONTAINER / nome_arquivo_excel,
        ]
    )

    for caminho in candidatos:
        if caminho.exists():
            return caminho

    raizes_busca = [
        Path("/opt/airflow"),
        Path("/root/PythonJobs/pipelines"),
        Path("/root/SHEMPO"),
    ]

    for raiz in raizes_busca:
        if not raiz.exists():
            continue

        try:
            encontrados = sorted(
                raiz.rglob(nome_arquivo_excel),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            encontrados = []

        if encontrados:
            return encontrados[0]

    caminhos_testados = "\n".join(str(c) for c in candidatos)
    raise FileNotFoundError(
        "Arquivo Excel do controle de contratos não encontrado dentro do container.\n"
        f"Nome esperado: {nome_arquivo_excel}\n"
        f"Caminhos testados:\n{caminhos_testados}"
    )


def ler_aba_ctr_xlsm_pandas(caminho_arquivo: str | Path) -> pd.DataFrame:
    caminho = Path(caminho_arquivo)

    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")

    if caminho.suffix.lower() not in {".xlsm", ".xlsx", ".xls"}:
        raise ValueError(
            f"Extensão inesperada ({caminho.suffix}). Esperado Excel (.xlsm/.xlsx/.xls)."
        )

    try:
        return pd.read_excel(
            caminho,
            sheet_name=NOME_ABA_EXCEL,
            engine="openpyxl",
            dtype=object,
        )
    except ImportError as erro:
        raise RuntimeError(
            "A biblioteca openpyxl não está instalada no container do Airflow. "
            "Instale com: pip install openpyxl"
        ) from erro


def tratar_dataframe_ctr_pandas(df_original: pd.DataFrame) -> pd.DataFrame:
    """Aplica o tratamento da aba CTR sem usar Polars/calamine."""
    df = df_original.copy()

    mapa_normalizado = {
        normalizar_nome_coluna_excel(origem): destino
        for origem, destino in mapeamento_colunas.items()
    }

    renomear: dict[Any, str] = {}
    for coluna in df.columns:
        destino = mapa_normalizado.get(normalizar_nome_coluna_excel(coluna))
        if destino:
            renomear[coluna] = destino

    df = df.rename(columns=renomear)
    df = consolidar_colunas_duplicadas_dataframe(df)

    colunas_datas = [
        "DataLancamento",
        "DataAssinaturaRenovacao",
        "DataInicioPrevisto",
        "DataTerminoPrevisto",
        "DataInicioVencimento",
        "DataCancelamento",
    ]

    colunas_int = [
        "CodPonto",
        "TexmpoExposicao",
        "NumeroParcelas",
    ]

    colunas_float = [
        "Cota",
        "PercentualPermuta",
        "CotaOportunidade",
        "PercentualAgencia",
        "PercentualBureau",
        "PercentualCartaAcordo",
        "PercentualComissaoVendedor",
        "PercentualComissaoCoordenacao",
        "PercentualComissaoGerencia",
    ]

    colunas_money = [
        "FaturamentoBrutoMensal",
        "ValorPermuta",
        "FaturamentoLiquidoPermuta",
        "TotalBrutoContrato",
        "TotalLiquidoContratoAGBRCTACORDO",
        "TotalLiquidoContratoAGBRVENDGERCOOR",
        "ValorMensalAgencia",
        "ValorBureauMensal",
        "ValorCartaAcordoMensal",
        "ValorOutrasComissoes",
        "FaturamentoLiquidoMensal",
        "ValorVendedor",
        "ValorVendedorTotal",
        "ValorCoordenador",
        "ValorCoordenadorTotal",
        "ValorGerencia",
        "ValorGerenciaTotal",
        "FaturamentoLiquidoFinalMensal",
        "ComissaoGerenciaNordeste",
        "Faturamento",
    ]

    colunas_cnpj = [
        "CNPJ",
        "CnpjAgencia",
        "CnpjBureau",
        "CnpjIntermediario",
        "CnpjExibibora",
        "CPF",
    ]

    for coluna in colunas_datas:
        if coluna in df.columns:
            df[coluna] = df[coluna].map(parse_data_br_pandas)

    for coluna in colunas_int:
        if coluna in df.columns:
            df[coluna] = df[coluna].map(parse_inteiro_pandas)

    for coluna in colunas_float + colunas_money:
        if coluna in df.columns:
            df[coluna] = df[coluna].map(parse_numero_br_pandas)

    for coluna in colunas_cnpj:
        if coluna in df.columns:
            df[coluna] = df[coluna].map(parse_cnpj_pandas)

    if "IDTrimestre" in df.columns:
        df["IDTrimestre"] = df["IDTrimestre"].map(parse_id_trimestre_pandas)

    for coluna in df.columns:
        if coluna not in set(colunas_datas + colunas_int + colunas_float + colunas_money + colunas_cnpj + ["IDTrimestre"]):
            df[coluna] = df[coluna].map(limpar_texto_valor)

    df = aplicar_hash_contrato_e_previa(df)
    df = garantir_colunas_saida(df)

    return df


def aplicar_hash_contrato_e_previa(df: pd.DataFrame) -> pd.DataFrame:
    colunas_necessarias = [
        "DataLancamento",
        "Origem",
        "CNPJ",
        "MarcaExibida",
        "DataAssinaturaRenovacao",
        "DataTerminoPrevisto",
        "NumeroContrato",
        "NumeroPrevia",
    ]
    faltantes = [coluna for coluna in colunas_necessarias if coluna not in df.columns]
    if faltantes:
        raise ValueError(f"Faltam colunas no dataframe para aplicar hash: {faltantes}")

    df_out = df.copy()

    def montar_assinatura_contrato(linha: pd.Series) -> str:
        return (
            _norm_date(linha.get("DataLancamento"))
            + "|"
            + _norm_text(linha.get("Origem"))
            + "|"
            + _only_digits(linha.get("CNPJ"))
            + "|"
            + _norm_text(linha.get("MarcaExibida"))
            + "|"
            + _norm_date(linha.get("DataAssinaturaRenovacao"))
            + "|"
            + _norm_date(linha.get("DataTerminoPrevisto"))
        )

    def montar_assinatura_previa(linha: pd.Series) -> str:
        return (
            _norm_date(linha.get("DataLancamento"))
            + "|"
            + _only_digits(linha.get("CNPJ"))
            + "|"
            + _norm_text(linha.get("MarcaExibida"))
            + "|"
            + _norm_date(linha.get("DataAssinaturaRenovacao"))
            + "|"
            + _norm_date(linha.get("DataTerminoPrevisto"))
        )

    def esta_vazio(valor: Any) -> bool:
        texto = limpar_texto_valor(valor)
        return texto is None

    assinaturas_contrato = df_out.apply(montar_assinatura_contrato, axis=1)
    assinaturas_previa = df_out.apply(montar_assinatura_previa, axis=1)

    hashes_contrato = assinaturas_contrato.map(lambda valor: "HASHC-" + _hash_base36_16(valor))
    hashes_previa = assinaturas_previa.map(lambda valor: "HASHP-" + _hash_base36_16(valor))

    mascara_contrato_vazio = df_out["NumeroContrato"].map(esta_vazio)
    mascara_previa_vazia = df_out["NumeroPrevia"].map(esta_vazio)

    df_out.loc[mascara_contrato_vazio, "NumeroContrato"] = hashes_contrato[mascara_contrato_vazio]
    df_out.loc[mascara_previa_vazia, "NumeroPrevia"] = hashes_previa[mascara_previa_vazia]

    return df_out


def garantir_colunas_saida(df: pd.DataFrame) -> pd.DataFrame:
    df_out = consolidar_colunas_duplicadas_dataframe(df.copy())

    colunas_saida = lista_unica_preservando_ordem(ORDEM_COLUNAS_SAIDA)

    for coluna in colunas_saida:
        if coluna not in df_out.columns:
            df_out[coluna] = None

    df_out = df_out.loc[:, colunas_saida]
    return consolidar_colunas_duplicadas_dataframe(df_out)



def obter_ultimo_csv_carga(pasta: Path) -> Path:
    arquivos = sorted(
        pasta.glob("df_fatocontrolecontratos_*.csv"),
        key=lambda caminho: (caminho.stat().st_mtime, caminho.name),
        reverse=True,
    )

    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum CSV df_fatocontrolecontratos_*.csv encontrado em {pasta}"
        )

    return arquivos[0]


def normalizar_partes_tabela_stage(nome_completo: str) -> tuple[str, str, str]:
    """Normaliza o nome da stage para sempre carregar no banco correto.

    Aceita:
    - tabela
    - schema.tabela
    - banco.schema.tabela

    Como os MERGEs do próprio DAG leem [Integracao].[dbo].[df_fatocontrolecontratos],
    quando o nome vier apenas como dbo.tabela, o banco padrão será Integracao.
    """
    if not nome_completo or not str(nome_completo).strip():
        raise ValueError("Nome da tabela stage não pode ser vazio.")

    partes = [
        parte.strip().strip("[]")
        for parte in str(nome_completo).split(".")
        if parte.strip()
    ]

    if len(partes) == 1:
        banco = BANCO_STAGE_CONTROLE_CONTRATOS
        schema = "dbo"
        tabela = partes[0]
    elif len(partes) == 2:
        banco = BANCO_STAGE_CONTROLE_CONTRATOS
        schema, tabela = partes
    elif len(partes) == 3:
        banco, schema, tabela = partes
    else:
        raise ValueError(
            "Nome da tabela stage deve estar em um destes formatos: tabela, schema.tabela "
            f"ou banco.schema.tabela. Valor recebido: {nome_completo}"
        )

    return banco, schema, tabela


def nome_tabela_stage_sql(nome_completo: str) -> str:
    """Retorna o nome qualificado e protegido da stage para SQL Server."""
    banco, schema, tabela = normalizar_partes_tabela_stage(nome_completo)
    return f"[{banco}].[{schema}].[{tabela}]"


def nome_objeto_stage_sql(nome_completo: str) -> str:
    """Retorna nome 3-part sem colchetes para OBJECT_ID."""
    banco, schema, tabela = normalizar_partes_tabela_stage(nome_completo)
    return f"{banco}.{schema}.{tabela}"


def gerar_sql_create_stage(df: pd.DataFrame, tabela_stage: str) -> str:
    """Gera CREATE TABLE da stage com colunas textuais.

    A stage é uma tabela técnica: todos os campos entram como NVARCHAR(MAX), porque as conversões
    tipadas acontecem nos MERGEs posteriores com TRY_CONVERT. Isso evita erro de tipo na carga bruta.
    """
    if df is None or df.empty:
        colunas = lista_unica_preservando_ordem(ORDEM_COLUNAS_SAIDA)
    else:
        colunas = lista_unica_preservando_ordem([str(coluna) for coluna in df.columns])

    if not colunas:
        raise ValueError("Não existem colunas para criar a stage.")

    nome_sql = nome_tabela_stage_sql(tabela_stage)
    colunas_sql = ",\n        ".join(f"[{coluna}] NVARCHAR(MAX) NULL" for coluna in colunas)

    return f"""
IF OBJECT_ID(N'{nome_objeto_stage_sql(tabela_stage)}', N'U') IS NULL
BEGIN
    CREATE TABLE {nome_sql}
    (
        {colunas_sql}
    );
END;
"""


def garantir_colunas_stage_sql_server(conn: Any, df: pd.DataFrame, tabela_stage: str) -> None:
    """Garante que a tabela stage existe e que todas as colunas do DataFrame existem nela."""
    nome_sql = nome_tabela_stage_sql(tabela_stage)
    banco, schema, tabela = normalizar_partes_tabela_stage(tabela_stage)

    conn.exec_driver_sql(gerar_sql_create_stage(df, tabela_stage))

    sql_colunas_existentes = """
SELECT c.name AS nome_coluna
FROM [{banco}].sys.columns AS c
INNER JOIN [{banco}].sys.objects AS o
    ON o.object_id = c.object_id
INNER JOIN [{banco}].sys.schemas AS s
    ON s.schema_id = o.schema_id
WHERE
    s.name = :schema
    AND o.name = :tabela
    AND o.type = 'U';
""".format(banco=banco)

    linhas = conn.execute(
        text(sql_colunas_existentes),
        {"schema": schema, "tabela": tabela},
    ).mappings().fetchall()

    existentes = {str(linha["nome_coluna"]) for linha in linhas}

    for coluna in df.columns:
        if coluna not in existentes:
            logger.warning(
                "Coluna ausente na stage e será criada como NVARCHAR(MAX): tabela=%s | coluna=%s",
                nome_sql,
                coluna,
            )
            conn.exec_driver_sql(f"ALTER TABLE {nome_sql} ADD [{coluna}] NVARCHAR(MAX) NULL;")


def limpar_stage_sql_server(conn: Any, tabela_stage: str) -> str:
    """Limpa a stage.

    Primeiro tenta TRUNCATE. Se o usuário SQL não tiver permissão de ALTER para TRUNCATE,
    cai para DELETE, que é mais permissivo. Isso resolve o erro comum:
    Cannot find the object ... or you do not have permissions.
    """
    nome_sql = nome_tabela_stage_sql(tabela_stage)

    try:
        conn.exec_driver_sql(f"TRUNCATE TABLE {nome_sql};")
        return "TRUNCATE"
    except Exception as erro_truncate:
        logger.warning(
            "Não foi possível executar TRUNCATE na stage %s. Tentando DELETE. Erro original: %r",
            nome_sql,
            erro_truncate,
        )
        conn.exec_driver_sql(f"DELETE FROM {nome_sql};")
        return "DELETE"


def inserir_dataframe_stage_sql_server(
    conn: Any,
    df_stage: pd.DataFrame,
    tabela_stage: str,
    tamanho_lote: int = 1000,
) -> int:
    """Insere o DataFrame na stage usando SQL parametrizado e tabela 3-part.

    Não usa pandas.to_sql porque to_sql depende do banco default da conexão.
    Aqui o INSERT aponta explicitamente para [Integracao].[dbo].[df_fatocontrolecontratos].
    """
    df_stage = consolidar_colunas_duplicadas_dataframe(df_stage)

    if df_stage.empty:
        return 0

    nome_sql = nome_tabela_stage_sql(tabela_stage)
    colunas = lista_unica_preservando_ordem([str(coluna) for coluna in df_stage.columns])
    df_stage = df_stage.loc[:, colunas]

    colunas_sql = ", ".join(f"[{coluna}]" for coluna in colunas)
    parametros_sql = ", ".join(f":{coluna}" for coluna in colunas)
    sql_insert = text(f"INSERT INTO {nome_sql} ({colunas_sql}) VALUES ({parametros_sql})")

    total_inserido = 0

    for inicio in range(0, len(df_stage), tamanho_lote):
        fim = inicio + tamanho_lote
        lote = df_stage.iloc[inicio:fim]
        registros = lote.to_dict(orient="records")

        if registros:
            conn.execute(sql_insert, registros)
            total_inserido += len(registros)

        logger.info(
            "Stage %s | lote inserido: início=%s | fim=%s | linhas_lote=%s | total_inserido=%s",
            nome_sql,
            inicio,
            min(fim, len(df_stage)),
            len(registros),
            total_inserido,
        )

    return total_inserido



def normalizar_valor_stage_sql_server(valor: Any) -> str | None:
    """Normaliza valores antes de inserir na stage SQL Server.

    O caso crítico desta DAG é o CodPonto vindo do Excel como número, mas chegando
    ao pandas como float por causa de linhas vazias na mesma coluna. Sem esta
    normalização, um ponto 635 pode virar "635.0" na stage. Depois o SQL Server
    recebe TRY_CONVERT(int, '635.0') e devolve NULL.

    A regra aqui é simples:
    - vazio/NaN vira NULL;
    - número inteiro representado como float vira texto inteiro, exemplo: 635.0 -> "635";
    - texto numérico decimal inteiro também vira inteiro, exemplo: "635.0" -> "635";
    - textos comuns continuam como texto limpo.
    """
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except Exception:
        pass

    if isinstance(valor, bool):
        return "1" if valor else "0"

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        try:
            numero = float(valor)
            if numero.is_integer():
                return str(int(numero))
            return str(valor).strip()
        except Exception:
            return str(valor).strip()

    texto_valor = str(valor).strip()

    if not texto_valor:
        return None

    texto_decimal = texto_valor.replace(",", ".")

    try:
        numero = float(texto_decimal)
        if numero.is_integer() and texto_decimal.endswith(".0"):
            return str(int(numero))
    except Exception:
        pass

    return texto_valor

def carregar_dataframe_stage_sql_server(df: pd.DataFrame, tabela_stage: str) -> None:
    """Cria/limpa/recarrega a stage técnica no SQL Server.

    Corrige dois problemas:
    1. A stage pode não existir ainda.
    2. A conexão pode estar apontando para outro banco; por isso usamos nome 3-part explícito.
    """
    df_stage = consolidar_colunas_duplicadas_dataframe(df.copy())

    for coluna in df_stage.columns:
        df_stage[coluna] = df_stage[coluna].map(normalizar_valor_stage_sql_server)

    df_stage = consolidar_colunas_duplicadas_dataframe(df_stage)

    banco, schema, nome_tabela = normalizar_partes_tabela_stage(tabela_stage)
    tabela_qualificada = nome_tabela_stage_sql(tabela_stage)

    logger.info(
        "Preparando carga da stage SQL Server | banco=%s | schema=%s | tabela=%s | tabela_qualificada=%s | linhas=%s | colunas=%s",
        banco,
        schema,
        nome_tabela,
        tabela_qualificada,
        len(df_stage),
        len(df_stage.columns),
    )

    engine = obter_engine_sql_server()

    try:
        with engine.begin() as conn:
            garantir_colunas_stage_sql_server(conn, df_stage, tabela_stage)
            metodo_limpeza = limpar_stage_sql_server(conn, tabela_stage)
            linhas_inseridas = inserir_dataframe_stage_sql_server(conn, df_stage, tabela_stage)

        logger.info(
            "Stage %s carregada com sucesso. Método limpeza: %s | Linhas inseridas: %s | Colunas: %s",
            tabela_qualificada,
            metodo_limpeza,
            linhas_inseridas,
            len(df_stage.columns),
        )
    finally:
        engine.dispose()


def executar_sql(nome_etapa: str, sql: str) -> None:
    engine = obter_engine_sql_server()
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql)
        logger.info("%s executado com sucesso.", nome_etapa)
    finally:
        engine.dispose()


def garantir_pasta_escrita(pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)

    arquivo_teste = pasta / "_teste_escrita_airflow.tmp"
    with open(arquivo_teste, "w", encoding="utf-8") as arquivo:
        arquivo.write("ok")

    arquivo_teste.unlink(missing_ok=True)



CAMINHO_DADOS_CONTAINER_RAIZ = Path("/opt/airflow/Dados")

PASTA_SHAREPOINT_OBRIGATORIA_CONTAINER = PASTA_SHAREPOINT_CONTAINER

PASTA_CARGA_OBRIGATORIA_CONTAINER = Path(
    "/opt/airflow/Dados/Euromidia/Comercial/CargasSQL/ControleContratosEuromidia"
)


def caminho_esta_dentro(caminho: Path, raiz: Path) -> bool:
    """Retorna True quando caminho está dentro da raiz informada."""
    try:
        caminho.resolve().relative_to(raiz.resolve())
        return True
    except Exception:
        return False


def detectar_mount_sobreposto_em_dados(caminho: Path) -> Path | None:
    """Detecta bind mounts sobrepostos dentro de /opt/airflow/Dados.

    Por regra deste DAG, queremos que tudo que a task gere em /opt/airflow/Dados
    apareça no host em:
      /root/PythonJobs/pipelines/airflow/Dados

    Portanto, o único mount esperado é a raiz /opt/airflow/Dados. Se existir outro
    mount mais específico, como:
      /opt/airflow/Dados/Euromidia/Comercial/CargasSQL
      /opt/airflow/Dados/SharePoint/BaseDados/<pasta configurada em PASTA_BASE_DADOS_CONTRATOS_CONTAINER>

    então o arquivo vai aparecer em outro lugar do host e o Guilherme não conseguirá
    acompanhar no caminho combinado.
    """
    raiz = CAMINHO_DADOS_CONTAINER_RAIZ.resolve()
    atual = caminho.resolve()

    if not caminho_esta_dentro(atual, raiz):
        return None

    partes = []
    cursor = atual
    while True:
        partes.append(cursor)
        if cursor == raiz or cursor.parent == cursor:
            break
        cursor = cursor.parent

    for candidato in reversed(partes):
        if candidato == raiz:
            continue
        try:
            if candidato.exists() and os.path.ismount(candidato):
                return candidato
        except Exception:
            pass

    return None


def validar_caminho_visivel_no_host(caminho: Path, nome_contexto: str) -> None:
    """Bloqueia caminhos que seriam gravados fora do histórico visível do host."""
    if not caminho_esta_dentro(caminho, CAMINHO_DADOS_CONTAINER_RAIZ):
        raise PermissionError(
            f"{nome_contexto} | caminho fora da raiz obrigatória {CAMINHO_DADOS_CONTAINER_RAIZ}: {caminho}. "
            "Este DAG foi configurado para gravar apenas em /opt/airflow/Dados, que deve estar montado no host em "
            "/root/PythonJobs/pipelines/airflow/Dados."
        )

    mount_sobreposto = detectar_mount_sobreposto_em_dados(caminho)
    if mount_sobreposto is not None:
        raise PermissionError(
            f"{nome_contexto} | foi detectado um volume/mount sobreposto dentro de /opt/airflow/Dados: {mount_sobreposto}. "
            "Isso desvia os arquivos para outro caminho do host e impede acompanhar o histórico em "
            "/root/PythonJobs/pipelines/airflow/Dados. Remova do docker-compose os volumes específicos que montam "
            "/opt/airflow/Dados/Euromidia/Comercial/CargasSQL ou "
            "/opt/airflow/Dados/SharePoint/BaseDados/Base Dados - 01- Controle de Contratos. "
            "Deixe somente o volume raiz: /root/PythonJobs/pipelines/airflow/Dados:/opt/airflow/Dados:rw"
        )


def resolver_pasta_gravavel(
    pasta_preferida: Path,
    candidatos_fallback: list[Path],
    nome_contexto: str,
) -> Path:
    """Resolve uma pasta gravável SEM fallback invisível.

    Regra operacional deste DAG:
    - Arquivo baixado do SharePoint deve aparecer no host em:
      /root/PythonJobs/pipelines/airflow/Dados/SharePoint/BaseDados/<pasta configurada>

    - CSVs técnicos gerados/importados devem aparecer no host em:
      /root/PythonJobs/pipelines/airflow/Dados/Euromidia/Comercial/CargasSQL/ControleContratosEuromidia

    Por isso, este DAG não pode cair para /home/airflow nem /tmp. Se a pasta correta não
    estiver montada ou gravável, a task falha com diagnóstico claro.
    """
    if nome_contexto == "download_sharepoint_controle_contratos":
        pasta_obrigatoria = PASTA_SHAREPOINT_OBRIGATORIA_CONTAINER
    elif nome_contexto == "gerar_csv_controle_contratos":
        pasta_obrigatoria = PASTA_CARGA_OBRIGATORIA_CONTAINER
    else:
        pasta_obrigatoria = Path(pasta_preferida)

    logger.info("%s | pasta obrigatória configurada: %s", nome_contexto, pasta_obrigatoria)

    try:
        garantir_pasta_escrita(pasta_obrigatoria)
        validar_caminho_visivel_no_host(pasta_obrigatoria, nome_contexto)
        logger.info(
            "%s | pasta gravável e visível via volume raiz validada: %s",
            nome_contexto,
            pasta_obrigatoria,
        )
        return pasta_obrigatoria
    except Exception as erro:
        raise PermissionError(
            f"{nome_contexto} | a pasta obrigatória não está pronta para uso: {pasta_obrigatoria}. "
            "Corrija o docker-compose/volumes/permissões. O DAG foi bloqueado para não gravar arquivo em pasta invisível. "
            f"Erro original: {type(erro).__name__}: {erro}"
        ) from erro


# Sem fallbacks invisíveis.
# Se a pasta correta falhar, a DAG deve falhar para não esconder arquivo em /home/airflow ou /tmp.
PASTAS_FALLBACK_SHAREPOINT: list[Path] = []
PASTAS_FALLBACK_CARGA: list[Path] = []


def obter_info_arquivo_local(caminho: Path) -> dict[str, Any]:
    """Retorna metadados simples do arquivo local para logs, auditoria e amostra do plugin."""
    if not caminho.exists():
        return {
            "existe": False,
            "caminho": str(caminho),
        }

    stat = caminho.stat()
    return {
        "existe": True,
        "nome": caminho.name,
        "caminho": str(caminho),
        "tamanho_bytes": int(stat.st_size),
        "tamanho_mb": round(stat.st_size / 1024 / 1024, 4),
        "modificado_em_local": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def executar_comando_sistema(
    comando: list[str],
    nome: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Executa comando externo com logs detalhados, preservando stdout/stderr no Airflow."""
    comando_legivel = shlex.join(comando)
    logger.info("%s | comando: %s", nome, comando_legivel)

    env_execucao = env if env is not None else os.environ.copy()

    resultado = subprocess.run(
        comando,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env_execucao,
    )

    if resultado.stdout:
        logger.info("%s | stdout:\n%s", nome, resultado.stdout.strip())
    else:
        logger.info("%s | stdout vazio.", nome)

    if resultado.stderr:
        logger.info("%s | stderr:\n%s", nome, resultado.stderr.strip())
    else:
        logger.info("%s | stderr vazio.", nome)

    if resultado.returncode != 0:
        raise RuntimeError(
            f"Falha ao executar {nome}. Código: {resultado.returncode}. Comando: {comando_legivel}. "
            f"STDOUT: {resultado.stdout}. STDERR: {resultado.stderr}"
        )

    return resultado


def descrever_permissao_arquivo(caminho: Path) -> dict[str, Any]:
    """Retorna informações de permissão sem quebrar a DAG caso o path esteja inacessível."""
    info: dict[str, Any] = {
        "caminho": str(caminho),
        "existe": caminho.exists(),
        "legivel_usuario_atual": os.access(caminho, os.R_OK),
        "gravavel_usuario_atual": os.access(caminho, os.W_OK),
    }

    try:
        stat_arquivo = caminho.stat()
        info.update(
            {
                "uid": stat_arquivo.st_uid,
                "gid": stat_arquivo.st_gid,
                "modo_octal": oct(stat_arquivo.st_mode & 0o777),
                "tamanho_bytes": int(stat_arquivo.st_size),
            }
        )
    except Exception as erro:
        info["erro_stat"] = repr(erro)

    return info


def obter_contexto_usuario_execucao() -> dict[str, Any]:
    """Mostra quem está executando a task dentro do container."""
    contexto: dict[str, Any] = {}

    for nome, funcao in [
        ("uid", getattr(os, "getuid", None)),
        ("euid", getattr(os, "geteuid", None)),
        ("gid", getattr(os, "getgid", None)),
        ("egid", getattr(os, "getegid", None)),
    ]:
        try:
            contexto[nome] = funcao() if funcao else None
        except Exception as erro:
            contexto[nome] = repr(erro)

    try:
        contexto["groups"] = list(os.getgroups())
    except Exception as erro:
        contexto["groups"] = repr(erro)

    contexto["usuario_env"] = os.getenv("USER")
    contexto["home_env"] = os.getenv("HOME")
    return contexto


def candidatos_rclone_config() -> list[Path]:
    """Lista os caminhos possíveis do rclone.conf em ordem de preferência."""
    candidatos: list[Path] = []

    valor_env = os.getenv("RCLONE_CONFIG")
    if valor_env:
        candidatos.append(Path(valor_env))

    candidatos.extend(
        [
            Path("/opt/airflow/config/rclone/rclone.conf"),
            Path("/home/airflow/.config/rclone/rclone.conf"),
            Path("/root/.config/rclone/rclone.conf"),
        ]
    )

    unicos: list[Path] = []
    for candidato in candidatos:
        if candidato not in unicos:
            unicos.append(candidato)

    return unicos


def resolver_rclone_config_legivel() -> Path:
    """Resolve um rclone.conf legível.

    Observação importante:
    - Se o arquivo estiver montado como 600 root:root e a task rodar como uid 1000,
      nenhum código Python consegue ler esse arquivo sem permissão do Linux.
    - Esta função não mascara o erro: ela falha antes do rclone com mensagem objetiva,
      para evitar o ETL seguir lendo arquivo antigo.
    """
    contexto_usuario = obter_contexto_usuario_execucao()
    logger.info(
        "Contexto do usuário que executa a task: %s",
        json.dumps(contexto_usuario, ensure_ascii=False, default=str),
    )

    diagnostico: list[dict[str, Any]] = []

    for candidato in candidatos_rclone_config():
        info = descrever_permissao_arquivo(candidato)
        diagnostico.append(info)
        logger.info("Diagnóstico rclone.conf candidato: %s", json.dumps(info, ensure_ascii=False, default=str))

        if info.get("existe") and info.get("legivel_usuario_atual"):
            logger.info("rclone.conf legível selecionado: %s", candidato)
            return candidato

    raise PermissionError(
        "Nenhum rclone.conf legível foi encontrado para o usuário que executa o Airflow. "
        "Isso impede baixar o arquivo atualizado do SharePoint e a DAG foi bloqueada para não ler arquivo antigo. "
        "Diagnóstico: "
        + json.dumps(diagnostico, ensure_ascii=False, default=str)
        + " | Correção no host: deixe o arquivo legível pelo grupo do processo do Airflow "
          "(ex.: chmod 640 /root/.config/rclone/rclone.conf e chown root:0 /root/.config/rclone/rclone.conf) "
          "ou monte um rclone.conf já legível em /opt/airflow/config/rclone/rclone.conf."
    )


def montar_ambiente_rclone() -> tuple[dict[str, str], Path]:
    """Monta ambiente explícito para rclone, sempre apontando para um config legível."""
    rclone_config = resolver_rclone_config_legivel()
    ambiente = os.environ.copy()
    ambiente["RCLONE_CONFIG"] = str(rclone_config)
    return ambiente, rclone_config


def montar_comando_rclone(argumentos: list[str], rclone_config: Path) -> list[str]:
    """Monta comando rclone fixando o config para não depender de HOME ou usuário do container."""
    return ["rclone", "--config", str(rclone_config), *argumentos]


def obter_info_remote_rclone(env_rclone: dict[str, str], rclone_config: Path) -> dict[str, Any]:
    """Consulta metadados do arquivo remoto antes do download para provar que a origem oficial foi checada."""
    comando = montar_comando_rclone(
        [
            "lsjson",
            RCLONE_ARQUIVO_ORIGEM,
            "--stat",
        ],
        rclone_config,
    )

    resultado = executar_comando_sistema(
        comando,
        "rclone_lsjson_arquivo_origem_sharepoint",
        env=env_rclone,
    )

    if not resultado.stdout.strip():
        raise FileNotFoundError(
            f"O rclone não retornou metadados para o arquivo de origem: {RCLONE_ARQUIVO_ORIGEM}"
        )

    try:
        dados = json.loads(resultado.stdout)
    except Exception as erro:
        raise RuntimeError(
            "O rclone lsjson retornou saída não parseável como JSON. "
            f"Saída recebida: {resultado.stdout}"
        ) from erro

    if isinstance(dados, list):
        if not dados:
            raise FileNotFoundError(
                f"O arquivo de origem não foi encontrado no SharePoint/rclone: {RCLONE_ARQUIVO_ORIGEM}"
            )
        dados = dados[0]

    if not isinstance(dados, dict):
        raise RuntimeError(f"Formato inesperado no retorno do rclone lsjson: {dados!r}")

    return dados


def calcular_sha256_arquivo(caminho: Path, bloco_bytes: int = 1024 * 1024) -> str:
    """Calcula hash SHA256 do arquivo local para auditoria."""
    sha = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        while True:
            bloco = arquivo.read(bloco_bytes)
            if not bloco:
                break
            sha.update(bloco)
    return sha.hexdigest()


def montar_amostra_arquivo_baixado(
    caminho_destino: Path,
    info_antes: dict[str, Any],
    info_depois: dict[str, Any],
) -> list[dict[str, Any]]:
    """Monta amostra pequena para aparecer no painel/plugin de auditoria."""
    return [
        {
            "arquivo": caminho_destino.name,
            "origem_rclone": RCLONE_ARQUIVO_ORIGEM,
            "destino_local": str(caminho_destino),
            "existia_antes": info_antes.get("existe"),
            "tamanho_antes_mb": info_antes.get("tamanho_mb"),
            "tamanho_depois_mb": info_depois.get("tamanho_mb"),
            "modificado_local_depois": info_depois.get("modificado_em_local"),
        }
    ]


def separar_schema_tabela(nome_completo: str) -> tuple[str, str]:
    """Compatibilidade com versões antigas.

    Retorna apenas schema e tabela. Para carga da stage, use as funções 3-part acima.
    """
    _, schema, tabela = normalizar_partes_tabela_stage(nome_completo)
    return schema, tabela



MERGE_CONTRATOS_SQL = r"""
;WITH Base AS (
    SELECT
          TRY_CONVERT(date, [DataLancamento]) AS [DataLancamento]
        , [Cota] AS [Cota]
        , TRY_CONVERT(int, TRY_CONVERT(decimal(18,4), REPLACE(LTRIM(RTRIM(CONVERT(varchar(100), [CodPonto]))), ',', '.'))) AS [CodPonto]
        , LEFT(TRY_CONVERT(varchar(200), [CodFace]), 20) AS [CodFace]
        , LEFT(TRY_CONVERT(nvarchar(200), [CidadeExibicao]), 100) AS [CidadeExibicao]
        , LEFT(TRY_CONVERT(nvarchar(200), [Tipo]), 70) AS [Tipo]
        , LEFT(TRY_CONVERT(nvarchar(50), [Origem]), 10) AS [Origem]
        , LEFT(TRY_CONVERT(nvarchar(200), [EmpresaEuro]), 100) AS [EmpresaEuro]
        , LEFT(TRY_CONVERT(char(50), [CnpjExibibora]), 20) AS [CnpjExibibora]
        , LEFT(TRY_CONVERT(nvarchar(200), [TipoDocumento]), 70) AS [TipoDocumento]
        , LEFT(TRY_CONVERT(varchar(300), [NumeroContrato]), 150) AS [NumeroContrato]
        , LEFT(TRY_CONVERT(varchar(300), [NumeroPrevia]), 150) AS [NumeroPrevia]
        , LEFT(TRY_CONVERT(nvarchar(400), [RazaoSocial]), 200) AS [RazaoSocial]
        , LEFT(TRY_CONVERT(char(50), [CNPJ]), 20) AS [CNPJ]
        , LEFT(TRY_CONVERT(char(50), [CPF]), 20) AS [CPF]
        , LEFT(TRY_CONVERT(nvarchar(200), [MarcaExibida]), 100) AS [MarcaExibida]
        , LEFT(TRY_CONVERT(nvarchar(200), [Vendedor]), 100) AS [Vendedor]
        , LEFT(TRY_CONVERT(char(50),[SDR]), 20) AS [SDR]
        , LEFT(TRY_CONVERT(nvarchar(200), [Agencia]), 100) AS [Agencia]
        , LEFT(TRY_CONVERT(char(50), [CnpjAgencia]), 20) AS [CnpjAgencia]
        , LEFT(TRY_CONVERT(nvarchar(200), [Bureau]), 100) AS [Bureau]
        , LEFT(TRY_CONVERT(char(50), [CnpjBureau]), 20) AS [CnpjBureau]
        , LEFT(TRY_CONVERT(nvarchar(200), [Intermediario]), 100) AS [Intermediario]
        , LEFT(TRY_CONVERT(char(50), [CnpjIntermediario]), 20) AS [CnpjIntermediario]
        , TRY_CONVERT(date, [DataAssinaturaRenovacao]) AS [DataAssinaturaRenovacao]
        , LEFT(TRY_CONVERT(varchar(200), [IDTrimestre]), 20) AS [IDTrimestre]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoBrutoMensal],'.',''),',','.')) AS [FaturamentoBrutoMensal]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualPermuta],'.',''),',','.')) AS [PercentualPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([CotaOportunidade],'.',''),',','.')) AS [CotaOportunidade]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorPermuta],'.',''),',','.')) AS [ValorPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoPermuta],'.',''),',','.')) AS [FaturamentoLiquidoPermuta]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalBrutoContrato],'.',''),',','.')) AS [TotalBrutoContrato]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRCTACORDO],'.',''),',','.')) AS [TotalLiquidoContratoAGBRCTACORDO]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRVENDGERCOOR],'.',''),',','.')) AS [TotalLiquidoContratoAGBRVENDGERCOOR]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualAgencia],'.',''),',','.')) AS [PercentualAgencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorMensalAgencia],'.',''),',','.')) AS [ValorMensalAgencia]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualBureau],'.',''),',','.')) AS [PercentualBureau]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorBureauMensal],'.',''),',','.')) AS [ValorBureauMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualCartaAcordo],'.',''),',','.')) AS [PercentualCartaAcordo]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCartaAcordoMensal],'.',''),',','.')) AS [ValorCartaAcordoMensal]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorOutrasComissoes],'.',''),',','.')) AS [ValorOutrasComissoes]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoMensal],'.',''),',','.')) AS [FaturamentoLiquidoMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoVendedor],'.',''),',','.')) AS [PercentualComissaoVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedor],'.',''),',','.')) AS [ValorVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedorTotal],'.',''),',','.')) AS [ValorVendedorTotal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoCoordenacao],'.',''),',','.')) AS [PercentualComissaoCoordenacao]
    FROM [Integracao].[dbo].[df_fatocontrolecontratos]
),
BaseComRef AS (
    SELECT
          b.*
        , CONVERT(varchar(64),
            HASHBYTES('SHA2_256',
                CONCAT(
                    UPPER(LTRIM(RTRIM(COALESCE(b.NumeroContrato,'')))), '|',
                    UPPER(LTRIM(RTRIM(COALESCE(b.NumeroPrevia,'')))),  '|',
                    REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(b.CNPJ,''),'.',''),'-',''),'/',''),' ','')
                )
            ), 2
          ) AS Referencia
    FROM Base AS b
),
Agg AS (
    SELECT
          Referencia

        , MAX(NumeroContrato) AS NumeroContrato
        , MAX(NumeroPrevia)   AS NumeroPrevia
        , MAX(CNPJ)           AS CNPJ

        , MAX(DataAssinaturaRenovacao) AS DataAssinaturaRenovacao
        , MAX(IDTrimestre) AS IDTrimestre
        , MAX(DataLancamento) AS DataLancamento
        , MAX(RazaoSocial) AS RazaoSocial
        , MAX(CPF) AS CPF
        , MAX(MarcaExibida) AS MarcaExibida
        , MAX(Vendedor) AS Vendedor
        , MAX(TipoDocumento) AS TipoDocumento
        , MAX(Origem) AS Origem
        , MAX(SDR)  AS SDR
        , MAX(Agencia) AS Agencia
        , MAX(CnpjAgencia) AS CnpjAgencia
        , MAX(Bureau)  AS Bureau
        , MAX(CnpjBureau)  AS CnpjBureau
        , MAX(Intermediario)  AS Intermediario
        , MAX(CnpjIntermediario)  AS CnpjIntermediario

        , COUNT(DISTINCT CodPonto) AS QuantidadePontos
        , COUNT(DISTINCT CodFace)  AS QuantidadeFaces

        , SUM(COALESCE(FaturamentoBrutoMensal, 0)) AS TotalFaturamentoBrutoMensal
        , SUM(COALESCE(PercentualPermuta, 0)) AS TotalPercentualPermuta
        , SUM(COALESCE(CotaOportunidade, 0)) AS TotalCotaOportunidade
        , SUM(COALESCE(ValorPermuta, 0)) AS TotalValorPermuta
        , SUM(COALESCE(FaturamentoLiquidoPermuta, 0)) AS TotalFaturamentoLiquidoPermuta

        , SUM(COALESCE(TotalBrutoContrato, 0)) AS TotalBrutoContrato
        , SUM(COALESCE(TotalLiquidoContratoAGBRCTACORDO, 0))   AS TotalLiquidoContratoAGBRCTACORDO
        , SUM(COALESCE(TotalLiquidoContratoAGBRVENDGERCOOR, 0)) AS TotalLiquidoContratoAGBRVENDGERCOOR

        , SUM(COALESCE(PercentualAgencia, 0)) AS TotalPercentualAgencia
        , SUM(COALESCE(ValorMensalAgencia, 0)) AS TotalValorMensalAgencia

        , SUM(COALESCE(PercentualBureau, 0)) AS TotalPercentualBureau
        , SUM(COALESCE(ValorBureauMensal, 0))  AS TotalValorBureauMensal

        , SUM(COALESCE(PercentualCartaAcordo, 0))  AS TotalPercentualCartaAcordo
        , SUM(COALESCE(ValorCartaAcordoMensal, 0)) AS TotalValorCartaAcordoMensal

        , SUM(COALESCE(ValorOutrasComissoes, 0)) AS TotalValorOutrasComissoes
        , SUM(COALESCE(FaturamentoLiquidoMensal, 0)) AS TotalFaturamentoLiquidoMensal

        , SUM(COALESCE(PercentualComissaoVendedor, 0)) AS TotalPercentualComissaoVendedor
        , SUM(COALESCE(ValorVendedor, 0)) AS TotalValorVendedor
        , SUM(COALESCE(ValorVendedorTotal, 0)) AS ValorVendedorTotal

        , SUM(COALESCE(PercentualComissaoCoordenacao, 0)) AS TotalPercentualComissaoCoordenacao
    FROM BaseComRef
    GROUP BY
          Referencia
),
Src AS (
    SELECT
          Referencia
        , NumeroContrato
        , NumeroPrevia
        , CNPJ
        , DataAssinaturaRenovacao
        , IDTrimestre
        , DataLancamento
        , RazaoSocial
        , CPF
        , MarcaExibida
        , Vendedor
        , TipoDocumento
        , Origem
        , SDR
        , Agencia
        , CnpjAgencia
        , Bureau
        , CnpjBureau
        , Intermediario
        , CnpjIntermediario
        , QuantidadePontos
        , QuantidadeFaces
        , TotalFaturamentoBrutoMensal
        , TotalPercentualPermuta
        , TotalCotaOportunidade
        , TotalValorPermuta
        , TotalFaturamentoLiquidoPermuta
        , TotalBrutoContrato
        , TotalLiquidoContratoAGBRCTACORDO
        , TotalLiquidoContratoAGBRVENDGERCOOR
        , TotalPercentualAgencia
        , TotalValorMensalAgencia
        , TotalPercentualBureau
        , TotalValorBureauMensal
        , TotalPercentualCartaAcordo
        , TotalValorCartaAcordoMensal
        , TotalValorOutrasComissoes
        , TotalFaturamentoLiquidoMensal
        , TotalPercentualComissaoVendedor
        , TotalValorVendedor
        , ValorVendedorTotal
        , TotalPercentualComissaoCoordenacao
    FROM Agg
)
MERGE [Silver].[FatoControleContratosEuromidia] AS T
USING Src AS S
    ON T.[Referencia] = S.[Referencia]
WHEN MATCHED AND (
       ISNULL(T.NumeroContrato,'') <> ISNULL(S.NumeroContrato,'')
    OR ISNULL(T.NumeroPrevia,'')   <> ISNULL(S.NumeroPrevia,'')
    OR ISNULL(T.CNPJ,'')           <> ISNULL(S.CNPJ,'')
    OR ISNULL(T.DataAssinaturaRenovacao,'19000101') <> ISNULL(S.DataAssinaturaRenovacao,'19000101')
    OR ISNULL(T.IDTrimestre,'')     <> ISNULL(S.IDTrimestre,'')
    OR ISNULL(T.DataLancamento,'19000101') <> ISNULL(S.DataLancamento,'19000101')
    OR ISNULL(T.RazaoSocial,'')   <> ISNULL(S.RazaoSocial,'')
    OR ISNULL(T.CPF,'')  <> ISNULL(S.CPF,'')
    OR ISNULL(T.MarcaExibida,'')  <> ISNULL(S.MarcaExibida,'')
    OR ISNULL(T.Vendedor,'') <> ISNULL(S.Vendedor,'')
    OR ISNULL(T.TipoDocumento,'')  <> ISNULL(S.TipoDocumento,'')
    OR ISNULL(T.Origem,'')  <> ISNULL(S.Origem,'')
    OR ISNULL(T.SDR,'')  <> ISNULL(S.SDR,'')
    OR ISNULL(T.Agencia,'')  <> ISNULL(S.Agencia,'')
    OR ISNULL(T.CnpjAgencia,'')   <> ISNULL(S.CnpjAgencia,'')
    OR ISNULL(T.Bureau,'') <> ISNULL(S.Bureau,'')
    OR ISNULL(T.CnpjBureau,'')   <> ISNULL(S.CnpjBureau,'')
    OR ISNULL(T.Intermediario,'')   <> ISNULL(S.Intermediario,'')
    OR ISNULL(T.CnpjIntermediario,'') <> ISNULL(S.CnpjIntermediario,'')
    OR ISNULL(T.QuantidadePontos,-1) <> ISNULL(S.QuantidadePontos,-1)
    OR ISNULL(T.QuantidadeFaces,-1)  <> ISNULL(S.QuantidadeFaces,-1)
    OR ISNULL(T.TotalFaturamentoBrutoMensal,0) <> ISNULL(S.TotalFaturamentoBrutoMensal,0)
    OR ISNULL(T.TotalPercentualPermuta,0) <> ISNULL(S.TotalPercentualPermuta,0)
    OR ISNULL(T.TotalCotaOportunidade,0)  <> ISNULL(S.TotalCotaOportunidade,0)
    OR ISNULL(T.TotalValorPermuta,0)  <> ISNULL(S.TotalValorPermuta,0)
    OR ISNULL(T.TotalFaturamentoLiquidoPermuta,0) <> ISNULL(S.TotalFaturamentoLiquidoPermuta,0)
    OR ISNULL(T.TotalBrutoContrato,0) <> ISNULL(S.TotalBrutoContrato,0)
    OR ISNULL(T.TotalLiquidoContratoAGBRCTACORDO,0) <> ISNULL(S.TotalLiquidoContratoAGBRCTACORDO,0)
    OR ISNULL(T.TotalLiquidoContratoAGBRVENDGERCOOR,0) <> ISNULL(S.TotalLiquidoContratoAGBRVENDGERCOOR,0)
    OR ISNULL(T.TotalPercentualAgencia,0)  <> ISNULL(S.TotalPercentualAgencia,0)
    OR ISNULL(T.TotalValorMensalAgencia,0) <> ISNULL(S.TotalValorMensalAgencia,0)
    OR ISNULL(T.TotalPercentualBureau,0) <> ISNULL(S.TotalPercentualBureau,0)
    OR ISNULL(T.TotalValorBureauMensal,0)   <> ISNULL(S.TotalValorBureauMensal,0)
    OR ISNULL(T.TotalPercentualCartaAcordo,0)  <> ISNULL(S.TotalPercentualCartaAcordo,0)
    OR ISNULL(T.TotalValorCartaAcordoMensal,0) <> ISNULL(S.TotalValorCartaAcordoMensal,0)
    OR ISNULL(T.TotalValorOutrasComissoes,0)   <> ISNULL(S.TotalValorOutrasComissoes,0)
    OR ISNULL(T.TotalFaturamentoLiquidoMensal,0) <> ISNULL(S.TotalFaturamentoLiquidoMensal,0)
    OR ISNULL(T.TotalPercentualComissaoVendedor,0) <> ISNULL(S.TotalPercentualComissaoVendedor,0)
    OR ISNULL(T.TotalValorVendedor,0)  <> ISNULL(S.TotalValorVendedor,0)
    OR ISNULL(T.ValorVendedorTotal,0)   <> ISNULL(S.ValorVendedorTotal,0)
    OR ISNULL(T.TotalPercentualComissaoCoordenacao,0) <> ISNULL(S.TotalPercentualComissaoCoordenacao,0)
)
THEN UPDATE SET
      T.[DataAtualizacao] = GETDATE()
    , T.[NumeroContrato] = S.[NumeroContrato]
    , T.[NumeroPrevia]   = S.[NumeroPrevia]
    , T.[CNPJ]  = S.[CNPJ]
    , T.[DataAssinaturaRenovacao] = S.[DataAssinaturaRenovacao]
    , T.[IDTrimestre]  = S.[IDTrimestre]
    , T.[DataLancamento] = S.[DataLancamento]
    , T.[RazaoSocial]  = S.[RazaoSocial]
    , T.[CPF]  = S.[CPF]
    , T.[MarcaExibida]   = S.[MarcaExibida]
    , T.[Vendedor] = S.[Vendedor]
    , T.[TipoDocumento]  = S.[TipoDocumento]
    , T.[Origem] = S.[Origem]
    , T.[SDR]  = S.[SDR]
    , T.[Agencia]     = S.[Agencia]
    , T.[CnpjAgencia]  = S.[CnpjAgencia]
    , T.[Bureau]  = S.[Bureau]
    , T.[CnpjBureau]     = S.[CnpjBureau]
    , T.[Intermediario]  = S.[Intermediario]
    , T.[CnpjIntermediario] = S.[CnpjIntermediario]
    , T.[QuantidadePontos] = S.[QuantidadePontos]
    , T.[QuantidadeFaces]  = S.[QuantidadeFaces]
    , T.[TotalFaturamentoBrutoMensal] = S.[TotalFaturamentoBrutoMensal]
    , T.[TotalPercentualPermuta] = S.[TotalPercentualPermuta]
    , T.[TotalCotaOportunidade]  = S.[TotalCotaOportunidade]
    , T.[TotalValorPermuta]   = S.[TotalValorPermuta]
    , T.[TotalFaturamentoLiquidoPermuta] = S.[TotalFaturamentoLiquidoPermuta]
    , T.[TotalBrutoContrato]  = S.[TotalBrutoContrato]
    , T.[TotalLiquidoContratoAGBRCTACORDO] = S.[TotalLiquidoContratoAGBRCTACORDO]
    , T.[TotalLiquidoContratoAGBRVENDGERCOOR] = S.[TotalLiquidoContratoAGBRVENDGERCOOR]
    , T.[TotalPercentualAgencia] = S.[TotalPercentualAgencia]
    , T.[TotalValorMensalAgencia] = S.[TotalValorMensalAgencia]
    , T.[TotalPercentualBureau] = S.[TotalPercentualBureau]
    , T.[TotalValorBureauMensal]  = S.[TotalValorBureauMensal]
    , T.[TotalPercentualCartaAcordo]  = S.[TotalPercentualCartaAcordo]
    , T.[TotalValorCartaAcordoMensal] = S.[TotalValorCartaAcordoMensal]
    , T.[TotalValorOutrasComissoes]   = S.[TotalValorOutrasComissoes]
    , T.[TotalFaturamentoLiquidoMensal] = S.[TotalFaturamentoLiquidoMensal]
    , T.[TotalPercentualComissaoVendedor] = S.[TotalPercentualComissaoVendedor]
    , T.[TotalValorVendedor]  = S.[TotalValorVendedor]
    , T.[ValorVendedorTotal]  = S.[ValorVendedorTotal]
    , T.[TotalPercentualComissaoCoordenacao] = S.[TotalPercentualComissaoCoordenacao]
WHEN NOT MATCHED BY TARGET
THEN INSERT (
      [Referencia]
    , [NumeroContrato]
    , [NumeroPrevia]
    , [CNPJ]
    , [DataAssinaturaRenovacao]
    , [IDTrimestre]
    , [DataLancamento]
    , [RazaoSocial]
    , [CPF]
    , [MarcaExibida]
    , [Vendedor]
    , [TipoDocumento]
    , [Origem]
    , [SDR]
    , [Agencia]
    , [CnpjAgencia]
    , [Bureau]
    , [CnpjBureau]
    , [Intermediario]
    , [CnpjIntermediario]
    , [QuantidadePontos]
    , [QuantidadeFaces]
    , [TotalFaturamentoBrutoMensal]
    , [TotalPercentualPermuta]
    , [TotalCotaOportunidade]
    , [TotalValorPermuta]
    , [TotalFaturamentoLiquidoPermuta]
    , [TotalBrutoContrato]
    , [TotalLiquidoContratoAGBRCTACORDO]
    , [TotalLiquidoContratoAGBRVENDGERCOOR]
    , [TotalPercentualAgencia]
    , [TotalValorMensalAgencia]
    , [TotalPercentualBureau]
    , [TotalValorBureauMensal]
    , [TotalPercentualCartaAcordo]
    , [TotalValorCartaAcordoMensal]
    , [TotalValorOutrasComissoes]
    , [TotalFaturamentoLiquidoMensal]
    , [TotalPercentualComissaoVendedor]
    , [TotalValorVendedor]
    , [ValorVendedorTotal]
    , [TotalPercentualComissaoCoordenacao]
) VALUES (
      S.[Referencia]
    , S.[NumeroContrato]
    , S.[NumeroPrevia]
    , S.[CNPJ]
    , S.[DataAssinaturaRenovacao]
    , S.[IDTrimestre]
    , S.[DataLancamento]
    , S.[RazaoSocial]
    , S.[CPF]
    , S.[MarcaExibida]
    , S.[Vendedor]
    , S.[TipoDocumento]
    , S.[Origem]
    , S.[SDR]
    , S.[Agencia]
    , S.[CnpjAgencia]
    , S.[Bureau]
    , S.[CnpjBureau]
    , S.[Intermediario]
    , S.[CnpjIntermediario]
    , S.[QuantidadePontos]
    , S.[QuantidadeFaces]
    , S.[TotalFaturamentoBrutoMensal]
    , S.[TotalPercentualPermuta]
    , S.[TotalCotaOportunidade]
    , S.[TotalValorPermuta]
    , S.[TotalFaturamentoLiquidoPermuta]
    , S.[TotalBrutoContrato]
    , S.[TotalLiquidoContratoAGBRCTACORDO]
    , S.[TotalLiquidoContratoAGBRVENDGERCOOR]
    , S.[TotalPercentualAgencia]
    , S.[TotalValorMensalAgencia]
    , S.[TotalPercentualBureau]
    , S.[TotalValorBureauMensal]
    , S.[TotalPercentualCartaAcordo]
    , S.[TotalValorCartaAcordoMensal]
    , S.[TotalValorOutrasComissoes]
    , S.[TotalFaturamentoLiquidoMensal]
    , S.[TotalPercentualComissaoVendedor]
    , S.[TotalValorVendedor]
    , S.[ValorVendedorTotal]
    , S.[TotalPercentualComissaoCoordenacao]
);
"""

MERGE_ITENS_SQL = r"""
;WITH Itens AS (
    SELECT
          TRY_CONVERT(date, [DataLancamento]) AS [DataLancamento]
        , TRY_CONVERT(int, TRY_CONVERT(decimal(18,2), REPLACE([Cota], ',', '.'))) AS [Cota]

        , TRY_CONVERT(int, TRY_CONVERT(decimal(18,4), REPLACE(LTRIM(RTRIM(CONVERT(varchar(100), [CodPonto]))), ',', '.'))) AS [CodPonto]
        , LEFT(TRY_CONVERT(varchar(200), [CodFace]), 20) AS [CodFace]
        , LEFT(TRY_CONVERT(nvarchar(200), [CidadeExibicao]), 100) AS [CidadeExibicao]
        , LEFT(TRY_CONVERT(nvarchar(200), [Tipo]), 70) AS [Tipo]
        , LEFT(TRY_CONVERT(nvarchar(50), [Origem]), 10) AS [Origem]
        , LEFT(TRY_CONVERT(nvarchar(200), [EmpresaEuro]), 100) AS [EmpresaEuro]
        , LEFT(TRY_CONVERT(char(50), [CnpjExibibora]), 20) AS [CnpjExibibora]
        , LEFT(TRY_CONVERT(nvarchar(200), [TipoDocumento]), 70) AS [TipoDocumento]

        , LEFT(TRY_CONVERT(varchar(300), [NumeroContrato]), 150) AS [NumeroContrato]
        , LEFT(TRY_CONVERT(varchar(300), [NumeroPrevia]), 150) AS [NumeroPrevia]
        , LEFT(TRY_CONVERT(char(50), [CNPJ]), 20) AS [CNPJ]
        , LEFT(TRY_CONVERT(char(50), [CPF]), 20) AS [CPF]

        , LEFT(TRY_CONVERT(nvarchar(400), [RazaoSocial]), 200) AS [RazaoSocial]
        , LEFT(TRY_CONVERT(nvarchar(200), [MarcaExibida]), 100) AS [MarcaExibida]
        , LEFT(TRY_CONVERT(nvarchar(200), [Vendedor]), 100) AS [Vendedor]
        , LEFT(TRY_CONVERT(char(50), [SDR]), 20) AS [SDR]
        , LEFT(TRY_CONVERT(nvarchar(200), [Agencia]), 100) AS [Agencia]
        , LEFT(TRY_CONVERT(char(50), [CnpjAgencia]), 20) AS [CnpjAgencia]
        , LEFT(TRY_CONVERT(nvarchar(200), [Bureau]), 100) AS [Bureau]
        , LEFT(TRY_CONVERT(char(50), [CnpjBureau]), 20) AS [CnpjBureau]
        , LEFT(TRY_CONVERT(nvarchar(200), [Intermediario]), 100) AS [Intermediario]
        , LEFT(TRY_CONVERT(char(50), [CnpjIntermediario]), 20) AS [CnpjIntermediario]

        , TRY_CONVERT(date, [DataAssinaturaRenovacao]) AS [DataAssinaturaRenovacao]
        , LEFT(TRY_CONVERT(varchar(200), [IDTrimestre]), 20) AS [IDTrimestre]
        , TRY_CONVERT(int, TRY_CONVERT(decimal(18,4), REPLACE(LTRIM(RTRIM(CONVERT(varchar(100), [TexmpoExposicao]))), ',', '.'))) AS [TexmpoExposicao]
        , TRY_CONVERT(date, [DataInicioPrevisto]) AS [DataInicioPrevisto]
        , TRY_CONVERT(date, [DataTerminoPrevisto]) AS [DataTerminoPrevisto]
        , LEFT(TRY_CONVERT(char(10), [InicioRenovacao]), 2) AS [InicioRenovacao]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoBrutoMensal],'.',''),',','.')) AS [FaturamentoBrutoMensal]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualPermuta],'.',''),',','.')) AS [PercentualPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([CotaOportunidade],'.',''),',','.')) AS [CotaOportunidade]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorPermuta],'.',''),',','.')) AS [ValorPermuta]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoPermuta],'.',''),',','.')) AS [FaturamentoLiquidoPermuta]

        , TRY_CONVERT(int, TRY_CONVERT(decimal(18,4), REPLACE(LTRIM(RTRIM(CONVERT(varchar(100), [NumeroParcelas]))), ',', '.'))) AS [NumeroParcelas]
        , TRY_CONVERT(date, [DataInicioVencimento]) AS [DataInicioVencimento]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalBrutoContrato],'.',''),',','.')) AS [TotalBrutoContrato]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRCTACORDO],'.',''),',','.')) AS [TotalLiquidoContratoAGBRCTACORDO]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([TotalLiquidoContratoAGBRVENDGERCOOR],'.',''),',','.')) AS [TotalLiquidoContratoAGBRVENDGERCOOR]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualAgencia],'.',''),',','.')) AS [PercentualAgencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorMensalAgencia],'.',''),',','.')) AS [ValorMensalAgencia]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualBureau],'.',''),',','.')) AS [PercentualBureau]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([ValorBureauMensal],'.',''),',','.')) AS [ValorBureauMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualCartaAcordo],'.',''),',','.')) AS [PercentualCartaAcordo]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCartaAcordoMensal],'.',''),',','.')) AS [ValorCartaAcordoMensal]

        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorOutrasComissoes],'.',''),',','.')) AS [ValorOutrasComissoes]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoMensal],'.',''),',','.')) AS [FaturamentoLiquidoMensal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoVendedor],'.',''),',','.')) AS [PercentualComissaoVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedor],'.',''),',','.')) AS [ValorVendedor]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorVendedorTotal],'.',''),',','.')) AS [ValorVendedorTotal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoCoordenacao],'.',''),',','.')) AS [PercentualComissaoCoordenacao]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCoordenador],'.',''),',','.')) AS [ValorCoordenador]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorCoordenadorTotal],'.',''),',','.')) AS [ValorCoordenadorTotal]

        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([PercentualComissaoGerencia],'.',''),',','.')) AS [PercentualComissaoGerencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorGerencia],'.',''),',','.')) AS [ValorGerencia]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([ValorGerenciaTotal],'.',''),',','.')) AS [ValorGerenciaTotal]

        , LEFT(TRY_CONVERT(char(10), [AtivoCancelamento]), 2) AS [AtivoCancelamento]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([FaturamentoLiquidoFinalMensal],'.',''),',','.')) AS [FaturamentoLiquidoFinalMensal]
        , TRY_CONVERT(decimal(5,2),  REPLACE(REPLACE([ComissaoGerenciaNordeste],'.',''),',','.')) AS [ComissaoGerenciaNordeste]
        , TRY_CONVERT(decimal(19,2), REPLACE(REPLACE([Faturamento],'.',''),',','.')) AS [Faturamento]
        , TRY_CONVERT(date, [DataCancelamento]) AS [DataCancelamento]
        , LEFT(TRY_CONVERT(nvarchar(300), [OBS]), 150) AS [OBS]
    FROM [Integracao].[dbo].[df_fatocontrolecontratos]
),
Final AS (
    SELECT
        CONVERT(varchar(64),
            HASHBYTES('SHA2_256',
                CONCAT(
                    UPPER(LTRIM(RTRIM(COALESCE(NumeroContrato,'')))), '|',
                    UPPER(LTRIM(RTRIM(COALESCE(NumeroPrevia,'')))),  '|',
                    REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(CNPJ,''),'.',''),'-',''),'/',''),' ',''), '|',
                    COALESCE(CONVERT(varchar(30), CodPonto), ''), '|',
                    UPPER(LTRIM(RTRIM(COALESCE(CodFace,'')))), '|',
                    COALESCE(CONVERT(varchar(10), DataInicioPrevisto, 23), ''), '|',
                    COALESCE(CONVERT(varchar(10), DataTerminoPrevisto, 23), '')
                )
            ), 2
        ) AS [Referencia]
      , CAST(NULL AS INT) AS [IDFatoControleContratoEuromidia]
      , [NumeroContrato]
      , [NumeroPrevia]
      , [CNPJ]
      , [CodPonto]
      , [CodFace]
      , [DataLancamento]
      , [Cota]
      , [CidadeExibicao]
      , [Tipo]
      , [Origem]
      , [EmpresaEuro]
      , [CnpjExibibora]
      , [TipoDocumento]
      , [RazaoSocial]
      , [CPF]
      , [MarcaExibida]
      , [Vendedor]
      , [SDR]
      , [Agencia]
      , [CnpjAgencia]
      , [Bureau]
      , [CnpjBureau]
      , [Intermediario]
      , [CnpjIntermediario]
      , [DataAssinaturaRenovacao]
      , [IDTrimestre]
      , [TexmpoExposicao]
      , [DataInicioPrevisto]
      , [DataTerminoPrevisto]
      , [InicioRenovacao]
      , [FaturamentoBrutoMensal]
      , [PercentualPermuta]
      , [CotaOportunidade]
      , [ValorPermuta]
      , [FaturamentoLiquidoPermuta]
      , [NumeroParcelas]
      , [DataInicioVencimento]
      , [TotalBrutoContrato]
      , [TotalLiquidoContratoAGBRCTACORDO]
      , [TotalLiquidoContratoAGBRVENDGERCOOR]
      , [PercentualAgencia]
      , [ValorMensalAgencia]
      , [PercentualBureau]
      , [ValorBureauMensal]
      , [PercentualCartaAcordo]
      , [ValorCartaAcordoMensal]
      , [ValorOutrasComissoes]
      , [FaturamentoLiquidoMensal]
      , [PercentualComissaoVendedor]
      , [ValorVendedor]
      , [ValorVendedorTotal]
      , [PercentualComissaoCoordenacao]
      , [ValorCoordenador]
      , [ValorCoordenadorTotal]
      , [PercentualComissaoGerencia]
      , [ValorGerencia]
      , [ValorGerenciaTotal]
      , [AtivoCancelamento]
      , [FaturamentoLiquidoFinalMensal]
      , [ComissaoGerenciaNordeste]
      , [Faturamento]
      , [DataCancelamento]
      , [OBS]
    FROM Itens
),
Fonte AS (
    SELECT
          f.Referencia
        , MAX(f.IDFatoControleContratoEuromidia) AS IDFatoControleContratoEuromidia
        , MAX(f.NumeroContrato) AS NumeroContrato
        , MAX(f.NumeroPrevia) AS NumeroPrevia
        , MAX(f.CNPJ) AS CNPJ
        , MAX(f.CodPonto) AS CodPonto
        , MAX(f.CodFace) AS CodFace
        , MAX(f.DataLancamento) AS DataLancamento
        , MAX(f.Cota) AS Cota
        , MAX(f.CidadeExibicao) AS CidadeExibicao
        , MAX(f.Tipo) AS Tipo
        , MAX(f.Origem) AS Origem
        , MAX(f.EmpresaEuro) AS EmpresaEuro
        , MAX(f.CnpjExibibora) AS CnpjExibibora
        , MAX(f.TipoDocumento) AS TipoDocumento
        , MAX(f.RazaoSocial) AS RazaoSocial
        , MAX(f.CPF) AS CPF
        , MAX(f.MarcaExibida) AS MarcaExibida
        , MAX(f.Vendedor) AS Vendedor
        , MAX(f.SDR) AS SDR
        , MAX(f.Agencia) AS Agencia
        , MAX(f.CnpjAgencia) AS CnpjAgencia
        , MAX(f.Bureau) AS Bureau
        , MAX(f.CnpjBureau) AS CnpjBureau
        , MAX(f.Intermediario) AS Intermediario
        , MAX(f.CnpjIntermediario) AS CnpjIntermediario
        , MAX(f.DataAssinaturaRenovacao) AS DataAssinaturaRenovacao
        , MAX(f.IDTrimestre) AS IDTrimestre
        , MAX(f.TexmpoExposicao) AS TexmpoExposicao
        , MAX(f.DataInicioPrevisto) AS DataInicioPrevisto
        , MAX(f.DataTerminoPrevisto) AS DataTerminoPrevisto
        , MAX(f.InicioRenovacao) AS InicioRenovacao
        , MAX(f.FaturamentoBrutoMensal) AS FaturamentoBrutoMensal
        , MAX(f.PercentualPermuta) AS PercentualPermuta
        , MAX(f.CotaOportunidade) AS CotaOportunidade
        , MAX(f.ValorPermuta) AS ValorPermuta
        , MAX(f.FaturamentoLiquidoPermuta) AS FaturamentoLiquidoPermuta
        , MAX(f.NumeroParcelas) AS NumeroParcelas
        , MAX(f.DataInicioVencimento) AS DataInicioVencimento
        , MAX(f.TotalBrutoContrato) AS TotalBrutoContrato
        , MAX(f.TotalLiquidoContratoAGBRCTACORDO) AS TotalLiquidoContratoAGBRCTACORDO
        , MAX(f.TotalLiquidoContratoAGBRVENDGERCOOR) AS TotalLiquidoContratoAGBRVENDGERCOOR
        , MAX(f.PercentualAgencia) AS PercentualAgencia
        , MAX(f.ValorMensalAgencia) AS ValorMensalAgencia
        , MAX(f.PercentualBureau) AS PercentualBureau
        , MAX(f.ValorBureauMensal) AS ValorBureauMensal
        , MAX(f.PercentualCartaAcordo) AS PercentualCartaAcordo
        , MAX(f.ValorCartaAcordoMensal) AS ValorCartaAcordoMensal
        , MAX(f.ValorOutrasComissoes) AS ValorOutrasComissoes
        , MAX(f.FaturamentoLiquidoMensal) AS FaturamentoLiquidoMensal
        , MAX(f.PercentualComissaoVendedor) AS PercentualComissaoVendedor
        , MAX(f.ValorVendedor) AS ValorVendedor
        , MAX(f.ValorVendedorTotal) AS ValorVendedorTotal
        , MAX(f.PercentualComissaoCoordenacao) AS PercentualComissaoCoordenacao
        , MAX(f.ValorCoordenador) AS ValorCoordenador
        , MAX(f.ValorCoordenadorTotal) AS ValorCoordenadorTotal
        , MAX(f.PercentualComissaoGerencia) AS PercentualComissaoGerencia
        , MAX(f.ValorGerencia) AS ValorGerencia
        , MAX(f.ValorGerenciaTotal) AS ValorGerenciaTotal
        , MAX(f.AtivoCancelamento) AS AtivoCancelamento
        , MAX(f.FaturamentoLiquidoFinalMensal) AS FaturamentoLiquidoFinalMensal
        , MAX(f.ComissaoGerenciaNordeste) AS ComissaoGerenciaNordeste
        , MAX(f.Faturamento) AS Faturamento
        , MAX(f.DataCancelamento) AS DataCancelamento
        , MAX(f.OBS) AS OBS
    FROM Final f
    GROUP BY f.Referencia
)
MERGE INTO [Silver].[FatoControleContratosItensEuromidia] AS T
USING Fonte AS S
    ON T.Referencia = S.Referencia
WHEN MATCHED THEN
    UPDATE SET
          T.DataAtualizacao = GETDATE()
        , T.IDFatoControleContratoEuromidia = S.IDFatoControleContratoEuromidia
        , T.NumeroContrato = S.NumeroContrato
        , T.NumeroPrevia = S.NumeroPrevia
        , T.CNPJ = S.CNPJ
        , T.CodPonto = S.CodPonto
        , T.CodFace = S.CodFace
        , T.DataLancamento = S.DataLancamento
        , T.Cota = S.Cota
        , T.CidadeExibicao = S.CidadeExibicao
        , T.Tipo = S.Tipo
        , T.Origem = S.Origem
        , T.EmpresaEuro = S.EmpresaEuro
        , T.CnpjExibibora = S.CnpjExibibora
        , T.TipoDocumento = S.TipoDocumento
        , T.RazaoSocial = S.RazaoSocial
        , T.CPF = S.CPF
        , T.MarcaExibida = S.MarcaExibida
        , T.Vendedor = S.Vendedor
        , T.SDR = S.SDR
        , T.Agencia = S.Agencia
        , T.CnpjAgencia = S.CnpjAgencia
        , T.Bureau = S.Bureau
        , T.CnpjBureau = S.CnpjBureau
        , T.Intermediario = S.Intermediario
        , T.CnpjIntermediario = S.CnpjIntermediario
        , T.DataAssinaturaRenovacao = S.DataAssinaturaRenovacao
        , T.IDTrimestre = S.IDTrimestre
        , T.TexmpoExposicao = S.TexmpoExposicao
        , T.DataInicioPrevisto = S.DataInicioPrevisto
        , T.DataTerminoPrevisto = S.DataTerminoPrevisto
        , T.InicioRenovacao = S.InicioRenovacao
        , T.FaturamentoBrutoMensal = S.FaturamentoBrutoMensal
        , T.PercentualPermuta = S.PercentualPermuta
        , T.CotaOportunidade = S.CotaOportunidade
        , T.ValorPermuta = S.ValorPermuta
        , T.FaturamentoLiquidoPermuta = S.FaturamentoLiquidoPermuta
        , T.NumeroParcelas = S.NumeroParcelas
        , T.DataInicioVencimento = S.DataInicioVencimento
        , T.TotalBrutoContrato = S.TotalBrutoContrato
        , T.TotalLiquidoContratoAGBRCTACORDO = S.TotalLiquidoContratoAGBRCTACORDO
        , T.TotalLiquidoContratoAGBRVENDGERCOOR = S.TotalLiquidoContratoAGBRVENDGERCOOR
        , T.PercentualAgencia = S.PercentualAgencia
        , T.ValorMensalAgencia = S.ValorMensalAgencia
        , T.PercentualBureau = S.PercentualBureau
        , T.ValorBureauMensal = S.ValorBureauMensal
        , T.PercentualCartaAcordo = S.PercentualCartaAcordo
        , T.ValorCartaAcordoMensal = S.ValorCartaAcordoMensal
        , T.ValorOutrasComissoes = S.ValorOutrasComissoes
        , T.FaturamentoLiquidoMensal = S.FaturamentoLiquidoMensal
        , T.PercentualComissaoVendedor = S.PercentualComissaoVendedor
        , T.ValorVendedor = S.ValorVendedor
        , T.ValorVendedorTotal = S.ValorVendedorTotal
        , T.PercentualComissaoCoordenacao = S.PercentualComissaoCoordenacao
        , T.ValorCoordenador = S.ValorCoordenador
        , T.ValorCoordenadorTotal = S.ValorCoordenadorTotal
        , T.PercentualComissaoGerencia = S.PercentualComissaoGerencia
        , T.ValorGerencia = S.ValorGerencia
        , T.ValorGerenciaTotal = S.ValorGerenciaTotal
        , T.AtivoCancelamento = S.AtivoCancelamento
        , T.FaturamentoLiquidoFinalMensal = S.FaturamentoLiquidoFinalMensal
        , T.ComissaoGerenciaNordeste = S.ComissaoGerenciaNordeste
        , T.Faturamento = S.Faturamento
        , T.DataCancelamento = S.DataCancelamento
        , T.OBS = S.OBS
WHEN NOT MATCHED BY TARGET THEN
    INSERT (
          [IDFatoControleContratoEuromidia]
        , [Referencia]
        , [NumeroContrato]
        , [NumeroPrevia]
        , [CNPJ]
        , [CodPonto]
        , [CodFace]
        , [DataLancamento]
        , [Cota]
        , [CidadeExibicao]
        , [Tipo]
        , [Origem]
        , [EmpresaEuro]
        , [CnpjExibibora]
        , [TipoDocumento]
        , [RazaoSocial]
        , [CPF]
        , [MarcaExibida]
        , [Vendedor]
        , [SDR]
        , [Agencia]
        , [CnpjAgencia]
        , [Bureau]
        , [CnpjBureau]
        , [Intermediario]
        , [CnpjIntermediario]
        , [DataAssinaturaRenovacao]
        , [IDTrimestre]
        , [TexmpoExposicao]
        , [DataInicioPrevisto]
        , [DataTerminoPrevisto]
        , [InicioRenovacao]
        , [FaturamentoBrutoMensal]
        , [PercentualPermuta]
        , [CotaOportunidade]
        , [ValorPermuta]
        , [FaturamentoLiquidoPermuta]
        , [NumeroParcelas]
        , [DataInicioVencimento]
        , [TotalBrutoContrato]
        , [TotalLiquidoContratoAGBRCTACORDO]
        , [TotalLiquidoContratoAGBRVENDGERCOOR]
        , [PercentualAgencia]
        , [ValorMensalAgencia]
        , [PercentualBureau]
        , [ValorBureauMensal]
        , [PercentualCartaAcordo]
        , [ValorCartaAcordoMensal]
        , [ValorOutrasComissoes]
        , [FaturamentoLiquidoMensal]
        , [PercentualComissaoVendedor]
        , [ValorVendedor]
        , [ValorVendedorTotal]
        , [PercentualComissaoCoordenacao]
        , [ValorCoordenador]
        , [ValorCoordenadorTotal]
        , [PercentualComissaoGerencia]
        , [ValorGerencia]
        , [ValorGerenciaTotal]
        , [AtivoCancelamento]
        , [FaturamentoLiquidoFinalMensal]
        , [ComissaoGerenciaNordeste]
        , [Faturamento]
        , [DataCancelamento]
        , [OBS]
    )
    VALUES (
          S.IDFatoControleContratoEuromidia
        , S.Referencia
        , S.NumeroContrato
        , S.NumeroPrevia
        , S.CNPJ
        , S.CodPonto
        , S.CodFace
        , S.DataLancamento
        , S.Cota
        , S.CidadeExibicao
        , S.Tipo
        , S.Origem
        , S.EmpresaEuro
        , S.CnpjExibibora
        , S.TipoDocumento
        , S.RazaoSocial
        , S.CPF
        , S.MarcaExibida
        , S.Vendedor
        , S.SDR
        , S.Agencia
        , S.CnpjAgencia
        , S.Bureau
        , S.CnpjBureau
        , S.Intermediario
        , S.CnpjIntermediario
        , S.DataAssinaturaRenovacao
        , S.IDTrimestre
        , S.TexmpoExposicao
        , S.DataInicioPrevisto
        , S.DataTerminoPrevisto
        , S.InicioRenovacao
        , S.FaturamentoBrutoMensal
        , S.PercentualPermuta
        , S.CotaOportunidade
        , S.ValorPermuta
        , S.FaturamentoLiquidoPermuta
        , S.NumeroParcelas
        , S.DataInicioVencimento
        , S.TotalBrutoContrato
        , S.TotalLiquidoContratoAGBRCTACORDO
        , S.TotalLiquidoContratoAGBRVENDGERCOOR
        , S.PercentualAgencia
        , S.ValorMensalAgencia
        , S.PercentualBureau
        , S.ValorBureauMensal
        , S.PercentualCartaAcordo
        , S.ValorCartaAcordoMensal
        , S.ValorOutrasComissoes
        , S.FaturamentoLiquidoMensal
        , S.PercentualComissaoVendedor
        , S.ValorVendedor
        , S.ValorVendedorTotal
        , S.PercentualComissaoCoordenacao
        , S.ValorCoordenador
        , S.ValorCoordenadorTotal
        , S.PercentualComissaoGerencia
        , S.ValorGerencia
        , S.ValorGerenciaTotal
        , S.AtivoCancelamento
        , S.FaturamentoLiquidoFinalMensal
        , S.ComissaoGerenciaNordeste
        , S.Faturamento
        , S.DataCancelamento
        , S.OBS
    );
"""

UPDATE_FK_SQL = r"""
UPDATE ftci
SET
    ftci.IDFatoControleContratoEuromidia = ftc.IDFatoControleContratosEuromidia
FROM [Silver].[FatoControleContratosItensEuromidia] AS ftci
INNER JOIN [Silver].[FatoControleContratosEuromidia] AS ftc
    ON  ftc.NumeroContrato = ftci.NumeroContrato
    AND ftc.NumeroPrevia   = ftci.NumeroPrevia
WHERE
    ftci.IDFatoControleContratoEuromidia IS NULL
    OR ftci.IDFatoControleContratoEuromidia <> ftc.IDFatoControleContratosEuromidia;
"""

UPDATE_VENDEDOR_SQL = r"""
UPDATE fctti
SET
    fctti.IDVendedor = vdd.IDVendedor
FROM [Silver].[FatoControleContratosItensEuromidia] AS fctti
INNER JOIN [Integracao].[dbo].[Vendedores] AS vdd
    ON vdd.NomeVendedor = fctti.Vendedor
WHERE
    fctti.Vendedor IS NOT NULL
    AND LTRIM(RTRIM(fctti.Vendedor)) <> ''
    AND (
        fctti.IDVendedor IS NULL
        OR fctti.IDVendedor <> vdd.IDVendedor
    );
"""

UPDATE_EMPRESA_SQL = r"""
UPDATE fct
SET
    fct.IDEmpresa = emp.IDEmpresa
FROM [Silver].[FatoControleContratosEuromidia] AS fct
INNER JOIN [Integracao].[Silver].[DimEmpresas] AS emp
    ON emp.CNPJ = fct.CNPJ;
"""

UPDATE_PONTOS_SQL = r"""
UPDATE fcti
SET
      fcti.IDPainelEuromidia = dpie.IDDimPaineisEuromidia
    , fcti.DataAtualizacao = GETDATE()
FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS fcti
INNER JOIN [Integracao].[Silver].[DimPaineisEuromidia] AS dpie
    ON TRY_CONVERT(int, dpie.CodPonto) = TRY_CONVERT(int, fcti.CodPonto)
WHERE
    fcti.CodPonto IS NOT NULL
    AND (
        fcti.IDPainelEuromidia IS NULL
        OR fcti.IDPainelEuromidia <> dpie.IDDimPaineisEuromidia
    );
"""

UPDATE_FACES_SQL = r"""
UPDATE fcti
SET
      fcti.IDDimFacesPaineis = dpe.IDDimFacesPaineis
    , fcti.IDPainelEuromidia = dpe.IDDimPaineisEuromidia
    , fcti.DataAtualizacao = GETDATE()
FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS fcti
INNER JOIN [Integracao].[Silver].[DimFacesPaineis] AS dpe
    ON TRY_CONVERT(int, dpe.CodPonto) = TRY_CONVERT(int, fcti.CodPonto)
   AND UPPER(LTRIM(RTRIM(dpe.CodFace))) = UPPER(LTRIM(RTRIM(fcti.CodFace)))
WHERE
    fcti.CodPonto IS NOT NULL
    AND NULLIF(LTRIM(RTRIM(fcti.CodFace)), '') IS NOT NULL
    AND (
        fcti.IDDimFacesPaineis IS NULL
        OR fcti.IDDimFacesPaineis <> dpe.IDDimFacesPaineis
        OR fcti.IDPainelEuromidia IS NULL
        OR fcti.IDPainelEuromidia <> dpe.IDDimPaineisEuromidia
    );
"""

CALL_PROCEDURE_SQL = r"""
EXEC Silver.sp_UpsertDimCalendario;
"""

UPDATE_OCUPACAO_SQL = r"""
SET NOCOUNT ON;

;WITH FaceResolvida AS (
    SELECT
          CodPonto = TRY_CONVERT(int, CodPonto)
        , CodFaceNormalizado = UPPER(LTRIM(RTRIM(CodFace)))
        , IDDimFacesPaineis = MAX(IDDimFacesPaineis)
        , IDDimPaineisEuromidia = MAX(IDDimPaineisEuromidia)
    FROM [Integracao].[Silver].[DimFacesPaineis]
    WHERE
        TRY_CONVERT(int, CodPonto) IS NOT NULL
        AND NULLIF(LTRIM(RTRIM(CodFace)), '') IS NOT NULL
    GROUP BY
          TRY_CONVERT(int, CodPonto)
        , UPPER(LTRIM(RTRIM(CodFace)))
),
FonteBase AS (
    SELECT
        DataAtualizacao = CAST(GETDATE() AS datetime2(0)),
        Referencia = i.Referencia,
        CodPonto = i.CodPonto,
        CodFace = LEFT(LTRIM(RTRIM(i.CodFace)), 100),
        IDPainelEuromidia = COALESCE(i.IDPainelEuromidia, fr.IDDimPaineisEuromidia),
        Origem = CAST('CONTRATO' AS varchar(20)),
        Status = CAST(
            CASE
                WHEN i.DataCancelamento IS NULL THEN 'ATIVO'
                ELSE 'CANCELADO'
            END AS varchar(20)
        ),
        DataInicio = i.DataInicioPrevisto,
        DataFim = COALESCE(i.DataCancelamento, i.DataTerminoPrevisto),
        LoopInicio = CAST(NULL AS int),
        LoopFim = CAST(NULL AS int),
        SpanQtd = CAST(NULL AS int),
        Cota = TRY_CONVERT(int, i.Cota),
        MarcaExibida = LEFT(i.MarcaExibida, 200),
        Vendedor = LEFT(i.Vendedor, 200),
        IDVendedor = i.IDVendedor,
        IDCliente = c.IDEmpresa,
        IDFatoControleContratos = i.IDFatoControleContratoEuromidia,
        NumeroContrato = LEFT(i.NumeroContrato, 150),
        NumeroPrevia = LEFT(i.NumeroPrevia, 150),
        TextoOriginal = CONCAT(
            'CONTRATO:', COALESCE(i.NumeroContrato,''),
            ' | PRÉVIA:', COALESCE(i.NumeroPrevia,''),
            ' | PONTO:', COALESCE(CONVERT(varchar(30), i.CodPonto), ''),
            ' | FACE:', COALESCE(i.CodFace, '')
        ),
        CriadoEm = CAST(COALESCE(i.DataLancamento, CAST(i.DataAtualizacao AS date), GETDATE()) AS datetime2(0)),
        CriadoPorIDUsuario = ISNULL(i.IDVendedor, 0),
        ExpiraEm = CAST(NULL AS datetime2(0)),
        CanceladoEm = CASE
                        WHEN i.DataCancelamento IS NULL THEN NULL
                        ELSE CAST(GETDATE() AS datetime2(0))
                      END,
        CanceladoPorIDUsuario = CAST(NULL AS int),
        Observacao = LEFT(i.OBS, 500),
        Dias = CASE
                 WHEN i.DataInicioPrevisto IS NULL THEN NULL
                 WHEN COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) IS NULL THEN NULL
                 WHEN COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) < i.DataInicioPrevisto THEN NULL
                 ELSE DATEDIFF(day, i.DataInicioPrevisto, COALESCE(i.DataCancelamento, i.DataTerminoPrevisto)) + 1
               END,
        rn = ROW_NUMBER() OVER (
                PARTITION BY i.Referencia
                ORDER BY i.DataAtualizacao DESC, i.IDFatoControleContratosItensEuromidia DESC
             )
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS i
    LEFT JOIN [Integracao].[Silver].[FatoControleContratosEuromidia] AS c
        ON c.IDFatoControleContratosEuromidia = i.IDFatoControleContratoEuromidia
    LEFT JOIN FaceResolvida AS fr
        ON fr.CodPonto = TRY_CONVERT(int, i.CodPonto)
       AND fr.CodFaceNormalizado = UPPER(LTRIM(RTRIM(i.CodFace)))
    WHERE
        i.Referencia IS NOT NULL
        AND LTRIM(RTRIM(i.Referencia)) <> ''
        AND i.CodPonto IS NOT NULL
        AND NULLIF(LTRIM(RTRIM(i.CodFace)), '') IS NOT NULL
        AND i.DataInicioPrevisto IS NOT NULL
        AND COALESCE(i.DataCancelamento, i.DataTerminoPrevisto) IS NOT NULL
),
FonteFinal AS (
    SELECT
        DataAtualizacao,
        Referencia,
        CodPonto,
        CodFace,
        IDPainelEuromidia,
        Origem,
        Status,
        DataInicio,
        DataFim,
        LoopInicio,
        LoopFim,
        SpanQtd,
        Cota,
        MarcaExibida,
        Vendedor,
        IDVendedor,
        IDCliente,
        IDFatoControleContratos,
        NumeroContrato,
        NumeroPrevia,
        TextoOriginal,
        CriadoEm,
        CriadoPorIDUsuario,
        ExpiraEm,
        CanceladoEm,
        CanceladoPorIDUsuario,
        Observacao,
        Dias
    FROM FonteBase
    WHERE rn = 1
)

MERGE [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS T
USING FonteFinal AS S
    ON  T.Referencia = S.Referencia
    AND T.Origem = S.Origem

WHEN MATCHED THEN
    UPDATE SET
        T.DataAtualizacao = S.DataAtualizacao,
        T.CodPonto = S.CodPonto,
        T.CodFace = S.CodFace,
        T.IDPainelEuromidia = S.IDPainelEuromidia,
        T.Status = S.Status,
        T.DataInicio = S.DataInicio,
        T.DataFim = S.DataFim,
        T.LoopInicio = S.LoopInicio,
        T.LoopFim = S.LoopFim,
        T.SpanQtd = S.SpanQtd,
        T.Cota = S.Cota,
        T.MarcaExibida = S.MarcaExibida,
        T.Vendedor = S.Vendedor,
        T.IDVendedor = S.IDVendedor,
        T.IDCliente = S.IDCliente,
        T.IDFatoControleContratos = S.IDFatoControleContratos,
        T.NumeroContrato = S.NumeroContrato,
        T.NumeroPrevia = S.NumeroPrevia,
        T.TextoOriginal = S.TextoOriginal,
        T.ExpiraEm = S.ExpiraEm,
        T.CanceladoEm = S.CanceladoEm,
        T.CanceladoPorIDUsuario = S.CanceladoPorIDUsuario,
        T.Observacao = S.Observacao,
        T.Dias = S.Dias

WHEN NOT MATCHED BY TARGET THEN
    INSERT (
        DataAtualizacao,
        Referencia,
        CodPonto,
        CodFace,
        IDPainelEuromidia,
        Origem,
        Status,
        DataInicio,
        DataFim,
        LoopInicio,
        LoopFim,
        SpanQtd,
        Cota,
        MarcaExibida,
        Vendedor,
        IDVendedor,
        IDCliente,
        IDFatoControleContratos,
        NumeroContrato,
        NumeroPrevia,
        TextoOriginal,
        CriadoEm,
        CriadoPorIDUsuario,
        ExpiraEm,
        CanceladoEm,
        CanceladoPorIDUsuario,
        Observacao,
        Dias
    )
    VALUES (
        S.DataAtualizacao,
        S.Referencia,
        S.CodPonto,
        S.CodFace,
        S.IDPainelEuromidia,
        S.Origem,
        S.Status,
        S.DataInicio,
        S.DataFim,
        S.LoopInicio,
        S.LoopFim,
        S.SpanQtd,
        S.Cota,
        S.MarcaExibida,
        S.Vendedor,
        S.IDVendedor,
        S.IDCliente,
        S.IDFatoControleContratos,
        S.NumeroContrato,
        S.NumeroPrevia,
        S.TextoOriginal,
        S.CriadoEm,
        S.CriadoPorIDUsuario,
        S.ExpiraEm,
        S.CanceladoEm,
        S.CanceladoPorIDUsuario,
        S.Observacao,
        S.Dias
    )

WHEN NOT MATCHED BY SOURCE
     AND T.Origem = 'CONTRATO'
     AND ISNULL(T.Status, '') <> 'CANCELADO'
THEN UPDATE SET
    T.DataAtualizacao = CAST(GETDATE() AS datetime2(0)),
    T.Status = 'CANCELADO',
    T.CanceladoEm = COALESCE(T.CanceladoEm, CAST(GETDATE() AS datetime2(0))),
    T.Observacao = LEFT(
        CONCAT(
            COALESCE(T.Observacao, ''),
            CASE WHEN NULLIF(COALESCE(T.Observacao, ''), '') IS NULL THEN '' ELSE ' | ' END,
            'Cancelado automaticamente pelo pipeline: item não encontrado na fonte atual do controle de contratos.'
        ),
        500
    );
"""


def normalizar_valor_json(valor: Any) -> Any:
    """Converte valores comuns do Airflow para JSON/log sem quebrar a task."""
    if valor is None:
        return None

    if isinstance(valor, (datetime, date)):
        return valor.isoformat()

    try:
        if hasattr(valor, "isoformat"):
            return valor.isoformat()
    except Exception:
        pass

    if isinstance(valor, dict):
        return {str(chave): normalizar_valor_json(valor_item) for chave, valor_item in valor.items()}

    if isinstance(valor, (list, tuple, set)):
        return [normalizar_valor_json(item) for item in valor]

    try:
        json.dumps(valor)
        return valor
    except Exception:
        return str(valor)


def obter_contexto_disparo_dag() -> dict[str, Any]:
    """Lê o contexto do DagRun, incluindo disparos manuais pela API REST do Airflow.

    O DAG continua funcionando normalmente pelo agendamento. Quando for disparado via API,
    o payload enviado no campo conf ficará disponível aqui para logs e auditoria.
    """
    try:
        contexto = get_current_context()
    except Exception as erro:
        return {
            "contexto_disponivel": False,
            "erro_contexto": repr(erro),
        }

    dag_run = contexto.get("dag_run")
    task_instance = contexto.get("ti") or contexto.get("task_instance")

    conf = {}
    if dag_run is not None:
        conf_original = getattr(dag_run, "conf", None)
        if isinstance(conf_original, dict):
            conf = normalizar_valor_json(conf_original)

    run_id = getattr(dag_run, "run_id", None) if dag_run is not None else None
    run_type = getattr(dag_run, "run_type", None) if dag_run is not None else None
    logical_date = getattr(dag_run, "logical_date", None) if dag_run is not None else None
    data_interval_start = contexto.get("data_interval_start")
    data_interval_end = contexto.get("data_interval_end")

    origem_conf = str(conf.get("origem_disparo") or conf.get("origem") or "").strip().lower()
    disparado_por_endpoint = origem_conf in {
        "endpoint",
        "endpoint_flask",
        "flask",
        "api",
        "api_airflow",
        "sistema_flask",
    }

    run_type_texto = str(run_type) if run_type is not None else None
    disparo_manual_ou_api = bool(
        disparado_por_endpoint
        or (run_id and str(run_id).startswith("manual__"))
        or (run_type_texto and "manual" in run_type_texto.lower())
    )

    return {
        "contexto_disponivel": True,
        "dag_id": DAG_ID,
        "run_id": run_id,
        "run_type": run_type_texto,
        "logical_date": normalizar_valor_json(logical_date),
        "data_interval_start": normalizar_valor_json(data_interval_start),
        "data_interval_end": normalizar_valor_json(data_interval_end),
        "task_id_atual": getattr(task_instance, "task_id", None) if task_instance is not None else None,
        "try_number_atual": getattr(task_instance, "try_number", None) if task_instance is not None else None,
        "disparo_manual_ou_api": disparo_manual_ou_api,
        "disparado_por_endpoint": disparado_por_endpoint,
        "origem_disparo_conf": origem_conf or None,
        "conf": conf,
    }


def registrar_log_contexto_disparo(nome_etapa: str) -> dict[str, Any]:
    """Registra no log a origem da execução atual do DAG."""
    contexto_disparo = obter_contexto_disparo_dag()
    logger.info(
        "%s | CONTEXTO DISPARO DAG: %s",
        nome_etapa,
        json.dumps(contexto_disparo, ensure_ascii=False, default=str),
    )
    return contexto_disparo


def obter_config_api_airflow_sem_segredos() -> dict[str, Any]:
    """Retorna apenas configurações não sensíveis relacionadas ao disparo via API.

    Não registra usuário nem senha da API nos logs. Essas credenciais devem ser usadas
    somente pelo sistema externo que chama o Airflow, por exemplo o Flask.
    """
    return {
        "dag_id_ativo": DAG_ID,
        "dag_id_padrao": DAG_ID_PADRAO,
        "airflow_dag_controle_contratos_env": os.getenv("AIRFLOW_DAG_CONTROLE_CONTRATOS"),
        "trigger_controle_contratos_habilitado_env": AIRFLOW_TRIGGER_CONTROLE_CONTRATOS_HABILITADO,
        "airflow_api_base_url_configurada": bool(AIRFLOW_API_BASE_URL_CONFIGURADA),
        "airflow_api_timeout_segundos_configurado": AIRFLOW_API_TIMEOUT_SEGUNDOS_CONFIGURADO or None,
    }


def executar_sql_auditado(
    nome_amigavel: str,
    descricao_etapa: str,
    sql_execucao: str,
    sql_amostra: str | None = None,
    destino_dados: str | None = None,
    origem_dados: str | None = None,
    ignorar_erro_permissao_execute: bool = False,
) -> dict[str, Any]:
    """Executa SQL com auditoria estruturada e amostra opcional."""
    resumo = criar_resumo_auditoria(
        nome_amigavel=nome_amigavel,
        descricao_etapa=descricao_etapa,
        origem_dados=origem_dados,
        destino_dados=destino_dados,
    )

    engine = obter_engine_sql_server()

    try:
        resumo.status = "RUNNING"
        resumo.metricas_extras["conn_id_sql_server"] = CONN_ID_SQL_SERVER
        resumo.metricas_extras["possui_sql_amostra"] = bool(sql_amostra)
        publicar_resumo_auditoria(resumo)

        logger.info("=" * 100)
        logger.info("INÍCIO ETAPA SQL | %s", nome_amigavel)
        logger.info("Descrição: %s", descricao_etapa)
        logger.info("Origem: %s | Destino: %s", origem_dados, destino_dados)
        logger.info("SQL execução - prévia normalizada: %s", " ".join(sql_execucao.split())[:1500])

        with engine.begin() as conn:
            conn.exec_driver_sql(sql_execucao)

        logger.info("SQL principal executado com sucesso | etapa=%s", nome_amigavel)

        resumo.status = "SUCCESS"
        adicionar_validacao(
            resumo,
            nome="sql_executado",
            status="ok",
            detalhe=f"A etapa SQL '{nome_amigavel}' foi executada com sucesso.",
        )

        amostra = []
        if sql_amostra:
            logger.info("SQL amostra - prévia normalizada | etapa=%s | sql=%s", nome_amigavel, " ".join(sql_amostra.split())[:1500])
            amostra = consultar_amostra_sql(engine=engine, sql=sql_amostra, limite=5)
            if amostra:
                definir_amostra(resumo, amostra, limite=10)
                logger.info(
                    "Amostra SQL | etapa=%s | linhas=%s:\n%s",
                    nome_amigavel,
                    len(amostra),
                    pd.DataFrame(amostra).to_string(index=False),
                )
            else:
                logger.warning("Amostra SQL vazia | etapa=%s", nome_amigavel)

        logger.info("FIM ETAPA SQL | %s | status=%s", nome_amigavel, resumo.status)
        logger.info("=" * 100)
        publicar_resumo_auditoria(resumo)

        return {
            "etapa": nome_amigavel,
            "amostra": amostra,
        }
    except Exception as erro:
        mensagem_erro = str(erro)

        erro_permissao_execute = (
            "EXECUTE permission was denied" in mensagem_erro
            or "A permissão EXECUTE foi negada" in mensagem_erro
            or "permissão EXECUTE foi negada" in mensagem_erro
        )

        if ignorar_erro_permissao_execute and erro_permissao_execute:
            resumo.status = "SKIPPED"
            registrar_erro_no_resumo(resumo, erro)
            adicionar_validacao(
                resumo,
                nome="sql_nao_executado_por_permissao",
                status="warning",
                detalhe=(
                    "A etapa SQL foi ignorada porque o usuário da conexão do Airflow não possui "
                    "permissão EXECUTE no objeto SQL chamado. O pipeline seguirá para as próximas "
                    "etapas para não bloquear a carga principal."
                ),
            )
            adicionar_observacao(
                resumo,
                "Permissão EXECUTE ausente. Corrija no SQL Server com GRANT EXECUTE no objeto "
                "necessário ou mantenha esta etapa como opcional no DAG.",
            )
            logger.warning(
                "ETAPA SQL IGNORADA POR FALTA DE PERMISSÃO EXECUTE | etapa=%s | erro=%r",
                nome_amigavel,
                erro,
            )
            publicar_resumo_auditoria(resumo)
            return {
                "etapa": nome_amigavel,
                "status": "SKIPPED_SEM_PERMISSAO_EXECUTE",
                "erro": mensagem_erro,
            }

        resumo.status = "FAILED"
        registrar_erro_no_resumo(resumo, erro)
        publicar_resumo_auditoria(resumo)
        raise
    finally:
        engine.dispose()


@dag(
    dag_id=DAG_ID,
    schedule=CRON_AGENDAMENTO,
    start_date=pendulum.datetime(2026, 3, 19, 0, 0, tz=FUSO_HORARIO),
    catchup=False,
    max_active_runs=1,
    tags=["Euromidia", "ETL", "Controle de Contratos"],
    description=(
        "Pipeline ETL do controle de contratos da Euromídia. Baixa via rclone a versão mais recente do "
        "arquivo oficial no SharePoint, lê a aba CTR no container, padroniza datas, números, percentuais, CNPJ e textos, gera identificadores "
        "hash para contratos e prévias ausentes, produz um CSV técnico, recarrega a stage SQL Server via "
        "insert em lote, executa a cadeia de consolidação nas tabelas Silver e ocupação, e ao final dispara "
        "a DAG de prioridade de reservas para criar reservas elegíveis sem duplicidade."
    ),
    doc_md=r"""
# ETL CTR - Controle de Contratos Euromídia

## Visão geral

Este DAG implementa o fluxo de ingestão e consolidação do arquivo de **controle de contratos CTR** da Euromídia.

Ele foi desenhado para resolver um problema operacional muito comum em ambientes Docker + Airflow + Windows/WSL:
a fragilidade de cargas baseadas em filesystem externo e permissões inconsistentes de bind mount.

Por isso, o fluxo foi estruturado para:

1. baixar do SharePoint, via rclone, a versão oficial mais recente do arquivo Excel;
2. substituir a cópia local dentro do container no caminho estável do Airflow;
3. ler o Excel diretamente no container;
4. tratar e normalizar os dados com pandas/openpyxl;
5. gerar um CSV técnico em uma pasta estável do container;
6. recarregar a tabela stage por insert em lote via SQLAlchemy;
7. consolidar os fatos e relacionamentos nas tabelas Silver;
8. atualizar ocupação, dimensões auxiliares e chaves de relacionamento.

---

## Objetivo de negócio

O pipeline existe para transformar uma planilha operacional de contratos em uma base estruturada para:

- consolidação de contratos;
- detalhamento por item/face/ponto;
- relacionamento com vendedor, cliente, painel e face;
- visão de ocupação dos painéis;
- apoio a análises comerciais, faturamento, ocupação e performance operacional.

---

## Origem dos dados

Arquivo Excel montado no container:

`/root/PythonJobs/pipelines/airflow/Dados/SharePoint/BaseDados/Base Dados - 01- Controle de Contratos/Copia-Controle de Contratos Euromidia.xlsm`

Aba lida:

`CTR`

---

## Destino intermediário e final

### Stage
- `dbo.df_fatocontrolecontratos`

### Silver
- `Silver.FatoControleContratosEuromidia`
- `Silver.FatoControleContratosItensEuromidia`
- `Silver.FatoOcupacaoPaineisEuromidia`

### Procedimentos e atualizações auxiliares
- `Silver.sp_UpsertDimCalendario`
- relacionamento com:
  - `DimEmpresas`
  - `DimPaineisEuromidia`
  - `DimFacesPaineis`
  - `Vendedores`

---

## Frequência de execução

O DAG executa:

- todos os dias
- às 08:00
- às 11:00
- às 15:00
- às 18:00

Cron:

`0 8,11,15,18 * * *`

---

## Motivação técnica da arquitetura

### Problema original
O modelo tradicional usando `BULK INSERT` em arquivo hospedado em bind mount do Windows é frágil em ambientes:

- Docker Desktop
- WSL
- Airflow
- permissões heterogêneas entre host e container

Isso costuma causar:
- erro de permissão
- arquivo visível no host mas inacessível ao SQL Server
- inconsistência de path
- falha intermitente entre ambientes

### Solução aplicada
O DAG foi estruturado para:
- processar o Excel no container
- escrever o CSV técnico em uma pasta interna estável:
  `/opt/airflow/Artefatos/CargasSQL/CTR`
- carregar a stage via `to_sql`/insert em lote
- remover a dependência crítica de `BULK INSERT` em caminho externo

Essa abordagem é mais robusta, mais portátil e mais compatível com execução containerizada.

---

## Etapas detalhadas do processo

### 1. Leitura da planilha CTR
A aba `CTR` é lida com `pandas.read_excel`, usando `openpyxl`, evitando o uso de engine nativa `calamine`/`polars` no parse do DAG.

### 2. Padronização dos dados
São tratados:
- datas em formatos brasileiros, ISO, datetime e serial Excel;
- inteiros;
- percentuais;
- valores monetários;
- CNPJ;
- textos com caracteres invisíveis.

### 3. Renomeação das colunas
As colunas de origem da planilha são renomeadas para nomes técnicos padronizados do pipeline.

### 4. Geração de hashes
Quando `NumeroContrato` ou `NumeroPrevia` estiverem vazios, são gerados hashes determinísticos baseados em assinatura de negócio.

Isso reduz perda de rastreabilidade e melhora a estabilidade do relacionamento entre registros.

### 5. Geração do CSV técnico
O CSV é gravado em pasta estável do container, com:
- `;` como separador
- vírgula decimal
- `utf-8-sig`
- formato de data `YYYY-MM-DD`

### 6. Recarga da stage
O CSV é relido e enviado para a stage:
- `dbo.df_fatocontrolecontratos`

A carga usa insert em lote via SQLAlchemy.

### 7. Consolidação do contrato
A tabela `Silver.FatoControleContratosEuromidia` recebe uma visão agregada por referência contratual.

### 8. Consolidação dos itens
A tabela `Silver.FatoControleContratosItensEuromidia` recebe a granularidade detalhada por item.

### 9. Atualização de vínculos
São atualizadas:
- FK de itens para contratos
- ID do vendedor
- ID da empresa
- ID do painel
- ID da face

### 10. Calendário, ocupação e prioridade de reservas
Por fim:
- atualiza dimensão calendário;
- executa MERGE de ocupação em `FatoOcupacaoPaineisEuromidia`;
- dispara a DAG `pipeline_prioridade_reservas` em modo pós-upsert.

Esse disparo é necessário porque o MERGE de ocupação apenas atualiza/cria ocupações contratuais.
Quem cria a reserva automática de preferência de renovação e recalcula `ReservaOrdemPrioridade`
é a DAG de prioridade de reservas.

O disparo envia o `conf`:

```json
{
  "origem": "pipeline_controle_contratos_euromidia",
  "modo_execucao": "VARREDURA_POS_UPSERT_OCUPACAO",
  "processar_todos_elegiveis": true,
  "tabela_origem": "[Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]"
}
```

---

## Atualização obrigatória do SharePoint

Antes de qualquer carga, a task `baixar_arquivo_sharepoint` executa `rclone copyto` com `--ignore-times` para forçar a cópia da fonte oficial do SharePoint para o caminho local do container.

Destino local padrão:
`/root/PythonJobs/pipelines/airflow/Dados/SharePoint/BaseDados/Base Dados - 01- Controle de Contratos/Copia-Controle de Contratos Euromidia.xlsm`

Isso garante que a versão local usada pela DAG seja substituída pela versão oficial mais recente do SharePoint em toda execução.

---

## Observabilidade e auditoria

Cada task publica auditoria estruturada contendo:
- nome amigável
- descrição da etapa
- origem e destino
- validações executadas
- observações técnicas
- amostras reais dos dados

Isso foi feito para que a execução fique legível no painel e não dependa só de log bruto.

---

## Conexões esperadas

### SQL Server
- `mssql_integracao`

---

## Premissas operacionais

- O `rclone` precisa estar instalado dentro do container que executa a task.
- O remote `sharepoint_basedados:` precisa estar configurado e acessível no container.
- A DAG baixa o arquivo oficial do SharePoint antes de ler a aba `CTR`.
- A aba `CTR` precisa existir.
- A stage deve existir previamente no banco.
- As tabelas Silver e dimensões relacionadas devem existir.
- O Airflow precisa ter acesso à connection `mssql_integracao`.

---

## Benefícios da abordagem atual

- menor dependência de filesystem externo
- maior estabilidade em ambiente Docker
- maior rastreabilidade
- tratamento explícito de formatos mistos
- melhor observabilidade por etapa
- redução de falhas silenciosas em planilhas operacionais

---
""",
)
def pipeline_controle_contratos_euromidia():
    @task(task_id="registrar_contexto_disparo")
    def registrar_contexto_disparo() -> dict[str, Any]:
        """Registra se a execução veio do agendamento, UI ou API REST do Airflow."""
        contexto_disparo = registrar_log_contexto_disparo("registrar_contexto_disparo")

        resumo = criar_resumo_auditoria(
            nome_amigavel="Registrar contexto de disparo do DAG",
            descricao_etapa=(
                "Registra metadados do DagRun para diferenciar execuções agendadas, manuais, "
                "via UI e via API REST do Airflow."
            ),
            origem_dados="Airflow DagRun context",
            destino_dados="Logs/Auditoria do pipeline",
        )
        resumo.status = "SUCCESS"
        resumo.metricas_extras["dag_id"] = DAG_ID
        resumo.metricas_extras["config_api_airflow_sem_segredos"] = obter_config_api_airflow_sem_segredos()
        resumo.metricas_extras["run_id"] = contexto_disparo.get("run_id")
        resumo.metricas_extras["run_type"] = contexto_disparo.get("run_type")
        resumo.metricas_extras["disparo_manual_ou_api"] = contexto_disparo.get("disparo_manual_ou_api")
        resumo.metricas_extras["disparado_por_endpoint"] = contexto_disparo.get("disparado_por_endpoint")
        resumo.metricas_extras["origem_disparo_conf"] = contexto_disparo.get("origem_disparo_conf")
        resumo.metricas_extras["conf"] = contexto_disparo.get("conf")
        adicionar_validacao(
            resumo,
            nome="contexto_disparo_registrado",
            status="ok",
            detalhe="Contexto do DagRun registrado com sucesso.",
        )
        publicar_resumo_auditoria(resumo)

        return contexto_disparo

    @task(task_id="baixar_arquivo_sharepoint")
    def baixar_arquivo_sharepoint() -> dict[str, Any]:
        """Baixa sempre a versão oficial do SharePoint e substitui a cópia local antes do ETL."""
        pasta_destino = resolver_pasta_gravavel(
            CAMINHO_ARQUIVO_EXCEL.parent,
            PASTAS_FALLBACK_SHAREPOINT,
            "download_sharepoint_controle_contratos",
        )
        caminho_destino = pasta_destino / NOME_ARQUIVO_EXCEL_CONTROLE_CONTRATOS
        caminho_temporario = caminho_destino.with_name(caminho_destino.name + ".download.tmp")

        resumo = criar_resumo_auditoria(
            nome_amigavel="Baixar arquivo atualizado do SharePoint",
            descricao_etapa=(
                "Valida o rclone.conf, consulta o arquivo oficial no SharePoint, baixa sempre a versão "
                "mais recente via rclone copyto --ignore-times, valida o arquivo temporário e substitui "
                "a cópia local usada pelo ETL."
            ),
            origem_dados=RCLONE_ARQUIVO_ORIGEM,
            destino_dados=str(caminho_destino),
        )

        try:
            resumo.status = "RUNNING"
            resumo.metricas_extras["remote_rclone"] = RCLONE_REMOTE_CONTROLE_CONTRATOS
            resumo.metricas_extras["pasta_rclone"] = RCLONE_PASTA_CONTROLE_CONTRATOS
            resumo.metricas_extras["arquivo_rclone_origem"] = RCLONE_ARQUIVO_ORIGEM
            resumo.metricas_extras["pasta_destino_container"] = str(pasta_destino)
            resumo.metricas_extras["arquivo_destino_container"] = str(caminho_destino)
            resumo.metricas_extras["usuario_execucao_container"] = obter_contexto_usuario_execucao()
            resumo.metricas_extras["contexto_disparo_dag"] = registrar_log_contexto_disparo("baixar_arquivo_sharepoint")
            publicar_resumo_auditoria(resumo)

            logger.info("=" * 100)
            logger.info("INÍCIO DOWNLOAD SHAREPOINT VIA RCLONE")
            logger.info("Origem oficial SharePoint/rclone: %s", RCLONE_ARQUIVO_ORIGEM)
            logger.info("Destino local no container: %s", caminho_destino)
            logger.info("Pasta destino local no container: %s", pasta_destino)
            logger.info("Usuário/container: %s", json.dumps(obter_contexto_usuario_execucao(), ensure_ascii=False, default=str))

            info_antes = obter_info_arquivo_local(caminho_destino)
            logger.info("Estado local antes do download: %s", json.dumps(info_antes, ensure_ascii=False, default=str))

            if caminho_temporario.exists():
                logger.warning("Arquivo temporário antigo encontrado e removido: %s", caminho_temporario)
                caminho_temporario.unlink()

            executar_comando_sistema(["rclone", "version"], "rclone_version")

            env_rclone, caminho_rclone_config = montar_ambiente_rclone()
            resumo.metricas_extras["rclone_config_utilizado"] = str(caminho_rclone_config)
            logger.info("RCLONE_CONFIG efetivo usado pela DAG: %s", caminho_rclone_config)

            info_remote = obter_info_remote_rclone(env_rclone, caminho_rclone_config)
            resumo.metricas_extras["arquivo_remote_sharepoint"] = info_remote
            logger.info(
                "Metadados do arquivo remoto SharePoint/rclone: %s",
                json.dumps(info_remote, ensure_ascii=False, default=str),
            )

            comando_download = montar_comando_rclone(
                [
                    "copyto",
                    RCLONE_ARQUIVO_ORIGEM,
                    str(caminho_temporario),
                    "--ignore-times",
                    "--log-level",
                    "INFO",
                    "--stats",
                    "10s",
                    "--retries",
                    "3",
                    "--low-level-retries",
                    "10",
                ],
                caminho_rclone_config,
            )
            executar_comando_sistema(
                comando_download,
                "rclone_copyto_sharepoint_para_tmp",
                env=env_rclone,
            )

            info_temporario = obter_info_arquivo_local(caminho_temporario)
            logger.info("Arquivo temporário baixado: %s", json.dumps(info_temporario, ensure_ascii=False, default=str))

            if not caminho_temporario.exists():
                raise FileNotFoundError(f"O rclone terminou sem erro, mas o arquivo temporário não foi encontrado: {caminho_temporario}")

            if caminho_temporario.stat().st_size <= 0:
                raise ValueError(f"O arquivo temporário baixado está vazio: {caminho_temporario}")

            tamanho_remote = info_remote.get("Size")
            if tamanho_remote is not None:
                try:
                    tamanho_remote_int = int(tamanho_remote)
                    tamanho_tmp_int = int(caminho_temporario.stat().st_size)
                    if tamanho_remote_int > 0 and tamanho_tmp_int != tamanho_remote_int:
                        raise ValueError(
                            "O tamanho do arquivo baixado não bate com o tamanho informado pelo SharePoint/rclone. "
                            f"remote={tamanho_remote_int} bytes | tmp={tamanho_tmp_int} bytes"
                        )
                except ValueError:
                    raise
                except Exception as erro:
                    logger.warning("Não foi possível comparar tamanho remoto/local: %r", erro)

            sha256_tmp = calcular_sha256_arquivo(caminho_temporario)

            # Substituição atômica/local: a cópia oficial baixada vira a versão consumida pela DAG.
            caminho_temporario.replace(caminho_destino)
            info_depois = obter_info_arquivo_local(caminho_destino)

            if not caminho_destino.exists() or caminho_destino.stat().st_size <= 0:
                raise ValueError(f"Arquivo final inválido após substituição: {caminho_destino}")

            sha256_final = calcular_sha256_arquivo(caminho_destino)

            if sha256_final != sha256_tmp:
                raise ValueError(
                    "Hash divergente após substituição do arquivo local. "
                    f"tmp={sha256_tmp} | final={sha256_final}"
                )

            amostra = montar_amostra_arquivo_baixado(caminho_destino, info_antes, info_depois)

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = 1
            resumo.linhas_inseridas = 1
            resumo.metricas_extras["arquivo_local_antes"] = info_antes
            resumo.metricas_extras["arquivo_local_depois"] = info_depois
            resumo.metricas_extras["sha256_arquivo_final"] = sha256_final
            resumo.metricas_extras["substituiu_arquivo_existente"] = bool(info_antes.get("existe"))
            definir_amostra(resumo, amostra, limite=10)

            adicionar_validacao(
                resumo,
                nome="rclone_config_legivel",
                status="ok",
                detalhe=f"O rclone.conf legível usado foi: {caminho_rclone_config}.",
            )
            adicionar_validacao(
                resumo,
                nome="remote_sharepoint_consultado",
                status="ok",
                detalhe=(
                    "A DAG consultou os metadados do arquivo remoto antes de baixar. "
                    f"Metadados: {json.dumps(info_remote, ensure_ascii=False, default=str)}"
                ),
            )
            adicionar_validacao(
                resumo,
                nome="download_rclone_executado",
                status="ok",
                detalhe="O rclone copyto foi executado com --ignore-times, forçando baixar a fonte oficial do SharePoint.",
            )
            adicionar_validacao(
                resumo,
                nome="arquivo_local_substituido",
                status="ok",
                detalhe=f"O arquivo local usado pela DAG foi substituído em: {caminho_destino}.",
            )
            adicionar_validacao(
                resumo,
                nome="arquivo_final_valido",
                status="ok",
                detalhe=f"Arquivo final validado com {info_depois.get('tamanho_mb')} MB e SHA256 {sha256_final}.",
            )
            adicionar_observacao(
                resumo,
                "Esta etapa roda antes de qualquer leitura do Excel; se o download falhar, as etapas seguintes não leem arquivo antigo.",
            )

            logger.info("Estado local depois do download: %s", json.dumps(info_depois, ensure_ascii=False, default=str))
            logger.info("SHA256 arquivo final: %s", sha256_final)
            logger.info("Amostra do arquivo baixado:\n%s", pd.DataFrame(amostra).to_string(index=False))
            logger.info("FIM DOWNLOAD SHAREPOINT VIA RCLONE")
            logger.info("=" * 100)
            publicar_resumo_auditoria(resumo)

            return {
                "download_sharepoint_sucesso": True,
                "arquivo_origem_rclone": RCLONE_ARQUIVO_ORIGEM,
                "arquivo_remote_sharepoint": info_remote,
                "rclone_config_utilizado": str(caminho_rclone_config),
                "caminho_arquivo_local": str(caminho_destino),
                "pasta_destino_local": str(pasta_destino),
                "tamanho_mb": info_depois.get("tamanho_mb"),
                "modificado_em_local": info_depois.get("modificado_em_local"),
                "sha256_arquivo_final": sha256_final,
                "substituiu_arquivo_existente": bool(info_antes.get("existe")),
            }
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            logger.exception(
                "Falha ao baixar/substituir arquivo do SharePoint via rclone. "
                "A DAG vai falhar de propósito para não carregar arquivo local desatualizado."
            )
            raise
        finally:
            if caminho_temporario.exists():
                try:
                    caminho_temporario.unlink()
                except Exception:
                    logger.warning("Não foi possível remover arquivo temporário: %s", caminho_temporario, exc_info=True)

    @task(task_id="gerar_csv_ctr")
    def gerar_csv_ctr(info_download: dict[str, Any]) -> dict[str, Any]:
        resumo = criar_resumo_auditoria(
            nome_amigavel="Gerar CSV técnico do CTR",
            descricao_etapa=(
                "Lê a aba CTR do arquivo Excel, aplica normalização de datas, números, percentuais, "
                "CNPJ e textos, renomeia colunas, gera hashes para contrato/prévia ausentes e grava "
                "um CSV técnico em pasta estável do container."
            ),
            origem_dados=str(CAMINHO_ARQUIVO_EXCEL),
            destino_dados=str(PASTA_CARGA_CONTAINER),
        )

        try:
            resumo.status = "RUNNING"
            resumo.metricas_extras["nome_aba_excel"] = NOME_ABA_EXCEL
            resumo.metricas_extras["conn_id_sql_server"] = CONN_ID_SQL_SERVER
            resumo.metricas_extras["info_download_sharepoint"] = info_download
            publicar_resumo_auditoria(resumo)

            if not info_download or not info_download.get("download_sharepoint_sucesso"):
                raise RuntimeError(
                    "A geração do CSV foi bloqueada porque a etapa de download do SharePoint "
                    "não confirmou sucesso. Isso evita ler arquivo local antigo/desatualizado."
                )

            pasta_carga_efetiva = resolver_pasta_gravavel(
                PASTA_CARGA_CONTAINER,
                PASTAS_FALLBACK_CARGA,
                "gerar_csv_controle_contratos",
            )

            caminho_excel_encontrado = Path(
                info_download.get("caminho_arquivo_local") or localizar_caminho_excel_controle_contratos()
            )

            if not caminho_excel_encontrado.exists():
                raise FileNotFoundError(f"Arquivo informado pela etapa de download não existe: {caminho_excel_encontrado}")

            logger.info("PASTA_SHAREPOINT_CONTAINER: %s", PASTA_SHAREPOINT_CONTAINER)
            logger.info("CAMINHO_ARQUIVO_EXCEL_CONFIGURADO: %s", CAMINHO_ARQUIVO_EXCEL)
            logger.info("CAMINHO_EXCEL_ENCONTRADO: %s", caminho_excel_encontrado)
            logger.info("PASTA_CARGA_CONTAINER_CONFIGURADA: %s", PASTA_CARGA_CONTAINER)
            logger.info("PASTA_CARGA_EFETIVA: %s", pasta_carga_efetiva)
            logger.info("PASTA_CARGA_EFETIVA.exists(): %s", pasta_carga_efetiva.exists())
            logger.info("INFO_ARQUIVO_EXCEL_LOCAL: %s", json.dumps(obter_info_arquivo_local(caminho_excel_encontrado), ensure_ascii=False, default=str))

            df_original = ler_aba_ctr_xlsm_pandas(caminho_excel_encontrado)
            linhas_original = len(df_original)
            colunas_original = len(df_original.columns)

            logger.info(
                "Excel lido com sucesso. Arquivo: %s | Aba: %s | Linhas: %s | Colunas: %s",
                caminho_excel_encontrado,
                NOME_ABA_EXCEL,
                linhas_original,
                colunas_original,
            )
            logger.info("Colunas originais da aba CTR: %s", list(df_original.columns))
            logger.info("Amostra original da aba CTR antes do tratamento:\n%s", df_original.head(5).to_string(index=False))

            df = tratar_dataframe_ctr_pandas(df_original)

            agora = datetime.now()
            data_hora = agora.strftime("%Y%m%d_%H%M%S")
            nome_arquivo_csv = f"df_fatocontrolecontratos_{data_hora}.csv"

            caminho_saida_linux = pasta_carga_efetiva / nome_arquivo_csv
            caminho_saida_linux.parent.mkdir(parents=True, exist_ok=True)

            df.to_csv(
                caminho_saida_linux,
                sep=";",
                index=False,
                encoding="utf-8-sig",
                decimal=",",
                date_format="%Y-%m-%d",
                lineterminator="\n",
            )

            ultimo_csv = obter_ultimo_csv_carga(pasta_carga_efetiva)

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = int(len(df))
            resumo.linhas_inseridas = int(len(df))
            resumo.metricas_extras["arquivo_excel_encontrado"] = str(caminho_excel_encontrado)
            resumo.metricas_extras["nome_arquivo_csv"] = nome_arquivo_csv
            resumo.metricas_extras["caminho_csv_linux"] = str(caminho_saida_linux)
            resumo.metricas_extras["ultimo_csv_detectado"] = str(ultimo_csv)
            resumo.metricas_extras["colunas_exportadas"] = int(len(df.columns))

            adicionar_validacao(
                resumo,
                nome="arquivo_excel_baixado_do_sharepoint",
                status="ok",
                detalhe=f"A etapa anterior baixou/substituiu o arquivo oficial do SharePoint em {caminho_excel_encontrado}.",
            )
            adicionar_validacao(
                resumo,
                nome="arquivo_excel_disponivel",
                status="ok",
                detalhe=f"O arquivo Excel foi encontrado em {caminho_excel_encontrado}.",
            )
            adicionar_validacao(
                resumo,
                nome="csv_tecnico_gerado",
                status="ok",
                detalhe=f"O CSV técnico foi gerado com {len(df):,} linhas e {len(df.columns):,} colunas.",
            )
            adicionar_validacao(
                resumo,
                nome="ultimo_csv_identificado",
                status="ok",
                detalhe=f"O último CSV disponível para carga é {ultimo_csv}.",
            )
            adicionar_observacao(
                resumo,
                "A amostra mostra o dataset já padronizado e pronto para carga na stage técnica.",
            )

            definir_amostra(
                resumo,
                df_para_amostra(
                    df,
                    limite=5,
                    colunas=[
                        "DataLancamento",
                        "NumeroContrato",
                        "NumeroPrevia",
                        "CNPJ",
                        "MarcaExibida",
                        "CodPonto",
                        "CodFace",
                        "DataInicioPrevisto",
                        "DataTerminoPrevisto",
                        "FaturamentoBrutoMensal",
                        "OBS",
                    ],
                ),
                limite=10,
            )
            publicar_resumo_auditoria(resumo)

            logger.info("Arquivo Excel lido com sucesso: %s", caminho_excel_encontrado)
            logger.info("Linhas tratadas: %s", len(df))
            logger.info("Colunas exportadas: %s", len(df.columns))
            logger.info("CSV técnico gerado em: %s", caminho_saida_linux)
            logger.info("Último CSV detectado para carga: %s", ultimo_csv)
            logger.info("Amostra:\n%s", df.head(5).to_string(index=False))

            return {
                "nome_arquivo_csv": nome_arquivo_csv,
                "caminho_csv_linux": str(caminho_saida_linux),
                "ultimo_csv_linux": str(ultimo_csv),
                "pasta_carga": str(pasta_carga_efetiva),
                "pasta_carga_configurada": str(PASTA_CARGA_CONTAINER),
                "linhas": int(len(df)),
                "colunas": int(len(df.columns)),
                "info_download_sharepoint": info_download,
            }
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            raise

    @task(task_id="carregar_stage")
    def carregar_stage(info_csv: dict[str, Any]) -> dict[str, Any]:
        resumo = criar_resumo_auditoria(
            nome_amigavel="Carregar stage técnica",
            descricao_etapa=(
                "Relê o CSV técnico gerado, converte todos os campos para formato textual controlado "
                "e recarrega a tabela stage dbo.df_fatocontrolecontratos por insert em lote."
            ),
            origem_dados=info_csv["caminho_csv_linux"],
            destino_dados=TABELA_STAGE,
        )

        caminho_csv_linux = Path(info_csv["caminho_csv_linux"])

        try:
            resumo.status = "RUNNING"
            publicar_resumo_auditoria(resumo)

            pasta_carga_efetiva = Path(info_csv.get("pasta_carga") or PASTA_CARGA_CONTAINER)
            caminho_csv_linux = obter_ultimo_csv_carga(pasta_carga_efetiva)
            logger.info("PASTA_CARGA_CONFIGURADA: %s", PASTA_CARGA_CONTAINER)
            logger.info("PASTA_CARGA_EFETIVA_STAGE: %s", pasta_carga_efetiva)

            if not caminho_csv_linux.exists():
                raise FileNotFoundError(f"CSV não encontrado para carga da stage: {caminho_csv_linux}")

            df_stage = pd.read_csv(
                caminho_csv_linux,
                sep=";",
                encoding="utf-8-sig",
                dtype=str,
                keep_default_na=False,
            )

            df_stage = garantir_colunas_saida(df_stage)
            df_stage = df_stage.where(df_stage.ne(""), None)

            logger.info(
                "CSV carregado para stage. Arquivo: %s | Linhas: %s | Colunas: %s",
                caminho_csv_linux,
                len(df_stage),
                len(df_stage.columns),
            )
            logger.info("Amostra do CSV relido antes de carregar a stage:\n%s", df_stage.head(5).to_string(index=False))

            carregar_dataframe_stage_sql_server(df_stage, TABELA_STAGE)

            resumo.status = "SUCCESS"
            resumo.linhas_lidas = int(len(df_stage))
            resumo.linhas_inseridas = int(len(df_stage))
            resumo.metricas_extras["csv_recebido_da_task_anterior"] = info_csv.get("caminho_csv_linux")
            resumo.metricas_extras["ultimo_csv_usado_na_stage"] = str(caminho_csv_linux)

            adicionar_validacao(
                resumo,
                nome="ultimo_csv_identificado",
                status="ok",
                detalhe=f"A stage usou o último CSV gerado na pasta: {caminho_csv_linux}.",
            )
            adicionar_validacao(
                resumo,
                nome="csv_relido_com_sucesso",
                status="ok",
                detalhe=f"O CSV técnico foi relido com {len(df_stage):,} linhas.",
            )
            adicionar_validacao(
                resumo,
                nome="stage_recarregada",
                status="ok",
                detalhe=f"A tabela {TABELA_STAGE} foi criada/validada, limpa e recarregada com sucesso.",
            )
            adicionar_observacao(
                resumo,
                "A amostra representa exatamente o conteúdo enviado para a stage técnica antes dos MERGEs de consolidação.",
            )

            definir_amostra(
                resumo,
                df_para_amostra(
                    df_stage,
                    limite=5,
                    colunas=[
                        "DataLancamento",
                        "NumeroContrato",
                        "NumeroPrevia",
                        "CNPJ",
                        "MarcaExibida",
                        "CodPonto",
                        "CodFace",
                        "DataInicioPrevisto",
                        "DataTerminoPrevisto",
                    ],
                ),
                limite=10,
            )
            publicar_resumo_auditoria(resumo)

            return {
                "tabela_stage": TABELA_STAGE,
                "linhas_stage": int(len(df_stage)),
                "csv_usado": str(caminho_csv_linux),
            }
        except Exception as erro:
            resumo.status = "FAILED"
            registrar_erro_no_resumo(resumo, erro)
            publicar_resumo_auditoria(resumo)
            raise

    @task(task_id="merge_contratos")
    def merge_contratos() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="MERGE contratos consolidados",
            descricao_etapa=(
                "Consolida a visão agregada de contratos na tabela Silver.FatoControleContratosEuromidia, "
                "agrupando os registros por referência contratual e calculando totais, quantidades e "
                "atributos principais de negócio."
            ),
            sql_execucao=MERGE_CONTRATOS_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    NumeroContrato,
                    NumeroPrevia,
                    CNPJ,
                    RazaoSocial,
                    QuantidadePontos,
                    QuantidadeFaces,
                    TotalBrutoContrato,
                    TotalFaturamentoLiquidoMensal,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados=TABELA_STAGE,
            destino_dados="[Silver].[FatoControleContratosEuromidia]",
        )

    @task(task_id="merge_itens")
    def merge_itens() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="MERGE itens de contratos",
            descricao_etapa=(
                "Consolida a granularidade detalhada dos contratos por item, ponto e face, "
                "alimentando a tabela Silver.FatoControleContratosItensEuromidia."
            ),
            sql_execucao=MERGE_ITENS_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    NumeroContrato,
                    NumeroPrevia,
                    CNPJ,
                    CodPonto,
                    CodFace,
                    MarcaExibida,
                    DataInicioPrevisto,
                    DataTerminoPrevisto,
                    FaturamentoBrutoMensal,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosItensEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados=TABELA_STAGE,
            destino_dados="[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_fk_contratos_itens")
    def update_fk_contratos_itens() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar vínculo itens para contratos",
            descricao_etapa=(
                "Preenche a chave estrangeira dos itens para a tabela consolidada de contratos, "
                "garantindo integridade entre contrato agregado e seus itens detalhados."
            ),
            sql_execucao=UPDATE_FK_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    IDFatoControleContratoEuromidia,
                    NumeroContrato,
                    NumeroPrevia,
                    CodPonto,
                    CodFace,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosItensEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Silver].[FatoControleContratosEuromidia] + [Silver].[FatoControleContratosItensEuromidia]",
            destino_dados="[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_id_vendedor")
    def update_id_vendedor() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID do vendedor",
            descricao_etapa=(
                "Relaciona o nome do vendedor presente nos itens de contratos com a dimensão de vendedores, "
                "preenchendo o identificador técnico do vendedor."
            ),
            sql_execucao=UPDATE_VENDEDOR_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    Vendedor,
                    IDVendedor,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosItensEuromidia]
                WHERE IDVendedor IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Silver].[FatoControleContratosItensEuromidia] + [Integracao].[dbo].[Vendedores]",
            destino_dados="[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_id_empresa")
    def update_id_empresa() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID da empresa",
            descricao_etapa=(
                "Relaciona o CNPJ do contrato consolidado à dimensão de empresas para preenchimento do IDEmpresa."
            ),
            sql_execucao=UPDATE_EMPRESA_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    NumeroContrato,
                    NumeroPrevia,
                    CNPJ,
                    IDEmpresa,
                    DataAtualizacao
                FROM [Silver].[FatoControleContratosEuromidia]
                WHERE IDEmpresa IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Silver].[FatoControleContratosEuromidia] + [Integracao].[Silver].[DimEmpresas]",
            destino_dados="[Silver].[FatoControleContratosEuromidia]",
        )

    @task(task_id="update_id_painel")
    def update_id_painel() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID do painel",
            descricao_etapa=(
                "Relaciona o código do ponto dos itens contratuais à dimensão de painéis, "
                "preenchendo o IDPainelEuromidia."
            ),
            sql_execucao=UPDATE_PONTOS_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    CodPonto,
                    IDPainelEuromidia,
                    CodFace,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                WHERE IDPainelEuromidia IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia] + [Integracao].[Silver].[DimPaineisEuromidia]",
            destino_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="update_id_face")
    def update_id_face() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar ID da face",
            descricao_etapa=(
                "Relaciona o código da face presente nos itens contratuais à dimensão de faces de painéis."
            ),
            sql_execucao=UPDATE_FACES_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    CodFace,
                    IDDimFacesPaineis,
                    CodPonto,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia]
                WHERE IDDimFacesPaineis IS NOT NULL
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia] + [Integracao].[Silver].[DimFacesPaineis]",
            destino_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia]",
        )

    @task(task_id="upsert_dim_calendario")
    def upsert_dim_calendario() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="Atualizar dimensão calendário",
            descricao_etapa=(
                "Executa o procedimento de upsert da dimensão calendário, garantindo suporte temporal "
                "para análises baseadas em datas de contratos, vencimentos e ocupação."
            ),
            sql_execucao=CALL_PROCEDURE_SQL,
            sql_amostra="""
                SELECT TOP 5 *
                FROM [Silver].[DimCalendario]
                ORDER BY Data DESC
            """,
            origem_dados="Procedimento Silver.sp_UpsertDimCalendario",
            destino_dados="[Silver].[DimCalendario]",
            ignorar_erro_permissao_execute=True,
        )

    @task(task_id="upsert_ocupacao")
    def upsert_ocupacao() -> dict[str, Any]:
        return executar_sql_auditado(
            nome_amigavel="MERGE ocupação dos painéis",
            descricao_etapa=(
                "Deriva a ocupação contratual dos painéis Euromídia a partir dos itens de contratos, "
                "calculando início, fim, status, cliente, vendedor e demais metadados operacionais."
            ),
            sql_execucao=UPDATE_OCUPACAO_SQL,
            sql_amostra="""
                SELECT TOP 5
                    Referencia,
                    CodPonto,
                    CodFace,
                    Origem,
                    Status,
                    DataInicio,
                    DataFim,
                    Cota,
                    MarcaExibida,
                    Vendedor,
                    NumeroContrato,
                    NumeroPrevia,
                    DataAtualizacao
                FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
                ORDER BY DataAtualizacao DESC, Referencia DESC
            """,
            origem_dados="[Integracao].[Silver].[FatoControleContratosItensEuromidia]",
            destino_dados="[Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]",
        )

    contexto_disparo = registrar_contexto_disparo()
    info_download = baixar_arquivo_sharepoint()
    info_csv = gerar_csv_ctr(info_download)
    stage = carregar_stage(info_csv)
    contratos = merge_contratos()
    itens = merge_itens()
    fk = update_fk_contratos_itens()
    vendedor = update_id_vendedor()
    empresa = update_id_empresa()
    painel = update_id_painel()
    face = update_id_face()
    calendario = upsert_dim_calendario()
    ocupacao = upsert_ocupacao()

    prioridade_reservas = TriggerDagRunOperator(
        task_id="acionar_prioridade_reservas_pos_upsert_ocupacao",
        trigger_dag_id=DAG_ID_PRIORIDADE_RESERVAS,
        trigger_run_id="pos_upsert_ocupacao__{{ ts_nodash }}__{{ ti.try_number }}",
        conf={
            "origem": "pipeline_controle_contratos_euromidia",
            "modo_execucao": "VARREDURA_POS_UPSERT_OCUPACAO",
            "processar_todos_elegiveis": True,
            "tabela_origem": "[Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]",
            "dag_origem": DAG_ID,
            "run_id_origem": "{{ dag_run.run_id }}",
            "logical_date_origem": "{{ ds }}",
        },
        wait_for_completion=False,
    )

    (
        contexto_disparo
        >> info_download
        >> info_csv
        >> stage
        >> contratos
        >> itens
        >> fk
        >> vendedor
        >> empresa
        >> painel
        >> face
        >> calendario
        >> ocupacao
        >> prioridade_reservas
    )


dag = pipeline_controle_contratos_euromidia()



