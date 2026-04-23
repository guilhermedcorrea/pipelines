/* =========================================================
   1) DimPaineisEuromidia -> busca por CodPonto
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_DimPaineisEuromidia_CodPonto_Data'
      AND object_id = OBJECT_ID(N'[Integracao].[Silver].[DimPaineisEuromidia]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_DimPaineisEuromidia_CodPonto_Data
    ON [Integracao].[Silver].[DimPaineisEuromidia]
    (
        CodPonto ASC,
        DataAtualizacao DESC,
        IDDimPaineisEuromidia DESC
    )
    INCLUDE
    (
        Tipo,
        Logradouro,
        Cidade,
        UF,
        Bairro,
        Numero,
        CEP,
        QuantidadeFaces,
        BitAtivo
    );
END
GO

/* =========================================================
   2) DimFacesPaineis -> busca por CodPonto + CodFace
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_DimFacesPaineis_CodPonto_CodFace'
      AND object_id = OBJECT_ID(N'[Integracao].[Silver].[DimFacesPaineis]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_DimFacesPaineis_CodPonto_CodFace
    ON [Integracao].[Silver].[DimFacesPaineis]
    (
        CodPonto ASC,
        CodFace ASC,
        IDDimFacesPaineis DESC
    )
    INCLUDE
    (
        Face,
        Tipo,
        IDDimPaineisEuromidia
    );
END
GO

/* =========================================================
   3) FatoControleContratosEuromidia -> contratos por IDEmpresa
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FCCEuromidia_Empresa_Ativo_Ord'
      AND object_id = OBJECT_ID(N'[Integracao].[Silver].[FatoControleContratosEuromidia]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FCCEuromidia_Empresa_Ativo_Ord
    ON [Integracao].[Silver].[FatoControleContratosEuromidia]
    (
        IDEmpresa ASC,
        BitAtivo ASC,
        DataAssinaturaRenovacao DESC,
        DataAtualizacao DESC,
        IDFatoControleContratosEuromidia DESC
    )
    INCLUDE
    (
        NumeroContrato,
        NumeroPrevia,
        Referencia,
        RazaoSocial,
        CNPJ,
        MarcaExibida,
        DataLancamento,
        QuantidadePontos,
        QuantidadeFaces,
        TotalFaturamentoLiquidoMensal,
        IDDimStatusContratos,
        IDEmpresaAgencia,
        IDEmpresaBureau
    );
END
GO

/* =========================================================
   4) FatoControleContratosEuromidia -> fallback por CNPJ
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FCCEuromidia_CNPJ_Ativo_Ord'
      AND object_id = OBJECT_ID(N'[Integracao].[Silver].[FatoControleContratosEuromidia]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FCCEuromidia_CNPJ_Ativo_Ord
    ON [Integracao].[Silver].[FatoControleContratosEuromidia]
    (
        CNPJ ASC,
        BitAtivo ASC,
        DataAssinaturaRenovacao DESC,
        DataAtualizacao DESC,
        IDFatoControleContratosEuromidia DESC
    )
    INCLUDE
    (
        IDEmpresa,
        NumeroContrato,
        NumeroPrevia,
        Referencia,
        RazaoSocial,
        MarcaExibida,
        DataLancamento,
        QuantidadePontos,
        QuantidadeFaces,
        TotalFaturamentoLiquidoMensal,
        IDDimStatusContratos,
        IDEmpresaAgencia,
        IDEmpresaBureau
    );
END
GO

/* =========================================================
   5) FatoControleContratosItensEuromidia
      -> lookup/update por contrato + ponto + face + ativo
      -> também ajuda lista de pontos e lista de faces
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FCCItens_Contrato_Ativo_Ponto_Face'
      AND object_id = OBJECT_ID(N'[Integracao].[Silver].[FatoControleContratosItensEuromidia]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FCCItens_Contrato_Ativo_Ponto_Face
    ON [Integracao].[Silver].[FatoControleContratosItensEuromidia]
    (
        IDFatoControleContratoEuromidia ASC,
        BitAtivo ASC,
        CodPonto ASC,
        CodFace ASC,
        IDFatoControleContratosItensEuromidia DESC
    )
    INCLUDE
    (
        IDPainelEuromidia,
        IDDimFacesPaineis,
        IDVendedor,
        CidadeExibicao,
        Tipo,
        Cota,
        FaturamentoLiquidoMensal,
        TotalLiquidoContratoAGBRCTACORDO,
        DataInicioPrevisto,
        DataTerminoPrevisto,
        IDFatoKanbanCard
    );
END
GO

/* =========================================================
   6) FatoSolicitacaoContratoEuromidia
      -> busca da solicitação ativa por card
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FSolicContrato_Card_Ativo_Data'
      AND object_id = OBJECT_ID(N'[Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FSolicContrato_Card_Ativo_Data
    ON [Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]
    (
        IDFatoKanbanCard ASC,
        BitAtivo ASC,
        DataAtualizacao DESC,
        DataCriacao DESC,
        IDFatoSolicitacaoContratoEuromidia DESC
    )
    INCLUDE
    (
        IDDimStatusContratos,
        TipoSolicitacao,
        IDFatoControleContratosEuromidia,
        IDEmpresa,
        IDEmpresaAgencia,
        IDEmpresaBureau,
        StatusSolicitacao
    );
END
GO

/* =========================================================
   7) FatoSolicitacaoContratoItemEuromidia
      -> item editável por solicitação/contrato/ponto/face
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FSolicContratoItem_Solic_Contrato_Ponto_Face'
      AND object_id = OBJECT_ID(N'[Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FSolicContratoItem_Solic_Contrato_Ponto_Face
    ON [Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]
    (
        IDFatoSolicitacaoContratoEuromidia ASC,
        BitSolicitacaoAtiva ASC,
        IDFatoControleContratosEuromidia ASC,
        CodPonto ASC,
        CodFace ASC,
        IDFatoSolicitacaoContratoItemEuromidia DESC
    )
    INCLUDE
    (
        IDFatoControleContratosItensEuromidia,
        IDFatoKanbanCard,
        IDPainelEuromidia,
        IDDimFacesPaineis,
        IDVendedor,
        IDDimCheckingHistorico
    );
END
GO

/* =========================================================
   8) FatoKanbanCard
      -> paginação/listagem por kanban + fase + ativo
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FatoKanbanCard_Kanban_Fase_Ativo'
      AND object_id = OBJECT_ID(N'[Kanban].[Silver].[FatoKanbanCard]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FatoKanbanCard_Kanban_Fase_Ativo
    ON [Kanban].[Silver].[FatoKanbanCard]
    (
        IDDimKanban ASC,
        IDDimKanbanFaseAtual ASC,
        Ativo ASC,
        IDFatoKanbanCard DESC
    )
    INCLUDE
    (
        Titulo,
        StatusCard,
        CriadoEm,
        AtualizadoEm,
        IDEmpresaProprietaria,
        IDEmpresa,
        IDVendedor,
        IDVendedorUsuario,
        IDDimUsuarios,
        IDDimKanbanStatusCard,
        IDEmpresaAgencia,
        IDEmpresaBureau
    );
END
GO

/* =========================================================
   9) FatoKanbanCardTag
      -> tags ativas por card
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FatoKanbanCardTag_Card_Removido_Tag'
      AND object_id = OBJECT_ID(N'[Kanban].[Silver].[FatoKanbanCardTag]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FatoKanbanCardTag_Card_Removido_Tag
    ON [Kanban].[Silver].[FatoKanbanCardTag]
    (
        IDFatoKanbanCard ASC,
        RemovidoEm ASC,
        IDDimKanbanTag ASC
    )
    INCLUDE
    (
        AplicadoEm,
        IDEmpresaProprietaria
    );
END
GO



/* =========================================================
   10) FatoKanbanNegociacaoPreco
       -> últimas negociações por card/empresa
   ========================================================= */
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_FKanbanNegPreco_Card_Empresa_Data'
      AND object_id = OBJECT_ID(N'[Kanban].[Silver].[FatoKanbanNegociacaoPreco]')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_FKanbanNegPreco_Card_Empresa_Data
    ON [Kanban].[Silver].[FatoKanbanNegociacaoPreco]
    (
        IDFatoKanbanCard ASC,
        IDEmpresaProprietaria ASC,
        DataPrecoProposto DESC,
        IDFatoKanbanNegociacaoPreco DESC
    )
    INCLUDE
    (
        IDDimKanbanFase,
        IDDimKanbanStatusCard,
        IDDimPaineisEuromidia,
        IDDimFacesPaineis,
        IDDimTabelaPrecosEuromidia,
        PrecoProposto,
        DescontoProposto,
        PrecoAprovado,
        DataAprovacaoPreco,
        IDDimUsuarios,
        IDDimUsuariosAprovacaoPreco
    );
END
GO

/* =========================================================
   Atualiza estatísticas depois de criar
   ========================================================= */
EXEC sp_updatestats;
GO