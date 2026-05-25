from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pendulum
from airflow.sdk import dag, task
from sqlalchemy import text

from hooks.BancodeDados.SqlServer import HookSqlServer


DOCUMENTACAO_DAG = """
# pipeline_verifica_preferencia_reserva

Este DAG sincroniza a preferência/reserva comercial dos contratos da Euromídia.

## Regra principal de status do contrato

Antes de sincronizar preferência/reserva, o DAG atualiza
`Silver.FatoControleContratosEuromidia.IDDimStatusContratos`
olhando somente as datas dos itens do contrato:

- Se existir item com:
  - DataInicioPrevisto <= hoje
  - DataTerminoPrevisto >= hoje

Então o contrato recebe:

- IDDimStatusContratos = 7

Se não houver item válido hoje e a maior DataTerminoPrevisto do contrato já passou:

- IDDimStatusContratos = 8

O DAG não usa DataLancamento para essa regra.

O DAG não usa BitAtivo para essa regra.

## Regra de preferência/reserva

Um item entra em preferência/reserva quando:

1. O contrato está com `IDDimStatusContratos = 7`.
2. O contrato está ativo (`BitAtivo = 1`).
3. O item do contrato está ativo (`BitAtivo = 1`).
4. DataInicioPrevisto está preenchida.
5. DataTerminoPrevisto está preenchida.
6. DataTerminoPrevisto >= DataInicioPrevisto.
7. O item está vigente na data atual:
   - DataInicioPrevisto <= hoje
   - DataTerminoPrevisto >= hoje
8. O período comercial do item é de 6 meses ou mais:
   - DataTerminoPrevisto >= DATEADD(DAY, -1, DATEADD(MONTH, 6, DataInicioPrevisto))

## O que o DAG faz

1. Atualiza o status do cabeçalho do contrato com base nas datas dos itens.
2. Marca `Silver.FatoControleContratosItensEuromidia.BitPreferencia = 1` para itens elegíveis.
3. Marca `BitPreferencia = 0` para itens que deixaram de ser elegíveis.
4. Insere/atualiza o cabeçalho em `Silver.FatoPreferenciaReserva`.
5. Insere/atualiza os itens em `Silver.FatoPreferenciaReservaItens`.
6. Remove das tabelas de preferência os contratos/itens que deixaram de atender os critérios.

## Observações importantes

A tabela `Silver.FatoPreferenciaReserva.IDUsuario` é preenchida a partir de
`Integracao.dbo.Vendedores.IDDimUsuarios`, usando o `IDVendedor` do item elegível.

A tabela `Silver.FatoPreferenciaReservaItens` não possui a coluna
`IDFatoControleContratosItensEuromidia`. Por isso, a sincronização dos itens usa uma chave lógica.
Para permitir renovação/expansão de prazo sem gerar duplicidade, o DAG tenta atualizar o item existente
por contrato + DataInicioPrevisto + painel + face e altera a DataTerminoPrevisto quando ela mudar.

Se no futuro existir mais de um item igual no mesmo contrato com mesmo painel, face e data inicial,
o ideal técnico é adicionar `IDFatoControleContratosItensEuromidia` na tabela de destino.
"""


@dataclass(frozen=True)
class ConfiguracaoPreferenciaReserva:
    """Configuração central do pipeline de preferência/reserva."""

    conn_id_sql_integracao: str = "mssql_integracao"

    tabela_contratos: str = "[Integracao].[Silver].[FatoControleContratosEuromidia]"
    tabela_contratos_itens: str = "[Integracao].[Silver].[FatoControleContratosItensEuromidia]"

    tabela_preferencia: str = "[Integracao].[Silver].[FatoPreferenciaReserva]"
    tabela_preferencia_itens: str = "[Integracao].[Silver].[FatoPreferenciaReservaItens]"

    tabela_vendedores: str = "[Integracao].[dbo].[Vendedores]"

    status_contrato_ativo: int = 7
    status_contrato_concluido: int = 8
    status_terminal_cancelado: int = 9
    status_terminal_erro: int = 10
    meses_minimos_preferencia: int = 6


def criar_engine_sql(conn_id: str):
    """Eu crio a engine SQL Server usando o hook customizado do projeto."""
    hook_sql_server = HookSqlServer(conn_id=conn_id)
    return hook_sql_server.obter_engine()


def normalizar_rowcount(resultado) -> int:
    """Eu normalizo o rowcount do SQLAlchemy/pyodbc para evitar retorno negativo."""
    rowcount = getattr(resultado, "rowcount", 0)

    if rowcount is None or rowcount < 0:
        return 0

    return int(rowcount)


def executar_select_unico(conexao, sql: str) -> dict[str, Any]:
    """Eu executo um SELECT único de forma segura em batches do SQL Server.

    O SQL Server/pyodbc pode devolver primeiro um result set fechado quando o batch
    tem SET, DECLARE, IF, DROP, UPDATE, INSERT ou CREATE antes do SELECT final.
    Por isso eu avanço entre os result sets até encontrar um que realmente tenha
    colunas e linhas para leitura.
    """
    cursor = conexao.connection.cursor()

    try:
        cursor.execute(sql)

        while True:
            if cursor.description is not None:
                colunas = [coluna[0] for coluna in cursor.description]
                linha = cursor.fetchone()

                if linha is None:
                    return {}

                return dict(zip(colunas, linha))

            if not cursor.nextset():
                return {}

    finally:
        cursor.close()


def validar_estrutura_tabelas() -> dict[str, Any]:
    """Eu valido se as tabelas e colunas necessárias existem antes da sincronização."""
    config = ConfiguracaoPreferenciaReserva()
    engine = criar_engine_sql(conn_id=config.conn_id_sql_integracao)

    sql_validacao = """
    SET NOCOUNT ON;

    ;WITH colunas_obrigatorias AS
    (
        SELECT *
        FROM
        (
            VALUES
                (N'FatoControleContratosEuromidia', N'IDFatoControleContratosEuromidia'),
                (N'FatoControleContratosEuromidia', N'DataAtualizacao'),
                (N'FatoControleContratosEuromidia', N'IDEmpresa'),
                (N'FatoControleContratosEuromidia', N'IDDimStatusContratos'),
                (N'FatoControleContratosEuromidia', N'BitAtivo'),
                (N'FatoControleContratosEuromidia', N'TotalLiquidoContratoAGBRVENDGERCOOR'),
                (N'FatoControleContratosEuromidia', N'MarcaExibida'),

                (N'FatoControleContratosItensEuromidia', N'IDFatoControleContratosItensEuromidia'),
                (N'FatoControleContratosItensEuromidia', N'IDFatoControleContratoEuromidia'),
                (N'FatoControleContratosItensEuromidia', N'DataInicioPrevisto'),
                (N'FatoControleContratosItensEuromidia', N'DataTerminoPrevisto'),
                (N'FatoControleContratosItensEuromidia', N'IDPainelEuromidia'),
                (N'FatoControleContratosItensEuromidia', N'IDDimFacesPaineis'),
                (N'FatoControleContratosItensEuromidia', N'IDVendedor'),
                (N'FatoControleContratosItensEuromidia', N'BitAtivo'),
                (N'FatoControleContratosItensEuromidia', N'BitPreferencia'),

                (N'Vendedores', N'IDVendedor'),
                (N'Vendedores', N'IDDimUsuarios'),

                (N'FatoPreferenciaReserva', N'IDFatoPreferenciaReserva'),
                (N'FatoPreferenciaReserva', N'IDFatoControleContratosEuromidia'),
                (N'FatoPreferenciaReserva', N'IDEmpresa'),
                (N'FatoPreferenciaReserva', N'IDVendedor'),
                (N'FatoPreferenciaReserva', N'IDUsuario'),
                (N'FatoPreferenciaReserva', N'TotalLiquidoContrato'),
                (N'FatoPreferenciaReserva', N'MarcaExibida'),
                (N'FatoPreferenciaReserva', N'BitAtivo'),
                (N'FatoPreferenciaReserva', N'DataCriacao'),
                (N'FatoPreferenciaReserva', N'DataAtualizacao'),

                (N'FatoPreferenciaReservaItens', N'IDFatoPreferenciaReservaItens'),
                (N'FatoPreferenciaReservaItens', N'IDFatoPreferenciaReserva'),
                (N'FatoPreferenciaReservaItens', N'DataInicioPrevisto'),
                (N'FatoPreferenciaReservaItens', N'DataTerminoPrevisto'),
                (N'FatoPreferenciaReservaItens', N'IDPainelEuromidia'),
                (N'FatoPreferenciaReservaItens', N'IDDimFacesPaineis'),
                (N'FatoPreferenciaReservaItens', N'BitAtivo'),
                (N'FatoPreferenciaReservaItens', N'DataCriacao'),
                (N'FatoPreferenciaReservaItens', N'DataAtualizacao')
        ) AS dados(NomeTabela, NomeColuna)
    ),
    colunas_encontradas AS
    (
        SELECT
            t.name AS NomeTabela,
            c.name AS NomeColuna
        FROM [Integracao].sys.tables AS t
        INNER JOIN [Integracao].sys.schemas AS s
            ON s.schema_id = t.schema_id
        INNER JOIN [Integracao].sys.columns AS c
            ON c.object_id = t.object_id
        WHERE
            (
                s.name = N'Silver'
                AND t.name IN
                (
                    N'FatoControleContratosEuromidia',
                    N'FatoControleContratosItensEuromidia',
                    N'FatoPreferenciaReserva',
                    N'FatoPreferenciaReservaItens'
                )
            )
            OR
            (
                s.name = N'dbo'
                AND t.name = N'Vendedores'
            )
    ),
    colunas_faltantes AS
    (
        SELECT
            obrigatoria.NomeTabela,
            obrigatoria.NomeColuna
        FROM colunas_obrigatorias AS obrigatoria
        LEFT JOIN colunas_encontradas AS encontrada
            ON encontrada.NomeTabela = obrigatoria.NomeTabela
           AND encontrada.NomeColuna = obrigatoria.NomeColuna
        WHERE encontrada.NomeColuna IS NULL
    )
    SELECT
        COUNT(1) AS QtdColunasFaltantes,
        STRING_AGG(CONCAT(NomeTabela, N'.', NomeColuna), N'; ') AS ColunasFaltantes
    FROM colunas_faltantes;
    """

    try:
        with engine.begin() as conexao:
            validacao = executar_select_unico(conexao, sql_validacao)

        qtd_colunas_faltantes = int(validacao.get("QtdColunasFaltantes") or 0)
        colunas_faltantes = validacao.get("ColunasFaltantes")

        if qtd_colunas_faltantes > 0:
            raise RuntimeError(
                "Estrutura inválida para o DAG pipeline_verifica_preferencia_reserva. "
                f"Colunas faltantes: {colunas_faltantes}"
            )

        return {
            "status": "ok",
            "qtd_colunas_faltantes": qtd_colunas_faltantes,
            "colunas_faltantes": colunas_faltantes,
        }

    finally:
        engine.dispose()


def sincronizar_status_contratos_por_periodo(conexao, config: ConfiguracaoPreferenciaReserva) -> dict[str, int]:
    """Eu atualizo o status do contrato olhando somente DataInicioPrevisto e DataTerminoPrevisto."""

    sql_preparar_status = f"""
    SET NOCOUNT ON;

    DECLARE @Hoje DATE = CAST(GETDATE() AS DATE);

    IF OBJECT_ID('tempdb..#StatusContratosAtualizar') IS NOT NULL
    BEGIN
        DROP TABLE #StatusContratosAtualizar;
    END;

    ;WITH StatusContrato AS
    (
        SELECT
            item.IDFatoControleContratoEuromidia,

            NovoStatus =
                CASE
                    WHEN MAX(
                        CASE
                            WHEN CAST(item.DataInicioPrevisto AS DATE) <= @Hoje
                             AND CAST(item.DataTerminoPrevisto AS DATE) >= @Hoje
                            THEN 1
                            ELSE 0
                        END
                    ) = 1
                    THEN {config.status_contrato_ativo}

                    WHEN MAX(CAST(item.DataTerminoPrevisto AS DATE)) < @Hoje
                    THEN {config.status_contrato_concluido}

                    ELSE NULL
                END
        FROM {config.tabela_contratos_itens} AS item
        GROUP BY
            item.IDFatoControleContratoEuromidia
    )
    SELECT
        contrato.IDFatoControleContratosEuromidia,
        contrato.IDDimStatusContratos AS StatusAtual,
        status_contrato.NovoStatus
    INTO #StatusContratosAtualizar
    FROM {config.tabela_contratos} AS contrato
    INNER JOIN StatusContrato AS status_contrato
        ON status_contrato.IDFatoControleContratoEuromidia = contrato.IDFatoControleContratosEuromidia
    WHERE
        status_contrato.NovoStatus IS NOT NULL
        AND ISNULL(contrato.IDDimStatusContratos, -1) NOT IN
        (
            {config.status_terminal_cancelado},
            {config.status_terminal_erro}
        )
        AND ISNULL(contrato.IDDimStatusContratos, -1) <> status_contrato.NovoStatus;
    """

    sql_atualizar_status = f"""
    SET NOCOUNT ON;

    UPDATE contrato
       SET contrato.IDDimStatusContratos = status_atualizar.NovoStatus,
           contrato.DataAtualizacao = GETDATE()
    FROM {config.tabela_contratos} AS contrato
    INNER JOIN #StatusContratosAtualizar AS status_atualizar
        ON status_atualizar.IDFatoControleContratosEuromidia = contrato.IDFatoControleContratosEuromidia;
    """

    sql_resumo_status = f"""
    SET NOCOUNT ON;

    SELECT
        COUNT(1) AS QtdContratosStatusAtualizados,
        SUM(CASE WHEN NovoStatus = {config.status_contrato_ativo} THEN 1 ELSE 0 END) AS QtdContratosMarcadosAtivos,
        SUM(CASE WHEN NovoStatus = {config.status_contrato_concluido} THEN 1 ELSE 0 END) AS QtdContratosMarcadosConcluidos
    FROM #StatusContratosAtualizar;
    """

    conexao.execute(text(sql_preparar_status))
    conexao.execute(text(sql_atualizar_status))
    resumo = executar_select_unico(conexao, sql_resumo_status)

    return {
        "qtd_contratos_status_atualizados": int(resumo.get("QtdContratosStatusAtualizados") or 0),
        "qtd_contratos_marcados_ativos_7": int(resumo.get("QtdContratosMarcadosAtivos") or 0),
        "qtd_contratos_marcados_concluidos_8": int(resumo.get("QtdContratosMarcadosConcluidos") or 0),
    }


def criar_tabelas_temporarias_preferencia(conexao, config: ConfiguracaoPreferenciaReserva) -> dict[str, int]:
    """Eu monto as tabelas temporárias com contratos e itens elegíveis para preferência."""

    sql_criar_temporarias = f"""
    SET NOCOUNT ON;

    DECLARE @DataHoje DATE = CAST(GETDATE() AS DATE);

    IF OBJECT_ID('tempdb..#ItensPreferenciaReserva') IS NOT NULL
    BEGIN
        DROP TABLE #ItensPreferenciaReserva;
    END;

    IF OBJECT_ID('tempdb..#ContratosPreferenciaReserva') IS NOT NULL
    BEGIN
        DROP TABLE #ContratosPreferenciaReserva;
    END;

    CREATE TABLE #ItensPreferenciaReserva
    (
        IDFatoControleContratosItensEuromidia INT NOT NULL,
        IDFatoControleContratoEuromidia INT NOT NULL,
        DataInicioPrevisto DATE NOT NULL,
        DataTerminoPrevisto DATE NOT NULL,
        IDPainelEuromidia INT NULL,
        IDDimFacesPaineis INT NULL,
        IDVendedor INT NULL,
        IDUsuario INT NULL
    );

    INSERT INTO #ItensPreferenciaReserva
    (
        IDFatoControleContratosItensEuromidia,
        IDFatoControleContratoEuromidia,
        DataInicioPrevisto,
        DataTerminoPrevisto,
        IDPainelEuromidia,
        IDDimFacesPaineis,
        IDVendedor,
        IDUsuario
    )
    SELECT
        item.IDFatoControleContratosItensEuromidia,
        item.IDFatoControleContratoEuromidia,
        CAST(item.DataInicioPrevisto AS DATE) AS DataInicioPrevisto,
        CAST(item.DataTerminoPrevisto AS DATE) AS DataTerminoPrevisto,
        item.IDPainelEuromidia,
        item.IDDimFacesPaineis,
        item.IDVendedor,
        vendedor.IDDimUsuarios AS IDUsuario
    FROM {config.tabela_contratos_itens} AS item
    INNER JOIN {config.tabela_contratos} AS contrato
        ON contrato.IDFatoControleContratosEuromidia = item.IDFatoControleContratoEuromidia
    LEFT JOIN {config.tabela_vendedores} AS vendedor
        ON vendedor.IDVendedor = item.IDVendedor
    WHERE
        ISNULL(contrato.IDDimStatusContratos, -1) = {config.status_contrato_ativo}
        AND ISNULL(contrato.BitAtivo, 0) = 1
        AND ISNULL(item.BitAtivo, 0) = 1
        AND item.DataInicioPrevisto IS NOT NULL
        AND item.DataTerminoPrevisto IS NOT NULL
        AND CAST(item.DataTerminoPrevisto AS DATE) >= CAST(item.DataInicioPrevisto AS DATE)
        AND CAST(item.DataInicioPrevisto AS DATE) <= @DataHoje
        AND CAST(item.DataTerminoPrevisto AS DATE) >= @DataHoje
        AND CAST(item.DataTerminoPrevisto AS DATE) >= DATEADD
        (
            DAY,
            -1,
            DATEADD(MONTH, {config.meses_minimos_preferencia}, CAST(item.DataInicioPrevisto AS DATE))
        );

    CREATE CLUSTERED INDEX IX_TMP_ItensPreferenciaReserva_Item
    ON #ItensPreferenciaReserva (IDFatoControleContratosItensEuromidia);

    CREATE NONCLUSTERED INDEX IX_TMP_ItensPreferenciaReserva_Contrato
    ON #ItensPreferenciaReserva
    (
        IDFatoControleContratoEuromidia,
        DataInicioPrevisto,
        DataTerminoPrevisto,
        IDPainelEuromidia,
        IDDimFacesPaineis
    );

    CREATE NONCLUSTERED INDEX IX_TMP_ItensPreferenciaReserva_Renovacao
    ON #ItensPreferenciaReserva
    (
        IDFatoControleContratoEuromidia,
        DataInicioPrevisto,
        IDPainelEuromidia,
        IDDimFacesPaineis
    )
    INCLUDE
    (
        DataTerminoPrevisto,
        IDVendedor,
        IDUsuario
    );

    CREATE TABLE #ContratosPreferenciaReserva
    (
        IDFatoControleContratosEuromidia INT NOT NULL PRIMARY KEY,
        IDEmpresa INT NULL,
        IDVendedor INT NULL,
        IDUsuario INT NULL,
        TotalLiquidoContrato DECIMAL(18, 2) NULL,
        MarcaExibida NVARCHAR(255) COLLATE DATABASE_DEFAULT NULL
    );

    ;WITH contratos_elegiveis AS
    (
        SELECT DISTINCT
            item.IDFatoControleContratoEuromidia
        FROM #ItensPreferenciaReserva AS item
    ),
    vendedor_contrato AS
    (
        SELECT
            item.IDFatoControleContratoEuromidia,
            item.IDVendedor,
            item.IDUsuario,
            ROW_NUMBER() OVER
            (
                PARTITION BY item.IDFatoControleContratoEuromidia
                ORDER BY
                    CASE WHEN item.IDUsuario IS NULL THEN 1 ELSE 0 END,
                    item.IDFatoControleContratosItensEuromidia DESC
            ) AS OrdemVendedor
        FROM #ItensPreferenciaReserva AS item
    )
    INSERT INTO #ContratosPreferenciaReserva
    (
        IDFatoControleContratosEuromidia,
        IDEmpresa,
        IDVendedor,
        IDUsuario,
        TotalLiquidoContrato,
        MarcaExibida
    )
    SELECT
        contrato.IDFatoControleContratosEuromidia,
        contrato.IDEmpresa,
        vendedor_contrato.IDVendedor,
        vendedor_contrato.IDUsuario,
        CAST(contrato.TotalLiquidoContratoAGBRVENDGERCOOR AS DECIMAL(18, 2)) AS TotalLiquidoContrato,
        CAST(contrato.MarcaExibida AS NVARCHAR(255)) COLLATE DATABASE_DEFAULT AS MarcaExibida
    FROM {config.tabela_contratos} AS contrato
    INNER JOIN contratos_elegiveis AS item_contrato
        ON item_contrato.IDFatoControleContratoEuromidia = contrato.IDFatoControleContratosEuromidia
    LEFT JOIN vendedor_contrato
        ON vendedor_contrato.IDFatoControleContratoEuromidia = contrato.IDFatoControleContratosEuromidia
       AND vendedor_contrato.OrdemVendedor = 1;
    """

    sql_resumo_temporarias = """
    SET NOCOUNT ON;

    SELECT
        (SELECT COUNT(1) FROM #ItensPreferenciaReserva) AS QtdItensElegiveis,
        (SELECT COUNT(1) FROM #ContratosPreferenciaReserva) AS QtdContratosElegiveis;
    """

    conexao.execute(text(sql_criar_temporarias))
    resumo = executar_select_unico(conexao, sql_resumo_temporarias)

    return {
        "qtd_itens_elegiveis": int(resumo.get("QtdItensElegiveis") or 0),
        "qtd_contratos_elegiveis": int(resumo.get("QtdContratosElegiveis") or 0),
    }

def sincronizar_bit_preferencia_origem(conexao, config: ConfiguracaoPreferenciaReserva) -> dict[str, int]:
    """Eu recalculo BitPreferencia na origem e atualizo DataAtualizacao quando houver mudança."""
    sql_marcar_preferencia = f"""
    SET NOCOUNT ON;

    UPDATE item
       SET item.BitPreferencia = 1,
           item.DataAtualizacao = SYSDATETIME()
    FROM {config.tabela_contratos_itens} AS item
    INNER JOIN #ItensPreferenciaReserva AS preferencia
        ON preferencia.IDFatoControleContratosItensEuromidia = item.IDFatoControleContratosItensEuromidia
    WHERE ISNULL(item.BitPreferencia, 0) <> 1;
    """

    sql_desmarcar_preferencia = f"""
    SET NOCOUNT ON;

    UPDATE item
       SET item.BitPreferencia = 0,
           item.DataAtualizacao = SYSDATETIME()
    FROM {config.tabela_contratos_itens} AS item
    WHERE
        (
            item.BitPreferencia IS NULL
            OR item.BitPreferencia <> 0
        )
        AND NOT EXISTS
        (
            SELECT 1
            FROM #ItensPreferenciaReserva AS preferencia
            WHERE preferencia.IDFatoControleContratosItensEuromidia = item.IDFatoControleContratosItensEuromidia
        );
    """

    resultado_marcar = conexao.execute(text(sql_marcar_preferencia))
    resultado_desmarcar = conexao.execute(text(sql_desmarcar_preferencia))

    return {
        "qtd_itens_marcados_bit_preferencia_1": normalizar_rowcount(resultado_marcar),
        "qtd_itens_marcados_bit_preferencia_0": normalizar_rowcount(resultado_desmarcar),
    }


def remover_preferencias_invalidas(conexao, config: ConfiguracaoPreferenciaReserva) -> dict[str, int]:
    """Eu removo da reserva os contratos e itens que deixaram de atender os critérios."""
    sql_remover_itens_invalidos = f"""
    SET NOCOUNT ON;

    DELETE destino_item
    FROM {config.tabela_preferencia_itens} AS destino_item
    INNER JOIN {config.tabela_preferencia} AS destino
        ON destino.IDFatoPreferenciaReserva = destino_item.IDFatoPreferenciaReserva
    WHERE NOT EXISTS
    (
        SELECT 1
        FROM #ItensPreferenciaReserva AS origem_item
        WHERE
            origem_item.IDFatoControleContratoEuromidia = destino.IDFatoControleContratosEuromidia
            AND origem_item.DataInicioPrevisto = destino_item.DataInicioPrevisto
            AND origem_item.DataTerminoPrevisto = destino_item.DataTerminoPrevisto
            AND ISNULL(origem_item.IDPainelEuromidia, -1) = ISNULL(destino_item.IDPainelEuromidia, -1)
            AND ISNULL(origem_item.IDDimFacesPaineis, -1) = ISNULL(destino_item.IDDimFacesPaineis, -1)
    );
    """

    sql_remover_contratos_invalidos = f"""
    SET NOCOUNT ON;

    DELETE destino
    FROM {config.tabela_preferencia} AS destino
    WHERE NOT EXISTS
    (
        SELECT 1
        FROM #ContratosPreferenciaReserva AS origem
        WHERE origem.IDFatoControleContratosEuromidia = destino.IDFatoControleContratosEuromidia
    );
    """

    resultado_itens = conexao.execute(text(sql_remover_itens_invalidos))
    resultado_contratos = conexao.execute(text(sql_remover_contratos_invalidos))

    return {
        "qtd_preferencia_itens_removidos": normalizar_rowcount(resultado_itens),
        "qtd_preferencia_contratos_removidos": normalizar_rowcount(resultado_contratos),
    }


def sincronizar_cabecalho_preferencia(conexao, config: ConfiguracaoPreferenciaReserva) -> dict[str, int]:
    """Eu atualizo e insiro os cabeçalhos de preferência/reserva."""
    sql_atualizar_cabecalhos = f"""
    SET NOCOUNT ON;

    UPDATE destino
       SET destino.IDEmpresa = origem.IDEmpresa,
           destino.IDVendedor = origem.IDVendedor,
           destino.IDUsuario = origem.IDUsuario,
           destino.TotalLiquidoContrato = origem.TotalLiquidoContrato,
           destino.MarcaExibida = origem.MarcaExibida COLLATE DATABASE_DEFAULT,
           destino.BitAtivo = 1,
           destino.DataAtualizacao = SYSDATETIME()
    FROM {config.tabela_preferencia} AS destino
    INNER JOIN #ContratosPreferenciaReserva AS origem
        ON origem.IDFatoControleContratosEuromidia = destino.IDFatoControleContratosEuromidia
    WHERE
        ISNULL(destino.IDEmpresa, -1) <> ISNULL(origem.IDEmpresa, -1)
        OR ISNULL(destino.IDVendedor, -1) <> ISNULL(origem.IDVendedor, -1)
        OR ISNULL(destino.IDUsuario, -1) <> ISNULL(origem.IDUsuario, -1)
        OR ISNULL(destino.TotalLiquidoContrato, -999999999.99) <> ISNULL(origem.TotalLiquidoContrato, -999999999.99)
        OR ISNULL(destino.MarcaExibida COLLATE DATABASE_DEFAULT, N'') <> ISNULL(origem.MarcaExibida COLLATE DATABASE_DEFAULT, N'')
        OR ISNULL(destino.BitAtivo, 0) <> 1;
    """

    sql_inserir_cabecalhos = f"""
    SET NOCOUNT ON;

    INSERT INTO {config.tabela_preferencia}
    (
        IDFatoControleContratosEuromidia,
        IDEmpresa,
        IDVendedor,
        IDUsuario,
        TotalLiquidoContrato,
        MarcaExibida,
        BitAtivo,
        DataCriacao,
        DataAtualizacao
    )
    SELECT
        origem.IDFatoControleContratosEuromidia,
        origem.IDEmpresa,
        origem.IDVendedor,
        origem.IDUsuario,
        origem.TotalLiquidoContrato,
        origem.MarcaExibida COLLATE DATABASE_DEFAULT,
        1 AS BitAtivo,
        SYSDATETIME() AS DataCriacao,
        SYSDATETIME() AS DataAtualizacao
    FROM #ContratosPreferenciaReserva AS origem
    WHERE NOT EXISTS
    (
        SELECT 1
        FROM {config.tabela_preferencia} AS destino
        WHERE destino.IDFatoControleContratosEuromidia = origem.IDFatoControleContratosEuromidia
    );
    """

    resultado_atualizar = conexao.execute(text(sql_atualizar_cabecalhos))
    resultado_inserir = conexao.execute(text(sql_inserir_cabecalhos))

    return {
        "qtd_preferencia_contratos_atualizados": normalizar_rowcount(resultado_atualizar),
        "qtd_preferencia_contratos_inseridos": normalizar_rowcount(resultado_inserir),
    }


def sincronizar_itens_preferencia(conexao, config: ConfiguracaoPreferenciaReserva) -> dict[str, int]:
    """Eu atualizo e insiro os itens de preferência/reserva."""
    sql_atualizar_itens_existentes = f"""
    SET NOCOUNT ON;

    ;WITH origem_unica AS
    (
        SELECT
            origem_item.IDFatoControleContratosItensEuromidia,
            origem_item.IDFatoControleContratoEuromidia,
            origem_item.DataInicioPrevisto,
            origem_item.DataTerminoPrevisto,
            origem_item.IDPainelEuromidia,
            origem_item.IDDimFacesPaineis,
            ROW_NUMBER() OVER
            (
                PARTITION BY
                    origem_item.IDFatoControleContratoEuromidia,
                    origem_item.DataInicioPrevisto,
                    ISNULL(origem_item.IDPainelEuromidia, -1),
                    ISNULL(origem_item.IDDimFacesPaineis, -1)
                ORDER BY
                    origem_item.DataTerminoPrevisto DESC,
                    origem_item.IDFatoControleContratosItensEuromidia DESC
            ) AS OrdemItem
        FROM #ItensPreferenciaReserva AS origem_item
    )
    UPDATE destino_item
       SET destino_item.DataTerminoPrevisto = origem_item.DataTerminoPrevisto,
           destino_item.BitAtivo = 1,
           destino_item.DataAtualizacao = SYSDATETIME()
    FROM {config.tabela_preferencia_itens} AS destino_item
    INNER JOIN {config.tabela_preferencia} AS destino
        ON destino.IDFatoPreferenciaReserva = destino_item.IDFatoPreferenciaReserva
    INNER JOIN origem_unica AS origem_item
        ON origem_item.IDFatoControleContratoEuromidia = destino.IDFatoControleContratosEuromidia
       AND origem_item.OrdemItem = 1
       AND origem_item.DataInicioPrevisto = destino_item.DataInicioPrevisto
       AND ISNULL(origem_item.IDPainelEuromidia, -1) = ISNULL(destino_item.IDPainelEuromidia, -1)
       AND ISNULL(origem_item.IDDimFacesPaineis, -1) = ISNULL(destino_item.IDDimFacesPaineis, -1)
    WHERE
        ISNULL(destino_item.BitAtivo, 0) <> 1
        OR ISNULL(CAST(destino_item.DataTerminoPrevisto AS DATE), '19000101') <> origem_item.DataTerminoPrevisto;
    """

    sql_inserir_itens = f"""
    SET NOCOUNT ON;

    ;WITH origem_unica AS
    (
        SELECT
            origem_item.IDFatoControleContratosItensEuromidia,
            origem_item.IDFatoControleContratoEuromidia,
            origem_item.DataInicioPrevisto,
            origem_item.DataTerminoPrevisto,
            origem_item.IDPainelEuromidia,
            origem_item.IDDimFacesPaineis,
            ROW_NUMBER() OVER
            (
                PARTITION BY
                    origem_item.IDFatoControleContratoEuromidia,
                    origem_item.DataInicioPrevisto,
                    ISNULL(origem_item.IDPainelEuromidia, -1),
                    ISNULL(origem_item.IDDimFacesPaineis, -1)
                ORDER BY
                    origem_item.DataTerminoPrevisto DESC,
                    origem_item.IDFatoControleContratosItensEuromidia DESC
            ) AS OrdemItem
        FROM #ItensPreferenciaReserva AS origem_item
    )
    INSERT INTO {config.tabela_preferencia_itens}
    (
        IDFatoPreferenciaReserva,
        DataInicioPrevisto,
        DataTerminoPrevisto,
        IDPainelEuromidia,
        IDDimFacesPaineis,
        BitAtivo,
        DataCriacao,
        DataAtualizacao
    )
    SELECT
        destino.IDFatoPreferenciaReserva,
        origem_item.DataInicioPrevisto,
        origem_item.DataTerminoPrevisto,
        origem_item.IDPainelEuromidia,
        origem_item.IDDimFacesPaineis,
        1 AS BitAtivo,
        SYSDATETIME() AS DataCriacao,
        SYSDATETIME() AS DataAtualizacao
    FROM origem_unica AS origem_item
    INNER JOIN {config.tabela_preferencia} AS destino
        ON destino.IDFatoControleContratosEuromidia = origem_item.IDFatoControleContratoEuromidia
    WHERE
        origem_item.OrdemItem = 1
        AND NOT EXISTS
        (
            SELECT 1
            FROM {config.tabela_preferencia_itens} AS destino_item
            WHERE
                destino_item.IDFatoPreferenciaReserva = destino.IDFatoPreferenciaReserva
                AND destino_item.DataInicioPrevisto = origem_item.DataInicioPrevisto
                AND ISNULL(destino_item.IDPainelEuromidia, -1) = ISNULL(origem_item.IDPainelEuromidia, -1)
                AND ISNULL(destino_item.IDDimFacesPaineis, -1) = ISNULL(origem_item.IDDimFacesPaineis, -1)
        );
    """

    resultado_atualizar = conexao.execute(text(sql_atualizar_itens_existentes))
    resultado_inserir = conexao.execute(text(sql_inserir_itens))

    return {
        "qtd_preferencia_itens_atualizados": normalizar_rowcount(resultado_atualizar),
        "qtd_preferencia_itens_inseridos": normalizar_rowcount(resultado_inserir),
    }

def executar_pipeline_preferencia_reserva() -> dict[str, Any]:
    """Eu executo a sincronização completa de preferência/reserva dentro de uma transação."""
    config = ConfiguracaoPreferenciaReserva()
    engine = criar_engine_sql(conn_id=config.conn_id_sql_integracao)

    try:
        print("=" * 100)
        print("INÍCIO DO PIPELINE - VERIFICA PREFERÊNCIA / RESERVA")
        print("=" * 100)

        with engine.begin() as conexao:
            resumo_status_contratos = sincronizar_status_contratos_por_periodo(
                conexao=conexao,
                config=config,
            )

            resumo_temporarias = criar_tabelas_temporarias_preferencia(
                conexao=conexao,
                config=config,
            )

            resumo_bit_origem = sincronizar_bit_preferencia_origem(
                conexao=conexao,
                config=config,
            )

            resumo_cabecalho = sincronizar_cabecalho_preferencia(
                conexao=conexao,
                config=config,
            )

            resumo_itens = sincronizar_itens_preferencia(
                conexao=conexao,
                config=config,
            )

            resumo_remocoes = remover_preferencias_invalidas(
                conexao=conexao,
                config=config,
            )

        resumo_final = {
            **resumo_status_contratos,
            **resumo_temporarias,
            **resumo_bit_origem,
            **resumo_remocoes,
            **resumo_cabecalho,
            **resumo_itens,
        }

        print("Resumo da execução:")
        for chave, valor in resumo_final.items():
            print(f"- {chave}: {valor}")

        print("=" * 100)
        print("FIM DO PIPELINE - VERIFICA PREFERÊNCIA / RESERVA")
        print("=" * 100)

        return resumo_final

    finally:
        engine.dispose()


@dag(
    dag_id="pipeline_verifica_preferencia_reserva",
    description="Atualiza status dos contratos por período e sincroniza preferência/reserva.",
    schedule="*/12 * * * *",
    start_date=pendulum.datetime(2026, 5, 12, 0, 0, tz="America/Sao_Paulo"),
    catchup=False,
    tags=["Euromidia", "Contratos", "Preferencia", "Reserva", "SQL Server"],
    max_active_runs=1,
    doc_md=DOCUMENTACAO_DAG,
)
def pipeline_verifica_preferencia_reserva():
    @task(
        task_id="validar_estrutura_tabelas",
        retries=1,
        retry_delay=timedelta(minutes=2),
        execution_timeout=timedelta(minutes=5),
    )
    def tarefa_validar_estrutura_tabelas() -> dict[str, Any]:
        """Eu valido se as tabelas e colunas necessárias existem."""
        return validar_estrutura_tabelas()

    @task(
        task_id="sincronizar_preferencia_reserva",
        retries=1,
        retry_delay=timedelta(minutes=3),
        execution_timeout=timedelta(minutes=20),
    )
    def tarefa_sincronizar_preferencia_reserva() -> dict[str, Any]:
        """Eu atualizo status dos contratos e sincronizo preferência/reserva."""
        return executar_pipeline_preferencia_reserva()

    validacao = tarefa_validar_estrutura_tabelas()
    sincronizacao = tarefa_sincronizar_preferencia_reserva()

    validacao >> sincronizacao


pipeline_verifica_preferencia_reserva_dag = pipeline_verifica_preferencia_reserva()
