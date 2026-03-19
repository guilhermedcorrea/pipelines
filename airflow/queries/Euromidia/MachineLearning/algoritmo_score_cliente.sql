SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @DataInicioTreino date = '2019-01-01';
DECLARE @DataFimTreino    date = EOMONTH(GETDATE(), -1);
DECLARE @HorizonteDias    int  = 90;

DROP TABLE IF EXISTS #numeros;
DROP TABLE IF EXISTS #meses;
DROP TABLE IF EXISTS #clientes;
DROP TABLE IF EXISTS #empresas;
DROP TABLE IF EXISTS #cnaes;
DROP TABLE IF EXISTS #contratos_cab;
DROP TABLE IF EXISTS #contratos_cliente;
DROP TABLE IF EXISTS #itens_enriquecidos;
DROP TABLE IF EXISTS #primeira_ultima_compra;
DROP TABLE IF EXISTS #snapshots;
DROP TABLE IF EXISTS #empresas_ibov;
DROP TABLE IF EXISTS #cnae_empresa_ibov;
DROP TABLE IF EXISTS #cotacao_empresa;
DROP TABLE IF EXISTS #variacao_empresa_dia;
DROP TABLE IF EXISTS #setor_dia;
DROP TABLE IF EXISTS #setor_mes;
DROP TABLE IF EXISTS #cdi_mes;
DROP TABLE IF EXISTS #dolar_mes;
DROP TABLE IF EXISTS #petroleo_mes;
DROP TABLE IF EXISTS #ouro_mes;
DROP TABLE IF EXISTS #ifnc_mes;
DROP TABLE IF EXISTS #industrial_mes;
DROP TABLE IF EXISTS #consumo_mes;
DROP TABLE IF EXISTS #imobiliario_mes;
DROP TABLE IF EXISTS #mercado_setor_mes;
DROP TABLE IF EXISTS #segmentos_painel;
DROP TABLE IF EXISTS #empresas_entorno_painel;
DROP TABLE IF EXISTS #base_features;

;WITH cte_numeros AS (
    SELECT 0 AS n
    UNION ALL
    SELECT n + 1
    FROM cte_numeros
    WHERE DATEADD(month, n + 1, @DataInicioTreino) <= @DataFimTreino
)
SELECT n
INTO #numeros
FROM cte_numeros
OPTION (MAXRECURSION 0);

CREATE CLUSTERED INDEX IX_#numeros_n ON #numeros(n);

SELECT
    DATEFROMPARTS(
        YEAR(DATEADD(month, n, @DataInicioTreino)),
        MONTH(DATEADD(month, n, @DataInicioTreino)),
        1
    ) AS MesRef,
    EOMONTH(DATEADD(month, n, @DataInicioTreino)) AS DataSnapshot
INTO #meses
FROM #numeros;

CREATE CLUSTERED INDEX IX_#meses_MesRef ON #meses(MesRef);
CREATE NONCLUSTERED INDEX IX_#meses_DataSnapshot ON #meses(DataSnapshot);

SELECT
    REPLACE(REPLACE(REPLACE(dc.CNPJ, '.', ''), '/', ''), '-', '') AS CNPJ_LIMPO,
    dc.IDEmpresa,
    dc.IDEmpresaProprietaria,
    dc.BitCliente,
    dc.RazaoSocial,
    dc.ClasseValor,
    dc.DescricaoClasseValor,
    dc.QtdContratos,
    dc.QtdPontos,
    dc.QtdFaces,
    dc.TotalCotasCompradas,
    dc.TempoTotalExibicao,
    dc.TipoEscalaOperacional,
    dc.DescricaoTipoEscalaOperacional,
    dc.CNAE,
    dc.Porte,
    dc.DescricaoCNAE,
    dc.ClasseCNAE,
    dc.SetorCNAE,
    dc.MacroSetorCNAE,
    dc.DependeCredito,
    dc.ConsumoDiscricionario,
    dc.InsumoImportado,
    dc.Exportador,
    dc.PoderRepassePreco,
    dc.CapitalSocial,
    dc.ClasseEstrutural,
    dc.QtdCidadesCliente,
    dc.QtdUFCliente,
    dc.QtdCidadesPainel,
    dc.QtdCEPPainel,
    dc.ItensMesmaCidade,
    dc.TotalItens,
    dc.PercItensMesmaCidade,
    dc.ClasseGeo,
    dc.ClassePotencial,
    dc.ReceitaTotal,
    dc.PercReceitaAcumulada,
    dc.ScoreRetornoCluster,
    dc.ClusterGrupoCliente,
    dc.ScoreRetornoTecnico
INTO #clientes
FROM [Integracao].[Silver].[DimClassificacacaoClientes] AS dc
WHERE dc.CNPJ IS NOT NULL
  AND dc.IDEmpresaProprietaria = 3
  AND EXISTS (
        SELECT 1
        FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS fi
        WHERE fi.CNPJ IS NOT NULL
          AND fi.DataInicioPrevisto IS NOT NULL
          AND REPLACE(REPLACE(REPLACE(fi.CNPJ, '.', ''), '/', ''), '-', '') =
              REPLACE(REPLACE(REPLACE(dc.CNPJ, '.', ''), '/', ''), '-', '')
  );

CREATE CLUSTERED INDEX IX_#clientes_CNPJ ON #clientes(CNPJ_LIMPO);
CREATE NONCLUSTERED INDEX IX_#clientes_IDEmpresa ON #clientes(IDEmpresa);
CREATE NONCLUSTERED INDEX IX_#clientes_IDEmpresaProprietaria ON #clientes(IDEmpresaProprietaria);

SELECT
    e.IDEmpresa,
    REPLACE(REPLACE(REPLACE(e.CNPJ, '.', ''), '/', ''), '-', '') AS CNPJ_LIMPO,
    e.CNAE AS CNAE_EMPRESA,
    e.Porte AS PorteEmpresa,
    e.RazaoSocial AS RazaoSocialEmpresa,
    e.NomeFantasia,
    e.CapitalSocial AS CapitalSocialEmpresa,
    UPPER(LTRIM(RTRIM(e.Municipio))) AS CidadeEmpresa,
    UPPER(LTRIM(RTRIM(e.UF))) AS UFEmpresa,
    e.DescricaoCnae
INTO #empresas
FROM [Integracao].[Silver].[DimEmpresas] AS e
WHERE e.CNPJ IS NOT NULL;

CREATE CLUSTERED INDEX IX_#empresas_IDEmpresa ON #empresas(IDEmpresa);
CREATE NONCLUSTERED INDEX IX_#empresas_CNPJ ON #empresas(CNPJ_LIMPO);

SELECT
    c.IDDimCnaes,
    c.cnaepadrao,
    c.Descricao,
    c.Classe,
    c.Setor,
    c.DependeCredito,
    c.ConsumoDiscricionario,
    c.InsumoImportado,
    c.Exportador,
    c.PoderRepassePreco
INTO #cnaes
FROM [Integracao].[Silver].[DimCnaes] AS c;

CREATE CLUSTERED INDEX IX_#cnaes_cnaepadrao ON #cnaes(cnaepadrao);
CREATE NONCLUSTERED INDEX IX_#cnaes_Setor ON #cnaes(Setor);

SELECT
    fc.IDFatoControleContratosEuromidia,
    fc.IDEmpresa,
    REPLACE(REPLACE(REPLACE(fc.CNPJ, '.', ''), '/', ''), '-', '') AS CNPJ_LIMPO,
    fc.DataLancamento AS DataLancamentoContrato,
    fc.DataAssinaturaRenovacao,
    fc.NumeroContrato,
    fc.NumeroPrevia,
    fc.QuantidadePontos,
    fc.QuantidadeFaces,
    fc.TotalLiquidoContratoAGBRCTACORDO AS ValorLiquidoContrato,
    fc.TotalBrutoContrato AS ValorBrutoContrato,
    fc.TotalFaturamentoLiquidoMensal AS ValorLiquidoMensalContrato,
    fc.TotalValorVendedor AS ValorVendedor,
    fc.IDCategoriaMarca
INTO #contratos_cab
FROM [Integracao].[Silver].[FatoControleContratosEuromidia] AS fc
WHERE fc.CNPJ IS NOT NULL
  AND EXISTS (
        SELECT 1
        FROM #clientes AS cl
        WHERE cl.CNPJ_LIMPO = REPLACE(REPLACE(REPLACE(fc.CNPJ, '.', ''), '/', ''), '-', '')
  );

CREATE CLUSTERED INDEX IX_#contratos_cab_ID ON #contratos_cab(IDFatoControleContratosEuromidia);
CREATE NONCLUSTERED INDEX IX_#contratos_cab_CNPJ_Data ON #contratos_cab(CNPJ_LIMPO, DataLancamentoContrato)
INCLUDE (NumeroContrato, ValorLiquidoContrato, ValorBrutoContrato, ValorLiquidoMensalContrato, ValorVendedor, IDEmpresa);

SELECT
    c.IDFatoControleContratosEuromidia,
    c.IDEmpresa,
    COALESCE(c.CNPJ_LIMPO, e.CNPJ_LIMPO) AS CNPJ_LIMPO,
    c.DataLancamentoContrato,
    c.DataAssinaturaRenovacao,
    c.NumeroContrato,
    c.NumeroPrevia,
    c.QuantidadePontos,
    c.QuantidadeFaces,
    c.ValorLiquidoContrato,
    c.ValorBrutoContrato,
    c.ValorLiquidoMensalContrato,
    c.ValorVendedor,
    c.IDCategoriaMarca
INTO #contratos_cliente
FROM #contratos_cab AS c
LEFT JOIN #empresas AS e
    ON e.IDEmpresa = c.IDEmpresa
WHERE COALESCE(c.CNPJ_LIMPO, e.CNPJ_LIMPO) IS NOT NULL
  AND EXISTS (
        SELECT 1
        FROM #clientes AS cl
        WHERE cl.CNPJ_LIMPO = COALESCE(c.CNPJ_LIMPO, e.CNPJ_LIMPO)
  );

CREATE CLUSTERED INDEX IX_#contratos_cliente_CNPJ_Data ON #contratos_cliente(CNPJ_LIMPO, DataLancamentoContrato);
CREATE NONCLUSTERED INDEX IX_#contratos_cliente_NumeroContrato ON #contratos_cliente(NumeroContrato)
INCLUDE (CNPJ_LIMPO, DataLancamentoContrato, ValorLiquidoContrato);

SELECT
    fi.IDFatoControleContratosItensEuromidia,
    fi.IDFatoControleContratoEuromidia,
    REPLACE(REPLACE(REPLACE(fi.CNPJ, '.', ''), '/', ''), '-', '') AS CNPJ_LIMPO,
    fi.Referencia,
    fi.NumeroContrato,
    fi.NumeroPrevia,
    fi.CodPonto,
    fi.CodFace,
    UPPER(LTRIM(RTRIM(fi.CidadeExibicao))) AS CidadeExibicao,
    UPPER(LTRIM(RTRIM(fi.Tipo))) AS TipoPainel,
    fi.IDPainelEuromidia,
    fi.IDDimFacesPaineis,
    fi.DataInicioPrevisto,
    fi.DataTerminoPrevisto,
    fi.DataFimEfetiva,
    fi.DataCancelamento,
    fi.TexmpoExposicao AS TempoExibicaoDias,
    CASE
        WHEN UPPER(LTRIM(RTRIM(fi.Tipo))) LIKE '%DIGITAL%' THEN 1
        ELSE 0
    END AS FlagDigital,
    CASE
        WHEN fi.DataInicioPrevisto IS NOT NULL
         AND fi.DataTerminoPrevisto IS NOT NULL
        THEN DATEDIFF(day, fi.DataInicioPrevisto, fi.DataTerminoPrevisto) + 1
        ELSE NULL
    END AS DuracaoContratoDias
INTO #itens_enriquecidos
FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS fi
WHERE fi.CNPJ IS NOT NULL
  AND fi.DataInicioPrevisto IS NOT NULL
  AND EXISTS (
        SELECT 1
        FROM #clientes AS cl
        WHERE cl.CNPJ_LIMPO = REPLACE(REPLACE(REPLACE(fi.CNPJ, '.', ''), '/', ''), '-', '')
  );

CREATE CLUSTERED INDEX IX_#itens_enriquecidos_CNPJ_Data ON #itens_enriquecidos(CNPJ_LIMPO, DataInicioPrevisto);
CREATE NONCLUSTERED INDEX IX_#itens_enriquecidos_CodPonto ON #itens_enriquecidos(CodPonto)
INCLUDE (CNPJ_LIMPO, DataInicioPrevisto);
CREATE NONCLUSTERED INDEX IX_#itens_enriquecidos_NumeroContrato ON #itens_enriquecidos(NumeroContrato)
INCLUDE (CNPJ_LIMPO, DataInicioPrevisto, CodPonto, CodFace);

SELECT
    i.CNPJ_LIMPO,
    MIN(i.DataInicioPrevisto) AS PrimeiraCompra,
    MAX(i.DataInicioPrevisto) AS UltimaCompraHistorica
INTO #primeira_ultima_compra
FROM #itens_enriquecidos AS i
GROUP BY i.CNPJ_LIMPO;

CREATE CLUSTERED INDEX IX_#primeira_ultima_compra_CNPJ ON #primeira_ultima_compra(CNPJ_LIMPO);

SELECT
    cl.CNPJ_LIMPO,
    m.MesRef,
    m.DataSnapshot
INTO #snapshots
FROM #clientes AS cl
INNER JOIN #primeira_ultima_compra AS pu
    ON pu.CNPJ_LIMPO = cl.CNPJ_LIMPO
CROSS JOIN #meses AS m
WHERE m.DataSnapshot >= pu.PrimeiraCompra;

CREATE CLUSTERED INDEX IX_#snapshots_CNPJ_Data ON #snapshots(CNPJ_LIMPO, DataSnapshot);
CREATE NONCLUSTERED INDEX IX_#snapshots_MesRef ON #snapshots(MesRef) INCLUDE (CNPJ_LIMPO, DataSnapshot);

SELECT
    e.IDDimEmpresasIbovespa,
    e.Ticker,
    e.CNAE
INTO #empresas_ibov
FROM [DataMining].[Silver].[DimEmpresasIbovespa] AS e;

CREATE CLUSTERED INDEX IX_#empresas_ibov_ID ON #empresas_ibov(IDDimEmpresasIbovespa);
CREATE NONCLUSTERED INDEX IX_#empresas_ibov_CNAE ON #empresas_ibov(CNAE) INCLUDE (Ticker);

SELECT DISTINCT
    c.Setor,
    e.IDDimEmpresasIbovespa,
    e.Ticker
INTO #cnae_empresa_ibov
FROM #cnaes AS c
INNER JOIN #empresas_ibov AS e
    ON REPLACE(REPLACE(REPLACE(c.cnaepadrao, '.', ''), '-', ''), '/', '') =
       REPLACE(REPLACE(REPLACE(e.CNAE,      '.', ''), '-', ''), '/', '')
WHERE c.Setor IS NOT NULL;

CREATE CLUSTERED INDEX IX_#cnae_empresa_ibov_ID ON #cnae_empresa_ibov(IDDimEmpresasIbovespa, Setor);
CREATE NONCLUSTERED INDEX IX_#cnae_empresa_ibov_Setor ON #cnae_empresa_ibov(Setor, IDDimEmpresasIbovespa);

SELECT
    f.IDDimEmpresasIbovespa,
    f.DataCotacao,
    COALESCE(f.HistoricoFechamentoAjustado, f.UltimoPreco, f.PrecoFechamentoAnterior) AS PrecoBase
INTO #cotacao_empresa
FROM [DataMining].[Silver].[FatoCotacaoDiariaEmpresas] AS f
WHERE f.DataCotacao IS NOT NULL
  AND COALESCE(f.HistoricoFechamentoAjustado, f.UltimoPreco, f.PrecoFechamentoAnterior) IS NOT NULL;

CREATE CLUSTERED INDEX IX_#cotacao_empresa_ID_Data ON #cotacao_empresa(IDDimEmpresasIbovespa, DataCotacao);

;WITH cte_preco_lag AS (
    SELECT
        c.IDDimEmpresasIbovespa,
        c.DataCotacao,
        c.PrecoBase,
        LAG(c.PrecoBase) OVER (
            PARTITION BY c.IDDimEmpresasIbovespa
            ORDER BY c.DataCotacao
        ) AS PrecoAnterior
    FROM #cotacao_empresa AS c
)
SELECT
    p.IDDimEmpresasIbovespa,
    p.DataCotacao,
    CASE
        WHEN p.PrecoAnterior IS NULL OR p.PrecoAnterior = 0 THEN NULL
        ELSE ((p.PrecoBase - p.PrecoAnterior) / p.PrecoAnterior) * 100.0
    END AS VariacaoEmpresaDia
INTO #variacao_empresa_dia
FROM cte_preco_lag AS p;

CREATE CLUSTERED INDEX IX_#variacao_empresa_dia_ID_Data ON #variacao_empresa_dia(IDDimEmpresasIbovespa, DataCotacao);

SELECT
    ce.Setor,
    v.DataCotacao AS DataRef,
    AVG(v.VariacaoEmpresaDia) AS VariacaoSetorDia
INTO #setor_dia
FROM #cnae_empresa_ibov AS ce
INNER JOIN #variacao_empresa_dia AS v
    ON v.IDDimEmpresasIbovespa = ce.IDDimEmpresasIbovespa
GROUP BY
    ce.Setor,
    v.DataCotacao;

CREATE CLUSTERED INDEX IX_#setor_dia_Setor_Data ON #setor_dia(Setor, DataRef);

SELECT
    s.Setor,
    DATEFROMPARTS(YEAR(s.DataRef), MONTH(s.DataRef), 1) AS MesRef,
    AVG(s.VariacaoSetorDia) AS RetornoMesSetor
INTO #setor_mes
FROM #setor_dia AS s
GROUP BY
    s.Setor,
    DATEFROMPARTS(YEAR(s.DataRef), MONTH(s.DataRef), 1);

CREATE CLUSTERED INDEX IX_#setor_mes_Setor_MesRef ON #setor_mes(Setor, MesRef);

SELECT
    DATEFROMPARTS(YEAR(t.DataReferencia), MONTH(t.DataReferencia), 1) AS MesRef,
    AVG(t.CdiPercentAno) AS CdiAnoMedioMes
INTO #cdi_mes
FROM [Integracao].[Silver].[DimTaxaJurosDiaria] AS t
WHERE t.DataReferencia >= @DataInicioTreino
  AND t.DataReferencia <= @DataFimTreino
GROUP BY DATEFROMPARTS(YEAR(t.DataReferencia), MONTH(t.DataReferencia), 1);

CREATE CLUSTERED INDEX IX_#cdi_mes_MesRef ON #cdi_mes(MesRef);

;WITH cte_dolar_dia AS (
    SELECT
        d.DataCotacao AS DataRef,
        COALESCE(d.CotacaoVenda, d.CotacaoCompra) AS CotacaoDolar
    FROM [Integracao].[Silver].[DimCotacaoDolar] AS d
    WHERE d.DataCotacao >= @DataInicioTreino
      AND d.DataCotacao <= @DataFimTreino
      AND COALESCE(d.CotacaoVenda, d.CotacaoCompra) IS NOT NULL
),
cte_dolar_lag AS (
    SELECT
        d.DataRef,
        d.CotacaoDolar,
        LAG(d.CotacaoDolar) OVER (ORDER BY d.DataRef) AS CotacaoAnterior
    FROM cte_dolar_dia AS d
)
SELECT
    DATEFROMPARTS(YEAR(d.DataRef), MONTH(d.DataRef), 1) AS MesRef,
    AVG(
        CASE
            WHEN d.CotacaoAnterior IS NULL OR d.CotacaoAnterior = 0 THEN NULL
            ELSE ((d.CotacaoDolar - d.CotacaoAnterior) / d.CotacaoAnterior) * 100.0
        END
    ) AS VariacaoDolarMes
INTO #dolar_mes
FROM cte_dolar_lag AS d
GROUP BY DATEFROMPARTS(YEAR(d.DataRef), MONTH(d.DataRef), 1);

CREATE CLUSTERED INDEX IX_#dolar_mes_MesRef ON #dolar_mes(MesRef);

;WITH cte_petroleo_dia AS (
    SELECT
        p.Data AS DataRef,
        p.UltimoUSD AS ValorPetroleo
    FROM [DataMining].[Silver].[FatoPetroleoDiario] AS p
    WHERE p.Data >= @DataInicioTreino
      AND p.Data <= @DataFimTreino
      AND p.UltimoUSD IS NOT NULL
),
cte_petroleo_lag AS (
    SELECT
        p.DataRef,
        p.ValorPetroleo,
        LAG(p.ValorPetroleo) OVER (ORDER BY p.DataRef) AS ValorAnterior
    FROM cte_petroleo_dia AS p
)
SELECT
    DATEFROMPARTS(YEAR(p.DataRef), MONTH(p.DataRef), 1) AS MesRef,
    AVG(
        CASE
            WHEN p.ValorAnterior IS NULL OR p.ValorAnterior = 0 THEN NULL
            ELSE ((p.ValorPetroleo - p.ValorAnterior) / p.ValorAnterior) * 100.0
        END
    ) AS VariacaoPetroleoMes
INTO #petroleo_mes
FROM cte_petroleo_lag AS p
GROUP BY DATEFROMPARTS(YEAR(p.DataRef), MONTH(p.DataRef), 1);

CREATE CLUSTERED INDEX IX_#petroleo_mes_MesRef ON #petroleo_mes(MesRef);

;WITH cte_ouro_dia AS (
    SELECT
        o.DataOuro AS DataRef,
        o.UltimoBRL AS CotacaoOuro
    FROM [DataMining].[Silver].[FatoCotacaoOuro] AS o
    WHERE o.DataOuro >= @DataInicioTreino
      AND o.DataOuro <= @DataFimTreino
      AND o.UltimoBRL IS NOT NULL
),
cte_ouro_lag AS (
    SELECT
        o.DataRef,
        o.CotacaoOuro,
        LAG(o.CotacaoOuro) OVER (ORDER BY o.DataRef) AS ValorAnterior
    FROM cte_ouro_dia AS o
)
SELECT
    DATEFROMPARTS(YEAR(o.DataRef), MONTH(o.DataRef), 1) AS MesRef,
    AVG(
        CASE
            WHEN o.ValorAnterior IS NULL OR o.ValorAnterior = 0 THEN NULL
            ELSE ((o.CotacaoOuro - o.ValorAnterior) / o.ValorAnterior) * 100.0
        END
    ) AS VariacaoOuroMes
INTO #ouro_mes
FROM cte_ouro_lag AS o
GROUP BY DATEFROMPARTS(YEAR(o.DataRef), MONTH(o.DataRef), 1);

CREATE CLUSTERED INDEX IX_#ouro_mes_MesRef ON #ouro_mes(MesRef);

SELECT
    DATEFROMPARTS(YEAR(f.DataIndiceFinanceiro), MONTH(f.DataIndiceFinanceiro), 1) AS MesRef,
    AVG(f.VariacaoPercentual) * 100.0 AS VariacaoIFNCMes
INTO #ifnc_mes
FROM [DataMining].[Silver].[FatoCotacaoIndiceFinanceiro] AS f
WHERE f.DataIndiceFinanceiro >= @DataInicioTreino
  AND f.DataIndiceFinanceiro <= @DataFimTreino
  AND f.VariacaoPercentual IS NOT NULL
GROUP BY DATEFROMPARTS(YEAR(f.DataIndiceFinanceiro), MONTH(f.DataIndiceFinanceiro), 1);

CREATE CLUSTERED INDEX IX_#ifnc_mes_MesRef ON #ifnc_mes(MesRef);

SELECT
    DATEFROMPARTS(YEAR(i.DataCotacao), MONTH(i.DataCotacao), 1) AS MesRef,
    AVG(i.VarBRL) AS VariacaoIndiceIndustrialMes
INTO #industrial_mes
FROM [DataMining].[Silver].[FatoCotacaoDiariaIndiceIndustrial] AS i
WHERE i.DataCotacao >= @DataInicioTreino
  AND i.DataCotacao <= @DataFimTreino
  AND i.VarBRL IS NOT NULL
GROUP BY DATEFROMPARTS(YEAR(i.DataCotacao), MONTH(i.DataCotacao), 1);

CREATE CLUSTERED INDEX IX_#industrial_mes_MesRef ON #industrial_mes(MesRef);

SELECT
    DATEFROMPARTS(YEAR(c.DataCotacao), MONTH(c.DataCotacao), 1) AS MesRef,
    AVG(c.VarBRL) AS VariacaoIndiceConsumoMes
INTO #consumo_mes
FROM [DataMining].[Silver].[FatoCotacaoDiariaIndiceConsumo] AS c
WHERE c.DataCotacao >= @DataInicioTreino
  AND c.DataCotacao <= @DataFimTreino
  AND c.VarBRL IS NOT NULL
GROUP BY DATEFROMPARTS(YEAR(c.DataCotacao), MONTH(c.DataCotacao), 1);

CREATE CLUSTERED INDEX IX_#consumo_mes_MesRef ON #consumo_mes(MesRef);

SELECT
    DATEFROMPARTS(YEAR(m.DataCotacao), MONTH(m.DataCotacao), 1) AS MesRef,
    AVG(m.VarBRL) AS VariacaoIndiceImobiliarioMes
INTO #imobiliario_mes
FROM [DataMining].[Silver].[FatoCotacaoDiariaIndiceImobiliario] AS m
WHERE m.DataCotacao >= @DataInicioTreino
  AND m.DataCotacao <= @DataFimTreino
  AND m.VarBRL IS NOT NULL
GROUP BY DATEFROMPARTS(YEAR(m.DataCotacao), MONTH(m.DataCotacao), 1);

CREATE CLUSTERED INDEX IX_#imobiliario_mes_MesRef ON #imobiliario_mes(MesRef);

SELECT
    s.Setor,
    s.MesRef,
    s.RetornoMesSetor,
    AVG(s.RetornoMesSetor) OVER (
        PARTITION BY s.Setor
        ORDER BY s.MesRef
        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
    ) AS Media3mRetornoSetor,
    AVG(s.RetornoMesSetor) OVER (
        PARTITION BY s.Setor
        ORDER BY s.MesRef
        ROWS BETWEEN 5 PRECEDING AND CURRENT ROW
    ) AS Media6mRetornoSetor,
    cdi.CdiAnoMedioMes,
    dol.VariacaoDolarMes,
    pet.VariacaoPetroleoMes,
    ouro.VariacaoOuroMes,
    ifnc.VariacaoIFNCMes,
    ind.VariacaoIndiceIndustrialMes,
    con.VariacaoIndiceConsumoMes,
    imo.VariacaoIndiceImobiliarioMes
INTO #mercado_setor_mes
FROM #setor_mes AS s
LEFT JOIN #cdi_mes         AS cdi  ON cdi.MesRef = s.MesRef
LEFT JOIN #dolar_mes       AS dol  ON dol.MesRef = s.MesRef
LEFT JOIN #petroleo_mes    AS pet  ON pet.MesRef = s.MesRef
LEFT JOIN #ouro_mes        AS ouro ON ouro.MesRef = s.MesRef
LEFT JOIN #ifnc_mes        AS ifnc ON ifnc.MesRef = s.MesRef
LEFT JOIN #industrial_mes  AS ind  ON ind.MesRef = s.MesRef
LEFT JOIN #consumo_mes     AS con  ON con.MesRef = s.MesRef
LEFT JOIN #imobiliario_mes AS imo  ON imo.MesRef = s.MesRef;

CREATE CLUSTERED INDEX IX_#mercado_setor_mes_Setor_MesRef ON #mercado_setor_mes(Setor, MesRef);

SELECT
    fs.CodPonto,
    COUNT(DISTINCT fs.Classe) AS QtdClassesSegmentoEntornoPainel,
    SUM(fs.QuantidadeClasse) AS QtdEmpresasSegmentadasEntornoPainel,
    MAX(fs.QuantidadeClasse) AS MaiorClasseEntornoPainel
INTO #segmentos_painel
FROM [DataMining].[Silver].[FatoSegmentosRegioes] AS fs
WHERE fs.CodPonto IS NOT NULL
GROUP BY fs.CodPonto;

CREATE CLUSTERED INDEX IX_#segmentos_painel_CodPonto ON #segmentos_painel(CodPonto);

SELECT
    fe.CodPonto,
    COUNT(*) AS QtdEmpresasEntornoPainel,
    COUNT(DISTINCT REPLACE(REPLACE(REPLACE(fe.CNPJ, '.', ''), '/', ''), '-', '')) AS QtdCnpjEntornoPainel,
    COUNT(DISTINCT fe.CNAE) AS QtdCnaesEntornoPainel,
    COUNT(DISTINCT fe.CEP) AS QtdCepsEntornoPainel
INTO #empresas_entorno_painel
FROM [DataMining].[Silver].[FatoEmpresasEntornoPainel] AS fe
WHERE fe.CodPonto IS NOT NULL
GROUP BY fe.CodPonto;

CREATE CLUSTERED INDEX IX_#empresas_entorno_painel_CodPonto ON #empresas_entorno_painel(CodPonto);

SELECT
    s.CNPJ_LIMPO,
    s.MesRef,
    s.DataSnapshot,

    cl.IDEmpresa,
    cl.IDEmpresaProprietaria,
    cl.BitCliente,
    cl.RazaoSocial,
    cl.ClasseValor,
    cl.DescricaoClasseValor,
    cl.CNAE,
    cl.Porte,
    cl.DescricaoCNAE,
    cl.ClasseCNAE,
    cl.SetorCNAE,
    cl.MacroSetorCNAE,
    cl.DependeCredito,
    cl.ConsumoDiscricionario,
    cl.InsumoImportado,
    cl.Exportador,
    cl.PoderRepassePreco,
    cl.CapitalSocial,
    cl.ClasseEstrutural,
    cl.QtdCidadesCliente,
    cl.QtdUFCliente,
    cl.QtdCidadesPainel,
    cl.QtdCEPPainel,
    cl.ItensMesmaCidade,
    cl.TotalItens,
    cl.PercItensMesmaCidade,
    cl.ClasseGeo,
    cl.ClassePotencial,
    cl.ReceitaTotal,
    cl.PercReceitaAcumulada,
    cl.ScoreRetornoCluster,
    cl.ClusterGrupoCliente,
    cl.ScoreRetornoTecnico,

    ult.UltimaDataCompra,
    CASE
        WHEN ult.UltimaDataCompra IS NULL THEN NULL
        ELSE DATEDIFF(day, ult.UltimaDataCompra, s.DataSnapshot)
    END AS DiasDesdeUltimaCompra,

    histc_vida.ContratosVida,
    histc_90.Contratos90d,
    histc_180.Contratos180d,
    histc_365.Contratos365d,

    histc_vida.ValorVida,
    histc_90.Valor90d,
    histc_180.Valor180d,
    histc_365.Valor365d,

    histc_vida.TicketMedioVida,
    histc_365.TicketMedio365d,

    histi_vida.ItensVida,
    histi_90.Itens90d,
    histi_180.Itens180d,
    histi_365.Itens365d,
    histi_365.Cidades365d,
    histi_365.CodPontos365d,
    histi_365.CodFaces365d,
    histi_365.TempoExibicao365d,
    histi_365.DuracaoCampanha365d,
    histi_365.PctDigital365d,
    histi_365.MesesAtivos365d,

    geo_365.MediaQtdClassesSegmentoEntorno365d,
    geo_365.MediaQtdEmpresasSegmentadasEntorno365d,
    geo_365.MediaMaiorClasseEntorno365d,
    geo_365.MediaQtdEmpresasEntorno365d,
    geo_365.MediaQtdCnaesEntorno365d,
    geo_365.MediaQtdCepsEntorno365d,
    geo_365.MaxQtdEmpresasEntorno365d,
    geo_365.MaxQtdEmpresasSegmentadasEntorno365d,

    mk.RetornoMesSetor,
    mk.Media3mRetornoSetor,
    mk.Media6mRetornoSetor,
    mk.CdiAnoMedioMes,
    mk.VariacaoDolarMes,
    mk.VariacaoPetroleoMes,
    mk.VariacaoOuroMes,
    mk.VariacaoIFNCMes,
    mk.VariacaoIndiceIndustrialMes,
    mk.VariacaoIndiceConsumoMes,
    mk.VariacaoIndiceImobiliarioMes
INTO #base_features
FROM #snapshots AS s
INNER JOIN #clientes AS cl
    ON cl.CNPJ_LIMPO = s.CNPJ_LIMPO

OUTER APPLY (
    SELECT
        MAX(i.DataInicioPrevisto) AS UltimaDataCompra
    FROM #itens_enriquecidos AS i
    WHERE i.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND i.DataInicioPrevisto <= s.DataSnapshot
) AS ult

OUTER APPLY (
    SELECT
        COUNT(DISTINCT c.NumeroContrato) AS ContratosVida,
        SUM(c.ValorLiquidoContrato) AS ValorVida,
        AVG(c.ValorLiquidoContrato) AS TicketMedioVida
    FROM #contratos_cliente AS c
    WHERE c.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND c.DataLancamentoContrato <= s.DataSnapshot
) AS histc_vida

OUTER APPLY (
    SELECT
        COUNT(DISTINCT c.NumeroContrato) AS Contratos90d,
        SUM(c.ValorLiquidoContrato) AS Valor90d
    FROM #contratos_cliente AS c
    WHERE c.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND c.DataLancamentoContrato > DATEADD(day, -90, s.DataSnapshot)
      AND c.DataLancamentoContrato <= s.DataSnapshot
) AS histc_90

OUTER APPLY (
    SELECT
        COUNT(DISTINCT c.NumeroContrato) AS Contratos180d,
        SUM(c.ValorLiquidoContrato) AS Valor180d
    FROM #contratos_cliente AS c
    WHERE c.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND c.DataLancamentoContrato > DATEADD(day, -180, s.DataSnapshot)
      AND c.DataLancamentoContrato <= s.DataSnapshot
) AS histc_180

OUTER APPLY (
    SELECT
        COUNT(DISTINCT c.NumeroContrato) AS Contratos365d,
        SUM(c.ValorLiquidoContrato) AS Valor365d,
        AVG(c.ValorLiquidoContrato) AS TicketMedio365d
    FROM #contratos_cliente AS c
    WHERE c.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND c.DataLancamentoContrato > DATEADD(day, -365, s.DataSnapshot)
      AND c.DataLancamentoContrato <= s.DataSnapshot
) AS histc_365

OUTER APPLY (
    SELECT
        COUNT(*) AS ItensVida
    FROM #itens_enriquecidos AS i
    WHERE i.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND i.DataInicioPrevisto <= s.DataSnapshot
) AS histi_vida

OUTER APPLY (
    SELECT
        COUNT(*) AS Itens90d
    FROM #itens_enriquecidos AS i
    WHERE i.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND i.DataInicioPrevisto > DATEADD(day, -90, s.DataSnapshot)
      AND i.DataInicioPrevisto <= s.DataSnapshot
) AS histi_90

OUTER APPLY (
    SELECT
        COUNT(*) AS Itens180d
    FROM #itens_enriquecidos AS i
    WHERE i.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND i.DataInicioPrevisto > DATEADD(day, -180, s.DataSnapshot)
      AND i.DataInicioPrevisto <= s.DataSnapshot
) AS histi_180

OUTER APPLY (
    SELECT
        COUNT(*) AS Itens365d,
        COUNT(DISTINCT i.CidadeExibicao) AS Cidades365d,
        COUNT(DISTINCT i.CodPonto) AS CodPontos365d,
        COUNT(DISTINCT i.CodFace) AS CodFaces365d,
        SUM(COALESCE(i.TempoExibicaoDias, 0)) AS TempoExibicao365d,
        AVG(CAST(i.DuracaoContratoDias AS decimal(18,6))) AS DuracaoCampanha365d,
        AVG(CAST(i.FlagDigital AS decimal(18,6))) AS PctDigital365d,
        COUNT(DISTINCT DATEFROMPARTS(YEAR(i.DataInicioPrevisto), MONTH(i.DataInicioPrevisto), 1)) AS MesesAtivos365d
    FROM #itens_enriquecidos AS i
    WHERE i.CNPJ_LIMPO = s.CNPJ_LIMPO
      AND i.DataInicioPrevisto > DATEADD(day, -365, s.DataSnapshot)
      AND i.DataInicioPrevisto <= s.DataSnapshot
) AS histi_365

OUTER APPLY (
    SELECT
        AVG(CAST(seg.QtdClassesSegmentoEntornoPainel AS decimal(18,6))) AS MediaQtdClassesSegmentoEntorno365d,
        AVG(CAST(seg.QtdEmpresasSegmentadasEntornoPainel AS decimal(18,6))) AS MediaQtdEmpresasSegmentadasEntorno365d,
        AVG(CAST(seg.MaiorClasseEntornoPainel AS decimal(18,6))) AS MediaMaiorClasseEntorno365d,
        AVG(CAST(empent.QtdEmpresasEntornoPainel AS decimal(18,6))) AS MediaQtdEmpresasEntorno365d,
        AVG(CAST(empent.QtdCnaesEntornoPainel AS decimal(18,6))) AS MediaQtdCnaesEntorno365d,
        AVG(CAST(empent.QtdCepsEntornoPainel AS decimal(18,6))) AS MediaQtdCepsEntorno365d,
        MAX(empent.QtdEmpresasEntornoPainel) AS MaxQtdEmpresasEntorno365d,
        MAX(seg.QtdEmpresasSegmentadasEntornoPainel) AS MaxQtdEmpresasSegmentadasEntorno365d
    FROM (
        SELECT DISTINCT i.CodPonto
        FROM #itens_enriquecidos AS i
        WHERE i.CNPJ_LIMPO = s.CNPJ_LIMPO
          AND i.DataInicioPrevisto > DATEADD(day, -365, s.DataSnapshot)
          AND i.DataInicioPrevisto <= s.DataSnapshot
          AND i.CodPonto IS NOT NULL
    ) AS paineis_cliente
    LEFT JOIN #segmentos_painel AS seg
        ON seg.CodPonto = paineis_cliente.CodPonto
    LEFT JOIN #empresas_entorno_painel AS empent
        ON empent.CodPonto = paineis_cliente.CodPonto
) AS geo_365

LEFT JOIN #mercado_setor_mes AS mk
    ON mk.Setor = cl.SetorCNAE
   AND mk.MesRef = s.MesRef;

CREATE CLUSTERED INDEX IX_#base_features_CNPJ_MesRef ON #base_features(CNPJ_LIMPO, MesRef);
CREATE NONCLUSTERED INDEX IX_#base_features_DataSnapshot ON #base_features(DataSnapshot);

SELECT
    b.CNPJ_LIMPO,
    b.MesRef,
    b.DataSnapshot,

    b.IDEmpresa,
    b.IDEmpresaProprietaria,
    b.BitCliente,
    b.RazaoSocial,

    b.ClasseValor,
    b.DescricaoClasseValor,
    b.CNAE,
    b.Porte,
    b.DescricaoCNAE,
    b.ClasseCNAE,
    b.SetorCNAE,
    b.MacroSetorCNAE,
    b.DependeCredito,
    b.ConsumoDiscricionario,
    b.InsumoImportado,
    b.Exportador,
    b.PoderRepassePreco,
    b.CapitalSocial,
    b.ClasseEstrutural,
    b.QtdCidadesCliente,
    b.QtdUFCliente,
    b.QtdCidadesPainel,
    b.QtdCEPPainel,
    b.ItensMesmaCidade,
    b.TotalItens,
    b.PercItensMesmaCidade,
    b.ClasseGeo,
    b.ClassePotencial,
    b.ReceitaTotal,
    b.PercReceitaAcumulada,
    b.ScoreRetornoCluster,
    b.ClusterGrupoCliente,
    b.ScoreRetornoTecnico,

    b.UltimaDataCompra,
    b.DiasDesdeUltimaCompra,

    b.ContratosVida,
    b.Contratos90d,
    b.Contratos180d,
    b.Contratos365d,

    b.ValorVida,
    b.Valor90d,
    b.Valor180d,
    b.Valor365d,

    b.TicketMedioVida,
    b.TicketMedio365d,

    b.ItensVida,
    b.Itens90d,
    b.Itens180d,
    b.Itens365d,
    b.Cidades365d,
    b.CodPontos365d,
    b.CodFaces365d,
    b.TempoExibicao365d,
    b.DuracaoCampanha365d,
    b.PctDigital365d,
    b.MesesAtivos365d,

    b.MediaQtdClassesSegmentoEntorno365d,
    b.MediaQtdEmpresasSegmentadasEntorno365d,
    b.MediaMaiorClasseEntorno365d,
    b.MediaQtdEmpresasEntorno365d,
    b.MediaQtdCnaesEntorno365d,
    b.MediaQtdCepsEntorno365d,
    b.MaxQtdEmpresasEntorno365d,
    b.MaxQtdEmpresasSegmentadasEntorno365d,

    b.RetornoMesSetor,
    b.Media3mRetornoSetor,
    b.Media6mRetornoSetor,
    b.CdiAnoMedioMes,
    b.VariacaoDolarMes,
    b.VariacaoPetroleoMes,
    b.VariacaoOuroMes,
    b.VariacaoIFNCMes,
    b.VariacaoIndiceIndustrialMes,
    b.VariacaoIndiceConsumoMes,
    b.VariacaoIndiceImobiliarioMes,

    CASE
        WHEN EXISTS (
            SELECT 1
            FROM #itens_enriquecidos AS i
            WHERE i.CNPJ_LIMPO = b.CNPJ_LIMPO
              AND i.DataInicioPrevisto > b.DataSnapshot
              AND i.DataInicioPrevisto <= DATEADD(day, @HorizonteDias, b.DataSnapshot)
        )
        THEN 1 ELSE 0
    END AS ContratouProx90d
FROM #base_features AS b
ORDER BY
    b.CNPJ_LIMPO,
    b.MesRef;