SET NOCOUNT ON;

DECLARE @base_indice decimal(18,6) = 100.0;

/* ============================================================
   1) LAMR (pega BRL e USD) + calcula câmbio implícito do dia
      FX = UltimoBRL / UltimoUSD
   ============================================================ */
IF OBJECT_ID('tempdb..#lamr') IS NOT NULL DROP TABLE #lamr;

SELECT
    Data = CAST(DataCotacao AS date),
    UltimoUSD = TRY_CONVERT(decimal(18,6), UltimoUSD),
    UltimoBRL = TRY_CONVERT(decimal(18,6), UltimoBRL),
    FX = CASE
            WHEN TRY_CONVERT(decimal(18,6), UltimoUSD) IS NULL OR TRY_CONVERT(decimal(18,6), UltimoUSD) = 0 THEN NULL
            WHEN TRY_CONVERT(decimal(18,6), UltimoBRL) IS NULL THEN NULL
            ELSE TRY_CONVERT(decimal(18,6), UltimoBRL) / TRY_CONVERT(decimal(18,6), UltimoUSD)
         END
INTO #lamr
FROM DataMining.Silver.FatoCotacaoDiariaLAMR
WHERE DataCotacao IS NOT NULL;

CREATE CLUSTERED INDEX IX_lamr_Data ON #lamr(Data);


/* ============================================================
   2) OUT (BRL)
   ============================================================ */
IF OBJECT_ID('tempdb..#out') IS NOT NULL DROP TABLE #out;

SELECT
    Data = CAST(DataCotacao AS date),
    UltimoBRL = TRY_CONVERT(decimal(18,6), UltimoBRL)
INTO #out
FROM DataMining.Silver.FatoCotacaoDiariaOUT
WHERE DataCotacao IS NOT NULL;

CREATE CLUSTERED INDEX IX_out_Data ON #out(Data);


/* ============================================================
   3) CCO (só tem "Último" -> assumo USD) e converto pra BRL usando FX do dia
   ============================================================ */
IF OBJECT_ID('tempdb..#cco') IS NOT NULL DROP TABLE #cco;

SELECT
    Data = CAST(c.[Data] AS date),
    UltimoUSD = TRY_CONVERT(decimal(18,6), REPLACE(CAST(c.[Último] AS varchar(50)), ',', '.'))
INTO #cco
FROM DataMining.dbo.CCO_Dados_Historicos c
WHERE c.[Data] IS NOT NULL;

CREATE CLUSTERED INDEX IX_cco_Data ON #cco(Data);


/* ============================================================
   4) Universo de datas válidas (interseção dos 3 + FX válido)
   ============================================================ */
IF OBJECT_ID('tempdb..#datas_ok') IS NOT NULL DROP TABLE #datas_ok;

SELECT
    d.Data
INTO #datas_ok
FROM (
    SELECT Data FROM #lamr
    INTERSECT
    SELECT Data FROM #out
    INTERSECT
    SELECT Data FROM #cco
) d
JOIN #lamr l
  ON l.Data = d.Data
WHERE l.FX IS NOT NULL
  AND l.FX > 0;

CREATE CLUSTERED INDEX IX_datasok_Data ON #datas_ok(Data);


/* ============================================================
   5) Preços finais (BRL) por ativo, já filtrado no universo
   ============================================================ */
IF OBJECT_ID('tempdb..#precos') IS NOT NULL DROP TABLE #precos;

SELECT
    ok.Data,
    CloseCCO_BRL  = CAST(cco.UltimoUSD * lamr.FX AS decimal(18,6)),
    CloseLAMR_BRL = CAST(lamr.UltimoBRL        AS decimal(18,6)),
    CloseOUT_BRL  = CAST([out].UltimoBRL       AS decimal(18,6))
INTO #precos
FROM #datas_ok ok
JOIN #lamr lamr ON lamr.Data = ok.Data
JOIN #cco  cco  ON cco.Data  = ok.Data
JOIN #out  [out] ON [out].Data = ok.Data
WHERE
    cco.UltimoUSD IS NOT NULL AND cco.UltimoUSD > 0
    AND lamr.UltimoBRL IS NOT NULL AND lamr.UltimoBRL > 0
    AND [out].UltimoBRL IS NOT NULL AND [out].UltimoBRL > 0;

CREATE CLUSTERED INDEX IX_precos_Data ON #precos(Data);


/* ============================================================
   6) Retornos diários por ativo (BRL)
      retorno = (preço_hoje / preço_ontem) - 1
   ============================================================ */
IF OBJECT_ID('tempdb..#retornos') IS NOT NULL DROP TABLE #retornos;

SELECT
    p.Data,
    RetCCO  = CASE
                WHEN LAG(p.CloseCCO_BRL)  OVER (ORDER BY p.Data) IS NULL THEN NULL
                WHEN LAG(p.CloseCCO_BRL)  OVER (ORDER BY p.Data) = 0    THEN NULL
                ELSE (p.CloseCCO_BRL / LAG(p.CloseCCO_BRL) OVER (ORDER BY p.Data)) - 1
              END,
    RetLAMR = CASE
                WHEN LAG(p.CloseLAMR_BRL) OVER (ORDER BY p.Data) IS NULL THEN NULL
                WHEN LAG(p.CloseLAMR_BRL) OVER (ORDER BY p.Data) = 0    THEN NULL
                ELSE (p.CloseLAMR_BRL / LAG(p.CloseLAMR_BRL) OVER (ORDER BY p.Data)) - 1
              END,
    RetOUT  = CASE
                WHEN LAG(p.CloseOUT_BRL)  OVER (ORDER BY p.Data) IS NULL THEN NULL
                WHEN LAG(p.CloseOUT_BRL)  OVER (ORDER BY p.Data) = 0    THEN NULL
                ELSE (p.CloseOUT_BRL / LAG(p.CloseOUT_BRL) OVER (ORDER BY p.Data)) - 1
              END
INTO #retornos
FROM #precos p;

CREATE CLUSTERED INDEX IX_retornos_Data ON #retornos(Data);


/* ============================================================
   7) Retorno ponderado do índice (pesos iguais 1/3)
      ret_indice = (RetCCO + RetLAMR + RetOUT) / 3
   ============================================================ */
IF OBJECT_ID('tempdb..#ret_indice') IS NOT NULL DROP TABLE #ret_indice;

SELECT
    r.Data,
    RetornoPonderado = CAST( (r.RetCCO + r.RetLAMR + r.RetOUT) / 3.0 AS decimal(18,10) )
INTO #ret_indice
FROM #retornos r
WHERE r.RetCCO  IS NOT NULL
  AND r.RetLAMR IS NOT NULL
  AND r.RetOUT  IS NOT NULL;

CREATE CLUSTERED INDEX IX_retindice_Data ON #ret_indice(Data);


/* ============================================================
   8) Encadeia o índice (base 100 no primeiro dia)
      Ind_t = Ind_(t-1) * (1 + ret)
   ============================================================ */
WITH ordenado AS (
    SELECT
        Data,
        RetornoPonderado,
        rn = ROW_NUMBER() OVER (ORDER BY Data)
    FROM #ret_indice
),
cte AS (
    SELECT
        Data,
        PeriodoAnterior = CAST(@base_indice AS decimal(18,6)),
        PeriodoAtual    = CAST(@base_indice * (1 + RetornoPonderado) AS decimal(18,6)),
        VariacaoPercent = CAST(RetornoPonderado * 100 AS decimal(18,6)),
        rn
    FROM ordenado
    WHERE rn = 1

    UNION ALL

    SELECT
        o.Data,
        PeriodoAnterior = c.PeriodoAtual,
        PeriodoAtual    = CAST(c.PeriodoAtual * (1 + o.RetornoPonderado) AS decimal(18,6)),
        VariacaoPercent = CAST(o.RetornoPonderado * 100 AS decimal(18,6)),
        o.rn
    FROM cte c
    JOIN ordenado o
      ON o.rn = c.rn + 1
)
SELECT
    Data,
    PeriodoAnterior,
    PeriodoAtual,
    VariacaoPercent
FROM cte
ORDER BY Data
OPTION (MAXRECURSION 0);





-- Tabela OOH Global



IF OBJECT_ID('DataMining.Silver.FatoOOHGlobal', 'U') IS NULL
BEGIN
    CREATE TABLE DataMining.Silver.FatoOOHGlobal (
        IDFatoOOHGlobal      INT IDENTITY(1,1) NOT NULL,
        Data                 DATE NOT NULL,

        PeriodoAnterior      DECIMAL(18,6) NOT NULL,
        PeriodoAtual         DECIMAL(18,6) NOT NULL,
        VariacaoPercent      DECIMAL(18,6) NOT NULL,

        DataAtualizacao      DATETIME2(0) NOT NULL
            CONSTRAINT DF_FatoOOHGlobal_DataAtualizacao DEFAULT (SYSDATETIME()),

        CONSTRAINT PK_FatoOOHGlobal PRIMARY KEY CLUSTERED (IDFatoOOHGlobal),
        CONSTRAINT UQ_FatoOOHGlobal_Data UNIQUE (Data)
    );

    CREATE INDEX IX_FatoOOHGlobal_Data ON DataMining.Silver.FatoOOHGlobal (Data);
END
GO




--- upsert ooh global



SET NOCOUNT ON;

DECLARE @base_indice decimal(18,6) = 100.0;

/* ============================================================
   1) LAMR (pega BRL e USD) + calcula câmbio implícito do dia
      FX = UltimoBRL / UltimoUSD
   ============================================================ */
IF OBJECT_ID('tempdb..#lamr') IS NOT NULL DROP TABLE #lamr;

SELECT
    Data = CAST(DataCotacao AS date),
    UltimoUSD = TRY_CONVERT(decimal(18,6), UltimoUSD),
    UltimoBRL = TRY_CONVERT(decimal(18,6), UltimoBRL),
    FX = CASE
            WHEN TRY_CONVERT(decimal(18,6), UltimoUSD) IS NULL OR TRY_CONVERT(decimal(18,6), UltimoUSD) = 0 THEN NULL
            WHEN TRY_CONVERT(decimal(18,6), UltimoBRL) IS NULL THEN NULL
            ELSE TRY_CONVERT(decimal(18,6), UltimoBRL) / TRY_CONVERT(decimal(18,6), UltimoUSD)
         END
INTO #lamr
FROM DataMining.Silver.FatoCotacaoDiariaLAMR
WHERE DataCotacao IS NOT NULL;

CREATE CLUSTERED INDEX IX_lamr_Data ON #lamr(Data);


/* ============================================================
   2) OUT (BRL)
   ============================================================ */
IF OBJECT_ID('tempdb..#out') IS NOT NULL DROP TABLE #out;

SELECT
    Data = CAST(DataCotacao AS date),
    UltimoBRL = TRY_CONVERT(decimal(18,6), UltimoBRL)
INTO #out
FROM DataMining.Silver.FatoCotacaoDiariaOUT
WHERE DataCotacao IS NOT NULL;

CREATE CLUSTERED INDEX IX_out_Data ON #out(Data);


/* ============================================================
   3) CCO (só tem "Último" -> assumo USD) e converto pra BRL usando FX do dia
   ============================================================ */
IF OBJECT_ID('tempdb..#cco') IS NOT NULL DROP TABLE #cco;

SELECT
    Data = CAST(c.[Data] AS date),
    UltimoUSD = TRY_CONVERT(decimal(18,6), REPLACE(CAST(c.[Último] AS varchar(50)), ',', '.'))
INTO #cco
FROM DataMining.dbo.CCO_Dados_Historicos c
WHERE c.[Data] IS NOT NULL;

CREATE CLUSTERED INDEX IX_cco_Data ON #cco(Data);


/* ============================================================
   4) Universo de datas válidas (interseção dos 3 + FX válido)
   ============================================================ */
IF OBJECT_ID('tempdb..#datas_ok') IS NOT NULL DROP TABLE #datas_ok;

SELECT
    d.Data
INTO #datas_ok
FROM (
    SELECT Data FROM #lamr
    INTERSECT
    SELECT Data FROM #out
    INTERSECT
    SELECT Data FROM #cco
) d
JOIN #lamr l
  ON l.Data = d.Data
WHERE l.FX IS NOT NULL
  AND l.FX > 0;

CREATE CLUSTERED INDEX IX_datasok_Data ON #datas_ok(Data);


/* ============================================================
   5) Preços finais (BRL) por ativo, já filtrado no universo
   ============================================================ */
IF OBJECT_ID('tempdb..#precos') IS NOT NULL DROP TABLE #precos;

SELECT
    ok.Data,
    CloseCCO_BRL  = CAST(cco.UltimoUSD * lamr.FX AS decimal(18,6)),
    CloseLAMR_BRL = CAST(lamr.UltimoBRL        AS decimal(18,6)),
    CloseOUT_BRL  = CAST([out].UltimoBRL       AS decimal(18,6))
INTO #precos
FROM #datas_ok ok
JOIN #lamr lamr ON lamr.Data = ok.Data
JOIN #cco  cco  ON cco.Data  = ok.Data
JOIN #out  [out] ON [out].Data = ok.Data
WHERE
    cco.UltimoUSD IS NOT NULL AND cco.UltimoUSD > 0
    AND lamr.UltimoBRL IS NOT NULL AND lamr.UltimoBRL > 0
    AND [out].UltimoBRL IS NOT NULL AND [out].UltimoBRL > 0;

CREATE CLUSTERED INDEX IX_precos_Data ON #precos(Data);


/* ============================================================
   6) Retornos diários por ativo (BRL)
      retorno = (preço_hoje / preço_ontem) - 1
   ============================================================ */
IF OBJECT_ID('tempdb..#retornos') IS NOT NULL DROP TABLE #retornos;

SELECT
    p.Data,
    RetCCO  = CASE
                WHEN LAG(p.CloseCCO_BRL)  OVER (ORDER BY p.Data) IS NULL THEN NULL
                WHEN LAG(p.CloseCCO_BRL)  OVER (ORDER BY p.Data) = 0    THEN NULL
                ELSE (p.CloseCCO_BRL / LAG(p.CloseCCO_BRL) OVER (ORDER BY p.Data)) - 1
              END,
    RetLAMR = CASE
                WHEN LAG(p.CloseLAMR_BRL) OVER (ORDER BY p.Data) IS NULL THEN NULL
                WHEN LAG(p.CloseLAMR_BRL) OVER (ORDER BY p.Data) = 0    THEN NULL
                ELSE (p.CloseLAMR_BRL / LAG(p.CloseLAMR_BRL) OVER (ORDER BY p.Data)) - 1
              END,
    RetOUT  = CASE
                WHEN LAG(p.CloseOUT_BRL)  OVER (ORDER BY p.Data) IS NULL THEN NULL
                WHEN LAG(p.CloseOUT_BRL)  OVER (ORDER BY p.Data) = 0    THEN NULL
                ELSE (p.CloseOUT_BRL / LAG(p.CloseOUT_BRL) OVER (ORDER BY p.Data)) - 1
              END
INTO #retornos
FROM #precos p;

CREATE CLUSTERED INDEX IX_retornos_Data ON #retornos(Data);


/* ============================================================
   7) Retorno ponderado do índice (pesos iguais 1/3)
      ret_indice = (RetCCO + RetLAMR + RetOUT) / 3
   ============================================================ */
IF OBJECT_ID('tempdb..#ret_indice') IS NOT NULL DROP TABLE #ret_indice;

SELECT
    r.Data,
    RetornoPonderado = CAST( (r.RetCCO + r.RetLAMR + r.RetOUT) / 3.0 AS decimal(18,10) )
INTO #ret_indice
FROM #retornos r
WHERE r.RetCCO  IS NOT NULL
  AND r.RetLAMR IS NOT NULL
  AND r.RetOUT  IS NOT NULL;

CREATE CLUSTERED INDEX IX_retindice_Data ON #ret_indice(Data);


/* ============================================================
   8) Encadeia o índice (base 100 no primeiro dia)
      Ind_t = Ind_(t-1) * (1 + ret)
      -> materializa em #indice_final
   ============================================================ */
IF OBJECT_ID('tempdb..#indice_final') IS NOT NULL DROP TABLE #indice_final;

WITH ordenado AS (
    SELECT
        Data,
        RetornoPonderado,
        rn = ROW_NUMBER() OVER (ORDER BY Data)
    FROM #ret_indice
),
cte AS (
    SELECT
        Data,
        PeriodoAnterior = CAST(@base_indice AS decimal(18,6)),
        PeriodoAtual    = CAST(@base_indice * (1 + RetornoPonderado) AS decimal(18,6)),
        VariacaoPercent = CAST(RetornoPonderado * 100 AS decimal(18,6)),
        rn
    FROM ordenado
    WHERE rn = 1

    UNION ALL

    SELECT
        o.Data,
        PeriodoAnterior = c.PeriodoAtual,
        PeriodoAtual    = CAST(c.PeriodoAtual * (1 + o.RetornoPonderado) AS decimal(18,6)),
        VariacaoPercent = CAST(o.RetornoPonderado * 100 AS decimal(18,6)),
        o.rn
    FROM cte c
    JOIN ordenado o
      ON o.rn = c.rn + 1
)
SELECT
    Data,
    PeriodoAnterior,
    PeriodoAtual,
    VariacaoPercent
INTO #indice_final
FROM cte
OPTION (MAXRECURSION 0);

CREATE CLUSTERED INDEX IX_indicefinal_Data ON #indice_final(Data);


/* ============================================================
   9) UPSERT em DataMining.Silver.FatoOOHGlobal
   ============================================================ */
MERGE DataMining.Silver.FatoOOHGlobal AS T
USING (
    SELECT
        Data,
        PeriodoAnterior,
        PeriodoAtual,
        VariacaoPercent
    FROM #indice_final
) AS S
ON T.Data = S.Data
WHEN MATCHED THEN
    UPDATE SET
        T.PeriodoAnterior = S.PeriodoAnterior,
        T.PeriodoAtual    = S.PeriodoAtual,
        T.VariacaoPercent = S.VariacaoPercent,
        T.DataAtualizacao = SYSDATETIME()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (Data, PeriodoAnterior, PeriodoAtual, VariacaoPercent)
    VALUES (S.Data, S.PeriodoAnterior, S.PeriodoAtual, S.VariacaoPercent)
-- opcional: remover datas que não existem mais na fonte (quase nunca precisa)
-- WHEN NOT MATCHED BY SOURCE THEN DELETE
;

-- (Opcional) conferir resultado
SELECT TOP (50)
    *
FROM DataMining.Silver.FatoOOHGlobal
ORDER BY Data DESC;