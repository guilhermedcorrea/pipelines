"""
DAG: pipeline_obter_inadimplencia_pj

Carga da série de inadimplência da carteira de crédito de pessoas jurídicas
(PJ) do Banco Central do Brasil para a tabela
[DataMining].[Silver].[FatoInadimplenciaCarteiraPJ] no SQL Server.

A DAG usa o HookSqlServer da conexão cadastrada no Airflow. Não há credenciais
hardcoded neste arquivo.
"""

from __future__ import annotations

import importlib
import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import pendulum

try:
    from airflow.sdk import dag, task
except ImportError:  # Compatibilidade com Airflow 2.x
    from airflow.decorators import dag, task


DAG_ID = "pipeline_obter_inadimplencia_pj"
CONN_ID_SQL_SERVER = "mssql_datamining"
DATABASE_DESTINO_SQL_SERVER = "DataMining"
TZ_SAO_PAULO = "America/Sao_Paulo"
CODIGO_SERIE_SGS = 21083
DATA_INICIO_HISTORICO = date(2020, 1, 1)
DATA_FINAL_CONSULTA_BCB = date(2026, 12, 31)


DAG_DOC_MD = """
# pipeline_obter_inadimplencia_pj

Pipeline responsável por obter a série mensal de **inadimplência da carteira de
crédito de pessoas jurídicas - total** no SGS/BCData do Banco Central do Brasil
e gravar os dados tratados na tabela `[DataMining].[Silver].[FatoInadimplenciaCarteiraPJ]`.

## Frequência

A execução ocorre todos os dias em dois horários:

- **09:00**
- **19:00**

A expressão cron usada é:

```text
0 9,19 * * *
```

## Origem dos dados

- Fonte: Banco Central do Brasil - SGS/BCData
- Código SGS: `21083`
- Série: inadimplência da carteira de crédito - pessoas jurídicas - total
- Endpoint base: série `bcdata.sgs.21083`

## Destino

Tabela SQL Server:

```text
[DataMining].[Silver].[FatoInadimplenciaCarteiraPJ]
```

## Regra de carga

- Se a tabela estiver vazia para a série, carrega desde `01/01/2020`.
- Se já houver dados, reprocessa uma janela recente para permitir upsert,
  recalcular tendências e capturar eventuais revisões ou meses faltantes.
- O upsert usa a chave lógica:
  - `CodigoSerieSGS`
  - `DataReferencia`

## Cálculos gerados

- Percentual mensal de inadimplência PJ.
- Percentual convertido para decimal.
- Inadimplência PJ do mês anterior.
- Variação mensal em pontos percentuais.
- Variação mensal relativa percentual.
- Variação em pontos percentuais contra 3, 6 e 12 meses anteriores.
- Médias móveis de 3, 6 e 12 meses.
- Mês/ano disponível para uso, considerando defasagem operacional de 2 meses.

## Conexão SQL Server

A conexão é feita exclusivamente pelo `HookSqlServer`, usando a Connection do
Airflow definida em `CONN_ID_SQL_SERVER`.

Valor padrão:

```text
mssql_datamining
```

Essa conexão deve apontar para o database `DataMining` no campo `schema` da Connection do Airflow.

## Observação importante

Essa série já vem da API como **percentual da carteira de crédito PJ em
inadimplência**. Portanto:

- `2.40` significa `2,40%`, não `240%`.
- O campo decimal é calculado como `percentual / 100`.
- A diferença mensal principal é em **pontos percentuais**, não por composição
  de fator como IPCA/IGP-M.
"""


def importar_hook_sql_server():
    """
    Importa o HookSqlServer sem prender a DAG a uma única estrutura de pastas.

    Caminhos comuns considerados:
    - SqlServer.py no mesmo diretório da DAG.
    - hooks/SqlServer.py.
    - plugins/hooks/SqlServer.py.
    - include/hooks/SqlServer.py.
    - includes/hooks/SqlServer.py.
    """

    caminhos_modulo = (
        "SqlServer",
        "hooks.SqlServer",
        "plugins.hooks.SqlServer",
        "include.hooks.SqlServer",
        "includes.hooks.SqlServer",
    )

    erros: list[str] = []

    for caminho_modulo in caminhos_modulo:
        try:
            modulo = importlib.import_module(caminho_modulo)
            return getattr(modulo, "HookSqlServer")
        except ModuleNotFoundError as erro:
            erros.append(f"{caminho_modulo}: {erro}")
        except AttributeError as erro:
            erros.append(f"{caminho_modulo}: {erro}")

    raise ImportError(
        "Não foi possível importar HookSqlServer. "
        "Coloque o arquivo SqlServer.py no mesmo diretório da DAG, em hooks/, "
        "em plugins/hooks/ ou ajuste os caminhos em importar_hook_sql_server(). "
        f"Tentativas: {' | '.join(erros)}"
    )


def primeiro_dia_mes(data_base: date) -> date:
    return date(data_base.year, data_base.month, 1)


def adicionar_meses(data_base: date, meses: int) -> date:
    mes_base = data_base.month - 1 + meses
    ano = data_base.year + mes_base // 12
    mes = mes_base % 12 + 1
    return date(ano, mes, 1)


def formatar_data_bcb(data_base: date) -> str:
    return data_base.strftime("%d/%m/%Y")


def formatar_mes_ano(data_base: date) -> str:
    return f"{data_base.year:04d}-{data_base.month:02d}"


def converter_data_bcb(data_texto: str) -> date:
    dia, mes, ano = data_texto.split("/")
    return date(int(ano), int(mes), int(dia))


def converter_decimal_bcb(valor_texto: str) -> Decimal:
    return Decimal(str(valor_texto).replace(",", "."))


def q6(valor: Optional[Decimal]) -> Optional[Decimal]:
    if valor is None:
        return None

    return valor.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def q10(valor: Optional[Decimal]) -> Optional[Decimal]:
    if valor is None:
        return None

    return valor.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)


def decimal_para_sql_texto(
    valor: Optional[Decimal],
    casas_decimais: int
) -> Optional[str]:
    """
    Converte Decimal para texto com escala fixa antes de enviar ao pyodbc.

    Isso evita erro de precisão:
    'Converting decimal loses precision'
    """

    if valor is None:
        return None

    if not isinstance(valor, Decimal):
        valor = Decimal(str(valor).replace(",", "."))

    quantizador = Decimal("1").scaleb(-casas_decimais)

    valor_quantizado = valor.quantize(
        quantizador,
        rounding=ROUND_HALF_UP
    )

    return format(valor_quantizado, f".{casas_decimais}f")


def calcular_media(lista_valores: List[Decimal]) -> Optional[Decimal]:
    if not lista_valores:
        return None

    return sum(lista_valores, Decimal("0")) / Decimal(len(lista_valores))


def calcular_variacao_relativa_pct(
    valor_atual: Decimal,
    valor_base: Optional[Decimal]
) -> Optional[Decimal]:
    if valor_base is None:
        return None

    if valor_base == 0:
        return None

    return ((valor_atual / valor_base) - Decimal("1")) * Decimal("100")


def validar_database_destino(cursor, database_esperado: str = DATABASE_DESTINO_SQL_SERVER) -> str:
    """
    Valida se a conexão do Airflow abriu o database correto no SQL Server.

    Isso evita o erro de a tabela existir em DataMining, mas a DAG procurar
    em Integracao por causa do conn_id errado.
    """

    cursor.execute("SELECT DB_NAME() AS DatabaseAtual;")
    row = cursor.fetchone()
    database_atual = str(row[0]) if row and row[0] is not None else ""

    if database_atual.lower() != database_esperado.lower():
        raise RuntimeError(
            f"Database SQL Server incorreto para esta DAG. "
            f"Esperado: {database_esperado}. "
            f"Conectado em: {database_atual}. "
            f"Verifique o conn_id '{CONN_ID_SQL_SERVER}' e o campo schema da Connection no Airflow."
        )

    print(f"Database SQL Server validado com sucesso: {database_atual}")
    return database_atual


def validar_tabela_fato_inadimplencia_pj(cursor) -> None:
    """
    Não cria schema.
    Não cria tabela.
    Não cria índice.

    Apenas valida se a tabela [DataMining].[Silver].[FatoInadimplenciaCarteiraPJ] já existe no database conectado.
    """

    cursor.execute(
        """
        IF OBJECT_ID('[Silver].[FatoInadimplenciaCarteiraPJ]', 'U') IS NULL
        BEGIN
            RAISERROR('A tabela [DataMining].[Silver].[FatoInadimplenciaCarteiraPJ] nao existe no database conectado. Crie a tabela antes de executar a carga ou verifique a Connection do Airflow.', 16, 1);
        END
        """
    )


def consultar_estado_tabela(cursor, codigo_serie: int) -> Tuple[int, Optional[date]]:
    cursor.execute(
        """
        SELECT
            COUNT(1) AS QtdRegistros,
            MAX(DataReferencia) AS MaxDataReferencia
        FROM [Silver].[FatoInadimplenciaCarteiraPJ]
        WHERE CodigoSerieSGS = ?
        """,
        codigo_serie,
    )

    row = cursor.fetchone()

    qtd_registros = int(row[0] or 0)
    max_data = row[1]

    if isinstance(max_data, datetime):
        max_data = max_data.date()

    if isinstance(max_data, str):
        max_data = datetime.strptime(max_data[:10], "%Y-%m-%d").date()

    return qtd_registros, max_data


def buscar_inadimplencia_pj_bcb(
    data_inicial: date,
    data_final: date,
) -> Tuple[List[Dict[str, Any]], str, int, str]:

    codigo_serie = 21083
    nome_serie = "Inadimplencia da carteira de credito - pessoas juridicas - total"

    base_url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo_serie}/dados"

    params = {
        "formato": "json",
        "dataInicial": formatar_data_bcb(data_inicial),
        "dataFinal": formatar_data_bcb(data_final),
    }

    url = base_url + "?" + urllib.parse.urlencode(params)

    with urllib.request.urlopen(url, timeout=30) as response:
        dados_json = response.read().decode("utf-8")

    dados = json.loads(dados_json)

    registros = []

    for item in dados:
        data_referencia = converter_data_bcb(item["data"])
        valor_percentual = converter_decimal_bcb(item["valor"])

        registros.append(
            {
                "data_referencia": data_referencia,
                "inadimplencia_pj_pct": valor_percentual,
            }
        )

    registros.sort(key=lambda x: x["data_referencia"])

    return registros, url, codigo_serie, nome_serie


def calcular_campos_inadimplencia_pj(
    registros: List[Dict[str, Any]],
    data_inicio_gravacao: date,
    codigo_serie: int,
    nome_serie: str,
    url_consulta: str,
) -> List[Tuple]:

    linhas = []

    fonte = "Banco Central do Brasil - SGS/BCData"
    data_carga = datetime.now().replace(microsecond=0)

    observacao = (
        "InadimplenciaCarteiraPJPct vem da API do Banco Central e ja esta em percentual. "
        "Exemplo: 2.40 significa 2,40% da carteira de credito PJ em inadimplencia, nao 240%. "
        "InadimplenciaCarteiraPJDecimal = percentual / 100. "
        "Diferenca mensal em pontos percentuais = valor atual - valor anterior. "
        "Nao e serie de inflacao, portanto nao deve ser acumulada por fator como IPCA/IGPM."
    )

    for i, registro in enumerate(registros):
        data_referencia = registro["data_referencia"]

        if data_referencia < data_inicio_gravacao:
            continue

        inadimplencia_pj_pct = registro["inadimplencia_pj_pct"]
        inadimplencia_pj_decimal = inadimplencia_pj_pct / Decimal("100")

        inadimplencia_pj_mes_anterior_pct = None
        variacao_mensal_pontos_pct = None
        variacao_mensal_relativa_pct = None

        if i >= 1:
            inadimplencia_pj_mes_anterior_pct = registros[i - 1]["inadimplencia_pj_pct"]
            variacao_mensal_pontos_pct = inadimplencia_pj_pct - inadimplencia_pj_mes_anterior_pct
            variacao_mensal_relativa_pct = calcular_variacao_relativa_pct(
                inadimplencia_pj_pct,
                inadimplencia_pj_mes_anterior_pct
            )

        variacao_3m_pontos_pct = None
        if i >= 3:
            valor_base_3m = registros[i - 3]["inadimplencia_pj_pct"]
            variacao_3m_pontos_pct = inadimplencia_pj_pct - valor_base_3m

        variacao_6m_pontos_pct = None
        if i >= 6:
            valor_base_6m = registros[i - 6]["inadimplencia_pj_pct"]
            variacao_6m_pontos_pct = inadimplencia_pj_pct - valor_base_6m

        variacao_12m_pontos_pct = None
        if i >= 12:
            valor_base_12m = registros[i - 12]["inadimplencia_pj_pct"]
            variacao_12m_pontos_pct = inadimplencia_pj_pct - valor_base_12m

        media_movel_3m = None
        if i >= 2:
            media_movel_3m = calcular_media(
                [
                    registros[i - 2]["inadimplencia_pj_pct"],
                    registros[i - 1]["inadimplencia_pj_pct"],
                    registros[i]["inadimplencia_pj_pct"],
                ]
            )

        media_movel_6m = None
        if i >= 5:
            media_movel_6m = calcular_media(
                [
                    registros[j]["inadimplencia_pj_pct"]
                    for j in range(i - 5, i + 1)
                ]
            )

        media_movel_12m = None
        if i >= 11:
            media_movel_12m = calcular_media(
                [
                    registros[j]["inadimplencia_pj_pct"]
                    for j in range(i - 11, i + 1)
                ]
            )

        mes_ano_disponivel_para_uso = formatar_mes_ano(
            adicionar_meses(data_referencia, 2)
        )

        linha = (
            codigo_serie,
            nome_serie,
            data_referencia,
            data_referencia.year,
            data_referencia.month,
            formatar_mes_ano(data_referencia),

            q6(inadimplencia_pj_pct),
            q10(inadimplencia_pj_decimal),
            q6(inadimplencia_pj_mes_anterior_pct),
            q6(variacao_mensal_pontos_pct),
            q6(variacao_mensal_relativa_pct),

            q6(variacao_3m_pontos_pct),
            q6(variacao_6m_pontos_pct),
            q6(variacao_12m_pontos_pct),

            q6(media_movel_3m),
            q6(media_movel_6m),
            q6(media_movel_12m),

            mes_ano_disponivel_para_uso,
            observacao,
            fonte,
            url_consulta[:500],
            data_carga,
            1,
        )

        linhas.append(linha)

    return linhas


def normalizar_linha_stage_inadimplencia_pj(linha: Tuple) -> Tuple:
    """
    Normaliza campos DECIMAL antes do executemany.

    Índices da tupla:
    6  = InadimplenciaCarteiraPJPct                 DECIMAL(18,6)
    7  = InadimplenciaCarteiraPJDecimal             DECIMAL(18,10)
    8  = InadimplenciaPJMesAnteriorPct              DECIMAL(18,6)
    9  = InadimplenciaPJVariacaoMensalPontosPct     DECIMAL(18,6)
    10 = InadimplenciaPJVariacaoMensalRelativaPct   DECIMAL(18,6)
    11 = InadimplenciaPJVariacao3MPontosPct         DECIMAL(18,6)
    12 = InadimplenciaPJVariacao6MPontosPct         DECIMAL(18,6)
    13 = InadimplenciaPJVariacao12MPontosPct        DECIMAL(18,6)
    14 = InadimplenciaPJMediaMovel3M                DECIMAL(18,6)
    15 = InadimplenciaPJMediaMovel6M                DECIMAL(18,6)
    16 = InadimplenciaPJMediaMovel12M               DECIMAL(18,6)
    """

    valores = list(linha)

    campos_decimal_6 = [
        6,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
    ]

    campos_decimal_10 = [
        7,
    ]

    for indice in campos_decimal_6:
        valores[indice] = decimal_para_sql_texto(valores[indice], 6)

    for indice in campos_decimal_10:
        valores[indice] = decimal_para_sql_texto(valores[indice], 10)

    return tuple(valores)


def diagnosticar_linhas_inadimplencia_pj(linhas: List[Tuple]) -> None:
    if not linhas:
        print("Nenhuma linha para diagnosticar.")
        return

    print("=" * 100)
    print("DIAGNOSTICO DAS PRIMEIRAS LINHAS GERADAS PARA INADIMPLENCIA PJ")
    print("=" * 100)

    for idx, linha in enumerate(linhas[:5], start=1):
        print(f"Linha {idx}:")
        print(f"  CodigoSerieSGS: {linha[0]}")
        print(f"  DataReferencia: {linha[2]}")
        print(f"  MesAno: {linha[5]}")
        print(f"  InadimplenciaCarteiraPJPct: {linha[6]}")
        print(f"  InadimplenciaCarteiraPJDecimal: {linha[7]}")
        print(f"  InadimplenciaPJMesAnteriorPct: {linha[8]}")
        print(f"  InadimplenciaPJVariacaoMensalPontosPct: {linha[9]}")
        print(f"  InadimplenciaPJVariacaoMensalRelativaPct: {linha[10]}")
        print("-" * 100)


def imprimir_ultima_data_bcb(registros: List[Dict[str, Any]], data_final_solicitada: date) -> None:
    print("=" * 100)
    print("ULTIMA DATA DISPONIVEL NA API DO BANCO CENTRAL")
    print("=" * 100)

    if not registros:
        print("Nenhum registro retornado pela API do Banco Central.")
        return

    ultima_data_bcb = max(registro["data_referencia"] for registro in registros)

    print(f"Data final solicitada: {data_final_solicitada}")
    print(f"Ultima DataReferencia retornada pelo BCB: {ultima_data_bcb}")
    print(f"Ultimo MesAno retornado pelo BCB: {formatar_mes_ano(ultima_data_bcb)}")

    if ultima_data_bcb < primeiro_dia_mes(date.today()):
        print(
            "AVISO: A serie e mensal e pode ter defasagem de publicacao. "
            "Isso nao significa erro na carga."
        )


def upsert_fato_inadimplencia_pj(cursor, linhas: List[Tuple]) -> int:
    if not linhas:
        return 0

    try:
        cursor.fast_executemany = False
    except Exception:
        pass

    cursor.execute(
        """
        IF OBJECT_ID('tempdb..#StageFatoInadimplenciaCarteiraPJ') IS NOT NULL
        BEGIN
            DROP TABLE #StageFatoInadimplenciaCarteiraPJ;
        END
        """
    )

    cursor.execute(
        """
        CREATE TABLE #StageFatoInadimplenciaCarteiraPJ
        (
            [CodigoSerieSGS] INT NOT NULL,
            [NomeSerie] VARCHAR(200) NOT NULL,
            [DataReferencia] DATE NOT NULL,
            [Ano] SMALLINT NOT NULL,
            [Mes] TINYINT NOT NULL,
            [MesAno] CHAR(7) NOT NULL,

            [InadimplenciaCarteiraPJPct] DECIMAL(18,6) NOT NULL,
            [InadimplenciaCarteiraPJDecimal] DECIMAL(18,10) NULL,
            [InadimplenciaPJMesAnteriorPct] DECIMAL(18,6) NULL,
            [InadimplenciaPJVariacaoMensalPontosPct] DECIMAL(18,6) NULL,
            [InadimplenciaPJVariacaoMensalRelativaPct] DECIMAL(18,6) NULL,

            [InadimplenciaPJVariacao3MPontosPct] DECIMAL(18,6) NULL,
            [InadimplenciaPJVariacao6MPontosPct] DECIMAL(18,6) NULL,
            [InadimplenciaPJVariacao12MPontosPct] DECIMAL(18,6) NULL,

            [InadimplenciaPJMediaMovel3M] DECIMAL(18,6) NULL,
            [InadimplenciaPJMediaMovel6M] DECIMAL(18,6) NULL,
            [InadimplenciaPJMediaMovel12M] DECIMAL(18,6) NULL,

            [MesAnoDisponivelParaUso] CHAR(7) NULL,
            [Observacao] VARCHAR(500) NULL,

            [Fonte] VARCHAR(100) NOT NULL,
            [UrlConsulta] VARCHAR(500) NULL,
            [DataCarga] DATETIME2(0) NOT NULL,
            [BitAtivo] BIT NOT NULL
        );
        """
    )

    insert_stage = """
        INSERT INTO #StageFatoInadimplenciaCarteiraPJ
        (
            CodigoSerieSGS,
            NomeSerie,
            DataReferencia,
            Ano,
            Mes,
            MesAno,

            InadimplenciaCarteiraPJPct,
            InadimplenciaCarteiraPJDecimal,
            InadimplenciaPJMesAnteriorPct,
            InadimplenciaPJVariacaoMensalPontosPct,
            InadimplenciaPJVariacaoMensalRelativaPct,

            InadimplenciaPJVariacao3MPontosPct,
            InadimplenciaPJVariacao6MPontosPct,
            InadimplenciaPJVariacao12MPontosPct,

            InadimplenciaPJMediaMovel3M,
            InadimplenciaPJMediaMovel6M,
            InadimplenciaPJMediaMovel12M,

            MesAnoDisponivelParaUso,
            Observacao,

            Fonte,
            UrlConsulta,
            DataCarga,
            BitAtivo
        )
        VALUES
        (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?
        );
    """

    linhas_normalizadas = [
        normalizar_linha_stage_inadimplencia_pj(linha)
        for linha in linhas
    ]

    cursor.executemany(insert_stage, linhas_normalizadas)

    cursor.execute(
        """
        MERGE [Silver].[FatoInadimplenciaCarteiraPJ] AS Destino
        USING #StageFatoInadimplenciaCarteiraPJ AS Origem
            ON Destino.CodigoSerieSGS = Origem.CodigoSerieSGS
           AND Destino.DataReferencia = Origem.DataReferencia

        WHEN MATCHED THEN
            UPDATE SET
                Destino.NomeSerie = Origem.NomeSerie,
                Destino.Ano = Origem.Ano,
                Destino.Mes = Origem.Mes,
                Destino.MesAno = Origem.MesAno,

                Destino.InadimplenciaCarteiraPJPct = Origem.InadimplenciaCarteiraPJPct,
                Destino.InadimplenciaCarteiraPJDecimal = Origem.InadimplenciaCarteiraPJDecimal,
                Destino.InadimplenciaPJMesAnteriorPct = Origem.InadimplenciaPJMesAnteriorPct,
                Destino.InadimplenciaPJVariacaoMensalPontosPct = Origem.InadimplenciaPJVariacaoMensalPontosPct,
                Destino.InadimplenciaPJVariacaoMensalRelativaPct = Origem.InadimplenciaPJVariacaoMensalRelativaPct,

                Destino.InadimplenciaPJVariacao3MPontosPct = Origem.InadimplenciaPJVariacao3MPontosPct,
                Destino.InadimplenciaPJVariacao6MPontosPct = Origem.InadimplenciaPJVariacao6MPontosPct,
                Destino.InadimplenciaPJVariacao12MPontosPct = Origem.InadimplenciaPJVariacao12MPontosPct,

                Destino.InadimplenciaPJMediaMovel3M = Origem.InadimplenciaPJMediaMovel3M,
                Destino.InadimplenciaPJMediaMovel6M = Origem.InadimplenciaPJMediaMovel6M,
                Destino.InadimplenciaPJMediaMovel12M = Origem.InadimplenciaPJMediaMovel12M,

                Destino.MesAnoDisponivelParaUso = Origem.MesAnoDisponivelParaUso,
                Destino.Observacao = Origem.Observacao,

                Destino.Fonte = Origem.Fonte,
                Destino.UrlConsulta = Origem.UrlConsulta,
                Destino.DataCarga = Origem.DataCarga,
                Destino.BitAtivo = Origem.BitAtivo

        WHEN NOT MATCHED THEN
            INSERT
            (
                CodigoSerieSGS,
                NomeSerie,
                DataReferencia,
                Ano,
                Mes,
                MesAno,

                InadimplenciaCarteiraPJPct,
                InadimplenciaCarteiraPJDecimal,
                InadimplenciaPJMesAnteriorPct,
                InadimplenciaPJVariacaoMensalPontosPct,
                InadimplenciaPJVariacaoMensalRelativaPct,

                InadimplenciaPJVariacao3MPontosPct,
                InadimplenciaPJVariacao6MPontosPct,
                InadimplenciaPJVariacao12MPontosPct,

                InadimplenciaPJMediaMovel3M,
                InadimplenciaPJMediaMovel6M,
                InadimplenciaPJMediaMovel12M,

                MesAnoDisponivelParaUso,
                Observacao,

                Fonte,
                UrlConsulta,
                DataCarga,
                BitAtivo
            )
            VALUES
            (
                Origem.CodigoSerieSGS,
                Origem.NomeSerie,
                Origem.DataReferencia,
                Origem.Ano,
                Origem.Mes,
                Origem.MesAno,

                Origem.InadimplenciaCarteiraPJPct,
                Origem.InadimplenciaCarteiraPJDecimal,
                Origem.InadimplenciaPJMesAnteriorPct,
                Origem.InadimplenciaPJVariacaoMensalPontosPct,
                Origem.InadimplenciaPJVariacaoMensalRelativaPct,

                Origem.InadimplenciaPJVariacao3MPontosPct,
                Origem.InadimplenciaPJVariacao6MPontosPct,
                Origem.InadimplenciaPJVariacao12MPontosPct,

                Origem.InadimplenciaPJMediaMovel3M,
                Origem.InadimplenciaPJMediaMovel6M,
                Origem.InadimplenciaPJMediaMovel12M,

                Origem.MesAnoDisponivelParaUso,
                Origem.Observacao,

                Origem.Fonte,
                Origem.UrlConsulta,
                Origem.DataCarga,
                Origem.BitAtivo
            );
        """
    )

    cursor.execute("DROP TABLE #StageFatoInadimplenciaCarteiraPJ;")

    return len(linhas)

def executar_carga_inadimplencia_carteira_pj(
    conn_id_sql_server: str = CONN_ID_SQL_SERVER,
) -> Dict[str, Any]:
    HookSqlServer = importar_hook_sql_server()
    hook_sql_server = HookSqlServer(conn_id=conn_id_sql_server)
    engine = hook_sql_server.obter_engine()

    codigo_serie = CODIGO_SERIE_SGS
    data_inicio_historico = DATA_INICIO_HISTORICO
    data_final_consulta = DATA_FINAL_CONSULTA_BCB

    conexao = engine.raw_connection()

    try:
        cursor = conexao.cursor()

        database_atual = validar_database_destino(cursor)
        validar_tabela_fato_inadimplencia_pj(cursor)

        qtd_antes, max_data_antes = consultar_estado_tabela(cursor, codigo_serie)

        if qtd_antes == 0 or max_data_antes is None:
            data_inicio_gravacao = data_inicio_historico
            motivo_carga = (
                "Tabela vazia para essa serie. "
                "Carregando desde 01/01/2020 ate 31/12/2026, conforme endpoint informado."
            )
        else:
            data_inicio_gravacao = adicionar_meses(
                primeiro_dia_mes(max_data_antes),
                -13,
            )

            if data_inicio_gravacao < data_inicio_historico:
                data_inicio_gravacao = data_inicio_historico

            motivo_carga = (
                "Tabela ja possui dados. Reprocessando janela recente para upsert, "
                "recalculo de tendencias e busca de meses faltantes ate 31/12/2026."
            )

        data_inicio_consulta = adicionar_meses(data_inicio_gravacao, -12)

        if data_inicio_consulta < data_inicio_historico:
            data_inicio_consulta = data_inicio_historico

        registros_bcb, url_consulta, codigo_serie, nome_serie = buscar_inadimplencia_pj_bcb(
            data_inicial=data_inicio_consulta,
            data_final=data_final_consulta,
        )

        imprimir_ultima_data_bcb(registros_bcb, data_final_consulta)

        linhas = calcular_campos_inadimplencia_pj(
            registros=registros_bcb,
            data_inicio_gravacao=data_inicio_gravacao,
            codigo_serie=codigo_serie,
            nome_serie=nome_serie,
            url_consulta=url_consulta,
        )

        diagnosticar_linhas_inadimplencia_pj(linhas)
        qtd_linhas_upsert = upsert_fato_inadimplencia_pj(cursor, linhas)

        conexao.commit()

        qtd_depois, max_data_depois = consultar_estado_tabela(cursor, codigo_serie)

        resumo = {
            "status": "OK",
            "database_sql_server": database_atual,
            "serie": nome_serie,
            "codigo_sgs": codigo_serie,
            "motivo_carga": motivo_carga,
            "data_inicio_consulta_bcb": formatar_data_bcb(data_inicio_consulta),
            "data_inicio_gravacao_sql": formatar_data_bcb(data_inicio_gravacao),
            "data_final_consulta_bcb": formatar_data_bcb(data_final_consulta),
            "qtd_registros_bcb_recebidos": len(registros_bcb),
            "qtd_linhas_enviadas_para_upsert": qtd_linhas_upsert,
            "qtd_registros_sql_antes": qtd_antes,
            "max_data_sql_antes": str(max_data_antes) if max_data_antes else None,
            "qtd_registros_sql_depois": qtd_depois,
            "max_data_sql_depois": str(max_data_depois) if max_data_depois else None,
            "observacao": (
                "InadimplenciaCarteiraPJPct vem da API como percentual. "
                "Exemplo: 2.40 significa 2,40% da carteira PJ, nao 240%. "
                "InadimplenciaCarteiraPJDecimal = percentual / 100. "
                "InadimplenciaPJVariacaoMensalPontosPct mede diferenca em pontos percentuais."
            ),
        }

        print(json.dumps(resumo, ensure_ascii=False, indent=4, default=str))
        return resumo

    except Exception as erro:
        conexao.rollback()

        resumo_erro = {
            "status": "ERRO",
            "erro": str(erro),
        }
        print(json.dumps(resumo_erro, ensure_ascii=False, indent=4, default=str))
        raise

    finally:
        conexao.close()
        engine.dispose()


@dag(
    dag_id=DAG_ID,
    description=(
        "Obtém a inadimplência PJ no Banco Central e realiza upsert "
        "em [DataMining].[Silver].[FatoInadimplenciaCarteiraPJ] no SQL Server."
    ),
    schedule="0 9,19 * * *",
    start_date=pendulum.datetime(2026, 6, 11, tz=TZ_SAO_PAULO),
    catchup=False,
    max_active_runs=1,
    tags=[
        "bcb",
        "sgs",
        "inadimplencia",
        "pj",
        "credito",
        "sql-server",
        "silver",
        "macroeconomia",
    ],
    default_args={
        "owner": "euromidia",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
    },
    doc_md=DAG_DOC_MD,
)
def pipeline_obter_inadimplencia_pj():
    @task(task_id="testar_conexao_sql_server")
    def testar_conexao_sql_server() -> str:
        HookSqlServer = importar_hook_sql_server()
        hook_sql_server = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
        mensagem = hook_sql_server.testar_conexao()
        print(mensagem)
        return mensagem

    @task(task_id="obter_e_gravar_inadimplencia_pj")
    def obter_e_gravar_inadimplencia_pj() -> Dict[str, Any]:
        return executar_carga_inadimplencia_carteira_pj(
            conn_id_sql_server=CONN_ID_SQL_SERVER,
        )

    testar_conexao_sql_server() >> obter_e_gravar_inadimplencia_pj()


pipeline_obter_inadimplencia_pj_dag = pipeline_obter_inadimplencia_pj()
