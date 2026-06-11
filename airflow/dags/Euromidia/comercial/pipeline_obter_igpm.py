"""
DAG: pipeline_obter_igpm

Carga do IGP-M mensal do Banco Central do Brasil para a tabela
[DataMining].[Silver].[FatoIGPM] no SQL Server.

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


DAG_ID = "pipeline_obter_igpm"
CONN_ID_SQL_SERVER = "mssql_datamining"
DATABASE_DESTINO_SQL_SERVER = "DataMining"
TZ_SAO_PAULO = "America/Sao_Paulo"


DAG_DOC_MD = """
# pipeline_obter_igpm

Pipeline responsável por obter a série mensal do **IGP-M** no SGS/BCData do
Banco Central do Brasil e gravar os dados tratados na tabela
`[DataMining].[Silver].[FatoIGPM]`.

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
- Código SGS: `189`
- Série: `IGP-M mensal`

## Destino

Tabela SQL Server:

```text
[DataMining].[Silver].[FatoIGPM]
```

## Regra de carga

- Se a tabela estiver vazia para a série, carrega os últimos 6 anos.
- Se já houver dados, reprocessa uma janela recente para permitir upsert,
  recalcular acumulados e capturar eventuais revisões ou meses faltantes.
- O upsert usa a chave lógica:
  - `CodigoSerieSGS`
  - `DataReferencia`

## Cálculos gerados

- Variação mensal em percentual.
- Variação mensal em decimal.
- Fator mensal.
- Acumulado de 3 meses.
- Acumulado de 6 meses.
- Acumulado de 12 meses.
- Acumulado no ano.
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

O valor retornado pela API do Banco Central já vem em percentual mensal.
Exemplo: `0.84` significa `0,84%`, não `84%`.
"""


def importar_hook_sql_server():
    """
    Importa o HookSqlServer sem prender a DAG a uma única estrutura de pastas.

    Caminhos comuns considerados:
    - SqlServer.py no mesmo diretório da DAG.
    - hooks/SqlServer.py.
    - plugins/hooks/SqlServer.py.
    - include/hooks/SqlServer.py.
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


def calcular_media(lista_valores: List[Decimal]) -> Optional[Decimal]:
    if not lista_valores:
        return None
    return sum(lista_valores, Decimal("0")) / Decimal(len(lista_valores))


def decimal_para_sql_texto(valor: Optional[Decimal], casas_decimais: int) -> Optional[str]:
    """
    Converte Decimal para texto com escala fixa antes de enviar ao pyodbc.

    Evita erro de perda de precisão ao gravar DECIMAL no SQL Server.
    """

    if valor is None:
        return None

    if not isinstance(valor, Decimal):
        valor = Decimal(str(valor).replace(",", "."))

    quantizador = Decimal("1").scaleb(-casas_decimais)
    valor_quantizado = valor.quantize(quantizador, rounding=ROUND_HALF_UP)

    return format(valor_quantizado, f".{casas_decimais}f")


def normalizar_linha_stage_igpm(linha: Tuple) -> Tuple:
    """Normaliza campos DECIMAL antes do executemany."""

    valores = list(linha)

    campos_decimal_6 = [6, 9, 10, 11, 12, 13, 14, 15]
    campos_decimal_10 = [7, 8]

    for indice in campos_decimal_6:
        valores[indice] = decimal_para_sql_texto(valores[indice], 6)

    for indice in campos_decimal_10:
        valores[indice] = decimal_para_sql_texto(valores[indice], 10)

    return tuple(valores)


def diagnosticar_linhas_igpm(linhas: List[Tuple]) -> None:
    """Loga as primeiras linhas geradas para facilitar validação no Airflow."""

    if not linhas:
        print("Nenhuma linha para diagnosticar.")
        return

    print("=" * 100)
    print("DIAGNOSTICO DAS PRIMEIRAS LINHAS GERADAS PARA O IGP-M")
    print("=" * 100)

    for idx, linha in enumerate(linhas[:5], start=1):
        print(f"Linha {idx}:")
        print(f"  CodigoSerieSGS: {linha[0]}")
        print(f"  DataReferencia: {linha[2]}")
        print(f"  MesAno: {linha[5]}")
        print(f"  IGPMVariacaoMensalPct: {linha[6]}")
        print(f"  IGPMVariacaoMensalDecimal: {linha[7]}")
        print(f"  IGPMFatorMensal: {linha[8]}")
        print(f"  IGPMAcumulado12MPct: {linha[11]}")
        print("-" * 100)


def calcular_acumulado_pct(lista_percentuais: List[Decimal]) -> Optional[Decimal]:
    """
    Calcula inflação acumulada por multiplicação dos fatores mensais.

    Exemplo:
    mês 1 = 1%
    mês 2 = 2%

    correto:
    (1,01 * 1,02 - 1) * 100 = 3,02%
    """

    if not lista_percentuais:
        return None

    fator_acumulado = Decimal("1")

    for percentual in lista_percentuais:
        fator_mensal = Decimal("1") + (percentual / Decimal("100"))
        fator_acumulado *= fator_mensal

    return (fator_acumulado - Decimal("1")) * Decimal("100")


def validar_database_destino(cursor, database_esperado: str = DATABASE_DESTINO_SQL_SERVER) -> str:
    """
    Valida se a conexão do Airflow abriu o database correto no SQL Server.

    Isso evita o erro clássico de a tabela existir em DataMining, mas a DAG
    procurar em Integracao por causa do conn_id errado.
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


def validar_tabela_fato_igpm(cursor) -> None:
    """
    Valida se a tabela destino existe.

    A DAG não cria schema, tabela nem índice. Ela apenas carrega dados.
    """

    cursor.execute(
        """
        IF OBJECT_ID('[Silver].[FatoIGPM]', 'U') IS NULL
        BEGIN
            RAISERROR('A tabela [DataMining].[Silver].[FatoIGPM] nao existe no database conectado. Crie a tabela antes de executar a carga ou verifique a Connection do Airflow.', 16, 1);
        END
        """
    )


def consultar_estado_tabela(cursor, codigo_serie: int) -> Tuple[int, Optional[date]]:
    cursor.execute(
        """
        SELECT
            COUNT(1) AS QtdRegistros,
            MAX(DataReferencia) AS MaxDataReferencia
        FROM [Silver].[FatoIGPM]
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


def buscar_igpm_bcb(data_inicial: date, data_final: date) -> Tuple[List[Dict[str, Any]], str, int, str]:
    codigo_serie = 189
    nome_serie = "IGP-M mensal"

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
                "igpm_variacao_mensal_pct": valor_percentual,
            }
        )

    registros.sort(key=lambda x: x["data_referencia"])

    return registros, url, codigo_serie, nome_serie


def calcular_campos_igpm(
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
        "IGPMVariacaoMensalPct vem da API do Banco Central e ja esta em percentual mensal. "
        "IGPMVariacaoMensalDecimal = percentual / 100. "
        "IGPMFatorMensal = 1 + decimal. "
        "Acumulados calculados por multiplicacao dos fatores mensais."
    )

    for i, registro in enumerate(registros):
        data_referencia = registro["data_referencia"]

        if data_referencia < data_inicio_gravacao:
            continue

        variacao_mensal_pct = registro["igpm_variacao_mensal_pct"]
        variacao_mensal_decimal = variacao_mensal_pct / Decimal("100")
        fator_mensal = Decimal("1") + variacao_mensal_decimal

        acumulado_3m_pct = None
        if i >= 2:
            percentuais_3m = [registros[j]["igpm_variacao_mensal_pct"] for j in range(i - 2, i + 1)]
            acumulado_3m_pct = calcular_acumulado_pct(percentuais_3m)

        acumulado_6m_pct = None
        if i >= 5:
            percentuais_6m = [registros[j]["igpm_variacao_mensal_pct"] for j in range(i - 5, i + 1)]
            acumulado_6m_pct = calcular_acumulado_pct(percentuais_6m)

        acumulado_12m_pct = None
        if i >= 11:
            percentuais_12m = [registros[j]["igpm_variacao_mensal_pct"] for j in range(i - 11, i + 1)]
            acumulado_12m_pct = calcular_acumulado_pct(percentuais_12m)

        percentuais_ano = [
            registros[j]["igpm_variacao_mensal_pct"]
            for j in range(0, i + 1)
            if registros[j]["data_referencia"].year == data_referencia.year
        ]
        acumulado_ano_pct = calcular_acumulado_pct(percentuais_ano)

        media_movel_3m = None
        if i >= 2:
            media_movel_3m = calcular_media(
                [
                    registros[i - 2]["igpm_variacao_mensal_pct"],
                    registros[i - 1]["igpm_variacao_mensal_pct"],
                    registros[i]["igpm_variacao_mensal_pct"],
                ]
            )

        media_movel_6m = None
        if i >= 5:
            media_movel_6m = calcular_media(
                [registros[j]["igpm_variacao_mensal_pct"] for j in range(i - 5, i + 1)]
            )

        media_movel_12m = None
        if i >= 11:
            media_movel_12m = calcular_media(
                [registros[j]["igpm_variacao_mensal_pct"] for j in range(i - 11, i + 1)]
            )

        mes_ano_disponivel_para_uso = formatar_mes_ano(adicionar_meses(data_referencia, 2))

        linha = (
            codigo_serie,
            nome_serie,
            data_referencia,
            data_referencia.year,
            data_referencia.month,
            formatar_mes_ano(data_referencia),
            q6(variacao_mensal_pct),
            q10(variacao_mensal_decimal),
            q10(fator_mensal),
            q6(acumulado_3m_pct),
            q6(acumulado_6m_pct),
            q6(acumulado_12m_pct),
            q6(acumulado_ano_pct),
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


def upsert_fato_igpm(cursor, linhas: List[Tuple]) -> int:
    if not linhas:
        return 0

    try:
        cursor.fast_executemany = False
    except Exception:
        pass

    cursor.execute(
        """
        IF OBJECT_ID('tempdb..#StageFatoIGPM') IS NOT NULL
        BEGIN
            DROP TABLE #StageFatoIGPM;
        END
        """
    )

    cursor.execute(
        """
        CREATE TABLE #StageFatoIGPM
        (
            [CodigoSerieSGS] INT NOT NULL,
            [NomeSerie] VARCHAR(150) NOT NULL,
            [DataReferencia] DATE NOT NULL,
            [Ano] SMALLINT NOT NULL,
            [Mes] TINYINT NOT NULL,
            [MesAno] CHAR(7) NOT NULL,
            [IGPMVariacaoMensalPct] DECIMAL(18,6) NOT NULL,
            [IGPMVariacaoMensalDecimal] DECIMAL(18,10) NULL,
            [IGPMFatorMensal] DECIMAL(18,10) NULL,
            [IGPMAcumulado3MPct] DECIMAL(18,6) NULL,
            [IGPMAcumulado6MPct] DECIMAL(18,6) NULL,
            [IGPMAcumulado12MPct] DECIMAL(18,6) NULL,
            [IGPMAcumuladoAnoPct] DECIMAL(18,6) NULL,
            [IGPMMediaMovel3M] DECIMAL(18,6) NULL,
            [IGPMMediaMovel6M] DECIMAL(18,6) NULL,
            [IGPMMediaMovel12M] DECIMAL(18,6) NULL,
            [MesAnoDisponivelParaUso] CHAR(7) NULL,
            [Observacao] VARCHAR(300) NULL,
            [Fonte] VARCHAR(100) NOT NULL,
            [UrlConsulta] VARCHAR(500) NULL,
            [DataCarga] DATETIME2(0) NOT NULL,
            [BitAtivo] BIT NOT NULL
        );
        """
    )

    insert_stage = """
        INSERT INTO #StageFatoIGPM
        (
            CodigoSerieSGS,
            NomeSerie,
            DataReferencia,
            Ano,
            Mes,
            MesAno,
            IGPMVariacaoMensalPct,
            IGPMVariacaoMensalDecimal,
            IGPMFatorMensal,
            IGPMAcumulado3MPct,
            IGPMAcumulado6MPct,
            IGPMAcumulado12MPct,
            IGPMAcumuladoAnoPct,
            IGPMMediaMovel3M,
            IGPMMediaMovel6M,
            IGPMMediaMovel12M,
            MesAnoDisponivelParaUso,
            Observacao,
            Fonte,
            UrlConsulta,
            DataCarga,
            BitAtivo
        )
        VALUES
        (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        );
    """

    linhas_normalizadas = [normalizar_linha_stage_igpm(linha) for linha in linhas]
    cursor.executemany(insert_stage, linhas_normalizadas)

    cursor.execute(
        """
        MERGE [Silver].[FatoIGPM] AS Destino
        USING #StageFatoIGPM AS Origem
            ON Destino.CodigoSerieSGS = Origem.CodigoSerieSGS
           AND Destino.DataReferencia = Origem.DataReferencia

        WHEN MATCHED THEN
            UPDATE SET
                Destino.NomeSerie = Origem.NomeSerie,
                Destino.Ano = Origem.Ano,
                Destino.Mes = Origem.Mes,
                Destino.MesAno = Origem.MesAno,
                Destino.IGPMVariacaoMensalPct = Origem.IGPMVariacaoMensalPct,
                Destino.IGPMVariacaoMensalDecimal = Origem.IGPMVariacaoMensalDecimal,
                Destino.IGPMFatorMensal = Origem.IGPMFatorMensal,
                Destino.IGPMAcumulado3MPct = Origem.IGPMAcumulado3MPct,
                Destino.IGPMAcumulado6MPct = Origem.IGPMAcumulado6MPct,
                Destino.IGPMAcumulado12MPct = Origem.IGPMAcumulado12MPct,
                Destino.IGPMAcumuladoAnoPct = Origem.IGPMAcumuladoAnoPct,
                Destino.IGPMMediaMovel3M = Origem.IGPMMediaMovel3M,
                Destino.IGPMMediaMovel6M = Origem.IGPMMediaMovel6M,
                Destino.IGPMMediaMovel12M = Origem.IGPMMediaMovel12M,
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
                IGPMVariacaoMensalPct,
                IGPMVariacaoMensalDecimal,
                IGPMFatorMensal,
                IGPMAcumulado3MPct,
                IGPMAcumulado6MPct,
                IGPMAcumulado12MPct,
                IGPMAcumuladoAnoPct,
                IGPMMediaMovel3M,
                IGPMMediaMovel6M,
                IGPMMediaMovel12M,
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
                Origem.IGPMVariacaoMensalPct,
                Origem.IGPMVariacaoMensalDecimal,
                Origem.IGPMFatorMensal,
                Origem.IGPMAcumulado3MPct,
                Origem.IGPMAcumulado6MPct,
                Origem.IGPMAcumulado12MPct,
                Origem.IGPMAcumuladoAnoPct,
                Origem.IGPMMediaMovel3M,
                Origem.IGPMMediaMovel6M,
                Origem.IGPMMediaMovel12M,
                Origem.MesAnoDisponivelParaUso,
                Origem.Observacao,
                Origem.Fonte,
                Origem.UrlConsulta,
                Origem.DataCarga,
                Origem.BitAtivo
            );
        """
    )

    cursor.execute("DROP TABLE #StageFatoIGPM;")

    return len(linhas)


def executar_carga_igpm(conn_id_sql_server: str = CONN_ID_SQL_SERVER) -> Dict[str, Any]:
    """Executa a carga do IGP-M usando o HookSqlServer do Airflow."""

    HookSqlServer = importar_hook_sql_server()
    hook_sql_server = HookSqlServer(conn_id=conn_id_sql_server)
    engine = hook_sql_server.obter_engine()

    hoje = pendulum.now(TZ_SAO_PAULO).date()
    codigo_serie = 189

    conexao = engine.raw_connection()

    try:
        cursor = conexao.cursor()

        database_atual = validar_database_destino(cursor)
        validar_tabela_fato_igpm(cursor)
        qtd_antes, max_data_antes = consultar_estado_tabela(cursor, codigo_serie)

        data_inicio_historico = date(hoje.year - 6, 1, 1)

        if qtd_antes == 0 or max_data_antes is None:
            data_inicio_gravacao = data_inicio_historico
            motivo_carga = "Tabela vazia para essa serie. Carregando os ultimos 6 anos ate hoje."
        else:
            data_inicio_gravacao = adicionar_meses(primeiro_dia_mes(max_data_antes), -13)

            if data_inicio_gravacao < data_inicio_historico:
                data_inicio_gravacao = data_inicio_historico

            motivo_carga = (
                "Tabela ja possui dados. Reprocessando janela recente para upsert, "
                "recalculo de acumulados e busca de meses faltantes ate hoje."
            )

        data_inicio_consulta = adicionar_meses(data_inicio_gravacao, -12)

        registros_bcb, url_consulta, codigo_serie, nome_serie = buscar_igpm_bcb(
            data_inicial=data_inicio_consulta,
            data_final=hoje,
        )

        linhas = calcular_campos_igpm(
            registros=registros_bcb,
            data_inicio_gravacao=data_inicio_gravacao,
            codigo_serie=codigo_serie,
            nome_serie=nome_serie,
            url_consulta=url_consulta,
        )

        diagnosticar_linhas_igpm(linhas)
        qtd_linhas_upsert = upsert_fato_igpm(cursor, linhas)

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
            "data_final": formatar_data_bcb(hoje),
            "qtd_registros_bcb_recebidos": len(registros_bcb),
            "qtd_linhas_enviadas_para_upsert": qtd_linhas_upsert,
            "qtd_registros_sql_antes": qtd_antes,
            "max_data_sql_antes": str(max_data_antes) if max_data_antes else None,
            "qtd_registros_sql_depois": qtd_depois,
            "max_data_sql_depois": str(max_data_depois) if max_data_depois else None,
            "observacao": (
                "IGPMVariacaoMensalPct ja vem da API como percentual mensal. "
                "Exemplo: 0.84 significa 0,84%, nao 84%. "
                "O decimal gravado e IGPMVariacaoMensalDecimal = percentual / 100. "
                "Os acumulados sao calculados por multiplicacao dos fatores mensais."
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
    description="Obtém o IGP-M mensal do Banco Central e realiza upsert em [DataMining].[Silver].[FatoIGPM] no SQL Server.",
    schedule="0 9,19 * * *",
    start_date=pendulum.datetime(2026, 6, 11, tz=TZ_SAO_PAULO),
    catchup=False,
    max_active_runs=1,
    tags=["bcb", "sgs", "igpm", "sql-server", "silver", "macroeconomia"],
    default_args={
        "owner": "euromidia",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
    },
    doc_md=DAG_DOC_MD,
)
def pipeline_obter_igpm():
    @task(task_id="testar_conexao_sql_server")
    def testar_conexao_sql_server() -> str:
        HookSqlServer = importar_hook_sql_server()
        hook_sql_server = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
        mensagem = hook_sql_server.testar_conexao()
        print(mensagem)
        return mensagem

    @task(task_id="obter_e_gravar_igpm")
    def obter_e_gravar_igpm() -> Dict[str, Any]:
        return executar_carga_igpm(conn_id_sql_server=CONN_ID_SQL_SERVER)

    testar_conexao_sql_server() >> obter_e_gravar_igpm()


pipeline_obter_igpm_dag = pipeline_obter_igpm()
