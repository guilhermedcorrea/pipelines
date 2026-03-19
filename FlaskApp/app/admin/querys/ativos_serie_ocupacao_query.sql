SET NOCOUNT ON;

DECLARE @id     INT;
DECLARE @dt_ini DATE;
DECLARE @dt_fim DATE;

SET @id     = :id;
SET @dt_ini = TRY_CONVERT(date, :dt_ini);
SET @dt_fim = TRY_CONVERT(date, :dt_fim);

IF @dt_ini IS NULL OR @dt_fim IS NULL
BEGIN
    SELECT CAST(NULL AS varchar(7)) AS Mes, CAST(0.0 AS decimal(18,6)) AS ocupacao_pct WHERE 1=0;
    RETURN;
END;

SET @dt_ini = DATEFROMPARTS(YEAR(@dt_ini), MONTH(@dt_ini), 1);
SET @dt_fim = EOMONTH(@dt_fim);

IF @dt_fim < @dt_ini
BEGIN
    SELECT CAST(NULL AS varchar(7)) AS Mes, CAST(0.0 AS decimal(18,6)) AS ocupacao_pct WHERE 1=0;
    RETURN;
END;

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
    SELECT CAST(NULL AS varchar(7)) AS Mes, CAST(0.0 AS decimal(18,6)) AS ocupacao_pct WHERE 1=0;
    RETURN;
END;

IF OBJECT_ID('tempdb..#meses')     IS NOT NULL DROP TABLE #meses;
IF OBJECT_ID('tempdb..#kp')        IS NOT NULL DROP TABLE #kp;
IF OBJECT_ID('tempdb..#base')      IS NOT NULL DROP TABLE #base;
IF OBJECT_ID('tempdb..#contratos') IS NOT NULL DROP TABLE #contratos;
IF OBJECT_ID('tempdb..#eventos')   IS NOT NULL DROP TABLE #eventos;

SELECT TOP (1)
    UPPER(LTRIM(RTRIM(COALESCE(p.Tipo,'')))) AS TipoPainel,
    TRY_CONVERT(int, p.QuantidadeFaces)      AS QuantidadeFaces
INTO #kp
FROM Integracao.Silver.DimPaineisEuromidia p WITH (NOLOCK)
WHERE TRY_CONVERT(int, p.CodPonto) = @CodPonto
ORDER BY p.IDDimPaineisEuromidia DESC;

IF NOT EXISTS (SELECT 1 FROM #kp)
BEGIN
    INSERT INTO #kp (TipoPainel, QuantidadeFaces)
    VALUES (N'NAO_DIGITAL', 1);
END;

DECLARE @EhDigital BIT =
(
    SELECT CASE WHEN TipoPainel LIKE '%DIGITAL%' THEN 1 ELSE 0 END
    FROM #kp
);

DECLARE @K_fisico INT =
(
    SELECT
        CASE
            WHEN TipoPainel LIKE '%DIGITAL%' THEN ISNULL(NULLIF(QuantidadeFaces, 0), 16)
            ELSE 1
        END
    FROM #kp
);

;WITH meses AS (
    SELECT @dt_ini AS DtMesIni
    UNION ALL
    SELECT DATEADD(MONTH, 1, DtMesIni)
    FROM meses
    WHERE DATEADD(MONTH, 1, DtMesIni) <= @dt_fim
)
SELECT
    DtMesIni,
    EOMONTH(DtMesIni) AS DtMesFim,
    CONVERT(varchar(7), DtMesIni, 120) AS Mes
INTO #meses
FROM meses
OPTION (MAXRECURSION 32767);

CREATE CLUSTERED INDEX CX__meses ON #meses (DtMesIni);

SELECT
    TRY_CONVERT(int, ftci.CodPonto) AS CodPonto,
    TRY_CONVERT(int, ftci.Cota) AS Cota,
    TRY_CONVERT(date, ftci.DataInicioPrevisto)  AS DtIni,
    TRY_CONVERT(date, ftci.DataTerminoPrevisto) AS DtFim
INTO #base
FROM Integracao.Silver.FatoControleContratosItensEuromidia ftci WITH (NOLOCK)
WHERE
    TRY_CONVERT(int, ftci.CodPonto) = @CodPonto
    AND ftci.DataInicioPrevisto IS NOT NULL
    AND ftci.DataTerminoPrevisto IS NOT NULL
    AND TRY_CONVERT(date, ftci.DataInicioPrevisto) <= TRY_CONVERT(date, ftci.DataTerminoPrevisto)
    AND TRY_CONVERT(date, ftci.DataTerminoPrevisto) >= @dt_ini
    AND TRY_CONVERT(date, ftci.DataInicioPrevisto)  <= @dt_fim
    AND ISNULL(TRY_CONVERT(INT, ftci.AtivoCancelamento), 0) = 0;

CREATE CLUSTERED INDEX CX__base ON #base (CodPonto, DtIni, DtFim);

SELECT
    b.CodPonto,
    CASE WHEN b.DtIni < @dt_ini THEN @dt_ini ELSE b.DtIni END AS DtIni2,
    CASE WHEN b.DtFim > @dt_fim THEN @dt_fim ELSE b.DtFim END AS DtFim2,
    CAST(
        CASE
            WHEN @EhDigital = 1 THEN
                CASE
                    WHEN b.Cota IS NULL OR b.Cota <= 0 THEN 0.0
                    ELSE 1080.0 / CAST(b.Cota AS float)
                END
            ELSE 1.0
        END
        AS float
    ) AS SlotsContrato
INTO #contratos
FROM #base b
WHERE (CASE WHEN b.DtFim > @dt_fim THEN @dt_fim ELSE b.DtFim END)
    >= (CASE WHEN b.DtIni < @dt_ini THEN @dt_ini ELSE b.DtIni END);

CREATE CLUSTERED INDEX CX__contratos ON #contratos (CodPonto, DtIni2, DtFim2);

SELECT
    e.CodPonto,
    e.DiaEvento,
    SUM(e.DeltaSlots) AS DeltaSlots
INTO #eventos
FROM (
    SELECT CodPonto, DtIni2 AS DiaEvento,  SlotsContrato AS DeltaSlots FROM #contratos
    UNION ALL
    SELECT CodPonto, DATEADD(DAY, 1, DtFim2) AS DiaEvento, -SlotsContrato AS DeltaSlots FROM #contratos
    UNION ALL
    SELECT @CodPonto, @dt_ini AS DiaEvento, 0.0 AS DeltaSlots
    UNION ALL
    SELECT @CodPonto, DATEADD(DAY, 1, @dt_fim) AS DiaEvento, 0.0 AS DeltaSlots
) e
GROUP BY e.CodPonto, e.DiaEvento;

CREATE CLUSTERED INDEX CX__eventos ON #eventos (CodPonto, DiaEvento);

;WITH eventos_ordenados AS (
    SELECT
        e.CodPonto,
        e.DiaEvento,
        SUM(e.DeltaSlots) OVER (
            PARTITION BY e.CodPonto
            ORDER BY e.DiaEvento
            ROWS UNBOUNDED PRECEDING
        ) AS SlotsAtivos,
        LEAD(e.DiaEvento) OVER (
            PARTITION BY e.CodPonto
            ORDER BY e.DiaEvento
        ) AS ProxDiaEvento
    FROM #eventos e
),
segmentos AS (
    SELECT
        eo.CodPonto,
        eo.DiaEvento,
        eo.ProxDiaEvento,
        CAST(
            CASE
                WHEN eo.SlotsAtivos > @K_fisico THEN CAST(@K_fisico AS float)
                WHEN eo.SlotsAtivos < 0 THEN 0.0
                ELSE CAST(eo.SlotsAtivos AS float)
            END
            AS float
        ) AS SlotsCap
    FROM eventos_ordenados eo
    WHERE eo.ProxDiaEvento IS NOT NULL
      AND eo.DiaEvento < eo.ProxDiaEvento
),
segmentos_x_mes AS (
    SELECT
        m.Mes,
        m.DtMesIni,
        m.DtMesFim,
        s.CodPonto,
        s.SlotsCap,
        CASE WHEN s.DiaEvento > m.DtMesIni THEN s.DiaEvento ELSE m.DtMesIni END AS IniInt,
        CASE
            WHEN DATEADD(DAY, -1, s.ProxDiaEvento) < m.DtMesFim
                THEN DATEADD(DAY, -1, s.ProxDiaEvento)
            ELSE m.DtMesFim
        END AS FimInt
    FROM segmentos s
    INNER JOIN #meses m
        ON s.DiaEvento <= m.DtMesFim
       AND DATEADD(DAY, -1, s.ProxDiaEvento) >= m.DtMesIni
),
calc_mes AS (
    SELECT
        Mes,
        SUM(
            CASE
                WHEN IniInt <= FimInt
                    THEN SlotsCap * CAST(DATEDIFF(DAY, IniInt, DATEADD(DAY, 1, FimInt)) AS float)
                ELSE 0.0
            END
        ) AS OcupadoSlotDiasMes
    FROM segmentos_x_mes
    GROUP BY Mes
)
SELECT
    m.Mes,
    CAST(
        CASE
            WHEN (CAST(@K_fisico AS float) * CAST(DATEDIFF(DAY, m.DtMesIni, DATEADD(DAY, 1, m.DtMesFim)) AS float)) = 0
                THEN 0.0
            ELSE
                (ISNULL(c.OcupadoSlotDiasMes, 0.0)
                 /
                 (CAST(@K_fisico AS float) * CAST(DATEDIFF(DAY, m.DtMesIni, DATEADD(DAY, 1, m.DtMesFim)) AS float))
                ) * 100.0
        END
        AS decimal(18,6)
    ) AS ocupacao_pct
FROM #meses m
LEFT JOIN calc_mes c
    ON c.Mes = m.Mes
ORDER BY m.Mes;