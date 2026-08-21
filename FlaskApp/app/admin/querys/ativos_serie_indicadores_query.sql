SET NOCOUNT ON;

DECLARE @DtIni DATE = TRY_CONVERT(date, :dt_ini);
DECLARE @DtFim DATE = TRY_CONVERT(date, :dt_fim);

IF @DtIni IS NULL OR @DtFim IS NULL
BEGIN
    SELECT
        CAST(NULL AS varchar(7)) AS Mes,
        0.0 AS cdi,
        0.0 AS dolar,
        0.0 AS sp500_brl,
        0.0 AS cco,
        0.0 AS icon,
        0.0 AS iimob,
        0.0 AS iind,
        0.0 AS lamr,
        0.0 AS out,
        0.0 AS ifin,
        0.0 AS ouro,
        0.0 AS petroleo,
        0.0 AS ooh,
        0.0 AS ooh_global
    WHERE 1 = 0;
    RETURN;
END;

SET @DtIni = DATEFROMPARTS(YEAR(@DtIni), MONTH(@DtIni), 1);
SET @DtFim = EOMONTH(@DtFim);

;WITH meses AS (
    SELECT
        DATEFROMPARTS(c.Ano, c.Mes, 1) AS MesRef
    FROM Integracao.Silver.DimCalendario c WITH (NOLOCK)
    WHERE c.[Data] >= @DtIni
      AND c.[Data] <= @DtFim
    GROUP BY c.Ano, c.Mes
),

--------------------------------------------------------------------------------
-- CDI DIÁRIO
--------------------------------------------------------------------------------
cdi_d AS (
    SELECT
        TRY_CONVERT(date, t.DataReferencia) AS DataRef,
        COALESCE(
            TRY_CONVERT(decimal(18,8), t.CdiPercentDiaRaw),
            TRY_CONVERT(
                decimal(18,8),
                REPLACE(
                    REPLACE(
                        NULLIF(LTRIM(RTRIM(CAST(t.CdiPercentDia AS nvarchar(50)))), ''),
                        ',', '.'
                    ),
                    '%', ''
                )
            )
        ) AS RetPct
    FROM Integracao.Silver.DimTaxaJurosDiaria t WITH (NOLOCK)
    WHERE TRY_CONVERT(date, t.DataReferencia) BETWEEN @DtIni AND @DtFim
),
cdi_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetPct IS NULL THEN NULL
                WHEN (1 + (d.RetPct / 100.0)) <= 0 THEN NULL
                ELSE LOG(1 + (d.RetPct / 100.0))
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN cdi_d d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- S&P 500 BRL DIÁRIO
--------------------------------------------------------------------------------
sp_d AS (
    SELECT
        TRY_CONVERT(date, s.[Data]) AS DataRef,
        COALESCE(
            TRY_CONVERT(decimal(18,8), s.RetornoBRL_Decimal),
            CASE
                WHEN TRY_CONVERT(decimal(18,8), s.RetornoBRL_Percent) IS NULL THEN NULL
                ELSE TRY_CONVERT(decimal(18,8), s.RetornoBRL_Percent) / 100.0
            END
        ) AS RetDec
    FROM Integracao.Silver.DimHistoricoSP500 s WITH (NOLOCK)
    WHERE TRY_CONVERT(date, s.[Data]) BETWEEN @DtIni AND @DtFim
),
sp_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN sp_d d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- DÓLAR DIÁRIO
--------------------------------------------------------------------------------
usd_d_raw AS (
    SELECT
        TRY_CONVERT(date, d.DataHoraCotacao) AS DataRef,
        TRY_CONVERT(decimal(18,8), d.CotacaoVenda) AS Px
    FROM Integracao.Silver.DimCotacaoDolar d WITH (NOLOCK)
    WHERE TRY_CONVERT(date, d.DataHoraCotacao) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), d.CotacaoVenda) IS NOT NULL
),
usd_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM (
        SELECT
            DataRef,
            MAX(Px) AS Px
        FROM usd_d_raw
        WHERE DataRef IS NOT NULL
        GROUP BY DataRef
    ) x
),
usd_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM usd_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
usd_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN usd_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- CCO
--------------------------------------------------------------------------------
cco_px_raw AS (
    SELECT
        TRY_CONVERT(date, c.DataCotacao) AS DataRef,
        TRY_CONVERT(decimal(18,8), c.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoCotacaoDiariaCCO c WITH (NOLOCK)
    WHERE TRY_CONVERT(date, c.DataCotacao) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), c.UltimoBRL) IS NOT NULL
),
cco_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM cco_px_raw
    WHERE DataRef IS NOT NULL
      AND Px IS NOT NULL
    GROUP BY DataRef
),
cco_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM cco_px
),
cco_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM cco_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
cco_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN cco_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- ICON (ÍNDICE DE CONSUMO)
--------------------------------------------------------------------------------
icon_px_raw AS (
    SELECT
        TRY_CONVERT(date, i.DataCotacao) AS DataRef,
        TRY_CONVERT(decimal(18,8), i.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoCotacaoDiariaIndiceConsumo i WITH (NOLOCK)
    WHERE TRY_CONVERT(date, i.DataCotacao) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), i.UltimoBRL) IS NOT NULL
),
icon_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM icon_px_raw
    WHERE DataRef IS NOT NULL
      AND Px IS NOT NULL
    GROUP BY DataRef
),
icon_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM icon_px
),
icon_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM icon_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
icon_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN icon_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- IIMOB (ÍNDICE IMOBILIÁRIO)
--------------------------------------------------------------------------------
iimob_px_raw AS (
    SELECT
        TRY_CONVERT(date, i.DataCotacao) AS DataRef,
        TRY_CONVERT(decimal(18,8), i.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoCotacaoDiariaIndiceImobiliario i WITH (NOLOCK)
    WHERE TRY_CONVERT(date, i.DataCotacao) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), i.UltimoBRL) IS NOT NULL
),
iimob_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM iimob_px_raw
    WHERE DataRef IS NOT NULL
      AND Px IS NOT NULL
    GROUP BY DataRef
),
iimob_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM iimob_px
),
iimob_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM iimob_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
iimob_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN iimob_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- IIND (ÍNDICE INDUSTRIAL)
--------------------------------------------------------------------------------
iind_px_raw AS (
    SELECT
        TRY_CONVERT(date, i.DataCotacao) AS DataRef,
        TRY_CONVERT(decimal(18,8), i.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoCotacaoDiariaIndiceIndustrial i WITH (NOLOCK)
    WHERE TRY_CONVERT(date, i.DataCotacao) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), i.UltimoBRL) IS NOT NULL
),
iind_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM iind_px_raw
    WHERE DataRef IS NOT NULL
      AND Px IS NOT NULL
    GROUP BY DataRef
),
iind_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM iind_px
),
iind_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM iind_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
iind_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN iind_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- LAMR
--------------------------------------------------------------------------------
lamr_px_raw AS (
    SELECT
        TRY_CONVERT(date, l.DataCotacao) AS DataRef,
        TRY_CONVERT(decimal(18,8), l.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoCotacaoDiariaLAMR l WITH (NOLOCK)
    WHERE TRY_CONVERT(date, l.DataCotacao) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), l.UltimoBRL) IS NOT NULL
),
lamr_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM lamr_px_raw
    WHERE DataRef IS NOT NULL
    GROUP BY DataRef
),
lamr_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM lamr_px
),
lamr_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM lamr_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
lamr_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN lamr_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- OUT
--------------------------------------------------------------------------------
out_px_raw AS (
    SELECT
        TRY_CONVERT(date, o.DataCotacao) AS DataRef,
        TRY_CONVERT(decimal(18,8), o.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoCotacaoDiariaOUT o WITH (NOLOCK)
    WHERE TRY_CONVERT(date, o.DataCotacao) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), o.UltimoBRL) IS NOT NULL
),
out_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM out_px_raw
    WHERE DataRef IS NOT NULL
    GROUP BY DataRef
),
out_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM out_px
),
out_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM out_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
out_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN out_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- IFIN (ÍNDICE FINANCEIRO)
--------------------------------------------------------------------------------
ifin_px_raw AS (
    SELECT
        TRY_CONVERT(date, f.DataIndiceFinanceiro) AS DataRef,
        TRY_CONVERT(decimal(18,8), f.Ultimo) AS Px
    FROM DataMining.Silver.FatoCotacaoIndiceFinanceiro f WITH (NOLOCK)
    WHERE TRY_CONVERT(date, f.DataIndiceFinanceiro) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), f.Ultimo) IS NOT NULL
),
ifin_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM ifin_px_raw
    WHERE DataRef IS NOT NULL
      AND Px IS NOT NULL
    GROUP BY DataRef
),
ifin_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM ifin_px
),
ifin_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM ifin_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
ifin_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN ifin_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- OURO
--------------------------------------------------------------------------------
ouro_px_raw AS (
    SELECT
        TRY_CONVERT(date, o.DataOuro) AS DataRef,
        TRY_CONVERT(decimal(18,8), o.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoCotacaoOuro o WITH (NOLOCK)
    WHERE TRY_CONVERT(date, o.DataOuro) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), o.UltimoBRL) IS NOT NULL
),
ouro_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM ouro_px_raw
    WHERE DataRef IS NOT NULL
      AND Px IS NOT NULL
    GROUP BY DataRef
),
ouro_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM ouro_px
),
ouro_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM ouro_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
ouro_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN ouro_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- PETRÓLEO
--------------------------------------------------------------------------------
pet_px_raw AS (
    SELECT
        TRY_CONVERT(date, p.[Data]) AS DataRef,
        TRY_CONVERT(decimal(18,8), p.UltimoBRL) AS Px
    FROM DataMining.Silver.FatoPetroleoDiario p WITH (NOLOCK)
    WHERE TRY_CONVERT(date, p.[Data]) BETWEEN DATEADD(DAY, -10, @DtIni) AND @DtFim
      AND TRY_CONVERT(decimal(18,8), p.UltimoBRL) IS NOT NULL
),
pet_px AS (
    SELECT
        DataRef,
        MAX(Px) AS Px
    FROM pet_px_raw
    WHERE DataRef IS NOT NULL
      AND Px IS NOT NULL
    GROUP BY DataRef
),
pet_d AS (
    SELECT
        DataRef,
        Px,
        LAG(Px) OVER (ORDER BY DataRef) AS PxAnt
    FROM pet_px
),
pet_d_ret AS (
    SELECT
        DataRef,
        CASE
            WHEN PxAnt IS NULL OR PxAnt = 0 THEN NULL
            ELSE (Px / PxAnt) - 1
        END AS RetDec
    FROM pet_d
    WHERE DataRef BETWEEN @DtIni AND @DtFim
),
pet_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN pet_d_ret d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- OOH DIÁRIO
--------------------------------------------------------------------------------
ooh_d AS (
    SELECT
        TRY_CONVERT(date, x.[Data]) AS DataRef,
        TRY_CONVERT(decimal(18,8), x.VariacaoPercent) / 100.0 AS RetDec
    FROM Integracao.Silver.FatoIndiceOOHDiario x WITH (NOLOCK)
    WHERE TRY_CONVERT(date, x.[Data]) BETWEEN @DtIni AND @DtFim
),
ooh_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN ooh_d d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- OOH GLOBAL
--------------------------------------------------------------------------------
oohg_d AS (
    SELECT
        TRY_CONVERT(date, x.[Data]) AS DataRef,
        TRY_CONVERT(decimal(18,8), x.VariacaoPercent) / 100.0 AS RetDec
    FROM Integracao.Silver.FatoIndiceOOHGlobal x WITH (NOLOCK)
    WHERE TRY_CONVERT(date, x.[Data]) BETWEEN @DtIni AND @DtFim
),
oohg_m AS (
    SELECT
        m.MesRef,
        (EXP(SUM(
            CASE
                WHEN d.RetDec IS NULL THEN NULL
                WHEN (1 + d.RetDec) <= 0 THEN NULL
                ELSE LOG(1 + d.RetDec)
            END
        )) - 1) AS RetDec
    FROM meses m
    LEFT JOIN oohg_d d
        ON d.DataRef >= m.MesRef
       AND d.DataRef < DATEADD(MONTH, 1, m.MesRef)
    GROUP BY m.MesRef
),

--------------------------------------------------------------------------------
-- JOIN MENSAL
--------------------------------------------------------------------------------
join_m AS (
    SELECT
        m.MesRef,
        CONVERT(varchar(7), m.MesRef, 120) AS Mes,

        ISNULL(c.RetDec, 0)     AS cdi_dec,
        ISNULL(u.RetDec, 0)     AS usd_dec,
        ISNULL(s.RetDec, 0)     AS sp_dec,
        ISNULL(cco.RetDec, 0)   AS cco_dec,
        ISNULL(icon.RetDec, 0)  AS icon_dec,
        ISNULL(iimob.RetDec, 0) AS iimob_dec,
        ISNULL(iind.RetDec, 0)  AS iind_dec,
        ISNULL(lamr.RetDec, 0)  AS lamr_dec,
        ISNULL(outt.RetDec, 0)  AS out_dec,
        ISNULL(ifin.RetDec, 0)  AS ifin_dec,
        ISNULL(ouro.RetDec, 0)  AS ouro_dec,
        ISNULL(pet.RetDec, 0)   AS petroleo_dec,
        ISNULL(ooh.RetDec, 0)   AS ooh_dec,
        ISNULL(oohg.RetDec, 0)  AS ooh_global_dec
    FROM meses m
    LEFT JOIN cdi_m   c     ON c.MesRef = m.MesRef
    LEFT JOIN usd_m   u     ON u.MesRef = m.MesRef
    LEFT JOIN sp_m    s     ON s.MesRef = m.MesRef
    LEFT JOIN cco_m   cco   ON cco.MesRef = m.MesRef
    LEFT JOIN icon_m  icon  ON icon.MesRef = m.MesRef
    LEFT JOIN iimob_m iimob ON iimob.MesRef = m.MesRef
    LEFT JOIN iind_m  iind  ON iind.MesRef = m.MesRef
    LEFT JOIN lamr_m  lamr  ON lamr.MesRef = m.MesRef
    LEFT JOIN out_m   outt  ON outt.MesRef = m.MesRef
    LEFT JOIN ifin_m  ifin  ON ifin.MesRef = m.MesRef
    LEFT JOIN ouro_m  ouro  ON ouro.MesRef = m.MesRef
    LEFT JOIN pet_m   pet   ON pet.MesRef = m.MesRef
    LEFT JOIN ooh_m   ooh   ON ooh.MesRef = m.MesRef
    LEFT JOIN oohg_m  oohg  ON oohg.MesRef = m.MesRef
),

--------------------------------------------------------------------------------
-- ÍNDICES ACUMULADOS
--------------------------------------------------------------------------------
idx AS (
    SELECT
        MesRef,
        Mes,

        EXP(SUM(LOG(1 + cdi_dec       )) OVER (ORDER BY MesRef)) AS idx_cdi,
        EXP(SUM(LOG(1 + usd_dec       )) OVER (ORDER BY MesRef)) AS idx_usd,
        EXP(SUM(LOG(1 + sp_dec        )) OVER (ORDER BY MesRef)) AS idx_sp,
        EXP(SUM(LOG(1 + cco_dec       )) OVER (ORDER BY MesRef)) AS idx_cco,
        EXP(SUM(LOG(1 + icon_dec      )) OVER (ORDER BY MesRef)) AS idx_icon,
        EXP(SUM(LOG(1 + iimob_dec     )) OVER (ORDER BY MesRef)) AS idx_iimob,
        EXP(SUM(LOG(1 + iind_dec      )) OVER (ORDER BY MesRef)) AS idx_iind,
        EXP(SUM(LOG(1 + lamr_dec      )) OVER (ORDER BY MesRef)) AS idx_lamr,
        EXP(SUM(LOG(1 + out_dec       )) OVER (ORDER BY MesRef)) AS idx_out,
        EXP(SUM(LOG(1 + ifin_dec      )) OVER (ORDER BY MesRef)) AS idx_ifin,
        EXP(SUM(LOG(1 + ouro_dec      )) OVER (ORDER BY MesRef)) AS idx_ouro,
        EXP(SUM(LOG(1 + petroleo_dec  )) OVER (ORDER BY MesRef)) AS idx_petroleo,
        EXP(SUM(LOG(1 + ooh_dec       )) OVER (ORDER BY MesRef)) AS idx_ooh,
        EXP(SUM(LOG(1 + ooh_global_dec)) OVER (ORDER BY MesRef)) AS idx_ooh_global
    FROM join_m
)

SELECT
    Mes,

    ((idx_cdi / NULLIF(FIRST_VALUE(idx_cdi ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS cdi,
    ((idx_usd / NULLIF(FIRST_VALUE(idx_usd ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS dolar,
    ((idx_sp / NULLIF(FIRST_VALUE(idx_sp ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS sp500_brl,
    ((idx_cco / NULLIF(FIRST_VALUE(idx_cco ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS cco,
    ((idx_icon / NULLIF(FIRST_VALUE(idx_icon ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS icon,
    ((idx_iimob / NULLIF(FIRST_VALUE(idx_iimob) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS iimob,
    ((idx_iind / NULLIF(FIRST_VALUE(idx_iind ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS iind,
    ((idx_lamr/ NULLIF(FIRST_VALUE(idx_lamr) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS lamr,
    ((idx_out / NULLIF(FIRST_VALUE(idx_out) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS out,
    ((idx_ifin / NULLIF(FIRST_VALUE(idx_ifin) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS ifin,
    ((idx_ouro / NULLIF(FIRST_VALUE(idx_ouro ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS ouro,
    ((idx_petroleo / NULLIF(FIRST_VALUE(idx_petroleo ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS petroleo,
    ((idx_ooh / NULLIF(FIRST_VALUE(idx_ooh ) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS ooh,
    ((idx_ooh_global / NULLIF(FIRST_VALUE(idx_ooh_global) OVER (ORDER BY MesRef), 0)) - 1) * 100.0 AS ooh_global

FROM idx
ORDER BY MesRef;