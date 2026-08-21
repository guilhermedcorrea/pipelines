;WITH contratos AS (
    SELECT DISTINCT
        c.IDFAtoControleContratoMidia,
        c.CodPonto,
        c.DataInicioPrevisto,
        c.DataTerminoPrevisto,
        c.TotalLiquidoContratoAGBRCTACORDO,
        c.AtivoCancelamento
    FROM Integracao.Silver.FatoControleContratosItensMidia c
    WHERE c.AtivoCancelamento = 'A'
      AND c.DataInicioPrevisto >= ?
      AND c.DataTerminoPrevisto <  ?
      AND c.CodPonto = ?
),
contratos_com_custo AS (
    SELECT
        c.*,
        custo.AnoCusto,
        custo.ValorMensal
    FROM contratos c
    OUTER APPLY (
        SELECT TOP (1)
            AnoCusto    = d.Ano,
            ValorMensal = CAST(d.Valor AS DECIMAL(18,10))
        FROM Integracao.Silver.DimCustoPainel d
        WHERE d.CodPonto = c.CodPonto
        ORDER BY
            CASE WHEN d.Ano <= YEAR(c.DataInicioPrevisto) THEN 0 ELSE 1 END,
            ABS(d.Ano - YEAR(c.DataInicioPrevisto)),
            d.Ano DESC,
            d.DataCarga DESC,
            d.IDDimCustoPainel DESC
    ) custo
),

contrato_dia AS (
    SELECT
        c.IDFAtoControleContratoMidia,
        c.CodPonto,
        c.DataInicioPrevisto,
        c.DataTerminoPrevisto,
        c.TotalLiquidoContratoAGBRCTACORDO,
        c.AtivoCancelamento,
        c.AnoCusto,
        c.ValorMensal,
        dcal.Data,
        DiasNoMes = DAY(EOMONTH(dcal.Data)),
        CustoDiaPainel =
            CASE
                WHEN c.ValorMensal IS NULL THEN CAST(0 AS DECIMAL(18,10))
                ELSE c.ValorMensal / CAST(DAY(EOMONTH(dcal.Data)) AS DECIMAL(18,10))
            END
    FROM contratos_com_custo c
    JOIN Integracao.Silver.DimCalendario dcal
      ON dcal.Data >= c.DataInicioPrevisto
     AND dcal.Data <= c.DataTerminoPrevisto
),
base_rateio AS (
    SELECT
        cd.*,
        SomaReceitaDia =
            SUM(
                CASE
                    WHEN cd.TotalLiquidoContratoAGBRCTACORDO > 0 THEN cd.TotalLiquidoContratoAGBRCTACORDO
                    ELSE 0
                END
            ) OVER (PARTITION BY cd.CodPonto, cd.Data),
        QtdContratosDia =
            COUNT(*) OVER (PARTITION BY cd.CodPonto, cd.Data)
    FROM contrato_dia cd
),

custo_dia_contrato AS (
    SELECT
        b.*,
        CustoDiaContrato =
            CASE
               
                WHEN b.SomaReceitaDia > 0 AND b.TotalLiquidoContratoAGBRCTACORDO > 0 THEN
                    b.CustoDiaPainel
                    * (CAST(b.TotalLiquidoContratoAGBRCTACORDO AS DECIMAL(18,10)) / CAST(b.SomaReceitaDia AS DECIMAL(18,10)))

             
                WHEN b.QtdContratosDia > 0 THEN
                    b.CustoDiaPainel / CAST(b.QtdContratosDia AS DECIMAL(18,10))

                ELSE CAST(0 AS DECIMAL(18,10))
            END
    FROM base_rateio b
),

custo_por_contrato AS (
    SELECT
        IDFAtoControleContratoMidia,
        CodPonto,
        DataInicioPrevisto,
        DataTerminoPrevisto,
        AtivoCancelamento,
        AnoCusto,
        TotalLiquidoContratoAGBRCTACORDO,
        CustoContrato = CAST(ROUND(SUM(CustoDiaContrato), 2) AS DECIMAL(18,2))
    FROM custo_dia_contrato
    GROUP BY
        IDFAtoControleContratoMidia,
        CodPonto,
        DataInicioPrevisto,
        DataTerminoPrevisto,
        AtivoCancelamento,
        AnoCusto,
        TotalLiquidoContratoAGBRCTACORDO
),
totais AS (
    SELECT
        QtdContratos = COUNT(*),
        ReceitaPeriodo = CAST(ROUND(SUM(CAST(TotalLiquidoContratoAGBRCTACORDO AS DECIMAL(18,10))), 2) AS DECIMAL(18,2)),
        CustoPeriodo = CAST(ROUND(SUM(CAST(CustoContrato AS DECIMAL(18,10))), 2) AS DECIMAL(18,2))
    FROM custo_por_contrato
)
SELECT
    t.QtdContratos,
    t.ReceitaPeriodo,
    t.CustoPeriodo,
    LucroPeriodo = CAST(ROUND(t.ReceitaPeriodo - t.CustoPeriodo, 2) AS DECIMAL(18,2)),
    MargemPct =
        CAST(ROUND(
            CASE
                WHEN t.ReceitaPeriodo IS NULL OR t.ReceitaPeriodo = 0 THEN NULL
                ELSE ((t.ReceitaPeriodo - t.CustoPeriodo) / t.ReceitaPeriodo) * 100
            END
        , 2) AS DECIMAL(18,2))
FROM totais t;