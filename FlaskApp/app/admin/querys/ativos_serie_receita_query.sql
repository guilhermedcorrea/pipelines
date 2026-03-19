SET NOCOUNT ON;

DECLARE @id     INT  = :id;
DECLARE @dt_ini DATE = TRY_CONVERT(date, :dt_ini);
DECLARE @dt_fim DATE = TRY_CONVERT(date, :dt_fim);

IF @dt_fim IS NULL SET @dt_fim = CONVERT(date, GETDATE());
SET @dt_fim = EOMONTH(@dt_fim);

IF @dt_ini IS NULL
    SET @dt_ini = DATEADD(MONTH, -11, DATEFROMPARTS(YEAR(@dt_fim), MONTH(@dt_fim), 1));
ELSE
    SET @dt_ini = DATEFROMPARTS(YEAR(@dt_ini), MONTH(@dt_ini), 1);

;WITH a AS (
    SELECT TOP (1)
        IDDimAtivos,
        CodPonto,
        ReferenciaExterna
    FROM Integracao.Silver.DimAtivos WITH (NOLOCK)
    WHERE IDDimAtivos = @id
),
cp AS (
    SELECT
        COALESCE(
            a.CodPonto,
            TRY_CONVERT(INT, NULLIF(LTRIM(RTRIM(CAST(a.ReferenciaExterna AS NVARCHAR(50)))), ''))
        ) AS CodPontoReceita
    FROM a
),
meses_ref AS (
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
    FROM Integracao.Silver.FatoControleContratosItensEuromidia f WITH (NOLOCK)
    CROSS JOIN cp
    WHERE
        cp.CodPontoReceita IS NOT NULL
        AND f.CodPonto = cp.CodPontoReceita
        AND f.DataInicioPrevisto IS NOT NULL
        AND f.DataTerminoPrevisto IS NOT NULL
        AND f.DataTerminoPrevisto >= f.DataInicioPrevisto
        AND TRY_CONVERT(DECIMAL(18,2), f.TotalLiquidoContratoAGBRCTACORDO) IS NOT NULL
        AND ISNULL(TRY_CONVERT(INT, f.AtivoCancelamento), 0) = 0
        AND f.DataTerminoPrevisto >= @dt_ini
        AND f.DataInicioPrevisto  <= @dt_fim
),
contratos_norm AS (
    SELECT
        CodPonto,
        CASE WHEN IniMes < @dt_ini THEN @dt_ini ELSE IniMes END AS IniMes2,
        CASE WHEN FimMes > DATEFROMPARTS(YEAR(@dt_fim), MONTH(@dt_fim), 1)
             THEN DATEFROMPARTS(YEAR(@dt_fim), MONTH(@dt_fim), 1)
             ELSE FimMes
        END AS FimMes2,
        TotalContrato
    FROM contratos_base
),
contratos_exp AS (
    SELECT
        CodPonto,
        IniMes2,
        FimMes2,
        TotalContrato,
        DATEDIFF(MONTH, IniMes2, FimMes2) + 1 AS QtMeses
    FROM contratos_norm
    WHERE FimMes2 >= IniMes2
),
mensal AS (
    SELECT
        mr.MesRef,
        CONVERT(VARCHAR(7), mr.MesRef, 120) AS Mes,
        COALESCE(
            SUM(CASE WHEN e.QtMeses > 0 THEN (e.TotalContrato / e.QtMeses) ELSE 0 END),
            0
        ) AS Receita
    FROM meses_ref mr
    LEFT JOIN contratos_exp e
        ON mr.MesRef >= e.IniMes2
       AND mr.MesRef <= e.FimMes2
    GROUP BY mr.MesRef
),
base AS (
    SELECT TOP (1)
        Receita AS ReceitaBase
    FROM mensal
    WHERE Receita > 0
    ORDER BY MesRef
)
SELECT
    m.Mes,
    CAST(m.Receita AS FLOAT) AS Receita,

    CAST(CASE WHEN m.Receita > 0 THEN 100.0 ELSE 0.0 END AS FLOAT) AS OcupacaoPct,

    /* RENTABILIDADE (%): (Receita / ReceitaBase - 1) * 100 */
    CAST(
        CASE
            WHEN b.ReceitaBase IS NULL OR b.ReceitaBase = 0 THEN NULL
            ELSE ((m.Receita / b.ReceitaBase) - 1) * 100.0
        END
    AS FLOAT) AS RentabilidadePct

FROM mensal m
OUTER APPLY (SELECT ReceitaBase FROM base) b
ORDER BY m.MesRef;