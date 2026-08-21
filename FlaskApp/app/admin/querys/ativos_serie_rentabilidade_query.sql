SET NOCOUNT ON;

DECLARE @id INT = :id;

DECLARE @dt_ini_in DATE = TRY_CONVERT(date, :dt_ini);
DECLARE @dt_fim_in DATE = TRY_CONVERT(date, :dt_fim);

IF @dt_ini_in IS NULL OR @dt_fim_in IS NULL
BEGIN
    SELECT CAST(NULL AS varchar(7)) AS Mes,
           CAST(0.0 AS decimal(18,2)) AS ReceitaMes,
           CAST(0.0 AS decimal(18,2)) AS CustoMes,
           CAST(0.0 AS decimal(18,2)) AS LucroMes,
           CAST(0.0 AS decimal(18,6)) AS RentabilidadePct
    WHERE 1=0;
    RETURN;
END;

DECLARE @dt_ini DATE = DATEFROMPARTS(YEAR(@dt_ini_in), MONTH(@dt_ini_in), 1);
DECLARE @dt_fim DATE = EOMONTH(@dt_fim_in);

IF @dt_fim < @dt_ini
BEGIN
    SELECT CAST(NULL AS varchar(7)) AS Mes,
           CAST(0.0 AS decimal(18,2)) AS ReceitaMes,
           CAST(0.0 AS decimal(18,2)) AS CustoMes,
           CAST(0.0 AS decimal(18,2)) AS LucroMes,
           CAST(0.0 AS decimal(18,6)) AS RentabilidadePct
    WHERE 1=0;
    RETURN;
END;

------------------------------------------------------------------------
-- resolve CodPonto (CodPonto ou ReferenciaExterna numérica)
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
    SELECT CAST(NULL AS varchar(7)) AS Mes,
           CAST(0.0 AS decimal(18,2)) AS ReceitaMes,
           CAST(0.0 AS decimal(18,2)) AS CustoMes,
           CAST(0.0 AS decimal(18,2)) AS LucroMes,
           CAST(0.0 AS decimal(18,6)) AS RentabilidadePct
    WHERE 1=0;
    RETURN;
END;

------------------------------------------------------------------------
-- meses_ref
------------------------------------------------------------------------
;WITH meses_ref AS (
    SELECT DATEFROMPARTS(c.Ano, c.Mes, 1) AS MesRef
    FROM Integracao.Silver.DimCalendario c WITH (NOLOCK)
    WHERE c.[Data] >= @dt_ini AND c.[Data] <= @dt_fim
    GROUP BY c.Ano, c.Mes
),

------------------------------------------------------------------------
-- RECEITA mensal (rateio do contrato)
------------------------------------------------------------------------
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
        AND (
            ISNULL(TRY_CONVERT(INT, f.AtivoCancelamento), 0) = 0
            OR UPPER(LTRIM(RTRIM(CAST(f.AtivoCancelamento AS NVARCHAR(10))))) IN (N'A', N'ATIVO')
        )
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
receita_m AS (
    SELECT
        mr.MesRef,
        CONVERT(varchar(7), mr.MesRef, 120) AS Mes,
        COALESCE(SUM(CASE WHEN e.QtMeses > 0 THEN (e.TotalContrato / e.QtMeses) ELSE 0 END), 0) AS ReceitaMes
    FROM meses_ref mr
    LEFT JOIN contratos_exp e
        ON mr.MesRef >= e.IniMes2
       AND mr.MesRef <= e.FimMes2
    GROUP BY mr.MesRef
),

------------------------------------------------------------------------
-- CUSTO mensal do painel (DimCustoPainel), aplicado apenas em dias ocupados
-- Seleção do custo: pega o "melhor" Ano em relação ao ano do mês (MesRef),
-- preferindo Ano <= YEAR(MesRef), depois mais próximo, etc (igual sua lógica).
------------------------------------------------------------------------
custo_ref AS (
    SELECT
        mr.MesRef,
        DiasNoMes = DAY(EOMONTH(mr.MesRef)),
        ValorMensal = CAST(COALESCE(custo.ValorMensal, 0.0) AS decimal(18,10))
    FROM meses_ref mr
    OUTER APPLY (
        SELECT TOP (1)
            ValorMensal = CAST(d.Valor AS DECIMAL(18,10))
        FROM Integracao.Silver.DimCustoPainel d WITH (NOLOCK)
        WHERE d.CodPonto = @CodPonto
        ORDER BY
            CASE WHEN d.Ano <= YEAR(mr.MesRef) THEN 0 ELSE 1 END,
            ABS(d.Ano - YEAR(mr.MesRef)),
            d.Ano DESC,
            d.DataCarga DESC,
            d.IDDimCustoPainel DESC
    ) custo
),

------------------------------------------------------------------------
-- Dias ocupados no mês: dia em que existe pelo menos 1 contrato ativo cobrindo o dia
------------------------------------------------------------------------
dias_ocupados AS (
    SELECT
        mr.MesRef,
        DiasOcupados =
            COUNT_BIG(1)
    FROM meses_ref mr
    JOIN Integracao.Silver.DimCalendario cal WITH (NOLOCK)
      ON cal.[Data] >= mr.MesRef
     AND cal.[Data] <= EOMONTH(mr.MesRef)
    WHERE
        EXISTS (
            SELECT 1
            FROM Integracao.Silver.FatoControleContratosItensMidia f WITH (NOLOCK)
            WHERE
                f.CodPonto = @CodPonto
                AND f.DataInicioPrevisto IS NOT NULL
                AND f.DataTerminoPrevisto IS NOT NULL
                AND cal.[Data] >= TRY_CONVERT(date, f.DataInicioPrevisto)
                AND cal.[Data] <= TRY_CONVERT(date, f.DataTerminoPrevisto)
                AND (
                    ISNULL(TRY_CONVERT(INT, f.AtivoCancelamento), 0) = 0
                    OR UPPER(LTRIM(RTRIM(CAST(f.AtivoCancelamento AS NVARCHAR(10))))) IN (N'A', N'ATIVO')
                )
        )
    GROUP BY mr.MesRef
),

custo_m AS (
    SELECT
        c.MesRef,
        CustoMes = CAST(
            CASE
                WHEN c.ValorMensal IS NULL OR c.ValorMensal = 0 THEN 0.0
                WHEN c.DiasNoMes IS NULL OR c.DiasNoMes = 0 THEN 0.0
                ELSE
                    (c.ValorMensal / CAST(c.DiasNoMes AS decimal(18,10)))
                    * CAST(ISNULL(d.DiasOcupados, 0) AS decimal(18,10))
            END
        AS decimal(18,2))
    FROM custo_ref c
    LEFT JOIN dias_ocupados d
      ON d.MesRef = c.MesRef
),

join_all AS (
    SELECT
        r.MesRef,
        r.Mes,
        CAST(r.ReceitaMes AS decimal(18,2)) AS ReceitaMes,
        CAST(ISNULL(c.CustoMes, 0.0) AS decimal(18,2)) AS CustoMes
    FROM receita_m r
    LEFT JOIN custo_m c
        ON c.MesRef = r.MesRef
)
SELECT
    Mes,
    ReceitaMes,
    CustoMes,
    CAST((ReceitaMes - CustoMes) AS decimal(18,2)) AS LucroMes,
    CAST(
        CASE
            WHEN ReceitaMes IS NULL OR ReceitaMes = 0 THEN 0.0
            ELSE ((ReceitaMes - CustoMes) / ReceitaMes) * 100.0
        END
    AS decimal(18,6)) AS RentabilidadePct
FROM join_all
ORDER BY MesRef;