SET NOCOUNT ON;

DECLARE @CodPonto INT = ?;
DECLARE @DtIni  DATE = ?;
DECLARE @DtFim  DATE = ?;

DECLARE @CodFace   VARCHAR(50)   = ?;
DECLARE @Cliente   NVARCHAR(200) = ?;
DECLARE @Vendedor  NVARCHAR(200) = ?;

DECLARE @KDigital  INT = ?;
DECLARE @EhDigital BIT = ?;

DECLARE @FacesCsv   NVARCHAR(MAX) = ?;
DECLARE @FacePadrao VARCHAR(50)   = ?;

SET @CodFace = NULLIF(LTRIM(RTRIM(@CodFace)), '');
SET @Cliente = NULLIF(LTRIM(RTRIM(@Cliente)), '');
SET @Vendedor = NULLIF(LTRIM(RTRIM(@Vendedor)), '');
SET @FacesCsv = NULLIF(LTRIM(RTRIM(@FacesCsv)), '');

SET @FacePadrao = NULLIF(LTRIM(RTRIM(@FacePadrao)), '');

IF @FacePadrao IS NULL
BEGIN
    SET @FacePadrao = NULLIF(LTRIM(RTRIM(@CodFace)), '');
END;

IF OBJECT_ID('tempdb..#faces') IS NOT NULL DROP TABLE #faces;
IF OBJECT_ID('tempdb..#faces_todas') IS NOT NULL DROP TABLE #faces_todas;
IF OBJECT_ID('tempdb..#custos_ponto') IS NOT NULL DROP TABLE #custos_ponto;
IF OBJECT_ID('tempdb..#cal') IS NOT NULL DROP TABLE #cal;

IF OBJECT_ID('tempdb..#itens_contratos') IS NOT NULL DROP TABLE #itens_contratos;
IF OBJECT_ID('tempdb..#itens_reservas') IS NOT NULL DROP TABLE #itens_reservas;
IF OBJECT_ID('tempdb..#itens') IS NOT NULL DROP TABLE #itens;

IF OBJECT_ID('tempdb..#ocupacao_capada') IS NOT NULL DROP TABLE #ocupacao_capada;
IF OBJECT_ID('tempdb..#por_mes') IS NOT NULL DROP TABLE #por_mes;
IF OBJECT_ID('tempdb..#totais') IS NOT NULL DROP TABLE #totais;
IF OBJECT_ID('tempdb..#pico') IS NOT NULL DROP TABLE #pico;

IF OBJECT_ID('tempdb..#painel_oficial') IS NOT NULL DROP TABLE #painel_oficial;
IF OBJECT_ID('tempdb..#itens_contratos_oficial') IS NOT NULL DROP TABLE #itens_contratos_oficial;
IF OBJECT_ID('tempdb..#ocupacao_oficial_por_dia') IS NOT NULL DROP TABLE #ocupacao_oficial_por_dia;
IF OBJECT_ID('tempdb..#por_mes_oficial') IS NOT NULL DROP TABLE #por_mes_oficial;
IF OBJECT_ID('tempdb..#kpi_oficial') IS NOT NULL DROP TABLE #kpi_oficial;
IF OBJECT_ID('tempdb..#pico_oficial') IS NOT NULL DROP TABLE #pico_oficial;

IF OBJECT_ID('tempdb..#meses_periodo') IS NOT NULL DROP TABLE #meses_periodo;
IF OBJECT_ID('tempdb..#custo_mensal') IS NOT NULL DROP TABLE #custo_mensal;
IF OBJECT_ID('tempdb..#itens_totais_sem_filtro') IS NOT NULL DROP TABLE #itens_totais_sem_filtro;
IF OBJECT_ID('tempdb..#ocup_total_mes_capada') IS NOT NULL DROP TABLE #ocup_total_mes_capada;

IF OBJECT_ID('tempdb..#custo_periodo') IS NOT NULL DROP TABLE #custo_periodo;
IF OBJECT_ID('tempdb..#receita_periodo') IS NOT NULL DROP TABLE #receita_periodo;
IF OBJECT_ID('tempdb..#financeiro') IS NOT NULL DROP TABLE #financeiro;

IF OBJECT_ID('tempdb..#cdi_final') IS NOT NULL DROP TABLE #cdi_final;

CREATE TABLE #faces (
    CodFace VARCHAR(50) COLLATE DATABASE_DEFAULT NOT NULL
);

IF @CodFace IS NOT NULL
BEGIN
    INSERT INTO #faces (CodFace)
    VALUES (CAST(@CodFace AS VARCHAR(50)));
END
ELSE
BEGIN
    IF @FacesCsv IS NOT NULL
    BEGIN
        INSERT INTO #faces (CodFace)
        SELECT DISTINCT CAST(LTRIM(RTRIM(value)) AS VARCHAR(50))
        FROM string_split(@FacesCsv, ',')
        WHERE NULLIF(LTRIM(RTRIM(value)), '') IS NOT NULL;
    END;

    IF NOT EXISTS (SELECT 1 FROM #faces) AND @FacePadrao IS NOT NULL
    BEGIN
        INSERT INTO #faces (CodFace)
        VALUES (CAST(@FacePadrao AS VARCHAR(50)));
    END;
END;

CREATE CLUSTERED INDEX CX__faces ON #faces (CodFace);

CREATE TABLE #faces_todas (
    CodFace VARCHAR(50) COLLATE DATABASE_DEFAULT NOT NULL
);

IF OBJECT_ID('Integracao.Silver.DimFacesPaineisEuromidia', 'U') IS NOT NULL
BEGIN
    INSERT INTO #faces_todas (CodFace)
    SELECT DISTINCT CAST(LTRIM(RTRIM(d.CodFace)) AS VARCHAR(50))
    FROM Integracao.Silver.DimFacesPaineisEuromidia d
    WHERE TRY_CONVERT(INT, d.CodPonto) = @CodPonto
      AND NULLIF(LTRIM(RTRIM(d.CodFace)), '') IS NOT NULL;
END
ELSE IF OBJECT_ID('Integracao.Silver.DimFacesPaineis', 'U') IS NOT NULL
BEGIN
    INSERT INTO #faces_todas (CodFace)
    SELECT DISTINCT CAST(LTRIM(RTRIM(d.CodFace)) AS VARCHAR(50))
    FROM Integracao.Silver.DimFacesPaineis d
    WHERE TRY_CONVERT(INT, d.CodPonto) = @CodPonto
      AND NULLIF(LTRIM(RTRIM(d.CodFace)), '') IS NOT NULL;
END
ELSE
BEGIN
    INSERT INTO #faces_todas (CodFace)
    SELECT DISTINCT CAST(LTRIM(RTRIM(x.CodFaceEfetivo)) AS VARCHAR(50))
    FROM (
        SELECT
            CASE
                WHEN NULLIF(LTRIM(RTRIM(COALESCE(c.CodFace, ''))), '') IS NULL THEN @FacePadrao
                ELSE LTRIM(RTRIM(COALESCE(c.CodFace, '')))
            END AS CodFaceEfetivo
        FROM Integracao.Silver.FatoControleContratosItensEuromidia c
        WHERE c.AtivoCancelamento = 'A'
          AND TRY_CONVERT(INT, c.CodPonto) = @CodPonto

        UNION

        SELECT
            CASE
                WHEN NULLIF(LTRIM(RTRIM(COALESCE(r.CodFace, ''))), '') IS NULL THEN @FacePadrao
                ELSE LTRIM(RTRIM(COALESCE(r.CodFace, '')))
            END AS CodFaceEfetivo
        FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia r
        WHERE TRY_CONVERT(INT, r.CodPonto) = @CodPonto
    ) x
    WHERE NULLIF(LTRIM(RTRIM(x.CodFaceEfetivo)), '') IS NOT NULL;
END;

IF NOT EXISTS (SELECT 1 FROM #faces_todas) AND @FacePadrao IS NOT NULL
BEGIN
    INSERT INTO #faces_todas (CodFace)
    VALUES (CAST(@FacePadrao AS VARCHAR(50)));
END;

CREATE CLUSTERED INDEX CX__faces_todas ON #faces_todas (CodFace);

DECLARE @CompFim INT = (YEAR(@DtFim) * 100) + MONTH(@DtFim);

CREATE TABLE #custos_ponto (
    Competencia INT NOT NULL,
    Ano INT NOT NULL,
    Mes INT NOT NULL,
    ValorMensal DECIMAL(18,6) NOT NULL
);

INSERT INTO #custos_ponto (Competencia, Ano, Mes, ValorMensal)
SELECT
    (TRY_CONVERT(INT, cmp.Ano) * 100) + TRY_CONVERT(INT, cmp.Mes) AS Competencia,
    TRY_CONVERT(INT, cmp.Ano) AS Ano,
    TRY_CONVERT(INT, cmp.Mes) AS Mes,
    TRY_CONVERT(DECIMAL(18,6), cmp.ValorMensal) AS ValorMensal
FROM Integracao.Silver.DimCustoMensalPainel cmp
WHERE TRY_CONVERT(INT, cmp.CodPonto) = @CodPonto
  AND TRY_CONVERT(INT, cmp.Ano) IS NOT NULL
  AND TRY_CONVERT(INT, cmp.Mes) IS NOT NULL
  AND TRY_CONVERT(DECIMAL(18,6), cmp.ValorMensal) IS NOT NULL
  AND ((TRY_CONVERT(INT, cmp.Ano) * 100) + TRY_CONVERT(INT, cmp.Mes)) <= @CompFim;

CREATE CLUSTERED INDEX CX__custos_ponto ON #custos_ponto (Competencia DESC);

SELECT
    c.data AS Dia,
    c.ano AS Ano,
    c.mes AS Mes
INTO #cal
FROM Integracao.Silver.DimCalendario c
WHERE c.data BETWEEN @DtIni AND @DtFim;

CREATE CLUSTERED INDEX CX__cal ON #cal (Dia);

SELECT
    'CONTRATO' AS OrigemItem,
    CASE
        WHEN NULLIF(LTRIM(RTRIM(COALESCE(c.CodFace, ''))), '') IS NULL THEN CAST(@FacePadrao AS VARCHAR(50))
        ELSE CAST(LTRIM(RTRIM(COALESCE(c.CodFace, ''))) AS VARCHAR(50))
    END AS CodFaceEfetivo,
    TRY_CONVERT(DATE, c.DataInicioPrevisto) AS DtIni,
    COALESCE(
        TRY_CONVERT(DATE, c.DataCancelamento),
        TRY_CONVERT(DATE, c.DataTerminoPrevisto),
        CONVERT(DATE, '9999-12-31')
    ) AS DtFim,
    CAST(
        CASE
            WHEN TRY_CONVERT(INT, c.Cota) IS NULL OR TRY_CONVERT(INT, c.Cota) <= 0 THEN 0
            ELSE (1080.0 / CAST(TRY_CONVERT(INT, c.Cota) AS FLOAT))
        END
    AS FLOAT) AS SpansKpi,
    TRY_CONVERT(DECIMAL(18,6), c.FaturamentoLiquidoFinalMensal) AS FatMensal
INTO #itens_contratos
FROM Integracao.Silver.FatoControleContratosItensEuromidia c
WHERE c.AtivoCancelamento = 'A'
  AND TRY_CONVERT(INT, c.CodPonto) = @CodPonto
  AND c.DataInicioPrevisto IS NOT NULL
  AND TRY_CONVERT(DATE, c.DataInicioPrevisto) <= @DtFim
  AND COALESCE(
        TRY_CONVERT(DATE, c.DataCancelamento),
        TRY_CONVERT(DATE, c.DataTerminoPrevisto),
        CONVERT(DATE, '9999-12-31')
      ) >= @DtIni
  AND (
        @CodFace IS NULL
        OR LTRIM(RTRIM(COALESCE(c.CodFace, ''))) COLLATE DATABASE_DEFAULT = @CodFace COLLATE DATABASE_DEFAULT
        OR (LTRIM(RTRIM(COALESCE(c.CodFace, ''))) = '' AND @CodFace = @FacePadrao)
      )
  AND (@Cliente IS NULL OR c.MarcaExibida LIKE @Cliente)
  AND (@Vendedor IS NULL OR c.Vendedor LIKE @Vendedor);

CREATE INDEX IX__itens_contratos_face ON #itens_contratos (CodFaceEfetivo, DtIni, DtFim);

SELECT
    'RESERVA' AS OrigemItem,
    CASE
        WHEN NULLIF(LTRIM(RTRIM(COALESCE(r.CodFace, ''))), '') IS NULL THEN CAST(@FacePadrao AS VARCHAR(50))
        ELSE CAST(LTRIM(RTRIM(COALESCE(r.CodFace, ''))) AS VARCHAR(50))
    END AS CodFaceEfetivo,
    TRY_CONVERT(DATE, r.DataInicio) AS DtIni,
    COALESCE(TRY_CONVERT(DATE, r.DataFim), CONVERT(DATE, '9999-12-31')) AS DtFim,
    CAST(
        CASE
            WHEN TRY_CONVERT(INT, r.SpanQtd) IS NOT NULL AND TRY_CONVERT(INT, r.SpanQtd) > 0 THEN TRY_CONVERT(INT, r.SpanQtd) * 1.0
            WHEN TRY_CONVERT(INT, r.Cota) IS NULL OR TRY_CONVERT(INT, r.Cota) <= 0 THEN 0
            ELSE (1080.0 / CAST(TRY_CONVERT(INT, r.Cota) AS FLOAT))
        END
    AS FLOAT) AS SpansKpi,
    CAST(NULL AS DECIMAL(18,6)) AS FatMensal
INTO #itens_reservas
FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia r
WHERE TRY_CONVERT(INT, r.CodPonto) = @CodPonto
  AND r.Origem = 'RESERVA'
  AND r.Status = 'RESERVADO'
  AND r.DataInicio IS NOT NULL
  AND TRY_CONVERT(DATE, r.DataInicio) <= @DtFim
  AND COALESCE(TRY_CONVERT(DATE, r.DataFim), CONVERT(DATE, '9999-12-31')) >= @DtIni
  AND (
        @CodFace IS NULL
        OR LTRIM(RTRIM(COALESCE(r.CodFace, ''))) COLLATE DATABASE_DEFAULT = @CodFace COLLATE DATABASE_DEFAULT
        OR (LTRIM(RTRIM(COALESCE(r.CodFace, ''))) = '' AND @CodFace = @FacePadrao)
      )
  AND (@Cliente IS NULL OR r.MarcaExibida LIKE @Cliente)
  AND (@Vendedor IS NULL OR r.Vendedor LIKE @Vendedor);

CREATE INDEX IX__itens_reservas_face ON #itens_reservas (CodFaceEfetivo, DtIni, DtFim);

SELECT CodFaceEfetivo, DtIni, DtFim, SpansKpi, FatMensal
INTO #itens
FROM #itens_contratos

UNION ALL

SELECT CodFaceEfetivo, DtIni, DtFim, SpansKpi, FatMensal
FROM #itens_reservas;

CREATE INDEX IX__itens_face ON #itens (CodFaceEfetivo, DtIni, DtFim);

SELECT
    x.CodFace,
    x.Dia,
    x.Ano,
    x.Mes,
    CASE
        WHEN @EhDigital = 1 THEN
            CASE
                WHEN x.OcupadoBrutoDia > @KDigital THEN @KDigital * 1.0
                WHEN x.OcupadoBrutoDia < 0 THEN 0.0
                ELSE x.OcupadoBrutoDia
            END
        ELSE
            CASE WHEN x.OcupadoBrutoDia >= 1 THEN 1.0 ELSE 0.0 END
    END AS OcupadoNoDia
INTO #ocupacao_capada
FROM (
    SELECT
        f.CodFace,
        cal.Dia,
        cal.Ano,
        cal.Mes,
        SUM(
            CASE
                WHEN i.DtIni <= cal.Dia AND i.DtFim >= cal.Dia THEN
                    CASE WHEN @EhDigital = 1 THEN ISNULL(i.SpansKpi, 0) ELSE 1.0 END
                ELSE 0
            END
        ) AS OcupadoBrutoDia
    FROM #faces f
    CROSS JOIN #cal cal
    LEFT JOIN #itens i
        ON i.CodFaceEfetivo COLLATE DATABASE_DEFAULT = f.CodFace COLLATE DATABASE_DEFAULT
    GROUP BY
        f.CodFace,
        cal.Dia,
        cal.Ano,
        cal.Mes
) x;

CREATE INDEX IX__ocupacao_capada_mes ON #ocupacao_capada (Ano, Mes, Dia);

SELECT
    Ano,
    Mes,
    SUM(OcupadoNoDia) AS OcupadoSlotDiasMes
INTO #por_mes
FROM #ocupacao_capada
GROUP BY
    Ano,
    Mes;

CREATE CLUSTERED INDEX CX__por_mes ON #por_mes (Ano, Mes);

SELECT
    (SELECT COUNT(*) FROM #faces) AS QtdFaces,
    DATEDIFF(DAY, @DtIni, DATEADD(DAY, 1, @DtFim)) AS TotalDias,
    SUM(OcupadoNoDia) AS OcupadoSlotDiasTotal
INTO #totais
FROM #ocupacao_capada;

SELECT
    CAST(ISNULL(MAX(OcupadoNoDiaPainel), 0) AS INT) AS SlotsOcupadosPico
INTO #pico
FROM (
    SELECT
        Dia,
        SUM(OcupadoNoDia) AS OcupadoNoDiaPainel
    FROM #ocupacao_capada
    GROUP BY
        Dia
) p;

SELECT
    Ano,
    Mes,
    (Ano * 100 + Mes) AS Competencia
INTO #meses_periodo
FROM #cal
GROUP BY
    Ano,
    Mes;

CREATE CLUSTERED INDEX CX__meses_periodo ON #meses_periodo (Ano, Mes);

SELECT
    mp.Ano,
    mp.Mes,
    mp.Competencia,
    CAST(COALESCE(ca.ValorMensal, 0.0) AS DECIMAL(18,6)) AS ValorMensal
INTO #custo_mensal
FROM #meses_periodo mp
OUTER APPLY (
    SELECT TOP (1)
        c.ValorMensal
    FROM #custos_ponto c
    WHERE c.Competencia <= mp.Competencia
    ORDER BY
        c.Competencia DESC
) ca;

CREATE CLUSTERED INDEX CX__custo_mensal ON #custo_mensal (Ano, Mes);

SELECT
    CASE
        WHEN NULLIF(LTRIM(RTRIM(COALESCE(c.CodFace, ''))), '') IS NULL THEN CAST(@FacePadrao AS VARCHAR(50))
        ELSE CAST(LTRIM(RTRIM(COALESCE(c.CodFace, ''))) AS VARCHAR(50))
    END AS CodFaceEfetivo,
    TRY_CONVERT(DATE, c.DataInicioPrevisto) AS DtIni,
    COALESCE(
        TRY_CONVERT(DATE, c.DataCancelamento),
        TRY_CONVERT(DATE, c.DataTerminoPrevisto),
        CONVERT(DATE, '9999-12-31')
    ) AS DtFim,
    CAST(
        CASE
            WHEN TRY_CONVERT(INT, c.Cota) IS NULL OR TRY_CONVERT(INT, c.Cota) <= 0 THEN 0
            ELSE (1080.0 / CAST(TRY_CONVERT(INT, c.Cota) AS FLOAT))
        END
    AS FLOAT) AS SpansKpi
INTO #itens_totais_sem_filtro
FROM Integracao.Silver.FatoControleContratosItensEuromidia c
WHERE c.AtivoCancelamento = 'A'
  AND TRY_CONVERT(INT, c.CodPonto) = @CodPonto
  AND c.DataInicioPrevisto IS NOT NULL
  AND TRY_CONVERT(DATE, c.DataInicioPrevisto) <= @DtFim
  AND COALESCE(
        TRY_CONVERT(DATE, c.DataCancelamento),
        TRY_CONVERT(DATE, c.DataTerminoPrevisto),
        CONVERT(DATE, '9999-12-31')
      ) >= @DtIni

UNION ALL

SELECT
    CASE
        WHEN NULLIF(LTRIM(RTRIM(COALESCE(r.CodFace, ''))), '') IS NULL THEN CAST(@FacePadrao AS VARCHAR(50))
        ELSE CAST(LTRIM(RTRIM(COALESCE(r.CodFace, ''))) AS VARCHAR(50))
    END AS CodFaceEfetivo,
    TRY_CONVERT(DATE, r.DataInicio) AS DtIni,
    COALESCE(TRY_CONVERT(DATE, r.DataFim), CONVERT(DATE, '9999-12-31')) AS DtFim,
    CAST(
        CASE
            WHEN TRY_CONVERT(INT, r.SpanQtd) IS NOT NULL AND TRY_CONVERT(INT, r.SpanQtd) > 0 THEN TRY_CONVERT(INT, r.SpanQtd) * 1.0
            WHEN TRY_CONVERT(INT, r.Cota) IS NULL OR TRY_CONVERT(INT, r.Cota) <= 0 THEN 0
            ELSE (1080.0 / CAST(TRY_CONVERT(INT, r.Cota) AS FLOAT))
        END
    AS FLOAT) AS SpansKpi
FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia r
WHERE TRY_CONVERT(INT, r.CodPonto) = @CodPonto
  AND r.Origem = 'RESERVA'
  AND r.Status = 'RESERVADO'
  AND r.DataInicio IS NOT NULL
  AND TRY_CONVERT(DATE, r.DataInicio) <= @DtFim
  AND COALESCE(TRY_CONVERT(DATE, r.DataFim), CONVERT(DATE, '9999-12-31')) >= @DtIni;

CREATE INDEX IX__itens_totais_sem_filtro_face
    ON #itens_totais_sem_filtro (CodFaceEfetivo, DtIni, DtFim);

;WITH ocup_total_por_dia_face AS (
    SELECT
        f.CodFace,
        cal.Ano,
        cal.Mes,
        cal.Dia,
        SUM(
            CASE
                WHEN i.DtIni <= cal.Dia AND i.DtFim >= cal.Dia THEN
                    CASE WHEN @EhDigital = 1 THEN ISNULL(i.SpansKpi, 0) ELSE 1.0 END
                ELSE 0
            END
        ) AS OcupadoBrutoDiaTotalFace
    FROM #faces_todas f
    CROSS JOIN #cal cal
    LEFT JOIN #itens_totais_sem_filtro i
        ON i.CodFaceEfetivo COLLATE DATABASE_DEFAULT = f.CodFace COLLATE DATABASE_DEFAULT
    GROUP BY
        f.CodFace,
        cal.Ano,
        cal.Mes,
        cal.Dia
),
ocup_total_capada_face AS (
    SELECT
        CodFace,
        Ano,
        Mes,
        Dia,
        CASE
            WHEN @EhDigital = 1 THEN
                CASE
                    WHEN OcupadoBrutoDiaTotalFace > @KDigital THEN @KDigital * 1.0
                    WHEN OcupadoBrutoDiaTotalFace < 0 THEN 0.0
                    ELSE OcupadoBrutoDiaTotalFace
                END
            ELSE
                CASE WHEN OcupadoBrutoDiaTotalFace >= 1 THEN 1.0 ELSE 0.0 END
        END AS OcupadoNoDiaTotalFace
    FROM ocup_total_por_dia_face
)
SELECT
    Ano,
    Mes,
    SUM(OcupadoNoDiaTotalFace) AS OcupadoSlotDiasTotalMes
INTO #ocup_total_mes_capada
FROM ocup_total_capada_face
GROUP BY
    Ano,
    Mes;

CREATE CLUSTERED INDEX CX__ocup_total_mes_capada
    ON #ocup_total_mes_capada (Ano, Mes);

SELECT
    CAST(SUM(
        CASE
            WHEN ISNULL(otm.OcupadoSlotDiasTotalMes, 0.0) <= 0 THEN 0.0
            ELSE cm.ValorMensal * (ISNULL(pm.OcupadoSlotDiasMes, 0.0) / otm.OcupadoSlotDiasTotalMes)
        END
    ) AS DECIMAL(18,2)) AS CustoPeriodo
INTO #custo_periodo
FROM #meses_periodo mp
LEFT JOIN #custo_mensal cm
    ON cm.Ano = mp.Ano
   AND cm.Mes = mp.Mes
LEFT JOIN #por_mes pm
    ON pm.Ano = mp.Ano
   AND pm.Mes = mp.Mes
LEFT JOIN #ocup_total_mes_capada otm
    ON otm.Ano = mp.Ano
   AND otm.Mes = mp.Mes;

SELECT
    CAST(SUM(
        CASE
            WHEN i.DtIni <= cal.Dia AND i.DtFim >= cal.Dia THEN
                (ISNULL(i.FatMensal, 0.0) / NULLIF(DAY(EOMONTH(cal.Dia)), 0))
            ELSE 0.0
        END
    ) AS DECIMAL(18,2)) AS ReceitaPeriodo
INTO #receita_periodo
FROM #cal cal
CROSS JOIN #faces f
LEFT JOIN #itens_contratos i
    ON i.CodFaceEfetivo COLLATE DATABASE_DEFAULT = f.CodFace COLLATE DATABASE_DEFAULT;

SELECT
    rp.ReceitaPeriodo,
    cp.CustoPeriodo,
    CAST((rp.ReceitaPeriodo - cp.CustoPeriodo) AS DECIMAL(18,2)) AS RentabilidadeValor,
    CAST(
        CASE
            WHEN rp.ReceitaPeriodo IS NULL OR rp.ReceitaPeriodo <= 0 THEN NULL
            ELSE ((rp.ReceitaPeriodo - cp.CustoPeriodo) / rp.ReceitaPeriodo) * 100.0
        END
    AS DECIMAL(18,2)) AS MargemPct
INTO #financeiro
FROM #receita_periodo rp
CROSS JOIN #custo_periodo cp;

;WITH cdi_dias AS (
    SELECT
        t.DataReferencia,
        TRY_CONVERT(DECIMAL(18,10), t.CdiPercentDia) AS CdiDia
    FROM Integracao.Silver.DimTaxaJurosDiaria t
    WHERE t.DataReferencia BETWEEN @DtIni AND @DtFim
      AND t.CdiPercentDia IS NOT NULL
),
cdi_resumo AS (
    SELECT
        CAST(COUNT(*) AS INT) AS QtdDiasCdi,
        CAST(SUM(CdiDia) AS DECIMAL(18,10)) AS CdiSomaPercentDia
    FROM cdi_dias
),
cdi_fatores AS (
    SELECT
        (1.0 + (CdiDia / 100.0)) AS FatorDia
    FROM cdi_dias
),
cdi_agregado AS (
    SELECT
        CAST(EXP(SUM(LOG(NULLIF(FatorDia, 0.0)))) AS DECIMAL(18,10)) AS CdiFatorPeriodo
    FROM cdi_fatores
    WHERE FatorDia > 0
)
SELECT
    r.QtdDiasCdi,
    r.CdiSomaPercentDia,
    a.CdiFatorPeriodo,
    CAST((a.CdiFatorPeriodo - 1.0) * 100.0 AS DECIMAL(18,6)) AS CdiPercentPeriodo
INTO #cdi_final
FROM cdi_resumo r
CROSS JOIN cdi_agregado a;

/*
    KPI OFICIAL DE OCUPAÇÃO DA GRADE

    Correção aplicada:

    1) A capacidade oficial vem de Integracao.Silver.DimPaineisEuromidia.QuantidadeFaces.
    2) A capacidade do período é:
       quantidade de dias do período × QuantidadeFaces.
    3) Reserva NÃO entra no percentual oficial.
    4) Cota/SpansKpi NÃO multiplicam a ocupação oficial.
    5) Cada campanha/contrato normalizado conta 1 slot-dia por dia dentro do período.
    6) A normalização remove duplicidades do mesmo contrato na mesma face, no mesmo painel
       e no mesmo intervalo, que era o ponto que inflava o KPI para 23,5%.
*/

IF OBJECT_ID('tempdb..#itens_contratos_oficial_raw') IS NOT NULL DROP TABLE #itens_contratos_oficial_raw;
IF OBJECT_ID('tempdb..#itens_contratos_oficial_dia') IS NOT NULL DROP TABLE #itens_contratos_oficial_dia;

SELECT DISTINCT
    f.CodFace,
    TRY_CONVERT(INT, df.CodPonto) AS CodPonto,
    df.IDDimPaineisEuromidia,
    TRY_CONVERT(INT, pn.QuantidadeFaces) AS QuantidadeSlots
INTO #painel_oficial
FROM #faces f
INNER JOIN Integracao.Silver.DimFacesPaineis df
    ON LTRIM(RTRIM(df.CodFace)) COLLATE DATABASE_DEFAULT = f.CodFace COLLATE DATABASE_DEFAULT
INNER JOIN Integracao.Silver.DimPaineisEuromidia pn
    ON pn.IDDimPaineisEuromidia = df.IDDimPaineisEuromidia
WHERE TRY_CONVERT(INT, df.CodPonto) = @CodPonto
  AND TRY_CONVERT(INT, pn.QuantidadeFaces) IS NOT NULL
  AND TRY_CONVERT(INT, pn.QuantidadeFaces) > 0;

CREATE CLUSTERED INDEX CX__painel_oficial
    ON #painel_oficial (CodFace, CodPonto, IDDimPaineisEuromidia);

SELECT
    c.IDFatoControleContratosItensEuromidia,
    c.IDFatoControleContratoEuromidia,
    LTRIM(RTRIM(c.CodFace)) AS CodFaceEfetivo,
    TRY_CONVERT(INT, c.CodPonto) AS CodPonto,
    c.IDPainelEuromidia,
    NULLIF(LTRIM(RTRIM(COALESCE(c.MarcaExibida, ''))), '') AS MarcaExibida,

    TRY_CONVERT(DATE, c.DataInicioPrevisto) AS DataInicioOriginal,

    COALESCE(
        TRY_CONVERT(DATE, c.DataCancelamento),
        TRY_CONVERT(DATE, c.DataTerminoPrevisto),
        CONVERT(DATE, '9999-12-31')
    ) AS DataFimOriginal,

    CASE
        WHEN TRY_CONVERT(DATE, c.DataInicioPrevisto) < @DtIni
            THEN @DtIni
        ELSE TRY_CONVERT(DATE, c.DataInicioPrevisto)
    END AS DtIni,

    CASE
        WHEN COALESCE(
                TRY_CONVERT(DATE, c.DataCancelamento),
                TRY_CONVERT(DATE, c.DataTerminoPrevisto),
                CONVERT(DATE, '9999-12-31')
             ) > @DtFim
            THEN @DtFim
        ELSE COALESCE(
                TRY_CONVERT(DATE, c.DataCancelamento),
                TRY_CONVERT(DATE, c.DataTerminoPrevisto),
                CONVERT(DATE, '9999-12-31')
             )
    END AS DtFim
INTO #itens_contratos_oficial_raw
FROM Integracao.Silver.FatoControleContratosItensEuromidia c
INNER JOIN #painel_oficial po
    ON po.CodFace COLLATE DATABASE_DEFAULT = LTRIM(RTRIM(c.CodFace)) COLLATE DATABASE_DEFAULT
   AND po.IDDimPaineisEuromidia = c.IDPainelEuromidia
WHERE c.AtivoCancelamento = 'A'
  AND c.DataInicioPrevisto IS NOT NULL
  AND TRY_CONVERT(DATE, c.DataInicioPrevisto) < DATEADD(DAY, 1, @DtFim)
  AND COALESCE(
        TRY_CONVERT(DATE, c.DataCancelamento),
        TRY_CONVERT(DATE, c.DataTerminoPrevisto),
        CONVERT(DATE, '9999-12-31')
      ) >= @DtIni
  AND (@Cliente IS NULL OR c.MarcaExibida LIKE @Cliente)
  AND (@Vendedor IS NULL OR c.Vendedor LIKE @Vendedor);

CREATE INDEX IX__itens_contratos_oficial_raw
    ON #itens_contratos_oficial_raw
    (
        CodFaceEfetivo,
        CodPonto,
        IDPainelEuromidia,
        IDFatoControleContratoEuromidia,
        DtIni,
        DtFim
    );

/*
    Aqui está a correção principal.

    Antes:
    - A ocupação diária contava DISTINCT IDFatoControleContratosItensEuromidia.
    - Se o mesmo contrato/face/painel/período aparecesse duplicado em itens diferentes,
      o KPI somava mais slot-dia do que deveria.

    Agora:
    - Primeiro normaliza por contrato + face + painel + período dentro do mês.
    - Depois cada linha normalizada conta 1 slot-dia.
*/
SELECT
    MIN(r.IDFatoControleContratosItensEuromidia) AS IDFatoControleContratosItensEuromidiaRepresentante,
    r.IDFatoControleContratoEuromidia,
    r.CodFaceEfetivo,
    r.CodPonto,
    r.IDPainelEuromidia,
    r.MarcaExibida,
    r.DtIni,
    r.DtFim,
    COUNT(*) AS QtdLinhasOriginaisAgrupadas
INTO #itens_contratos_oficial
FROM #itens_contratos_oficial_raw r
WHERE r.DtIni IS NOT NULL
  AND r.DtFim IS NOT NULL
  AND r.DtIni <= r.DtFim
GROUP BY
    r.IDFatoControleContratoEuromidia,
    r.CodFaceEfetivo,
    r.CodPonto,
    r.IDPainelEuromidia,
    r.MarcaExibida,
    r.DtIni,
    r.DtFim;

CREATE INDEX IX__itens_contratos_oficial
    ON #itens_contratos_oficial
    (
        CodFaceEfetivo,
        CodPonto,
        IDPainelEuromidia,
        DtIni,
        DtFim
    );

SELECT
    po.CodFace,
    po.CodPonto,
    po.IDDimPaineisEuromidia,
    po.QuantidadeSlots,
    cal.Dia,
    cal.Ano,
    cal.Mes,

    CAST(COUNT(ic.IDFatoControleContratosItensEuromidiaRepresentante) AS DECIMAL(18,6)) AS OcupadoBrutoNoDiaOficial,

    CAST(
        CASE
            WHEN COUNT(ic.IDFatoControleContratosItensEuromidiaRepresentante) > po.QuantidadeSlots
                THEN po.QuantidadeSlots
            ELSE COUNT(ic.IDFatoControleContratosItensEuromidiaRepresentante)
        END
        AS DECIMAL(18,6)
    ) AS OcupadoNoDiaOficial,

    CAST(
        CASE
            WHEN COUNT(ic.IDFatoControleContratosItensEuromidiaRepresentante) > po.QuantidadeSlots
                THEN COUNT(ic.IDFatoControleContratosItensEuromidiaRepresentante) - po.QuantidadeSlots
            ELSE 0
        END
        AS DECIMAL(18,6)
    ) AS ExcedenteNoDiaOficial
INTO #ocupacao_oficial_por_dia
FROM #painel_oficial po
CROSS JOIN #cal cal
LEFT JOIN #itens_contratos_oficial ic
    ON ic.CodFaceEfetivo COLLATE DATABASE_DEFAULT = po.CodFace COLLATE DATABASE_DEFAULT
   AND ic.IDPainelEuromidia = po.IDDimPaineisEuromidia
   AND ic.DtIni <= cal.Dia
   AND ic.DtFim >= cal.Dia
GROUP BY
    po.CodFace,
    po.CodPonto,
    po.IDDimPaineisEuromidia,
    po.QuantidadeSlots,
    cal.Dia,
    cal.Ano,
    cal.Mes;

CREATE INDEX IX__ocupacao_oficial_por_dia_mes
    ON #ocupacao_oficial_por_dia (Ano, Mes, Dia);

SELECT
    Ano,
    Mes,
    CAST(SUM(OcupadoNoDiaOficial) AS DECIMAL(18,2)) AS OcupadoSlotDiasMes,
    CAST(SUM(QuantidadeSlots) AS DECIMAL(18,2)) AS CapacidadeSlotDiasMes,
    CAST(SUM(ExcedenteNoDiaOficial) AS DECIMAL(18,2)) AS ExcedenteSlotDiasMes,
    CAST(
        CASE
            WHEN SUM(QuantidadeSlots) <= 0 THEN NULL
            ELSE SUM(OcupadoNoDiaOficial) * 100.0 / SUM(QuantidadeSlots)
        END
    AS DECIMAL(18,2)) AS OcupacaoPctMes
INTO #por_mes_oficial
FROM #ocupacao_oficial_por_dia
GROUP BY
    Ano,
    Mes;

CREATE CLUSTERED INDEX CX__por_mes_oficial
    ON #por_mes_oficial (Ano, Mes);

SELECT
    CAST(COUNT(DISTINCT CodFace) AS INT) AS QtdFaces,
    CAST(COUNT(DISTINCT Dia) AS INT) AS TotalDias,
    CAST(
        CASE
            WHEN COUNT(DISTINCT Dia) <= 0 THEN 0
            ELSE SUM(QuantidadeSlots) / COUNT(DISTINCT Dia)
        END
    AS INT) AS SlotsTotalDia,
    CAST(ISNULL(SUM(OcupadoNoDiaOficial), 0.0) AS DECIMAL(18,2)) AS OcupadoSlotDiasTotal,
    CAST(ISNULL(SUM(QuantidadeSlots), 0.0) AS DECIMAL(18,2)) AS CapacidadeSlotDiasTotal,
    CAST(ISNULL(SUM(ExcedenteNoDiaOficial), 0.0) AS DECIMAL(18,2)) AS ExcedenteSlotDiasTotal,
    CAST(
        CASE
            WHEN ISNULL(SUM(QuantidadeSlots), 0.0) <= 0 THEN NULL
            ELSE ISNULL(SUM(OcupadoNoDiaOficial), 0.0) * 100.0 / SUM(QuantidadeSlots)
        END
    AS DECIMAL(18,2)) AS OcupacaoPct
INTO #kpi_oficial
FROM #ocupacao_oficial_por_dia;

SELECT
    CAST(ISNULL(MAX(OcupadoNoDiaPainelOficial), 0) AS INT) AS SlotsOcupadosPicoOficial
INTO #pico_oficial
FROM (
    SELECT
        Dia,
        SUM(OcupadoNoDiaOficial) AS OcupadoNoDiaPainelOficial
    FROM #ocupacao_oficial_por_dia
    GROUP BY
        Dia
) p;

SELECT
    k.QtdFaces,
    k.TotalDias,
    k.SlotsTotalDia,
    k.OcupadoSlotDiasTotal,
    k.CapacidadeSlotDiasTotal,
    k.ExcedenteSlotDiasTotal,
    k.OcupacaoPct,
    p.SlotsOcupadosPicoOficial
FROM #kpi_oficial k
CROSS JOIN #pico_oficial p
OPTION (RECOMPILE);

SELECT
    pm.Ano,
    pm.Mes,
    CAST(pm.OcupadoSlotDiasMes AS INT) AS OcupadoSlotDiasMes,
    CAST(pm.CapacidadeSlotDiasMes AS INT) AS CapacidadeSlotDiasMes,
    CAST(pm.ExcedenteSlotDiasMes AS INT) AS ExcedenteSlotDiasMes,
    pm.OcupacaoPctMes
FROM #por_mes_oficial pm
ORDER BY
    pm.Ano,
    pm.Mes
OPTION (RECOMPILE);


SELECT
    fin.ReceitaPeriodo,
    fin.CustoPeriodo,
    fin.RentabilidadeValor,
    fin.MargemPct
FROM #financeiro fin
OPTION (RECOMPILE);

SELECT
    cf.QtdDiasCdi,
    cf.CdiSomaPercentDia,
    cf.CdiFatorPeriodo,
    cf.CdiPercentPeriodo
FROM #cdi_final cf
OPTION (RECOMPILE);

SELECT
    t.DataReferencia,
    t.CdiPercentDiaRaw,
    t.CdiPercentDia,
    t.CdiPercentAno,
    t.SelicPercentDiaRaw,
    t.SelicPercentDia,
    t.SelicPercentAno,
    t.DataAtualizacao
FROM Integracao.Silver.DimTaxaJurosDiaria t
WHERE t.DataReferencia BETWEEN @DtIni AND @DtFim
ORDER BY
    t.DataReferencia ASC
OPTION (RECOMPILE);

;WITH sp500_base_raw AS (
    SELECT
        CONVERT(DATE, s.[Data]) AS DataRef,
        TRY_CONVERT(DECIMAL(18,8), s.UltimoBRL) AS UltimoBRL,
        ROW_NUMBER() OVER (
            PARTITION BY CONVERT(DATE, s.[Data])
            ORDER BY s.[Data] DESC
        ) AS rn
    FROM Integracao.Silver.DimHistoricoSP500 s
    WHERE s.[Data] >= @DtIni
      AND s.[Data] < DATEADD(DAY, 1, @DtFim)
),
sp500_base AS (
    SELECT
        DataRef,
        UltimoBRL
    FROM sp500_base_raw
    WHERE rn = 1
      AND UltimoBRL IS NOT NULL
      AND UltimoBRL > 0
),
sp500_ini AS (
    SELECT TOP (1)
        DataRef AS DataInicioEfetiva,
        UltimoBRL AS UltimoBRL_Inicio
    FROM sp500_base
    ORDER BY
        DataRef ASC
),
sp500_fim AS (
    SELECT TOP (1)
        DataRef AS DataFimEfetiva,
        UltimoBRL AS UltimoBRL_Fim
    FROM sp500_base
    ORDER BY
        DataRef DESC
),
sp500_validos AS (
    SELECT
        b.DataRef,
        CAST(b.UltimoBRL AS FLOAT) AS UltimoBRL,
        LAG(CAST(b.UltimoBRL AS FLOAT)) OVER (ORDER BY b.DataRef) AS UltimoBRL_Anterior
    FROM sp500_base b
),
sp500_validos2 AS (
    SELECT
        DataRef,
        (UltimoBRL / UltimoBRL_Anterior) AS FatorDia
    FROM sp500_validos
    WHERE UltimoBRL_Anterior IS NOT NULL
      AND UltimoBRL_Anterior > 0
      AND (UltimoBRL / UltimoBRL_Anterior) > 0
),
sp500_agregado AS (
    SELECT
        COUNT(*) AS QtdDiasComRetorno,
        EXP(SUM(LOG(FatorDia))) AS FatorPeriodo
    FROM sp500_validos2
),
sp500_preco AS (
    SELECT
        CAST(
            CAST(f.UltimoBRL_Fim AS FLOAT) / NULLIF(CAST(i.UltimoBRL_Inicio AS FLOAT), 0.0)
        AS FLOAT) AS FatorPorPreco
    FROM sp500_ini i
    CROSS JOIN sp500_fim f
)
SELECT
    i.DataInicioEfetiva,
    f.DataFimEfetiva,
    i.UltimoBRL_Inicio,
    f.UltimoBRL_Fim,
    CAST(ISNULL(a.QtdDiasComRetorno, 0) AS INT) AS QtdDias,
    CAST(
        COALESCE(a.FatorPeriodo, p.FatorPorPreco, 1.0)
    AS DECIMAL(38,18)) AS FatorPeriodo,
    CAST(
        (COALESCE(a.FatorPeriodo, p.FatorPorPreco, 1.0) - 1.0) * 100.0
    AS DECIMAL(38,10)) AS RetornoBRL_PercentPeriodo
FROM sp500_agregado a
CROSS JOIN sp500_ini i
CROSS JOIN sp500_fim f
CROSS JOIN sp500_preco p
OPTION (RECOMPILE);