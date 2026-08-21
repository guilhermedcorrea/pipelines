SET NOCOUNT ON;

DECLARE @id INT = :id;

DECLARE @dt_ini_in DATE = TRY_CONVERT(date, :dt_ini);
DECLARE @dt_fim_in DATE = TRY_CONVERT(date, :dt_fim);

IF @dt_ini_in IS NULL SET @dt_ini_in = '2024-01-01';
IF @dt_fim_in IS NULL SET @dt_fim_in = CONVERT(date, GETDATE());

DECLARE @dt_ini DATE = DATEFROMPARTS(YEAR(@dt_ini_in), MONTH(@dt_ini_in), 1);
DECLARE @dt_fim DATE = EOMONTH(@dt_fim_in);

------------------------------------------------------------------------
-- CodPonto
------------------------------------------------------------------------
DECLARE @CodPonto INT;

SELECT TOP (1)
    @CodPonto = COALESCE(
        a.CodPonto,
        TRY_CONVERT(INT, NULLIF(LTRIM(RTRIM(CAST(a.ReferenciaExterna AS NVARCHAR(80)))), ''))
    )
FROM Integracao.Silver.DimAtivos a WITH (NOLOCK)
WHERE a.IDDimAtivos = @id;

IF @CodPonto IS NULL
BEGIN
    SELECT 'EMPTY' AS Tipo, CAST(NULL AS int) AS Ano, CAST(NULL AS int) AS Mes, CAST(NULL AS varchar(7)) AS MesStr,
           CAST(0.0 AS float) AS Receita, CAST(0.0 AS float) AS Custo, CAST(0.0 AS float) AS MargemPct,
           CAST(NULL AS varchar(200)) AS Categoria, CAST(0.0 AS float) AS Valor;
    RETURN;
END;

------------------------------------------------------------------------
-- Range FULL: pega range dos contratos, mas garante que vá até @dt_fim
------------------------------------------------------------------------
DECLARE @dt_min DATE;
DECLARE @dt_max DATE;

SELECT
    @dt_min = MIN(DATEFROMPARTS(YEAR(f.DataInicioPrevisto), MONTH(f.DataInicioPrevisto), 1)),
    @dt_max = MAX(DATEFROMPARTS(YEAR(f.DataTerminoPrevisto), MONTH(f.DataTerminoPrevisto), 1))
FROM Integracao.Silver.FatoControleContratosItensMidia f WITH (NOLOCK)
WHERE f.CodPonto = @CodPonto
  AND f.DataInicioPrevisto IS NOT NULL
  AND f.DataTerminoPrevisto IS NOT NULL
  AND ISNULL(TRY_CONVERT(INT, f.AtivoCancelamento), 0) = 0;

IF @dt_min IS NULL SET @dt_min = @dt_ini;
IF @dt_max IS NULL SET @dt_max = @dt_fim;

IF @dt_min > @dt_ini SET @dt_min = @dt_ini;
IF @dt_max < @dt_fim SET @dt_max = @dt_fim;

------------------------------------------------------------------------
-- Receita mensal: rateio do TotalLiquidoContratoAGBRCTACORDO
------------------------------------------------------------------------
;WITH meses_full AS (
    SELECT DATEFROMPARTS(c.Ano, c.Mes, 1) AS MesRef
    FROM Integracao.Silver.DimCalendario c WITH (NOLOCK)
    WHERE c.[Data] >= @dt_min AND c.[Data] <= EOMONTH(@dt_max)
    GROUP BY c.Ano, c.Mes
),
meses_periodo AS (
    SELECT DATEFROMPARTS(c.Ano, c.Mes, 1) AS MesRef
    FROM Integracao.Silver.DimCalendario c WITH (NOLOCK)
    WHERE c.[Data] >= @dt_ini AND c.[Data] <= @dt_fim
    GROUP BY c.Ano, c.Mes
),
contratos_base AS (
    SELECT
        f.CodPonto,
        DATEFROMPARTS(YEAR(f.DataInicioPrevisto),  MONTH(f.DataInicioPrevisto),  1) AS IniMes,
        DATEFROMPARTS(YEAR(f.DataTerminoPrevisto), MONTH(f.DataTerminoPrevisto), 1) AS FimMes,
        TRY_CONVERT(DECIMAL(18,2), f.TotalLiquidoContratoAGBRCTACORDO) AS TotalContrato
    FROM Integracao.Silver.FatoControleContratosItensMidia f WITH (NOLOCK)
    WHERE
        f.CodPonto = @CodPonto
        AND f.DataInicioPrevisto IS NOT NULL
        AND f.DataTerminoPrevisto IS NOT NULL
        AND f.DataTerminoPrevisto >= f.DataInicioPrevisto
        AND TRY_CONVERT(DECIMAL(18,2), f.TotalLiquidoContratoAGBRCTACORDO) IS NOT NULL
        AND ISNULL(TRY_CONVERT(INT, f.AtivoCancelamento), 0) = 0
),
contratos_exp AS (
    SELECT
        CodPonto,
        IniMes,
        FimMes,
        TotalContrato,
        DATEDIFF(MONTH, IniMes, FimMes) + 1 AS QtMeses
    FROM contratos_base
    WHERE FimMes >= IniMes
),
receita_full AS (
    SELECT
        mf.MesRef,
        COALESCE(SUM(CASE WHEN e.QtMeses > 0 THEN (e.TotalContrato / e.QtMeses) ELSE 0 END), 0) AS Receita
    FROM meses_full mf
    LEFT JOIN contratos_exp e
        ON mf.MesRef >= e.IniMes
       AND mf.MesRef <= e.FimMes
    GROUP BY mf.MesRef
),
receita_periodo AS (
    SELECT
        mp.MesRef,
        COALESCE(SUM(CASE WHEN e.QtMeses > 0 THEN (e.TotalContrato / e.QtMeses) ELSE 0 END), 0) AS Receita
    FROM meses_periodo mp
    LEFT JOIN contratos_exp e
        ON mp.MesRef >= e.IniMes
       AND mp.MesRef <= e.FimMes
    GROUP BY mp.MesRef
),

------------------------------------------------------------------------
-- ✅ CUSTO (FULL): vem de DimCustoPainel (não divide por faces)
-- Regra igual sua lógica: pega o "melhor" ano em relação ao ano do mês.
------------------------------------------------------------------------
custo_full AS (
    SELECT
        mf.MesRef,
        CAST(COALESCE(custo.ValorMensal, 0.0) AS decimal(18,6)) AS Custo
    FROM meses_full mf
    OUTER APPLY (
        SELECT TOP (1)
            ValorMensal = CAST(d.Valor AS DECIMAL(18,10))
        FROM Integracao.Silver.DimCustoPainel d WITH (NOLOCK)
        WHERE d.CodPonto = @CodPonto
        ORDER BY
            CASE WHEN d.Ano <= YEAR(mf.MesRef) THEN 0 ELSE 1 END,
            ABS(d.Ano - YEAR(mf.MesRef)),
            d.Ano DESC,
            d.DataCarga DESC,
            d.IDDimCustoPainel DESC
    ) custo
),

join_full AS (
    SELECT
        rf.MesRef,
        YEAR(rf.MesRef) AS Ano,
        MONTH(rf.MesRef) AS Mes,
        CONVERT(varchar(7), rf.MesRef, 120) AS MesStr,
        CAST(rf.Receita AS float) AS Receita,
        CAST(ISNULL(cf.Custo, 0.0) AS float) AS Custo
    FROM receita_full rf
    LEFT JOIN custo_full cf
        ON cf.MesRef = rf.MesRef
),

join_periodo AS (
    SELECT
        rp.MesRef,
        YEAR(rp.MesRef) AS Ano,
        MONTH(rp.MesRef) AS Mes,
        CONVERT(varchar(7), rp.MesRef, 120) AS MesStr,
        CAST(rp.Receita AS float) AS Receita,
        CAST(ISNULL(cf.Custo, 0.0) AS float) AS Custo
    FROM receita_periodo rp
    LEFT JOIN custo_full cf
        ON cf.MesRef = rp.MesRef
),

------------------------------------------------------------------------
-- COMPOSIÇÃO (DONUT): mantém por categoria (se quiser a rosca)
------------------------------------------------------------------------
ultimo_custo_ref AS (
    SELECT TOP (1)
        (TRY_CONVERT(int, cc.Ano) * 100) + TRY_CONVERT(int, cc.Mes) AS AnoMes
    FROM Integracao.Silver.DimCustoCategoriaMensalPainel cc WITH (NOLOCK)
    WHERE TRY_CONVERT(int, cc.CodPonto) = @CodPonto
      AND DATEFROMPARTS(TRY_CONVERT(int, cc.Ano), TRY_CONVERT(int, cc.Mes), 1) <= @dt_fim
    ORDER BY (TRY_CONVERT(int, cc.Ano) * 100) + TRY_CONVERT(int, cc.Mes) DESC
),
comp AS (
    SELECT
        CAST(ISNULL(cc.Categoria,'') AS varchar(200)) AS Categoria,
        CAST(SUM(COALESCE(TRY_CONVERT(float, cc.ValorMensal), 0.0)) AS float) AS Valor
    FROM Integracao.Silver.DimCustoCategoriaMensalPainel cc WITH (NOLOCK)
    CROSS JOIN ultimo_custo_ref u
    WHERE TRY_CONVERT(int, cc.CodPonto) = @CodPonto
      AND ((TRY_CONVERT(int, cc.Ano) * 100) + TRY_CONVERT(int, cc.Mes)) = u.AnoMes
    GROUP BY cc.Categoria
    HAVING SUM(COALESCE(TRY_CONVERT(float, cc.ValorMensal), 0.0)) > 0
)

SELECT
    Tipo, Ano, Mes, MesStr, Receita, Custo, MargemPct, Categoria, Valor
FROM (
    SELECT
        'FULL' AS Tipo,
        jf.Ano, jf.Mes, jf.MesStr,
        jf.Receita,
        jf.Custo,
        CAST(
            CASE
                WHEN jf.Receita IS NULL OR jf.Receita = 0 THEN 0.0
                ELSE ((jf.Receita - jf.Custo) / jf.Receita) * 100.0
            END
        AS float) AS MargemPct,
        CAST(NULL AS varchar(200)) AS Categoria,
        CAST(0.0 AS float) AS Valor
    FROM join_full jf

    UNION ALL

    SELECT
        'PERIODO' AS Tipo,
        jp.Ano, jp.Mes, jp.MesStr,
        jp.Receita,
        jp.Custo,
        CAST(
            CASE
                WHEN jp.Receita IS NULL OR jp.Receita = 0 THEN 0.0
                ELSE ((jp.Receita - jp.Custo) / jp.Receita) * 100.0
            END
        AS float) AS MargemPct,
        CAST(NULL AS varchar(200)) AS Categoria,
        CAST(0.0 AS float) AS Valor
    FROM join_periodo jp

    UNION ALL

    SELECT
        'COMP' AS Tipo,
        CAST(NULL AS int) AS Ano,
        CAST(NULL AS int) AS Mes,
        CAST(NULL AS varchar(7)) AS MesStr,
        CAST(0.0 AS float) AS Receita,
        CAST(0.0 AS float) AS Custo,
        CAST(0.0 AS float) AS MargemPct,
        Categoria,
        Valor
    FROM comp
) X
ORDER BY
    CASE X.Tipo WHEN 'FULL' THEN 1 WHEN 'PERIODO' THEN 2 ELSE 3 END,
    X.Ano,
    X.Mes;