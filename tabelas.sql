SELECT TOP (1000) [IDDimKanban]
      ,[NomeKanban]
      ,[Descricao]
      ,[Ativo]
      ,[CriadoEm]
      ,[IDUsuario]
      ,[InativadoEm]
      ,[InativadoPor]
      ,[IDEmpresaProprietaria]
      ,[BitPrincipal]
  FROM [Kanban].[Silver].[DimKanban]




SELECT TOP (1000) [IDDimKanbanCardMotivoEncerramento]
      ,[IDEmpresa]
      ,[IDDimKanbanMotivoEncerramento]
      ,[DataEncerramento]
  FROM [Kanban].[Silver].[DimKanbanCardMotivoEncerramento]




SELECT TOP (1000) [IDDimKanbanFase]
      ,[IDDimKanban]
      ,[NomeFase]
      ,[OrdemFase]
      ,[TipoFase]
      ,[Ativo]
      ,[CriadoEm]
      ,[IDUsuario]
      ,[IDEmpresaProprietaria]
      ,[CorHex]
      ,[CorTextoHex]
      ,[AtualizadoEm]
      ,[AtualizadoPor]
      ,[InativadoEm]
      ,[InativadoPor]
  FROM [Kanban].[Silver].[DimKanbanFase]




SELECT TOP (1000) [IDDimKanbanMotivoAcao]
      ,[NomeMotivo]
      ,[TipoEvento]
      ,[ObrigatorioObservacao]
      ,[Ativo]
      ,[CriadoEm]
      ,[IDDimUsuarios]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[DimKanbanMotivoAcao]



SELECT TOP (1000) [IDDimKanbanMotivoEncerramento]
      ,[NomeMotivo]
      ,[TipoMotivo]
      ,[Ativo]
      ,[IDEmpresaProprietaria]
      ,[IDDimUsuarios]
  FROM [Kanban].[Silver].[DimKanbanMotivoEncerramento]




SELECT TOP (1000) [IDDimKanbanOrigem]
      ,[NomeOrigem]
      ,[Ativo]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[DimKanbanOrigem]




SELECT TOP (1000) [IDDimKanbanTag]
      ,[IDDimKanban]
      ,[NomeTag]
      ,[TipoTag]
      ,[CorHex]
      ,[Icone]
      ,[AfetaCorCard]
      ,[PodeVendedorAplicar]
      ,[PodeAdminAplicar]
      ,[AplicacaoUnica]
      ,[Ativo]
      ,[CriadoEm]
      ,[IDUsuario]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[DimKanbanTag]




SELECT TOP (1000) [IDDimKanbanTipoAprovacao]
      ,[NomeTipoAprovacao]
      ,[Ativo]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[DimKanbanTipoAprovacao]



SELECT TOP (1000) [IDFatoKanbanAprovacao]
      ,[IDFatoKanbanCard]
      ,[IDDimKanbanTipoAprovacao]
      ,[StatusAprovacao]
      ,[SolicitadoEm]
      ,[SolicitadoPor]
      ,[DecididoEm]
      ,[DecididoPor]
      ,[ObservacaoSolicitacao]
      ,[ObservacaoDecisao]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[FatoKanbanAprovacao]




SELECT TOP (1000) [IDFatoKanbanCard]
      ,[IDDimKanban]
      ,[IDDimKanbanFaseAtual]
      ,[Titulo]
      ,[Descricao]
      ,[IDCliente]
      ,[IDVendedorUsuario]
      ,[IDDimKanbanOrigem]
      ,[StatusCard]
      ,[IDDimKanbanMotivoEncerramento]
      ,[MotivoEncerramentoObs]
      ,[CriadoEm]
      ,[AtualizadoEm]
      ,[EncerradoEm]
      ,[Ativo]
      ,[IDEmpresaProprietaria]
      ,[IDDimUsuarios]
      ,[VersaoConcorrencia]
  FROM [Kanban].[Silver].[FatoKanbanCard]








SELECT TOP (1000) [IDFatoKanbanCardItem]
      ,[IDFatoKanbanCard]
      ,[IDProduto]
      ,[CodPonto]
      ,[CodFace]
      ,[Quantidade]
      ,[Cota]
      ,[DtInicio]
      ,[DtFim]
      ,[PrecoTabela]
      ,[PrecoNegociado]
      ,[Observacao]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[FatoKanbanCardItem]






SELECT TOP (1000) [IDFatoKanbanCardLog]
      ,[IDFatoKanbanCard]
      ,[IDDimKanban]
      ,[IDEmpresaProprietaria]
      ,[IDEmpresaRelacionada]
      ,[IDUsuarioAcao]
      ,[TipoEvento]
      ,[SubtipoEvento]
      ,[OcorridoEm]
      ,[IDFaseDe]
      ,[IDFasePara]
      ,[IDDimKanbanMotivoAcao]
      ,[TabelaOrigem]
      ,[IDRegistroOrigem]
      ,[TextoLivre]
      ,[PayloadAntes]
      ,[PayloadDepois]
  FROM [Kanban].[Silver].[FatoKanbanCardLog]




SELECT TOP (1000) [IDFatoKanbanCardMovimento]
      ,[IDFatoKanbanCard]
      ,[IDFaseDe]
      ,[IDFasePara]
      ,[MovidoEm]
      ,[MovidoPor]
      ,[Observacao]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[FatoKanbanCardMovimento]








SELECT TOP (1000) [IDFatoKanbanCardNota]
      ,[IDFatoKanbanCard]
      ,[TipoNota]
      ,[Texto]
      ,[CriadoEm]
      ,[CriadoPor]
      ,[IDEmpresaProprietaria]
      ,[IDEmpresa]
  FROM [Kanban].[Silver].[FatoKanbanCardNota]





SELECT TOP (1000) [IDFatoKanbanCardPainelFace]
      ,[IDFatoKanbanCard]
      ,[Ordem]
      ,[IDDimPaineisEuromidia]
      ,[IDDimFacesPaineis]
      ,[CodPonto]
      ,[CodFace]
      ,[TipoPainel]
      ,[AnoCusto]
      ,[CustoTabela]
      ,[IDDimTabelaPrecosEuromidia]
      ,[PeriodoExibicao]
      ,[ExibicoesDia]
      ,[ValorTabela]
      ,[Tabela]
      ,[PoliticaTrocas]
      ,[ValorTroca]
      ,[NovoValor]
      ,[PercentualDesconto]
      ,[ValorVendaFinal]
      ,[MargemValor]
      ,[MargemPercentual]
      ,[Ativo]
      ,[CriadoEm]
      ,[DataAtualizacao]
      ,[RemovidoEm]
      ,[RemovidoPor]
      ,[IDUsuario]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[FatoKanbanCardPainelFace]




SELECT TOP (1000) [IDFatoKanbanCardTag]
      ,[IDFatoKanbanCard]
      ,[IDDimKanbanTag]
      ,[AplicadoEm]
      ,[AplicadoPor]
      ,[RemovidoEm]
      ,[RemovidoPor]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[FatoKanbanCardTag]





SELECT TOP (1000) [IDFatoKanbanCardVinculoContrato]
      ,[IDFatoKanbanCard]
      ,[IDContrato]
      ,[TipoVinculo]
      ,[VinculadoEm]
      ,[VinculadoPor]
      ,[IDEmpresaProprietaria]
  FROM [Kanban].[Silver].[FatoKanbanCardVinculoContrato]
