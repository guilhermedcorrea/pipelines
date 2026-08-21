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


DAG_ID = "pipeline_obter_ibcbr"
CONN_ID_SQL_SERVER = "mssql_datamining"
DATABASE_DESTINO_SQL_SERVER = "DataMining"
TZ_SAO_PAULO = "America/Sao_Paulo"
AJUSTE_SAZONAL_PADRAO = True


DAG_DOC_MD = """
# pipeline_obter_ibcbr

Pipeline responsável por obter a série mensal do **IBC-Br** no SGS/BCData do
Banco Central do Brasil e gravar os dados tratados na tabela
`[DataMining].[Silver].[FatoIBCBr]`.

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
- Código SGS com ajuste sazonal: `24364`
- Código SGS sem ajuste sazonal: `24363`
- Configuração padrão desta DAG: **com ajuste sazonal**

## Destino

Tabela SQL Server:

```text
[DataMining].[Silver].[FatoIBCBr]
```

## Regra de carga

- Se a tabela estiver vazia para a série, carrega os últimos 6 anos.
- Se já houver dados, reprocessa uma janela recente para permitir upsert,
  recalcular variações e capturar eventuais revisões ou meses faltantes.
- O upsert usa a chave lógica:
  - `CodigoSerieSGS`
  - `DataReferencia`

## Cálculos gerados

- Índice mensal do IBC-Br.
- Índice do mês anterior.
- Variação mensal percentual.
- Variação mensal decimal.
- Fator da variação mensal.
- Variação acumulada contra 3, 6 e 12 meses anteriores.
- Médias móveis de 3, 6 e 12 meses do índice.
- Mês/ano disponível para uso, considerando defasagem operacional de 2 meses.

## Conexão SQL Server

A conexão é feita exclusivamente pelo `HookSqlServer`, usando a Connection do
Airflow definida em `CONN_ID_SQL_SERVER`.

Esta DAG deve usar a Connection `mssql_datamining`, pois a tabela destino está
no database `DataMining`.

Valor padrão:

```text
mssql_datamining
```

## Observação importante

O IBC-Br vem da API como **índice**, não como percentual. Portanto:

- `IBCBrIndice` é o valor do índice.
- `IBCBrVariacaoMensalPct` é calculado pela DAG usando:

```text
((IBC atual / IBC anterior) - 1) * 100
```
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


def calcular_variacao_pct(valor_atual: Decimal, valor_base: Optional[Decimal]) -> Optional[Decimal]:
    if valor_base is None:
        return None

    if valor_base == 0:
        return None

    return ((valor_atual / valor_base) - Decimal("1")) * Decimal("100")


def calcular_media(lista_valores: List[Decimal]) -> Optional[Decimal]:
    if not lista_valores:
        return None
    return sum(lista_valores, Decimal("0")) / Decimal(len(lista_valores))



def validar_database_ativo(cursor, database_esperado: str = DATABASE_DESTINO_SQL_SERVER) -> None:
    """
    Valida o database ativo da conexão.

    Isso evita o erro silencioso de procurar [Silver].[FatoIBCBr] no database errado,
    por exemplo em Integracao em vez de DataMining.
    """

    cursor.execute("SELECT DB_NAME() AS DatabaseAtual")
    row = cursor.fetchone()
    database_atual = str(row[0]) if row and row[0] is not None else ""

    print(f"Database ativo na conexão SQL Server: {database_atual}")

    if database_atual.lower() != database_esperado.lower():
        raise RuntimeError(
            "Database SQL Server incorreto para esta DAG. "
            f"Esperado: {database_esperado}. Atual: {database_atual}. "
            f"Verifique a Connection do Airflow usada em CONN_ID_SQL_SERVER={CONN_ID_SQL_SERVER}."
        )


def validar_tabela_fato_ibc_br(cursor) -> None:
    """
    Valida se a tabela destino existe.

    A DAG não cria schema, tabela nem índice. Ela apenas carrega dados.
    """

    cursor.execute(
        """
        IF OBJECT_ID('[Silver].[FatoIBCBr]', 'U') IS NULL
        BEGIN
            RAISERROR('A tabela [Silver].[FatoIBCBr] nao existe. Crie a tabela antes de executar a carga.', 16, 1);
        END
        """
    )


def consultar_estado_tabela(cursor, codigo_serie: int) -> Tuple[int, Optional[date]]:
    cursor.execute(
        """
        SELECT
            COUNT(1) AS QtdRegistros,
            MAX(DataReferencia) AS MaxDataReferencia
        FROM [Silver].[FatoIBCBr]
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


def buscar_ibc_br_bcb(
    data_inicial: date,
    data_final: date,
    ajuste_sazonal: bool = True,
) -> Tuple[List[Dict[str, Any]], str, int, str, str]:
    codigo_serie = 24364 if ajuste_sazonal else 24363

    nome_serie = "IBC-Br com ajuste sazonal" if ajuste_sazonal else "IBC-Br sem ajuste sazonal"
    tipo_ajuste_sazonal = "COM_AJUSTE_SAZONAL" if ajuste_sazonal else "SEM_AJUSTE_SAZONAL"

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
        valor_indice = converter_decimal_bcb(item["valor"])

        registros.append(
            {
                "data_referencia": data_referencia,
                "ibc_br_indice": valor_indice,
            }
        )

    registros.sort(key=lambda x: x["data_referencia"])

    return registros, url, codigo_serie, nome_serie, tipo_ajuste_sazonal


def imprimir_ultima_data_bcb(registros: List[Dict[str, Any]], hoje: date) -> None:
    """Loga a última competência disponível retornada pela API do Banco Central."""

    print("=" * 100)
    print("ULTIMA DATA DISPONIVEL NA API DO BANCO CENTRAL")
    print("=" * 100)

    if not registros:
        print("Nenhum registro retornado pela API do Banco Central.")
        return

    ultima_data_bcb = max(registro["data_referencia"] for registro in registros)

    print(f"Data final solicitada: {hoje}")
    print(f"Ultima DataReferencia retornada pelo BCB: {ultima_data_bcb}")
    print(f"Ultimo MesAno retornado pelo BCB: {formatar_mes_ano(ultima_data_bcb)}")

    if ultima_data_bcb < primeiro_dia_mes(hoje):
        print(
            "AVISO: A serie do IBC-Br e mensal e pode ter defasagem de publicacao. "
            "Isso nao significa erro na carga. Significa que o Banco Central ainda "
            "nao publicou meses mais recentes nessa serie."
        )


def calcular_campos_ibc_br(
    registros: List[Dict[str, Any]],
    data_inicio_gravacao: date,
    codigo_serie: int,
    nome_serie: str,
    tipo_ajuste_sazonal: str,
    url_consulta: str,
) -> List[Tuple]:
    linhas = []

    fonte = "Banco Central do Brasil - SGS/BCData"
    data_carga = datetime.now().replace(microsecond=0)

    observacao = (
        "IBCBrIndice e indice, nao percentual. "
        "IBCBrVariacaoMensalPct e a variacao percentual calculada contra o mes anterior."
    )

    for i, registro in enumerate(registros):
        data_referencia = registro["data_referencia"]

        if data_referencia < data_inicio_gravacao:
            continue

        valor_atual = registro["ibc_br_indice"]

        valor_anterior = None
        if i >= 1:
            valor_anterior = registros[i - 1]["ibc_br_indice"]

        variacao_mensal_pct = calcular_variacao_pct(valor_atual, valor_anterior)

        if variacao_mensal_pct is not None:
            variacao_mensal_decimal = variacao_mensal_pct / Decimal("100")
            fator_variacao_mensal = Decimal("1") + variacao_mensal_decimal
        else:
            variacao_mensal_decimal = None
            fator_variacao_mensal = None

        variacao_3m_pct = None
        if i >= 3:
            valor_base_3m = registros[i - 3]["ibc_br_indice"]
            variacao_3m_pct = calcular_variacao_pct(valor_atual, valor_base_3m)

        variacao_6m_pct = None
        if i >= 6:
            valor_base_6m = registros[i - 6]["ibc_br_indice"]
            variacao_6m_pct = calcular_variacao_pct(valor_atual, valor_base_6m)

        variacao_12m_pct = None
        if i >= 12:
            valor_base_12m = registros[i - 12]["ibc_br_indice"]
            variacao_12m_pct = calcular_variacao_pct(valor_atual, valor_base_12m)

        media_movel_3m = None
        if i >= 2:
            media_movel_3m = calcular_media(
                [
                    registros[i - 2]["ibc_br_indice"],
                    registros[i - 1]["ibc_br_indice"],
                    registros[i]["ibc_br_indice"],
                ]
            )

        media_movel_6m = None
        if i >= 5:
            media_movel_6m = calcular_media(
                [registros[j]["ibc_br_indice"] for j in range(i - 5, i + 1)]
            )

        media_movel_12m = None
        if i >= 11:
            media_movel_12m = calcular_media(
                [registros[j]["ibc_br_indice"] for j in range(i - 11, i + 1)]
            )

        mes_ano_disponivel_para_uso = formatar_mes_ano(adicionar_meses(data_referencia, 2))

        linha = (
            codigo_serie,
            nome_serie,
            tipo_ajuste_sazonal,
            data_referencia,
            data_referencia.year,
            data_referencia.month,
            formatar_mes_ano(data_referencia),
            q6(valor_atual),
            q6(valor_anterior),
            q6(variacao_mensal_pct),
            q10(variacao_mensal_decimal),
            q10(fator_variacao_mensal),
            q6(variacao_3m_pct),
            q6(variacao_6m_pct),
            q6(variacao_12m_pct),
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


def normalizar_linha_stage_ibc_br(linha: Tuple) -> Tuple:
    """Normaliza campos DECIMAL antes do executemany."""

    valores = list(linha)

    campos_decimal_6 = [7, 8, 9, 12, 13, 14, 15, 16, 17]
    campos_decimal_10 = [10, 11]

    for indice in campos_decimal_6:
        valores[indice] = decimal_para_sql_texto(valores[indice], 6)

    for indice in campos_decimal_10:
        valores[indice] = decimal_para_sql_texto(valores[indice], 10)

    return tuple(valores)


def diagnosticar_linhas_ibc_br(linhas: List[Tuple]) -> None:
    """Loga as primeiras linhas geradas para facilitar validação no Airflow."""

    if not linhas:
        print("Nenhuma linha para diagnosticar.")
        return

    print("=" * 100)
    print("DIAGNOSTICO DAS PRIMEIRAS LINHAS GERADAS PARA O IBC-BR")
    print("=" * 100)

    for idx, linha in enumerate(linhas[:5], start=1):
        print(f"Linha {idx}:")
        print(f"  CodigoSerieSGS: {linha[0]}")
        print(f"  DataReferencia: {linha[3]}")
        print(f"  MesAno: {linha[6]}")
        print(f"  IBCBrIndice: {linha[7]}")
        print(f"  IBCBrIndiceMesAnterior: {linha[8]}")
        print(f"  IBCBrVariacaoMensalPct: {linha[9]}")
        print(f"  IBCBrVariacaoMensalDecimal: {linha[10]}")
        print(f"  IBCBrFatorVariacaoMensal: {linha[11]}")
        print("-" * 100)


def upsert_fato_ibc_br(cursor, linhas: List[Tuple]) -> int:
    if not linhas:
        return 0

    try:
        cursor.fast_executemany = False
    except Exception:
        pass

    cursor.execute(
        """
        IF OBJECT_ID('tempdb..#StageFatoIBCBr') IS NOT NULL
        BEGIN
            DROP TABLE #StageFatoIBCBr;
        END
        """
    )

    cursor.execute(
        """
        CREATE TABLE #StageFatoIBCBr
        (
            [CodigoSerieSGS] INT NOT NULL,
            [NomeSerie] VARCHAR(150) NOT NULL,
            [TipoAjusteSazonal] VARCHAR(30) NOT NULL,
            [DataReferencia] DATE NOT NULL,
            [Ano] SMALLINT NOT NULL,
            [Mes] TINYINT NOT NULL,
            [MesAno] CHAR(7) NOT NULL,
            [IBCBrIndice] DECIMAL(18,6) NOT NULL,
            [IBCBrIndiceMesAnterior] DECIMAL(18,6) NULL,
            [IBCBrVariacaoMensalPct] DECIMAL(18,6) NULL,
            [IBCBrVariacaoMensalDecimal] DECIMAL(18,10) NULL,
            [IBCBrFatorVariacaoMensal] DECIMAL(18,10) NULL,
            [IBCBrVariacao3MPct] DECIMAL(18,6) NULL,
            [IBCBrVariacao6MPct] DECIMAL(18,6) NULL,
            [IBCBrVariacao12MPct] DECIMAL(18,6) NULL,
            [IBCBrMediaMovel3M] DECIMAL(18,6) NULL,
            [IBCBrMediaMovel6M] DECIMAL(18,6) NULL,
            [IBCBrMediaMovel12M] DECIMAL(18,6) NULL,
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
        INSERT INTO #StageFatoIBCBr
        (
            CodigoSerieSGS,
            NomeSerie,
            TipoAjusteSazonal,
            DataReferencia,
            Ano,
            Mes,
            MesAno,
            IBCBrIndice,
            IBCBrIndiceMesAnterior,
            IBCBrVariacaoMensalPct,
            IBCBrVariacaoMensalDecimal,
            IBCBrFatorVariacaoMensal,
            IBCBrVariacao3MPct,
            IBCBrVariacao6MPct,
            IBCBrVariacao12MPct,
            IBCBrMediaMovel3M,
            IBCBrMediaMovel6M,
            IBCBrMediaMovel12M,
            MesAnoDisponivelParaUso,
            Observacao,
            Fonte,
            UrlConsulta,
            DataCarga,
            BitAtivo
        )
        VALUES
        (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        );
    """

    linhas_normalizadas = [normalizar_linha_stage_ibc_br(linha) for linha in linhas]
    cursor.executemany(insert_stage, linhas_normalizadas)

    cursor.execute(
        """
        MERGE [Silver].[FatoIBCBr] AS Destino
        USING #StageFatoIBCBr AS Origem
            ON Destino.CodigoSerieSGS = Origem.CodigoSerieSGS
           AND Destino.DataReferencia = Origem.DataReferencia

        WHEN MATCHED THEN
            UPDATE SET
                Destino.NomeSerie = Origem.NomeSerie,
                Destino.TipoAjusteSazonal = Origem.TipoAjusteSazonal,
                Destino.Ano = Origem.Ano,
                Destino.Mes = Origem.Mes,
                Destino.MesAno = Origem.MesAno,
                Destino.IBCBrIndice = Origem.IBCBrIndice,
                Destino.IBCBrIndiceMesAnterior = Origem.IBCBrIndiceMesAnterior,
                Destino.IBCBrVariacaoMensalPct = Origem.IBCBrVariacaoMensalPct,
                Destino.IBCBrVariacaoMensalDecimal = Origem.IBCBrVariacaoMensalDecimal,
                Destino.IBCBrFatorVariacaoMensal = Origem.IBCBrFatorVariacaoMensal,
                Destino.IBCBrVariacao3MPct = Origem.IBCBrVariacao3MPct,
                Destino.IBCBrVariacao6MPct = Origem.IBCBrVariacao6MPct,
                Destino.IBCBrVariacao12MPct = Origem.IBCBrVariacao12MPct,
                Destino.IBCBrMediaMovel3M = Origem.IBCBrMediaMovel3M,
                Destino.IBCBrMediaMovel6M = Origem.IBCBrMediaMovel6M,
                Destino.IBCBrMediaMovel12M = Origem.IBCBrMediaMovel12M,
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
                TipoAjusteSazonal,
                DataReferencia,
                Ano,
                Mes,
                MesAno,
                IBCBrIndice,
                IBCBrIndiceMesAnterior,
                IBCBrVariacaoMensalPct,
                IBCBrVariacaoMensalDecimal,
                IBCBrFatorVariacaoMensal,
                IBCBrVariacao3MPct,
                IBCBrVariacao6MPct,
                IBCBrVariacao12MPct,
                IBCBrMediaMovel3M,
                IBCBrMediaMovel6M,
                IBCBrMediaMovel12M,
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
                Origem.TipoAjusteSazonal,
                Origem.DataReferencia,
                Origem.Ano,
                Origem.Mes,
                Origem.MesAno,
                Origem.IBCBrIndice,
                Origem.IBCBrIndiceMesAnterior,
                Origem.IBCBrVariacaoMensalPct,
                Origem.IBCBrVariacaoMensalDecimal,
                Origem.IBCBrFatorVariacaoMensal,
                Origem.IBCBrVariacao3MPct,
                Origem.IBCBrVariacao6MPct,
                Origem.IBCBrVariacao12MPct,
                Origem.IBCBrMediaMovel3M,
                Origem.IBCBrMediaMovel6M,
                Origem.IBCBrMediaMovel12M,
                Origem.MesAnoDisponivelParaUso,
                Origem.Observacao,
                Origem.Fonte,
                Origem.UrlConsulta,
                Origem.DataCarga,
                Origem.BitAtivo
            );
        """
    )

    cursor.execute("DROP TABLE #StageFatoIBCBr;")

    return len(linhas)


def executar_carga_ibc_br(
    conn_id_sql_server: str = CONN_ID_SQL_SERVER,
    ajuste_sazonal: bool = AJUSTE_SAZONAL_PADRAO,
) -> Dict[str, Any]:
    HookSqlServer = importar_hook_sql_server()
    hook_sql_server = HookSqlServer(conn_id=conn_id_sql_server)
    engine = hook_sql_server.obter_engine()

    hoje = date.today()
    codigo_serie = 24364 if ajuste_sazonal else 24363

    conexao = engine.raw_connection()

    try:
        cursor = conexao.cursor()

        validar_database_ativo(cursor)
        validar_tabela_fato_ibc_br(cursor)
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
                "recalculo de variacoes e busca de meses faltantes ate hoje."
            )

        data_inicio_consulta = adicionar_meses(data_inicio_gravacao, -12)

        registros_bcb, url_consulta, codigo_serie, nome_serie, tipo_ajuste_sazonal = buscar_ibc_br_bcb(
            data_inicial=data_inicio_consulta,
            data_final=hoje,
            ajuste_sazonal=ajuste_sazonal,
        )

        imprimir_ultima_data_bcb(registros_bcb, hoje)

        linhas = calcular_campos_ibc_br(
            registros=registros_bcb,
            data_inicio_gravacao=data_inicio_gravacao,
            codigo_serie=codigo_serie,
            nome_serie=nome_serie,
            tipo_ajuste_sazonal=tipo_ajuste_sazonal,
            url_consulta=url_consulta,
        )

        diagnosticar_linhas_ibc_br(linhas)
        qtd_linhas_upsert = upsert_fato_ibc_br(cursor, linhas)

        conexao.commit()

        qtd_depois, max_data_depois = consultar_estado_tabela(cursor, codigo_serie)

        resumo = {
            "status": "OK",
            "serie": nome_serie,
            "codigo_sgs": codigo_serie,
            "tipo_ajuste_sazonal": tipo_ajuste_sazonal,
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
                "IBCBrIndice e indice, nao percentual. "
                "O percentual gravado e IBCBrVariacaoMensalPct, calculado por "
                "((IBC atual / IBC anterior) - 1) * 100."
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
    description="Obtém o IBC-Br mensal do Banco Central e realiza upsert em [DataMining].[Silver].[FatoIBCBr] no SQL Server.",
    schedule="0 9,19 * * *",
    start_date=pendulum.datetime(2026, 6, 11, tz=TZ_SAO_PAULO),
    catchup=False,
    max_active_runs=1,
    tags=["bcb", "sgs", "ibc-br", "atividade-economica", "sql-server", "silver", "macroeconomia"],
    default_args={
        "owner": "midia",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
    },
    doc_md=DAG_DOC_MD,
)
def pipeline_obter_ibcbr():
    @task(task_id="testar_conexao_sql_server")
    def testar_conexao_sql_server() -> str:
        HookSqlServer = importar_hook_sql_server()
        hook_sql_server = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
        mensagem = hook_sql_server.testar_conexao()
        print(mensagem)
        return mensagem

    @task(task_id="obter_e_gravar_ibcbr")
    def obter_e_gravar_ibcbr() -> Dict[str, Any]:
        return executar_carga_ibc_br(
            conn_id_sql_server=CONN_ID_SQL_SERVER,
            ajuste_sazonal=AJUSTE_SAZONAL_PADRAO,
        )

    testar_conexao_sql_server() >> obter_e_gravar_ibcbr()


pipeline_obter_ibcbr_dag = pipeline_obter_ibcbr()
