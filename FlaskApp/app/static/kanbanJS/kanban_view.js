
(() => {
  function lerConfiguracaoKanbanView(){
    const elConfig = document.getElementById("kanban-view-config");
    if (!elConfig) {
      throw new Error("Configuração do kanban não encontrada no template.");
    }

    try {
      return JSON.parse(elConfig.textContent || "{}");
    } catch (erro) {
      console.error("Falha ao ler a configuração do kanban.", erro);
      throw erro;
    }
  }

  const KANBAN_VIEW_CONFIG = lerConfiguracaoKanbanView();
  const PODE_VER_CUSTO_MARGEM = KANBAN_VIEW_CONFIG.podeVerCustoMargem === true;
  const USUARIO_EH_VENDEDOR = KANBAN_VIEW_CONFIG.usuarioEhVendedor === true;
  const USUARIO_EH_COORDENADOR = KANBAN_VIEW_CONFIG.usuarioEhCoordenador === true;
  const USUARIO_EH_ADMIN_KANBAN = KANBAN_VIEW_CONFIG.usuarioEhAdminKanban === true || USUARIO_EH_COORDENADOR === true;
  const USUARIO_TEM_BLOQUEIO_CARTEIRA = KANBAN_VIEW_CONFIG.usuarioTemBloqueioCarteira === true || (USUARIO_EH_VENDEDOR && !USUARIO_EH_ADMIN_KANBAN);
  const USUARIO_PODE_GERENCIAR_FASES_E_TAGS = !USUARIO_EH_VENDEDOR;
  const ID_USUARIO_LOGADO = Number(KANBAN_VIEW_CONFIG.idUsuarioLogado || 0);
  const ID_VENDEDOR_LOGADO = Number(KANBAN_VIEW_CONFIG.idVendedorLogado || 0);
  const ID_KANBAN = Number(KANBAN_VIEW_CONFIG.idKanban || 0);
  const board = document.getElementById("board");
  const msgBoard = document.getElementById("msgBoard");
  const SCRIPT_ROOT = String(KANBAN_VIEW_CONFIG.scriptRoot || "").trim();
  const URL_IMAGEM_PAINEL_PUBLICITARIO = String(KANBAN_VIEW_CONFIG.urlPainelPublicidade || "").trim();
  const URL_CABECALHO_ORCAMENTO_PADRAO = String(KANBAN_VIEW_CONFIG.urlCabecalhoOrcamentoPadrao || "").trim();
  const URL_API_CARD_DETALHE_TEMPLATE = String(KANBAN_VIEW_CONFIG.urlApiCardDetalheTemplate || "").trim();
  const URL_API_CARD_ORCAMENTO_TEMPLATE = String(KANBAN_VIEW_CONFIG.urlApiCardOrcamentoTemplate || "").trim();
  const SOCKET_IO_NAMESPACE = "/kanban";
  const SOCKET_IO_PATH = `${SCRIPT_ROOT}/socket.io`;

  function removerControlesGestaoFasesParaVendedor(){
    if (!USUARIO_EH_VENDEDOR) return;

    document.querySelectorAll(
      "#btnNovaFase, .kb-col-edit, .kb-col-del, #modalFase, #modalInativarFase"
    ).forEach((elemento) => {
      elemento.remove();
    });
  }

  removerControlesGestaoFasesParaVendedor();

  function injetarEstiloBloqueioCarteiraVendedor(){
    if (document.getElementById("kb-estilo-bloqueio-carteira-vendedor")) return;

    const style = document.createElement("style");
    style.id = "kb-estilo-bloqueio-carteira-vendedor";
    style.textContent = `
      .kb-combobox-opcao.is-disabled{
        opacity:.70;
        cursor:not-allowed;
        background:rgba(239,68,68,.06);
        border-color:rgba(239,68,68,.18);
      }
      .kb-combobox-opcao.is-disabled:hover{
        background:rgba(239,68,68,.10);
      }
      .kb-combobox-aviso{
        display:block;
        margin-top:4px;
        font-size:11px;
        font-weight:900;
        color:#991b1b;
        line-height:1.25;
      }
    `;
    document.head.appendChild(style);
  }

  injetarEstiloBloqueioCarteiraVendedor();

  function configurarScrollKanban(){
    if (!board || board.dataset.scrollKanbanAjustado === "1") return;

    let scrollTimer = null;
    let ultimoScrollLeft = board.scrollLeft || 0;

    board.addEventListener("scroll", () => {
      if (board.classList.contains("is-dragging")) return;

      const scrollLeftAtual = board.scrollLeft || 0;
      const houveRolagemHorizontal = Math.abs(scrollLeftAtual - ultimoScrollLeft) > 0.5;
      ultimoScrollLeft = scrollLeftAtual;

      if (!houveRolagemHorizontal) return;

      board.classList.add("is-scrolling-x");
      window.clearTimeout(scrollTimer);
      scrollTimer = window.setTimeout(() => {
        board.classList.remove("is-scrolling-x");
      }, 160);
    }, { passive: true });

    board.dataset.scrollKanbanAjustado = "1";
  }

  const limpadoresScrollbarFase = [];

  function limparRecursosScrollbarFase(){
    while (limpadoresScrollbarFase.length) {
      const limpar = limpadoresScrollbarFase.pop();
      try {
        if (typeof limpar === "function") limpar();
      } catch (erro) {
        console.warn("Falha ao limpar recurso de scrollbar da fase.", erro);
      }
    }
  }

  function configurarScrollbarFase(dropEl, bodyEl){
    if (!dropEl || !bodyEl || bodyEl.dataset.scrollbarFaseConfigurada === "1") return;

    const barra = document.createElement("div");
    barra.className = "kb-drop-scrollbar";
    barra.setAttribute("aria-hidden", "true");

    const trilho = document.createElement("div");
    trilho.className = "kb-drop-scrollbar-track";

    const polegar = document.createElement("div");
    polegar.className = "kb-drop-scrollbar-thumb";

    barra.appendChild(trilho);
    barra.appendChild(polegar);
    bodyEl.appendChild(barra);

    let rafAtualizacao = null;
    let arrastando = false;
    let dragStartY = 0;
    let dragStartScrollTop = 0;

    function calcularGeometriaScrollbar(){
      const alturaVisivel = dropEl.clientHeight || 0;
      const alturaConteudo = dropEl.scrollHeight || 0;
      const alturaTrilho = barra.clientHeight || 0;
      const maxScroll = Math.max(0, alturaConteudo - alturaVisivel);
      const temOverflow = maxScroll > 2 && alturaTrilho > 0;

      if (!temOverflow) {
        return {
          temOverflow: false,
          alturaTrilho,
          alturaPolegar: 0,
          maxTopPolegar: 0,
          maxScroll: 0
        };
      }

      const proporcaoVisivel = alturaVisivel / Math.max(alturaConteudo, 1);
      const alturaPolegar = Math.max(36, Math.min(alturaTrilho, Math.round(alturaTrilho * proporcaoVisivel)));
      const maxTopPolegar = Math.max(0, alturaTrilho - alturaPolegar);

      return {
        temOverflow: true,
        alturaTrilho,
        alturaPolegar,
        maxTopPolegar,
        maxScroll
      };
    }

    function atualizarScrollbarFaseAgora(){
      rafAtualizacao = null;

      const geo = calcularGeometriaScrollbar();
      bodyEl.classList.toggle("has-scrollbar", geo.temOverflow);

      if (!geo.temOverflow) {
        polegar.style.height = "0px";
        polegar.style.transform = "translateY(0px)";
        return;
      }

      const topPolegar = geo.maxTopPolegar > 0
        ? Math.round((dropEl.scrollTop / geo.maxScroll) * geo.maxTopPolegar)
        : 0;

      polegar.style.height = `${geo.alturaPolegar}px`;
      polegar.style.transform = `translateY(${topPolegar}px)`;
    }

    function agendarAtualizacaoScrollbarFase(){
      if (rafAtualizacao !== null) return;
      rafAtualizacao = window.requestAnimationFrame(atualizarScrollbarFaseAgora);
    }

    function rolarPeloTrilho(clientY){
      const geo = calcularGeometriaScrollbar();
      if (!geo.temOverflow) return;

      const rect = barra.getBoundingClientRect();
      const yRelativo = Math.min(Math.max(clientY - rect.top - (geo.alturaPolegar / 2), 0), geo.maxTopPolegar);
      const proporcao = geo.maxTopPolegar > 0 ? yRelativo / geo.maxTopPolegar : 0;
      dropEl.scrollTop = Math.round(proporcao * geo.maxScroll);
      agendarAtualizacaoScrollbarFase();
    }

    dropEl.addEventListener("scroll", agendarAtualizacaoScrollbarFase, { passive: true });

    barra.addEventListener("pointerdown", (evento) => {
      evento.preventDefault();
      evento.stopPropagation();

      if (evento.target !== polegar) {
        rolarPeloTrilho(evento.clientY);
        return;
      }

      arrastando = true;
      dragStartY = evento.clientY;
      dragStartScrollTop = dropEl.scrollTop;
      bodyEl.classList.add("kb-scrollbar-dragging");
      polegar.setPointerCapture?.(evento.pointerId);
    });

    polegar.addEventListener("pointermove", (evento) => {
      if (!arrastando) return;
      evento.preventDefault();
      evento.stopPropagation();

      const geo = calcularGeometriaScrollbar();
      if (!geo.temOverflow || geo.maxTopPolegar <= 0) return;

      const deltaY = evento.clientY - dragStartY;
      const deltaScroll = (deltaY / geo.maxTopPolegar) * geo.maxScroll;
      dropEl.scrollTop = Math.round(dragStartScrollTop + deltaScroll);
      agendarAtualizacaoScrollbarFase();
    });

    function encerrarDragScrollbar(evento){
      if (!arrastando) return;
      if (evento) {
        evento.preventDefault();
        evento.stopPropagation();
      }
      arrastando = false;
      bodyEl.classList.remove("kb-scrollbar-dragging");
    }

    polegar.addEventListener("pointerup", encerrarDragScrollbar);
    polegar.addEventListener("pointercancel", encerrarDragScrollbar);
    window.addEventListener("resize", agendarAtualizacaoScrollbarFase, { passive: true });
    limpadoresScrollbarFase.push(() => window.removeEventListener("resize", agendarAtualizacaoScrollbarFase));

    if ("ResizeObserver" in window) {
      const resizeObserver = new ResizeObserver(agendarAtualizacaoScrollbarFase);
      resizeObserver.observe(dropEl);
      resizeObserver.observe(bodyEl);
      limpadoresScrollbarFase.push(() => resizeObserver.disconnect());
    }

    /*
     * Eu não uso MutationObserver aqui. Com muitos cards, observar childList/subtree/attributes
     * força o navegador a recalcular a barra customizada em massa durante renderizações,
     * drag/drop e atualizações ao vivo. As rotinas de renderização já chamam
     * _kbAtualizarScrollbarFase explicitamente quando o conteúdo da fase muda.
     */
    dropEl._kbAtualizarScrollbarFase = agendarAtualizacaoScrollbarFase;
    bodyEl.dataset.scrollbarFaseConfigurada = "1";
    agendarAtualizacaoScrollbarFase();
  }


  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
  const headersJSON = {
    "Content-Type": "application/json",
    "X-CSRFToken": csrf
  };

  let fases = [];
  let cards = [];
  let indiceCardsPorId = new Map();
  let indiceCardsPorFase = new Map();
  let indiceCardsVisiveisPorFase = new Map();
  let totalCardsVisiveisCache = 0;
  let tagsCatalogo = [];
  let vendedoresCatalogo = [];
  let filtrosCardsKanban = [];
  let tiposClienteDescontoCatalogo = [];
  let tiposClienteDescontoPorId = new Map();
  let origensAtendimentoCatalogo = [];
  let origensAtendimentoPorId = new Map();
  let tiposDocumentoCatalogo = [];
  let tiposDocumentoPorId = new Map();
  let vendedoresSelecionados = new Set();
  let tagsSelecionadasFiltro = new Set();
  let mapaTagsPorCard = new Map();
  let mapaNotasPorCard = new Map();
  let resumoComercial = null;
  let sequenciaResumoComercial = 0;
  let cardAbertoId = null;
  let cardOrcamentoAbertoId = null;
  let termoBusca = "";
  let versaoConcorrenciaCardAberto = "";
  let cardAbertoConflitoExterno = false;
  let salvandoCardAtual = false;
  let carregandoCardAtual = false;
  let sequenciaAberturaCard = 0;
  let estadoInicialCardAberto = null;
  let fluxoContratoPersistidoCardAberto = null;
  let faseEditandoId = null;

  let kanbanCfg = {};
  let paineisCatalogo = [];
  let painelFacesCatalogo = [];
  let catalogoPainelFacesCarregado = false;
  let promessaCatalogoPainelFaces = null;
  let paineisPorId = new Map();
  let painelFacesPorChave = new Map();

  let empresasCatalogo = [];
  let empresasPorId = new Map();
  let empresasResultadoComboboxAtual = [];
  let empresaBuscaRemotaController = null;
  let empresaPrincipalBloqueadaCarteiraAtual = null;
  let agenciasResultadoComboboxAtual = [];
  let agenciaBuscaRemotaController = null;
  let cnaesCatalogo = [];
  let cnaesResultadoComboboxAtual = [];
  let cnaeBuscaRemotaController = null;
  let empresasProprietariasCatalogo = [];
  const ID_TAG_TIPO_CONTRATO_ADITIVO = 8;
  const ID_TAG_TIPO_CONTRATO_NOVO = 9;
  const ID_TAG_RENOVACAO = 17;
  const VALOR_OPCAO_NOVO_CONTRATO = "__NOVO_CONTRATO__";
  const VALOR_OPCAO_NOVO_PAINEL = "__NOVO_PAINEL__";
  const VALOR_MODO_CONTRATO_ADITIVO = "ADITIVO";
  const VALOR_MODO_CONTRATO_NOVO = "NOVO_CONTRATO";
  const contratosPorEmpresaCache = new Map();
  const pontosPorContratoCache = new Map();
  const facesPorContratoPontoCache = new Map();
  let contratosCardCatalogo = [];
  let contratosResultadoComboboxAtual = [];
  let empresaCadastroConsultaTimer = null;
  let empresaCadastroConsultaController = null;
  let empresaCadastroUltimoCnpjConsultado = "";

  // Performance: lote inicial menor para pintar a tela primeiro.
  // O restante é carregado sob demanda/ociosidade, mantendo o Kanban leve mesmo com muitos cards.
  const TAM_LOTE_POR_FASE = 8;
  const LIMITE_CARDS_DOM_MODO_DESEMPENHO = 40;
  const LIMITE_CARDS_CARREGADOS_MODO_DESEMPENHO = 100;
  const LIMITE_CARDS_PARA_VIRTUALIZAR_FASE = 28;
  const LIMITE_CARDS_DOM_POR_FASE_VIRTUAL = 22;
  const OVERSCAN_CARDS_VIRTUAIS = 8;
  const ALTURA_ESTIMADA_CARD_VIRTUAL = 240;
  const ATRASO_REDRAW_TEMPO_REAL_MS = 120;
  const estadoFase = new Map();
  let socketKanban = null;
  let socketConectado = false;
  let socketConectando = false;
  let socketDesativadoPorErroTransporte = false;
  let sincronizacaoSemSocketEmAndamento = false;
  let verificacaoVersaoKanbanEmAndamento = false;
  let versaoKanbanAtual = 0;
  let ultimaAtualizacaoAoVivoMs = 0;
  let kanbanInicializado = false;

  const modalCard = document.getElementById("modalCard");
  const btnFecharCard = document.getElementById("btnFecharCard");
  const msgCard = document.getElementById("msgCard");
  const btnSalvarCard = document.getElementById("btnSalvarCard");
  const inputIdCard = document.getElementById("inputIdCard") || document.getElementById("cardId") || null;
  const inputTituloCard = document.getElementById("cardTitulo");
  const inputDescricaoCard = document.getElementById("cardDescricao");
  const selectTipoClienteDescontoCard = document.getElementById("selectTipoClienteDescontoCard");
  const selectOrigemAtendimentoCard = document.getElementById("selectOrigemAtendimentoCard");
  const comboSegmentoCard = document.getElementById("comboSegmentoCard");
  const inputSegmentoCardBusca = document.getElementById("inputSegmentoCardBusca");
  const btnToggleSegmentoCard = document.getElementById("btnToggleSegmentoCard");
  const listaSegmentoCardBusca = document.getElementById("listaSegmentoCardBusca");
  const selectSegmentoCard = document.getElementById("selectSegmentoCard");
  const cardIdVisual = document.getElementById("cardIdVisual");
  const cardTipoClienteBadgeVisual = document.getElementById("cardTipoClienteBadgeVisual");
  const cardOrigemAtendimentoBadgeVisual = document.getElementById("cardOrigemAtendimentoBadgeVisual");
  const selectResponsavelCard = document.getElementById("selectResponsavelCard");
  const selectOrigemCard = document.getElementById("selectOrigemCard");
  const selectMotivoEncerramentoCard = document.getElementById("selectMotivoEncerramentoCard");
  const inputMotivoEncerramentoObsCard = document.getElementById("inputMotivoEncerramentoObsCard");
  const selectStatusCard = document.getElementById("selectStatusCard");
  const selectTagCard = document.getElementById("selectTagCard");
  const inputNotaTexto = document.getElementById("notaTexto");
  const listaNotas = document.getElementById("listaNotas");

  const modalFase = document.getElementById("modalFase");
  const modalFaseTitulo = document.getElementById("modalFaseTitulo");
  const faseNomeInput = document.getElementById("faseNome");
  const faseTipoSelect = document.getElementById("faseTipo");
  const faseUsarCor = document.getElementById("faseUsarCor");
  const faseCorHex = document.getElementById("faseCorHex");
  const btnLimparCorFase = document.getElementById("btnLimparCorFase");
  const msgFase = document.getElementById("msgFase");

  const buscaKanban = document.getElementById("buscaKanban");
  const comboBuscaKanban = document.getElementById("comboBuscaKanban");
  const listaBuscaKanban = document.getElementById("listaBuscaKanban");
  const btnLimparBusca = document.getElementById("btnLimparBusca");
  const contadorPesquisa = document.getElementById("contadorPesquisa");

  const filtroVendedorWrap = document.getElementById("filtroVendedor");
  const btnFiltroVendedor = document.getElementById("btnFiltroVendedor");
  const menuFiltroVendedor = document.getElementById("menuFiltroVendedor");
  const inputFiltroVendedor = document.getElementById("inputFiltroVendedor");
  const listaFiltroVendedor = document.getElementById("listaFiltroVendedor");
  const resumoFiltroVendedor = document.getElementById("resumoFiltroVendedor");

  const filtroTagWrap = document.getElementById("filtroTag");
  const btnFiltroTag = document.getElementById("btnFiltroTag");
  const menuFiltroTag = document.getElementById("menuFiltroTag");
  const inputFiltroTag = document.getElementById("inputFiltroTag");
  const listaFiltroTag = document.getElementById("listaFiltroTag");
  const resumoFiltroTag = document.getElementById("resumoFiltroTag");

  const kpiAtendimentosAtivos = document.getElementById("kpiAtendimentosAtivos");
  const kpiAprovacaoPreco = document.getElementById("kpiAprovacaoPreco");
  const kpiCustoTotal = document.getElementById("kpiCustoTotal");
  const kpiVendaTotal = document.getElementById("kpiVendaTotal");
  const kpiMargemPercentual = document.getElementById("kpiMargemPercentual");

  const comboEmpresaCard = document.getElementById("comboEmpresaCard");
  const inputEmpresaCardBusca = document.getElementById("inputEmpresaCardBusca");
  const btnToggleEmpresaCard = document.getElementById("btnToggleEmpresaCard");
  const listaEmpresaCardBusca = document.getElementById("listaEmpresaCardBusca");
  const selectEmpresaCard = document.getElementById("selectEmpresaCard");
  const btnLimparEmpresa = document.getElementById("btnLimparEmpresa");
  const labelEmpresaPrincipalCard = document.getElementById("labelEmpresaPrincipalCard");
  const wrapEmpresasRelacionadasCard = document.getElementById("wrapEmpresasRelacionadasCard");
  const wrapAgenciaRelacionadaCard = document.getElementById("wrapAgenciaRelacionadaCard");
  const wrapClienteDiretoCard = document.getElementById("wrapClienteDiretoCard");
  const wrapBureauCard = document.getElementById("wrapBureauCard");
  const wrapIntermediarioCard = document.getElementById("wrapIntermediarioCard");
  const wrapDadosNovoContrato = document.getElementById("wrapDadosNovoContrato");
  const contratoFlowBox = document.getElementById("contratoFlowBox");
  const comboAgenciaCard = document.getElementById("comboAgenciaCard");
  const inputAgenciaCardBusca = document.getElementById("inputAgenciaCardBusca");
  const btnToggleAgenciaCard = document.getElementById("btnToggleAgenciaCard");
  const listaAgenciaCardBusca = document.getElementById("listaAgenciaCardBusca");
  const selectAgenciaCard = document.getElementById("selectAgenciaCard");
  const btnLimparAgencia = document.getElementById("btnLimparAgencia");
  const inputCnpjAgenciaCard = document.getElementById("inputCnpjAgenciaCard");
  const comboClienteDiretoCard = document.getElementById("comboClienteDiretoCard");
  const inputClienteDiretoCardBusca = document.getElementById("inputClienteDiretoCardBusca");
  const btnToggleClienteDiretoCard = document.getElementById("btnToggleClienteDiretoCard");
  const listaClienteDiretoCardBusca = document.getElementById("listaClienteDiretoCardBusca");
  const selectClienteDiretoCard = document.getElementById("selectClienteDiretoCard");
  const btnLimparClienteDireto = document.getElementById("btnLimparClienteDireto");
  const comboBureauCard = document.getElementById("comboBureauCard");
  const inputBureauCardBusca = document.getElementById("inputBureauCardBusca");
  const btnToggleBureauCard = document.getElementById("btnToggleBureauCard");
  const listaBureauCardBusca = document.getElementById("listaBureauCardBusca");
  const selectBureauCard = document.getElementById("selectBureauCard");
  const btnLimparBureau = document.getElementById("btnLimparBureau");
  const inputCnpjBureauCard = document.getElementById("inputCnpjBureauCard");
  const comboIntermediarioCard = document.getElementById("comboIntermediarioCard");
  const inputIntermediarioCardBusca = document.getElementById("inputIntermediarioCardBusca");
  const btnToggleIntermediarioCard = document.getElementById("btnToggleIntermediarioCard");
  const listaIntermediarioCardBusca = document.getElementById("listaIntermediarioCardBusca");
  const selectIntermediarioCard = document.getElementById("selectIntermediarioCard");
  const btnLimparIntermediario = document.getElementById("btnLimparIntermediario");
  const inputCnpjIntermediarioCard = document.getElementById("inputCnpjIntermediarioCard");
  const inputMarcaCard = document.getElementById("inputMarcaCard");
  const inputTelefoneCard = document.getElementById("inputTelefoneCard");
  const inputEmailCard = document.getElementById("inputEmailCard");
  const btnAbrirCadastroEmpresa = document.getElementById("btnAbrirCadastroEmpresa");
  const comboContratoCard = document.getElementById("comboContratoCard");
  const inputContratoCardBusca = document.getElementById("inputContratoCardBusca");
  const btnToggleContratoCard = document.getElementById("btnToggleContratoCard");
  const listaContratoCardBusca = document.getElementById("listaContratoCardBusca");
  const selectContratoCard = document.getElementById("selectContratoCard");
  const selectModoContratoCard = document.getElementById("selectModoContratoCard");
  const selectCodPontoContratoCard = document.getElementById("selectCodPontoContratoCard");
  const selectCodFaceContratoCard = document.getElementById("selectCodFaceContratoCard");
  const wrapSelectContratoCard = document.getElementById("wrapSelectContratoCard");
  const wrapSelectModoContratoCard = document.getElementById("wrapSelectModoContratoCard");
  const wrapSelectCodPontoContratoCard = document.getElementById("wrapSelectCodPontoContratoCard");
  const wrapSelectCodFaceContratoCard = document.getElementById("wrapSelectCodFaceContratoCard");
  const msgFluxoContrato = document.getElementById("msgFluxoContrato");
  const wrapFormularioSolicitacaoContrato = document.getElementById("wrapFormularioSolicitacaoContrato");
  const formSolicitacaoContratoHeader = document.getElementById("formSolicitacaoContratoHeader");
  const formSolicitacaoContratoItem = document.getElementById("formSolicitacaoContratoItem");
  const wrapContatoClienteDiretoFormulario = document.getElementById("wrapContatoClienteDiretoFormulario");
  const wrapContatoClienteDiretoAssinatura = document.getElementById("wrapContatoClienteDiretoAssinatura");
  const tituloContatoClienteDiretoAssinatura = document.getElementById("tituloContatoClienteDiretoAssinatura");
  const formContatoClienteDiretoAssinatura = document.getElementById("formContatoClienteDiretoAssinatura");
  const wrapContatoClienteDiretoFinanceiro = document.getElementById("wrapContatoClienteDiretoFinanceiro");
  const tituloContatoClienteDiretoFinanceiro = document.getElementById("tituloContatoClienteDiretoFinanceiro");
  const formContatoClienteDiretoFinanceiro = document.getElementById("formContatoClienteDiretoFinanceiro");
  const ID_FASE_FORMULARIO_CONTRATO = 4;
  const ID_TIPO_CLIENTE_DIRETO = 2;
  const ID_TIPO_DOCUMENTO_ADITIVO = 3;
  const modalCadastroEmpresa = document.getElementById("modalCadastroEmpresa");
  const btnFecharCadastroEmpresa = document.getElementById("btnFecharCadastroEmpresa");
  const btnSalvarCadastroEmpresa = document.getElementById("btnSalvarCadastroEmpresa");
  const msgCadastroEmpresa = document.getElementById("msgCadastroEmpresa");

  const CAMPOS_SOLICITACAO_HEADER = [
    { nome: "NumeroPrevia", label: "Número Prévia" },
    { nome: "CNPJ", label: "CNPJ", somenteLeitura: true, obrigatorio: true },
    { nome: "DataAssinaturaRenovacao", label: "Data Assinatura / Renovação", tipo: "date" },
    { nome: "IDTrimestre", label: "Trimestre" },
    { nome: "DataLancamento", label: "Data Lançamento", tipo: "date" },
    { nome: "RazaoSocial", label: "Razão Social", span: 2 },
    { nome: "CPF", label: "CPF" },
    { nome: "MarcaExibida", label: "Marca Exibida", obrigatorio: true },
    { nome: "Vendedor", label: "Vendedor", somenteLeitura: true, obrigatorio: true },
    { nome: "TipoDocumento", label: "Tipo Documento", tipo: "tipo_documento", obrigatorio: true },
    { nome: "Origem", label: "Origem", obrigatorio: true },
    { nome: "SDR", label: "SDR" },
    { nome: "Agencia", label: "Agência", tipo: "empresa_agencia", span: 2 },
    { nome: "CnpjAgencia", label: "CNPJ Agência" },
    { nome: "PercentualAgencia", label: "% Agência" },
    { nome: "Bureau", label: "Bureau", tipo: "empresa_header", span: 2 },
    { nome: "PercentualBureau", label: "% Bureau" },
    { nome: "CnpjBureau", label: "CNPJ Bureau" },
    { nome: "Intermediario", label: "Intermediário", tipo: "empresa_header", span: 2 },
    { nome: "CnpjIntermediario", label: "CNPJ Intermediário" },
    { nome: "PercentualIntermediario", label: "% Intermediário" },
    { nome: "PercentualCartaAcordo", label: "% Carta Acordo" }
  ];

  const CAMPOS_CONTATO_CLIENTE_DIRETO_ASSINATURA = [
    { nome: "NomeResponsavelLegalProcuradorEmpresa", label: "Nome Responsável Legal / Procurador", span: 2, obrigatorio: true },
    { nome: "WhatsappEmpresa", label: "WhatsApp Empresa", obrigatorio: true },
    { nome: "NomeTestemunha", label: "Nome Testemunha", span: 2, obrigatorio: true },
    { nome: "Email", label: "E-mail", obrigatorio: true },
    { nome: "Telefone", label: "Telefone", obrigatorio: true }
  ];

  const CAMPOS_CONTATO_CLIENTE_DIRETO_FINANCEIRO = [
    { nome: "NomeFinanceiro", label: "Nome Financeiro", span: 2, obrigatorio: true },
    { nome: "EmailFinanceiro", label: "E-mail Financeiro", obrigatorio: true },
    { nome: "TelefoneFinanceiro", label: "Telefone Financeiro", obrigatorio: true }
  ];

  const CAMPOS_SOLICITACAO_ITEM = [
    { nome: "DataLancamento", label: "Data Lançamento", tipo: "date" },
    { nome: "Cota", label: "Cota" },
    { nome: "CidadeExibicao", label: "Cidade Exibição" },
    { nome: "Tipo", label: "Tipo" },
    { nome: "Origem", label: "Origem" },
    { nome: "EmpresaEuro", label: "Empresa Euro" },
    { nome: "CnpjExibibora", label: "CNPJ Exibidora" },
    { nome: "RazaoSocial", label: "Razão Social", span: 2 },
    { nome: "CPF", label: "CPF" },
    { nome: "MarcaExibida", label: "Marca Exibida" },
    { nome: "Vendedor", label: "Vendedor", somenteLeitura: true },
    { nome: "SDR", label: "SDR" },
    { nome: "Agencia", label: "Agência", span: 2 },
    { nome: "CnpjAgencia", label: "CNPJ Agência" },
    { nome: "Bureau", label: "Bureau", span: 2 },
    { nome: "CnpjBureau", label: "CNPJ Bureau" },
    { nome: "Intermediario", label: "Intermediário" },
    { nome: "CnpjIntermediario", label: "CNPJ Intermediário" },
    { nome: "DataAssinaturaRenovacao", label: "Data Assinatura / Renovação", tipo: "date" },
    { nome: "IDTrimestre", label: "Trimestre" },
    { nome: "TexmpoExposicao", label: "Tempo Exposição" },
    { nome: "DataInicioPrevisto", label: "Data Início Previsto", tipo: "date" },
    { nome: "DataTerminoPrevisto", label: "Data Término Previsto", tipo: "date" },
    { nome: "InicioRenovacao", label: "Início Renovação" },
    { nome: "FaturamentoBrutoMensal", label: "Faturamento Bruto Mensal" },
    { nome: "PercentualPermuta", label: "% Permuta" },
    { nome: "CotaOportunidade", label: "Cota Oportunidade" },
    { nome: "ValorPermuta", label: "Valor Permuta" },
    { nome: "FaturamentoLiquidoPermuta", label: "Faturamento Líquido Permuta" },
    { nome: "NumeroParcelas", label: "Número Parcelas" },
    { nome: "DataInicioVencimento", label: "Data Início Vencimento", tipo: "date" },
    { nome: "TotalBrutoContrato", label: "Total Bruto Contrato" },
    { nome: "TotalLiquidoContratoAGBRCTACORDO", label: "Total Líquido Contrato AGBR CTA Acordo" },
    { nome: "TotalLiquidoContratoAGBRVENDGERCOOR", label: "Total Líquido Contrato AGBR Vend/Ger/Coor" },
    { nome: "ValorMensalAgencia", label: "Valor Mensal Agência" },
    { nome: "ValorBureauMensal", label: "Valor Bureau Mensal" },
    { nome: "ValorCartaAcordoMensal", label: "Valor Carta Acordo Mensal" },
    { nome: "ValorOutrasComissoes", label: "Valor Outras Comissões" },
    { nome: "FaturamentoLiquidoMensal", label: "Faturamento Líquido Mensal" },
    { nome: "PercentualComissaoVendedor", label: "% Comissão Vendedor" },
    { nome: "ValorVendedor", label: "Valor Vendedor" },
    { nome: "ValorVendedorTotal", label: "Valor Vendedor Total" },
    { nome: "PercentualComissaoCoordenacao", label: "% Comissão Coordenação" },
    { nome: "ValorCoordenador", label: "Valor Coordenador" },
    { nome: "ValorCoordenadorTotal", label: "Valor Coordenador Total" },
    { nome: "PercentualComissaoGerencia", label: "% Comissão Gerência" },
    { nome: "ValorGerencia", label: "Valor Gerência" },
    { nome: "ValorGerenciaTotal", label: "Valor Gerência Total" },
    { nome: "AtivoCancelamento", label: "Ativo Cancelamento" },
    { nome: "FaturamentoLiquidoFinalMensal", label: "Faturamento Líquido Final Mensal" },
    { nome: "ComissaoGerenciaNordeste", label: "Comissão Gerência Nordeste" },
    { nome: "Faturamento", label: "Faturamento" },
    { nome: "DataCancelamento", label: "Data Cancelamento", tipo: "date" },
    { nome: "Status", label: "Status" },
    { nome: "OBS", label: "OBS", tipo: "textarea", span: 2 }
  ];

  let snapshotSolicitacaoEditavelAtual = null;
  let vendedorLogadoSolicitacaoAtual = null;
  let formularioSolicitacaoLiberadoNestaAbertura = false;
  let quantidadeItensFormularioSolicitacaoAtual = 1;
  let timerSincronizacaoFormularioSolicitacao = null;
  let timestampBloqueioReconciliacaoPainelFace = 0;
  const JANELA_BLOQUEIO_RECONCILIACAO_PAINEL_FACE_MS = 450;

  function idCampoSolicitacao(secao, nomeCampo, indice = null){
    const sufixoIndice = indice === null || indice === undefined ? "" : String(indice);
    return `solicitacao${secao}${sufixoIndice}${nomeCampo}`;
  }

  function normalizarDataParaInput(valor){
    const texto = safeStr(valor || "").trim();
    if (!texto) return "";

    if (/^\d{4}-\d{2}-\d{2}$/.test(texto)) return texto;

    const matchIso = texto.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (matchIso) return `${matchIso[1]}-${matchIso[2]}-${matchIso[3]}`;

    const matchBr = texto.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (matchBr) {
      const dia = matchBr[1];
      const mes = matchBr[2];
      const ano = matchBr[3];
      return `${ano}-${mes}-${dia}`;
    }

    return texto;
  }

  function parseDataIsoFormularioSolicitacao(valor){
    const dataIso = normalizarDataParaInput(valor);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dataIso)) return null;

    const partes = dataIso.split('-').map((parte) => Number(parte));
    const ano = partes[0];
    const mes = partes[1];
    const dia = partes[2];

    if (!Number.isInteger(ano) || !Number.isInteger(mes) || !Number.isInteger(dia)) return null;

    const dataUtc = new Date(Date.UTC(ano, mes - 1, dia));
    if (Number.isNaN(dataUtc.getTime())) return null;
    return dataUtc;
  }

  function calcularTempoExposicaoDiasFormularioSolicitacao(dataInicio, dataFim){
    const inicio = parseDataIsoFormularioSolicitacao(dataInicio);
    const fim = parseDataIsoFormularioSolicitacao(dataFim);
    if (!inicio || !fim) return null;

    const diferencaMs = fim.getTime() - inicio.getTime();
    if (diferencaMs < 0) return null;

    const umDiaMs = 24 * 60 * 60 * 1000;
    return Math.floor(diferencaMs / umDiaMs) + 1;
  }

  function atualizarTempoExposicaoFormularioSolicitacao(indice = null){
    const inputInicio = obterInputFormularioSolicitacao("Item", "DataInicioPrevisto", indice);
    const inputFim = obterInputFormularioSolicitacao("Item", "DataTerminoPrevisto", indice);
    const inputTempo = obterInputFormularioSolicitacao("Item", "TexmpoExposicao", indice);
    if (!inputTempo) return;

    const totalDias = calcularTempoExposicaoDiasFormularioSolicitacao(inputInicio?.value, inputFim?.value);
    inputTempo.value = Number.isInteger(totalDias) && totalDias > 0 ? String(totalDias) : "";
  }

  function nomeTipoDocumentoRegistro(item){
    return safeStr(item?.NomeTipoDocumento ?? item?.TipoDocumento ?? item?.nome_tipo_documento ?? "").trim();
  }

  function obterTipoDocumentoPorId(idTipoDocumento){
    const idTipo = idNum(idTipoDocumento || 0);
    if (!idTipo) return null;
    return tiposDocumentoPorId.get(idTipo) || null;
  }

  function obterTipoDocumentoPorNome(nomeTipoDocumento){
    const nomeNormalizado = normalizarTexto(nomeTipoDocumento || "");
    if (!nomeNormalizado) return null;
    return (Array.isArray(tiposDocumentoCatalogo) ? tiposDocumentoCatalogo : []).find((item) => {
      return normalizarTexto(nomeTipoDocumentoRegistro(item)) === nomeNormalizado;
    }) || null;
  }

  function obterIdTipoDocumentoAditivoDisponivel(){
    const porId = obterTipoDocumentoPorId(ID_TIPO_DOCUMENTO_ADITIVO);
    if (porId) return ID_TIPO_DOCUMENTO_ADITIVO;

    const porNome = obterTipoDocumentoPorNome("ADITIVO");
    return idNum(porNome?.IDDimTipoDocumento || 0) || null;
  }

  function obterNomeTipoDocumentoPorId(idTipoDocumento){
    const item = obterTipoDocumentoPorId(idTipoDocumento);
    return nomeTipoDocumentoRegistro(item) || "";
  }

  function resolverIdTipoDocumentoFormulario(registro = {}, valorFallback = null){
    const idDireto = idNum(
      registro?.IDDimTipoDocumento ??
      registro?.id_dim_tipo_documento ??
      registro?.idTipoDocumento ??
      valorFallback ??
      0
    );
    if (idDireto && obterTipoDocumentoPorId(idDireto)) return idDireto;

    const nome = safeStr(
      registro?.TipoDocumento ??
      registro?.NomeTipoDocumento ??
      registro?.nome_tipo_documento ??
      valorFallback ??
      ""
    ).trim();
    const porNome = obterTipoDocumentoPorNome(nome);
    return idNum(porNome?.IDDimTipoDocumento || 0) || null;
  }

  function preencherOpcoesSelectTipoDocumento(selectEl){
    if (!selectEl) return;
    const valorAtual = safeStr(selectEl.value || "").trim();
    selectEl.innerHTML = "";
    selectEl.appendChild(el("option", { value: "" }, ["— Selecione —"]));

    (Array.isArray(tiposDocumentoCatalogo) ? tiposDocumentoCatalogo : []).forEach((item) => {
      const idTipo = idNum(item?.IDDimTipoDocumento || 0);
      const nomeTipo = nomeTipoDocumentoRegistro(item);
      if (!idTipo || !nomeTipo) return;
      selectEl.appendChild(el("option", { value: String(idTipo) }, [nomeTipo]));
    });

    if (valorAtual && [...selectEl.options].some((option) => option.value === valorAtual)) {
      selectEl.value = valorAtual;
    }
  }

  function aplicarTipoDocumentoAditivoPadraoNosItensFormulario(){
    const fluxo = obterFluxoContratoAtual();
    if (fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO) return;

    const idAditivo = obterIdTipoDocumentoAditivoDisponivel();
    if (!idAditivo) return;

    const selectTipoDocumentoHeader = obterInputFormularioSolicitacao("Header", "TipoDocumento");
    if (!selectTipoDocumentoHeader || selectTipoDocumentoHeader.tagName !== "SELECT") return;
    if (safeStr(selectTipoDocumentoHeader.value || "").trim()) return;
    if (![...selectTipoDocumentoHeader.options].some((option) => option.value === String(idAditivo))) return;

    selectTipoDocumentoHeader.value = String(idAditivo);
  }


  function substituirInputsTipoDocumentoLegadosPorSelect(){
    const campoHeader = obterInputFormularioSolicitacao("Header", "TipoDocumento");

    if (campoHeader) {
      if (campoHeader.tagName === "SELECT") {
        preencherOpcoesSelectTipoDocumento(campoHeader);
      } else if (campoHeader.tagName === "INPUT") {
        const valorAtual = safeStr(campoHeader.value || "").trim();
        const select = document.createElement("select");
        select.className = "kb-select";
        select.id = campoHeader.id;

        Array.from(campoHeader.attributes || []).forEach((attr) => {
          if (["type", "value", "class"].includes(String(attr.name || "").toLowerCase())) return;
          select.setAttribute(attr.name, attr.value);
        });

        select.dataset.tipoCampoSolicitacao = "tipo_documento";
        preencherOpcoesSelectTipoDocumento(select);

        const idTipoDocumento = resolverIdTipoDocumentoFormulario({}, valorAtual);
        select.value = idTipoDocumento ? String(idTipoDocumento) : "";

        campoHeader.replaceWith(select);
      }
    }

    for (let indice = 0; indice < quantidadeItensFormularioSolicitacaoAtual; indice += 1) {
      const campoItemLegado = obterInputFormularioSolicitacao("Item", "TipoDocumento", indice);
      if (!campoItemLegado) continue;

      const wrapCampo = campoItemLegado.closest(".kb-contrato-campo");
      if (wrapCampo) {
        wrapCampo.remove();
      } else {
        campoItemLegado.remove();
      }
    }
  }


  function obterValorPercentualAgenciaHeaderFormulario(){
    const input = obterInputFormularioSolicitacao("Header", "PercentualAgencia");
    return safeStr(input?.value || "").trim();
  }

  function obterValorPercentualCartaAcordoHeaderFormulario(){
    const input = obterInputFormularioSolicitacao("Header", "PercentualCartaAcordo");
    return safeStr(input?.value || "").trim();
  }

  function obterValorPercentualBureauHeaderFormulario(){
    const input = obterInputFormularioSolicitacao("Header", "PercentualBureau");
    return safeStr(input?.value || "").trim();
  }

  function obterValorPercentualIntermediarioHeaderFormulario(){
    const input = obterInputFormularioSolicitacao("Header", "PercentualIntermediario");
    return safeStr(input?.value || "").trim();
  }

  function resolverIdEmpresaAgenciaFormulario(valor, registro = {}){
    const idDireto = idNum(
      registro?.IDEmpresaAgencia ??
      registro?.id_empresa_agencia ??
      registro?.IDAgencia ??
      registro?.id_agencia ??
      0
    );
    if (idDireto > 0) return String(idDireto);

    const valorTexto = safeStr(valor || "").trim();
    if (/^\d+$/.test(valorTexto)) return valorTexto;

    const idAgenciaCard = safeStr(selectAgenciaCard?.value || "").trim();
    const empresaAgenciaCard = idAgenciaCard ? obterEmpresaCatalogoPorId(idAgenciaCard) : null;
    const textoAgenciaCard = safeStr(empresaAgenciaCard?.RazaoSocial || "").trim();
    if (idAgenciaCard && valorTexto && normalizarTexto(textoAgenciaCard) === normalizarTexto(valorTexto)) {
      return idAgenciaCard;
    }

    const empresaPorTexto = localizarEmpresaPorTextoDigitado(valorTexto);
    const idPorTexto = safeStr(
      empresaPorTexto?.IDEmpresa ??
      empresaPorTexto?.IDEmpresaProprietaria ??
      empresaPorTexto?.ID ??
      ""
    ).trim();
    return idPorTexto || "";
  }

  function garantirOpcaoAgenciaFormularioNoSelect(selectEl, idEmp){
    if (!selectEl) return;
    const valor = safeStr(idEmp || "").trim();
    if (!valor) return;

    const jaExiste = Array.from(selectEl.options || []).some((opt) => safeStr(opt.value || "").trim() === valor);
    if (jaExiste) return;

    const empresa = obterEmpresaCatalogoPorId(valor);
    const texto = empresa ? textoOpcaoEmpresa(empresa) : `Empresa #${valor}`;
    selectEl.appendChild(el("option", { value: valor }, [texto]));
  }

  function atualizarInputAgenciaFormulario(selectEl, inputBusca){
    if (!selectEl || !inputBusca) return;
    const idEmp = safeStr(selectEl.value || "").trim();
    inputBusca.value = idEmp ? obterTextoAgenciaSelecionada(idEmp) : "";
  }

  function obterCnpjEmpresaCatalogoFormatado(idEmp){
    const idEmpresa = safeStr(idEmp || "").trim();
    if (!idEmpresa) return "";

    const empresa = obterEmpresaCatalogoPorId(idEmpresa);
    const cnpj = safeStr(
      empresa?.CNPJ ??
      empresa?.EmpresaCNPJ ??
      empresa?.cnpj ??
      ""
    ).trim();

    return cnpj ? mascaraCnpj(cnpj) : "";
  }

  function definirCnpjAgenciaHeaderFormulario(cnpj){
    const inputCnpjAgencia = obterInputFormularioSolicitacao("Header", "CnpjAgencia");
    if (!inputCnpjAgencia) return;

    const valorFormatado = safeStr(cnpj || "").trim() ? mascaraCnpj(cnpj) : "";
    const valorAnterior = safeStr(inputCnpjAgencia.value || "").trim();

    inputCnpjAgencia.value = valorFormatado;

    const wrapCampo = inputCnpjAgencia.closest(".kb-contrato-campo");
    if (wrapCampo && valorFormatado) {
      wrapCampo.classList.remove("is-invalido");
    }

    if (valorAnterior !== valorFormatado) {
      inputCnpjAgencia.dispatchEvent(new Event("input", { bubbles: true }));
      inputCnpjAgencia.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function atualizarCnpjAgenciaHeaderFormularioPorEmpresa(idEmp){
    const cnpj = obterCnpjEmpresaCatalogoFormatado(idEmp);
    definirCnpjAgenciaHeaderFormulario(cnpj);
  }

  function obterEmpresaAgenciaEfetivaFormulario(){
    const idTipoCliente = obterIdTipoClienteAtualFormularioSolicitacao();

    if (idTipoCliente === 3) {
      return obterEmpresaSelecionadaDoCampo(selectEmpresaCard);
    }

    return obterEmpresaSelecionadaDoCampo(selectAgenciaCard);
  }

  function obterDadosAgenciaEfetivaFormulario(){
    const empresa = obterEmpresaAgenciaEfetivaFormulario();
    const inputCnpjAgencia = obterInputFormularioSolicitacao("Header", "CnpjAgencia");
    const cnpjFallback = safeStr(inputCnpjAgencia?.value || "").trim();

    return {
      IDEmpresaAgencia: idNum(empresa?.IDEmpresa ?? empresa?.ID ?? 0) || null,
      Agencia: safeStr(empresa?.RazaoSocial || empresa?.EmpresaRazaoSocial || "").trim() || null,
      CnpjAgencia: safeStr(empresa?.CNPJ || empresa?.EmpresaCNPJ || cnpjFallback || "").trim() || null,
    };
  }

  function sincronizarCnpjAgenciaHeaderComAgenciaEfetiva(){
    const idTipoCliente = obterIdTipoClienteAtualFormularioSolicitacao();

    if (idTipoCliente !== 3) return;

    const dadosAgencia = obterDadosAgenciaEfetivaFormulario();
    definirCnpjAgenciaHeaderFormulario(dadosAgencia.CnpjAgencia || "");
  }

  function fecharListaAgenciaFormulario(comboEl, listaEl){
    if (comboEl) comboEl.classList.remove("is-open");
    if (listaEl) listaEl.hidden = true;
  }

  function renderizarListaAgenciaFormulario(texto, refs, opcoes = {}){
    const listaEl = refs?.listaEl || null;
    const selectEl = refs?.selectEl || null;
    const inputBusca = refs?.inputBusca || null;
    const comboEl = refs?.comboEl || null;
    if (!listaEl || !selectEl) return;

    const base = Array.isArray(opcoes.empresas)
      ? opcoes.empresas
      : filtrarEmpresasCombobox(texto);

    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    const valorSelecionado = safeStr(selectEl.value || "").trim();

    listaEl.innerHTML = "";
    if (!filtradas.length) {
      listaEl.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Nenhuma agência encontrada."]));
      return;
    }

    filtradas.forEach((item) => {
      const id = safeStr(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? "").trim();
      if (!id) return;

      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "—").trim() || "—";
      const cnpj = mascaraCnpj(item?.CNPJ || item?.EmpresaCNPJ || "");
      const botao = el("button", { type: "button", class: `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [razao]),
        el("span", {}, [cnpj || "Sem CNPJ"])
      ]);

      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarAgenciaFormularioPorId(selectEl, inputBusca, listaEl, comboEl, id, { atualizarCard: true });
      });

      listaEl.appendChild(botao);
    });
  }

  function abrirListaAgenciaFormulario(comboEl, inputBusca, listaEl, selectEl){
    if (!comboEl || !listaEl || !selectEl) return;
    comboEl.classList.add("is-open");
    listaEl.hidden = false;
    renderizarListaAgenciaFormulario(inputBusca?.value || "", { comboEl, inputBusca, listaEl, selectEl });
  }

  async function selecionarAgenciaFormularioPorId(selectEl, inputBusca, listaEl, comboEl, idEmp, opcoes = {}){
    if (!selectEl) return;

    const novoValor = safeStr(idEmp || "").trim();
    const valorAnterior = safeStr(selectEl.value || "").trim();

    if (!novoValor) {
      selectEl.value = "";
      if (inputBusca) inputBusca.value = "";
      atualizarCnpjAgenciaHeaderFormularioPorEmpresa("");
      sincronizarAgenciaBureauHeaderNosItensFormulario();
      fecharListaAgenciaFormulario(comboEl, listaEl);
      if (valorAnterior) selectEl.dispatchEvent(new Event("change", { bubbles: true }));
      if (opcoes.atualizarCard) {
        await selecionarAgenciaPorIdComGarantia("", false);
      }
      agendarSincronizacaoFormularioSolicitacao();
      return;
    }

    await garantirAgenciaNoCatalogoPorId(novoValor);
    garantirOpcaoAgenciaFormularioNoSelect(selectEl, novoValor);
    selectEl.value = novoValor;
    atualizarInputAgenciaFormulario(selectEl, inputBusca);
    atualizarCnpjAgenciaHeaderFormularioPorEmpresa(novoValor);
    sincronizarAgenciaBureauHeaderNosItensFormulario();
    fecharListaAgenciaFormulario(comboEl, listaEl);

    if (novoValor !== valorAnterior) {
      selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    }

    if (opcoes.atualizarCard) {
      await selecionarAgenciaPorIdComGarantia(novoValor, true);
    }

    agendarSincronizacaoFormularioSolicitacao();
  }

  function reconciliarBuscaAgenciaFormularioDigitada(selectEl, inputBusca, listaEl, comboEl){
    if (!selectEl || !inputBusca) return;

    const textoDigitado = safeStr(inputBusca.value || "").trim();
    if (!textoDigitado) {
      selecionarAgenciaFormularioPorId(selectEl, inputBusca, listaEl, comboEl, "", { atualizarCard: true }).catch((erro) => {
        console.warn("reconciliarBuscaAgenciaFormularioDigitada: falhou ao limpar agência", erro);
      });
      return;
    }

    const empresa = localizarEmpresaPorTextoDigitado(textoDigitado);
    const id = safeStr(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? "").trim();
    if (id) {
      selecionarAgenciaFormularioPorId(selectEl, inputBusca, listaEl, comboEl, id, { atualizarCard: true }).catch((erro) => {
        console.warn("reconciliarBuscaAgenciaFormularioDigitada: falhou ao selecionar agência", erro);
      });
      return;
    }

    atualizarInputAgenciaFormulario(selectEl, inputBusca);
    fecharListaAgenciaFormulario(comboEl, listaEl);
  }

  function configurarComboboxAgenciaFormularioSolicitacao(comboEl, inputBusca, btnToggle, listaEl, selectEl){
    if (!comboEl || !inputBusca || !btnToggle || !listaEl || !selectEl) return;
    if (comboEl.dataset.comboAgenciaFormularioConfigurado === "1") return;
    comboEl.dataset.comboAgenciaFormularioConfigurado = "1";

    const buscarRemoto = () => {
      const termo = inputBusca.value || "";
      abrirListaAgenciaFormulario(comboEl, inputBusca, listaEl, selectEl);

      if (!safeStr(termo).trim()) {
        buscarEmpresasRemoto("", {
          tipo: "agencia",
          listaDestino: listaEl,
          renderizador: (texto, opcoes) => renderizarListaAgenciaFormulario(texto, { comboEl, inputBusca, listaEl, selectEl }, opcoes),
        }).catch((erro) => console.warn("agência header vazio: falhou", erro));
        return;
      }

      const termoDigits = normalizaCnpj(termo);
      if (safeStr(termo).trim().length >= 2 || termoDigits.length >= 4) {
        buscarEmpresasRemoto(termo, {
          tipo: "agencia",
          listaDestino: listaEl,
          renderizador: (texto, opcoes) => renderizarListaAgenciaFormulario(texto, { comboEl, inputBusca, listaEl, selectEl }, opcoes),
        }).catch((erro) => {
          console.warn("agência header remoto: falhou", erro);
          renderizarListaAgenciaFormulario(termo, { comboEl, inputBusca, listaEl, selectEl });
        });
        return;
      }

      renderizarListaAgenciaFormulario(termo, { comboEl, inputBusca, listaEl, selectEl });
    };

    inputBusca.addEventListener("focus", buscarRemoto);
    inputBusca.addEventListener("click", buscarRemoto);
    inputBusca.addEventListener("input", buscarRemoto);

    selectEl.addEventListener("change", () => {
      atualizarCnpjAgenciaHeaderFormularioPorEmpresa(selectEl.value || "");
      sincronizarAgenciaBureauHeaderNosItensFormulario();
      agendarSincronizacaoFormularioSolicitacao();
    });

    inputBusca.addEventListener("keydown", (evento) => {
      if (evento.key === "Enter") {
        evento.preventDefault();
        const primeira = filtrarEmpresasCombobox(inputBusca.value || "")[0] || null;
        const id = safeStr(primeira?.IDEmpresa ?? primeira?.IDEmpresaProprietaria ?? primeira?.ID ?? "").trim();
        if (id) {
          selecionarAgenciaFormularioPorId(selectEl, inputBusca, listaEl, comboEl, id, { atualizarCard: true }).catch((erro) => {
            console.warn("keydown agência header: falhou ao selecionar primeira opção", erro);
          });
          return;
        }
        reconciliarBuscaAgenciaFormularioDigitada(selectEl, inputBusca, listaEl, comboEl);
        return;
      }

      if (evento.key === "Escape") {
        evento.preventDefault();
        atualizarInputAgenciaFormulario(selectEl, inputBusca);
        fecharListaAgenciaFormulario(comboEl, listaEl);
        return;
      }

      if (evento.key === "ArrowDown") {
        abrirListaAgenciaFormulario(comboEl, inputBusca, listaEl, selectEl);
      }
    });

    btnToggle.addEventListener("click", () => {
      if (listaEl.hidden) abrirListaAgenciaFormulario(comboEl, inputBusca, listaEl, selectEl);
      else {
        atualizarInputAgenciaFormulario(selectEl, inputBusca);
        fecharListaAgenciaFormulario(comboEl, listaEl);
      }
    });

    inputBusca.addEventListener("blur", () => {
      window.setTimeout(() => reconciliarBuscaAgenciaFormularioDigitada(selectEl, inputBusca, listaEl, comboEl), 140);
    });
  }

  function setValorAgenciaHeaderFormulario(valor, registro = {}){
    const selectEl = obterInputFormularioSolicitacao("Header", "Agencia");
    if (!selectEl) return;

    const comboEl = selectEl.closest(".kb-combobox");
    const inputBusca = comboEl?.querySelector('[data-role="input-agencia-header-busca"]') || null;
    const listaEl = comboEl?.querySelector('[data-role="lista-agencia-header-busca"]') || null;

    const idEmp = resolverIdEmpresaAgenciaFormulario(valor, registro);
    if (idEmp) {
      garantirOpcaoAgenciaFormularioNoSelect(selectEl, idEmp);
      selectEl.value = idEmp;
      atualizarInputAgenciaFormulario(selectEl, inputBusca);
      atualizarCnpjAgenciaHeaderFormularioPorEmpresa(idEmp);
      sincronizarAgenciaBureauHeaderNosItensFormulario();
      return;
    }

    selectEl.value = "";
    if (inputBusca) inputBusca.value = safeStr(valor || "").trim();
    definirCnpjAgenciaHeaderFormulario(registro?.CnpjAgencia ?? registro?.CNPJAgencia ?? "");
    sincronizarAgenciaBureauHeaderNosItensFormulario();
    fecharListaAgenciaFormulario(comboEl, listaEl);
  }

  function obterAgenciaHeaderFormularioSelecionada(){
    const idTipoCliente = obterIdTipoClienteAtualFormularioSolicitacao();

    if (idTipoCliente === 3) {
      return obterDadosAgenciaEfetivaFormulario();
    }

    if (campoFormularioSolicitacaoOcultoPorTipoCliente("Agencia", idTipoCliente, "Header")) {
      return {
        IDEmpresaAgencia: null,
        Agencia: null,
        CnpjAgencia: null,
      };
    }

    const selectEl = obterInputFormularioSolicitacao("Header", "Agencia");
    const idEmp = safeStr(selectEl?.value || "").trim();
    const empresa = idEmp ? obterEmpresaCatalogoPorId(idEmp) : null;
    const comboEl = selectEl?.closest?.(".kb-combobox") || null;
    const textoDigitado = safeStr(comboEl?.querySelector('[data-role="input-agencia-header-busca"]')?.value || "").trim();

    const inputCnpjAgencia = obterInputFormularioSolicitacao("Header", "CnpjAgencia");
    const cnpjDigitado = safeStr(inputCnpjAgencia?.value || "").trim();

    return {
      IDEmpresaAgencia: idEmp ? Number(idEmp) : null,
      Agencia: safeStr(empresa?.RazaoSocial || textoDigitado || "").trim() || null,
      CnpjAgencia: safeStr(empresa?.CNPJ || empresa?.EmpresaCNPJ || cnpjDigitado || "").trim() || null,
    };
  }


  function obterConfigEmpresaHeaderFormulario(nomeCampo){
    const nome = safeStr(nomeCampo || "").trim();
    const configs = {
      Bureau: {
        nomeCampo: "Bureau",
        nomeId: "IDEmpresaBureau",
        campoCnpj: "CnpjBureau",
        rotulo: "Bureau",
        placeholder: "Digite razão social ou CNPJ do bureau...",
        aria: "Abrir lista de bureaus",
        textoVazio: "Nenhum bureau encontrado.",
        dataRole: "bureau-header",
      },
      Intermediario: {
        nomeCampo: "Intermediario",
        nomeId: "IDEmpresaIntermediario",
        campoCnpj: "CnpjIntermediario",
        rotulo: "Intermediário",
        placeholder: "Digite razão social ou CNPJ do intermediário...",
        aria: "Abrir lista de intermediários",
        textoVazio: "Nenhum intermediário encontrado.",
        dataRole: "intermediario-header",
      },
    };
    return configs[nome] || null;
  }

  function campoHeaderEmpresaEhCombobox(nomeCampo){
    return !!obterConfigEmpresaHeaderFormulario(nomeCampo);
  }

  function resolverIdEmpresaHeaderFormulario(nomeCampo, valor, registro = {}){
    const cfg = obterConfigEmpresaHeaderFormulario(nomeCampo);
    if (!cfg) return "";

    const nomeLower = cfg.nomeCampo === "Bureau" ? "bureau" : "intermediario";
    const aliases = [
      cfg.nomeId,
      `id_empresa_${nomeLower}`,
      `ID${cfg.nomeCampo}`,
      `id_${nomeLower}`,
    ];

    for (const chave of aliases) {
      const idDireto = idNum(registro?.[chave] ?? 0);
      if (idDireto > 0) return String(idDireto);
    }

    const valorTexto = safeStr(valor || "").trim();
    if (/^\d+$/.test(valorTexto)) return valorTexto;

    const empresaPorTexto = localizarEmpresaPorTextoDigitado(valorTexto);
    const idPorTexto = safeStr(
      empresaPorTexto?.IDEmpresa ??
      empresaPorTexto?.IDEmpresaProprietaria ??
      empresaPorTexto?.ID ??
      ""
    ).trim();

    return idPorTexto || "";
  }

  function garantirOpcaoEmpresaHeaderFormularioNoSelect(selectEl, idEmp){
    if (!selectEl) return;
    const valor = safeStr(idEmp || "").trim();
    if (!valor) return;

    const jaExiste = Array.from(selectEl.options || []).some((opt) => safeStr(opt.value || "").trim() === valor);
    if (jaExiste) return;

    const empresa = obterEmpresaCatalogoPorId(valor);
    const texto = empresa ? textoOpcaoEmpresa(empresa) : `Empresa #${valor}`;
    selectEl.appendChild(el("option", { value: valor }, [texto]));
  }

  function atualizarInputEmpresaHeaderFormulario(selectEl, inputBusca){
    if (!selectEl || !inputBusca) return;
    const idEmp = safeStr(selectEl.value || "").trim();
    inputBusca.value = idEmp ? obterTextoEmpresaSelecionada(idEmp) : "";
  }

  function definirCnpjEmpresaHeaderFormulario(nomeCampo, cnpj){
    const cfg = obterConfigEmpresaHeaderFormulario(nomeCampo);
    if (!cfg) return;

    const inputCnpj = obterInputFormularioSolicitacao("Header", cfg.campoCnpj);
    if (!inputCnpj) return;

    const valorFormatado = safeStr(cnpj || "").trim() ? mascaraCnpj(cnpj) : "";
    const valorAnterior = safeStr(inputCnpj.value || "").trim();
    inputCnpj.value = valorFormatado;

    const wrapCampo = inputCnpj.closest(".kb-contrato-campo");
    if (wrapCampo && valorFormatado) {
      wrapCampo.classList.remove("is-invalido");
    }

    if (valorAnterior !== valorFormatado) {
      inputCnpj.dispatchEvent(new Event("input", { bubbles: true }));
      inputCnpj.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function atualizarCnpjEmpresaHeaderFormularioPorEmpresa(nomeCampo, idEmp){
    const cnpj = obterCnpjEmpresaCatalogoFormatado(idEmp);
    definirCnpjEmpresaHeaderFormulario(nomeCampo, cnpj);
  }

  function fecharListaEmpresaHeaderFormulario(comboEl, listaEl){
    if (comboEl) comboEl.classList.remove("is-open");
    if (listaEl) listaEl.hidden = true;
  }

  function renderizarListaEmpresaHeaderFormulario(nomeCampo, texto, refs, opcoes = {}){
    const cfg = obterConfigEmpresaHeaderFormulario(nomeCampo);
    const listaEl = refs?.listaEl || null;
    const selectEl = refs?.selectEl || null;
    const inputBusca = refs?.inputBusca || null;
    const comboEl = refs?.comboEl || null;
    if (!cfg || !listaEl || !selectEl) return;

    const base = Array.isArray(opcoes.empresas)
      ? opcoes.empresas
      : filtrarEmpresasCombobox(texto);

    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    const valorSelecionado = safeStr(selectEl.value || "").trim();

    listaEl.innerHTML = "";
    if (!filtradas.length) {
      listaEl.appendChild(el("div", { class: "kb-combobox-vazio" }, [cfg.textoVazio]));
      return;
    }

    filtradas.forEach((item) => {
      const id = safeStr(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? "").trim();
      if (!id) return;

      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "—").trim() || "—";
      const cnpj = mascaraCnpj(item?.CNPJ || item?.EmpresaCNPJ || "");
      const botao = el("button", { type: "button", class: `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [razao]),
        el("span", {}, [cnpj || "Sem CNPJ"])
      ]);

      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarEmpresaHeaderFormularioPorId(nomeCampo, selectEl, inputBusca, listaEl, comboEl, id);
      });

      listaEl.appendChild(botao);
    });
  }

  function abrirListaEmpresaHeaderFormulario(nomeCampo, comboEl, inputBusca, listaEl, selectEl){
    if (!comboEl || !listaEl || !selectEl) return;
    comboEl.classList.add("is-open");
    listaEl.hidden = false;
    renderizarListaEmpresaHeaderFormulario(nomeCampo, inputBusca?.value || "", { comboEl, inputBusca, listaEl, selectEl });
  }

  async function selecionarEmpresaHeaderFormularioPorId(nomeCampo, selectEl, inputBusca, listaEl, comboEl, idEmp){
    if (!selectEl) return;

    const novoValor = safeStr(idEmp || "").trim();
    const valorAnterior = safeStr(selectEl.value || "").trim();

    if (!novoValor) {
      selectEl.value = "";
      if (inputBusca) inputBusca.value = "";
      atualizarCnpjEmpresaHeaderFormularioPorEmpresa(nomeCampo, "");
      if (nomeCampo === "Bureau") sincronizarAgenciaBureauHeaderNosItensFormulario();
      fecharListaEmpresaHeaderFormulario(comboEl, listaEl);
      if (valorAnterior) selectEl.dispatchEvent(new Event("change", { bubbles: true }));
      agendarSincronizacaoFormularioSolicitacao();
      return;
    }

    await garantirEmpresaNoCatalogoPorId(novoValor);
    garantirOpcaoEmpresaHeaderFormularioNoSelect(selectEl, novoValor);
    selectEl.value = novoValor;
    atualizarInputEmpresaHeaderFormulario(selectEl, inputBusca);
    atualizarCnpjEmpresaHeaderFormularioPorEmpresa(nomeCampo, novoValor);
    if (nomeCampo === "Bureau") sincronizarAgenciaBureauHeaderNosItensFormulario();
    fecharListaEmpresaHeaderFormulario(comboEl, listaEl);

    if (novoValor !== valorAnterior) {
      selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    }

    agendarSincronizacaoFormularioSolicitacao();
  }

  function reconciliarBuscaEmpresaHeaderFormularioDigitada(nomeCampo, selectEl, inputBusca, listaEl, comboEl){
    if (!selectEl || !inputBusca) return;

    const textoDigitado = safeStr(inputBusca.value || "").trim();
    if (!textoDigitado) {
      selecionarEmpresaHeaderFormularioPorId(nomeCampo, selectEl, inputBusca, listaEl, comboEl, "").catch((erro) => {
        console.warn("reconciliarBuscaEmpresaHeaderFormularioDigitada: falhou ao limpar empresa", erro);
      });
      return;
    }

    const empresa = localizarEmpresaPorTextoDigitado(textoDigitado);
    const id = safeStr(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? "").trim();
    if (id) {
      selecionarEmpresaHeaderFormularioPorId(nomeCampo, selectEl, inputBusca, listaEl, comboEl, id).catch((erro) => {
        console.warn("reconciliarBuscaEmpresaHeaderFormularioDigitada: falhou ao selecionar empresa", erro);
      });
      return;
    }

    atualizarInputEmpresaHeaderFormulario(selectEl, inputBusca);
    fecharListaEmpresaHeaderFormulario(comboEl, listaEl);
  }

  function configurarComboboxEmpresaHeaderFormularioSolicitacao(nomeCampo, comboEl, inputBusca, btnToggle, listaEl, selectEl){
    const cfg = obterConfigEmpresaHeaderFormulario(nomeCampo);
    if (!cfg || !comboEl || !inputBusca || !btnToggle || !listaEl || !selectEl) return;
    if (comboEl.dataset.comboEmpresaHeaderConfigurado === "1") return;
    comboEl.dataset.comboEmpresaHeaderConfigurado = "1";

    const buscarRemoto = () => {
      const termo = inputBusca.value || "";
      abrirListaEmpresaHeaderFormulario(nomeCampo, comboEl, inputBusca, listaEl, selectEl);

      if (!safeStr(termo).trim()) {
        buscarEmpresasRemoto("", {
          tipo: "empresa",
          listaDestino: listaEl,
          renderizador: (texto, opcoes) => renderizarListaEmpresaHeaderFormulario(nomeCampo, texto, { comboEl, inputBusca, listaEl, selectEl }, opcoes),
        }).catch((erro) => console.warn(`${cfg.rotulo} header vazio: falhou`, erro));
        return;
      }

      const termoDigits = normalizaCnpj(termo);
      if (safeStr(termo).trim().length >= 2 || termoDigits.length >= 4) {
        buscarEmpresasRemoto(termo, {
          tipo: "empresa",
          listaDestino: listaEl,
          renderizador: (texto, opcoes) => renderizarListaEmpresaHeaderFormulario(nomeCampo, texto, { comboEl, inputBusca, listaEl, selectEl }, opcoes),
        }).catch((erro) => {
          console.warn(`${cfg.rotulo} header remoto: falhou`, erro);
          renderizarListaEmpresaHeaderFormulario(nomeCampo, termo, { comboEl, inputBusca, listaEl, selectEl });
        });
        return;
      }

      renderizarListaEmpresaHeaderFormulario(nomeCampo, termo, { comboEl, inputBusca, listaEl, selectEl });
    };

    inputBusca.addEventListener("focus", buscarRemoto);
    inputBusca.addEventListener("click", buscarRemoto);
    inputBusca.addEventListener("input", buscarRemoto);

    selectEl.addEventListener("change", () => {
      atualizarCnpjEmpresaHeaderFormularioPorEmpresa(nomeCampo, selectEl.value || "");
      if (nomeCampo === "Bureau") sincronizarAgenciaBureauHeaderNosItensFormulario();
      agendarSincronizacaoFormularioSolicitacao();
    });

    inputBusca.addEventListener("keydown", (evento) => {
      if (evento.key === "Enter") {
        evento.preventDefault();
        const primeira = filtrarEmpresasCombobox(inputBusca.value || "")[0] || null;
        const id = safeStr(primeira?.IDEmpresa ?? primeira?.IDEmpresaProprietaria ?? primeira?.ID ?? "").trim();
        if (id) {
          selecionarEmpresaHeaderFormularioPorId(nomeCampo, selectEl, inputBusca, listaEl, comboEl, id).catch((erro) => {
            console.warn(`keydown ${cfg.rotulo} header: falhou ao selecionar primeira opção`, erro);
          });
          return;
        }
        reconciliarBuscaEmpresaHeaderFormularioDigitada(nomeCampo, selectEl, inputBusca, listaEl, comboEl);
        return;
      }

      if (evento.key === "Escape") {
        evento.preventDefault();
        atualizarInputEmpresaHeaderFormulario(selectEl, inputBusca);
        fecharListaEmpresaHeaderFormulario(comboEl, listaEl);
        return;
      }

      if (evento.key === "ArrowDown") {
        abrirListaEmpresaHeaderFormulario(nomeCampo, comboEl, inputBusca, listaEl, selectEl);
      }
    });

    btnToggle.addEventListener("click", () => {
      if (listaEl.hidden) abrirListaEmpresaHeaderFormulario(nomeCampo, comboEl, inputBusca, listaEl, selectEl);
      else {
        atualizarInputEmpresaHeaderFormulario(selectEl, inputBusca);
        fecharListaEmpresaHeaderFormulario(comboEl, listaEl);
      }
    });

    inputBusca.addEventListener("blur", () => {
      window.setTimeout(() => reconciliarBuscaEmpresaHeaderFormularioDigitada(nomeCampo, selectEl, inputBusca, listaEl, comboEl), 140);
    });
  }

  function setValorEmpresaHeaderFormulario(nomeCampo, valor, registro = {}){
    const selectEl = obterInputFormularioSolicitacao("Header", nomeCampo);
    const cfg = obterConfigEmpresaHeaderFormulario(nomeCampo);
    if (!selectEl || !cfg) return;

    const comboEl = selectEl.closest(".kb-combobox");
    const inputBusca = comboEl?.querySelector(`[data-role="input-${cfg.dataRole}-busca"]`) || null;
    const listaEl = comboEl?.querySelector(`[data-role="lista-${cfg.dataRole}-busca"]`) || null;

    const idEmp = resolverIdEmpresaHeaderFormulario(nomeCampo, valor, registro);
    if (idEmp) {
      garantirOpcaoEmpresaHeaderFormularioNoSelect(selectEl, idEmp);
      selectEl.value = idEmp;
      atualizarInputEmpresaHeaderFormulario(selectEl, inputBusca);
      atualizarCnpjEmpresaHeaderFormularioPorEmpresa(nomeCampo, idEmp);
      if (nomeCampo === "Bureau") sincronizarAgenciaBureauHeaderNosItensFormulario();
      return;
    }

    selectEl.value = "";
    if (inputBusca) inputBusca.value = safeStr(valor || "").trim();
    definirCnpjEmpresaHeaderFormulario(nomeCampo, registro?.[cfg.campoCnpj] ?? "");
    if (nomeCampo === "Bureau") sincronizarAgenciaBureauHeaderNosItensFormulario();
    fecharListaEmpresaHeaderFormulario(comboEl, listaEl);
  }

  function obterEmpresaHeaderFormularioSelecionada(nomeCampo){
    const cfg = obterConfigEmpresaHeaderFormulario(nomeCampo);
    if (!cfg) return {};

    const idTipoCliente = obterIdTipoClienteAtualFormularioSolicitacao();
    if (campoFormularioSolicitacaoOcultoPorTipoCliente(nomeCampo, idTipoCliente, "Header")) {
      return {
        [cfg.nomeId]: null,
        [cfg.nomeCampo]: null,
        [cfg.campoCnpj]: null,
      };
    }

    const selectEl = obterInputFormularioSolicitacao("Header", nomeCampo);
    const idEmp = safeStr(selectEl?.value || "").trim();
    const empresa = idEmp ? obterEmpresaCatalogoPorId(idEmp) : null;
    const comboEl = selectEl?.closest?.(".kb-combobox") || null;
    const textoDigitado = safeStr(comboEl?.querySelector(`[data-role="input-${cfg.dataRole}-busca"]`)?.value || "").trim();
    const inputCnpj = obterInputFormularioSolicitacao("Header", cfg.campoCnpj);
    const cnpjDigitado = safeStr(inputCnpj?.value || "").trim();

    return {
      [cfg.nomeId]: idEmp ? Number(idEmp) : null,
      [cfg.nomeCampo]: safeStr(empresa?.RazaoSocial || textoDigitado || "").trim() || null,
      [cfg.campoCnpj]: safeStr(empresa?.CNPJ || empresa?.EmpresaCNPJ || cnpjDigitado || "").trim() || null,
    };
  }


  function definirValorCampoItemFormularioPorIndice(nomeCampo, valor, indice){
    const input = obterInputFormularioSolicitacao("Item", nomeCampo, indice);
    if (!input) return;

    const novoValor = valor == null ? "" : String(valor);
    if (input.value === novoValor) return;

    input.value = novoValor;

    const wrapCampo = input.closest(".kb-contrato-campo");
    if (wrapCampo && safeStr(novoValor || "").trim()) {
      wrapCampo.classList.remove("is-invalido");
    }

    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function obterRelacionamentosComerciaisHeaderParaItens(){
    const dadosAgencia = obterAgenciaHeaderFormularioSelecionada();
    const dadosBureau = obterEmpresaHeaderFormularioSelecionada("Bureau");

    return {
      Agencia: dadosAgencia.Agencia || null,
      CnpjAgencia: dadosAgencia.CnpjAgencia || null,
      Bureau: dadosBureau.Bureau || null,
      CnpjBureau: dadosBureau.CnpjBureau || null,
    };
  }

  function sincronizarAgenciaBureauHeaderNosItensFormulario(){
    const dados = obterRelacionamentosComerciaisHeaderParaItens();

    for (let indice = 0; indice < quantidadeItensFormularioSolicitacaoAtual; indice += 1) {
      definirValorCampoItemFormularioPorIndice("Agencia", dados.Agencia, indice);
      definirValorCampoItemFormularioPorIndice("CnpjAgencia", dados.CnpjAgencia, indice);
      definirValorCampoItemFormularioPorIndice("Bureau", dados.Bureau, indice);
      definirValorCampoItemFormularioPorIndice("CnpjBureau", dados.CnpjBureau, indice);
    }
  }

  function criarCampoFormularioSolicitacao(secao, meta, indice = null){
    const wrap = document.createElement("div");
    wrap.className = "kb-contrato-campo" + (meta.span === 2 ? " span-2" : "");

    if (meta.obrigatorio) {
      wrap.classList.add("is-obrigatorio");
    }

    const label = document.createElement("label");
    label.className = "kb-contrato-label";
    label.setAttribute("for", idCampoSolicitacao(secao, meta.nome, indice));
    label.textContent = meta.label;
    wrap.appendChild(label);

    const deveCriarSelectTipoDocumento = meta.tipo === "tipo_documento" || (secao === "Header" && meta.nome === "TipoDocumento");
    const deveCriarComboboxAgenciaHeader = secao === "Header" && meta.nome === "Agencia";
    const deveCriarComboboxEmpresaHeader = secao === "Header" && campoHeaderEmpresaEhCombobox(meta.nome);

    let campo;
    let elementoRenderizado = null;
    let refsComboboxAgencia = null;
    let refsComboboxEmpresaHeader = null;

    if (meta.tipo === "textarea") {
      campo = document.createElement("textarea");
      campo.rows = 3;
      campo.className = "kb-textarea";
    } else if (deveCriarSelectTipoDocumento) {
      campo = document.createElement("select");
      campo.className = "kb-select";
      campo.dataset.tipoCampoSolicitacao = "tipo_documento";
      preencherOpcoesSelectTipoDocumento(campo);
    } else if (deveCriarComboboxAgenciaHeader) {
      const combo = document.createElement("div");
      combo.className = "kb-combobox grow";
      combo.dataset.role = "combo-agencia-header";

      const inputBusca = document.createElement("input");
      inputBusca.className = "kb-input kb-combobox-input";
      inputBusca.type = "text";
      inputBusca.autocomplete = "off";
      inputBusca.spellcheck = false;
      inputBusca.placeholder = "Digite razão social ou CNPJ da agência...";
      inputBusca.dataset.role = "input-agencia-header-busca";

      const btnToggle = document.createElement("button");
      btnToggle.className = "kb-combobox-toggle";
      btnToggle.type = "button";
      btnToggle.setAttribute("aria-label", "Abrir lista de agências");
      btnToggle.textContent = "▾";

      const lista = document.createElement("div");
      lista.className = "kb-combobox-lista";
      lista.dataset.role = "lista-agencia-header-busca";
      lista.hidden = true;

      campo = document.createElement("select");
      campo.className = "kb-select grow kb-select-hidden";
      campo.tabIndex = -1;
      campo.setAttribute("aria-hidden", "true");
      campo.appendChild(el("option", { value: "" }, ["— Selecione uma agência —"]));

      combo.appendChild(inputBusca);
      combo.appendChild(btnToggle);
      combo.appendChild(lista);
      combo.appendChild(campo);
      elementoRenderizado = combo;
      refsComboboxAgencia = { combo, inputBusca, btnToggle, lista, selectEl: campo };
    } else if (deveCriarComboboxEmpresaHeader) {
      const cfgEmpresaHeader = obterConfigEmpresaHeaderFormulario(meta.nome);
      const combo = document.createElement("div");
      combo.className = "kb-combobox grow";
      combo.dataset.role = `combo-${cfgEmpresaHeader.dataRole}`;

      const inputBusca = document.createElement("input");
      inputBusca.className = "kb-input kb-combobox-input";
      inputBusca.type = "text";
      inputBusca.autocomplete = "off";
      inputBusca.spellcheck = false;
      inputBusca.placeholder = cfgEmpresaHeader.placeholder;
      inputBusca.dataset.role = `input-${cfgEmpresaHeader.dataRole}-busca`;

      const btnToggle = document.createElement("button");
      btnToggle.className = "kb-combobox-toggle";
      btnToggle.type = "button";
      btnToggle.setAttribute("aria-label", cfgEmpresaHeader.aria);
      btnToggle.textContent = "▾";

      const lista = document.createElement("div");
      lista.className = "kb-combobox-lista";
      lista.dataset.role = `lista-${cfgEmpresaHeader.dataRole}-busca`;
      lista.hidden = true;

      campo = document.createElement("select");
      campo.className = "kb-select grow kb-select-hidden";
      campo.tabIndex = -1;
      campo.setAttribute("aria-hidden", "true");
      campo.appendChild(el("option", { value: "" }, [`— Selecione ${cfgEmpresaHeader.rotulo.toLowerCase()} —`]));

      combo.appendChild(inputBusca);
      combo.appendChild(btnToggle);
      combo.appendChild(lista);
      combo.appendChild(campo);
      elementoRenderizado = combo;
      refsComboboxEmpresaHeader = { nomeCampo: meta.nome, combo, inputBusca, btnToggle, lista, selectEl: campo };
    } else {
      campo = document.createElement("input");
      campo.type = meta.tipo === "date" ? "date" : "text";
      campo.className = "kb-input";
    }

    campo.id = idCampoSolicitacao(secao, meta.nome, indice);
    campo.dataset.campoSolicitacao = meta.nome;
    campo.dataset.secaoSolicitacao = secao;

    if (deveCriarComboboxAgenciaHeader && refsComboboxAgencia?.inputBusca) {
      refsComboboxAgencia.inputBusca.id = `${campo.id}Busca`;
    }

    if (meta.obrigatorio) {
      campo.required = true;
      campo.setAttribute("aria-required", "true");
      campo.dataset.obrigatorioSolicitacao = "1";
      campo.dataset.labelObrigatorioSolicitacao = meta.label;
    }

    if (indice !== null && indice !== undefined) {
      campo.dataset.indiceSolicitacao = String(indice);
    }

    if (
      meta.somenteLeitura
      || (secao === "Item" && meta.nome === "TexmpoExposicao")
      || (secao === "Header" && ["CnpjAgencia", "CnpjBureau", "CnpjIntermediario"].includes(meta.nome))
    ) {
      campo.readOnly = true;
    }

    const limparErroCampo = () => {
      const wrapCampo = campo.closest(".kb-contrato-campo");
      if (wrapCampo && safeStr(campo.value || "").trim()) {
        wrapCampo.classList.remove("is-invalido");
      }
    };

    campo.addEventListener("input", limparErroCampo);
    campo.addEventListener("change", limparErroCampo);

    if (secao === "Item" && (meta.nome === "DataInicioPrevisto" || meta.nome === "DataTerminoPrevisto")) {
      const recalcularTempo = () => atualizarTempoExposicaoFormularioSolicitacao(indice);
      campo.addEventListener("input", recalcularTempo);
      campo.addEventListener("change", recalcularTempo);
    }

    if (refsComboboxAgencia) {
      configurarComboboxAgenciaFormularioSolicitacao(
        refsComboboxAgencia.combo,
        refsComboboxAgencia.inputBusca,
        refsComboboxAgencia.btnToggle,
        refsComboboxAgencia.lista,
        refsComboboxAgencia.selectEl
      );
    }

    if (refsComboboxEmpresaHeader) {
      configurarComboboxEmpresaHeaderFormularioSolicitacao(
        refsComboboxEmpresaHeader.nomeCampo,
        refsComboboxEmpresaHeader.combo,
        refsComboboxEmpresaHeader.inputBusca,
        refsComboboxEmpresaHeader.btnToggle,
        refsComboboxEmpresaHeader.lista,
        refsComboboxEmpresaHeader.selectEl
      );
    }

    wrap.appendChild(elementoRenderizado || campo);

    if (meta.obrigatorio) {
      const mensagemErro = document.createElement("small");
      mensagemErro.className = "kb-campo-obrigatorio-erro";
      mensagemErro.textContent = `${meta.label} é obrigatório.`;
      wrap.appendChild(mensagemErro);
    }

    return wrap;
  }

  function renderizarFormularioSolicitacaoContrato(quantidadeItens = 1){
    quantidadeItensFormularioSolicitacaoAtual = Math.max(1, idNum(quantidadeItens || 0) || 1);

    if (formSolicitacaoContratoHeader) {
      formSolicitacaoContratoHeader.innerHTML = "";
      CAMPOS_SOLICITACAO_HEADER.forEach((meta) => {
        formSolicitacaoContratoHeader.appendChild(criarCampoFormularioSolicitacao("Header", meta));
      });
    }

    if (formSolicitacaoContratoItem) {
      formSolicitacaoContratoItem.innerHTML = "";
      for (let indice = 0; indice < quantidadeItensFormularioSolicitacaoAtual; indice += 1) {
        const secao = document.createElement("div");
        secao.className = "kb-contrato-item-secao";
        secao.dataset.indiceSolicitacaoItem = String(indice);

        const titulo = document.createElement("div");
        titulo.className = "kb-section-title";
        titulo.style.marginTop = indice === 0 ? "0" : "10px";
        titulo.textContent = quantidadeItensFormularioSolicitacaoAtual > 1 ? `Item do contrato ${indice + 1}` : "Item do contrato";
        secao.appendChild(titulo);

        const grid = document.createElement("div");
        grid.className = "kb-cadastro-empresa-grid kb-solicitacao-grid";
        CAMPOS_SOLICITACAO_ITEM.forEach((meta) => {
          grid.appendChild(criarCampoFormularioSolicitacao("Item", meta, indice));
        });

        secao.appendChild(grid);
        formSolicitacaoContratoItem.appendChild(secao);
      }
    }

    renderizarFormularioContatoClienteDireto();
    substituirInputsTipoDocumentoLegadosPorSelect();
    aplicarTipoDocumentoAditivoPadraoNosItensFormulario();
    aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente();
  }

  function obterInputFormularioSolicitacao(secao, nomeCampo, indice = null){
    return document.getElementById(idCampoSolicitacao(secao, nomeCampo, indice));
  }

  function sincronizarMarcaExibidaHeaderComInputTopo(){
    const inputMarcaHeader = obterInputFormularioSolicitacao("Header", "MarcaExibida");
    if (!inputMarcaHeader || !inputMarcaCard) return;

    inputMarcaHeader.value = inputMarcaCard.value || "";

    const wrapCampo = inputMarcaHeader.closest(".kb-contrato-campo");
    if (wrapCampo && safeStr(inputMarcaHeader.value || "").trim()) {
      wrapCampo.classList.remove("is-invalido");
    }
  }

  function obterTipoDocumentoSelecionadoNoHeaderSolicitacao(){
    const selectTipoDocumento = obterInputFormularioSolicitacao("Header", "TipoDocumento");
    const idTipoDocumento = idNum(selectTipoDocumento?.value || 0) || null;
    const nomeTipoDocumento = obterNomeTipoDocumentoPorId(idTipoDocumento) || null;

    return {
      IDDimTipoDocumento: idTipoDocumento,
      TipoDocumento: nomeTipoDocumento,
    };
  }

  function setValorFormularioSolicitacao(secao, meta, valor, indice = null, registro = {}){
    const input = obterInputFormularioSolicitacao(secao, meta.nome, indice);
    if (!input) return;

    if (secao === "Header" && meta.nome === "Agencia") {
      setValorAgenciaHeaderFormulario(valor, registro || {});
      return;
    }

    if (secao === "Header" && campoHeaderEmpresaEhCombobox(meta.nome)) {
      setValorEmpresaHeaderFormulario(meta.nome, valor, registro || {});
      return;
    }

    if (meta.tipo === "date") {
      input.value = normalizarDataParaInput(valor);
      return;
    }

    if (meta.tipo === "tipo_documento" || (secao === "Header" && meta.nome === "TipoDocumento")) {
      if (input.tagName !== "SELECT") {
        substituirInputsTipoDocumentoLegadosPorSelect();
      }

      const selectTipoDocumento = obterInputFormularioSolicitacao(secao, meta.nome, indice);
      if (!selectTipoDocumento || selectTipoDocumento.tagName !== "SELECT") return;

      preencherOpcoesSelectTipoDocumento(selectTipoDocumento);
      const idTipoDocumento = resolverIdTipoDocumentoFormulario(registro, valor);
      selectTipoDocumento.value = idTipoDocumento ? String(idTipoDocumento) : "";
      if (!selectTipoDocumento.value && obterFluxoContratoAtual().modo_contrato === VALOR_MODO_CONTRATO_ADITIVO) {
        const idAditivo = obterIdTipoDocumentoAditivoDisponivel();
        selectTipoDocumento.value = idAditivo ? String(idAditivo) : "";
      }
      return;
    }

    input.value = valor == null ? "" : String(valor);
  }

  function renderizarFormularioContatoClienteDireto(){
    if (formContatoClienteDiretoAssinatura && formContatoClienteDiretoAssinatura.dataset.renderizado !== "1") {
      formContatoClienteDiretoAssinatura.innerHTML = "";
      CAMPOS_CONTATO_CLIENTE_DIRETO_ASSINATURA.forEach((meta) => {
        formContatoClienteDiretoAssinatura.appendChild(criarCampoFormularioSolicitacao("ContatoClienteDireto", meta));
      });
      formContatoClienteDiretoAssinatura.dataset.renderizado = "1";
    }

    if (formContatoClienteDiretoFinanceiro && formContatoClienteDiretoFinanceiro.dataset.renderizado !== "1") {
      formContatoClienteDiretoFinanceiro.innerHTML = "";
      CAMPOS_CONTATO_CLIENTE_DIRETO_FINANCEIRO.forEach((meta) => {
        formContatoClienteDiretoFinanceiro.appendChild(criarCampoFormularioSolicitacao("ContatoClienteDireto", meta));
      });
      formContatoClienteDiretoFinanceiro.dataset.renderizado = "1";
    }
  }

  function campoContatoClienteDiretoTemConteudo(meta){
    const input = obterInputFormularioSolicitacao("ContatoClienteDireto", meta.nome);
    return campoFormularioSolicitacaoTemConteudo(input);
  }

  function grupoContatoClienteDiretoTemConteudo(camposGrupo){
    return (camposGrupo || []).some((meta) => campoContatoClienteDiretoTemConteudo(meta));
  }

  function definirVisibilidadeGrupoContatoClienteDireto(opcoes){
    const wrapGrupo = opcoes?.wrapGrupo || null;
    const tituloGrupo = opcoes?.tituloGrupo || null;
    const formGrupo = opcoes?.formGrupo || null;
    const camposGrupo = opcoes?.camposGrupo || [];
    const deveExibirGrupo = Boolean(opcoes?.deveExibirGrupo);
    const estaNaFaseQuatro = Boolean(opcoes?.estaNaFaseQuatro);
    const ehClienteDireto = Boolean(opcoes?.ehClienteDireto);

    if (wrapGrupo) {
      wrapGrupo.hidden = !deveExibirGrupo;
      wrapGrupo.style.display = deveExibirGrupo ? "" : "none";
    }

    if (tituloGrupo) {
      tituloGrupo.hidden = !deveExibirGrupo;
      tituloGrupo.style.display = deveExibirGrupo ? "" : "none";
    }

    if (formGrupo) {
      formGrupo.hidden = !deveExibirGrupo;
      formGrupo.style.display = deveExibirGrupo ? "" : "none";
    }

    camposGrupo.forEach((meta) => {
      const input = obterInputFormularioSolicitacao("ContatoClienteDireto", meta.nome);
      if (!input) return;

      const temValor = campoFormularioSolicitacaoTemConteudo(input);
      const deveExibirCampo = Boolean(deveExibirGrupo && (estaNaFaseQuatro || temValor));
      const wrapCampo = input.closest(".kb-contrato-campo");

      input.required = Boolean(ehClienteDireto && estaNaFaseQuatro && meta.obrigatorio);
      input.disabled = !ehClienteDireto;

      if (wrapCampo) {
        wrapCampo.hidden = !deveExibirCampo;
        wrapCampo.style.display = deveExibirCampo ? "" : "none";

        if (!deveExibirCampo) {
          wrapCampo.classList.remove("is-invalido");
        }
      }
    });
  }

  function aplicarVisibilidadeFormularioContatoClienteDireto(){
    renderizarFormularioContatoClienteDireto();

    const ehClienteDireto = obterIdTipoClienteAtualFormularioSolicitacao() === ID_TIPO_CLIENTE_DIRETO;
    const estaNaFaseQuatro = modalCardEstaNaFaseQuatro();

    const assinaturaTemConteudo = grupoContatoClienteDiretoTemConteudo(CAMPOS_CONTATO_CLIENTE_DIRETO_ASSINATURA);
    const financeiroTemConteudo = grupoContatoClienteDiretoTemConteudo(CAMPOS_CONTATO_CLIENTE_DIRETO_FINANCEIRO);

    const deveExibirAssinatura = Boolean(ehClienteDireto && (estaNaFaseQuatro || assinaturaTemConteudo));
    const deveExibirFinanceiro = Boolean(ehClienteDireto && (estaNaFaseQuatro || financeiroTemConteudo));
    const deveExibirContainer = deveExibirAssinatura || deveExibirFinanceiro;

    if (wrapContatoClienteDiretoFormulario) {
      wrapContatoClienteDiretoFormulario.hidden = !deveExibirContainer;
      wrapContatoClienteDiretoFormulario.style.display = deveExibirContainer ? "" : "none";
    }

    definirVisibilidadeGrupoContatoClienteDireto({
      wrapGrupo: wrapContatoClienteDiretoAssinatura,
      tituloGrupo: tituloContatoClienteDiretoAssinatura,
      formGrupo: formContatoClienteDiretoAssinatura,
      camposGrupo: CAMPOS_CONTATO_CLIENTE_DIRETO_ASSINATURA,
      deveExibirGrupo: deveExibirAssinatura,
      estaNaFaseQuatro,
      ehClienteDireto,
    });

    definirVisibilidadeGrupoContatoClienteDireto({
      wrapGrupo: wrapContatoClienteDiretoFinanceiro,
      tituloGrupo: tituloContatoClienteDiretoFinanceiro,
      formGrupo: formContatoClienteDiretoFinanceiro,
      camposGrupo: CAMPOS_CONTATO_CLIENTE_DIRETO_FINANCEIRO,
      deveExibirGrupo: deveExibirFinanceiro,
      estaNaFaseQuatro,
      ehClienteDireto,
    });
  }

  function setValorFormularioContatoClienteDireto(meta, valor){
    const input = obterInputFormularioSolicitacao("ContatoClienteDireto", meta.nome);
    if (!input) return;
    input.value = valor == null ? "" : String(valor);
  }

  function preencherFormularioContatoClienteDireto(dadosContato){
    renderizarFormularioContatoClienteDireto();
    const contato = dadosContato && typeof dadosContato === "object" ? dadosContato : {};
    [...CAMPOS_CONTATO_CLIENTE_DIRETO_ASSINATURA, ...CAMPOS_CONTATO_CLIENTE_DIRETO_FINANCEIRO].forEach((meta) => {
      setValorFormularioContatoClienteDireto(meta, contato?.[meta.nome] ?? null);
    });
    aplicarVisibilidadeFormularioContatoClienteDireto();
  }

  function coletarFormularioContatoClienteDireto(){
    renderizarFormularioContatoClienteDireto();

    if (obterIdTipoClienteAtualFormularioSolicitacao() !== ID_TIPO_CLIENTE_DIRETO) {
      return null;
    }

    const contato = {};
    let temAlgumValor = false;

    [...CAMPOS_CONTATO_CLIENTE_DIRETO_ASSINATURA, ...CAMPOS_CONTATO_CLIENTE_DIRETO_FINANCEIRO].forEach((meta) => {
      const input = obterInputFormularioSolicitacao("ContatoClienteDireto", meta.nome);
      const valor = safeStr(input?.value || "").trim();
      contato[meta.nome] = valor || null;
      if (valor) temAlgumValor = true;
    });

    if (!modalCardEstaNaFaseQuatro() && !temAlgumValor) {
      return null;
    }

    return contato;
  }

  function obterIdTipoClienteAtualFormularioSolicitacao(){
    const valorSelect = idNum(selectTipoClienteDescontoCard?.value || 0);
    if (valorSelect > 0) return valorSelect;

    const card = obterCardPorId(cardAbertoId) || {};
    return idNum(card.IDDimTipoCliente ?? card.IDDimKanbanTipoClienteDesconto ?? 0);
  }

  function campoFormularioSolicitacaoOcultoPorTipoCliente(nomeCampo, idTipoCliente, secao = ""){
    const nome = safeStr(nomeCampo || "").trim();
    const idTipo = idNum(idTipoCliente || 0);
    const secaoNormalizada = safeStr(secao || "").trim();

    const camposPercentuaisCabecalho = new Set([
      "PercentualAgencia",
      "PercentualBureau",
      "PercentualIntermediario",
      "PercentualCartaAcordo"
    ]);

    if (camposPercentuaisCabecalho.has(nome)) {
      if (secaoNormalizada !== "Header") return true;

      if (nome === "PercentualAgencia" || nome === "PercentualCartaAcordo") {
        return !(idTipo === 1 || idTipo === 3);
      }

      if (nome === "PercentualBureau" || nome === "PercentualIntermediario") {
        return idTipo !== 1;
      }
    }

    const camposIntermediacao = new Set([
      "Agencia",
      "CnpjAgencia",
      "Bureau",
      "CnpjBureau",
      "Intermediario",
      "CnpjIntermediario",
      "ValorMensalAgencia",
      "PercentualBureau",
      "PercentualIntermediario",
      "ValorBureauMensal"
    ]);

    /*
     * Regra do formulário da solicitação, não dos selects de painel/face:
     * - IDDimTipoCliente = 2 (Cliente Direto): não exibe Agência, Bureau, Intermediário, CNPJs relacionados
     *   nem os campos comerciais de comissão/valor de agência e bureau.
     * - IDDimTipoCliente = 3 (Agência de Publicidade): a agência é a empresa principal do card.
     *   Portanto não exibo Bureau/CNPJ Bureau no cabeçalho nem nos itens.
     * - IDDimTipoCliente = 4 (Bureau): o bureau é a empresa principal do card.
     *   Portanto não exibo Agência/CNPJ Agência no cabeçalho nem nos itens.
     *
     * Importante: esta regra não mexe em DataInicioPrevisto/DataTerminoPrevisto,
     * CodPonto, CodFace, painel/face ou nas opções dos selects de contrato/aditivo.
     */
    if (idTipo === ID_TIPO_CLIENTE_DIRETO) {
      return camposIntermediacao.has(nome);
    }

    if (idTipo === 3) {
      return nome === "Agencia"
        || nome === "Bureau"
        || nome === "CnpjBureau"
        || nome === "PercentualBureau"
        || nome === "ValorBureauMensal";
    }

    if (idTipo === 4) {
      return nome === "Agencia" || nome === "CnpjAgencia";
    }

    if (idTipo === 1) {
      return nome === "ValorMensalAgencia" || nome === "ValorBureauMensal";
    }

    return false;
  }

  function aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente(){
    const idTipoCliente = obterIdTipoClienteAtualFormularioSolicitacao();
    const campos = [
      ...(formSolicitacaoContratoHeader?.querySelectorAll("[data-campo-solicitacao]") || []),
      ...(formSolicitacaoContratoItem?.querySelectorAll("[data-campo-solicitacao]") || []),
    ];

    campos.forEach((campo) => {
      const nomeCampo = campo.dataset.campoSolicitacao || "";
      const secaoCampo = campo.dataset.secaoSolicitacao || "";
      const deveOcultar = campoFormularioSolicitacaoOcultoPorTipoCliente(nomeCampo, idTipoCliente, secaoCampo);
      const wrapCampo = campo.closest(".kb-contrato-campo");

      if (wrapCampo) {
        wrapCampo.hidden = deveOcultar;
        wrapCampo.style.display = deveOcultar ? "none" : "";
      }

      if (deveOcultar) {
        campo.value = "";

        if (nomeCampo === "Agencia") {
          const comboAgenciaHeader = campo.closest?.(".kb-combobox") || null;
          const listaAgenciaHeader = comboAgenciaHeader?.querySelector('[data-role="lista-agencia-header-busca"]') || null;
          fecharListaAgenciaFormulario(comboAgenciaHeader, listaAgenciaHeader);
        }

        if (campoHeaderEmpresaEhCombobox(nomeCampo)) {
          const cfgEmpresaHeader = obterConfigEmpresaHeaderFormulario(nomeCampo);
          const comboEmpresaHeader = campo.closest?.(".kb-combobox") || null;
          const listaEmpresaHeader = comboEmpresaHeader?.querySelector(`[data-role="lista-${cfgEmpresaHeader.dataRole}-busca"]`) || null;
          fecharListaEmpresaHeaderFormulario(comboEmpresaHeader, listaEmpresaHeader);
          definirCnpjEmpresaHeaderFormulario(nomeCampo, "");
        }
      }
    });

    sincronizarCnpjAgenciaHeaderComAgenciaEfetiva();
    aplicarVisibilidadeFormularioContatoClienteDireto();
  }


  function validarCamposObrigatoriosFormularioSolicitacaoContrato(){
    if (!modalCardEstaNaFaseQuatro()) {
      return { ok: true, msg: "" };
    }

    const container = wrapFormularioSolicitacaoContrato;
    if (!container || container.hidden || container.style.display === "none") {
      return { ok: true, msg: "" };
    }

    const camposObrigatorios = [
      ...container.querySelectorAll('[data-obrigatorio-solicitacao="1"]')
    ];

    const camposInvalidos = [];

    camposObrigatorios.forEach((campo) => {
      const wrapCampo = campo.closest(".kb-contrato-campo");

      if (!wrapCampo || wrapCampo.hidden || wrapCampo.style.display === "none") {
        return;
      }

      if (campo.closest("[hidden]")) {
        return;
      }

      if (campo.disabled) {
        return;
      }

      wrapCampo.classList.remove("is-invalido");

      const valor = safeStr(campo.value || "").trim();
      if (valor) {
        return;
      }

      wrapCampo.classList.add("is-invalido");
      camposInvalidos.push({
        campo,
        label: safeStr(campo.dataset.labelObrigatorioSolicitacao || campo.dataset.campoSolicitacao || "Campo").trim()
      });
    });

    if (!camposInvalidos.length) {
      return { ok: true, msg: "" };
    }

    const primeiroCampo = camposInvalidos[0].campo;
    primeiroCampo.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => primeiroCampo.focus({ preventScroll: true }), 250);

    const nomesCampos = camposInvalidos
      .slice(0, 6)
      .map((item) => item.label)
      .join(", ");

    const complemento = camposInvalidos.length > 6
      ? ` e mais ${camposInvalidos.length - 6} campo(s)`
      : "";

    return {
      ok: false,
      msg: `Preencha os campos obrigatórios do formulário do contrato: ${nomesCampos}${complemento}.`
    };
  }

  function obterTextoSelectSelecionado(selectEl){
    const option = selectEl?.selectedOptions?.[0] || null;
    return safeStr(option?.textContent || "").trim();
  }

  function obterEmpresaSelecionadaDoCampo(selectEl){
    const idEmpresa = safeStr(selectEl?.value || "").trim();
    return idEmpresa ? obterEmpresaCatalogoPorId(idEmpresa) : null;
  }

  function obterOrigemAtendimentoSelecionadaTexto(){
    return obterTextoSelectSelecionado(selectOrigemAtendimentoCard) || null;
  }

  function obterValorResumoPainelFace(bloco, seletor){
    return parseNumeroInput(bloco?.querySelector(seletor)?.textContent || null);
  }

  function obterExibicoesDiaSelecionadasDoBloco(bloco){
    const exibicaoAtiva = obterExibicaoDiaAtivaDoBloco(bloco);
    if (exibicaoAtiva) return [exibicaoAtiva];

    const selectExibicoesDia = bloco?.querySelector('[data-role="select-exibicoes-dia"]');
    const selecionadas = obterValoresSelecionadosSelect(selectExibicoesDia)
      .map((valor) => safeStr(valor || "").trim())
      .filter(Boolean);

    const unicas = Array.from(new Set(selecionadas));
    return unicas.length ? unicas : [""];
  }

  function calcularDadosComerciaisPainelFacePorExibicao(bloco, exibicoesDia = "", periodoPreferido = ""){
    const exibicoesTexto = safeStr(exibicoesDia || "").trim();
    const periodoAtual = safeStr(
      periodoPreferido ||
      bloco?.querySelector('[data-role="select-periodo-exibicao"]')?.value ||
      ""
    ).trim();

    const precoPorExibicao = exibicoesTexto
      ? (
          localizarPrecoPorFiltrosDoBloco(bloco, {
            exibicoes_dia: exibicoesTexto,
            periodo_exibicao: periodoAtual,
          }) || localizarPrecoPorFiltrosDoBloco(bloco, {
            exibicoes_dia: exibicoesTexto,
          })
        )
      : null;

    const precoSelecionado = precoPorExibicao || obterPrecoSelecionadoDoBloco(bloco);
    const valorTabela = obterValorPrecoTabela(precoSelecionado);
    const novoValorInformado = parseNumeroInput(bloco?.querySelector('[data-role="input-novo-valor"]')?.value);
    const percentualInformado = parseNumeroInput(bloco?.querySelector('[data-role="input-percentual"]')?.value);
    const origemEdicao = bloco?.__origemEdicaoComercial || (
      novoValorInformado !== null
        ? "novo_valor"
        : (percentualInformado !== null ? "percentual" : null)
    );

    let valorFinal = valorTabela;

    if (origemEdicao === "novo_valor" && novoValorInformado !== null) {
      valorFinal = novoValorInformado;
    } else if (origemEdicao === "percentual" && percentualInformado !== null) {
      valorFinal = calcularNovoValorPorPercentual(valorTabela, percentualInformado);
    } else if (novoValorInformado !== null) {
      valorFinal = novoValorInformado;
    } else if (percentualInformado !== null) {
      valorFinal = calcularNovoValorPorPercentual(valorTabela, percentualInformado);
    }

    const cotaExibicoesDia = exibicoesTexto || null;

    return {
      id_preco: idNum(precoSelecionado?.IDDimTabelaPrecosEuromidia || 0) || null,
      periodo_exibicao: safeStr(precoSelecionado?.PeriodoExibicao || periodoAtual || "").trim() || null,
      ExibicoesDia: cotaExibicoesDia,
      exibicoes_dia: cotaExibicoesDia,
      Cota: cotaExibicoesDia,
      cota: cotaExibicoesDia,
      valor_tabela: valorTabela,
      valor_venda_final: valorFinal,
      novo_valor: novoValorInformado,
      percentual_desconto: novoValorInformado !== null ? null : percentualInformado,
    };
  }

  function obterResumoPainelFaceDoBloco(bloco){
    if (!bloco) return null;

    const selectPainel = bloco.querySelector('[data-role="select-painel"]');
    const selectFace = bloco.querySelector('[data-role="select-face"]');
    const selectExibicoesDia = bloco.querySelector('[data-role="select-exibicoes-dia"]');
    const painelFace = obterPainelFaceSelecionadoDoBloco(bloco) || null;
    const idPainel = idNum(selectPainel?.value || painelFace?.IDDimPaineisEuromidia || 0) || null;
    const codPontoSelecionado = safeStr(painelFace?.CodPonto || "").trim();
    const painel =
      (idPainel ? paineisPorId.get(idPainel) : null) ||
      (codPontoSelecionado
        ? (Array.isArray(paineisCatalogo) ? paineisCatalogo : []).find((item) => {
            return safeStr(item?.CodPonto || "").trim() === codPontoSelecionado;
          }) || null
        : null);

    const exibicoesResumo = obterValoresSelecionadosSelect(selectExibicoesDia).join(', ') || safeStr(bloco.__dadosComerciais?.preco?.ExibicoesDia || "").trim() || null;

    return {
      id_painel: idPainel,
      id_face: idNum(painelFace?.IDDimFacesPaineis ?? 0) || null,
      cod_ponto: safeStr(painelFace?.CodPonto || painel?.CodPonto || "").trim() || null,
      cod_face: safeStr(selectFace?.value || painelFace?.CodFace || "").trim().toUpperCase() || null,
      tipo_painel: safeStr(painelFace?.Tipo || painel?.Tipo || "").trim() || null,
      cidade: safeStr(painel?.Cidade || painelFace?.Cidade || "").trim() || null,
      ExibicoesDia: exibicoesResumo,
      exibicoes_dia: exibicoesResumo,
      Cota: exibicoesResumo,
      cota: exibicoesResumo,
      valor_venda_final: obterValorResumoPainelFace(bloco, '[data-role="kpi-valor-final"]'),
      data_inicio: normalizarDataParaInput(bloco.querySelector('[data-role="input-data-inicio"]')?.value || "") || null,
      data_fim: normalizarDataParaInput(bloco.querySelector('[data-role="input-data-fim"]')?.value || "") || null,
    };
  }

  function listarResumosPainelFaceDoFormulario(){
    const resumos = [];

    for (const bloco of [...(painelFaceLista?.querySelectorAll('.kb-painel-item') || [])]) {
      const resumoBase = obterResumoPainelFaceDoBloco(bloco);
      if (!resumoBase) continue;

      const periodoAtual = safeStr(bloco.querySelector('[data-role="select-periodo-exibicao"]')?.value || "").trim();
      const exibicoesParaGerar = obterExibicoesDiaSelecionadasDoBloco(bloco);

      exibicoesParaGerar.forEach((exibicoesDia) => {
        const dadosComerciais = calcularDadosComerciaisPainelFacePorExibicao(bloco, exibicoesDia, periodoAtual);
        resumos.push({
          ...resumoBase,
          ...dadosComerciais,
          exibicoes_dia: dadosComerciais.exibicoes_dia || resumoBase.exibicoes_dia || null,
          valor_venda_final: dadosComerciais.valor_venda_final,
        });
      });
    }

    return resumos.filter(Boolean);
  }

  function coletarHeaderFormularioSolicitacaoExistente(){
    const header = {};
    const idTipoCliente = obterIdTipoClienteAtualFormularioSolicitacao();

    CAMPOS_SOLICITACAO_HEADER.forEach((meta) => {
      const input = obterInputFormularioSolicitacao("Header", meta.nome);
      if (!input) return;

      if (meta.nome === "Agencia") {
        const dadosAgencia = obterAgenciaHeaderFormularioSelecionada();
        header.IDEmpresaAgencia = dadosAgencia.IDEmpresaAgencia;
        header.Agencia = dadosAgencia.Agencia;
        header.CnpjAgencia = dadosAgencia.CnpjAgencia || header.CnpjAgencia || null;
        return;
      }

      if (campoHeaderEmpresaEhCombobox(meta.nome)) {
        if (campoFormularioSolicitacaoOcultoPorTipoCliente(meta.nome, idTipoCliente, "Header")) {
          const cfgEmpresaHeader = obterConfigEmpresaHeaderFormulario(meta.nome);
          header[cfgEmpresaHeader.nomeId] = null;
          header[cfgEmpresaHeader.nomeCampo] = null;
          header[cfgEmpresaHeader.campoCnpj] = null;
          return;
        }

        Object.assign(header, obterEmpresaHeaderFormularioSelecionada(meta.nome));
        return;
      }

      if (campoFormularioSolicitacaoOcultoPorTipoCliente(meta.nome, idTipoCliente, "Header")) {
        header[meta.nome] = null;
        return;
      }

      const valor = safeStr(input.value || "").trim();

      if (meta.tipo === "tipo_documento") {
        const idTipoDocumento = idNum(valor || 0) || null;
        const nomeTipoDocumento = obterNomeTipoDocumentoPorId(idTipoDocumento);
        header.IDDimTipoDocumento = idTipoDocumento;
        header[meta.nome] = nomeTipoDocumento || null;
        return;
      }

      header[meta.nome] = valor || null;
    });
    return header;
  }

  function coletarItensFormularioSolicitacaoExistentes(){
    const itens = [];
    const idTipoCliente = obterIdTipoClienteAtualFormularioSolicitacao();

    for (let indice = 0; indice < quantidadeItensFormularioSolicitacaoAtual; indice += 1) {
      const item = {};
      let possuiValor = false;
      CAMPOS_SOLICITACAO_ITEM.forEach((meta) => {
        const input = obterInputFormularioSolicitacao("Item", meta.nome, indice);
        if (!input) return;

        if (campoFormularioSolicitacaoOcultoPorTipoCliente(meta.nome, idTipoCliente, "Item")) {
          item[meta.nome] = null;
          return;
        }

        const valor = safeStr(input.value || "").trim();

        if (meta.tipo === "tipo_documento") {
          const idTipoDocumento = idNum(valor || 0) || null;
          const nomeTipoDocumento = obterNomeTipoDocumentoPorId(idTipoDocumento);
          item.IDDimTipoDocumento = idTipoDocumento;
          item.TipoDocumento = nomeTipoDocumento || null;
          if (idTipoDocumento) possuiValor = true;
          return;
        }

        item[meta.nome] = valor || null;
        if (valor) possuiValor = true;
      });
      const tipoDocumentoHeader = obterTipoDocumentoSelecionadoNoHeaderSolicitacao();
      if (tipoDocumentoHeader.IDDimTipoDocumento) {
        item.IDDimTipoDocumento = tipoDocumentoHeader.IDDimTipoDocumento;
        item.TipoDocumento = tipoDocumentoHeader.TipoDocumento;
      }

      const relacionamentosHeader = obterRelacionamentosComerciaisHeaderParaItens();
      item.Agencia = relacionamentosHeader.Agencia;
      item.CnpjAgencia = relacionamentosHeader.CnpjAgencia;
      item.Bureau = relacionamentosHeader.Bureau;
      item.CnpjBureau = relacionamentosHeader.CnpjBureau;

      const percentualAgenciaHeader = obterValorPercentualAgenciaHeaderFormulario();
      const percentualBureauHeader = obterValorPercentualBureauHeaderFormulario();
      const percentualIntermediarioHeader = obterValorPercentualIntermediarioHeaderFormulario();
      const percentualCartaAcordoHeader = obterValorPercentualCartaAcordoHeaderFormulario();
      if (idTipoCliente === 1) {
        item.PercentualAgencia = percentualAgenciaHeader || null;
        item.PercentualBureau = percentualBureauHeader || null;
        item.PercentualIntermediario = percentualIntermediarioHeader || null;
        item.PercentualCartaAcordo = percentualCartaAcordoHeader || null;
      } else if (idTipoCliente === 3) {
        item.PercentualAgencia = percentualAgenciaHeader || null;
        item.PercentualBureau = null;
        item.PercentualIntermediario = null;
        item.PercentualCartaAcordo = percentualCartaAcordoHeader || null;
      } else {
        item.PercentualAgencia = null;
        item.PercentualBureau = null;
        item.PercentualIntermediario = null;
        item.PercentualCartaAcordo = null;
      }

      if (possuiValor || indice === 0) {
        itens.push(item);
      }
    }
    return itens;
  }

  function montarHeaderSolicitacaoBase(snapshotEditavel, card, vendedorLogado, valoresDigitadosHeader = {}){
    const headerSnapshot = snapshotEditavel && typeof snapshotEditavel.header === "object" ? snapshotEditavel.header : {};
    const empresaPrincipal = obterEmpresaSelecionadaDoCampo(selectEmpresaCard);
    const idTipoClienteAtual = obterIdTipoClienteAtualFormularioSolicitacao();
    const idEmpresaAgenciaFormulario = idNum(
      valoresDigitadosHeader?.IDEmpresaAgencia ??
      valoresDigitadosHeader?.id_empresa_agencia ??
      headerSnapshot?.IDEmpresaAgencia ??
      card?.IDEmpresaAgencia ??
      0
    );
    const empresaAgencia = idEmpresaAgenciaFormulario > 0
      ? obterEmpresaCatalogoPorId(idEmpresaAgenciaFormulario)
      : (idTipoClienteAtual === 3 ? empresaPrincipal : obterEmpresaSelecionadaDoCampo(selectAgenciaCard));
    const idEmpresaBureauFormulario = idNum(
      valoresDigitadosHeader?.IDEmpresaBureau ??
      valoresDigitadosHeader?.id_empresa_bureau ??
      headerSnapshot?.IDEmpresaBureau ??
      card?.IDEmpresaBureau ??
      0
    );
    const idEmpresaIntermediarioFormulario = idNum(
      valoresDigitadosHeader?.IDEmpresaIntermediario ??
      valoresDigitadosHeader?.id_empresa_intermediario ??
      headerSnapshot?.IDEmpresaIntermediario ??
      card?.IDEmpresaIntermediario ??
      0
    );
    const empresaBureau = idEmpresaBureauFormulario > 0
      ? obterEmpresaCatalogoPorId(idEmpresaBureauFormulario)
      : obterEmpresaSelecionadaDoCampo(selectBureauCard);
    const empresaIntermediario = idEmpresaIntermediarioFormulario > 0
      ? obterEmpresaCatalogoPorId(idEmpresaIntermediarioFormulario)
      : obterEmpresaSelecionadaDoCampo(selectIntermediarioCard);
    const origemTexto = obterOrigemAtendimentoSelecionadaTexto();
    const vendedorNome = safeStr(
      valoresDigitadosHeader?.Vendedor ||
      headerSnapshot?.Vendedor ||
      vendedorLogado?.NomeVendedor ||
      nomeVendedorDoCard(card) ||
      ""
    ).trim() || null;
    const marcaExibida = safeStr(
      valoresDigitadosHeader?.MarcaExibida ||
      inputMarcaCard?.value ||
      headerSnapshot?.MarcaExibida ||
      card?.Marca ||
      ""
    ).trim() || null;

    const header = Object.assign({}, headerSnapshot, valoresDigitadosHeader || {});

    header.CNPJ = empresaPrincipal?.CNPJ || null;
    header.RazaoSocial = empresaPrincipal?.RazaoSocial || null;
    header.IDEmpresaAgencia = empresaAgencia?.IDEmpresa || empresaAgencia?.ID || null;
    header.Agencia = empresaAgencia?.RazaoSocial || valoresDigitadosHeader?.Agencia || null;
    header.CnpjAgencia = empresaAgencia?.CNPJ || valoresDigitadosHeader?.CnpjAgencia || null;
    const percentualAgenciaHeader = safeStr(
      valoresDigitadosHeader?.PercentualAgencia ||
      valoresDigitadosHeader?.TotalPercentualAgencia ||
      headerSnapshot?.PercentualAgencia ||
      headerSnapshot?.TotalPercentualAgencia ||
      ""
    ).trim() || null;
    const percentualBureauHeader = safeStr(
      valoresDigitadosHeader?.PercentualBureau ||
      valoresDigitadosHeader?.TotalPercentualBureau ||
      headerSnapshot?.PercentualBureau ||
      headerSnapshot?.TotalPercentualBureau ||
      ""
    ).trim() || null;
    const percentualIntermediarioHeader = safeStr(
      valoresDigitadosHeader?.PercentualIntermediario ||
      valoresDigitadosHeader?.TotalPercentualIntermediario ||
      headerSnapshot?.PercentualIntermediario ||
      headerSnapshot?.TotalPercentualIntermediario ||
      ""
    ).trim() || null;
    const percentualCartaAcordoHeader = safeStr(
      valoresDigitadosHeader?.PercentualCartaAcordo ||
      valoresDigitadosHeader?.TotalPercentualCartaAcordo ||
      headerSnapshot?.PercentualCartaAcordo ||
      headerSnapshot?.TotalPercentualCartaAcordo ||
      ""
    ).trim() || null;

    header.PercentualAgencia = (idTipoClienteAtual === 1 || idTipoClienteAtual === 3) ? percentualAgenciaHeader : null;
    header.PercentualBureau = idTipoClienteAtual === 1 ? percentualBureauHeader : null;
    header.PercentualIntermediario = idTipoClienteAtual === 1 ? percentualIntermediarioHeader : null;
    header.PercentualCartaAcordo = (idTipoClienteAtual === 1 || idTipoClienteAtual === 3) ? percentualCartaAcordoHeader : null;
    header.TotalPercentualAgencia = header.PercentualAgencia;
    header.TotalPercentualBureau = header.PercentualBureau;
    header.TotalPercentualIntermediario = header.PercentualIntermediario;
    header.TotalPercentualCartaAcordo = header.PercentualCartaAcordo;
    header.IDEmpresaBureau = empresaBureau?.IDEmpresa || empresaBureau?.ID || idEmpresaBureauFormulario || null;
    header.Bureau = empresaBureau?.RazaoSocial || valoresDigitadosHeader?.Bureau || null;
    header.CnpjBureau = empresaBureau?.CNPJ || valoresDigitadosHeader?.CnpjBureau || null;
    header.IDEmpresaIntermediario = empresaIntermediario?.IDEmpresa || empresaIntermediario?.ID || idEmpresaIntermediarioFormulario || null;
    header.Intermediario = empresaIntermediario?.RazaoSocial || valoresDigitadosHeader?.Intermediario || null;
    header.CnpjIntermediario = empresaIntermediario?.CNPJ || valoresDigitadosHeader?.CnpjIntermediario || null;
    header.MarcaExibida = marcaExibida;

    if (origemTexto) {
      header.Origem = origemTexto;
    } else if (!selectOrigemAtendimentoCard?.value) {
      header.Origem = null;
    }

    if (vendedorNome) {
      header.Vendedor = vendedorNome;
    }

    return header;
  }

  function montarItemSolicitacaoBasePorIndice(indice, snapshotItem, card, vendedorLogado, valoresDigitadosItem = {}){
    const empresaPrincipal = obterEmpresaSelecionadaDoCampo(selectEmpresaCard);
    const idTipoClienteAtual = obterIdTipoClienteAtualFormularioSolicitacao();
    const dadosAgenciaHeader = obterAgenciaHeaderFormularioSelecionada();
    const dadosBureauHeader = obterEmpresaHeaderFormularioSelecionada("Bureau");
    const dadosIntermediarioHeader = obterEmpresaHeaderFormularioSelecionada("Intermediario");
    const empresaBureau = dadosBureauHeader.IDEmpresaBureau
      ? obterEmpresaCatalogoPorId(dadosBureauHeader.IDEmpresaBureau)
      : obterEmpresaSelecionadaDoCampo(selectBureauCard);
    const empresaIntermediario = dadosIntermediarioHeader.IDEmpresaIntermediario
      ? obterEmpresaCatalogoPorId(dadosIntermediarioHeader.IDEmpresaIntermediario)
      : obterEmpresaSelecionadaDoCampo(selectIntermediarioCard);
    const origemTexto = obterOrigemAtendimentoSelecionadaTexto();
    const resumosPainel = listarResumosPainelFaceDoFormulario();
    const resumoPainel = resumosPainel[indice] || null;
    const vendedorNome = safeStr(
      valoresDigitadosItem?.Vendedor ||
      snapshotItem?.Vendedor ||
      vendedorLogado?.NomeVendedor ||
      nomeVendedorDoCard(card) ||
      ""
    ).trim() || null;
    const marcaExibidaItem = safeStr(
      valoresDigitadosItem?.MarcaExibida ||
      inputMarcaCard?.value ||
      snapshotItem?.MarcaExibida ||
      card?.Marca ||
      ""
    ).trim() || null;

    const item = Object.assign({}, snapshotItem || {}, valoresDigitadosItem || {});

    const tipoDocumentoHeader = obterTipoDocumentoSelecionadoNoHeaderSolicitacao();
    if (tipoDocumentoHeader.IDDimTipoDocumento) {
      item.IDDimTipoDocumento = tipoDocumentoHeader.IDDimTipoDocumento;
      item.TipoDocumento = tipoDocumentoHeader.TipoDocumento || item.TipoDocumento || null;
    } else if (obterFluxoContratoAtual().modo_contrato === VALOR_MODO_CONTRATO_ADITIVO && !idNum(item.IDDimTipoDocumento || 0)) {
      const idAditivo = obterIdTipoDocumentoAditivoDisponivel();
      if (idAditivo) {
        item.IDDimTipoDocumento = idAditivo;
        item.TipoDocumento = obterNomeTipoDocumentoPorId(idAditivo) || item.TipoDocumento || "ADITIVO";
      }
    }

    item.CNPJ = empresaPrincipal?.CNPJ || null;
    item.RazaoSocial = empresaPrincipal?.RazaoSocial || null;
    item.Agencia = dadosAgenciaHeader.Agencia || null;
    item.CnpjAgencia = dadosAgenciaHeader.CnpjAgencia || null;
    const percentualAgenciaHeader = obterValorPercentualAgenciaHeaderFormulario();
    const percentualBureauHeader = obterValorPercentualBureauHeaderFormulario();
    const percentualIntermediarioHeader = obterValorPercentualIntermediarioHeaderFormulario();
    const percentualCartaAcordoHeader = obterValorPercentualCartaAcordoHeaderFormulario();

    if (idTipoClienteAtual === 1) {
      item.PercentualAgencia = percentualAgenciaHeader || item.PercentualAgencia || null;
      item.PercentualBureau = percentualBureauHeader || item.PercentualBureau || null;
      item.PercentualIntermediario = percentualIntermediarioHeader || item.PercentualIntermediario || null;
      item.PercentualCartaAcordo = percentualCartaAcordoHeader || item.PercentualCartaAcordo || null;
    } else if (idTipoClienteAtual === 3) {
      item.PercentualAgencia = percentualAgenciaHeader || item.PercentualAgencia || null;
      item.PercentualBureau = null;
      item.PercentualIntermediario = null;
      item.PercentualCartaAcordo = percentualCartaAcordoHeader || item.PercentualCartaAcordo || null;
    } else {
      item.PercentualAgencia = null;
      item.PercentualBureau = null;
      item.PercentualIntermediario = null;
      item.PercentualCartaAcordo = null;
    }

    item.Bureau = empresaBureau?.RazaoSocial || dadosBureauHeader.Bureau || null;
    item.CnpjBureau = empresaBureau?.CNPJ || dadosBureauHeader.CnpjBureau || null;
    item.Intermediario = empresaIntermediario?.RazaoSocial || dadosIntermediarioHeader.Intermediario || null;
    item.CnpjIntermediario = empresaIntermediario?.CNPJ || dadosIntermediarioHeader.CnpjIntermediario || null;

    if (origemTexto) {
      item.Origem = origemTexto;
    } else if (!selectOrigemAtendimentoCard?.value) {
      item.Origem = null;
    }

    if (vendedorNome) {
      item.Vendedor = vendedorNome;
    }

    if (marcaExibidaItem) {
      item.MarcaExibida = marcaExibidaItem;
    }

    if (resumoPainel) {
      item.IDPainelEuromidia = resumoPainel.id_painel || null;
      item.IDDimFacesPaineis = resumoPainel.id_face || null;
      item.CodPonto = resumoPainel.cod_ponto || null;
      item.CodFace = resumoPainel.cod_face || null;
      item.Tipo = resumoPainel.tipo_painel || null;
      item.CidadeExibicao = resumoPainel.cidade || null;
      item.Cota = resumoPainel.exibicoes_dia || null;
      item.DataInicioPrevisto = resumoPainel.data_inicio || null;
      item.DataTerminoPrevisto = resumoPainel.data_fim || null;
      item.TotalBrutoContrato = resumoPainel.valor_venda_final == null ? null : resumoPainel.valor_venda_final;

      if (!item.MarcaExibida && marcaExibidaItem) {
        item.MarcaExibida = marcaExibidaItem;
      }
    }

    return item;
  }

  function sincronizarFormularioSolicitacaoContratoComEstadoAtual(opcoes = {}){
    const snapshotEditavel = opcoes.snapshot !== undefined ? opcoes.snapshot : snapshotSolicitacaoEditavelAtual;
    const card = opcoes.card !== undefined ? opcoes.card : obterCardPorId(cardAbertoId);
    const vendedorLogado = opcoes.vendedor !== undefined ? opcoes.vendedor : vendedorLogadoSolicitacaoAtual;
    const preservarDigitado = opcoes.preservarDigitado !== false;

    const valoresDigitados = preservarDigitado
      ? {
          header: coletarHeaderFormularioSolicitacaoExistente(),
          itens: coletarItensFormularioSolicitacaoExistentes(),
          contatoClienteDireto: coletarFormularioContatoClienteDireto(),
        }
      : { header: {}, itens: [], contatoClienteDireto: null };

    const itensSnapshot = Array.isArray(snapshotEditavel?.itens)
      ? snapshotEditavel.itens
      : (snapshotEditavel?.item ? [snapshotEditavel.item] : []);

    const quantidadeItens = Math.max(
      1,
      itensSnapshot.length,
      valoresDigitados.itens.length,
      listarResumosPainelFaceDoFormulario().length,
    );

    renderizarFormularioSolicitacaoContrato(quantidadeItens);

    const header = montarHeaderSolicitacaoBase(snapshotEditavel, card, vendedorLogado, valoresDigitados.header || {});
    CAMPOS_SOLICITACAO_HEADER.forEach((meta) => {
      setValorFormularioSolicitacao("Header", meta, header?.[meta.nome] ?? null, null, header || {});
    });
    sincronizarMarcaExibidaHeaderComInputTopo();
    substituirInputsTipoDocumentoLegadosPorSelect();

    for (let indice = 0; indice < quantidadeItens; indice += 1) {
      const itemBase = montarItemSolicitacaoBasePorIndice(
        indice,
        itensSnapshot[indice] || (indice === 0 ? snapshotEditavel?.item || {} : {}),
        card,
        vendedorLogado,
        valoresDigitados.itens[indice] || {}
      );

      CAMPOS_SOLICITACAO_ITEM.forEach((meta) => {
        setValorFormularioSolicitacao("Item", meta, itemBase?.[meta.nome] ?? null, indice, itemBase || {});
      });

      atualizarTempoExposicaoFormularioSolicitacao(indice);
    }

    preencherFormularioContatoClienteDireto(
      valoresDigitados.contatoClienteDireto || snapshotEditavel?.contato_cliente_direto || snapshotEditavel?.contatoClienteDireto || null
    );
    sincronizarAgenciaBureauHeaderNosItensFormulario();
    aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente();
  }

  function limparFormularioSolicitacaoContrato(){
    renderizarFormularioSolicitacaoContrato(1);
    CAMPOS_SOLICITACAO_HEADER.forEach((meta) => setValorFormularioSolicitacao("Header", meta, ""));
    substituirInputsTipoDocumentoLegadosPorSelect();
    sincronizarMarcaExibidaHeaderComInputTopo();
    CAMPOS_SOLICITACAO_ITEM.forEach((meta) => setValorFormularioSolicitacao("Item", meta, "", 0));
    preencherFormularioContatoClienteDireto(null);
    aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente();
  }

  function preencherFormularioSolicitacaoContrato(snapshotEditavel, card, vendedorLogado){
    snapshotSolicitacaoEditavelAtual = snapshotEditavel && typeof snapshotEditavel === "object" ? snapshotEditavel : null;
    vendedorLogadoSolicitacaoAtual = vendedorLogado && typeof vendedorLogado === "object" ? vendedorLogado : null;
    sincronizarFormularioSolicitacaoContratoComEstadoAtual({
      snapshot: snapshotSolicitacaoEditavelAtual,
      card,
      vendedor: vendedorLogadoSolicitacaoAtual,
      preservarDigitado: false,
    });
  }

  function coletarFormularioSolicitacaoContrato(){
    const header = coletarHeaderFormularioSolicitacaoExistente();
    const itens = coletarItensFormularioSolicitacaoExistentes();
    const contatoClienteDireto = coletarFormularioContatoClienteDireto();
    return {
      header,
      item: itens[0] || {},
      itens,
      contato_cliente_direto: contatoClienteDireto,
      contatoClienteDireto
    };
  }

  function usuarioEstaDigitandoNoFormularioSolicitacao(){
    const ativo = document.activeElement;
    if (!ativo || typeof ativo.closest !== "function") return false;

    const dentroFormularioContrato = ativo.closest("#wrapFormularioSolicitacaoContrato");
    const dentroContatoClienteDireto = ativo.closest("#wrapContatoClienteDiretoFormulario");
    if (!dentroFormularioContrato && !dentroContatoClienteDireto) return false;

    if (ativo.disabled || ativo.readOnly) return false;

    const tag = safeStr(ativo.tagName || "").toUpperCase();
    if (tag === "TEXTAREA" || tag === "SELECT") return true;

    if (tag !== "INPUT") return false;

    const tipo = safeStr(ativo.getAttribute("type") || "text").toLowerCase();
    return !["button", "submit", "reset", "hidden", "checkbox", "radio", "file"].includes(tipo);
  }

  function agendarSincronizacaoFormularioSolicitacao(){
    if (timerSincronizacaoFormularioSolicitacao) {
      window.clearTimeout(timerSincronizacaoFormularioSolicitacao);
    }

    timerSincronizacaoFormularioSolicitacao = window.setTimeout(() => {
      timerSincronizacaoFormularioSolicitacao = null;
      if (!wrapFormularioSolicitacaoContrato) return;

      if (usuarioEstaDigitandoNoFormularioSolicitacao()) {
        return;
      }

      sincronizarFormularioSolicitacaoContratoComEstadoAtual({ preservarDigitado: true });
    }, 180);
  }

  function obterIdFaseAtualDoCardAbertoNoModal(){
    const idFaseDoDataset = idNum(modalCard?.dataset?.idFaseAtual || 0);
    if (idFaseDoDataset > 0) {
      return idFaseDoDataset;
    }

    const idFaseDoCardAberto = idNum(obterCardPorId(cardAbertoId)?.IDDimKanbanFaseAtual || 0);
    return idFaseDoCardAberto;
  }

  function modalCardEstaNaFaseQuatro(){
    return obterIdFaseAtualDoCardAbertoNoModal() === ID_FASE_FORMULARIO_CONTRATO;
  }

  function valorFormularioSolicitacaoTemConteudo(valor){
    if (valor === null || valor === undefined) return false;

    if (typeof valor === "number") {
      return Number.isFinite(valor);
    }

    if (typeof valor === "boolean") {
      return valor === true;
    }

    return safeStr(valor).trim() !== "";
  }

  function estruturaFormularioSolicitacaoTemConteudo(valor){
    if (valor === null || valor === undefined) return false;

    if (Array.isArray(valor)) {
      return valor.some((item) => estruturaFormularioSolicitacaoTemConteudo(item));
    }

    if (typeof valor === "object") {
      return Object.values(valor).some((item) => estruturaFormularioSolicitacaoTemConteudo(item));
    }

    return valorFormularioSolicitacaoTemConteudo(valor);
  }

  function formularioSolicitacaoContratoTemDadosPersistidos(){
    const snapshot = snapshotSolicitacaoEditavelAtual;
    if (!snapshot || typeof snapshot !== "object") return false;

    return estruturaFormularioSolicitacaoTemConteudo(snapshot.header)
      || estruturaFormularioSolicitacaoTemConteudo(snapshot.item)
      || estruturaFormularioSolicitacaoTemConteudo(snapshot.itens)
      || estruturaFormularioSolicitacaoTemConteudo(snapshot.contato_cliente_direto)
      || estruturaFormularioSolicitacaoTemConteudo(snapshot.contatoClienteDireto);
  }

  function cardAbertoJaPassouPelaFaseFormularioContrato(){
    /*
     * Regra correta do formulário:
     * - selecionar empresa no card NÃO libera o formulário;
     * - o formulário nasce visualmente quando o card está na fase 4;
     * - depois que o card já passou pela fase 4 e existe snapshot salvo, os campos preenchidos continuam visíveis.
     */
    if (modalCard?.dataset?.jaPassouFaseFormularioContrato === "1") return true;
    if (modalCardEstaNaFaseQuatro()) return true;

    const card = obterCardPorId(cardAbertoId) || {};
    const flagBackend = idNum(
      card.BitJaPassouPelaFaseFormularioContrato ??
      card.bit_ja_passou_pela_fase_formulario_contrato ??
      card.JaPassouPelaFaseFormularioContrato ??
      0
    );

    return flagBackend === 1;
  }

  function formularioSolicitacaoContratoTemDadosPersistidosValidosParaExibir(){
    return formularioSolicitacaoContratoTemDadosPersistidos()
      && cardAbertoJaPassouPelaFaseFormularioContrato();
  }

  function campoFormularioSolicitacaoTemConteudo(campo){
    if (!campo) return false;

    const tag = safeStr(campo.tagName || "").toUpperCase();
    if (tag === "INPUT") {
      const tipo = safeStr(campo.getAttribute("type") || "text").toLowerCase();
      if (["button", "submit", "reset", "file"].includes(tipo)) return false;
      if (["checkbox", "radio"].includes(tipo)) return campo.checked;
    }

    return valorFormularioSolicitacaoTemConteudo(campo.value);
  }

  function formularioSolicitacaoContratoTemValoresNoDom(){
    if (!wrapFormularioSolicitacaoContrato) return false;
    return [...wrapFormularioSolicitacaoContrato.querySelectorAll("[data-campo-solicitacao]")]
      .some((campo) => campoFormularioSolicitacaoTemConteudo(campo));
  }

  function formularioSolicitacaoContratoDevePersistirNoPayload(){
    /*
     * Regra de negócio:
     * - antes da fase 4, selecionar empresa, origem, marca ou painel NÃO deve criar solicitação;
     * - na fase 4, o formulário é liberado e pode ser salvo;
     * - depois que o card passou pela fase 4, o snapshot existente continua sendo preservado.
     */
    if (modalCardEstaNaFaseQuatro()) return true;
    if (formularioSolicitacaoContratoTemDadosPersistidosValidosParaExibir()) return true;
    return !!formularioSolicitacaoLiberadoNestaAbertura && formularioSolicitacaoContratoTemValoresNoDom();
  }

  function obterSolicitacaoContratoParaPayload(){
    if (!formularioSolicitacaoContratoDevePersistirNoPayload()) {
      return null;
    }

    return coletarFormularioSolicitacaoContrato();
  }

  function aplicarVisibilidadeCamposPreenchidosFormularioSolicitacaoForaDaFaseQuatro(estaNaFaseQuatro){
    if (!wrapFormularioSolicitacaoContrato) return;

    const wrapsCampos = [...wrapFormularioSolicitacaoContrato.querySelectorAll(".kb-contrato-campo")];

    wrapsCampos.forEach((wrapCampo) => {
      const campos = [...wrapCampo.querySelectorAll("[data-campo-solicitacao]")];
      if (!campos.length) return;

      if (estaNaFaseQuatro) {
        if (wrapCampo.dataset.ocultoForaFaseQuatro === "1") {
          wrapCampo.hidden = false;
          wrapCampo.style.display = "";
          delete wrapCampo.dataset.ocultoForaFaseQuatro;
        }
        return;
      }

      const temValor = campos.some((campo) => campoFormularioSolicitacaoTemConteudo(campo));
      wrapCampo.hidden = !temValor;
      wrapCampo.style.display = temValor ? "" : "none";

      if (temValor) {
        delete wrapCampo.dataset.ocultoForaFaseQuatro;
      } else {
        wrapCampo.dataset.ocultoForaFaseQuatro = "1";
      }
    });

    if (!estaNaFaseQuatro) {
      [...wrapFormularioSolicitacaoContrato.querySelectorAll(".kb-contrato-item-secao")].forEach((secao) => {
        const temCampoVisivel = [...secao.querySelectorAll(".kb-contrato-campo")].some((wrapCampo) => {
          return !wrapCampo.hidden && wrapCampo.style.display !== "none";
        });
        secao.hidden = !temCampoVisivel;
        secao.style.display = temCampoVisivel ? "" : "none";
      });

      aplicarVisibilidadeFormularioContatoClienteDireto();
    } else {
      [...wrapFormularioSolicitacaoContrato.querySelectorAll(".kb-contrato-item-secao")].forEach((secao) => {
        secao.hidden = false;
        secao.style.display = "";
      });
    }
  }

  function definirVisibilidadeContratoFlowBox(visivel){
    const deveMostrar = !!visivel;

    if (contratoFlowBox) {
      contratoFlowBox.hidden = !deveMostrar;
      contratoFlowBox.style.display = deveMostrar ? "" : "none";
      contratoFlowBox.setAttribute("aria-hidden", deveMostrar ? "false" : "true");
    }

    if (!deveMostrar) {
      if (wrapSelectContratoCard) wrapSelectContratoCard.hidden = true;
      if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = true;
      if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = true;
      if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = true;
      fecharListaContratosCombobox();
    }
  }

  function atualizarVisibilidadeFormularioSolicitacaoContrato(){
    const estaNaFaseQuatro = modalCardEstaNaFaseQuatro();

    if (estaNaFaseQuatro) {
      formularioSolicitacaoLiberadoNestaAbertura = true;
      if (modalCard) {
        modalCard.dataset.jaPassouFaseFormularioContrato = "1";
      }
    }

    const temDadosPersistidosValidos = formularioSolicitacaoContratoTemDadosPersistidosValidosParaExibir();
    const temDadosDigitadosNestaAbertura = formularioSolicitacaoLiberadoNestaAbertura
      && formularioSolicitacaoContratoTemValoresNoDom();

    const deveExibirFormularioContrato = estaNaFaseQuatro
      || temDadosPersistidosValidos
      || temDadosDigitadosNestaAbertura;

    if (wrapFormularioSolicitacaoContrato) {
      wrapFormularioSolicitacaoContrato.hidden = !deveExibirFormularioContrato;
      wrapFormularioSolicitacaoContrato.style.display = deveExibirFormularioContrato ? "" : "none";
      wrapFormularioSolicitacaoContrato.setAttribute("aria-hidden", deveExibirFormularioContrato ? "false" : "true");
    }

    if (deveExibirFormularioContrato) {
      aplicarVisibilidadeCamposPreenchidosFormularioSolicitacaoForaDaFaseQuatro(estaNaFaseQuatro);

      if (estaNaFaseQuatro) {
        aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente();
      }
    }

    definirVisibilidadeContratoFlowBox(estaNaFaseQuatro);

    try {
      sincronizarSeletoresContratoAditivoEmTodosBlocos();
    } catch (erro) {
      if (!(erro instanceof ReferenceError)) {
        console.warn("atualizarVisibilidadeFormularioSolicitacaoContrato: falha ao sincronizar seletores de aditivo", erro);
      }
    }
  }

  renderizarFormularioSolicitacaoContrato();
  atualizarVisibilidadeFormularioSolicitacaoContrato();

  function registrarEventosFormularioSolicitacaoPainelFace(){
    if (!painelFaceLista || painelFaceLista.dataset.formularioSolicitacaoEventosRegistrados === "1") return;

    painelFaceLista.dataset.formularioSolicitacaoEventosRegistrados = "1";

    painelFaceLista.addEventListener("input", (evento) => {
      if (evento.target?.closest?.('.kb-painel-item')) {
        agendarSincronizacaoFormularioSolicitacao();
      }
    });

    painelFaceLista.addEventListener("change", (evento) => {
      if (evento.target?.closest?.('.kb-painel-item')) {
        agendarSincronizacaoFormularioSolicitacao();
      }
    });
  }

  let clientesDiretoResultadoComboboxAtual = [];
  let bureauResultadoComboboxAtual = [];
  let intermediariosResultadoComboboxAtual = [];

  const cadEmpresaId = document.getElementById("cadEmpresaId");
  const cadEmpresaCnpj = document.getElementById("cadEmpresaCnpj");
  const cadEmpresaProprietaria = document.getElementById("cadEmpresaProprietaria");
  const cadEmpresaRazaoSocial = document.getElementById("cadEmpresaRazaoSocial");
  const cadEmpresaNomeFantasia = document.getElementById("cadEmpresaNomeFantasia");
  const cadEmpresaEmail = document.getElementById("cadEmpresaEmail");
  const cadEmpresaBitCliente = document.getElementById("cadEmpresaBitCliente");
  const cadEmpresaTelefone1 = document.getElementById("cadEmpresaTelefone1");
  const cadEmpresaTelefone2 = document.getElementById("cadEmpresaTelefone2");
  const cadEmpresaPais = document.getElementById("cadEmpresaPais");
  const cadEmpresaCep = document.getElementById("cadEmpresaCep");
  const cadEmpresaUf = document.getElementById("cadEmpresaUf");
  const cadEmpresaMunicipio = document.getElementById("cadEmpresaMunicipio");
  const cadEmpresaBairro = document.getElementById("cadEmpresaBairro");
  const cadEmpresaLogradouro = document.getElementById("cadEmpresaLogradouro");
  const cadEmpresaNumero = document.getElementById("cadEmpresaNumero");
  const cadEmpresaComplemento = document.getElementById("cadEmpresaComplemento");
  const cadEmpresaCnae = document.getElementById("cadEmpresaCnae");
  const cadEmpresaCodigoPorte = document.getElementById("cadEmpresaCodigoPorte");
  const cadEmpresaPorte = document.getElementById("cadEmpresaPorte");
  const cadEmpresaDescricaoCnae = document.getElementById("cadEmpresaDescricaoCnae");
  const cadEmpresaNaturezaJuridica = document.getElementById("cadEmpresaNaturezaJuridica");
  const cadEmpresaCapitalSocial = document.getElementById("cadEmpresaCapitalSocial");
  const cadEmpresaIdentificadorMatrizFilial = document.getElementById("cadEmpresaIdentificadorMatrizFilial");
  const cadEmpresaDescricaoIdentificadorMatrizFilial = document.getElementById("cadEmpresaDescricaoIdentificadorMatrizFilial");
  const cadEmpresaDescricaoSituacaoCadastral = document.getElementById("cadEmpresaDescricaoSituacaoCadastral");
  const cadEmpresaDescricaoMotivoSituacaoCadastral = document.getElementById("cadEmpresaDescricaoMotivoSituacaoCadastral");
  const cadEmpresaDescricaoTipoLogradouro = document.getElementById("cadEmpresaDescricaoTipoLogradouro");
  const cadEmpresaLatitude = document.getElementById("cadEmpresaLatitude");
  const cadEmpresaLongitude = document.getElementById("cadEmpresaLongitude");
  const cadEmpresaDataInicioAtividades = document.getElementById("cadEmpresaDataInicioAtividades");
  const cadEmpresaDataSituacaoEspecial = document.getElementById("cadEmpresaDataSituacaoEspecial");
  const cadEmpresaDataOpcaoPeloSimples = document.getElementById("cadEmpresaDataOpcaoPeloSimples");
  const cadEmpresaDataSituacaoCadastral = document.getElementById("cadEmpresaDataSituacaoCadastral");
  const cadEmpresaDataExclusaoSimples = document.getElementById("cadEmpresaDataExclusaoSimples");
  const cadEmpresaDataAtualizacao = document.getElementById("cadEmpresaDataAtualizacao");

  const painelFaceWrap = document.getElementById("painelFaceWrap");
  const painelFaceLista = document.getElementById("painelFaceLista");
  const btnAdicionarPainelFace = document.getElementById("btnAdicionarPainelFace");

  registrarEventosFormularioSolicitacaoPainelFace();

  const modalOrcamentoCard = document.getElementById("modalOrcamentoCard");
  const btnFecharOrcamentoCard = document.getElementById("btnFecharOrcamentoCard");
  const btnImprimirOrcamentoCard = document.getElementById("btnImprimirOrcamentoCard");
  const orcamentoCardConteudo = document.getElementById("orcamentoCardConteudo");

  const modalRemoverCard = document.getElementById("modalRemoverCard");
  const btnFecharRemoverCard = document.getElementById("btnFecharRemoverCard");
  const btnConfirmarRemoverCard = document.getElementById("btnConfirmarRemoverCard");
  const motivoRemocao = document.getElementById("motivoRemocao");
  const descricaoRemocao = document.getElementById("descricaoRemocao");
  const msgRemoverCard = document.getElementById("msgRemoverCard");

  const modalInativarFase = document.getElementById("modalInativarFase");
  const btnFecharInativarFase = document.getElementById("btnFecharInativarFase");
  const btnConfirmarInativarFase = document.getElementById("btnConfirmarInativarFase");
  const msgInativarFase = document.getElementById("msgInativarFase");


  let cardParaRemover = null;
  let faseParaInativar = null;

  const facesPorPainelId = new Map();
  const comercialPorPainelFace = new Map();
  let facesCarregando = false;
  let debounceBuscaTimer = null;
  let debounceSugestaoBuscaTimer = null;
  let buscaKanbanSugestoesController = null;
  let sugestoesBuscaKanbanAtual = [];
  let indiceSugestaoBuscaKanbanAtiva = -1;
  const LIMITE_SUGESTOES_BUSCA_KANBAN = 12;
  const MIN_CARACTERES_SUGESTAO_BUSCA_KANBAN = 2;
  const LIMITE_EMPRESAS_COMBOBOX = 80;
  const LIMITE_PAINEIS_COMBOBOX = 80;
  const LIMITE_PAINEL_FACES_COMBOBOX = 120;

  let cardArrastandoId = 0;
  let faseOrigemArrasteId = 0;
  let elementoArrastando = null;
  let dragImageEl = null;
  const movimentosCardsPendentes = new Set();
  const eventosLocaisPendentes = new Set();

  function gerarClientRequestIdKanban(prefixo = "kb"){
    const parteTempo = Date.now().toString(36);
    const parteAleatoria = Math.random().toString(36).slice(2, 10);
    return `${prefixo}_${ID_KANBAN}_${parteTempo}_${parteAleatoria}`;
  }

  function registrarEventoLocalPendente(clientRequestId, tempoVidaMs = 15000){
    const id = safeStr(clientRequestId || "").trim();
    if (!id) return;
    eventosLocaisPendentes.add(id);
    window.setTimeout(() => eventosLocaisPendentes.delete(id), Math.max(1000, Number(tempoVidaMs) || 15000));
  }

  function eventoSocketVeioDesteCliente(payload){
    const id = safeStr(payload?.client_request_id || payload?.clientRequestId || "").trim();
    return !!id && eventosLocaisPendentes.has(id);
  }


  function el(tag, attrs = {}, children = []) {
    const d = document.createElement(tag);

    Object.entries(attrs).forEach(([k, v]) => {
      if (k === "class") d.className = v;
      else if (k === "style") d.setAttribute("style", v);
      else if (k.startsWith("on") && typeof v === "function") d.addEventListener(k.slice(2), v);
      else d.setAttribute(k, v);
    });

    children.forEach(c => d.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return d;
  }

  function idNum(v){
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  function safeStr(v){
    if (v === null || v === undefined) return "";
    return String(v);
  }

  function montarUrlKanban(caminho){
    const texto = safeStr(caminho || "").trim();
    if (!texto) return texto;
    if (/^https?:\/\//i.test(texto)) return texto;

    const raiz = safeStr(SCRIPT_ROOT || "").replace(/\/+$/, "");
    if (!raiz) return texto;
    if (texto === raiz || texto.startsWith(`${raiz}/`)) return texto;

    return texto.startsWith("/") ? `${raiz}${texto}` : `${raiz}/${texto}`;
  }

  const TIMEOUT_FETCH_PADRAO_KANBAN_MS = 45000;
  const TIMEOUT_FETCH_CRITICO_KANBAN_MS = 90000;
  const TIMEOUT_FETCH_POS_SAVE_KANBAN_MS = 12000;

  function erroTimeoutKanban(url, timeoutMs){
    const erro = new Error(`Tempo limite excedido ao chamar ${safeStr(url || "API")} (${Math.round((Number(timeoutMs) || 0) / 1000)}s).`);
    erro.name = "AbortError";
    erro.timeoutKanban = true;
    return erro;
  }

  async function fetchKanbanComTimeout(url, opcoes = {}, timeoutMs = TIMEOUT_FETCH_PADRAO_KANBAN_MS){
    const urlFinal = montarUrlKanban(url);
    const limite = Number(timeoutMs || 0);

    if (!limite || typeof AbortController !== "function") {
      return fetch(urlFinal, opcoes || {});
    }

    const controller = new AbortController();
    const opcoesFetch = Object.assign({}, opcoes || {}, { signal: controller.signal });
    let expirou = false;
    const timer = window.setTimeout(() => {
      expirou = true;
      try { controller.abort(); } catch (_erro) {}
    }, limite);

    try {
      return await fetch(urlFinal, opcoesFetch);
    } catch (erro) {
      if (expirou || erro?.name === "AbortError") {
        throw erroTimeoutKanban(urlFinal, limite);
      }
      throw erro;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function mensagemErroComunicacaoKanban(erro, fallback = "Falha de comunicação com o servidor."){
    if (erro?.timeoutKanban || erro?.name === "AbortError") {
      return "A requisição demorou demais e foi interrompida pelo navegador. A operação pode ter sido gravada no servidor; atualize o card se precisar confirmar.";
    }
    return fallback;
  }

  function resumirTextoRespostaHttp(texto, limite = 300){
    const bruto = safeStr(texto || "").replace(/\s+/g, " ").trim();
    if (!bruto) return "";
    return bruto.length > limite ? `${bruto.slice(0, limite)}...` : bruto;
  }

  async function fetchJsonKanban(url, opcoes = {}){
    const urlFinal = montarUrlKanban(url);
    const opcoesFetch = Object.assign({ credentials: "same-origin" }, opcoes || {});
    opcoesFetch.headers = Object.assign(
      {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest"
      },
      opcoesFetch.headers || {}
    );

    const timeoutMs = Number(opcoesFetch.timeoutMs || 0) || TIMEOUT_FETCH_PADRAO_KANBAN_MS;
    delete opcoesFetch.timeoutMs;

    const resposta = await fetchKanbanComTimeout(urlFinal, opcoesFetch, timeoutMs);

    const texto = await resposta.text().catch(() => "");
    let corpo = null;
    let jsonValido = false;

    if (safeStr(texto || "").trim()) {
      try {
        corpo = JSON.parse(texto);
        jsonValido = true;
      } catch (_erroJson) {
        corpo = null;
      }
    }

    return {
      resposta,
      corpo,
      jsonValido,
      textoBruto: texto,
      resumoTexto: resumirTextoRespostaHttp(texto),
      contentType: safeStr(resposta.headers?.get?.("content-type") || ""),
      urlFinal
    };
  }

  function respostaJsonKanbanOk(resultado){
    return !!(
      resultado &&
      resultado.resposta &&
      resultado.resposta.ok &&
      resultado.corpo &&
      resultado.corpo.ok
    );
  }

  function detalhesFalhaJsonKanban(resultado){
    return {
      http: resultado?.resposta?.status || 0,
      body: resultado?.corpo || null,
      jsonValido: !!resultado?.jsonValido,
      contentType: resultado?.contentType || "",
      urlFinal: resultado?.urlFinal || "",
      preview: resultado?.resumoTexto || ""
    };
  }

  const URL_FLATPICKR_JS = "https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.js";
  const URL_FLATPICKR_CSS = "https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css";
  const cacheCalendarioOcupacaoPorFace = new Map();
  const promessasCalendarioOcupacaoPorFace = new Map();
  let promessaFlatpickrBiblioteca = null;

  const localeFlatpickrPtBr = {
    weekdays: {
      shorthand: ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'],
      longhand: ['Domingo', 'Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado']
    },
    months: {
      shorthand: ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
      longhand: ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
    },
    firstDayOfWeek: 0,
    ordinal: () => 'º',
    rangeSeparator: ' até ',
    weekAbbreviation: 'Sem',
    scrollTitle: 'Role para aumentar',
    toggleTitle: 'Clique para alternar',
    time_24hr: true
  };

  function garantirCssExterno(url, idElemento){
    if (!url) return;
    if (idElemento && document.getElementById(idElemento)) return;

    const existente = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).find((item) => {
      const href = safeStr(item.getAttribute('href') || '').trim();
      return href === url;
    });

    if (existente) return;

    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = url;
    if (idElemento) link.id = idElemento;
    document.head.appendChild(link);
  }

  function carregarFlatpickrBiblioteca(){
    if (window.flatpickr) {
      return Promise.resolve(window.flatpickr);
    }

    if (promessaFlatpickrBiblioteca) {
      return promessaFlatpickrBiblioteca;
    }

    garantirCssExterno(URL_FLATPICKR_CSS, 'kb-flatpickr-css');

    promessaFlatpickrBiblioteca = new Promise((resolve, reject) => {
      const existente = document.getElementById('kb-flatpickr-js');
      if (existente) {
        existente.addEventListener('load', () => resolve(window.flatpickr), { once: true });
        existente.addEventListener('error', () => reject(new Error('Falha ao carregar flatpickr.')), { once: true });
        return;
      }

      const script = document.createElement('script');
      script.id = 'kb-flatpickr-js';
      script.src = URL_FLATPICKR_JS;
      script.async = true;
      script.onload = () => {
        if (window.flatpickr) {
          resolve(window.flatpickr);
          return;
        }
        reject(new Error('Flatpickr carregou sem expor a função global.'));
      };
      script.onerror = () => reject(new Error('Falha ao carregar a biblioteca de calendário.'));
      document.head.appendChild(script);
    }).catch((erro) => {
      promessaFlatpickrBiblioteca = null;
      throw erro;
    });

    return promessaFlatpickrBiblioteca;
  }

  function formatarDataIso(data){
    if (!(data instanceof Date) || Number.isNaN(data.getTime())) return '';

    const ano = data.getFullYear();
    const mes = String(data.getMonth() + 1).padStart(2, '0');
    const dia = String(data.getDate()).padStart(2, '0');
    return `${ano}-${mes}-${dia}`;
  }

  function obterDataAtualIsoLocal(){
    return formatarDataIso(new Date());
  }

  function dataIsoEhAnteriorAoDiaAtual(valor){
    const dataIso = safeStr(valor || '').trim();
    return /^\d{4}-\d{2}-\d{2}$/.test(dataIso) && dataIso < obterDataAtualIsoLocal();
  }

  function manterSomenteDataAtualOuFutura(valor){
    const dataIso = normalizarDataParaInput(valor);
    if (!dataIso) return '';
    if (dataIsoEhAnteriorAoDiaAtual(dataIso)) return '';
    return dataIso;
  }

  function limparInputDataReserva(input){
    if (!input) return;
    if (input._flatpickr) {
      input._flatpickr.clear();
    } else {
      input.value = '';
    }
  }

  function marcarDiaPassadoFlatpickr(dayElem, data){
    if (!dayElem) return;

    const dataIso = formatarDataIso(data);
    if (!dataIso) return;

    dayElem.dataset.dateIso = dataIso;

    if (dataIsoEhAnteriorAoDiaAtual(dataIso)) {
      dayElem.classList.add('flatpickr-disabled', 'kb-dia-passado-bloqueado');
      dayElem.setAttribute('aria-disabled', 'true');
      dayElem.title = `Data bloqueada: anterior a ${formatarDataIsoParaBr(obterDataAtualIsoLocal())}.`;
    }
  }

  function obterMaiorDataIso(...datas){
    return datas
      .map((valor) => safeStr(valor || '').trim())
      .filter(Boolean)
      .sort()
      .pop() || '';
  }

  function parseDataIso(texto){
    const valor = safeStr(texto).trim();
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(valor);
    if (!match) return null;

    const ano = Number(match[1]);
    const mes = Number(match[2]);
    const dia = Number(match[3]);

    const data = new Date(ano, mes - 1, dia);
    if (formatarDataIso(data) !== valor) return null;
    return data;
  }

  function formatarDataIsoParaBr(texto){
    const data = parseDataIso(texto);
    if (!data) return safeStr(texto).trim();
    const dia = String(data.getDate()).padStart(2, '0');
    const mes = String(data.getMonth() + 1).padStart(2, '0');
    const ano = data.getFullYear();
    return `${dia}/${mes}/${ano}`;
  }

  function somarDiasDataIso(texto, quantidadeDias){
    const data = parseDataIso(texto);
    if (!data) return '';
    data.setDate(data.getDate() + Number(quantidadeDias || 0));
    return formatarDataIso(data);
  }

  function construirSetDatasIntervalo(dataInicio, dataFim){
    const datas = new Set();
    if (!dataInicio || !dataFim || dataFim < dataInicio) return datas;

    let cursor = dataInicio;
    let guard = 0;

    while (cursor && cursor <= dataFim && guard < 3700){
      datas.add(cursor);
      cursor = somarDiasDataIso(cursor, 1);
      guard += 1;
    }

    return datas;
  }

  function obterCalendarioOcupacaoDoBloco(bloco){
    if (!bloco || !bloco.__calendarioOcupacao || typeof bloco.__calendarioOcupacao !== 'object') {
      return null;
    }
    return bloco.__calendarioOcupacao;
  }

  function sincronizarRestricoesDataFimDoBloco(bloco){
    const inputDataInicio = bloco?.querySelector('[data-role="input-data-inicio"]');
    const inputDataFim = bloco?.querySelector('[data-role="input-data-fim"]');
    if (!inputDataInicio || !inputDataFim) return;

    const dataHojeIso = obterDataAtualIsoLocal();
    let dataInicio = safeStr(inputDataInicio.value || '').trim();
    let dataFim = safeStr(inputDataFim.value || '').trim();

    if (dataInicio && dataInicio < dataHojeIso) {
      limparInputDataReserva(inputDataInicio);
      dataInicio = '';
    }

    if (dataFim && dataFim < dataHojeIso) {
      limparInputDataReserva(inputDataFim);
      dataFim = '';
    }

    const dataMinimaFim = obterMaiorDataIso(dataHojeIso, dataInicio);

    inputDataInicio.min = dataHojeIso;
    inputDataFim.min = dataMinimaFim || dataHojeIso;

    if (inputDataInicio._flatpickr) {
      inputDataInicio._flatpickr.set('minDate', dataHojeIso);
      inputDataInicio._flatpickr.redraw();
    }

    if (inputDataFim._flatpickr) {
      inputDataFim._flatpickr.set('minDate', dataMinimaFim || dataHojeIso);

      if (dataInicio && dataInicio >= dataHojeIso) {
        inputDataFim._flatpickr.jumpToDate(dataInicio, false);
      } else {
        inputDataFim._flatpickr.jumpToDate(dataHojeIso, false);
      }

      inputDataFim._flatpickr.redraw();
    }

    if (dataInicio && dataFim && dataFim < dataInicio) {
      limparInputDataReserva(inputDataFim);
    }
  }

  function obterInfoDiaOcupacaoDoBloco(bloco, dataIso){
    const calendario = obterCalendarioOcupacaoDoBloco(bloco);
    if (!calendario || !dataIso) return null;
    return calendario[dataIso] || null;
  }

  function diaEstaManualMenteLiberadoNoBloco(bloco, dataIso){
    return !!(bloco && bloco.__datasReservaLiberadasManual instanceof Set && bloco.__datasReservaLiberadasManual.has(dataIso));
  }

  function obterPrimeiroNumeroValido(...valores){
    for (const valor of valores) {
      if (valor === null || valor === undefined || valor === '') continue;
      const numero = Number(valor);
      if (Number.isFinite(numero)) return numero;
    }
    return null;
  }

  function obterCotaReservaDoBloco(bloco){
    const selectExibicoesDia = bloco?.querySelector('[data-role="select-exibicoes-dia"]');

    const valor = safeStr(
      obterExibicaoDiaAtivaDoBloco(bloco) ||
      obterValoresSelecionadosSelect(selectExibicoesDia)[0] ||
      bloco?.dataset?.exibicoesDia ||
      bloco?.dataset?.exibicaoDia ||
      ''
    ).trim();

    return valor;
  }

  function obterSlotsNecessariosReservaDoBloco(bloco){
    const usarInsercoesDigitais = painelFaceUsaInsercoesDigitais(bloco);
    if (!usarInsercoesDigitais) return 1;

    const cotaTexto = obterCotaReservaDoBloco(bloco);
    const cotaNormalizada = safeStr(cotaTexto || '').replace(',', '.');
    const matchCota = cotaNormalizada.match(/\d+(?:\.\d+)?/);
    const cota = matchCota ? Number(matchCota[0]) : Number(cotaNormalizada);

    if (!Number.isFinite(cota) || cota <= 0) return 1;

    /*
     * Regra oficial da grade de ocupação digital:
     * - COTA 540 consome 1 slot;
     * - COTA 1080 consome 2 slots.
     *
     * Motivo: 1080 inserções/dia ocupa o dobro de 540 inserções/dia.
     * Os valores 1 e 2 abaixo são compatibilidade com registros legados
     * que gravavam código de cota em vez de quantidade de inserções.
     */
    if (cota === 540) return 1;
    if (cota === 1080) return 2;
    if (cota === 1) return 2;
    if (cota === 2) return 1;

    return Math.max(1, Math.ceil(cota / 540));
  }

  function normalizarInfoDiaOcupacaoKanban(info){
    if (!info || typeof info !== 'object') return null;

    const cap = obterPrimeiroNumeroValido(
      info.cap,
      info.CapacidadeSlots,
      info.capacidade,
      info.Capacidade,
      info.total,
      info.Total
    );

    let disp = obterPrimeiroNumeroValido(
      info.disp,
      info.SlotsDisponiveis,
      info.disponiveis,
      info.Disponiveis,
      info.available,
      info.Available
    );

    const ocup = obterPrimeiroNumeroValido(
      info.ocup,
      info.SlotsOcupados,
      info.ocupados,
      info.Ocupados,
      info.used,
      info.Used
    );

    if (disp === null && cap !== null && ocup !== null) {
      disp = cap - ocup;
    }

    const maxBlocoLivre = obterPrimeiroNumeroValido(
      info.max_bloco_livre,
      info.bloco_livre_max,
      info.MaxBlocoLivre,
      info.MaiorBlocoLivre,
      info.maior_bloco_livre
    );

    return {
      cap: cap === null ? null : cap,
      disp: disp === null ? null : Math.max(0, disp),
      ocup: ocup === null ? null : ocup,
      maxBlocoLivre: maxBlocoLivre === null ? null : Math.max(0, maxBlocoLivre),
      status: safeStr(info.status || info.Status || info.StatusDia || '').trim().toUpperCase(),
      diaDisponivel: Number(info.dia_disponivel ?? info.DiaDisponivel ?? 0) === 1
    };
  }

  function obterSlotsLivresDiaParaEncaixeNoBloco(bloco, dataIso){
    if (!dataIso) return 0;

    const slotsNecessarios = obterSlotsNecessariosReservaDoBloco(bloco);

    if (diaEstaManualMenteLiberadoNoBloco(bloco, dataIso)) {
      const infoManual = normalizarInfoDiaOcupacaoKanban(obterInfoDiaOcupacaoDoBloco(bloco, dataIso));
      if (infoManual?.disp !== null && infoManual?.disp !== undefined) {
        return Math.max(slotsNecessarios, Number(infoManual.disp) || 0);
      }
      return slotsNecessarios;
    }

    const info = normalizarInfoDiaOcupacaoKanban(obterInfoDiaOcupacaoDoBloco(bloco, dataIso));
    if (!info) return 0;

    const status = info.status;
    if (status === 'INDISPONIVEL' || status === 'INATIVO') {
      return 0;
    }

    if (info.cap !== null && info.cap <= 0) return 0;

    if (info.maxBlocoLivre !== null && info.maxBlocoLivre !== undefined) {
      const maxBloco = Math.max(0, Number(info.maxBlocoLivre) || 0);
      if (info.disp !== null && info.disp !== undefined) {
        return Math.min(Math.max(0, Number(info.disp) || 0), maxBloco);
      }
      return maxBloco;
    }

    if (status === 'OCUPADO' || status === 'LOTADO' || status === 'CHEIO') {
      return 0;
    }

    if (info.disp !== null) return Math.max(0, Number(info.disp) || 0);

    return info.diaDisponivel ? slotsNecessarios : 0;
  }

  function diaEstaDisponivelNoBloco(bloco, dataIso){
    const slotsNecessarios = obterSlotsNecessariosReservaDoBloco(bloco);
    return obterSlotsLivresDiaParaEncaixeNoBloco(bloco, dataIso) >= slotsNecessarios;
  }

  function obterResumoEncaixeFragmentadoIntervaloNoBloco(bloco, dataInicio, dataFim){
    const slotsNecessarios = obterSlotsNecessariosReservaDoBloco(bloco);
    const resumo = {
      ok: false,
      dias: 0,
      slotsNecessarios,
      primeiroDiaSemEncaixe: '',
      slotsLivresPrimeiroDiaSemEncaixe: null,
      menorSaldoDia: null,
      slotsLivresTotal: 0,
      slotsNecessariosTotal: 0
    };

    if (!dataInicio || !dataFim || dataFim < dataInicio) return resumo;

    let cursor = dataInicio;
    let guard = 0;

    while (cursor && cursor <= dataFim && guard < 3700){
      const livresDia = obterSlotsLivresDiaParaEncaixeNoBloco(bloco, cursor);
      resumo.dias += 1;
      resumo.slotsLivresTotal += livresDia;
      resumo.slotsNecessariosTotal += slotsNecessarios;

      if (resumo.menorSaldoDia === null || livresDia < resumo.menorSaldoDia) {
        resumo.menorSaldoDia = livresDia;
      }

      if (livresDia < slotsNecessarios && !resumo.primeiroDiaSemEncaixe) {
        resumo.primeiroDiaSemEncaixe = cursor;
        resumo.slotsLivresPrimeiroDiaSemEncaixe = livresDia;
      }

      cursor = somarDiasDataIso(cursor, 1);
      guard += 1;
    }

    resumo.ok = resumo.dias > 0 && !resumo.primeiroDiaSemEncaixe;
    return resumo;
  }

  function intervaloEstaDisponivelNoBloco(bloco, dataInicio, dataFim){
    return obterResumoEncaixeFragmentadoIntervaloNoBloco(bloco, dataInicio, dataFim).ok;
  }

  function obterPrimeiroDiaIndisponivelIntervaloNoBloco(bloco, dataInicio, dataFim){
    return obterResumoEncaixeFragmentadoIntervaloNoBloco(bloco, dataInicio, dataFim).primeiroDiaSemEncaixe || '';
  }

  function montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataFim, prefixo = 'Não foi possível encaixar automaticamente o período inteiro.'){
    const resumo = obterResumoEncaixeFragmentadoIntervaloNoBloco(bloco, dataInicio, dataFim);
    const slotsNecessarios = resumo.slotsNecessarios || obterSlotsNecessariosReservaDoBloco(bloco);
    const cota = safeStr(obterCotaReservaDoBloco(bloco) || '').trim();
    const cotaTxt = cota ? `A cota ${cota}` : 'A cota selecionada';
    const unidade = slotsNecessarios === 1 ? 'slot livre' : 'slots livres';

    if (resumo.primeiroDiaSemEncaixe) {
      const livres = resumo.slotsLivresPrimeiroDiaSemEncaixe ?? 0;
      return `${prefixo} ${cotaTxt} precisa manter ${slotsNecessarios} ${unidade} em cada dia do período. O sistema pode trocar de linha/slot ao longo da grade, mas o dia ${formatarDataIsoParaBr(resumo.primeiroDiaSemEncaixe)} tem apenas ${livres} slot(s) livre(s). Verifique a grade para encaixe.`;
    }

    if (resumo.dias > 0) {
      return `${prefixo} ${cotaTxt} precisa manter ${slotsNecessarios} ${unidade} em cada dia do período. O sistema pode trocar de linha/slot ao longo da grade, mas não pode pular dias dentro do range.`;
    }

    return `${prefixo} Verifique a grade para encaixe.`;
  }

  function redesenharCalendariosReservaDoBloco(bloco){
    const inputDataInicio = bloco?.querySelector('[data-role="input-data-inicio"]');
    const inputDataFim = bloco?.querySelector('[data-role="input-data-fim"]');
    if (!inputDataInicio || !inputDataFim) return;

    const dataInicio = safeStr(inputDataInicio.value || '').trim();
    const dataFim = safeStr(inputDataFim.value || '').trim();
    const slotsNecessarios = obterSlotsNecessariosReservaDoBloco(bloco);
    let mensagemBloqueioDefinida = false;

    if (dataInicio && !diaEstaDisponivelNoBloco(bloco, dataInicio)) {
      limparInputDataReserva(inputDataInicio);
      limparInputDataReserva(inputDataFim);
      atualizarMensagemReservaDoBloco(
        bloco,
        montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataInicio, 'A data inicial não comporta a cota selecionada.'),
        'erro'
      );
      mensagemBloqueioDefinida = true;
    } else if (dataInicio && dataFim && !intervaloEstaDisponivelNoBloco(bloco, dataInicio, dataFim)) {
      atualizarMensagemReservaDoBloco(
        bloco,
        montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataFim),
        'aviso'
      );
      mensagemBloqueioDefinida = true;
    }

    sincronizarRestricoesDataFimDoBloco(bloco);
    inputDataInicio._flatpickr?.redraw();
    inputDataFim._flatpickr?.redraw();
    if (!mensagemBloqueioDefinida) {
      atualizarResumoDisponibilidadeDoBloco(bloco);
    }
  }

  function atualizarMensagemReservaDoBloco(bloco, mensagem = '', tipo = 'info'){
    const alvo = bloco?.querySelector('[data-role="reserva-disponibilidade"]');
    if (!alvo) return;

    const texto = safeStr(mensagem).trim();
    if (!texto){
      alvo.hidden = true;
      alvo.textContent = '';
      alvo.dataset.tipo = 'info';
      return;
    }

    alvo.hidden = false;
    alvo.dataset.tipo = safeStr(tipo).trim() || 'info';
    alvo.textContent = texto;
  }

  function atualizarResumoDisponibilidadeDoBloco(bloco){
    const inputDataInicio = bloco?.querySelector('[data-role="input-data-inicio"]');
    const inputDataFim = bloco?.querySelector('[data-role="input-data-fim"]');
    const calendario = obterCalendarioOcupacaoDoBloco(bloco);

    const dataInicio = safeStr(inputDataInicio?.value || '').trim();
    const dataFim = safeStr(inputDataFim?.value || '').trim();
    const dataHojeIso = obterDataAtualIsoLocal();

    if (dataInicio && dataInicio < dataHojeIso) {
      atualizarMensagemReservaDoBloco(
        bloco,
        `A Data de início não pode ser anterior a ${formatarDataIsoParaBr(dataHojeIso)}.`,
        'erro'
      );
      return;
    }

    if (dataFim && dataFim < dataHojeIso) {
      atualizarMensagemReservaDoBloco(
        bloco,
        `A Data até não pode ser anterior a ${formatarDataIsoParaBr(dataHojeIso)}.`,
        'erro'
      );
      return;
    }

    if (!calendario) {
      if (safeStr(bloco?.__calendarioOcupacaoErro || '').trim()) {
        atualizarMensagemReservaDoBloco(
          bloco,
          bloco.__calendarioOcupacaoErro,
          'aviso'
        );
      } else {
        atualizarMensagemReservaDoBloco(bloco, '', 'info');
      }
      return;
    }

    if (!dataInicio && !dataFim){
      atualizarMensagemReservaDoBloco(bloco, '', 'info');
      return;
    }

    if (dataInicio && !dataFim){
      if (!diaEstaDisponivelNoBloco(bloco, dataInicio)) {
        atualizarMensagemReservaDoBloco(
          bloco,
          montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataInicio, 'A data selecionada não comporta a cota.'),
          'erro'
        );
        return;
      }

      atualizarMensagemReservaDoBloco(bloco, '', 'info');
      return;
    }

    if (!dataInicio && dataFim){
      atualizarMensagemReservaDoBloco(bloco, 'Preencha primeiro a Data de início.', 'erro');
      return;
    }

    if (dataFim < dataInicio){
      atualizarMensagemReservaDoBloco(bloco, 'A Data até não pode ser menor que a Data de início.', 'erro');
      return;
    }

    if (!intervaloEstaDisponivelNoBloco(bloco, dataInicio, dataFim)) {
      atualizarMensagemReservaDoBloco(
        bloco,
        montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataFim),
        'aviso'
      );
      return;
    }

    atualizarMensagemReservaDoBloco(bloco, '', 'info');
  }

  function destruirCalendariosReservaDoBloco(bloco){
    for (const role of ['input-data-inicio', 'input-data-fim']) {
      const input = bloco?.querySelector(`[data-role="${role}"]`);
      if (!input) continue;

      if (input._flatpickr) {
        try {
          input._flatpickr.destroy();
        } catch (erro) {
          console.warn('Falha ao destruir flatpickr do bloco.', erro);
        }
      }

      if (input.__kbReservaOnChange) {
        input.removeEventListener('change', input.__kbReservaOnChange);
        delete input.__kbReservaOnChange;
      }
    }
  }

  async function carregarCalendarioOcupacaoDaFace(codFace, opcoes = {}){
    const chave = safeStr(codFace).trim().toUpperCase();
    if (!chave) return {};

    const fresh = !!opcoes.fresh;
    const meses = Number(opcoes.meses || 18) || 18;
    const cota = safeStr(opcoes.cota || opcoes.exibicoesDia || '').trim();
    const codPonto = safeStr(opcoes.cod_ponto || opcoes.codPonto || '').trim();

    // CodFace sozinho não é uma chave segura para cache: em algumas telas o mesmo CodFace
    // pode ser reaplicado em fluxo de contrato/aditivo ou ficar com resposta antiga em memória.
    // Incluo CodPonto + cota na chave e também envio CodPonto para o backend resolver exatamente
    // a mesma grade que o usuário selecionou.
    const chaveCache = [codPonto || 'SEM_CODPONTO', chave, cota || 'SEM_COTA'].join('|');

    if (!fresh && cacheCalendarioOcupacaoPorFace.has(chaveCache)) {
      return cacheCalendarioOcupacaoPorFace.get(chaveCache) || {};
    }

    if (!fresh && promessasCalendarioOcupacaoPorFace.has(chaveCache)) {
      return promessasCalendarioOcupacaoPorFace.get(chaveCache);
    }

    const params = new URLSearchParams({
      cod_face: chave,
      meses: String(meses)
    });
    if (codPonto) params.set('cod_ponto', codPonto);
    if (cota) params.set('cota', cota);

    const promessa = fetch(
      `/kanban/api/ocupacao/calendario?${params.toString()}`,
      { credentials: 'same-origin' }
    )
      .then(async (resposta) => {
        const json = await resposta.json().catch(() => null);
        if (!resposta.ok || !json || !json.ok) {
          throw new Error((json && (json.erro || json.msg)) || 'Não foi possível consultar a ocupação desta face.');
        }

        const calendario = json && typeof json.cal === 'object' && json.cal ? json.cal : {};
        cacheCalendarioOcupacaoPorFace.set(chaveCache, calendario);
        return calendario;
      })
      .finally(() => {
        promessasCalendarioOcupacaoPorFace.delete(chaveCache);
      });

    promessasCalendarioOcupacaoPorFace.set(chaveCache, promessa);
    return promessa;
  }

  function limparCacheCalendarioOcupacao(codFaces = null){
    if (!codFaces) {
      cacheCalendarioOcupacaoPorFace.clear();
      promessasCalendarioOcupacaoPorFace.clear();
      return;
    }

    const lista = Array.isArray(codFaces) ? codFaces : [codFaces];
    lista.forEach((codFace) => {
      const chave = safeStr(codFace || '').trim().toUpperCase();
      if (!chave) return;
      for (const chaveCache of Array.from(cacheCalendarioOcupacaoPorFace.keys())) {
        if (
          chaveCache === chave ||
          chaveCache.startsWith(`${chave}|`) ||
          chaveCache.includes(`|${chave}|`)
        ) {
          cacheCalendarioOcupacaoPorFace.delete(chaveCache);
        }
      }
      for (const chaveCache of Array.from(promessasCalendarioOcupacaoPorFace.keys())) {
        if (
          chaveCache === chave ||
          chaveCache.startsWith(`${chave}|`) ||
          chaveCache.includes(`|${chave}|`)
        ) {
          promessasCalendarioOcupacaoPorFace.delete(chaveCache);
        }
      }
    });
  }

  function extrairCodFacesPainelFaces(lista){
    if (!Array.isArray(lista)) return [];
    return Array.from(new Set(
      lista
        .map((item) => safeStr(item?.cod_face || item?.CodFace || item?.codFace || item?.face || '').trim().toUpperCase())
        .filter(Boolean)
    ));
  }

  function prepararInputReservaParaFallback(input){
    if (!input) return;
    input.type = 'date';
    input.inputMode = 'numeric';
    input.placeholder = '';
    input.min = obterDataAtualIsoLocal();
  }

  function removerBadgeDiaCalendarioKanban(dayElem){
    if (!dayElem) return;
    dayElem.classList.remove('kb-dia-has-badge');
    const antigo = dayElem.querySelector?.('.kb-dia-badge');
    if (antigo) antigo.remove();
  }

  function adicionarBadgeDiaCalendarioKanban(dayElem, texto){
    if (!dayElem || !texto) return;
    removerBadgeDiaCalendarioKanban(dayElem);
    dayElem.classList.add('kb-dia-has-badge');
    const badge = document.createElement('span');
    badge.className = 'kb-dia-badge';
    badge.textContent = texto;
    dayElem.appendChild(badge);
  }

  function decorarDiaFlatpickr(instance, dayElem, date){
    const input = instance?.input;
    const bloco = input?.closest('.kb-painel-item');
    if (!bloco) return;

    const dataIso = formatarDataIso(date);
    const infoOriginal = obterInfoDiaOcupacaoDoBloco(bloco, dataIso);
    const info = normalizarInfoDiaOcupacaoKanban(infoOriginal);
    const liberadoManual = diaEstaManualMenteLiberadoNoBloco(bloco, dataIso);
    const slotsNecessarios = obterSlotsNecessariosReservaDoBloco(bloco);

    dayElem.classList.remove('kb-dia-livre', 'kb-dia-parcial', 'kb-dia-indisponivel', 'kb-dia-inativo');
    removerBadgeDiaCalendarioKanban(dayElem);
    dayElem.title = '';

    if (!info && !liberadoManual) {
      dayElem.classList.add('kb-dia-indisponivel');
      dayElem.classList.add('flatpickr-disabled');
      dayElem.setAttribute('aria-disabled', 'true');
      dayElem.title = 'Dia sem informação de disponibilidade.';
      return;
    }

    const status = safeStr(info?.status || '').trim().toUpperCase();
    const disponivelParaCota = liberadoManual || diaEstaDisponivelNoBloco(bloco, dataIso);
    const cap = info?.cap;
    const disp = info?.disp;
    const ocup = info?.ocup !== null && info?.ocup !== undefined
      ? info?.ocup
      : (cap !== null && cap !== undefined && disp !== null && disp !== undefined ? Math.max(0, cap - disp) : null);

    if (!disponivelParaCota) {
      const classeBloqueio = status === 'INDISPONIVEL' || status === 'INATIVO' ? 'kb-dia-inativo' : 'kb-dia-indisponivel';
      dayElem.classList.add(classeBloqueio);
      dayElem.classList.add('flatpickr-disabled');
      dayElem.setAttribute('aria-disabled', 'true');
    } else if (status === 'PARCIAL' || (disp !== null && cap !== null && disp < cap)) {
      dayElem.classList.add('kb-dia-parcial');
    } else {
      dayElem.classList.add('kb-dia-livre');
    }

    if (cap !== null && cap !== undefined && disp !== null && disp !== undefined) {
      adicionarBadgeDiaCalendarioKanban(dayElem, `${Math.max(0, disp)}/${cap}`);
    }

    const pedacosTitulo = [];
    if (liberadoManual) pedacosTitulo.push('liberado para a reserva já vinculada ao card');
    if (status) pedacosTitulo.push(status);
    if (ocup !== null && ocup !== undefined && cap !== null && cap !== undefined) {
      pedacosTitulo.push(`ocupação ${ocup}/${cap}`);
    }
    if (disp !== null && disp !== undefined) {
      pedacosTitulo.push(`livres ${disp}`);
    }
    if (info?.maxBlocoLivre !== null && info?.maxBlocoLivre !== undefined) {
      pedacosTitulo.push(`maior bloco livre ${info.maxBlocoLivre}`);
    }
    pedacosTitulo.push(`necessário ${slotsNecessarios}`);
    if (!disponivelParaCota && !liberadoManual) {
      pedacosTitulo.push('bloqueado para a cota selecionada');
    }

    dayElem.title = pedacosTitulo.join(' • ');
  }

  function configurarFallbackNativoReservaDoBloco(bloco, datasPadrao = {}){
    const inputDataInicio = bloco?.querySelector('[data-role="input-data-inicio"]');
    const inputDataFim = bloco?.querySelector('[data-role="input-data-fim"]');
    if (!inputDataInicio || !inputDataFim) return;

    prepararInputReservaParaFallback(inputDataInicio);
    prepararInputReservaParaFallback(inputDataFim);

    const dataInicioPadrao = manterSomenteDataAtualOuFutura(datasPadrao.dataInicio || '');
    const dataFimPadraoBruta = manterSomenteDataAtualOuFutura(datasPadrao.dataFim || '');
    const dataFimPadrao = dataInicioPadrao && dataFimPadraoBruta && dataFimPadraoBruta >= dataInicioPadrao
      ? dataFimPadraoBruta
      : '';

    inputDataInicio.value = dataInicioPadrao;
    inputDataFim.value = dataFimPadrao;
    inputDataInicio.disabled = false;
    inputDataFim.disabled = false;
    sincronizarRestricoesDataFimDoBloco(bloco);

    const aoAlterarDataInicio = () => {
      const dataHojeIso = obterDataAtualIsoLocal();
      const dataInicio = safeStr(inputDataInicio.value || '').trim();
      const dataFim = safeStr(inputDataFim.value || '').trim();

      if (dataInicio && dataInicio < dataHojeIso) {
        inputDataInicio.value = '';
        atualizarMensagemReservaDoBloco(
          bloco,
          `A Data de início não pode ser anterior a ${formatarDataIsoParaBr(dataHojeIso)}.`,
          'erro'
        );
        sincronizarRestricoesDataFimDoBloco(bloco);
        atualizarResumoDisponibilidadeDoBloco(bloco);
        agendarSincronizacaoFormularioSolicitacao();
        return;
      }

      sincronizarRestricoesDataFimDoBloco(bloco);

      if (obterCalendarioOcupacaoDoBloco(bloco) && dataInicio && !diaEstaDisponivelNoBloco(bloco, dataInicio)) {
        inputDataInicio.value = '';
        sincronizarRestricoesDataFimDoBloco(bloco);
        atualizarMensagemReservaDoBloco(
          bloco,
          montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataInicio, 'A data selecionada não comporta a cota.'),
          'erro'
        );
        atualizarResumoDisponibilidadeDoBloco(bloco);
        return;
      }

      if (dataInicio && dataFim && dataFim < dataInicio){
        inputDataFim.value = '';
      }

      // Não exigir que a campanha fique na mesma linha/slot do começo ao fim.
      // A regra correta é: cada dia do range precisa comportar a cota selecionada
      // e o encaixe visual pode mudar de linha conforme os pedaços livres da grade.
      atualizarResumoDisponibilidadeDoBloco(bloco);
      agendarSincronizacaoFormularioSolicitacao();
    };

    const aoAlterarDataFim = () => {
      const dataHojeIso = obterDataAtualIsoLocal();
      const dataInicio = safeStr(inputDataInicio.value || '').trim();
      const dataFim = safeStr(inputDataFim.value || '').trim();

      if (dataFim && dataFim < dataHojeIso) {
        inputDataFim.value = '';
        atualizarMensagemReservaDoBloco(
          bloco,
          `A Data até não pode ser anterior a ${formatarDataIsoParaBr(dataHojeIso)}.`,
          'erro'
        );
        sincronizarRestricoesDataFimDoBloco(bloco);
        atualizarResumoDisponibilidadeDoBloco(bloco);
        agendarSincronizacaoFormularioSolicitacao();
        return;
      }

      if (dataInicio && dataFim && dataFim < dataInicio){
        inputDataFim.value = '';
      }

      if (
        obterCalendarioOcupacaoDoBloco(bloco) &&
        dataInicio &&
        safeStr(inputDataFim.value || '').trim() &&
        !intervaloEstaDisponivelNoBloco(bloco, dataInicio, safeStr(inputDataFim.value || '').trim())
      ) {
        atualizarMensagemReservaDoBloco(
          bloco,
          montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataFim),
          'aviso'
        );
      }

      atualizarResumoDisponibilidadeDoBloco(bloco);
      agendarSincronizacaoFormularioSolicitacao();
    };

    inputDataInicio.__kbReservaOnChange = aoAlterarDataInicio;
    inputDataFim.__kbReservaOnChange = aoAlterarDataFim;
    inputDataInicio.addEventListener('change', aoAlterarDataInicio);
    inputDataFim.addEventListener('change', aoAlterarDataFim);

    atualizarResumoDisponibilidadeDoBloco(bloco);
  }

  async function configurarCalendarioReservaNoBloco(bloco, datasPadrao = {}){
    const inputDataInicio = bloco?.querySelector('[data-role="input-data-inicio"]');
    const inputDataFim = bloco?.querySelector('[data-role="input-data-fim"]');
    if (!inputDataInicio || !inputDataFim) return;

    try {
      const flatpickr = await carregarFlatpickrBiblioteca();
      const dataHojeIso = obterDataAtualIsoLocal();

      const dataInicioPadraoOriginal = normalizarDataParaInput(datasPadrao.dataInicio || '');
      const dataFimPadraoOriginal = normalizarDataParaInput(datasPadrao.dataFim || '');

      const dataInicioPadrao = manterSomenteDataAtualOuFutura(dataInicioPadraoOriginal);
      const dataFimPadraoBase = manterSomenteDataAtualOuFutura(dataFimPadraoOriginal);
      const dataFimPadrao = dataInicioPadrao && dataFimPadraoBase && dataFimPadraoBase >= dataInicioPadrao
        ? dataFimPadraoBase
        : '';

      const tinhaDataPadraoPassada = (
        (dataInicioPadraoOriginal && dataInicioPadraoOriginal < dataHojeIso) ||
        (dataFimPadraoOriginal && dataFimPadraoOriginal < dataHojeIso)
      );

      inputDataInicio.type = 'text';
      inputDataInicio.inputMode = 'none';
      inputDataInicio.placeholder = 'dd/mm/aaaa';
      inputDataFim.type = 'text';
      inputDataFim.inputMode = 'none';
      inputDataFim.placeholder = 'dd/mm/aaaa';
      inputDataInicio.disabled = false;
      inputDataFim.disabled = false;
      inputDataInicio.min = dataHojeIso;
      inputDataFim.min = obterMaiorDataIso(dataHojeIso, dataInicioPadrao) || dataHojeIso;

      const opcoesBase = {
        locale: localeFlatpickrPtBr,
        altInput: true,
        altFormat: 'd/m/Y',
        dateFormat: 'Y-m-d',
        allowInput: false,
        disableMobile: true,
        minDate: dataHojeIso,
        monthSelectorType: 'static',
        onDayCreate: function(_dObj, _dStr, instance, dayElem){
          marcarDiaPassadoFlatpickr(dayElem, dayElem.dateObj);
          decorarDiaFlatpickr(instance, dayElem, dayElem.dateObj);
          marcarDiaPassadoFlatpickr(dayElem, dayElem.dateObj);
        }
      };

      flatpickr(inputDataInicio, {
        ...opcoesBase,
        defaultDate: dataInicioPadrao || null,
        disable: [
          function(data){
            const dataIso = formatarDataIso(data);
            const hojeAtualIso = obterDataAtualIsoLocal();

            if (dataIso && dataIso < hojeAtualIso) {
              return true;
            }

            return !diaEstaDisponivelNoBloco(bloco, dataIso);
          }
        ],
        onReady: function(_selectedDates, _dataStr, instance){
          const hojeAtualIso = obterDataAtualIsoLocal();
          instance.set('minDate', hojeAtualIso);
          if (dataInicioPadrao && dataInicioPadrao >= hojeAtualIso) {
            instance.jumpToDate(dataInicioPadrao, false);
          } else {
            instance.jumpToDate(hojeAtualIso, false);
          }
          instance.redraw();
        },
        onOpen: function(_selectedDates, _dataStr, instance){
          const hojeAtualIso = obterDataAtualIsoLocal();
          instance.set('minDate', hojeAtualIso);
          instance.jumpToDate(hojeAtualIso, false);
          instance.redraw();
        },
        onChange: function(_selectedDates, dataStr){
          const hojeAtualIso = obterDataAtualIsoLocal();
          const dataInicio = safeStr(dataStr || '').trim();

          if (dataInicio && dataInicio < hojeAtualIso) {
            limparInputDataReserva(inputDataInicio);
            atualizarMensagemReservaDoBloco(
              bloco,
              `A Data de início não pode ser anterior a ${formatarDataIsoParaBr(hojeAtualIso)}.`,
              'erro'
            );
            sincronizarRestricoesDataFimDoBloco(bloco);
            atualizarResumoDisponibilidadeDoBloco(bloco);
            agendarSincronizacaoFormularioSolicitacao();
            return;
          }

          sincronizarRestricoesDataFimDoBloco(bloco);

          // A Data até pode atravessar mudança de linha/slot na grade.
          // Não exigimos linha contínua; validamos se cada dia do range comporta a cota.
          atualizarResumoDisponibilidadeDoBloco(bloco);
          agendarSincronizacaoFormularioSolicitacao();
        }
      });

      flatpickr(inputDataFim, {
        ...opcoesBase,
        defaultDate: dataFimPadrao || null,
        minDate: obterMaiorDataIso(dataHojeIso, dataInicioPadrao) || dataHojeIso,
        disable: [
          function(data){
            const dataIso = formatarDataIso(data);
            const hojeAtualIso = obterDataAtualIsoLocal();
            const dataInicio = safeStr(inputDataInicio.value || '').trim();
            const dataMinimaFim = obterMaiorDataIso(hojeAtualIso, dataInicio);

            if (dataIso && dataIso < hojeAtualIso) {
              return true;
            }

            if (!dataInicio) {
              return true;
            }

            if (dataIso < dataMinimaFim) {
              return true;
            }

            // Para a Data até, bloqueie apenas se esse dia não comportar a cota.
            // O período pode trocar de linha/slot entre os dias, desde que cada dia mantenha
            // 1 slot para 540 ou 2 slots para 1080.
            return !diaEstaDisponivelNoBloco(bloco, dataIso);
          }
        ],
        onReady: function(_selectedDates, _dataStr, instance){
          const hojeAtualIso = obterDataAtualIsoLocal();
          const dataInicioAtual = safeStr(inputDataInicio.value || '').trim() || dataInicioPadrao;
          const dataMinimaFim = obterMaiorDataIso(hojeAtualIso, dataInicioAtual);
          instance.set('minDate', dataMinimaFim || hojeAtualIso);
          if (dataFimPadrao && dataFimPadrao >= dataMinimaFim) {
            instance.jumpToDate(dataFimPadrao, false);
          } else if (dataMinimaFim) {
            instance.jumpToDate(dataMinimaFim, false);
          } else {
            instance.jumpToDate(hojeAtualIso, false);
          }
          instance.redraw();
        },
        onChange: function(_selectedDates, dataStr){
          const hojeAtualIso = obterDataAtualIsoLocal();
          const dataFim = safeStr(dataStr || '').trim();

          if (dataFim && dataFim < hojeAtualIso) {
            limparInputDataReserva(inputDataFim);
            atualizarMensagemReservaDoBloco(
              bloco,
              `A Data até não pode ser anterior a ${formatarDataIsoParaBr(hojeAtualIso)}.`,
              'erro'
            );
            sincronizarRestricoesDataFimDoBloco(bloco);
            atualizarResumoDisponibilidadeDoBloco(bloco);
            agendarSincronizacaoFormularioSolicitacao();
            return;
          }

          atualizarResumoDisponibilidadeDoBloco(bloco);
          agendarSincronizacaoFormularioSolicitacao();
        },
        onOpen: function(_selectedDates, _dataStr, instance){
          const hojeAtualIso = obterDataAtualIsoLocal();
          const dataInicio = safeStr(inputDataInicio.value || '').trim();
          const dataMinimaFim = obterMaiorDataIso(hojeAtualIso, dataInicio);
          instance.set('minDate', dataMinimaFim || hojeAtualIso);
          if (dataMinimaFim) {
            instance.jumpToDate(dataMinimaFim, false);
          } else {
            instance.jumpToDate(hojeAtualIso, false);
          }
          instance.redraw();
        }
      });

      if (dataInicioPadrao && dataInicioPadrao >= obterDataAtualIsoLocal()) {
        inputDataInicio._flatpickr?.setDate(dataInicioPadrao, false, 'Y-m-d');
      } else {
        limparInputDataReserva(inputDataInicio);
      }

      if (dataFimPadrao && dataFimPadrao >= obterDataAtualIsoLocal()) {
        inputDataFim._flatpickr?.setDate(dataFimPadrao, false, 'Y-m-d');
      } else {
        limparInputDataReserva(inputDataFim);
      }

      if (tinhaDataPadraoPassada) {
        atualizarMensagemReservaDoBloco(
          bloco,
          `Datas anteriores a ${formatarDataIsoParaBr(obterDataAtualIsoLocal())} foram bloqueadas. Selecione um novo período.`,
          'erro'
        );
      }

      sincronizarRestricoesDataFimDoBloco(bloco);
      atualizarResumoDisponibilidadeDoBloco(bloco);
    } catch (erro) {
      console.warn('Falha ao carregar calendário avançado. Voltando para o input nativo.', erro);
      bloco.__calendarioOcupacaoErro = 'O calendário avançado não carregou; o campo voltou ao modo simples.';
      configurarFallbackNativoReservaDoBloco(bloco, datasPadrao);
    }
  }

  document.addEventListener('mousedown', (evento) => {
    const dia = evento.target?.closest?.('.flatpickr-day[data-date-iso]');
    if (!dia) return;

    const dataIso = safeStr(dia.dataset.dateIso || '').trim();
    const calendario = dia.closest?.('.flatpickr-calendar');
    const input = calendario?._flatpickr?.input || null;
    const bloco = input?.closest?.('.kb-painel-item') || null;

    if (
      (dataIso && dataIso < obterDataAtualIsoLocal()) ||
      dia.classList.contains('flatpickr-disabled') ||
      dia.classList.contains('kb-dia-indisponivel') ||
      dia.classList.contains('kb-dia-inativo') ||
      (bloco && dataIso && !diaEstaDisponivelNoBloco(bloco, dataIso))
    ) {
      evento.preventDefault();
      evento.stopPropagation();
      return false;
    }
  }, true);


  async function inicializarCalendarioReservaDoBloco(bloco, valoresSalvos = null){
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const inputDataInicio = bloco?.querySelector('[data-role="input-data-inicio"]');
    const inputDataFim = bloco?.querySelector('[data-role="input-data-fim"]');
    if (!selectFace || !inputDataInicio || !inputDataFim) return;

    const codFace = safeStr(selectFace.value || '').trim().toUpperCase();
    const codPonto = safeStr(obterCodPontoManualDoBloco(bloco) || '').trim();
    const dataInicioSalva = safeStr(
      valoresSalvos?.DataInicioReserva ||
      valoresSalvos?.data_inicio_reserva ||
      valoresSalvos?.DataInicio ||
      valoresSalvos?.data_inicio ||
      ''
    ).trim();
    const dataFimSalva = safeStr(
      valoresSalvos?.DataFimReserva ||
      valoresSalvos?.data_fim_reserva ||
      valoresSalvos?.DataFim ||
      valoresSalvos?.data_fim ||
      ''
    ).trim();

    bloco.__datasReservaLiberadasManual = construirSetDatasIntervalo(dataInicioSalva, dataFimSalva);
    bloco.__calendarioOcupacao = null;
    bloco.__calendarioOcupacaoErro = '';
    bloco.__codFaceCalendario = codFace;
    bloco.__codPontoCalendario = codPonto;

    destruirCalendariosReservaDoBloco(bloco);

    inputDataInicio.disabled = true;
    inputDataFim.disabled = true;

    if (!codFace) {
      configurarFallbackNativoReservaDoBloco(bloco, {
        dataInicio: dataInicioSalva,
        dataFim: dataFimSalva,
      });
      atualizarMensagemReservaDoBloco(bloco, 'Selecione uma face para consultar a disponibilidade do calendário.', 'info');
      return;
    }

    atualizarMensagemReservaDoBloco(bloco, 'Carregando disponibilidade da face selecionada...', 'info');

    try {
      const calendario = await carregarCalendarioOcupacaoDaFace(codFace, {
        cod_ponto: codPonto,
        meses: 18,
        cota: obterCotaReservaDoBloco(bloco)
      });

      if (safeStr(selectFace.value || '').trim().toUpperCase() != codFace) {
        return;
      }

      bloco.__calendarioOcupacao = calendario && typeof calendario === 'object' ? calendario : {};
      bloco.__calendarioOcupacaoErro = '';
      await configurarCalendarioReservaNoBloco(bloco, {
        dataInicio: dataInicioSalva,
        dataFim: dataFimSalva,
      });
    } catch (erro) {
      console.warn('Falha ao carregar ocupação da face.', erro);
      bloco.__calendarioOcupacao = null;
      bloco.__calendarioOcupacaoErro = (erro && erro.message)
        ? `${erro.message} O campo voltou ao modo simples até a disponibilidade poder ser consultada.`
        : 'Não foi possível consultar a ocupação desta face. O campo voltou ao modo simples.';

      configurarFallbackNativoReservaDoBloco(bloco, {
        dataInicio: dataInicioSalva,
        dataFim: dataFimSalva,
      });
    }
  }

  function mostrarMensagemBoard(texto, tipo = "erro", esconderAposMs = 0){
    if (!msgBoard) return;

    const mensagem = safeStr(texto).trim();
    window.clearTimeout(mostrarMensagemBoard._timer);

    if (!mensagem) {
      msgBoard.textContent = "";
      msgBoard.style.display = "none";
      msgBoard.style.color = "";
      msgBoard.style.border = "";
      msgBoard.style.background = "";
      msgBoard.style.padding = "";
      msgBoard.style.borderRadius = "";
      msgBoard.style.fontWeight = "";
      return;
    }

    msgBoard.textContent = mensagem;
    msgBoard.style.display = "block";
    msgBoard.style.padding = "10px 12px";
    msgBoard.style.borderRadius = "12px";
    msgBoard.style.fontWeight = "900";

    if (tipo === "sucesso") {
      msgBoard.style.color = "#166534";
      msgBoard.style.border = "1px solid rgba(34,197,94,.28)";
      msgBoard.style.background = "rgba(34,197,94,.08)";
    } else {
      msgBoard.style.color = "#991b1b";
      msgBoard.style.border = "1px solid rgba(239,68,68,.28)";
      msgBoard.style.background = "rgba(239,68,68,.08)";
    }

    if (esconderAposMs > 0) {
      mostrarMensagemBoard._timer = window.setTimeout(() => {
        if (msgBoard.textContent === mensagem) {
          limparMensagemBoard();
        }
      }, esconderAposMs);
    }
  }

  function limparMensagemBoard(){
    mostrarMensagemBoard("", "erro", 0);
  }

  function mensagemErroHttp(resposta, corpo, fallback){
    if (corpo && typeof corpo === "object") {
      const msg = corpo.msg || corpo.erro || corpo.message || corpo.detail;
      if (msg) return String(msg);
    }
    return fallback || `Erro (HTTP ${resposta?.status || 0})`;
  }

  function carregarScriptExterno(src){
    return new Promise((resolve, reject) => {
      const existente = [...document.scripts].find(s => s.src === src);
      if (existente) {
        if (typeof window.io === "function") {
          resolve(true);
        } else {
          existente.addEventListener("load", () => resolve(true), {once: true});
          existente.addEventListener("error", () => reject(new Error(`Falha ao carregar ${src}`)), {once: true});
        }
        return;
      }

      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = () => resolve(true);
      script.onerror = () => reject(new Error(`Falha ao carregar ${src}`));
      document.head.appendChild(script);
    });
  }



  async function garantirClienteSocketIo(){
  if (typeof window.io === "function") return true;

  const fontes = [
    "https://cdn.socket.io/4.7.5/socket.io.min.js",
    "https://cdn.jsdelivr.net/npm/socket.io-client@4.7.5/dist/socket.io.min.js"
  ];

  for (const src of fontes) {
    try {
      await carregarScriptExterno(src);
      if (typeof window.io === "function") return true;
    } catch (erro) {
      console.warn("Falha ao carregar cliente Socket.IO", { src, erro: String(erro) });
    }
  }

  return typeof window.io === "function";
}



  function normalizarTexto(v){
    return safeStr(v)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function derivarIdTipoClienteDescontoDoCard(card){
    const c = card || {};
    if (idNum(c.IDDimKanbanTipoClienteDesconto || 0)) return idNum(c.IDDimKanbanTipoClienteDesconto || 0);
    if (idNum(c.IDDimTipoCliente || 0)) return idNum(c.IDDimTipoCliente || 0);
    if (idNum(c.BitPlanejador || 0)) return 1;
    if (idNum(c.BitClienteDireto || 0)) return 2;
    if (idNum(c.BitAgencia || 0)) return 3;
    return null;
  }

  function nomeTipoClienteDescontoPorId(idTipo){
    const id = idNum(idTipo || 0);
    if (!id) return "";
    if (tiposClienteDescontoPorId.has(id)) {
      return safeStr(tiposClienteDescontoPorId.get(id)?.TipoCliente || "").trim();
    }
    return ({ 1: "Planejador de Mídia", 2: "Cliente Direto", 3: "Agência de Publicidade", 4: "Bureau" })[id] || "";
  }

  function montarBitsTipoClienteDesconto(idTipo){
    const id = idNum(idTipo || 0);
    return {
      BitPlanejador: id === 1 ? 1 : 0,
      BitClienteDireto: id === 2 ? 1 : 0,
      BitAgencia: id === 3 ? 1 : 0,
    };
  }

  function obterConfigEmpresasRelacionadasPorTipo(idTipo){
    const id = idNum(idTipo || 0);

    if (id === ID_TIPO_CLIENTE_DIRETO) {
      return {
        labelEmpresaPrincipal: "Cliente Direto",
        mostrarWrap: false,
        mostrarAgencia: false,
        mostrarClienteDireto: false,
        mostrarBureau: false,
        mostrarIntermediario: false,
      };
    }

    if (id === 3) {
      return {
        labelEmpresaPrincipal: "Agência de Publicidade",
        mostrarWrap: false,
        mostrarAgencia: false,
        mostrarClienteDireto: false,
        mostrarBureau: false,
        mostrarIntermediario: false,
      };
    }

    if (id === 4) {
      return {
        labelEmpresaPrincipal: "Bureau",
        mostrarWrap: false,
        mostrarAgencia: false,
        mostrarClienteDireto: false,
        mostrarBureau: false,
        mostrarIntermediario: false,
      };
    }

    if (id === 1) {
      return {
        labelEmpresaPrincipal: "Planejador de Mídia",
        mostrarWrap: false,
        mostrarAgencia: true,
        mostrarClienteDireto: false,
        mostrarBureau: true,
        mostrarIntermediario: true,
      };
    }

    return {
      labelEmpresaPrincipal: "Nome Empresa",
      mostrarWrap: false,
      mostrarAgencia: false,
      mostrarClienteDireto: false,
      mostrarBureau: false,
      mostrarIntermediario: false,
    };
  }

  function montarEmpresaPrincipalDoCardParaCatalogo(card){
    const c = card || {};
    const idEmpresa = idNum(
      c.IDEmpresa ??
      c.IDEmpresaRelacionadaCard ??
      c.IDCliente ??
      c.IDEmpresaRelacionada ??
      0
    );

    if (!idEmpresa) return null;

    return {
      IDEmpresa: idEmpresa,
      ID: idEmpresa,
      RazaoSocial: safeStr(
        c.EmpresaRazaoSocial ||
        c.RazaoSocial ||
        c.NomeEmpresa ||
        c.EmpresaNomeFantasia ||
        c.NomeFantasia ||
        c.MarcaExibida ||
        c.Marca ||
        c.Titulo ||
        ""
      ).trim() || null,
      NomeFantasia: safeStr(c.EmpresaNomeFantasia || c.NomeFantasia || c.MarcaExibida || c.Marca || "").trim() || null,
      CNPJ: safeStr(c.EmpresaCNPJ || c.CNPJ || c.cnpj || "").trim() || null,
      CNAE: safeStr(c.EmpresaCNAE || c.CNAE || "").trim() || null,
      Classe: safeStr(c.EmpresaClasse || c.SegmentoClasse || c.Classe || "").trim() || null,
      Setor: safeStr(c.EmpresaSetor || c.SegmentoSetor || c.Setor || "").trim() || null,
      IDDimOrigemAtendimento: idNum(c.IDDimOrigemAtendimento || 0) || null
    };
  }

  function registrarEmpresaPrincipalDoCardNoCatalogo(card){
    const empresa = montarEmpresaPrincipalDoCardParaCatalogo(card);
    if (!empresa) return null;

    registrarEmpresasNoCatalogo([empresa]);
    garantirOpcaoEmpresaNoSelect(empresa.IDEmpresa);
    return obterEmpresaCatalogoPorId(empresa.IDEmpresa) || empresa;
  }

  function obterEmpresasRelacionadasDoCard(card){
    const c = card || {};
    const idTipo = derivarIdTipoClienteDescontoDoCard(c);
    const idEmpresa = idNum(c.IDEmpresa ?? c.IDEmpresaRelacionadaCard ?? 0) || null;
    const idAgencia = idNum(c.IDEmpresaAgencia ?? 0) || null;
    const idBureau = idNum(c.IDEmpresaBureau ?? 0) || null;
    const idIntermediario = idNum(c.IDEmpresaIntermediario ?? 0) || null;

    if (idTipo === 3) {
      return {
        principal: idEmpresa || idAgencia,
        agencia: null,
        clienteDireto: null,
        bureau: null,
        intermediario: null,
      };
    }

    if (idTipo === 4) {
      return {
        principal: idEmpresa || idBureau,
        agencia: null,
        clienteDireto: null,
        bureau: null,
        intermediario: null,
      };
    }

    return {
      principal: idEmpresa,
      agencia: idAgencia,
      clienteDireto: null,
      bureau: idBureau,
      intermediario: idIntermediario,
    };
  }

  function validarEmpresasRelacionadasFase4Formulario(idTipo, idFaseAtual){
    if (idNum(idFaseAtual || 0) !== 4) {
      return { ok: true };
    }

    const id = idNum(idTipo || 0);
    const principal = idNum(selectEmpresaCard?.value || 0) || null;

    if (!id) {
      return { ok: false, msg: "Na fase 4, o Tipo de cliente é obrigatório." };
    }

    if (!principal) {
      return { ok: false, msg: "Na fase 4, informe a empresa principal antes de salvar." };
    }

    return { ok: true };
  }

  function obterTemaTipoClienteDesconto(cardOuId){
    const idTipo = typeof cardOuId === "object"
      ? derivarIdTipoClienteDescontoDoCard(cardOuId)
      : idNum(cardOuId || 0);

    if (idTipo === 1) {
      return {
        bg: "rgba(37,99,235,.14)",
        fg: "#1D4ED8",
        bd: "rgba(37,99,235,.28)"
      };
    }

    if (idTipo === 2) {
      return {
        bg: "rgba(249,115,22,.14)",
        fg: "#C2410C",
        bd: "rgba(249,115,22,.30)"
      };
    }

    if (idTipo === 3) {
      return {
        bg: "rgba(168,85,247,.14)",
        fg: "#7E22CE",
        bd: "rgba(168,85,247,.30)"
      };
    }

    return {
      bg: "rgba(15,23,42,.05)",
      fg: "rgba(15,23,42,.86)",
      bd: "rgba(15,23,42,.10)"
    };
  }

  function nomeTipoClienteDescontoDoCard(card){
    const nomeDireto = safeStr(card?.TipoClienteDesconto || card?.tipoClienteDesconto || "").trim();
    if (nomeDireto) return nomeDireto;
    return nomeTipoClienteDescontoPorId(derivarIdTipoClienteDescontoDoCard(card));
  }


function derivarIdOrigemAtendimentoDoCard(card){
  const c = card || {};
  if (idNum(c.IDDimOrigemAtendimento || 0)) return idNum(c.IDDimOrigemAtendimento || 0);
  if (idNum(c.IDOrigemAtendimento || 0)) return idNum(c.IDOrigemAtendimento || 0);
  return null;
}

function nomeOrigemAtendimentoPorId(idOrigem){
  const id = idNum(idOrigem || 0);
  if (!id) return "";
  if (origensAtendimentoPorId.has(id)) {
    return safeStr(origensAtendimentoPorId.get(id)?.NomeOrigemAtendimento || "").trim();
  }
  return "";
}

function nomeOrigemAtendimentoDoCard(card){
  const nomeDireto = safeStr(
    card?.OrigemAtendimento ||
    card?.NomeOrigemAtendimento ||
    card?.origemAtendimento ||
    ""
  ).trim();
  if (nomeDireto) return nomeDireto;
  return nomeOrigemAtendimentoPorId(derivarIdOrigemAtendimentoDoCard(card));
}

function obterTemaOrigemAtendimento(cardOuIdOuNome){
  let nomeBase = "";
  if (typeof cardOuIdOuNome === "object" && cardOuIdOuNome !== null) {
    nomeBase = nomeOrigemAtendimentoDoCard(cardOuIdOuNome);
  } else if (typeof cardOuIdOuNome === "string") {
    nomeBase = safeStr(cardOuIdOuNome).trim();
  } else {
    nomeBase = nomeOrigemAtendimentoPorId(idNum(cardOuIdOuNome || 0));
  }

  const chave = normalizarTexto(nomeBase);

  if (chave === "receptivo") {
    return {
      bg: "rgba(37,99,235,.14)",
      fg: "#1D4ED8",
      bd: "rgba(37,99,235,.28)"
    };
  }

  if (chave === "carteira") {
    return {
      bg: "rgba(168,85,247,.14)",
      fg: "#7E22CE",
      bd: "rgba(168,85,247,.30)"
    };
  }

  if (chave === "prospeccao") {
    return {
      bg: "rgba(250,204,21,.18)",
      fg: "#A16207",
      bd: "rgba(250,204,21,.34)"
    };
  }

  if (chave === "indicacao") {
    return {
      bg: "rgba(34,197,94,.14)",
      fg: "#15803D",
      bd: "rgba(34,197,94,.30)"
    };
  }

  return {
    bg: "rgba(15,23,42,.05)",
    fg: "rgba(15,23,42,.86)",
    bd: "rgba(15,23,42,.10)"
  };
}



  function atualizarCabecalhoModalCard(card){
    const cardNorm = normalizarCardServidor(card || {});
    const idCard = idNum(cardNorm.IDFatoKanbanCard || 0);
    const idTipoCliente = derivarIdTipoClienteDescontoDoCard(cardNorm);
    const nomeTipoCliente = nomeTipoClienteDescontoDoCard(cardNorm);
    const temaTipoCliente = obterTemaTipoClienteDesconto(idTipoCliente);
    const idOrigemAtendimento = derivarIdOrigemAtendimentoDoCard(cardNorm);
    const nomeOrigemAtendimento = nomeOrigemAtendimentoDoCard(cardNorm);
    const temaOrigemAtendimento = obterTemaOrigemAtendimento(idOrigemAtendimento || nomeOrigemAtendimento);

    if (cardIdVisual) {
      cardIdVisual.textContent = idCard ? `#${idCard}` : "#—";
      cardIdVisual.title = idCard ? `IDFatoKanbanCard ${idCard}` : "IDFatoKanbanCard";
    }

    if (cardTipoClienteBadgeVisual) {
      if (nomeTipoCliente) {
        cardTipoClienteBadgeVisual.hidden = false;
        cardTipoClienteBadgeVisual.textContent = nomeTipoCliente;
        cardTipoClienteBadgeVisual.title = nomeTipoCliente;
        cardTipoClienteBadgeVisual.style.setProperty("--tipo-cliente-bg", temaTipoCliente.bg);
        cardTipoClienteBadgeVisual.style.setProperty("--tipo-cliente-fg", temaTipoCliente.fg);
        cardTipoClienteBadgeVisual.style.setProperty("--tipo-cliente-bd", temaTipoCliente.bd);
      } else {
        cardTipoClienteBadgeVisual.hidden = true;
        cardTipoClienteBadgeVisual.textContent = "";
      }
    }

    if (cardOrigemAtendimentoBadgeVisual) {
      if (nomeOrigemAtendimento) {
        cardOrigemAtendimentoBadgeVisual.hidden = false;
        cardOrigemAtendimentoBadgeVisual.textContent = nomeOrigemAtendimento;
        cardOrigemAtendimentoBadgeVisual.title = nomeOrigemAtendimento;
        cardOrigemAtendimentoBadgeVisual.style.setProperty("--tipo-cliente-bg", temaOrigemAtendimento.bg);
        cardOrigemAtendimentoBadgeVisual.style.setProperty("--tipo-cliente-fg", temaOrigemAtendimento.fg);
        cardOrigemAtendimentoBadgeVisual.style.setProperty("--tipo-cliente-bd", temaOrigemAtendimento.bd);
      } else {
        cardOrigemAtendimentoBadgeVisual.hidden = true;
        cardOrigemAtendimentoBadgeVisual.textContent = "";
      }
    }
  }


function normalizarCardServidor(card){
  const c = Object.assign({}, card || {});
  c.IDFatoKanbanCard = idNum(c.IDFatoKanbanCard);
  c.IDDimKanban = idNum(c.IDDimKanban || ID_KANBAN);
  c.IDDimKanbanFaseAtual = idNum(c.IDDimKanbanFaseAtual);
  c.IDEmpresaProprietaria =
    c.IDEmpresaProprietaria === null || c.IDEmpresaProprietaria === undefined
      ? null
      : idNum(c.IDEmpresaProprietaria);

  const idEmpRelacionadaBruto =
    c.IDEmpresaRelacionadaCard ??
    c.IDCliente ??
    c.IDEmpresaRelacionada ??
    c.IDEmpresa ??
    null;

  c.IDEmpresaRelacionadaCard =
    idEmpRelacionadaBruto === null ||
    idEmpRelacionadaBruto === undefined ||
    idEmpRelacionadaBruto === ""
      ? null
      : idNum(idEmpRelacionadaBruto);

  c.IDEmpresa =
    c.IDEmpresa === null ||
    c.IDEmpresa === undefined ||
    c.IDEmpresa === ""
      ? c.IDEmpresaRelacionadaCard
      : idNum(c.IDEmpresa);

  if (!c.IDEmpresa && c.IDEmpresaRelacionadaCard) {
    c.IDEmpresa = c.IDEmpresaRelacionadaCard;
  }

  const idReservaBruto =
    c.IDReserva ??
    c.id_reserva ??
    c.Reserva ??
    c.reserva ??
    null;

  c.IDReserva =
    idReservaBruto === null ||
    idReservaBruto === undefined ||
    idReservaBruto === ""
      ? null
      : idNum(idReservaBruto);
  c.id_reserva = c.IDReserva;

  for (const nomeCampoEmpresaRelacionada of ["IDEmpresaAgencia", "IDEmpresaBureau", "IDEmpresaIntermediario"]) {
    c[nomeCampoEmpresaRelacionada] =
      c[nomeCampoEmpresaRelacionada] === null ||
      c[nomeCampoEmpresaRelacionada] === undefined ||
      c[nomeCampoEmpresaRelacionada] === ""
        ? null
        : idNum(c[nomeCampoEmpresaRelacionada]);
  }

  const versaoHexBruta = safeStr(
    c.VersaoConcorrenciaHex ??
    c.versao_concorrencia ??
    c.versaoConcorrencia ??
    c.VersaoConcorrencia ??
    ""
  ).trim();

  c.VersaoConcorrenciaHex = versaoHexBruta
    .replace(/^0x/i, "")
    .replace(/[^0-9a-fA-F]/g, "")
    .toUpperCase();

  c.VersaoConcorrencia = c.VersaoConcorrenciaHex;
  c.versaoConcorrencia = c.VersaoConcorrenciaHex;
  c.versao_concorrencia = c.VersaoConcorrenciaHex;

  c.IDDimOrigemAtendimento =
    c.IDDimOrigemAtendimento === null || c.IDDimOrigemAtendimento === undefined || c.IDDimOrigemAtendimento === ""
      ? null
      : idNum(c.IDDimOrigemAtendimento);

  c.IDVendedor =
    c.IDVendedor === null || c.IDVendedor === undefined || c.IDVendedor === ""
      ? null
      : idNum(c.IDVendedor);

  c.IDVendedorUsuario =
    c.IDVendedorUsuario === null || c.IDVendedorUsuario === undefined || c.IDVendedorUsuario === ""
      ? null
      : idNum(c.IDVendedorUsuario);

  c.IDDimUsuarios =
    c.IDDimUsuarios === null || c.IDDimUsuarios === undefined || c.IDDimUsuarios === ""
      ? null
      : idNum(c.IDDimUsuarios);

  c.IDUsuarioRelacionadoCard =
    c.IDUsuarioRelacionadoCard === null || c.IDUsuarioRelacionadoCard === undefined || c.IDUsuarioRelacionadoCard === ""
      ? null
      : idNum(c.IDUsuarioRelacionadoCard);

  c.OrigemAtendimento = safeStr(
    c.OrigemAtendimento ??
    c.NomeOrigemAtendimento ??
    c.origemAtendimento ??
    ""
  ).trim();

  c.NomeUsuarioResponsavel = safeStr(
    c.NomeUsuarioResponsavel ??
    c.nome_usuario_responsavel ??
    c.NomeUsuario ??
    ""
  ).trim();

  const idContratoBruto =
    c.IDFatoControleContratosEuromidia ??
    c.IDFatoControleContratoEuromidia ??
    c.id_contrato_existente ??
    c.id_controle_contrato ??
    null;

  c.IDFatoControleContratosEuromidia =
    idContratoBruto === null || idContratoBruto === undefined || idContratoBruto === ""
      ? null
      : idNum(idContratoBruto);

  c.IDFatoControleContratoEuromidia = c.IDFatoControleContratosEuromidia;

  c.CodPontoContrato = safeStr(
    c.CodPontoContrato ??
    c.cod_ponto_contrato ??
    ""
  ).trim() || null;

  c.cod_ponto_contrato = c.CodPontoContrato;

  c.CodFaceContrato = safeStr(
    c.CodFaceContrato ??
    c.cod_face_contrato ??
    ""
  ).trim().toUpperCase() || null;

  c.cod_face_contrato = c.CodFaceContrato;

  c.BitAditivo = idNum(c.BitAditivo || 0) ? 1 : 0;
  c.BitContratoNovo = idNum(c.BitContratoNovo || 0) ? 1 : 0;

  const tipoContratoBruto = safeStr(
    c.tipo_contrato ??
    c.tipoContrato ??
    c.tipo_contrato_card ??
    c.TipoSolicitacao ??
    ""
  ).trim().toUpperCase();

  if (tipoContratoBruto === VALOR_MODO_CONTRATO_ADITIVO || tipoContratoBruto === "ADITIVO") {
    c.tipo_contrato = VALOR_MODO_CONTRATO_ADITIVO;
  } else if (
    tipoContratoBruto === VALOR_MODO_CONTRATO_NOVO ||
    tipoContratoBruto === "NOVO CONTRATO" ||
    tipoContratoBruto === "NOVO_CONTRATO"
  ) {
    c.tipo_contrato = VALOR_MODO_CONTRATO_NOVO;
  } else if (c.BitAditivo) {
    c.tipo_contrato = VALOR_MODO_CONTRATO_ADITIVO;
  } else if (c.BitContratoNovo) {
    c.tipo_contrato = VALOR_MODO_CONTRATO_NOVO;
  } else {
    c.tipo_contrato = c.IDFatoControleContratosEuromidia
      ? VALOR_MODO_CONTRATO_ADITIVO
      : VALOR_MODO_CONTRATO_NOVO;
  }

  c.BitClienteDireto = idNum(c.BitClienteDireto || 0) ? 1 : 0;
  c.BitAgencia = idNum(c.BitAgencia || 0) ? 1 : 0;
  c.BitPlanejador = idNum(c.BitPlanejador || 0) ? 1 : 0;
  c.IDDimKanbanTipoClienteDesconto = derivarIdTipoClienteDescontoDoCard(c);
  c.IDDimTipoCliente = c.IDDimKanbanTipoClienteDesconto;
  Object.assign(c, montarBitsTipoClienteDesconto(c.IDDimKanbanTipoClienteDesconto));
  c.TipoClienteDesconto = nomeTipoClienteDescontoDoCard(c);

  c.QuantidadePaineisVinculados = idNum(c.QuantidadePaineisVinculados || 0);
  c.QuantidadePaineisUnicos = idNum(c.QuantidadePaineisUnicos || 0);

  const valorTotalPaineisBruto =
    c.ValorTotalPaineis ??
    c.valor_total_paineis ??
    0;

  c.ValorTotalPaineis = Number(valorTotalPaineisBruto || 0);
  if (!Number.isFinite(c.ValorTotalPaineis)) {
    c.ValorTotalPaineis = 0;
  }

  c.IDEmpresaAgencia =
    c.IDEmpresaAgencia === null || c.IDEmpresaAgencia === undefined || c.IDEmpresaAgencia === ""
      ? null
      : idNum(c.IDEmpresaAgencia);

  c.Marca = safeStr(c.Marca ?? c.marca ?? "").trim();
  c.Telefone = safeStr(c.Telefone ?? c.telefone ?? "").replace(/\D+/g, "").trim();
  c.Email = safeStr(c.Email ?? c.email ?? "").trim();

  return c;
}






  function tagsDoCard(cardId) {
    return mapaTagsPorCard.get(idNum(cardId)) || [];
  }





  function setTagsDoCard(cardId, tags, opcoes = {}){
    const id = idNum(cardId);
    if (!id) return;

    mapaTagsPorCard.set(
      id,
      Array.isArray(tags) ? tags.map(t => Object.assign({}, t || {})) : []
    );

    atualizarTagsFiltroCardPorId(id, tags);

    if (opcoes.reconstruir !== false) {
      reconstruirIndicesCards();
    }
  }

  function removerTagsDoCard(cardId, opcoes = {}){
    const id = idNum(cardId);
    if (!id) return;

    mapaTagsPorCard.delete(id);
    atualizarTagsFiltroCardPorId(id, []);

    if (opcoes.reconstruir !== false) {
      reconstruirIndicesCards();
    }
  }

  function normalizaCnpj(v){
    const s = safeStr(v).replace(/\D+/g, "");
    if (!s) return "";
    return s;
  }

  function mascaraCnpj(v){
    const s = normalizaCnpj(v);
    if (s.length !== 14) return s;
    return `${s.slice(0,2)}.${s.slice(2,5)}.${s.slice(5,8)}/${s.slice(8,12)}-${s.slice(12,14)}`;
  }

  function normalizarCorHex(cor){
    let hex = safeStr(cor).trim();
    if (!hex) return "";

    if (!hex.startsWith("#")) hex = `#${hex}`;

    if (!/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(hex)) return "";

    if (hex.length === 4) {
      hex = `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}`;
    }

    return hex.toUpperCase();
  }

  function hexParaRgb(hex){
    const cor = normalizarCorHex(hex);
    if (!cor) return null;

    return {
      r: parseInt(cor.slice(1, 3), 16),
      g: parseInt(cor.slice(3, 5), 16),
      b: parseInt(cor.slice(5, 7), 16)
    };
  }

  function canalSrgbParaLinear(valor){
    const canal = valor / 255;
    return canal <= 0.03928 ? canal / 12.92 : ((canal + 0.055) / 1.055) ** 2.4;
  }

  function luminanciaRelativa(rgb){
    if (!rgb) return 1;

    const r = canalSrgbParaLinear(rgb.r);
    const g = canalSrgbParaLinear(rgb.g);
    const b = canalSrgbParaLinear(rgb.b);

    return (0.2126 * r) + (0.7152 * g) + (0.0722 * b);
  }

  function corTextoPorFundo(hex){
    const rgb = hexParaRgb(hex);
    return luminanciaRelativa(rgb) > 0.56 ? "#0F172A" : "#FFFFFF";
  }

  function escurecerCor(hex, fator = 0.22){
    const rgb = hexParaRgb(hex);
    if (!rgb) return "rgba(15,23,42,.14)";

    const reduzir = (canal) => Math.max(0, Math.min(255, Math.round(canal * (1 - fator))));

    const r = reduzir(rgb.r).toString(16).padStart(2, "0");
    const g = reduzir(rgb.g).toString(16).padStart(2, "0");
    const b = reduzir(rgb.b).toString(16).padStart(2, "0");

    return `#${r}${g}${b}`.toUpperCase();
  }

  function estiloTag(corHex){
    const corBase = normalizarCorHex(corHex);
    if (!corBase) return "";

    const corTexto = corTextoPorFundo(corBase);
    const corBorda = escurecerCor(corBase, 0.22);
    return `background:${corBase}; color:${corTexto}; border-color:${corBorda};`;
  }

  function obterTextoFaceCard(card){
    const nomeFace =
      safeStr(card?.NomeFace || card?.Face || card?.DescricaoFace || card?.LabelFace || "");

    const codFace =
      safeStr(card?.CodFace || card?.CodigoFace || "");

    if (nomeFace && codFace) return `${nomeFace} ${codFace}`;
    if (nomeFace) return nomeFace;
    if (codFace) return codFace;
    return "";
  }

  function nomeVendedorDoCard(card){
    return safeStr(
      card?.NomeUsuarioResponsavel ||
      card?.nome_usuario_responsavel ||
      card?.NomeUsuario ||
      ""
    ).trim();
  }

  function nomesTagsDoCard(card){
    return tagsDoCard(card?.IDFatoKanbanCard)
      .map(tag => safeStr(tag?.NomeTag).trim())
      .filter(Boolean);
  }

  function tagAfetaCorDoCard(tag){
    const nomeTag = normalizarTexto(tag?.NomeTag || tag?.nomeTag || "");
    const idTag = idNum(tag?.IDDimKanbanTag || tag?.id_dim_kanban_tag || tag?.id_tag || 0);
    return idTag === 4 || idNum(tag?.AfetaCorCard || 0) === 1 || nomeTag === "aprovacao desconto";
  }

  function obterCorEspecialDoCard(card){
    const tagEspecial = tagsDoCard(card?.IDFatoKanbanCard).find(tagAfetaCorDoCard);
    if (!tagEspecial) return "";
    return normalizarCorHex(tagEspecial?.CorHex || "") || "#DC2626";
  }

  function textoBuscaDoCard(card){
    const idFase = idNum(card?.IDDimKanbanFaseAtual || card?.id_fase_atual || 0);
    const fase = fasePorId(idFase);

    return [
      safeStr(card?.IDFatoKanbanCard),
      safeStr(card?.Titulo),
      safeStr(card?.Descricao),
      safeStr(card?.EmpresaRazaoSocial),
      safeStr(card?.EmpresaCNPJ),
      safeStr(fase?.NomeFase),
      obterTextoFaceCard(card),
      nomeVendedorDoCard(card),
      nomesTagsDoCard(card).join(" ")
    ].join(" ");
  }

  function cardPassaBusca(card){
    const termo = normalizarTexto(termoBusca);
    if (!termo) return true;

    const base = normalizarTexto(textoBuscaDoCard(card));
    return base.includes(termo);
  }

  function cardPertenceAoVendedorLogado(card){
    if (!USUARIO_EH_VENDEDOR) return true;

    const idVendedorCard = idNum(card?.IDVendedor || card?.id_vendedor || 0);
    const idUsuarioCard = idNum(
      card?.IDUsuarioRelacionadoCard ||
      card?.IDVendedorUsuario ||
      card?.IDDimUsuarios ||
      card?.id_usuario_relacionado ||
      0
    );

    if (ID_VENDEDOR_LOGADO && idVendedorCard === ID_VENDEDOR_LOGADO) return true;
    if (ID_USUARIO_LOGADO && idUsuarioCard === ID_USUARIO_LOGADO) return true;

    return false;
  }

  function cardPassaFiltroVendedor(card){
    if (USUARIO_EH_VENDEDOR && !cardPertenceAoVendedorLogado(card)) return false;
    if (USUARIO_EH_VENDEDOR) return true;

    if (!vendedoresSelecionados.size) return true;
    const nome = normalizarTexto(nomeVendedorDoCard(card));
    return !!nome && vendedoresSelecionados.has(nome);
  }

  function cardPassaFiltroTag(card){
    if (!tagsSelecionadasFiltro.size) return true;
    const tagsCard = nomesTagsDoCard(card).map(normalizarTexto).filter(Boolean);
    return tagsCard.some(tag => tagsSelecionadasFiltro.has(tag));
  }

  function cardPassaFiltros(card){
    return cardPassaBusca(card) && cardPassaFiltroVendedor(card) && cardPassaFiltroTag(card);
  }

  function haFiltroAtivo(){
    return !!safeStr(termoBusca).trim() || vendedoresSelecionados.size > 0 || tagsSelecionadasFiltro.size > 0;
  }

  function fasePorId(idFase){
    const id = idNum(idFase);
    return fases.find(f => idNum(f.IDDimKanbanFase) === id) || null;
  }

  function faseEhFinalDoQuadro(idFase){
    const fase = fasePorId(idFase);
    if (!fase) return false;

    const nomeFase = normalizarTexto(fase?.NomeFase || "");
    const tipoFase = safeStr(fase?.TipoFase || "").trim().toUpperCase();

    return nomeFase === "concluido" || tipoFase === "SUCESSO";
  }

  function statusCardEhFinal(statusCard){
    const codigo = safeStr(statusCard || "").trim().toUpperCase();
    return codigo === "CONCLUIDO" || codigo === "CANCELADO";
  }

  function cardDeveSairDoQuadro(card, idFaseSobrescrita = null){
    if (!card) return false;

    const idFaseAtual = idNum(
      idFaseSobrescrita != null
        ? idFaseSobrescrita
        : (card.IDDimKanbanFaseAtual || card.id_fase_atual || 0)
    );

    return faseEhFinalDoQuadro(idFaseAtual) || statusCardEhFinal(card.StatusCard || card.status_card);
  }

  function reconstruirIndicesCards(){
    const porId = new Map();
    const porFase = new Map();
    const visiveisPorFase = new Map();
    const fasesAtivas = new Set(
      (Array.isArray(fases) ? fases : [])
        .map(fase => idNum(fase?.IDDimKanbanFase || 0))
        .filter(Boolean)
    );
    let totalVisiveis = 0;

    fasesAtivas.forEach(idFase => {
      porFase.set(idFase, []);
      visiveisPorFase.set(idFase, []);
    });

    const lista = Array.isArray(cards) ? cards : [];

    for (let i = 0; i < lista.length; i += 1) {
      const card = lista[i];
      const idCard = idNum(card?.IDFatoKanbanCard || 0);
      const idFase = idNum(card?.IDDimKanbanFaseAtual || card?.id_fase_atual || 0);

      if (!idCard || !idFase) continue;
      if (fasesAtivas.size && !fasesAtivas.has(idFase)) continue;

      porId.set(idCard, card);

      if (!porFase.has(idFase)) {
        porFase.set(idFase, []);
      }
      porFase.get(idFase).push(card);

      if (!cardPassaFiltros(card)) continue;

      if (!visiveisPorFase.has(idFase)) {
        visiveisPorFase.set(idFase, []);
      }
      visiveisPorFase.get(idFase).push(card);
      totalVisiveis += 1;
    }

    indiceCardsPorId = porId;
    indiceCardsPorFase = porFase;
    indiceCardsVisiveisPorFase = visiveisPorFase;
    totalCardsVisiveisCache = totalVisiveis;
  }

  function listaCardsDaFase(idFase, considerarFiltros = true){
    const mapa = considerarFiltros ? indiceCardsVisiveisPorFase : indiceCardsPorFase;
    return mapa.get(idNum(idFase)) || [];
  }

  function contarCardsNaFaseCarregados(idFase){
    return (indiceCardsPorFase.get(idNum(idFase)) || []).length;
  }

  function contarCardsNaFaseVisiveis(idFase){
    return (indiceCardsVisiveisPorFase.get(idNum(idFase)) || []).length;
  }

  function contarCardsVisiveis(){
    return totalCardsVisiveisCache;
  }

  function totalServidorDoQuadro(){
    return fases.reduce((acc, f) => acc + idNum(f.QuantidadeCardsTotal || 0), 0);
  }

  function contarCardsRenderizadosNoEstado(){
    let total = 0;
    estadoFase.forEach(st => {
      total += idNum(st?.cardsNoDom || st?.rendered || 0);
    });
    return total;
  }

  function atualizarModoDesempenhoKanban(){
    if (!board) return;

    const totalDom = contarCardsRenderizadosNoEstado();
    const totalCarregado = Array.isArray(cards) ? cards.length : 0;
    const ativar = totalDom >= LIMITE_CARDS_DOM_MODO_DESEMPENHO
      || totalCarregado >= LIMITE_CARDS_CARREGADOS_MODO_DESEMPENHO;

    board.classList.toggle("kb-performance-mode", ativar);
    board.dataset.cardsDom = String(totalDom);
    board.dataset.cardsCarregados = String(totalCarregado);
  }

  function resumoTextoSelecao(quantidade, singular, plural, padrao){
    if (!quantidade) return padrao;
    if (quantidade === 1) return `1 ${singular}`;
    return `${quantidade} ${plural}`;
  }

  function normalizarTagFiltroKanban(tag){
    const t = Object.assign({}, tag || {});
    return {
      IDDimKanbanTag: idNum(t.IDDimKanbanTag || t.id_dim_kanban_tag || t.id_tag || 0) || null,
      NomeTag: safeStr(t.NomeTag || t.nome_tag || t.nome || "").trim(),
      CorHex: normalizarCorHex(t.CorHex || t.cor_hex || t.cor || ""),
      Icone: safeStr(t.Icone || t.icone || "").trim(),
      AfetaCorCard: idNum(t.AfetaCorCard || t.afeta_cor_card || 0) === 1 || t.AfetaCorCard === true
    };
  }

  function normalizarFiltroCardKanban(item){
    const base = Object.assign({}, item || {});
    const idCard = idNum(base.IDFatoKanbanCard || base.id_card || 0);
    const tagsBase = Array.isArray(base.Tags)
      ? base.Tags
      : (Array.isArray(base.tags) ? base.tags : tagsDoCard(idCard));

    return {
      IDFatoKanbanCard: idCard,
      IDVendedor: idNum(base.IDVendedor || base.id_vendedor || 0) || null,
      IDUsuarioRelacionadoCard: idNum(base.IDUsuarioRelacionadoCard || base.IDDimUsuarios || base.id_usuario_relacionado || 0) || null,
      NomeUsuarioResponsavel: safeStr(
        base.NomeUsuarioResponsavel ||
        base.NomeUsuario ||
        base.NomeVendedor ||
        base.nome_usuario_responsavel ||
        ""
      ).trim(),
      Tags: (Array.isArray(tagsBase) ? tagsBase : [])
        .map(normalizarTagFiltroKanban)
        .filter(tag => tag.IDDimKanbanTag || tag.NomeTag)
    };
  }

  function atualizarFiltroCardLocal(card, tagsForcadas = null){
    const cardNorm = normalizarCardServidor(card || {});
    const idCard = idNum(cardNorm.IDFatoKanbanCard || 0);
    if (!idCard) return;

    const atual = filtrosCardsKanban.find(item => idNum(item.IDFatoKanbanCard) === idCard) || {};
    const tagsBase = Array.isArray(tagsForcadas)
      ? tagsForcadas
      : (Array.isArray(atual.Tags) ? atual.Tags : tagsDoCard(idCard));

    const atualizado = normalizarFiltroCardKanban(Object.assign({}, atual, cardNorm, { Tags: tagsBase }));
    const idx = filtrosCardsKanban.findIndex(item => idNum(item.IDFatoKanbanCard) === idCard);

    if (idx >= 0) {
      filtrosCardsKanban[idx] = atualizado;
    } else {
      filtrosCardsKanban.push(atualizado);
    }
  }

  function atualizarTagsFiltroCardPorId(cardId, tags){
    const idCard = idNum(cardId);
    if (!idCard) return;

    const idx = filtrosCardsKanban.findIndex(item => idNum(item.IDFatoKanbanCard) === idCard);
    const tagsNormalizadas = (Array.isArray(tags) ? tags : [])
      .map(normalizarTagFiltroKanban)
      .filter(tag => tag.IDDimKanbanTag || tag.NomeTag);

    if (idx >= 0) {
      filtrosCardsKanban[idx] = Object.assign({}, filtrosCardsKanban[idx], { Tags: tagsNormalizadas });
      return;
    }

    const card = obterCardPorId(idCard);
    if (card) {
      atualizarFiltroCardLocal(card, tagsNormalizadas);
    }
  }

  function removerFiltroCardLocal(cardId){
    const idCard = idNum(cardId);
    if (!idCard) return;
    filtrosCardsKanban = filtrosCardsKanban.filter(item => idNum(item.IDFatoKanbanCard) !== idCard);
  }

  function obterBaseFiltrosCards(){
    const cardsCarregados = Array.isArray(cards) ? cards : [];

    if (cardsCarregados.length) {
      return cardsCarregados
        .filter(card => cardPertenceAoVendedorLogado(card))
        .filter(card => cardPassaBusca(card))
        .map(card => normalizarFiltroCardKanban(Object.assign({}, card || {}, {
          Tags: tagsDoCard(card?.IDFatoKanbanCard)
        })))
        .filter(item => idNum(item.IDFatoKanbanCard));
    }

    return (Array.isArray(filtrosCardsKanban) ? filtrosCardsKanban : [])
      .map(normalizarFiltroCardKanban)
      .filter(item => idNum(item.IDFatoKanbanCard));
  }

  function filtroCardPassaSelecaoVendedores(item, selecionados = vendedoresSelecionados){
    if (!selecionados || !selecionados.size) return true;
    const nome = safeStr(item?.NomeUsuarioResponsavel || "").trim();
    const chave = normalizarTexto(nome);
    return !!chave && selecionados.has(chave);
  }

  function filtroCardPassaSelecaoTags(item, selecionados = tagsSelecionadasFiltro){
    if (!selecionados || !selecionados.size) return true;
    const tags = Array.isArray(item?.Tags) ? item.Tags : [];
    return tags.some(tag => {
      const chave = normalizarTexto(tag?.NomeTag || tag?.nome_tag || "");
      return !!chave && selecionados.has(chave);
    });
  }

  function obterCardsVisiveisAtuais(){
    const lista = [];

    if (indiceCardsVisiveisPorFase instanceof Map) {
      indiceCardsVisiveisPorFase.forEach(cardsFase => {
        (Array.isArray(cardsFase) ? cardsFase : []).forEach(card => {
          if (card && idNum(card.IDFatoKanbanCard)) lista.push(card);
        });
      });
    }

    return lista;
  }

  function obterIdsCardsVisiveisAtuais(){
    return [...new Set(
      obterCardsVisiveisAtuais()
        .map(card => idNum(card?.IDFatoKanbanCard || 0))
        .filter(Boolean)
    )];
  }

  function resumoComercialZerado(){
    return {
      QuantidadeAtendimentosAtivos: 0,
      QuantidadeAprovacaoPreco: 0,
      ValorCustoTotal: 0,
      ValorVendaTotal: 0,
      MargemPercentualTotal: 0
    };
  }

  function montarQueryResumoComercialFiltrado(){
    const params = new URLSearchParams();
    vendedoresSelecionados.forEach(valor => {
      if (valor) params.append("vendedor", valor);
    });
    tagsSelecionadasFiltro.forEach(valor => {
      if (valor) params.append("tag", valor);
    });

    if (haFiltroAtivo()) {
      params.set("escopo_visivel", "1");
      obterIdsCardsVisiveisAtuais().forEach(idCard => {
        params.append("card_id", String(idCard));
      });
    }

    return params.toString();
  }

  function obterCatalogoVendedoresFiltro(){
    const mapa = new Map();
    const baseCompleta = obterBaseFiltrosCards();
    const base = baseCompleta.filter(item => filtroCardPassaSelecaoTags(item));

    base.forEach(item => {
      const nome = safeStr(item?.NomeUsuarioResponsavel || "").trim();
      const chave = normalizarTexto(nome);
      if (nome && chave && !mapa.has(chave)) {
        mapa.set(chave, nome);
      }
    });

    if (!mapa.size && !baseCompleta.length) {
      vendedoresCatalogo.forEach(item => {
        const nome = safeStr(item?.NomeUsuario || item?.NomeVendedor || item?.nome_usuario || item?.nome_vendedor || "").trim();
        const chave = normalizarTexto(nome);
        if (nome && chave && !mapa.has(chave)) {
          mapa.set(chave, nome);
        }
      });
    }

    return [...mapa.entries()]
      .sort((a, b) => a[1].localeCompare(b[1], "pt-BR", { sensitivity: "base" }))
      .map(([valor, label]) => ({ valor, label }));
  }

  function obterCatalogoTagsFiltro(){
    const mapa = new Map();
    const base = obterBaseFiltrosCards().filter(item => filtroCardPassaSelecaoVendedores(item));

    base.forEach(item => {
      const tags = Array.isArray(item?.Tags) ? item.Tags : [];
      tags.forEach(tag => {
        const tagNorm = normalizarTagFiltroKanban(tag);
        const nome = safeStr(tagNorm.NomeTag || "").trim();
        const chave = normalizarTexto(nome);
        if (nome && chave && !mapa.has(chave)) {
          mapa.set(chave, {
            valor: chave,
            label: nome,
            corHex: tagNorm.CorHex,
            icone: tagNorm.Icone
          });
        }
      });
    });

    return [...mapa.values()]
      .sort((a, b) => a.label.localeCompare(b.label, "pt-BR", { sensitivity: "base" }));
  }

  function fecharMenusFiltros(excecao = null){
    [
      { wrap: filtroVendedorWrap, menu: menuFiltroVendedor },
      { wrap: filtroTagWrap, menu: menuFiltroTag }
    ].forEach(item => {
      if (!item.wrap || !item.menu || item.wrap === excecao) return;
      item.wrap.classList.remove("is-open");
      item.menu.hidden = true;
    });
  }

  function alternarMenuFiltro(wrap, menu, input){
    if (!wrap || !menu) return;
    const vaiAbrir = menu.hidden;
    fecharMenusFiltros(vaiAbrir ? wrap : null);
    wrap.classList.toggle("is-open", vaiAbrir);
    menu.hidden = !vaiAbrir;
    if (vaiAbrir && input) {
      requestAnimationFrame(() => input.focus());
    }
  }

  function renderizarListaFiltro({
    listaEl,
    inputEl,
    selecionados,
    itens,
    textoVazio,
    onChange
  }){
    if (!listaEl) return;

    const termo = normalizarTexto(inputEl?.value || "");
    const itensFiltrados = itens.filter(item => {
      if (!termo) return true;
      return normalizarTexto(item.label).includes(termo);
    });

    listaEl.innerHTML = "";

    if (!itensFiltrados.length) {
      listaEl.appendChild(el("div", { class:"kb-filtro-multi-vazio" }, [textoVazio]));
      return;
    }

    itensFiltrados.forEach(item => {
      const inputId = `flt_${Math.random().toString(36).slice(2)}`;
      const checked = selecionados.has(item.valor);

      const checkbox = el("input", {
        id: inputId,
        type:"checkbox"
      }, []);
      checkbox.checked = checked;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          selecionados.add(item.valor);
        } else {
          selecionados.delete(item.valor);
        }
        onChange();
      });

      const textoItem = safeStr(item.label || "").trim();
      const conteudoItem = item.corHex
        ? [
            checkbox,
            el("span", {
              class: "kb-tag kb-filtro-tag-pill",
              style: `${estiloTag(item.corHex)} flex:0 1 auto; max-width:100%;`
            }, [
              ...(item.icone ? [el("span", { class:"kb-tag-icone" }, [item.icone])] : []),
              el("span", { class:"kb-tag-text" }, [textoItem])
            ])
          ]
        : [
            checkbox,
            el("span", {}, [textoItem])
          ];

      const label = el("label", { class:"kb-filtro-multi-item", for: inputId }, conteudoItem);

      listaEl.appendChild(label);
    });
  }

  function atualizarResumoFiltros(){
    if (resumoFiltroVendedor) {
      resumoFiltroVendedor.textContent = resumoTextoSelecao(
        vendedoresSelecionados.size,
        "selecionado",
        "selecionados",
        "Todos"
      );
    }

    if (resumoFiltroTag) {
      resumoFiltroTag.textContent = resumoTextoSelecao(
        tagsSelecionadasFiltro.size,
        "selecionada",
        "selecionadas",
        "Todas"
      );
    }
  }

  function renderizarFiltrosMultiselect(){
    renderizarListaFiltro({
      listaEl: listaFiltroVendedor,
      inputEl: inputFiltroVendedor,
      selecionados: vendedoresSelecionados,
      itens: obterCatalogoVendedoresFiltro(),
      textoVazio: "Nenhum vendedor encontrado.",
      onChange: () => {
        atualizarResumoFiltros();
        aplicarBusca();
        renderizarFiltrosMultiselect();
        agendarRecarregarResumoComercial(150);
      }
    });

    renderizarListaFiltro({
      listaEl: listaFiltroTag,
      inputEl: inputFiltroTag,
      selecionados: tagsSelecionadasFiltro,
      itens: obterCatalogoTagsFiltro(),
      textoVazio: "Nenhuma tag encontrada para os cards filtrados.",
      onChange: () => {
        atualizarResumoFiltros();
        aplicarBusca();
        renderizarFiltrosMultiselect();
        agendarRecarregarResumoComercial(150);
      }
    });

    atualizarResumoFiltros();
  }

  function atualizarResumoBusca(){
    if (!contadorPesquisa) return;

    const totalCarregado = cards.length;
    const totalVisivel = contarCardsVisiveis();
    const totalServidor = totalServidorDoQuadro();
    const termo = safeStr(termoBusca).trim();

    if (!totalCarregado && !totalServidor){
      contadorPesquisa.textContent = "Nenhum card carregado";
      return;
    }

    if (!haFiltroAtivo()){
      if (totalServidor > 0 && totalServidor !== totalCarregado){
        contadorPesquisa.textContent = `${totalCarregado} carregados de ${totalServidor} card${totalServidor === 1 ? "" : "s"} no quadro`;
      } else {
        contadorPesquisa.textContent = `${totalCarregado} card${totalCarregado === 1 ? "" : "s"} no quadro`;
      }
      return;
    }

    if (!totalVisivel){
      contadorPesquisa.textContent = termo
        ? `Nenhum resultado para "${termo}" com os filtros atuais`
        : `Nenhum card encontrado com os filtros atuais`;
      return;
    }

    contadorPesquisa.textContent = termo
      ? `${totalVisivel} resultado${totalVisivel === 1 ? "" : "s"} para "${termo}" com os filtros atuais`
      : `${totalVisivel} card${totalVisivel === 1 ? "" : "s"} visível${totalVisivel === 1 ? "" : "eis"} com os filtros atuais`;
  }

  function aplicarBusca(){
    reconstruirIndicesCards();
    const idsFases = fases.map(f => idNum(f.IDDimKanbanFase)).filter(Boolean);
    idsFases.forEach(idFase => preencherCabecalhoFase(idFase));
    redesenharFasesLocalmente(idsFases, null, true);
    atualizarResumoBusca();
    renderizarFiltrosMultiselect();
    agendarRecarregarResumoComercial(150);
  }


  function abrirListaSugestoesBuscaKanban(){
    if (!comboBuscaKanban || !listaBuscaKanban) return;
    comboBuscaKanban.classList.add("is-open");
    listaBuscaKanban.hidden = false;
    if (buscaKanban) buscaKanban.setAttribute("aria-expanded", "true");
  }

  function fecharListaSugestoesBuscaKanban(){
    if (!comboBuscaKanban || !listaBuscaKanban) return;
    comboBuscaKanban.classList.remove("is-open");
    listaBuscaKanban.hidden = true;
    indiceSugestaoBuscaKanbanAtiva = -1;
    if (buscaKanban) buscaKanban.setAttribute("aria-expanded", "false");
  }

  function textoCurtoBuscaKanban(valor, limite = 180){
    const texto = safeStr(valor || "").replace(/\s+/g, " ").trim();
    if (!texto) return "";
    return texto.length > limite ? `${texto.slice(0, limite - 1)}…` : texto;
  }

  function normalizarSugestaoBuscaKanban(item){
    const c = Object.assign({}, item || {});
    c.IDFatoKanbanCard = idNum(c.IDFatoKanbanCard || c.id_card || 0);
    c.Titulo = safeStr(c.Titulo || c.titulo || "").trim() || `Card #${c.IDFatoKanbanCard}`;
    c.Descricao = safeStr(c.Descricao || c.descricao || "").trim();
    c.NomeFase = safeStr(c.NomeFase || c.nome_fase || "Sem fase").trim() || "Sem fase";
    c.NomeUsuarioCriador = safeStr(c.NomeUsuarioCriador || c.nome_usuario_criador || c.NomeUsuarioResponsavel || "").trim();
    c.EmpresaRazaoSocial = safeStr(c.EmpresaRazaoSocial || c.empresa_razao_social || c.NomeEmpresa || "").trim();
    c.EmpresaCNPJ = mascaraCnpj(c.EmpresaCNPJ || c.empresa_cnpj || "");
    c.QuantidadeCodFaces = idNum(c.QuantidadeCodFaces || c.quantidade_codfaces || 0);
    c.QuantidadePaineisVinculados = idNum(c.QuantidadePaineisVinculados || c.quantidade_paineis_vinculados || 0);

    const tagsBrutas = Array.isArray(c.Tags)
      ? c.Tags
      : (Array.isArray(c.tags) ? c.tags : tagsDoCard(c.IDFatoKanbanCard));

    c.Tags = (Array.isArray(tagsBrutas) ? tagsBrutas : [])
      .map(tag => ({
        IDDimKanbanTag: idNum(tag?.IDDimKanbanTag || tag?.id_dim_kanban_tag || tag?.id_tag || 0),
        NomeTag: safeStr(tag?.NomeTag || tag?.nome_tag || tag?.nome || "").trim(),
        CorHex: normalizarCorHex(tag?.CorHex || tag?.cor_hex || tag?.cor || ""),
        Icone: safeStr(tag?.Icone || tag?.icone || "").trim()
      }))
      .filter(tag => tag.IDDimKanbanTag > 0 || tag.NomeTag);

    return c;
  }

  function sugestoesBuscaKanbanLocais(texto){
    const termo = normalizarTexto(texto);
    if (!termo) return [];

    return (Array.isArray(cards) ? cards : [])
      .filter(card => normalizarTexto(textoBuscaDoCard(card)).includes(termo))
      .slice(0, LIMITE_SUGESTOES_BUSCA_KANBAN)
      .map(card => {
        const idFase = idNum(card?.IDDimKanbanFaseAtual || 0);
        const fase = fasePorId(idFase);
        return normalizarSugestaoBuscaKanban({
          IDFatoKanbanCard: card?.IDFatoKanbanCard,
          Titulo: card?.Titulo,
          Descricao: card?.Descricao,
          NomeFase: fase?.NomeFase,
          NomeUsuarioCriador: card?.NomeUsuarioResponsavel,
          EmpresaRazaoSocial: card?.EmpresaRazaoSocial,
          EmpresaCNPJ: card?.EmpresaCNPJ,
          QuantidadeCodFaces: card?.QuantidadeCodFaces || card?.QuantidadePaineisVinculados,
          QuantidadePaineisVinculados: card?.QuantidadePaineisVinculados,
          Tags: tagsDoCard(card?.IDFatoKanbanCard)
        });
      });
  }

  function definirItemAtivoSugestaoBuscaKanban(indice){
    if (!listaBuscaKanban) return;
    const botoes = Array.from(listaBuscaKanban.querySelectorAll(".kb-search-sugestao-card"));
    if (!botoes.length) {
      indiceSugestaoBuscaKanbanAtiva = -1;
      return;
    }

    const ultimoIndice = botoes.length - 1;
    const indiceNormalizado = Math.max(0, Math.min(indice, ultimoIndice));
    indiceSugestaoBuscaKanbanAtiva = indiceNormalizado;

    botoes.forEach((botao, i) => {
      botao.classList.toggle("is-active", i === indiceNormalizado);
      if (i === indiceNormalizado) {
        botao.scrollIntoView({ block: "nearest" });
      }
    });
  }

  function renderizarListaSugestoesBuscaKanban(itens, opcoes = {}){
    if (!listaBuscaKanban) return;

    const lista = (Array.isArray(itens) ? itens : [])
      .map(normalizarSugestaoBuscaKanban)
      .filter(item => item.IDFatoKanbanCard > 0)
      .slice(0, LIMITE_SUGESTOES_BUSCA_KANBAN);

    sugestoesBuscaKanbanAtual = lista;
    indiceSugestaoBuscaKanbanAtiva = -1;
    listaBuscaKanban.innerHTML = "";

    const mensagem = safeStr(opcoes.mensagem || "").trim();
    if (mensagem && !lista.length) {
      listaBuscaKanban.appendChild(el("div", { class: "kb-search-sugestoes-status" }, [mensagem]));
      abrirListaSugestoesBuscaKanban();
      return;
    }

    if (!lista.length) {
      fecharListaSugestoesBuscaKanban();
      return;
    }

    lista.forEach((item, indice) => {
      const partesMeta = [
        `Fase: ${item.NomeFase}`,
        item.NomeUsuarioCriador ? `Criado por: ${item.NomeUsuarioCriador}` : "Criador não informado"
      ];

      const badges = [];
      if (item.EmpresaRazaoSocial) badges.push(`Empresa: ${textoCurtoBuscaKanban(item.EmpresaRazaoSocial, 70)}`);
      if (item.EmpresaCNPJ) badges.push(item.EmpresaCNPJ);
      if (item.QuantidadeCodFaces > 0) {
        badges.push(`${item.QuantidadeCodFaces} CodFace${item.QuantidadeCodFaces === 1 ? "" : "s"}`);
      } else if (item.QuantidadePaineisVinculados > 0) {
        badges.push(`${item.QuantidadePaineisVinculados} painel/face${item.QuantidadePaineisVinculados === 1 ? "" : "s"}`);
      }

      const tagsSugestao = Array.isArray(item.Tags) ? item.Tags : [];
      const badgesTags = tagsSugestao
        .filter(tag => safeStr(tag?.NomeTag || "").trim())
        .map(tag => {
          const nomeTag = safeStr(tag?.NomeTag || "").trim();
          const iconeTag = safeStr(tag?.Icone || "").trim();
          const filhos = [];
          if (iconeTag) filhos.push(el("span", { class: "kb-search-sugestao-tag-icone" }, [iconeTag]));
          filhos.push(el("span", { class: "kb-search-sugestao-tag-text" }, [nomeTag]));
          return el("span", {
            class: "kb-search-sugestao-tag",
            style: estiloTag(tag?.CorHex || "")
          }, filhos);
        });

      const badgesInfo = badges.map(txt => el("span", { class: "kb-search-sugestao-badge" }, [txt]));

      const botao = el("button", {
        type: "button",
        class: "kb-search-sugestao-card",
        "data-id-card": String(item.IDFatoKanbanCard)
      }, [
        el("div", { class: "kb-search-sugestao-topo" }, [
          el("div", { class: "kb-search-sugestao-titulo", title: item.Titulo }, [item.Titulo]),
          el("span", { class: "kb-search-sugestao-id" }, [`#${item.IDFatoKanbanCard}`])
        ]),
        el("div", { class: "kb-search-sugestao-meta" }, [partesMeta.join(" • ")]),
        item.Descricao
          ? el("div", { class: "kb-search-sugestao-desc" }, [textoCurtoBuscaKanban(item.Descricao, 220)])
          : el("div", { class: "kb-search-sugestao-desc" }, ["Sem descrição informada."]),
        el("div", { class: "kb-search-sugestao-badges" }, [...badgesTags, ...badgesInfo])
      ]);

      botao.addEventListener("mouseenter", () => definirItemAtivoSugestaoBuscaKanban(indice));
      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarSugestaoBuscaKanban(item);
      });

      listaBuscaKanban.appendChild(botao);
    });

    abrirListaSugestoesBuscaKanban();
  }

  async function buscarSugestoesBuscaKanban(texto){
    const termo = safeStr(texto || "").trim();
    if (!buscaKanban || !listaBuscaKanban) return;

    if (buscaKanbanSugestoesController) {
      buscaKanbanSugestoesController.abort();
      buscaKanbanSugestoesController = null;
    }

    if (termo.length < MIN_CARACTERES_SUGESTAO_BUSCA_KANBAN) {
      sugestoesBuscaKanbanAtual = [];
      fecharListaSugestoesBuscaKanban();
      return;
    }

    const locais = sugestoesBuscaKanbanLocais(termo);
    if (locais.length) {
      renderizarListaSugestoesBuscaKanban(locais);
    } else {
      renderizarListaSugestoesBuscaKanban([], { mensagem: "Buscando cards..." });
    }

    const controller = new AbortController();
    buscaKanbanSugestoesController = controller;

    try {
      const url = `/kanban/api/kanbans/${ID_KANBAN}/cards/sugestoes?q=${encodeURIComponent(termo)}&limit=${LIMITE_SUGESTOES_BUSCA_KANBAN}`;
      const resultado = await fetchJsonKanban(url, { signal: controller.signal });
      const corpo = resultado.corpo;

      if (controller.signal.aborted) return;

      if (!respostaJsonKanbanOk(resultado)) {
        console.warn("buscarSugestoesBuscaKanban: resposta inválida", detalhesFalhaJsonKanban(resultado));
        if (!locais.length) renderizarListaSugestoesBuscaKanban([], { mensagem: "Não foi possível buscar sugestões agora." });
        return;
      }

      const sugestoes = Array.isArray(corpo?.sugestoes) ? corpo.sugestoes : [];
      renderizarListaSugestoesBuscaKanban(sugestoes.length ? sugestoes : locais, {
        mensagem: "Nenhum card encontrado para este texto."
      });
    } catch (erro) {
      if (erro?.name === "AbortError") return;
      console.warn("buscarSugestoesBuscaKanban: falhou", erro);
      if (!locais.length) renderizarListaSugestoesBuscaKanban([], { mensagem: "Erro ao buscar sugestões." });
    } finally {
      if (buscaKanbanSugestoesController === controller) buscaKanbanSugestoesController = null;
    }
  }

  function agendarSugestoesBuscaKanban(){
    window.clearTimeout(debounceSugestaoBuscaTimer);
    debounceSugestaoBuscaTimer = window.setTimeout(() => {
      buscarSugestoesBuscaKanban(buscaKanban?.value || "");
    }, 180);
  }

  async function selecionarSugestaoBuscaKanban(item){
    const sugestao = normalizarSugestaoBuscaKanban(item);
    if (!sugestao.IDFatoKanbanCard) return;

    if (buscaKanban) {
      buscaKanban.value = `#${sugestao.IDFatoKanbanCard} ${sugestao.Titulo}`.trim();
    }

    termoBusca = String(sugestao.IDFatoKanbanCard);
    fecharListaSugestoesBuscaKanban();
    aplicarBusca();

    try {
      await sincronizarCardPorDetalhe(sugestao.IDFatoKanbanCard, true);
      await abrirCard(sugestao.IDFatoKanbanCard);
    } catch (erro) {
      console.warn("selecionarSugestaoBuscaKanban: falhou ao abrir card", erro);
      mostrarMensagemBoard("Encontrei o card, mas não consegui abrir os detalhes agora.");
    }
  }

  function corColunaPorIndice(i){
    const paleta = ["#16a34a", "#f97316", "#64748b", "#7c3aed", "#10b981", "#0ea5e9", "#eab308", "#ef4444"];
    return paleta[i % paleta.length];
  }

  function obterCorFase(fase, indice){
    const corPersistida = normalizarCorHex(
      fase?.CorHex ?? fase?.cor_hex ?? fase?.cor_fase ?? ""
    );
    if (corPersistida) return corPersistida;
    return corColunaPorIndice(indice);
  }

  function obterCorTextoFase(fase, corFundo){
    return normalizarCorHex(
      fase?.CorTextoHex ?? fase?.cor_texto_hex ?? fase?.cor_texto_fase ?? ""
    ) || corTextoPorFundo(corFundo);
  }

  function aplicarFaseRetornadaServidor(faseServidor){
    if (!faseServidor || typeof faseServidor !== "object") return;

    const idFase = idNum(faseServidor.IDDimKanbanFase || faseServidor.id_fase || 0);
    if (!idFase) return;

    const idx = fases.findIndex(f => idNum(f?.IDDimKanbanFase || 0) === idFase);
    if (idx >= 0) {
      fases[idx] = Object.assign({}, fases[idx], faseServidor);
    } else {
      fases.push(Object.assign({}, faseServidor));
      fases.sort((a, b) => {
        const ordemA = idNum(a?.OrdemFase || 999999);
        const ordemB = idNum(b?.OrdemFase || 999999);
        return ordemA - ordemB;
      });
    }
  }

  function resetModalFase(){
    faseEditandoId = null;
    if (modalFaseTitulo) modalFaseTitulo.textContent = "Nova Fase";
    if (faseNomeInput) faseNomeInput.value = "";
    if (faseTipoSelect) faseTipoSelect.value = "ATIVA";
    if (faseUsarCor) faseUsarCor.checked = false;
    if (faseCorHex) {
      faseCorHex.value = "#0B4EA2";
      faseCorHex.disabled = true;
    }
    if (msgFase) {
      msgFase.style.display = "none";
      msgFase.textContent = "";
    }
  }

  function abrirModalNovaFase(){
    if (!USUARIO_PODE_GERENCIAR_FASES_E_TAGS || !modalFase) return;
    resetModalFase();
    if (modalFase) modalFase.style.display = "block";
  }

  function abrirModalEditarFase(idFase){
    if (!USUARIO_PODE_GERENCIAR_FASES_E_TAGS || !modalFase) return;
    const fase = fasePorId(idFase);
    if (!fase) return;

    resetModalFase();
    faseEditandoId = idNum(idFase);
    if (modalFaseTitulo) modalFaseTitulo.textContent = "Editar Fase";
    if (faseNomeInput) faseNomeInput.value = safeStr(fase.NomeFase);
    if (faseTipoSelect) faseTipoSelect.value = safeStr(fase.TipoFase || "ATIVA").toUpperCase() || "ATIVA";

    const corPersistida = normalizarCorHex(fase.CorHex || "");
    if (faseUsarCor) faseUsarCor.checked = !!corPersistida;
    if (faseCorHex) {
      faseCorHex.value = corPersistida || obterCorFase(fase, fases.findIndex(f => idNum(f.IDDimKanbanFase) === idNum(idFase)));
      faseCorHex.disabled = !corPersistida;
    }

    modalFase.style.display = "block";
  }

  async function apiPost(url, payload){
    const r = await fetchKanbanComTimeout(url, {
      method:"POST",
      credentials:"same-origin",
      headers: headersJSON,
      body: JSON.stringify(payload || {})
    }, TIMEOUT_FETCH_PADRAO_KANBAN_MS);

    const j = await r.json().catch(() => null);
    return { ok: !!(j && j.ok), http: r.status, body: j };
  }

  function limparEstadosDrop(){
    estadoFase.forEach(st => {
      st.dragDepth = 0;
      if (st.dropEl) st.dropEl.classList.remove("is-over");
    });
  }

  function limparDragImage(){
    if (dragImageEl && dragImageEl.parentNode) {
      dragImageEl.parentNode.removeChild(dragImageEl);
    }
    dragImageEl = null;
  }

  function criarDragImage(cardEl){
    if (!cardEl) return null;

    const rect = cardEl.getBoundingClientRect();
    const titulo = safeStr(cardEl.querySelector?.(".kb-card-title")?.textContent || "Card").trim() || "Card";
    const idTexto = safeStr(cardEl.querySelector?.(".kb-card-badge-id")?.textContent || "").trim();
    const faseTexto = safeStr(cardEl.querySelector?.(".kb-phase-pill")?.textContent || "").trim();

    const clone = document.createElement("div");
    clone.className = "kb-drag-image";
    clone.appendChild(document.createTextNode(titulo));

    const meta = document.createElement("small");
    meta.textContent = [idTexto, faseTexto].filter(Boolean).join(" • ");
    if (meta.textContent) clone.appendChild(meta);

    clone.style.position = "fixed";
    clone.style.top = "-9999px";
    clone.style.left = "-9999px";
    clone.style.pointerEvents = "none";
    clone.style.width = `${Math.min(Math.ceil(rect.width), 280)}px`;
    clone.style.maxWidth = `${Math.min(Math.ceil(rect.width), 280)}px`;
    clone.style.zIndex = "999999";
    document.body.appendChild(clone);
    return clone;
  }

  function iniciarModoDrag(cardId, idFaseOrigem, cardEl, event){
    cardArrastandoId = idNum(cardId);
    faseOrigemArrasteId = idNum(idFaseOrigem);
    elementoArrastando = cardEl || null;

    document.body.classList.add("kb-dragging");
    board.classList.add("is-dragging");

    if (elementoArrastando){
      requestAnimationFrame(() => {
        if (elementoArrastando) elementoArrastando.classList.add("drag-source");
      });
    }

    if (event && event.dataTransfer){
      event.dataTransfer.effectAllowed = "move";

      const img = criarDragImage(cardEl);
      if (img){
        dragImageEl = img;
        try{
          event.dataTransfer.setDragImage(img, 28, 18);
        } catch (_erro) {
        }
      }
    }
  }

  function encerrarModoDrag(){
    document.body.classList.remove("kb-dragging");
    board.classList.remove("is-dragging");

    if (elementoArrastando){
      elementoArrastando.classList.remove("drag-source");
    }

    elementoArrastando = null;
    cardArrastandoId = 0;
    faseOrigemArrasteId = 0;

    limparEstadosDrop();

    setTimeout(() => {
      limparDragImage();
    }, 0);
  }

  function obterCardPorId(idCard){
    return indiceCardsPorId.get(idNum(idCard)) || null;
  }

  function inserirOuAtualizarCardLocal(card, opcoes = {}){
    const cardNorm = normalizarCardServidor(card);
    if (!cardNorm.IDFatoKanbanCard) return null;

    const reconstruir = opcoes.reconstruir !== false;
    const reposicionarNoTopoDaFase = opcoes.reposicionarNoTopoDaFase === true;

    if (USUARIO_EH_VENDEDOR && !cardPertenceAoVendedorLogado(cardNorm)) {
      removerCardLocal(cardNorm.IDFatoKanbanCard, { reconstruir });
      return null;
    }

    if (cardDeveSairDoQuadro(cardNorm)) {
      removerCardLocal(cardNorm.IDFatoKanbanCard, { reconstruir });
      return null;
    }

    const nomeVendedor = nomeVendedorDoCard(cardNorm);
    const chaveVendedor = normalizarTexto(nomeVendedor);
    if (nomeVendedor && chaveVendedor) {
      const jaExiste = vendedoresCatalogo.some(item => normalizarTexto(item?.NomeUsuario || "") === chaveVendedor);
      if (!jaExiste) {
        vendedoresCatalogo.push({ IDDimUsuarios: cardNorm.IDUsuarioRelacionadoCard || null, NomeUsuario: nomeVendedor });
      }
    }

    const idx = cards.findIndex(c => idNum(c.IDFatoKanbanCard) === cardNorm.IDFatoKanbanCard);
    let cardAtualizado = null;

    if (idx >= 0){
      cardAtualizado = Object.assign({}, cards[idx], cardNorm);
      cards.splice(idx, 1);
    } else {
      cardAtualizado = cardNorm;
    }

    if (reposicionarNoTopoDaFase) {
      const idFaseDestino = idNum(cardAtualizado.IDDimKanbanFaseAtual || cardAtualizado.id_fase_atual || 0);
      const indicePrimeiroDaFase = cards.findIndex(c => idNum(c.IDDimKanbanFaseAtual || c.id_fase_atual || 0) === idFaseDestino);
      const indiceInsercao = indicePrimeiroDaFase >= 0 ? indicePrimeiroDaFase : 0;
      cards.splice(indiceInsercao, 0, cardAtualizado);
    } else if (idx >= 0) {
      cards.splice(idx, 0, cardAtualizado);
    } else {
      cards.unshift(cardAtualizado);
    }

    atualizarFiltroCardLocal(cardAtualizado);

    if (reconstruir) {
      reconstruirIndicesCards();
    }

    return cardAtualizado;
  }

  function removerCardLocal(idCard, opcoes = {}){
    const id = idNum(idCard);
    cards = cards.filter(c => idNum(c.IDFatoKanbanCard) !== id);
    mapaTagsPorCard.delete(id);
    mapaNotasPorCard.delete(id);
    removerFiltroCardLocal(id);

    if (opcoes.reconstruir !== false) {
      reconstruirIndicesCards();
    }
  }

  function ajustarQuantidadeFaseLocal(idFase, delta){
    const idF = idNum(idFase);
    const variacao = Number(delta || 0);
    if (!idF || !Number.isFinite(variacao) || variacao === 0) return;

    const fase = fasePorId(idF);
    const st = estadoFase.get(idF);
    const totalAtual = Math.max(
      idNum(fase?.QuantidadeCardsTotal || 0),
      idNum(st?.totalServidor || 0),
      contarCardsNaFaseCarregados(idF)
    );
    const totalNovo = Math.max(0, totalAtual + variacao);

    if (fase) fase.QuantidadeCardsTotal = totalNovo;
    if (st) {
      st.totalServidor = totalNovo;
      st.offsetServidor = Math.min(idNum(st.offsetServidor || 0), totalNovo);
      st.cargaCompleta = contarCardsNaFaseCarregados(idF) >= totalNovo;
    }
  }

  function removerCardInativadoLocal(idCard, idFaseFallback = 0){
    const id = idNum(idCard);
    if (!id) return;

    const cardAntes = obterCardPorId(id);
    const idFase = idNum(cardAntes?.IDDimKanbanFaseAtual || idFaseFallback || 0);
    const existiaLocalmente = !!cardAntes;

    removerCardLocal(id);

    if (existiaLocalmente && idFase) {
      ajustarQuantidadeFaseLocal(idFase, -1);
    }

    if (idFase) {
      redesenharFasesLocalmente([idFase], null, true);
    } else {
      renderBoardCompleto();
    }

    agendarRecarregarResumoComercial();
  }




  function moverCardLocalmente(idCard, idFasePara){
  const idC = idNum(idCard);
  const idDestino = idNum(idFasePara);

  if (!idC || !idDestino) {
    return { ok: false, idCard: 0, origem: 0, destino: 0, indiceOrigem: -1, snapshot: null };
  }

  const indiceAtual = cards.findIndex(c => idNum(c.IDFatoKanbanCard) === idC);
  if (indiceAtual < 0) {
    return { ok: false, idCard: idC, origem: 0, destino: idDestino, indiceOrigem: -1, snapshot: null };
  }

  const cardAtual = cards[indiceAtual];
  const idOrigem = idNum(cardAtual.IDDimKanbanFaseAtual);

  if (!idOrigem || idOrigem === idDestino) {
    return {
      ok: false,
      idCard: idC,
      origem: idOrigem,
      destino: idDestino,
      indiceOrigem: indiceAtual,
      snapshot: Object.assign({}, cardAtual)
    };
  }

  const snapshot = Object.assign({}, cardAtual);

  const [cardMovido] = cards.splice(indiceAtual, 1);
  cardMovido.IDDimKanbanFaseAtual = idDestino;

  const indicePrimeiroDestino = cards.findIndex(c => idNum(c.IDDimKanbanFaseAtual || c.id_fase_atual || 0) === idDestino);
  const indiceInsercao = indicePrimeiroDestino >= 0 ? indicePrimeiroDestino : 0;

  cards.splice(indiceInsercao, 0, cardMovido);
  reconstruirIndicesCards();

  return {
    ok: true,
    idCard: idC,
    origem: idOrigem,
    destino: idDestino,
    indiceOrigem: indiceAtual,
    indiceDestino: indiceInsercao,
    snapshot
  };
}




function restaurarMovimentoCardLocal(movimento){
  if (!movimento || !movimento.ok) return false;

  const idCard = idNum(movimento.idCard);
  const idOrigem = idNum(movimento.origem);

  if (!idCard || !idOrigem) return false;

  const indiceAtual = cards.findIndex(c => idNum(c.IDFatoKanbanCard) === idCard);
  if (indiceAtual >= 0) {
    cards.splice(indiceAtual, 1);
  }

  const cardRestaurado = normalizarCardServidor(
    Object.assign({}, movimento.snapshot || {}, {
      IDFatoKanbanCard: idCard,
      IDDimKanbanFaseAtual: idOrigem
    })
  );

  let indiceInsercao = idNum(movimento.indiceOrigem);
  if (indiceInsercao < 0 || indiceInsercao > cards.length) {
    indiceInsercao = cards.length;
  }

  cards.splice(indiceInsercao, 0, cardRestaurado);
  reconstruirIndicesCards();
  return true;
}





function redesenharFasesLocalmente(idsFase, mapaQuantidades = null, manterScroll = true){
  const ids = [...new Set((Array.isArray(idsFase) ? idsFase : [idsFase]).map(idNum).filter(Boolean))];

  ids.forEach(idFase => {
    const st = estadoFase.get(idFase);
    if (!st) return;

    const scrollTopAnterior = manterScroll ? st.dropEl.scrollTop : 0;
    const qtdPadrao = Math.max(TAM_LOTE_POR_FASE, st.visiveis || st.rendered || 0);
    const qtd = mapaQuantidades instanceof Map && mapaQuantidades.has(idFase)
      ? Math.max(TAM_LOTE_POR_FASE, idNum(mapaQuantidades.get(idFase)) || TAM_LOTE_POR_FASE)
      : qtdPadrao;

    sincronizarCardsRenderizadosDaFase(idFase, qtd);

    if (manterScroll) {
      requestAnimationFrame(() => {
        const maxScroll = Math.max(0, st.dropEl.scrollHeight - st.dropEl.clientHeight);
        st.dropEl.scrollTop = Math.min(scrollTopAnterior, maxScroll);
        if (typeof st.dropEl._kbAtualizarScrollbarFase === "function") st.dropEl._kbAtualizarScrollbarFase();
        void garantirPreenchimentoMinimoDaFase(idFase, { maxTentativas: 2 });
      });
    } else {
      void garantirPreenchimentoMinimoDaFase(idFase, { maxTentativas: 2 });
    }
  });

  atualizarResumoBusca();
}

let rafRedesenhoTempoReal = null;
const idsFasesRedesenhoTempoReal = new Set();
const mapaQuantidadesRedesenhoTempoReal = new Map();
let manterScrollRedesenhoTempoReal = true;

function agendarRedesenhoFasesTempoReal(idsFase, mapaQuantidades = null, manterScroll = true){
  const ids = [...new Set((Array.isArray(idsFase) ? idsFase : [idsFase]).map(idNum).filter(Boolean))];
  if (!ids.length) return;

  ids.forEach(idFase => idsFasesRedesenhoTempoReal.add(idFase));
  manterScrollRedesenhoTempoReal = manterScrollRedesenhoTempoReal && manterScroll !== false;

  if (mapaQuantidades instanceof Map) {
    mapaQuantidades.forEach((valor, idFaseBruto) => {
      const idFase = idNum(idFaseBruto);
      if (!idFase) return;

      const qtdAtual = idNum(mapaQuantidadesRedesenhoTempoReal.get(idFase) || 0);
      const qtdNova = idNum(valor || 0);
      mapaQuantidadesRedesenhoTempoReal.set(idFase, Math.max(qtdAtual, qtdNova));
      idsFasesRedesenhoTempoReal.add(idFase);
    });
  }

  if (rafRedesenhoTempoReal !== null) return;

  rafRedesenhoTempoReal = window.setTimeout(() => {
    rafRedesenhoTempoReal = null;

    const idsPendentes = [...idsFasesRedesenhoTempoReal];
    const mapaPendentes = mapaQuantidadesRedesenhoTempoReal.size
      ? new Map(mapaQuantidadesRedesenhoTempoReal)
      : null;
    const deveManterScroll = manterScrollRedesenhoTempoReal;

    idsFasesRedesenhoTempoReal.clear();
    mapaQuantidadesRedesenhoTempoReal.clear();
    manterScrollRedesenhoTempoReal = true;

    if (idsPendentes.length) {
      redesenharFasesLocalmente(idsPendentes, mapaPendentes, deveManterScroll);
    }
  }, ATRASO_REDRAW_TEMPO_REAL_MS);
}



function montarMapaQuantidadesAtualizacaoAoVivo(idsFase, idFaseDestino = 0, acrescimoDestino = 0){
  const mapa = new Map();
  const ids = [...new Set((Array.isArray(idsFase) ? idsFase : [idsFase]).map(idNum).filter(Boolean))];

  ids.forEach(idFase => {
    const st = estadoFase.get(idFase);
    const qtdBase = st
      ? Math.max(TAM_LOTE_POR_FASE, st.visiveis || st.rendered || 0)
      : TAM_LOTE_POR_FASE;
    mapa.set(idFase, qtdBase);
  });

  const idDestino = idNum(idFaseDestino);
  if (idDestino) {
    const stDestino = estadoFase.get(idDestino);
    const qtdDestinoBase = stDestino
      ? Math.max(TAM_LOTE_POR_FASE, stDestino.visiveis || stDestino.rendered || 0)
      : TAM_LOTE_POR_FASE;
    mapa.set(idDestino, Math.max(TAM_LOTE_POR_FASE, qtdDestinoBase + Math.max(0, idNum(acrescimoDestino))));
  }

  return mapa;
}








  function destacarCardNaFase(idCard, idFase, tentativa = 0){
    requestAnimationFrame(() => {
      const idC = idNum(idCard);
      const idF = idNum(idFase);
      const st = estadoFase.get(idF);
      if (!st || !st.dropEl || !idC) return;

      let cardEl = st.dropEl.querySelector(`.kb-card[data-card="${idC}"]`);

      if (!cardEl && st.virtualizado) {
        const lista = listaCardsDaFase(idF);
        const indiceCard = lista.findIndex(card => idNum(card?.IDFatoKanbanCard || 0) === idC);
        if (indiceCard >= 0) {
          const altura = obterAlturaEstimadaVirtual(st);
          st.visiveis = Math.max(st.visiveis || TAM_LOTE_POR_FASE, indiceCard + 1, TAM_LOTE_POR_FASE);
          st.dropEl.scrollTop = Math.max(0, (indiceCard * altura) - altura);
          sincronizarCardsRenderizadosDaFase(idF, st.visiveis, { atualizarJanelaVirtual: true });
          cardEl = st.dropEl.querySelector(`.kb-card[data-card="${idC}"]`);
        }
      }

      if (!cardEl) {
        if (tentativa < 8) destacarCardNaFase(idC, idF, tentativa + 1);
        return;
      }

      cardEl.classList.add("drop-commit");
      setTimeout(() => {
        cardEl.classList.remove("drop-commit");
      }, 220);
    });
  }




  let carregandoVendedoresKanban = false;

  async function carregarVendedoresKanbanDeferido() {
    if (carregandoVendedoresKanban) return;
    if (Array.isArray(vendedoresCatalogo) && vendedoresCatalogo.length) return;

    carregandoVendedoresKanban = true;
    try {
      const resultado = await fetchJsonKanban(`/kanban/api/kanbans/${ID_KANBAN}/vendedores`);
      const j = resultado.corpo;
      if (!respostaJsonKanbanOk(resultado)) return;
      vendedoresCatalogo = Array.isArray(j.vendedores) ? j.vendedores.map(v => Object.assign({}, v || {})) : [];
      renderizarFiltrosMultiselect();
    } catch (erro) {
      console.warn("carregarVendedoresKanbanDeferido: falhou", erro);
    } finally {
      carregandoVendedoresKanban = false;
    }
  }

  async function carregarEmpresas() {
    if (empresasCatalogo.length) return;

    try {
      const resultado = await fetchJsonKanban(`/kanban/api/empresas`);
      const j = resultado.corpo;

      if (!respostaJsonKanbanOk(resultado)) {
        console.warn("carregarEmpresas: resposta inválida", detalhesFalhaJsonKanban(resultado));
        montarSelectEmpresas();
        return;
      }

      empresasCatalogo = [];
      empresasPorId = new Map();
      registrarEmpresasNoCatalogo(Array.isArray(j.empresas) ? j.empresas : []);
      montarSelectEmpresas();
    } catch (erro) {
      console.warn("carregarEmpresas: falhou", erro);
      montarSelectEmpresas();
    }
  }

  function montarSelectEmpresas() {
    const valorAtual = safeStr(selectEmpresaCard?.value || "").trim();

    selectEmpresaCard.innerHTML = "";
    selectEmpresaCard.appendChild(el("option", {value: ""}, ["— Selecione uma empresa —"]));

    empresasCatalogo.forEach(e => {
      const id = e.IDEmpresa ?? e.IDEmpresaProprietaria ?? e.ID;
      const texto = textoOpcaoEmpresa(e);

      selectEmpresaCard.appendChild(el("option", {value: String(id)}, [texto]));
    });

    if (valorAtual) {
      garantirOpcaoEmpresaNoSelect(valorAtual);
      selectEmpresaCard.value = valorAtual;
    }

    sincronizarBuscaEmpresaComSelect();
    renderizarListaEmpresasCombobox(inputEmpresaCardBusca?.value || "");
  }

  function nomeExibicaoEmpresa(item){
    const emp = item || {};
    return safeStr(
      emp.RazaoSocial ||
      emp.EmpresaRazaoSocial ||
      emp.NomeFantasia ||
      emp.EmpresaNomeFantasia ||
      emp.NomeEmpresa ||
      emp.NomeEmpresaCarteira ||
      emp.MarcaExibida ||
      emp.Marca ||
      emp.Titulo ||
      ""
    ).trim();
  }

  function empresaTemNomeExibicaoValido(item){
    const nome = nomeExibicaoEmpresa(item);
    return !!nome && nome !== "—" && nome !== "-";
  }

  function textoOpcaoEmpresa(item){
    const emp = item || {};
    const razao = nomeExibicaoEmpresa(emp) || "—";
    const cnpj = mascaraCnpj(emp.CNPJ || emp.EmpresaCNPJ || emp.cnpj || "");
    return cnpj ? `${razao} • ${cnpj}` : razao;
  }

  function empresaBloqueadaCarteiraParaVendedor(item){
    if (!item) return false;

    const bloqueada = item.EmpresaBloqueadaCarteiraVendedor ?? item.empresa_bloqueada_carteira_vendedor ?? false;
    return bloqueada === true || bloqueada === 1 || bloqueada === "1" || safeStr(bloqueada).toLowerCase() === "true";
  }

  function nomeVendedorCarteiraEmpresa(item){
    return safeStr(item?.NomeVendedorCarteira || item?.nome_vendedor_carteira || "Vendedor responsável").trim() || "Vendedor responsável";
  }

  function nomeEmpresaCarteiraEmpresa(item){
    return safeStr(
      item?.RazaoSocial ||
      item?.EmpresaRazaoSocial ||
      item?.NomeEmpresaCarteira ||
      item?.nome_empresa ||
      item?.nome_empresa_carteira ||
      item?.NomeFantasia ||
      item?.EmpresaNomeFantasia ||
      "Empresa selecionada"
    ).trim() || "Empresa selecionada";
  }

  function mensagemEmpresaBloqueadaCarteira(item){
    const nomeEmpresa = nomeEmpresaCarteiraEmpresa(item);
    const nomeVendedor = nomeVendedorCarteiraEmpresa(item);
    return `A Empresa ${nomeEmpresa} pertence à Carteira ${nomeVendedor}. Favor verificar com o Coordenador.`;
  }

  function definirBloqueioCarteiraEmpresaPrincipal(empresa = null, mensagem = ""){
    if (empresa) {
      empresaPrincipalBloqueadaCarteiraAtual = Object.assign({}, empresa || {}, {
        MensagemBloqueioCarteiraVendedor: safeStr(mensagem || empresa?.MensagemBloqueioCarteiraVendedor || empresa?.msg || "").trim() || mensagemEmpresaBloqueadaCarteira(empresa)
      });
      if (selectEmpresaCard) {
        selectEmpresaCard.dataset.carteiraBloqueada = "1";
        selectEmpresaCard.dataset.msgCarteiraBloqueada = empresaPrincipalBloqueadaCarteiraAtual.MensagemBloqueioCarteiraVendedor;
      }
      if (inputEmpresaCardBusca) {
        inputEmpresaCardBusca.setAttribute("aria-invalid", "true");
        inputEmpresaCardBusca.title = empresaPrincipalBloqueadaCarteiraAtual.MensagemBloqueioCarteiraVendedor;
      }
    } else {
      empresaPrincipalBloqueadaCarteiraAtual = null;
      if (selectEmpresaCard) {
        selectEmpresaCard.dataset.carteiraBloqueada = "0";
        selectEmpresaCard.dataset.msgCarteiraBloqueada = "";
      }
      if (inputEmpresaCardBusca) {
        inputEmpresaCardBusca.removeAttribute("aria-invalid");
        inputEmpresaCardBusca.title = "";
      }
    }

    if (typeof atualizarEstadoSalvarCard === "function") {
      atualizarEstadoSalvarCard();
    }
  }

  function mostrarAvisoEmpresaBloqueadaCarteira(item, mensagemForcada = "", opcoes = {}){
    const msg = safeStr(mensagemForcada || item?.MensagemBloqueioCarteiraVendedor || item?.msg || "").trim() || mensagemEmpresaBloqueadaCarteira(item);
    const exibirPopup = opcoes?.exibirPopup !== false;

    if (typeof mostrarMensagemCard === "function") {
      mostrarMensagemCard(msg);
    } else if (msgCard) {
      msgCard.textContent = msg;
      msgCard.style.display = "block";
    }

    // O popup aparece somente na tentativa ativa de selecionar a empresa bloqueada.
    // Em fechar/reabrir card e em validação de salvamento, a mensagem fica só no modal.
    if (exibirPopup) {
      window.alert(msg);
    }
  }

  function limparSelecaoEmpresaBloqueadaSemPopup(valorSeguro = ""){
    const valorFinal = safeStr(valorSeguro || "").trim();

    definirBloqueioCarteiraEmpresaPrincipal(null);

    if (selectEmpresaCard) {
      if (valorFinal) {
        garantirOpcaoEmpresaNoSelect(valorFinal);
      }

      selectEmpresaCard.value = valorFinal;
      selectEmpresaCard.dataset.valorCarteiraPermitido = valorFinal;
      selectEmpresaCard.dataset.carteiraBloqueada = "0";
      selectEmpresaCard.dataset.msgCarteiraBloqueada = "";
      selectEmpresaCard.dataset.validandoCarteira = "0";
    }

    if (inputEmpresaCardBusca) {
      inputEmpresaCardBusca.removeAttribute("aria-invalid");
      inputEmpresaCardBusca.title = "";
      inputEmpresaCardBusca.value = valorFinal ? obterTextoEmpresaSelecionada(valorFinal) : "";
    }

    if (!valorFinal) {
      setEmpresaPreviewById("");
      limparDadosNovoContratoFormulario();
      if (typeof resetarFluxoContrato === "function") {
        resetarFluxoContrato();
      }
    }

    fecharListaEmpresasCombobox();
    atualizarVisibilidadeDadosNovoContrato();
    aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente();
    agendarSincronizacaoFormularioSolicitacao();
    atualizarEstadoSalvarCard();
  }

  function solicitarAtualizacaoAoVivoCardDescartado(idCard){
    const idCardInt = idNum(idCard || 0);
    if (!idCardInt) return;

    if (socketKanban && socketConectado) {
      try {
        socketKanban.emit("card_edicao_descartada", {
          id_kanban: ID_KANBAN,
          id_card: idCardInt
        });
        return;
      } catch (erro) {
        console.warn("Falha ao emitir card_edicao_descartada. Vou atualizar por HTTP.", erro);
      }
    }

    sincronizarCardPorDetalhe(idCardInt, true).catch((erro) => {
      console.warn("Falha ao sincronizar card descartado por detalhe. Vou recarregar o quadro.", erro);
      carregar().catch((erroCarregar) => console.warn("Falha ao recarregar quadro após descarte.", erroCarregar));
    });
  }

  function montarEmpresaComStatusCarteira(idEmp, empresaBase, statusCarteira){
    const idEmpresa = idNum(idEmp || empresaBase?.IDEmpresa || empresaBase?.ID || 0);
    const status = statusCarteira || {};
    const bloqueada = status.bloqueada === true || status.bloqueada === 1 || status.bloqueada === "1";
    const temCarteira = status.tem_carteira === true || status.tem_carteira === 1 || status.tem_carteira === "1";

    return Object.assign({}, empresaBase || {}, {
      IDEmpresa: idEmpresa || empresaBase?.IDEmpresa || empresaBase?.ID || null,
      EmpresaTemCarteira: temCarteira,
      EmpresaBloqueadaCarteiraVendedor: bloqueada,
      EmpresaPermitidaCarteiraVendedor: !bloqueada,
      IDVendedorCarteira: status.id_vendedor_carteira || null,
      NomeVendedorCarteira: status.nome_vendedor_carteira || empresaBase?.NomeVendedorCarteira || null,
      NomeEmpresaCarteira: status.nome_empresa || empresaBase?.RazaoSocial || empresaBase?.EmpresaRazaoSocial || null,
      IDFatoCarteiraVendedorEmpresa: status.id_fato_carteira_vendedor || null,
      MensagemBloqueioCarteiraVendedor: status.msg || null
    });
  }

  async function validarEmpresaCarteiraVendedorPorId(idEmp, empresaBase = null){
    const idEmpresa = idNum(idEmp || 0);

    if (!idEmpresa) {
      return { permitida: true, empresa: empresaBase || null, status: null };
    }

    try {
      const r = await fetch(montarUrlKanban(`/kanban/api/empresas/${idEmpresa}/status-carteira`), {
        method: "GET",
        credentials: "same-origin",
        headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
      });

      const j = await r.json().catch(() => null);
      const status = j?.empresa_carteira || {};
      const empresaAtualizada = montarEmpresaComStatusCarteira(idEmpresa, empresaBase, status);
      if (j?.msg || j?.erro) {
        empresaAtualizada.MensagemBloqueioCarteiraVendedor = j.msg || j.erro;
      }
      registrarEmpresasNoCatalogo([empresaAtualizada]);

      const bloqueada = empresaBloqueadaCarteiraParaVendedor(empresaAtualizada) || status.bloqueada === true || status.bloqueada === 1 || status.bloqueada === "1";

      if (bloqueada || r.status === 403) {
        return {
          permitida: false,
          empresa: empresaAtualizada,
          status,
          msg: status.msg || j?.msg || mensagemEmpresaBloqueadaCarteira(empresaAtualizada)
        };
      }

      if (!r.ok || !j) {
        throw new Error((j && (j.msg || j.erro)) || `Erro ao validar carteira da empresa (HTTP ${r.status})`);
      }

      return { permitida: true, empresa: empresaAtualizada, status };
    } catch (erro) {
      console.warn("validarEmpresaCarteiraVendedorPorId: falhou", { idEmpresa, erro });

      if (empresaBloqueadaCarteiraParaVendedor(empresaBase)) {
        return {
          permitida: false,
          empresa: empresaBase,
          status: null,
          msg: mensagemEmpresaBloqueadaCarteira(empresaBase)
        };
      }

      return {
        permitida: false,
        empresa: empresaBase || { NomeVendedorCarteira: "Vendedor responsável" },
        status: null,
        msg: "Não foi possível validar a carteira desta empresa. Por segurança, o vendedor não pode vincular esta empresa ao card agora."
      };
    }
  }

  function obterEmpresaCatalogoPorId(idEmp){
    const id = idNum(idEmp || 0);
    if (!id) return null;
    return empresasPorId.get(id) || null;
  }

  function obterTextoEmpresaSelecionada(idEmp){
    const emp = obterEmpresaCatalogoPorId(idEmp);
    return emp ? textoOpcaoEmpresa(emp) : "";
  }

  function garantirOpcaoEmpresaNoSelect(idEmp){
    if (!selectEmpresaCard) return;

    const valor = safeStr(idEmp || "").trim();
    if (!valor) return;

    const empresa = obterEmpresaCatalogoPorId(valor);
    const texto = empresa ? textoOpcaoEmpresa(empresa) : `Empresa #${valor}`;
    const opcaoExistente = Array.from(selectEmpresaCard.options || [])
      .find((opt) => safeStr(opt.value || "").trim() === valor);

    if (opcaoExistente) {
      if (safeStr(opcaoExistente.textContent || "").trim() !== texto) {
        opcaoExistente.textContent = texto;
      }
      return;
    }

    selectEmpresaCard.appendChild(el("option", { value: valor }, [texto]));
  }

  async function garantirEmpresaNoCatalogoPorId(idEmp){
    const idEmpresa = idNum(idEmp || 0);
    if (!idEmpresa) return null;

    const existente = obterEmpresaCatalogoPorId(idEmpresa);
    if (existente && empresaTemNomeExibicaoValido(existente)) {
      garantirOpcaoEmpresaNoSelect(idEmpresa);
      return existente;
    }

    try {
      const resposta = await consultarCadastroEmpresa({ idEmpresa });
      const empresa = resposta?.empresa || null;
      if (empresa) {
        atualizarCatalogoEmpresa(empresa);
        garantirOpcaoEmpresaNoSelect(idEmpresa);
        return obterEmpresaCatalogoPorId(idEmpresa) || empresa;
      }
    } catch (erro) {
      console.warn("garantirEmpresaNoCatalogoPorId: falhou", { idEmpresa, erro });
    }

    if (existente) {
      garantirOpcaoEmpresaNoSelect(idEmpresa);
      return existente;
    }

    return null;
  }

  async function selecionarEmpresaPorIdComGarantia(idEmp, dispararChange = true){
    if (!selectEmpresaCard) return;

    const novoValor = safeStr(idEmp || "").trim();
    const valorAnterior = safeStr(selectEmpresaCard.value || "").trim();

    if (!novoValor) {
      definirBloqueioCarteiraEmpresaPrincipal(null);
      selectEmpresaCard.value = "";
      selectEmpresaCard.dataset.valorCarteiraPermitido = "";
      sincronizarBuscaEmpresaComSelect();
      fecharListaEmpresasCombobox();

      if (dispararChange && valorAnterior) {
        selectEmpresaCard.dispatchEvent(new Event("change", { bubbles: true }));
      }
      return;
    }

    const empresaSelecionada = await garantirEmpresaNoCatalogoPorId(novoValor);
    const validacaoCarteira = await validarEmpresaCarteiraVendedorPorId(novoValor, empresaSelecionada);

    if (!validacaoCarteira.permitida) {
      const empresaBloqueada = validacaoCarteira.empresa || empresaSelecionada || { NomeVendedorCarteira: "Vendedor responsável" };
      const mensagemBloqueio = safeStr(validacaoCarteira.msg || empresaBloqueada?.MensagemBloqueioCarteiraVendedor || "").trim() || mensagemEmpresaBloqueadaCarteira(empresaBloqueada);
      const valorSeguroAnterior = safeStr(selectEmpresaCard.dataset.valorCarteiraPermitido || valorAnterior || "").trim();

      mostrarAvisoEmpresaBloqueadaCarteira(empresaBloqueada, mensagemBloqueio, { exibirPopup: true });
      limparSelecaoEmpresaBloqueadaSemPopup(valorSeguroAnterior);
      return;
    }

    definirBloqueioCarteiraEmpresaPrincipal(null);
    garantirOpcaoEmpresaNoSelect(novoValor);

    selectEmpresaCard.value = novoValor;
    selectEmpresaCard.dataset.valorCarteiraPermitido = novoValor;
    sincronizarBuscaEmpresaComSelect();
    fecharListaEmpresasCombobox();

    if (dispararChange && novoValor !== valorAnterior) {
      selectEmpresaCard.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function obterTextoAgenciaSelecionada(idEmp){
    return obterTextoEmpresaSelecionada(idEmp);
  }

  function garantirOpcaoAgenciaNoSelect(idEmp){
    if (!selectAgenciaCard) return;

    const valor = safeStr(idEmp || "").trim();
    if (!valor) return;

    const jaExiste = Array.from(selectAgenciaCard.options || []).some((opt) => safeStr(opt.value || "").trim() === valor);
    if (jaExiste) return;

    const empresa = obterEmpresaCatalogoPorId(valor);
    const texto = empresa ? textoOpcaoEmpresa(empresa) : `Empresa #${valor}`;
    selectAgenciaCard.appendChild(el("option", { value: valor }, [texto]));
  }

  async function garantirAgenciaNoCatalogoPorId(idEmp){
    const empresa = await garantirEmpresaNoCatalogoPorId(idEmp);
    if (empresa) {
      garantirOpcaoAgenciaNoSelect(idEmp);
    }
    return empresa;
  }

  function abrirListaAgenciasCombobox(){
    if (!comboAgenciaCard || !listaAgenciaCardBusca) return;
    comboAgenciaCard.classList.add("is-open");
    listaAgenciaCardBusca.hidden = false;
    renderizarListaAgenciasCombobox(inputAgenciaCardBusca?.value || "");
  }

  function fecharListaAgenciasCombobox(){
    if (!comboAgenciaCard || !listaAgenciaCardBusca) return;
    comboAgenciaCard.classList.remove("is-open");
    listaAgenciaCardBusca.hidden = true;
  }

  function sincronizarBuscaAgenciaComSelect(){
    if (!inputAgenciaCardBusca || !selectAgenciaCard) return;
    inputAgenciaCardBusca.value = obterTextoAgenciaSelecionada(selectAgenciaCard.value || "");
  }

  async function selecionarAgenciaPorIdComGarantia(idEmp, dispararChange = false){
    if (!selectAgenciaCard) return;

    const novoValor = safeStr(idEmp || "").trim();
    const valorAnterior = safeStr(selectAgenciaCard.value || "").trim();

    if (!novoValor) {
      selectAgenciaCard.value = "";
      sincronizarBuscaAgenciaComSelect();
      fecharListaAgenciasCombobox();

      if (dispararChange && valorAnterior) {
        selectAgenciaCard.dispatchEvent(new Event("change", { bubbles: true }));
      }
      sincronizarCnpjsEmpresasRelacionadasCard();
      agendarSincronizacaoFormularioSolicitacao();
      return;
    }

    await garantirAgenciaNoCatalogoPorId(novoValor);
    garantirOpcaoAgenciaNoSelect(novoValor);

    selectAgenciaCard.value = novoValor;
    sincronizarBuscaAgenciaComSelect();
    fecharListaAgenciasCombobox();

    if (dispararChange && novoValor !== valorAnterior) {
      selectAgenciaCard.dispatchEvent(new Event("change", { bubbles: true }));
    }

    sincronizarCnpjsEmpresasRelacionadasCard();
    agendarSincronizacaoFormularioSolicitacao();
  }

  function renderizarListaAgenciasCombobox(texto, opcoes = {}){
    if (!listaAgenciaCardBusca) return;

    const base = Array.isArray(opcoes.empresas)
      ? opcoes.empresas
      : filtrarEmpresasCombobox(texto);

    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    agenciasResultadoComboboxAtual = filtradas.slice();

    const valorSelecionado = safeStr(selectAgenciaCard?.value || "").trim();
    listaAgenciaCardBusca.innerHTML = "";

    if (!filtradas.length) {
      listaAgenciaCardBusca.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Nenhuma agência encontrada."]));
      return;
    }

    filtradas.forEach((item) => {
      const id = safeStr(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? "").trim();
      if (!id) return;

      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "—").trim() || "—";
      const cnpj = mascaraCnpj(item?.CNPJ || item?.EmpresaCNPJ || "");
      const botao = el("button", { type: "button", class: `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [razao]),
        el("span", {}, [cnpj || "Sem CNPJ"])
      ]);

      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarAgenciaPorIdComGarantia(id, false);
      });

      listaAgenciaCardBusca.appendChild(botao);
    });
  }

  function reconciliarBuscaAgenciaDigitada(){
    if (!inputAgenciaCardBusca || !selectAgenciaCard) return;

    const textoDigitado = safeStr(inputAgenciaCardBusca.value || "").trim();

    if (!textoDigitado) {
      selecionarAgenciaPorIdComGarantia("", false).catch((erro) => {
        console.warn("reconciliarBuscaAgenciaDigitada: falhou ao limpar agência", erro);
      });
      return;
    }

    const empresa = localizarEmpresaPorTextoDigitado(textoDigitado);
    if (empresa) {
      const id = safeStr(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? "").trim();
      if (id) {
        selecionarAgenciaPorIdComGarantia(id, false).catch((erro) => {
          console.warn("reconciliarBuscaAgenciaDigitada: falhou ao selecionar agência", erro);
        });
        return;
      }
    }

    sincronizarBuscaAgenciaComSelect();
    fecharListaAgenciasCombobox();
  }

  function obterTextoClienteDiretoSelecionado(idEmp){
    return obterTextoEmpresaSelecionada(idEmp);
  }

  function garantirOpcaoClienteDiretoNoSelect(idEmp){
    if (!selectClienteDiretoCard) return;
    const valor = safeStr(idEmp || "").trim();
    if (!valor) return;
    const jaExiste = Array.from(selectClienteDiretoCard.options || []).some((opt) => safeStr(opt.value || "").trim() === valor);
    if (jaExiste) return;
    const empresa = obterEmpresaCatalogoPorId(valor);
    const texto = empresa ? textoOpcaoEmpresa(empresa) : `Empresa #${valor}`;
    selectClienteDiretoCard.appendChild(el("option", { value: valor }, [texto]));
  }

  async function garantirClienteDiretoNoCatalogoPorId(idEmp){
    const empresa = await garantirEmpresaNoCatalogoPorId(idEmp);
    if (empresa) garantirOpcaoClienteDiretoNoSelect(idEmp);
    return empresa;
  }

  function abrirListaClienteDiretoCombobox(){
    if (!comboClienteDiretoCard || !listaClienteDiretoCardBusca) return;
    comboClienteDiretoCard.classList.add("is-open");
    listaClienteDiretoCardBusca.hidden = false;
    renderizarListaClienteDiretoCombobox(inputClienteDiretoCardBusca?.value || "");
  }

  function fecharListaClienteDiretoCombobox(){
    if (!comboClienteDiretoCard || !listaClienteDiretoCardBusca) return;
    comboClienteDiretoCard.classList.remove("is-open");
    listaClienteDiretoCardBusca.hidden = true;
  }

  function sincronizarBuscaClienteDiretoComSelect(){
    if (!inputClienteDiretoCardBusca || !selectClienteDiretoCard) return;
    inputClienteDiretoCardBusca.value = obterTextoClienteDiretoSelecionado(selectClienteDiretoCard.value || "");
  }

  async function selecionarClienteDiretoPorIdComGarantia(idEmp, dispararChange = false){
    if (!selectClienteDiretoCard) return;
    const novoValor = safeStr(idEmp || "").trim();
    const valorAnterior = safeStr(selectClienteDiretoCard.value || "").trim();
    if (!novoValor) {
      selectClienteDiretoCard.value = "";
      sincronizarBuscaClienteDiretoComSelect();
      fecharListaClienteDiretoCombobox();
      if (dispararChange && valorAnterior) {
        selectClienteDiretoCard.dispatchEvent(new Event("change", { bubbles: true }));
      }
      return;
    }
    await garantirClienteDiretoNoCatalogoPorId(novoValor);
    garantirOpcaoClienteDiretoNoSelect(novoValor);
    selectClienteDiretoCard.value = novoValor;
    sincronizarBuscaClienteDiretoComSelect();
    fecharListaClienteDiretoCombobox();
    if (dispararChange && novoValor !== valorAnterior) {
      selectClienteDiretoCard.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function renderizarListaClienteDiretoCombobox(texto, opcoes = {}){
    if (!listaClienteDiretoCardBusca) return;
    const base = Array.isArray(opcoes.empresas) ? opcoes.empresas : filtrarEmpresasCombobox(texto);
    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    clientesDiretoResultadoComboboxAtual = filtradas.slice();
    const valorSelecionado = safeStr(selectClienteDiretoCard?.value || "").trim();
    listaClienteDiretoCardBusca.innerHTML = "";
    if (!filtradas.length) {
      listaClienteDiretoCardBusca.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Nenhum cliente direto encontrado."]));
      return;
    }
    filtradas.forEach((item) => {
      const id = safeStr(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? "").trim();
      if (!id) return;
      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "—").trim() || "—";
      const cnpj = mascaraCnpj(item?.CNPJ || item?.EmpresaCNPJ || "");
      const botao = el("button", { type: "button", class: `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [razao]),
        el("span", {}, [cnpj || "Sem CNPJ"])
      ]);
      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarClienteDiretoPorIdComGarantia(id, false);
      });
      listaClienteDiretoCardBusca.appendChild(botao);
    });
  }

  function reconciliarBuscaClienteDiretoDigitada(){
    if (!inputClienteDiretoCardBusca || !selectClienteDiretoCard) return;
    const textoDigitado = safeStr(inputClienteDiretoCardBusca.value || "").trim();
    if (!textoDigitado) {
      selecionarClienteDiretoPorIdComGarantia("", false).catch((erro) => {
        console.warn("reconciliarBuscaClienteDiretoDigitada: falhou ao limpar cliente direto", erro);
      });
      return;
    }
    const empresa = localizarEmpresaPorTextoDigitado(textoDigitado);
    if (empresa) {
      const id = safeStr(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? "").trim();
      if (id) {
        selecionarClienteDiretoPorIdComGarantia(id, false).catch((erro) => {
          console.warn("reconciliarBuscaClienteDiretoDigitada: falhou ao selecionar cliente direto", erro);
        });
        return;
      }
    }
    sincronizarBuscaClienteDiretoComSelect();
    fecharListaClienteDiretoCombobox();
  }

  function obterTextoBureauSelecionado(idEmp){
    return obterTextoEmpresaSelecionada(idEmp);
  }

  function garantirOpcaoBureauNoSelect(idEmp){
    if (!selectBureauCard) return;
    const valor = safeStr(idEmp || "").trim();
    if (!valor) return;
    const jaExiste = Array.from(selectBureauCard.options || []).some((opt) => safeStr(opt.value || "").trim() === valor);
    if (jaExiste) return;
    const empresa = obterEmpresaCatalogoPorId(valor);
    const texto = empresa ? textoOpcaoEmpresa(empresa) : `Empresa #${valor}`;
    selectBureauCard.appendChild(el("option", { value: valor }, [texto]));
  }

  async function garantirBureauNoCatalogoPorId(idEmp){
    const empresa = await garantirEmpresaNoCatalogoPorId(idEmp);
    if (empresa) garantirOpcaoBureauNoSelect(idEmp);
    return empresa;
  }

  function abrirListaBureauCombobox(){
    if (!comboBureauCard || !listaBureauCardBusca) return;
    comboBureauCard.classList.add("is-open");
    listaBureauCardBusca.hidden = false;
    renderizarListaBureauCombobox(inputBureauCardBusca?.value || "");
  }

  function fecharListaBureauCombobox(){
    if (!comboBureauCard || !listaBureauCardBusca) return;
    comboBureauCard.classList.remove("is-open");
    listaBureauCardBusca.hidden = true;
  }

  function sincronizarBuscaBureauComSelect(){
    if (!inputBureauCardBusca || !selectBureauCard) return;
    inputBureauCardBusca.value = obterTextoBureauSelecionado(selectBureauCard.value || "");
  }

  async function selecionarBureauPorIdComGarantia(idEmp, dispararChange = false){
    if (!selectBureauCard) return;
    const novoValor = safeStr(idEmp || "").trim();
    const valorAnterior = safeStr(selectBureauCard.value || "").trim();
    if (!novoValor) {
      selectBureauCard.value = "";
      sincronizarBuscaBureauComSelect();
      fecharListaBureauCombobox();
      if (dispararChange && valorAnterior) {
        selectBureauCard.dispatchEvent(new Event("change", { bubbles: true }));
      }
      sincronizarCnpjsEmpresasRelacionadasCard();
      agendarSincronizacaoFormularioSolicitacao();
      return;
    }
    await garantirBureauNoCatalogoPorId(novoValor);
    garantirOpcaoBureauNoSelect(novoValor);
    selectBureauCard.value = novoValor;
    sincronizarBuscaBureauComSelect();
    fecharListaBureauCombobox();
    if (dispararChange && novoValor !== valorAnterior) {
      selectBureauCard.dispatchEvent(new Event("change", { bubbles: true }));
    }

    sincronizarCnpjsEmpresasRelacionadasCard();
    agendarSincronizacaoFormularioSolicitacao();
  }

  function renderizarListaBureauCombobox(texto, opcoes = {}){
    if (!listaBureauCardBusca) return;
    const base = Array.isArray(opcoes.empresas) ? opcoes.empresas : filtrarEmpresasCombobox(texto);
    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    bureauResultadoComboboxAtual = filtradas.slice();
    const valorSelecionado = safeStr(selectBureauCard?.value || "").trim();
    listaBureauCardBusca.innerHTML = "";
    if (!filtradas.length) {
      listaBureauCardBusca.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Nenhum bureau encontrado."]));
      return;
    }
    filtradas.forEach((item) => {
      const id = safeStr(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? "").trim();
      if (!id) return;
      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "—").trim() || "—";
      const cnpj = mascaraCnpj(item?.CNPJ || item?.EmpresaCNPJ || "");
      const botao = el("button", { type: "button", class: `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [razao]),
        el("span", {}, [cnpj || "Sem CNPJ"])
      ]);
      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarBureauPorIdComGarantia(id, false);
      });
      listaBureauCardBusca.appendChild(botao);
    });
  }

  function reconciliarBuscaBureauDigitada(){
    if (!inputBureauCardBusca || !selectBureauCard) return;
    const textoDigitado = safeStr(inputBureauCardBusca.value || "").trim();
    if (!textoDigitado) {
      selecionarBureauPorIdComGarantia("", false).catch((erro) => {
        console.warn("reconciliarBuscaBureauDigitada: falhou ao limpar bureau", erro);
      });
      return;
    }
    const empresa = localizarEmpresaPorTextoDigitado(textoDigitado);
    if (empresa) {
      const id = safeStr(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? "").trim();
      if (id) {
        selecionarBureauPorIdComGarantia(id, false).catch((erro) => {
          console.warn("reconciliarBuscaBureauDigitada: falhou ao selecionar bureau", erro);
        });
        return;
      }
    }
    sincronizarBuscaBureauComSelect();
    fecharListaBureauCombobox();
  }

  function obterTextoIntermediarioSelecionado(idEmp){
    return obterTextoEmpresaSelecionada(idEmp);
  }

  function garantirOpcaoIntermediarioNoSelect(idEmp){
    if (!selectIntermediarioCard) return;
    const valor = safeStr(idEmp || "").trim();
    if (!valor) return;
    const jaExiste = Array.from(selectIntermediarioCard.options || []).some((opt) => safeStr(opt.value || "").trim() === valor);
    if (jaExiste) return;
    const empresa = obterEmpresaCatalogoPorId(valor);
    const texto = empresa ? textoOpcaoEmpresa(empresa) : `Empresa #${valor}`;
    selectIntermediarioCard.appendChild(el("option", { value: valor }, [texto]));
  }

  async function garantirIntermediarioNoCatalogoPorId(idEmp){
    const empresa = await garantirEmpresaNoCatalogoPorId(idEmp);
    if (empresa) garantirOpcaoIntermediarioNoSelect(idEmp);
    return empresa;
  }

  function abrirListaIntermediarioCombobox(){
    if (!comboIntermediarioCard || !listaIntermediarioCardBusca) return;
    comboIntermediarioCard.classList.add("is-open");
    listaIntermediarioCardBusca.hidden = false;
    renderizarListaIntermediarioCombobox(inputIntermediarioCardBusca?.value || "");
  }

  function fecharListaIntermediarioCombobox(){
    if (!comboIntermediarioCard || !listaIntermediarioCardBusca) return;
    comboIntermediarioCard.classList.remove("is-open");
    listaIntermediarioCardBusca.hidden = true;
  }

  function sincronizarBuscaIntermediarioComSelect(){
    if (!inputIntermediarioCardBusca || !selectIntermediarioCard) return;
    inputIntermediarioCardBusca.value = obterTextoIntermediarioSelecionado(selectIntermediarioCard.value || "");
  }

  async function selecionarIntermediarioPorIdComGarantia(idEmp, dispararChange = false){
    if (!selectIntermediarioCard) return;
    const novoValor = safeStr(idEmp || "").trim();
    const valorAnterior = safeStr(selectIntermediarioCard.value || "").trim();
    if (!novoValor) {
      selectIntermediarioCard.value = "";
      sincronizarBuscaIntermediarioComSelect();
      fecharListaIntermediarioCombobox();
      if (dispararChange && valorAnterior) {
        selectIntermediarioCard.dispatchEvent(new Event("change", { bubbles: true }));
      }
      sincronizarCnpjsEmpresasRelacionadasCard();
      agendarSincronizacaoFormularioSolicitacao();
      return;
    }
    await garantirIntermediarioNoCatalogoPorId(novoValor);
    garantirOpcaoIntermediarioNoSelect(novoValor);
    selectIntermediarioCard.value = novoValor;
    sincronizarBuscaIntermediarioComSelect();
    fecharListaIntermediarioCombobox();
    if (dispararChange && novoValor !== valorAnterior) {
      selectIntermediarioCard.dispatchEvent(new Event("change", { bubbles: true }));
    }
    sincronizarCnpjsEmpresasRelacionadasCard();
    agendarSincronizacaoFormularioSolicitacao();
  }

  function renderizarListaIntermediarioCombobox(texto, opcoes = {}){
    if (!listaIntermediarioCardBusca) return;
    const base = Array.isArray(opcoes.empresas) ? opcoes.empresas : filtrarEmpresasCombobox(texto);
    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    intermediariosResultadoComboboxAtual = filtradas.slice();
    const valorSelecionado = safeStr(selectIntermediarioCard?.value || "").trim();
    listaIntermediarioCardBusca.innerHTML = "";
    if (!filtradas.length) {
      listaIntermediarioCardBusca.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Nenhum intermediário encontrado."]));
      return;
    }
    filtradas.forEach((item) => {
      const id = safeStr(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? "").trim();
      if (!id) return;
      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "—").trim() || "—";
      const cnpj = mascaraCnpj(item?.CNPJ || item?.EmpresaCNPJ || "");
      const botao = el("button", { type: "button", class: `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [razao]),
        el("span", {}, [cnpj || "Sem CNPJ"])
      ]);
      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarIntermediarioPorIdComGarantia(id, false);
      });
      listaIntermediarioCardBusca.appendChild(botao);
    });
  }

  function reconciliarBuscaIntermediarioDigitada(){
    if (!inputIntermediarioCardBusca || !selectIntermediarioCard) return;
    const textoDigitado = safeStr(inputIntermediarioCardBusca.value || "").trim();
    if (!textoDigitado) {
      selecionarIntermediarioPorIdComGarantia("", false).catch((erro) => {
        console.warn("reconciliarBuscaIntermediarioDigitada: falhou ao limpar intermediário", erro);
      });
      return;
    }
    const empresa = localizarEmpresaPorTextoDigitado(textoDigitado);
    if (empresa) {
      const id = safeStr(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? "").trim();
      if (id) {
        selecionarIntermediarioPorIdComGarantia(id, false).catch((erro) => {
          console.warn("reconciliarBuscaIntermediarioDigitada: falhou ao selecionar intermediário", erro);
        });
        return;
      }
    }
    sincronizarBuscaIntermediarioComSelect();
    fecharListaIntermediarioCombobox();
  }

  function normalizarTelefoneContato(valor){
    return safeStr(valor || "").replace(/\D+/g, "").slice(0, 30);
  }

  function limparDadosNovoContratoFormulario(){
    if (inputMarcaCard) inputMarcaCard.value = "";
    if (inputTelefoneCard) inputTelefoneCard.value = "";
    if (inputEmailCard) inputEmailCard.value = "";
  }

  function definirEstadoCampoEmpresaRelacionada({ inputBusca, botaoToggle, selectOculto, botaoLimpar, habilitado }){
    const ativo = !!habilitado;

    if (inputBusca) {
      inputBusca.disabled = !ativo;
      inputBusca.readOnly = !ativo;
    }

    if (botaoToggle) {
      botaoToggle.disabled = !ativo;
      botaoToggle.tabIndex = ativo ? 0 : -1;
    }

    if (selectOculto) {
      selectOculto.disabled = !ativo;
    }

    if (botaoLimpar) {
      botaoLimpar.disabled = !ativo;
      botaoLimpar.tabIndex = ativo ? 0 : -1;
    }
  }

  function definirVisibilidadeCampoEmpresaRelacionada(elemento, visivel){
    if (!elemento) return;
    const mostrar = !!visivel;
    elemento.hidden = !mostrar;
    elemento.style.display = mostrar ? "" : "none";
  }

  function campoEmpresaRelacionadaTemValor(selectOculto, inputBusca){
    const idSelecionado = idNum(selectOculto?.value || 0);
    if (idSelecionado > 0) return true;
    return !!safeStr(inputBusca?.value || "").trim();
  }

  function atualizarCnpjEmpresaRelacionadaInput(inputDestino, idEmpresa){
    if (!inputDestino) return;
    const empresa = obterEmpresaCatalogoPorId(idEmpresa || "");
    inputDestino.value = empresa?.CNPJ ? mascaraCnpj(empresa.CNPJ) : "";
  }

  function sincronizarCnpjsEmpresasRelacionadasCard(){
    atualizarCnpjEmpresaRelacionadaInput(inputCnpjAgenciaCard, selectAgenciaCard?.value || "");
    atualizarCnpjEmpresaRelacionadaInput(inputCnpjBureauCard, selectBureauCard?.value || "");
    atualizarCnpjEmpresaRelacionadaInput(inputCnpjIntermediarioCard, selectIntermediarioCard?.value || "");
  }

  function coletarEmpresasRelacionadasPermitidasParaPayload(idTipoCliente){
    const config = obterConfigEmpresasRelacionadasPorTipo(idTipoCliente);
    const dadosAgenciaHeader = obterAgenciaHeaderFormularioSelecionada();
    const dadosBureauHeader = obterEmpresaHeaderFormularioSelecionada("Bureau");
    const dadosIntermediarioHeader = obterEmpresaHeaderFormularioSelecionada("Intermediario");

    return {
      id_empresa_agencia: config.mostrarAgencia
        ? (dadosAgenciaHeader.IDEmpresaAgencia || (selectAgenciaCard?.value ? Number(selectAgenciaCard.value) : null))
        : null,
      id_empresa_cliente_direto: config.mostrarClienteDireto && selectClienteDiretoCard?.value ? Number(selectClienteDiretoCard.value) : null,
      id_empresa_bureau: config.mostrarBureau
        ? (dadosBureauHeader.IDEmpresaBureau || (selectBureauCard?.value ? Number(selectBureauCard.value) : null))
        : null,
      id_empresa_intermediario: config.mostrarIntermediario
        ? (dadosIntermediarioHeader.IDEmpresaIntermediario || (selectIntermediarioCard?.value ? Number(selectIntermediarioCard.value) : null))
        : null,
    };
  }

  function atualizarVisibilidadeEmpresasRelacionadasCard(){
    const config = obterConfigEmpresasRelacionadasPorTipo(selectTipoClienteDescontoCard?.value || "");
    const idFaseAtualModal = idNum(modalCard?.dataset?.idFaseAtual || obterCardPorId(cardAbertoId)?.IDDimKanbanFaseAtual || 0);
    const exibirRelacionadasNaFase4 = idFaseAtualModal === 4;

    if (labelEmpresaPrincipalCard) {
      labelEmpresaPrincipalCard.textContent = config.labelEmpresaPrincipal || "Nome Empresa";
    }

    const agenciaTemValor = campoEmpresaRelacionadaTemValor(selectAgenciaCard, inputAgenciaCardBusca);
    const clienteDiretoTemValor = campoEmpresaRelacionadaTemValor(selectClienteDiretoCard, inputClienteDiretoCardBusca);
    const bureauTemValor = campoEmpresaRelacionadaTemValor(selectBureauCard, inputBureauCardBusca);
    const intermediarioTemValor = campoEmpresaRelacionadaTemValor(selectIntermediarioCard, inputIntermediarioCardBusca);

    const mostrarAgencia = !!config.mostrarAgencia && (exibirRelacionadasNaFase4 || agenciaTemValor);
    const mostrarClienteDireto = !!config.mostrarClienteDireto && (exibirRelacionadasNaFase4 || clienteDiretoTemValor);
    const mostrarBureau = !!config.mostrarBureau && (exibirRelacionadasNaFase4 || bureauTemValor);
    const mostrarIntermediario = !!config.mostrarIntermediario && (exibirRelacionadasNaFase4 || intermediarioTemValor);
    const mostrarWrapRelacionadas = !!config.mostrarWrap && (exibirRelacionadasNaFase4 || mostrarAgencia || mostrarClienteDireto || mostrarBureau || mostrarIntermediario);

    definirVisibilidadeCampoEmpresaRelacionada(wrapEmpresasRelacionadasCard, mostrarWrapRelacionadas);

    definirVisibilidadeCampoEmpresaRelacionada(wrapAgenciaRelacionadaCard, mostrarAgencia);
    if (!mostrarAgencia) fecharListaAgenciasCombobox();
    definirEstadoCampoEmpresaRelacionada({
      inputBusca: inputAgenciaCardBusca,
      botaoToggle: btnToggleAgenciaCard,
      selectOculto: selectAgenciaCard,
      botaoLimpar: btnLimparAgencia,
      habilitado: mostrarAgencia,
    });

    definirVisibilidadeCampoEmpresaRelacionada(wrapClienteDiretoCard, mostrarClienteDireto);
    if (!mostrarClienteDireto) fecharListaClienteDiretoCombobox();
    definirEstadoCampoEmpresaRelacionada({
      inputBusca: inputClienteDiretoCardBusca,
      botaoToggle: btnToggleClienteDiretoCard,
      selectOculto: selectClienteDiretoCard,
      botaoLimpar: btnLimparClienteDireto,
      habilitado: mostrarClienteDireto,
    });

    definirVisibilidadeCampoEmpresaRelacionada(wrapBureauCard, mostrarBureau);
    if (!mostrarBureau) fecharListaBureauCombobox();
    definirEstadoCampoEmpresaRelacionada({
      inputBusca: inputBureauCardBusca,
      botaoToggle: btnToggleBureauCard,
      selectOculto: selectBureauCard,
      botaoLimpar: btnLimparBureau,
      habilitado: mostrarBureau,
    });

    definirVisibilidadeCampoEmpresaRelacionada(wrapIntermediarioCard, mostrarIntermediario);
    if (!mostrarIntermediario) fecharListaIntermediarioCombobox();
    definirEstadoCampoEmpresaRelacionada({
      inputBusca: inputIntermediarioCardBusca,
      botaoToggle: btnToggleIntermediarioCard,
      selectOculto: selectIntermediarioCard,
      botaoLimpar: btnLimparIntermediario,
      habilitado: mostrarIntermediario,
    });

    sincronizarCnpjsEmpresasRelacionadasCard();
  }

  function atualizarVisibilidadeDadosNovoContrato(){
    if (!wrapDadosNovoContrato) return;

    /*
     * Marca, telefone e email não são dados exclusivos de "Novo Contrato".
     * Eles são dados comerciais do atendimento/card e precisam continuar visíveis
     * no fluxo de Aditivo. Antes, ao selecionar Aditivo, o bloco era ocultado e,
     * no salvamento seguinte, esses campos podiam ser enviados como nulos.
     */
    wrapDadosNovoContrato.hidden = false;
    wrapDadosNovoContrato.style.display = "";
    wrapDadosNovoContrato.setAttribute("aria-hidden", "false");

    sincronizarBuscaAgenciaComSelect();
  }

  function registrarEmpresasNoCatalogo(listaEmpresas) {
    const lista = Array.isArray(listaEmpresas) ? listaEmpresas : [];
    const adicionadas = [];

    for (const item of lista) {
      const id = idNum(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? item?.id_empresa ?? 0);
      if (!id) continue;

      const atual = empresasPorId.get(id) || null;
      const registro = Object.assign({}, atual || {}, item || {}, {
        IDEmpresa: id,
        ID: id
      });

      if (!empresaTemNomeExibicaoValido(registro) && empresaTemNomeExibicaoValido(atual)) {
        registro.RazaoSocial = nomeExibicaoEmpresa(atual);
      }

      empresasPorId.set(id, registro);

      const idx = empresasCatalogo.findIndex((empresa) => idNum(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? 0) === id);
      if (idx >= 0) empresasCatalogo[idx] = registro;
      else empresasCatalogo.push(registro);

      adicionadas.push(registro);
    }

    empresasCatalogo.sort((a, b) => {
      const nomeA = nomeExibicaoEmpresa(a);
      const nomeB = nomeExibicaoEmpresa(b);
      return nomeA.localeCompare(nomeB, "pt-BR");
    });

    return adicionadas;
  }

  function limparTermoBuscaEmpresaRemota(textoBusca){
    let termo = safeStr(textoBusca || "").trim();

    // Evita HTTP 414 quando algum HTML/template entra no campo por cache, colagem ou estado antigo.
    if (termo.includes("{%") || termo.includes("%}") || /<\/?[a-z][\s\S]*>/i.test(termo)) {
      termo = termo
        .replace(/<script[\s\S]*?<\/script>/gi, " ")
        .replace(/<style[\s\S]*?<\/style>/gi, " ")
        .replace(/<[^>]*>/g, " ")
        .replace(/\{[%#][\s\S]*?[%#]\}/g, " ")
        .replace(/\{\{[\s\S]*?\}\}/g, " ");
    }

    termo = termo.replace(/\s+/g, " ").trim();

    if (termo.length > 120) {
      termo = termo.slice(0, 120).trim();
    }

    return termo;
  }

  async function buscarEmpresasRemoto(textoBusca = "", opcoes = {}) {
    const termo = limparTermoBuscaEmpresaRemota(textoBusca);
    const tipoBusca = safeStr(opcoes?.tipo || "empresa").trim().toLowerCase() === "agencia" ? "agencia" : "empresa";
    const listaDestino = opcoes?.listaDestino || (tipoBusca === "agencia" ? listaAgenciaCardBusca : listaEmpresaCardBusca);
    const renderizador = typeof opcoes?.renderizador === "function"
      ? opcoes.renderizador
      : (tipoBusca === "agencia" ? renderizarListaAgenciasCombobox : renderizarListaEmpresasCombobox);

    const controladorAtual = tipoBusca === "agencia" ? agenciaBuscaRemotaController : empresaBuscaRemotaController;
    if (controladorAtual) {
      controladorAtual.abort();
    }

    const controller = new AbortController();
    if (tipoBusca === "agencia") {
      agenciaBuscaRemotaController = controller;
    } else {
      empresaBuscaRemotaController = controller;
    }

    if (listaDestino) {
      listaDestino.innerHTML = "";
      listaDestino.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Buscando empresas..."]))
    }

    const query = new URLSearchParams();
    if (termo) query.set("q", termo);
    query.set("_", String(Date.now()));

    try {
      const url = query.toString()
        ? `/kanban/api/empresas/buscar?${query.toString()}`
        : `/kanban/api/empresas/buscar`;

      const r = await fetch(montarUrlKanban(url), {
        credentials: "same-origin",
        signal: controller.signal,
      });
      const j = await r.json().catch(() => null);

      const controladorVigente = tipoBusca === "agencia" ? agenciaBuscaRemotaController : empresaBuscaRemotaController;
      if (controladorVigente !== controller) {
        return [];
      }

      if (!r.ok || !j || !j.ok) {
        throw new Error((j && (j.msg || j.erro)) || `Erro ao buscar empresas (HTTP ${r.status})`);
      }

      const empresas = registrarEmpresasNoCatalogo(Array.isArray(j.empresas) ? j.empresas : []);
      renderizador(termo, { empresas });
      return empresas;
    } catch (erro) {
      if (erro?.name === "AbortError") {
        return [];
      }

      console.warn("buscarEmpresasRemoto: falhou", erro);
      renderizador(termo);
      return [];
    } finally {
      if (tipoBusca === "agencia") {
        if (agenciaBuscaRemotaController === controller) {
          agenciaBuscaRemotaController = null;
        }
      } else if (empresaBuscaRemotaController === controller) {
        empresaBuscaRemotaController = null;
      }
    }
  }

  function localizarEmpresaPorTextoDigitado(texto){
    const bruto = safeStr(texto || "").trim();
    if (!bruto) return null;

    const textoNormalizado = normalizarTexto(bruto);
    const digitos = normalizaCnpj(bruto);

    const candidatos = empresasCatalogo.filter((item) => {
      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "").trim();
      const cnpj = safeStr(item?.CNPJ || item?.EmpresaCNPJ || "").trim();
      const textoOpcao = textoOpcaoEmpresa(item);

      return normalizarTexto(textoOpcao) === textoNormalizado
        || normalizarTexto(razao) === textoNormalizado
        || (!!digitos && normalizaCnpj(cnpj) === digitos);
    });

    return candidatos[0] || null;
  }

  function filtrarEmpresasCombobox(texto){
    const termoBruto = safeStr(texto || "").trim();
    const termoNormalizado = normalizarTexto(termoBruto);
    const termoDigitos = normalizaCnpj(termoBruto);

    if (!termoNormalizado && !termoDigitos) {
      return empresasCatalogo.slice(0, LIMITE_EMPRESAS_COMBOBOX);
    }

    return empresasCatalogo.filter((item) => {
      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "");
      const cnpj = safeStr(item?.CNPJ || item?.EmpresaCNPJ || "");
      const textoOpcao = textoOpcaoEmpresa(item);

      const bateTexto = termoNormalizado
        ? normalizarTexto(textoOpcao).includes(termoNormalizado)
          || normalizarTexto(razao).includes(termoNormalizado)
        : false;

      const bateCnpj = termoDigitos
        ? normalizaCnpj(cnpj).includes(termoDigitos)
        : false;

      return bateTexto || bateCnpj;
    }).slice(0, LIMITE_EMPRESAS_COMBOBOX);
  }

  function abrirListaEmpresasCombobox(){
    if (!comboEmpresaCard || !listaEmpresaCardBusca) return;
    comboEmpresaCard.classList.add("is-open");
    listaEmpresaCardBusca.hidden = false;
    renderizarListaEmpresasCombobox(inputEmpresaCardBusca?.value || "");
  }

  function fecharListaEmpresasCombobox(){
    if (!comboEmpresaCard || !listaEmpresaCardBusca) return;
    comboEmpresaCard.classList.remove("is-open");
    listaEmpresaCardBusca.hidden = true;
  }

  function sincronizarBuscaEmpresaComSelect(){
    if (!inputEmpresaCardBusca || !selectEmpresaCard) return;
    inputEmpresaCardBusca.value = obterTextoEmpresaSelecionada(selectEmpresaCard.value || "");
  }

  async function selecionarEmpresaCombobox(idEmp, dispararChange = true){
    await selecionarEmpresaPorIdComGarantia(idEmp, dispararChange);
  }

  function renderizarListaEmpresasCombobox(texto, opcoes = {}){
    if (!listaEmpresaCardBusca) return;

    const base = Array.isArray(opcoes.empresas)
      ? opcoes.empresas
      : filtrarEmpresasCombobox(texto);

    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    empresasResultadoComboboxAtual = filtradas.slice();

    const valorSelecionado = safeStr(selectEmpresaCard?.value || "").trim();
    listaEmpresaCardBusca.innerHTML = "";

    if (!filtradas.length) {
      listaEmpresaCardBusca.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Nenhuma empresa encontrada."]));
      return;
    }

    filtradas.forEach((item) => {
      const id = safeStr(item?.IDEmpresa ?? item?.IDEmpresaProprietaria ?? item?.ID ?? "").trim();
      if (!id) return;

      const razao = safeStr(item?.RazaoSocial || item?.EmpresaRazaoSocial || "—").trim() || "—";
      const cnpj = mascaraCnpj(item?.CNPJ || item?.EmpresaCNPJ || "");
      const bloqueadaCarteira = empresaBloqueadaCarteiraParaVendedor(item);
      const classeBotao = `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}${bloqueadaCarteira ? " is-disabled" : ""}`;
      const filhosBotao = [
        el("strong", {}, [razao]),
        el("span", {}, [cnpj || "Sem CNPJ"])
      ];

      if (bloqueadaCarteira) {
        filhosBotao.push(el("span", { class: "kb-combobox-aviso" }, [mensagemEmpresaBloqueadaCarteira(item)]));
      }

      const botao = el("button", {
        type: "button",
        class: classeBotao,
        title: bloqueadaCarteira ? mensagemEmpresaBloqueadaCarteira(item) : "",
        "aria-disabled": bloqueadaCarteira ? "true" : "false"
      }, filhosBotao);

      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();

        if (bloqueadaCarteira) {
          const valorSeguroAnterior = safeStr(selectEmpresaCard?.dataset?.valorCarteiraPermitido || selectEmpresaCard?.value || "").trim();
          mostrarAvisoEmpresaBloqueadaCarteira(item, "", { exibirPopup: true });
          limparSelecaoEmpresaBloqueadaSemPopup(valorSeguroAnterior);
          return;
        }

        await selecionarEmpresaCombobox(id, true);
      });

      listaEmpresaCardBusca.appendChild(botao);
    });
  }

  function reconciliarBuscaEmpresaDigitada(){
    if (!inputEmpresaCardBusca || !selectEmpresaCard) return;

    const textoDigitado = safeStr(inputEmpresaCardBusca.value || "").trim();

    if (!textoDigitado) {
      selecionarEmpresaPorIdComGarantia("", true).catch((erro) => {
        console.warn("reconciliarBuscaEmpresaDigitada: falhou ao limpar empresa", erro);
      });
      return;
    }

    const empresa = localizarEmpresaPorTextoDigitado(textoDigitado);
    if (empresa) {
      const id = safeStr(empresa?.IDEmpresa ?? empresa?.IDEmpresaProprietaria ?? empresa?.ID ?? "").trim();
      if (id) {
        selecionarEmpresaCombobox(id, true).catch((erro) => {
          console.warn("reconciliarBuscaEmpresaDigitada: falhou ao selecionar empresa", erro);
        });
        return;
      }
    }

    sincronizarBuscaEmpresaComSelect();
    fecharListaEmpresasCombobox();
  }

  function textoOpcaoCnae(item){
    const classe = safeStr(item?.Classe || "").trim();
    const descricao = safeStr(item?.Descricao || "").trim();
    if (classe && descricao) return `${classe} • ${descricao}`;
    return classe || descricao || "";
  }

  function registrarCnaesNoCatalogo(lista){
    const adicionadas = [];
    for (const bruto of (Array.isArray(lista) ? lista : [])) {
      const id = idNum(bruto?.IDDimCnaes || bruto?.id_dim_cnaes || 0);
      if (!id) continue;
      const registro = {
        IDDimCnaes: id,
        cnaepadrao: safeStr(bruto?.cnaepadrao || "").trim(),
        Classe: safeStr(bruto?.Classe || "").trim(),
        Descricao: safeStr(bruto?.Descricao || "").trim(),
        Setor: safeStr(bruto?.Setor || "").trim(),
        MacroSetor: safeStr(bruto?.MacroSetor || "").trim(),
        SubClasse: safeStr(bruto?.SubClasse || "").trim()
      };
      const idx = cnaesCatalogo.findIndex((item) => idNum(item?.IDDimCnaes || 0) === id);
      if (idx >= 0) cnaesCatalogo[idx] = registro;
      else cnaesCatalogo.push(registro);
      adicionadas.push(registro);
    }

    cnaesCatalogo.sort((a, b) => textoOpcaoCnae(a).localeCompare(textoOpcaoCnae(b), "pt-BR"));
    return adicionadas;
  }

  function filtrarCnaesCombobox(texto){
    const termo = normalizarTexto(safeStr(texto || "").trim());
    if (!termo) return cnaesCatalogo.slice(0, LIMITE_EMPRESAS_COMBOBOX);
    return cnaesCatalogo.filter((item) => {
      return normalizarTexto(textoOpcaoCnae(item)).includes(termo)
        || normalizarTexto(item?.Classe || "").includes(termo)
        || normalizarTexto(item?.Descricao || "").includes(termo)
        || normalizarTexto(item?.Setor || "").includes(termo)
        || normalizarTexto(item?.cnaepadrao || "").includes(termo);
    }).slice(0, LIMITE_EMPRESAS_COMBOBOX);
  }

  function obterTextoCnaeSelecionado(idCnae){
    const id = idNum(idCnae || 0);
    if (!id) return "";
    const item = cnaesCatalogo.find((registro) => idNum(registro?.IDDimCnaes || 0) === id);
    return item ? textoOpcaoCnae(item) : "";
  }

  function sincronizarBuscaSegmentoComSelect(){
    if (!inputSegmentoCardBusca || !selectSegmentoCard) return;

    const textoSelecionado = obterTextoCnaeSelecionado(selectSegmentoCard.value || "");
    if (textoSelecionado) {
      inputSegmentoCardBusca.value = textoSelecionado;
      return;
    }

    if (!safeStr(selectSegmentoCard.value || "").trim()) {
      inputSegmentoCardBusca.value = "";
    }
  }

  function registrarSegmentoDoCardNoCatalogo(card){
    const origem = card || {};
    const idSegmento = idNum(
      origem.IDDimCnaes ??
      origem.id_dim_cnaes ??
      origem.EmpresaIDDimCnaes ??
      origem.IDDimCnae ??
      origem.id_dim_cnae ??
      0
    );

    if (!idSegmento) return "";

    const cnaePadrao = safeStr(
      origem.cnaepadrao ??
      origem.CnaePadrao ??
      origem.SegmentoCnae ??
      origem.EmpresaCNAE ??
      origem.CNAE ??
      origem.cnae_empresa ??
      ""
    ).trim();

    const classe = safeStr(
      origem.SegmentoClasse ??
      origem.EmpresaClasse ??
      origem.Classe ??
      origem.classe ??
      ""
    ).trim();

    const descricao = safeStr(
      origem.SegmentoDescricao ??
      origem.DescricaoCnae ??
      origem.EmpresaDescricaoCnae ??
      origem.descricao_cnae ??
      origem.CnaeDescricao ??
      classe ??
      ""
    ).trim();

    const setor = safeStr(
      origem.SegmentoSetor ??
      origem.EmpresaSetor ??
      origem.Setor ??
      origem.setor ??
      ""
    ).trim();

    const macroSetor = safeStr(
      origem.SegmentoMacroSetor ??
      origem.EmpresaMacroSetor ??
      origem.MacroSetor ??
      origem.macro_setor ??
      ""
    ).trim();

    const subClasse = safeStr(
      origem.SegmentoSubClasse ??
      origem.EmpresaSubClasse ??
      origem.SubClasse ??
      origem.sub_classe ??
      ""
    ).trim();

    registrarCnaesNoCatalogo([{
      IDDimCnaes: idSegmento,
      cnaepadrao: cnaePadrao,
      Classe: classe || descricao || `Segmento ${idSegmento}`,
      Descricao: descricao || classe || `Segmento ${idSegmento}`,
      Setor: setor,
      MacroSetor: macroSetor,
      SubClasse: subClasse
    }]);

    return String(idSegmento);
  }

  async function restaurarSegmentoCard(card){
    if (!selectSegmentoCard) return;

    const idSegmentoPersistido = registrarSegmentoDoCardNoCatalogo(card);
    if (idSegmentoPersistido) {
      await selecionarCnaePorIdComGarantia(idSegmentoPersistido, false);
      if (safeStr(selectSegmentoCard.value || "").trim()) return;
    }

    const cnaeEmpresa = safeStr(
      card?.EmpresaCNAE ??
      card?.CNAE ??
      card?.cnae_empresa ??
      card?.cnaepadrao ??
      ""
    ).trim();

    if (cnaeEmpresa) {
      const cnaeEncontrado = await garantirCnaeNoCatalogoPorCodigo(cnaeEmpresa);
      if (cnaeEncontrado?.IDDimCnaes) {
        await selecionarCnaePorIdComGarantia(String(cnaeEncontrado.IDDimCnaes), false);
        if (safeStr(selectSegmentoCard.value || "").trim()) return;
      }
    }

    const classeFallback = safeStr(card?.SegmentoClasse ?? card?.EmpresaClasse ?? card?.Classe ?? "").trim();
    const descricaoFallback = safeStr(card?.SegmentoDescricao ?? card?.DescricaoCnae ?? card?.EmpresaDescricaoCnae ?? card?.descricao_cnae ?? "").trim();

    if (inputSegmentoCardBusca && (classeFallback || descricaoFallback)) {
      inputSegmentoCardBusca.value = classeFallback && descricaoFallback
        ? `${classeFallback} • ${descricaoFallback}`
        : (classeFallback || descricaoFallback);
    }
  }

  function normalizarCnaeComparacao(valor){
    return safeStr(valor || "").replace(/\D+/g, "").trim();
  }

  function localizarCnaePorCodigoNoCatalogo(cnaeEmpresa){
    const alvo = normalizarCnaeComparacao(cnaeEmpresa);
    if (!alvo) return null;

    return cnaesCatalogo.find((item) => normalizarCnaeComparacao(item?.cnaepadrao) === alvo) || null;
  }

  async function garantirCnaeNoCatalogoPorCodigo(cnaeEmpresa){
    const alvo = normalizarCnaeComparacao(cnaeEmpresa);
    if (!alvo) return null;

    let existente = localizarCnaePorCodigoNoCatalogo(alvo);
    if (existente) return existente;

    try {
      const resposta = await buscarCnaesRemoto(alvo);
      const lista = Array.isArray(resposta) ? resposta : (Array.isArray(resposta?.cnaes) ? resposta.cnaes : []);
      registrarCnaesNoCatalogo(lista);
      existente = localizarCnaePorCodigoNoCatalogo(alvo);
      if (existente) return existente;
    } catch (erro) {
      console.warn("garantirCnaeNoCatalogoPorCodigo: falhou ao buscar CNAE remoto", erro);
    }

    return null;
  }

  async function aplicarSegmentoAutomaticoDaEmpresaSelecionada(idEmp){
    const idEmpresa = idNum(idEmp || 0);
    if (!idEmpresa || !selectSegmentoCard) return;

    let empresa = obterEmpresaCatalogoPorId(idEmpresa);

    if (!empresa || (!safeStr(empresa?.CNAE).trim() && !safeStr(empresa?.Classe).trim())) {
      try {
        const resposta = await consultarCadastroEmpresa({ idEmpresa });
        if (resposta?.empresa) {
          atualizarCatalogoEmpresa(resposta.empresa);
          empresa = resposta.empresa;
        }
      } catch (erro) {
        console.warn("aplicarSegmentoAutomaticoDaEmpresaSelecionada: falhou ao consultar cadastro da empresa", erro);
      }
    }

    if (!empresa) return;

    const cnaeEmpresa = safeStr(empresa?.CNAE || "").trim();
    if (!cnaeEmpresa) return;

    const cnaeEncontrado = await garantirCnaeNoCatalogoPorCodigo(cnaeEmpresa);
    if (!cnaeEncontrado?.IDDimCnaes) return;

    await selecionarCnaePorIdComGarantia(String(cnaeEncontrado.IDDimCnaes), false);
    agendarSincronizacaoFormularioSolicitacao();
  }

  function renderizarListaCnaesCombobox(texto, opcoes = {}){
    if (!listaSegmentoCardBusca) return;
    const base = Array.isArray(opcoes.cnaes) ? opcoes.cnaes : filtrarCnaesCombobox(texto);
    const filtradas = (Array.isArray(base) ? base : []).slice(0, LIMITE_EMPRESAS_COMBOBOX);
    cnaesResultadoComboboxAtual = filtradas.slice();
    const valorSelecionado = safeStr(selectSegmentoCard?.value || "").trim();
    listaSegmentoCardBusca.innerHTML = "";

    if (!filtradas.length){
      listaSegmentoCardBusca.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Nenhum segmento encontrado."]));
      return;
    }

    filtradas.forEach((item) => {
      const id = safeStr(item?.IDDimCnaes || "").trim();
      if (!id) return;
      const botao = el("button", { type: "button", class: `kb-combobox-opcao${id === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [safeStr(item?.Classe || item?.Descricao || "—") || "—"]),
        el("span", {}, [safeStr(item?.Descricao || item?.Classe || "Sem descrição") || "Sem descrição"])
      ]);
      botao.addEventListener("mousedown", async (evento) => {
        evento.preventDefault();
        await selecionarCnaePorIdComGarantia(id, true);
      });
      listaSegmentoCardBusca.appendChild(botao);
    });
  }

  function abrirListaCnaesCombobox(){
    if (!comboSegmentoCard || !listaSegmentoCardBusca) return;
    comboSegmentoCard.classList.add("is-open");
    listaSegmentoCardBusca.hidden = false;
    renderizarListaCnaesCombobox(inputSegmentoCardBusca?.value || "");
  }

  function fecharListaCnaesCombobox(){
    if (!comboSegmentoCard || !listaSegmentoCardBusca) return;
    comboSegmentoCard.classList.remove("is-open");
    listaSegmentoCardBusca.hidden = true;
  }

  async function buscarCnaesRemoto(textoBusca = ""){
    const termo = safeStr(textoBusca || "").trim();
    if (cnaeBuscaRemotaController) cnaeBuscaRemotaController.abort();
    const controller = new AbortController();
    cnaeBuscaRemotaController = controller;

    if (listaSegmentoCardBusca){
      listaSegmentoCardBusca.innerHTML = "";
      listaSegmentoCardBusca.appendChild(el("div", { class: "kb-combobox-vazio" }, ["Buscando segmentos..."]))
    }

    const query = new URLSearchParams();
    if (termo) query.set("q", termo);

    try {
      const url = query.toString() ? `/kanban/api/cnaes/buscar?${query.toString()}` : `/kanban/api/cnaes/buscar`;
      const resultado = await fetchJsonKanban(url, { signal: controller.signal });
      const j = resultado.corpo;
      if (cnaeBuscaRemotaController !== controller) return [];

      if (!respostaJsonKanbanOk(resultado)) {
        console.warn("buscarCnaesRemoto: resposta inválida", detalhesFalhaJsonKanban(resultado));
        renderizarListaCnaesCombobox(termo);
        return [];
      }

      const cnaes = registrarCnaesNoCatalogo(Array.isArray(j.cnaes) ? j.cnaes : []);
      renderizarListaCnaesCombobox(termo, { cnaes });
      return cnaes;
    } catch (erro) {
      if (erro?.name === "AbortError") return [];
      console.warn("buscarCnaesRemoto: falhou", erro);
      renderizarListaCnaesCombobox(termo);
      return [];
    } finally {
      if (cnaeBuscaRemotaController === controller) cnaeBuscaRemotaController = null;
    }
  }

  async function selecionarCnaePorIdComGarantia(idCnae, dispararChange = true){
    if (!selectSegmentoCard) return;
    const valor = safeStr(idCnae || "").trim();
    if (!valor){
      selectSegmentoCard.value = "";
      sincronizarBuscaSegmentoComSelect();
      fecharListaCnaesCombobox();
      if (dispararChange) selectSegmentoCard.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    let item = cnaesCatalogo.find((registro) => safeStr(registro?.IDDimCnaes || "").trim() === valor);
    if (!item){
      const encontrados = await buscarCnaesRemoto(valor);
      item = (encontrados || []).find((registro) => safeStr(registro?.IDDimCnaes || "").trim() === valor) || null;
    }

    if (!item) return;

    let option = Array.from(selectSegmentoCard.options || []).find((opt) => safeStr(opt.value || "").trim() === valor);
    if (!option){
      option = new Option(textoOpcaoCnae(item), valor, false, false);
      selectSegmentoCard.appendChild(option);
    } else {
      option.text = textoOpcaoCnae(item);
    }

    selectSegmentoCard.value = valor;
    sincronizarBuscaSegmentoComSelect();
    fecharListaCnaesCombobox();
    if (dispararChange) selectSegmentoCard.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function reconciliarBuscaSegmentoDigitada(){
    if (!inputSegmentoCardBusca || !selectSegmentoCard) return;
    const textoDigitado = safeStr(inputSegmentoCardBusca.value || "").trim();
    if (!textoDigitado){
      selecionarCnaePorIdComGarantia("", true).catch((erro) => console.warn("limpar segmento falhou", erro));
      return;
    }

    const textoNormalizado = normalizarTexto(textoDigitado);
    const item = cnaesCatalogo.find((registro) => normalizarTexto(textoOpcaoCnae(registro)) === textoNormalizado);
    if (item){
      selecionarCnaePorIdComGarantia(String(item.IDDimCnaes), true).catch((erro) => console.warn("selecionar segmento falhou", erro));
      return;
    }

    sincronizarBuscaSegmentoComSelect();
    fecharListaCnaesCombobox();
  }


function montarSelectTipoClienteDesconto(valorSelecionado = null){
  if (!selectTipoClienteDescontoCard) return;

  const valorAtual = (
    valorSelecionado !== null && valorSelecionado !== undefined
      ? safeStr(valorSelecionado).trim()
      : safeStr(selectTipoClienteDescontoCard.value || "").trim()
  );

  selectTipoClienteDescontoCard.innerHTML = "";
  selectTipoClienteDescontoCard.appendChild(
    el("option", { value: "" }, ["— Selecione —"])
  );

  selectTipoClienteDescontoCard.disabled = false;

  tiposClienteDescontoCatalogo.forEach((item) => {
    const id = idNum(item?.IDDimKanbanTipoClienteDesconto || item?.IDDimTipoCliente || 0);
    const nome = safeStr(item?.TipoCliente || item?.NomeTipoCliente || "").trim();
    if (!id || !nome) return;

    selectTipoClienteDescontoCard.appendChild(
      el("option", { value: String(id) }, [nome])
    );
  });

  if (valorAtual) {
    selectTipoClienteDescontoCard.value = String(valorAtual);
  }
}


function montarSelectOrigemAtendimento(valorSelecionado = null){
  if (!selectOrigemAtendimentoCard) return;

  const valorAtual = (
    valorSelecionado !== null && valorSelecionado !== undefined
      ? safeStr(valorSelecionado).trim()
      : safeStr(selectOrigemAtendimentoCard.value || "").trim()
  );

  selectOrigemAtendimentoCard.innerHTML = "";
  selectOrigemAtendimentoCard.appendChild(
    el("option", { value: "" }, ["— Selecione —"])
  );

  selectOrigemAtendimentoCard.disabled = false;

  origensAtendimentoCatalogo.forEach((item) => {
    const id = idNum(item?.IDDimOrigemAtendimento || 0);
    const nome = safeStr(item?.NomeOrigemAtendimento || "").trim();
    if (!id || !nome) return;

    selectOrigemAtendimentoCard.appendChild(
      el("option", { value: String(id) }, [nome])
    );
  });

  if (valorAtual) {
    selectOrigemAtendimentoCard.value = String(valorAtual);
  }
}




  function setEmpresaPreviewByObj(emp) {
    const box = document.getElementById("empresaPreview");
    const wrap = box?.closest(".kb-empresa-box");

    const razao = nomeExibicaoEmpresa(emp) || "";
    const cnpj = mascaraCnpj(emp?.CNPJ || emp?.EmpresaCNPJ || emp?.cnpj || "");
    const cnae = safeStr(emp?.CNAE || emp?.EmpresaCNAE || emp?.cnae || "").trim();
    const setor = safeStr(emp?.Setor || emp?.EmpresaSetor || emp?.CnaeSetor || emp?.setor_cnae || "").trim();
    const classe = safeStr(emp?.Classe || emp?.EmpresaClasse || emp?.CnaeClasse || emp?.classe_cnae || "").trim();

    const tem = !!(emp && (razao || cnpj || cnae || setor || classe));

    if (wrap) wrap.classList.toggle("has-empresa", tem);

    if (!tem) {
      if (box) box.style.display = "none";
      ["empPrevRazao", "empPrevCnpj", "empPrevCnae", "empPrevSetor", "empPrevClasse"].forEach((idCampo) => {
        const campo = document.getElementById(idCampo);
        if (!campo) return;
        campo.textContent = "";
        const linha = campo.closest?.(".linha");
        if (linha) linha.style.display = "none";
      });
      return;
    }

    if (box) box.style.display = "grid";

    const aplicarValorPreview = (idCampo, valor) => {
      const campo = document.getElementById(idCampo);
      if (!campo) return;
      const texto = safeStr(valor || "").trim();
      campo.textContent = texto;
      campo.title = texto;
      const linha = campo.closest?.(".linha");
      if (linha) linha.style.display = texto ? "" : "none";
    };

    aplicarValorPreview("empPrevRazao", razao);
    aplicarValorPreview("empPrevCnpj", cnpj);
    aplicarValorPreview("empPrevCnae", cnae);
    aplicarValorPreview("empPrevSetor", setor);
    aplicarValorPreview("empPrevClasse", classe);
  }

  function setEmpresaPreviewById(idEmp) {
    const id = Number(idEmp || 0);
    if (!id) {
      setEmpresaPreviewByObj(null);
      return;
    }
    setEmpresaPreviewByObj(empresasPorId.get(id) || null);
  }

  function limparMensagemFluxoContrato() {
    if (!msgFluxoContrato) return;
    msgFluxoContrato.className = "kb-contrato-status";
    msgFluxoContrato.textContent = "";
  }

  function setMensagemFluxoContrato(texto, tipo = "info") {
    if (!msgFluxoContrato) return;

    const mensagem = safeStr(texto).trim();
    msgFluxoContrato.className = "kb-contrato-status";
    msgFluxoContrato.textContent = mensagem;

    if (!mensagem) {
      return;
    }

    if (tipo === "erro") msgFluxoContrato.classList.add("is-erro");
    else if (tipo === "sucesso") msgFluxoContrato.classList.add("is-sucesso");
    else if (tipo === "alerta") msgFluxoContrato.classList.add("is-alerta");
    else msgFluxoContrato.classList.add("is-info");
  }

  function textoAvisoEmpresaJaTemContratos() {
    return "Essa empresa já tem contratos conosco. Caso precise fazer um aditivo, selecione o contrato abaixo; se for uma venda totalmente nova, pode continuar como Novo Contrato.";
  }

  function limparSelectComPlaceholder(selectEl, textoPlaceholder) {
    if (!selectEl) return;
    selectEl.innerHTML = "";
    selectEl.appendChild(el("option", { value: "" }, [textoPlaceholder]));
  }

  function resetarFluxoContrato() {
    limparMensagemFluxoContrato();

    limparSelectComPlaceholder(selectContratoCard, "— Selecione um contrato —");
    contratosCardCatalogo = [];
    contratosResultadoComboboxAtual = [];
    if (inputContratoCardBusca) inputContratoCardBusca.value = "";
    fecharListaContratosCombobox();

    limparSelectComPlaceholder(selectModoContratoCard, "— Selecione o tipo —");
    limparSelectComPlaceholder(selectCodPontoContratoCard, "— Selecione o CodPonto —");
    limparSelectComPlaceholder(selectCodFaceContratoCard, "— Selecione o CodFace —");

    if (wrapSelectContratoCard) wrapSelectContratoCard.hidden = true;
    if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = true;
    if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = true;
    if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = true;
    atualizarVisibilidadeDadosNovoContrato();
  }

  function normalizarContratoKanban(item) {
    if (!item || typeof item !== "object") return null;

    const idContrato = idNum(
      item.IDFatoControleContratosEuromidia ??
      item.IDFatoControleContratoEuromidia ??
      item.IDFatoControleContrato ??
      item.id_controle_contrato ??
      item.idContrato ??
      item.ID ??
      0
    );

    if (!idContrato) return null;

    const numeroContrato = safeStr(item.NumeroContrato ?? item.numero_contrato ?? "").trim();
    const razaoSocial = safeStr(item.RazaoSocial ?? item.razao_social ?? "").trim();
    const cnpj = mascaraCnpj(item.CNPJ ?? item.cnpj ?? "");
    const referencia = safeStr(item.Referencia ?? item.referencia ?? "").trim();

    const partes = [];
    if (numeroContrato) partes.push(`Contrato ${numeroContrato}`);
    else partes.push(`Contrato #${idContrato}`);
    if (razaoSocial) partes.push(razaoSocial);
    if (cnpj) partes.push(cnpj);
    if (referencia) partes.push(`Ref. ${referencia}`);

    return {
      id_contrato: idContrato,
      numero_contrato: numeroContrato,
      razao_social: razaoSocial,
      cnpj,
      referencia,
      label: partes.join(" | ")
    };
  }

  function converterValorContratoParaNumero(valor) {
    if (valor === null || valor === undefined || valor === "") return null;

    if (typeof valor === "number") {
      return Number.isFinite(valor) ? valor : null;
    }

    let texto = safeStr(valor).trim();
    if (!texto) return null;

    texto = texto.replace(/^R\$\s*/i, "").replace(/\s+/g, "");

    if (texto.includes(",") && texto.includes(".")) {
      texto = texto.replace(/\./g, "").replace(",", ".");
    } else if (texto.includes(",") && !texto.includes(".")) {
      texto = texto.replace(",", ".");
    }

    const numero = Number(texto);
    return Number.isFinite(numero) ? numero : null;
  }

  function normalizarPontoContratoKanban(item) {
    if (!item || typeof item !== "object") return null;

    const codPonto = safeStr(item.CodPonto ?? item.cod_ponto ?? "").trim();
    if (!codPonto) return null;

    const tipo = safeStr(item.TipoPainel ?? item.Tipo ?? item.tipo ?? "").trim();
    const cidade = safeStr(item.CidadeExibicao ?? item.Cidade ?? item.cidade ?? "").trim();
    const uf = safeStr(item.UF ?? item.uf ?? "").trim();

    const valorBruto =
      item.FaturamentoLiquidoMensalTotal ??
      item.faturamento_liquido_mensal_total ??
      item.valor_mensal ??
      item.ValorMensal ??
      null;

    const valorMensal = converterValorContratoParaNumero(valorBruto);

    const partes = [codPonto];
    if (tipo) partes.push(tipo);
    if (cidade || uf) partes.push(`${cidade}${cidade && uf ? "/" : ""}${uf}`);
    if (Number.isFinite(valorMensal)) partes.push(formatarMoedaBR(valorMensal));

    const dataInicioPrevisto = safeStr(item.DataInicioPrevisto ?? item.data_inicio_previsto ?? item.DataInicioContrato ?? "").trim() || null;
    const dataTerminoPrevisto = safeStr(item.DataTerminoPrevisto ?? item.data_termino_previsto ?? item.DataTerminoContrato ?? "").trim() || null;

    return {
      cod_ponto: codPonto,
      tipo,
      cidade,
      uf,
      valor_mensal: Number.isFinite(valorMensal) ? valorMensal : null,
      data_inicio_previsto: dataInicioPrevisto,
      data_termino_previsto: dataTerminoPrevisto,
      quantidade_faces: idNum(item.QuantidadeFaces ?? item.quantidade_faces ?? 0) || null,
      label: partes.join(" • ")
    };
  }

  function normalizarFaceContratoKanban(item) {
    if (!item || typeof item !== "object") return null;

    const codFace = safeStr(item.CodFace ?? item.cod_face ?? item.codigo_face ?? "").trim();
    if (!codFace) return null;

    const codPonto = safeStr(item.CodPonto ?? item.cod_ponto ?? "").trim();
    const tipoFace = safeStr(item.TipoFace ?? item.Face ?? item.TipoPainel ?? item.tipo_face ?? item.tipo ?? "").trim();
    const cota = safeStr(item.Cota ?? item.cota ?? "").trim();

    const valorMensalBruto =
      item.FaturamentoLiquidoMensal ??
      item.valor_mensal ??
      item.ValorMensal ??
      null;

    const precoVendaAtualBruto =
      item.TotalLiquidoContratoAGBRCTACORDO ??
      item.preco_venda_atual ??
      item.PrecoVendaAtualContrato ??
      valorMensalBruto ??
      null;

    const valorMensal = converterValorContratoParaNumero(valorMensalBruto);
    const precoVendaAtual = converterValorContratoParaNumero(precoVendaAtualBruto);
    const dataInicioPrevisto = safeStr(item.DataInicioPrevisto ?? item.data_inicio_previsto ?? "").trim() || null;
    const dataTerminoPrevisto = safeStr(item.DataTerminoPrevisto ?? item.data_termino_previsto ?? "").trim() || null;
    const idPainelEuromidia = idNum(item.IDPainelEuromidia ?? item.id_painel ?? 0) || null;
    const idDimFacesPaineis = idNum(item.IDDimFacesPaineis ?? item.id_dim_faces_paineis ?? 0) || null;
    const idItemContrato = idNum(item.IDFatoControleContratosItensEuromidia ?? item.id_item_contrato ?? item.id_contrato_item ?? 0) || null;
    const cidadeExibicao = safeStr(item.CidadeExibicao ?? item.cidade_exibicao ?? item.Cidade ?? "").trim() || null;
    const faturamentoBrutoMensal = converterValorContratoParaNumero(item.FaturamentoBrutoMensal ?? item.faturamento_bruto_mensal ?? null);
    const faturamentoLiquidoFinalMensal = converterValorContratoParaNumero(item.FaturamentoLiquidoFinalMensal ?? item.faturamento_liquido_final_mensal ?? null);
    const totalBrutoContrato = converterValorContratoParaNumero(item.TotalBrutoContrato ?? item.total_bruto_contrato ?? null);
    const percentualPermuta = converterValorContratoParaNumero(item.PercentualPermuta ?? item.percentual_permuta ?? null);
    const valorPermuta = converterValorContratoParaNumero(item.ValorPermuta ?? item.valor_permuta ?? null);
    const numeroParcelas = safeStr(item.NumeroParcelas ?? item.numero_parcelas ?? "").trim() || null;
    const dataInicioVencimento = safeStr(item.DataInicioVencimento ?? item.data_inicio_vencimento ?? "").trim() || null;
    const labelServidor = safeStr(item.label ?? item.Label ?? "").trim();

    const partes = [codFace];
    if (tipoFace) partes.push(tipoFace);
    if (cota) partes.push(`Cota ${cota}`);
    if (Number.isFinite(precoVendaAtual)) partes.push(formatarMoedaBR(precoVendaAtual));

    const labelMontado = partes.filter((parte) => safeStr(parte).trim()).join(" • ");

    return {
      cod_face: codFace,
      cod_ponto: codPonto,
      tipo_face: tipoFace,
      cota,
      valor_mensal: Number.isFinite(valorMensal) ? valorMensal : null,
      preco_venda_atual: Number.isFinite(precoVendaAtual) ? precoVendaAtual : null,
      data_inicio_previsto: dataInicioPrevisto,
      data_termino_previsto: dataTerminoPrevisto,
      id_painel: idPainelEuromidia,
      id_dim_faces_paineis: idDimFacesPaineis,
      id_item_contrato: idItemContrato,
      cidade_exibicao: cidadeExibicao,
      faturamento_bruto_mensal: Number.isFinite(faturamentoBrutoMensal) ? faturamentoBrutoMensal : null,
      faturamento_liquido_final_mensal: Number.isFinite(faturamentoLiquidoFinalMensal) ? faturamentoLiquidoFinalMensal : null,
      total_bruto_contrato: Number.isFinite(totalBrutoContrato) ? totalBrutoContrato : null,
      percentual_permuta: Number.isFinite(percentualPermuta) ? percentualPermuta : null,
      valor_permuta: Number.isFinite(valorPermuta) ? valorPermuta : null,
      numero_parcelas: numeroParcelas,
      data_inicio_vencimento: dataInicioVencimento,
      label: labelServidor || labelMontado || codFace
    };
  }

  async function carregarContratosDaEmpresa(idEmpresa) {
    const idEmp = idNum(idEmpresa);
    if (!idEmp) return [];
    if (contratosPorEmpresaCache.has(idEmp)) return contratosPorEmpresaCache.get(idEmp) || [];

    const r = await fetch(`/kanban/api/empresas/${idEmp}/contratos`, { credentials: "same-origin" });
    const j = await r.json().catch(() => null);

    if (!r.ok || !j || !j.ok) {
      const erro = new Error((j && (j.msg || j.erro)) || `Falha ao carregar contratos da empresa (HTTP ${r.status}).`);
      erro.httpStatus = r.status;
      throw erro;
    }

    const contratos = (Array.isArray(j.contratos) ? j.contratos : [])
      .map(normalizarContratoKanban)
      .filter(Boolean);

    contratosPorEmpresaCache.set(idEmp, contratos);
    return contratos;
  }

  async function carregarPontosDoContrato(idContrato) {
    const idCtr = idNum(idContrato);
    if (!idCtr) return [];
    if (pontosPorContratoCache.has(idCtr)) return pontosPorContratoCache.get(idCtr) || [];

    const r = await fetch(`/kanban/api/contratos/${idCtr}/pontos`, { credentials: "same-origin" });
    const j = await r.json().catch(() => null);

    if (!r.ok || !j || !j.ok) {
      const erro = new Error((j && (j.msg || j.erro)) || `Falha ao carregar os pontos do contrato (HTTP ${r.status}).`);
      erro.httpStatus = r.status;
      throw erro;
    }

    const pontos = (Array.isArray(j.pontos) ? j.pontos : [])
      .map(normalizarPontoContratoKanban)
      .filter(Boolean);

    pontosPorContratoCache.set(idCtr, pontos);
    return pontos;
  }

  async function carregarFacesDoContrato(idContrato, codPonto) {
    const idCtr = idNum(idContrato);
    const cod = safeStr(codPonto).trim();
    if (!idCtr || !cod) return [];

    const chave = `${idCtr}|${cod}`;
    if (facesPorContratoPontoCache.has(chave)) return facesPorContratoPontoCache.get(chave) || [];

    const r = await fetch(`/kanban/api/contratos/${idCtr}/pontos/${encodeURIComponent(cod)}/faces`, { credentials: "same-origin" });
    const j = await r.json().catch(() => null);

    if (!r.ok || !j || !j.ok) {
      const erro = new Error((j && (j.msg || j.erro)) || `Falha ao carregar as faces do contrato (HTTP ${r.status}).`);
      erro.httpStatus = r.status;
      throw erro;
    }

    const faces = (Array.isArray(j.faces) ? j.faces : [])
      .map(normalizarFaceContratoKanban)
      .filter(Boolean);

    facesPorContratoPontoCache.set(chave, faces);
    return faces;
  }

  function normalizarContratoComboboxItem(item) {
    if (!item || typeof item !== "object") return null;

    const idContrato = safeStr(item.id_contrato ?? item.IDFatoControleContratosEuromidia ?? item.value ?? "").trim();
    const label = safeStr(item.label ?? item.texto ?? item.text ?? "").trim();

    if (!idContrato || !label) return null;

    return {
      id_contrato: idContrato,
      label,
      numero_contrato: safeStr(item.numero_contrato ?? item.NumeroContrato ?? "").trim(),
      razao_social: safeStr(item.razao_social ?? item.RazaoSocial ?? "").trim(),
      cnpj: safeStr(item.cnpj ?? item.CNPJ ?? "").trim(),
      referencia: safeStr(item.referencia ?? item.Referencia ?? "").trim(),
      eh_novo_contrato: idContrato === VALOR_OPCAO_NOVO_CONTRATO
    };
  }

  function textoContratoCombobox(item) {
    const label = safeStr(item?.label || "").trim();
    if (label) return label;

    const idContrato = safeStr(item?.id_contrato || "").trim();
    if (idContrato === VALOR_OPCAO_NOVO_CONTRATO) return "Novo Contrato";
    return idContrato ? `Contrato #${idContrato}` : "";
  }

  function obterTextoContratoSelecionado(valor) {
    const valorComparacao = safeStr(valor || "").trim();
    if (!valorComparacao) return "";

    if (valorComparacao === VALOR_OPCAO_NOVO_CONTRATO) {
      return "Novo Contrato";
    }

    const itemCatalogo = contratosCardCatalogo.find((item) => {
      return safeStr(item?.id_contrato || "").trim() === valorComparacao;
    });

    if (itemCatalogo) {
      return textoContratoCombobox(itemCatalogo);
    }

    const opcaoSelect = Array.from(selectContratoCard?.options || []).find((opcao) => {
      return safeStr(opcao?.value || "").trim() === valorComparacao;
    });

    return safeStr(opcaoSelect?.textContent || "").trim();
  }

  function filtrarContratosCombobox(texto) {
    const termoBruto = safeStr(texto || "").trim();
    const termoNormalizado = normalizarTexto(termoBruto);
    const termoDigitos = normalizaCnpj(termoBruto);

    if (!termoNormalizado && !termoDigitos) {
      return contratosCardCatalogo.slice(0, 60);
    }

    return contratosCardCatalogo.filter((item) => {
      const textoOpcao = textoContratoCombobox(item);
      const textoBusca = [
        textoOpcao,
        item?.numero_contrato,
        item?.razao_social,
        item?.cnpj,
        item?.referencia,
        item?.id_contrato
      ].map((parte) => safeStr(parte || "")).join(" ");

      const bateTexto = termoNormalizado
        ? normalizarTexto(textoBusca).includes(termoNormalizado)
        : false;

      const bateDigitos = termoDigitos
        ? normalizaCnpj(textoBusca).includes(termoDigitos)
        : false;

      return bateTexto || bateDigitos;
    }).slice(0, 60);
  }

  function abrirListaContratosCombobox() {
    if (wrapSelectContratoCard?.hidden) return;
    if (!comboContratoCard || !listaContratoCardBusca) return;
    comboContratoCard.classList.add("is-open");
    listaContratoCardBusca.hidden = false;
    renderizarListaContratosCombobox(inputContratoCardBusca?.value || "");
  }

  function fecharListaContratosCombobox() {
    if (!comboContratoCard || !listaContratoCardBusca) return;
    comboContratoCard.classList.remove("is-open");
    listaContratoCardBusca.hidden = true;
  }

  function sincronizarBuscaContratoComSelect() {
    if (!inputContratoCardBusca || !selectContratoCard) return;
    inputContratoCardBusca.value = obterTextoContratoSelecionado(selectContratoCard.value || "");
  }

  function renderizarListaContratosCombobox(texto) {
    if (!listaContratoCardBusca) return;

    const filtradas = filtrarContratosCombobox(texto);
    contratosResultadoComboboxAtual = filtradas.slice();

    const valorSelecionado = safeStr(selectContratoCard?.value || "").trim();
    listaContratoCardBusca.innerHTML = "";

    if (!filtradas.length) {
      listaContratoCardBusca.appendChild(
        el("div", { class: "kb-combobox-vazio" }, ["Nenhum contrato encontrado."])
      );
      return;
    }

    filtradas.forEach((item) => {
      const idContrato = safeStr(item?.id_contrato || "").trim();
      if (!idContrato) return;

      const textoOpcao = textoContratoCombobox(item);
      const partes = textoOpcao.split("|").map((parte) => safeStr(parte).trim()).filter(Boolean);
      const titulo = partes[0] || textoOpcao || "Contrato";
      const detalhe = partes.slice(1).join(" | ") || (item?.eh_novo_contrato ? "Criar uma nova solicitação de contrato" : `ID ${idContrato}`);

      const botao = el("button", { type: "button", class: `kb-combobox-opcao${idContrato === valorSelecionado ? " is-selected" : ""}` }, [
        el("strong", {}, [titulo]),
        el("span", {}, [detalhe])
      ]);

      botao.addEventListener("mousedown", (evento) => {
        evento.preventDefault();
        selecionarContratoCombobox(idContrato, true);
      });

      listaContratoCardBusca.appendChild(botao);
    });
  }

  function localizarContratoPorTextoDigitado(texto) {
    const textoDigitado = safeStr(texto || "").trim();
    const textoNormalizado = normalizarTexto(textoDigitado);
    const digitos = normalizaCnpj(textoDigitado);

    if (!textoNormalizado && !digitos) return null;

    return contratosCardCatalogo.find((item) => {
      const idContrato = safeStr(item?.id_contrato || "").trim();
      const textoOpcao = textoContratoCombobox(item);

      return normalizarTexto(textoOpcao) === textoNormalizado
        || normalizarTexto(idContrato) === textoNormalizado
        || (!!digitos && normalizaCnpj(textoOpcao).includes(digitos));
    }) || null;
  }

  function selecionarContratoSilenciosamente(valor) {
    if (!selectContratoCard) return false;
    selectContratoCard.value = safeStr(valor || "").trim();
    sincronizarBuscaContratoComSelect();
    renderizarListaContratosCombobox(inputContratoCardBusca?.value || "");
    return true;
  }

  function selecionarContratoCombobox(valor, dispararChange = true) {
    if (!selectContratoCard) return false;

    const valorComparacao = safeStr(valor || "").trim();
    selecionarValorOuAcrescentarOpcao(
      selectContratoCard,
      valorComparacao,
      valorComparacao === VALOR_OPCAO_NOVO_CONTRATO ? "Novo Contrato" : `Contrato #${valorComparacao}`
    );

    sincronizarBuscaContratoComSelect();
    fecharListaContratosCombobox();

    if (dispararChange) {
      selectContratoCard.dispatchEvent(new Event("change", { bubbles: true }));
    }

    return true;
  }

  function reconciliarBuscaContratoDigitada() {
    if (!inputContratoCardBusca || !selectContratoCard) return;

    const textoDigitado = safeStr(inputContratoCardBusca.value || "").trim();

    if (!textoDigitado) {
      selecionarContratoCombobox("", true);
      return;
    }

    const contrato = localizarContratoPorTextoDigitado(textoDigitado);
    if (contrato) {
      selecionarContratoCombobox(contrato.id_contrato, true);
      return;
    }

    sincronizarBuscaContratoComSelect();
    fecharListaContratosCombobox();
  }

  function montarSelectContratoCard(contratos) {
    limparSelectComPlaceholder(selectContratoCard, "— Selecione um contrato —");
    contratosCardCatalogo = [];
    contratosResultadoComboboxAtual = [];

    (Array.isArray(contratos) ? contratos : []).forEach((contrato) => {
      const item = normalizarContratoComboboxItem(contrato);
      if (!item) return;

      contratosCardCatalogo.push(item);
      selectContratoCard.appendChild(
        el("option", { value: String(item.id_contrato) }, [item.label])
      );
    });

    const itemNovoContrato = {
      id_contrato: VALOR_OPCAO_NOVO_CONTRATO,
      label: "Novo Contrato",
      eh_novo_contrato: true
    };

    contratosCardCatalogo.push(itemNovoContrato);
    selectContratoCard.appendChild(
      el("option", { value: VALOR_OPCAO_NOVO_CONTRATO }, ["Novo Contrato"])
    );

    sincronizarBuscaContratoComSelect();
    renderizarListaContratosCombobox("");
  }

  function montarSelectModoContratoCard(modoSelecionado = VALOR_MODO_CONTRATO_NOVO, incluirAditivo = false) {
    limparSelectComPlaceholder(selectModoContratoCard, "— Selecione o tipo —");

    if (incluirAditivo) {
      selectModoContratoCard.appendChild(el("option", { value: VALOR_MODO_CONTRATO_ADITIVO }, ["Aditivo"]));
    }
    selectModoContratoCard.appendChild(el("option", { value: VALOR_MODO_CONTRATO_NOVO }, ["Novo Contrato"]));

    selectModoContratoCard.value = incluirAditivo
      ? (modoSelecionado || VALOR_MODO_CONTRATO_ADITIVO)
      : VALOR_MODO_CONTRATO_NOVO;
  }

  function montarSelectPontosContratoCard(pontos) {
    limparSelectComPlaceholder(selectCodPontoContratoCard, "— Selecione o CodPonto —");

    (Array.isArray(pontos) ? pontos : []).forEach((ponto) => {
      selectCodPontoContratoCard.appendChild(
        el("option", { value: ponto.cod_ponto }, [ponto.label])
      );
    });

    selectCodPontoContratoCard.appendChild(
      el("option", { value: VALOR_OPCAO_NOVO_PAINEL }, ["Novo Painel"])
    );
  }

  function montarSelectFacesContratoCard(faces) {
    limparSelectComPlaceholder(selectCodFaceContratoCard, "— Selecione o CodFace —");

    (Array.isArray(faces) ? faces : []).forEach((face) => {
      selectCodFaceContratoCard.appendChild(
        el("option", { value: face.cod_face }, [face.label])
      );
    });
  }

  function obterFluxoContratoAtual() {
    const valorContrato = safeStr(selectContratoCard?.value || "").trim();
    const idContrato = valorContrato && valorContrato !== VALOR_OPCAO_NOVO_CONTRATO ? idNum(valorContrato) : null;

    let modo = safeStr(selectModoContratoCard?.value || "").trim();
    if (!modo) {
      modo = idContrato ? VALOR_MODO_CONTRATO_ADITIVO : VALOR_MODO_CONTRATO_NOVO;
    }

    const codPonto = safeStr(selectCodPontoContratoCard?.value || "").trim();
    const codFace = safeStr(selectCodFaceContratoCard?.value || "").trim();

    return {
      id_contrato: idContrato,
      modo_contrato: modo,
      cod_ponto_contrato: codPonto || null,
      cod_face_contrato: codFace || null,
      usar_novo_contrato: !idContrato || modo === VALOR_MODO_CONTRATO_NOVO,
      usar_novo_painel: codPonto === VALOR_OPCAO_NOVO_PAINEL
    };
  }

  function resolverFluxoContratoParaSalvamento(){
    const fluxoAtual = obterFluxoContratoAtual();
    const fluxoPersistido = fluxoContratoPersistidoCardAberto && typeof fluxoContratoPersistidoCardAberto === "object"
      ? fluxoContratoPersistidoCardAberto
      : {};

    const idContratoAtual = idNum(fluxoAtual?.id_contrato || 0) || null;
    const idContratoPersistido = idNum(fluxoPersistido?.id_contrato_existente || 0) || null;
    const idContratoFinal = idContratoAtual || idContratoPersistido || null;
    const ehRenovacaoPersistida = fluxoPersistido?.eh_renovacao === true;

    let modoFinal = normalizarModoContratoPersistido(
      fluxoAtual?.modo_contrato || fluxoPersistido?.tipo_contrato_card || "",
      idContratoFinal ? VALOR_MODO_CONTRATO_ADITIVO : VALOR_MODO_CONTRATO_NOVO
    );

    /*
     * Proteção contra perda da tag Aditivo:
     * se o card já estava persistido como Aditivo e o select visual voltou para
     * "Novo Contrato" por falha de reaplicação do combobox, eu preservo o Aditivo
     * no payload. Isso evita remover a tag IDDimKanbanTag = 8 no segundo salvamento.
     */
    if (idContratoPersistido && (!idContratoAtual || fluxoPersistido?.tipo_contrato_card === VALOR_MODO_CONTRATO_ADITIVO)) {
      modoFinal = VALOR_MODO_CONTRATO_ADITIVO;
    }

    if (ehRenovacaoPersistida) {
      modoFinal = VALOR_MODO_CONTRATO_ADITIVO;
    }

    if (!idContratoFinal && !ehRenovacaoPersistida) {
      modoFinal = VALOR_MODO_CONTRATO_NOVO;
    }

    const codPontoFinal = safeStr(
      fluxoAtual?.cod_ponto_contrato || fluxoPersistido?.cod_ponto_contrato || ""
    ).trim() || null;

    const codFaceFinal = safeStr(
      fluxoAtual?.cod_face_contrato || fluxoPersistido?.cod_face_contrato || ""
    ).trim().toUpperCase() || null;

    return {
      id_contrato: idContratoFinal,
      modo_contrato: modoFinal,
      cod_ponto_contrato: codPontoFinal,
      cod_face_contrato: codFaceFinal,
      eh_renovacao: ehRenovacaoPersistida,
      usar_novo_contrato: (!idContratoFinal && !ehRenovacaoPersistida) || modoFinal === VALOR_MODO_CONTRATO_NOVO,
      usar_novo_painel: codPontoFinal === VALOR_OPCAO_NOVO_PAINEL
    };
  }

  function normalizarModoContratoPersistido(valor, modoFallback = VALOR_MODO_CONTRATO_NOVO) {
    const texto = safeStr(valor).trim().toUpperCase();

    if (texto === VALOR_MODO_CONTRATO_ADITIVO || texto === "ADITIVO") {
      return VALOR_MODO_CONTRATO_ADITIVO;
    }

    if (
      texto === VALOR_MODO_CONTRATO_NOVO ||
      texto === "NOVO CONTRATO" ||
      texto === "NOVO_CONTRATO"
    ) {
      return VALOR_MODO_CONTRATO_NOVO;
    }

    return modoFallback;
  }

  function possuiOpcaoNoSelect(selectEl, valor) {
    if (!selectEl) return false;
    const valorComparacao = safeStr(valor).trim();
    if (!valorComparacao) return false;

    return Array.from(selectEl.options || []).some((opcao) => safeStr(opcao?.value || "").trim() === valorComparacao);
  }

  function selecionarValorOuAcrescentarOpcao(selectEl, valor, textoFallback) {
    if (!selectEl) return false;

    const valorComparacao = safeStr(valor).trim();
    if (!valorComparacao) {
      selectEl.value = "";
      return false;
    }

    if (!possuiOpcaoNoSelect(selectEl, valorComparacao)) {
      selectEl.appendChild(el("option", { value: valorComparacao }, [safeStr(textoFallback).trim() || valorComparacao]));
    }

    selectEl.value = valorComparacao;

    if (selectEl === selectContratoCard) {
      sincronizarBuscaContratoComSelect();
      renderizarListaContratosCombobox(inputContratoCardBusca?.value || "");
    }

    return safeStr(selectEl.value || "").trim() === valorComparacao;
  }

  function extrairFluxoContratoPersistidoDoCard(card, contexto = {}) {
    const cardNormalizado = normalizarCardServidor(card || {});
    const snapshot = contexto && typeof contexto === "object" ? (contexto.snapshot || {}) : {};
    const headerSnapshot = snapshot && typeof snapshot === "object" ? (snapshot.header || {}) : {};
    const itemSnapshot = snapshot && typeof snapshot === "object" ? (snapshot.item || {}) : {};
    const itensSnapshot = Array.isArray(snapshot?.itens) ? snapshot.itens : [];
    const primeiroItemSnapshot = itensSnapshot.find((item) => item && typeof item === "object") || {};
    const painelFaces = Array.isArray(contexto?.painelFaces) ? contexto.painelFaces : [];
    const primeiroPainelFace = painelFaces.find((item) => item && typeof item === "object") || {};
    const tagsContexto = Array.isArray(contexto?.tags) ? contexto.tags : [];

    const obterPrimeiroNumero = (...valores) => {
      for (const valor of valores) {
        const numero = idNum(valor || 0);
        if (numero) return numero;
      }
      return null;
    };

    const obterPrimeiroTexto = (...valores) => {
      for (const valor of valores) {
        const texto = safeStr(valor ?? "").trim();
        if (texto) return texto;
      }
      return "";
    };

    const temTagAditivo = tagsContexto.some((tag) => {
      const idTag = idNum(tag?.IDDimKanbanTag ?? tag?.id_dim_kanban_tag ?? tag?.id ?? 0);
      const nomeTag = safeStr(tag?.NomeTag ?? tag?.nomeTag ?? tag?.nome ?? "").trim().toUpperCase();
      return idTag === ID_TAG_TIPO_CONTRATO_ADITIVO || nomeTag === "ADITIVO";
    });

    const temTagNovoContrato = tagsContexto.some((tag) => {
      const idTag = idNum(tag?.IDDimKanbanTag ?? tag?.id_dim_kanban_tag ?? tag?.id ?? 0);
      const nomeTag = safeStr(tag?.NomeTag ?? tag?.nomeTag ?? tag?.nome ?? "").trim().toUpperCase();
      return idTag === ID_TAG_TIPO_CONTRATO_NOVO || nomeTag === "NOVO CONTRATO";
    });

    const temTagRenovacao = tagsContexto.some((tag) => {
      const idTag = idNum(tag?.IDDimKanbanTag ?? tag?.id_dim_kanban_tag ?? tag?.id ?? 0);
      const nomeTag = safeStr(tag?.NomeTag ?? tag?.nomeTag ?? tag?.nome ?? "").trim().toUpperCase();
      return idTag === ID_TAG_RENOVACAO || nomeTag.includes("RENOVA");
    });

    const idContrato = obterPrimeiroNumero(
      cardNormalizado.IDFatoControleContratosEuromidia,
      cardNormalizado.IDFatoControleContratoEuromidia,
      cardNormalizado.id_contrato_existente,
      cardNormalizado.id_controle_contrato,
      headerSnapshot.IDFatoControleContratosEuromidia,
      headerSnapshot.IDFatoControleContratoEuromidia,
      headerSnapshot.id_contrato_existente,
      itemSnapshot.IDFatoControleContratosEuromidia,
      itemSnapshot.IDFatoControleContratoEuromidia,
      primeiroItemSnapshot.IDFatoControleContratosEuromidia,
      primeiroItemSnapshot.IDFatoControleContratoEuromidia
    );

    const codPontoContrato = obterPrimeiroTexto(
      cardNormalizado.CodPontoContrato,
      cardNormalizado.cod_ponto_contrato,
      headerSnapshot.CodPontoContrato,
      headerSnapshot.cod_ponto_contrato,
      itemSnapshot.CodPonto,
      itemSnapshot.cod_ponto,
      primeiroItemSnapshot.CodPonto,
      primeiroItemSnapshot.cod_ponto,
      primeiroPainelFace.CodPontoContratoAditivo,
      primeiroPainelFace.cod_ponto_contrato_aditivo,
      primeiroPainelFace.CodPontoContrato,
      primeiroPainelFace.cod_ponto_contrato,
      primeiroPainelFace.CodPonto,
      primeiroPainelFace.cod_ponto
    ) || null;

    const codFaceContrato = obterPrimeiroTexto(
      cardNormalizado.CodFaceContrato,
      cardNormalizado.cod_face_contrato,
      headerSnapshot.CodFaceContrato,
      headerSnapshot.cod_face_contrato,
      itemSnapshot.CodFace,
      itemSnapshot.cod_face,
      primeiroItemSnapshot.CodFace,
      primeiroItemSnapshot.cod_face,
      primeiroPainelFace.CodFaceContratoAditivo,
      primeiroPainelFace.cod_face_contrato_aditivo,
      primeiroPainelFace.CodFaceContrato,
      primeiroPainelFace.cod_face_contrato,
      primeiroPainelFace.CodFace,
      primeiroPainelFace.cod_face
    ).toUpperCase() || null;

    const tipoContratoBruto = obterPrimeiroTexto(
      cardNormalizado.tipo_contrato,
      cardNormalizado.tipo_contrato_card,
      cardNormalizado.TipoSolicitacao,
      headerSnapshot.TipoSolicitacao,
      headerSnapshot.tipo_contrato,
      headerSnapshot.tipo_contrato_card,
      itemSnapshot.TipoSolicitacao,
      primeiroItemSnapshot.TipoSolicitacao
    );

    let modoPersistido = normalizarModoContratoPersistido(
      tipoContratoBruto,
      idContrato ? VALOR_MODO_CONTRATO_ADITIVO : VALOR_MODO_CONTRATO_NOVO
    );

    if (temTagRenovacao) {
      modoPersistido = VALOR_MODO_CONTRATO_ADITIVO;
    } else if (temTagAditivo) {
      modoPersistido = VALOR_MODO_CONTRATO_ADITIVO;
    } else if (temTagNovoContrato && !idContrato) {
      modoPersistido = VALOR_MODO_CONTRATO_NOVO;
    } else if (idContrato && !temTagNovoContrato) {
      modoPersistido = VALOR_MODO_CONTRATO_ADITIVO;
    }

    if (!idContrato && modoPersistido === VALOR_MODO_CONTRATO_ADITIVO && !temTagAditivo && !temTagRenovacao) {
      modoPersistido = VALOR_MODO_CONTRATO_NOVO;
    }

    return {
      id_contrato_existente: idContrato,
      tipo_contrato_card: modoPersistido,
      cod_ponto_contrato: codPontoContrato,
      cod_face_contrato: codFaceContrato,
      eh_renovacao: temTagRenovacao
    };
  }

  function obterIdTagTipoContratoDesejada() {
    const fluxo = resolverFluxoContratoParaSalvamento();

    if (fluxo?.eh_renovacao === true) {
      return ID_TAG_TIPO_CONTRATO_ADITIVO;
    }

    return fluxo.id_contrato && fluxo.modo_contrato === VALOR_MODO_CONTRATO_ADITIVO
      ? ID_TAG_TIPO_CONTRATO_ADITIVO
      : ID_TAG_TIPO_CONTRATO_NOVO;
  }

  function normalizarTextoComparacaoPainelContrato(valor) {
    return safeStr(valor)
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .trim()
      .toUpperCase();
  }

  function obterPontoContratoSelecionado(idContrato, codPonto) {
    const idCtr = idNum(idContrato);
    const codPontoNormalizado = safeStr(codPonto).trim();
    if (!idCtr || !codPontoNormalizado) return null;

    const pontos = pontosPorContratoCache.get(idCtr) || [];
    return (Array.isArray(pontos) ? pontos : []).find((ponto) => {
      return safeStr(ponto?.cod_ponto ?? ponto?.CodPonto ?? "").trim() === codPontoNormalizado;
    }) || null;
  }

  function obterCodPontoManualDoBloco(bloco){
    const painelSelecionado = obterPainelFaceSelecionadoDoBloco(bloco) || null;
    const selectPainel = bloco?.querySelector('[data-role="select-painel"]');
    const idPainel = idNum(selectPainel?.value || painelSelecionado?.IDDimPaineisEuromidia || 0);
    const painel = (idPainel ? paineisPorId.get(idPainel) : null) || null;
    return safeStr(painelSelecionado?.CodPonto || painel?.CodPonto || "").trim();
  }

  function obterCodFaceManualDoBloco(bloco){
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const painelSelecionado = obterPainelFaceSelecionadoDoBloco(bloco) || null;
    return safeStr(selectFace?.value || painelSelecionado?.CodFace || "").trim().toUpperCase();
  }

  function encontrarFaceContratoEmLista(faces, codFace){
    const codFaceNormalizado = safeStr(codFace || "").trim().toUpperCase();
    if (!codFaceNormalizado) return null;

    return (Array.isArray(faces) ? faces : []).find((face) => {
      return safeStr(face?.cod_face || face?.CodFace || "").trim().toUpperCase() === codFaceNormalizado;
    }) || null;
  }

  function confirmarCarregamentoItemContratoExistente({ codPonto, codFace, faceSelecionada } = {}){
    const codPontoTxt = safeStr(codPonto || faceSelecionada?.cod_ponto || faceSelecionada?.CodPonto || "").trim();
    const codFaceTxt = safeStr(codFace || faceSelecionada?.cod_face || faceSelecionada?.CodFace || "").trim().toUpperCase();

    return window.confirm(
      `Você está selecionando um CodFace já existente nesse contrato.\n\n` +
      `CodPonto: ${codPontoTxt || "—"}\n` +
      `CodFace: ${codFaceTxt || "—"}\n\n` +
      `Deseja prosseguir e alterar as inserções e o período?`
    );
  }

  function itemContratoSelecionadoEhMesmoPainelFace(bloco, codPonto, codFace){
    const item = bloco?.__itemContratoAditivoSelecionado || null;
    if (!item) return false;

    const codPontoItem = safeStr(item?.cod_ponto ?? item?.CodPonto ?? "").trim();
    const codFaceItem = safeStr(item?.cod_face ?? item?.CodFace ?? "").trim().toUpperCase();

    return codPontoItem === safeStr(codPonto || "").trim()
      && codFaceItem === safeStr(codFace || "").trim().toUpperCase();
  }

  async function validarPainelFaceManualAditivoNoBloco(bloco){
    if (!bloco || !modalCardEstaNaFaseQuatro()) return { ok: true, existe_no_contrato: false };

    const fluxo = obterFluxoContratoAtual();
    if (!fluxo.id_contrato || fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO) {
      return { ok: true, existe_no_contrato: false };
    }

    const codPontoManual = obterCodPontoManualDoBloco(bloco);
    const codFaceManual = obterCodFaceManualDoBloco(bloco);

    if (!codPontoManual || !codFaceManual) {
      return { ok: true, existe_no_contrato: false };
    }

    if (itemContratoSelecionadoEhMesmoPainelFace(bloco, codPontoManual, codFaceManual)) {
      return { ok: true, existe_no_contrato: true, ja_carregado: true };
    }

    try {
      const faces = await carregarFacesDoContrato(fluxo.id_contrato, codPontoManual);
      const faceSelecionada = encontrarFaceContratoEmLista(faces, codFaceManual);

      if (!faceSelecionada) {
        bloco.__itemContratoAditivoSelecionado = null;
        bloco.__codPontoContratoItemDesejado = VALOR_OPCAO_NOVO_PAINEL;
        bloco.__codFaceContratoItemDesejada = codFaceManual;
        renderizarInfoItemContratoAditivo(
          bloco,
          null,
          `CodPonto ${codPontoManual} / CodFace ${codFaceManual} não existe neste contrato. O sistema vai tratar como inclusão de novo item no aditivo.`
        );
        setMensagemFluxoContrato(
          `CodPonto ${codPontoManual} / CodFace ${codFaceManual} não existe no contrato selecionado. Pode prosseguir como novo item do aditivo.`,
          "info"
        );
        return { ok: true, existe_no_contrato: false };
      }

      const confirmado = confirmarCarregamentoItemContratoExistente({
        codPonto: codPontoManual,
        codFace: codFaceManual,
        faceSelecionada
      });

      if (!confirmado) {
        limparSelecaoPainelFaceDoBloco(bloco, false);
        bloco.__itemContratoAditivoSelecionado = null;
        renderizarInfoItemContratoAditivo(
          bloco,
          null,
          "Seleção cancelada. O CodFace existe no contrato, mas os dados não foram carregados para edição."
        );
        setMensagemFluxoContrato("Seleção cancelada. Nenhum item existente do contrato foi carregado.", "alerta");
        return { ok: false, cancelado: true };
      }

      const aplicado = await aplicarPainelFaceContratoNoBloco(bloco, codPontoManual, codFaceManual, faceSelecionada);
      if (aplicado) {
        selecionarValorOuAcrescentarOpcao(selectCodPontoContratoCard, codPontoManual, codPontoManual);
        if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = false;
        montarSelectFacesContratoCard(faces);
        selecionarValorOuAcrescentarOpcao(selectCodFaceContratoCard, codFaceManual, faceSelecionada.label || codFaceManual);
        setMensagemFluxoContrato(`Item existente do contrato carregado para edição: ${montarTextoResumoItemContratoAditivo(faceSelecionada)}.`, "sucesso");
      }

      return { ok: true, existe_no_contrato: true, face: faceSelecionada };
    } catch (erro) {
      console.warn("validarPainelFaceManualAditivoNoBloco: falhou ao validar CodPonto/CodFace no contrato", erro);
      setMensagemFluxoContrato("Não foi possível validar se este CodPonto/CodFace já existe no contrato. Confira antes de salvar.", "alerta");
      return { ok: false, erro };
    }
  }

  function encontrarPainelPorCodPonto(codPonto, tipoPainel = "") {
    const cod = safeStr(codPonto).trim();
    if (!cod) return null;

    const candidatos = (Array.isArray(paineisCatalogo) ? paineisCatalogo : []).filter((painel) => {
      return safeStr(painel?.CodPonto ?? "").trim() === cod;
    });

    if (!candidatos.length) {
      return null;
    }

    if (candidatos.length === 1) {
      return candidatos[0];
    }

    const tipoContratoNormalizado = normalizarTextoComparacaoPainelContrato(tipoPainel);
    if (tipoContratoNormalizado) {
      const painelMesmoTipo = candidatos.find((painel) => {
        const tipoCatalogo = normalizarTextoComparacaoPainelContrato(painel?.Tipo ?? painel?.TipoPainel ?? "");
        return tipoCatalogo === tipoContratoNormalizado;
      });

      if (painelMesmoTipo) {
        return painelMesmoTipo;
      }
    }

    return candidatos[0];
  }

  function encontrarPainelFacePorContrato(codPonto, codFace, tipoPainel = "") {
    const codPontoNormalizado = safeStr(codPonto).trim();
    const codFaceNormalizado = safeStr(codFace).trim().toUpperCase();
    if (!codPontoNormalizado || !codFaceNormalizado) return null;

    const candidatos = (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : []).filter((item) => {
      return safeStr(item?.CodPonto ?? '').trim() === codPontoNormalizado
        && safeStr(item?.CodFace ?? '').trim().toUpperCase() === codFaceNormalizado;
    });

    if (!candidatos.length) return null;
    if (candidatos.length === 1) return candidatos[0];

    const tipoContratoNormalizado = normalizarTextoComparacaoPainelContrato(tipoPainel);
    if (tipoContratoNormalizado) {
      const itemMesmoTipo = candidatos.find((item) => {
        const tipoCatalogo = normalizarTextoComparacaoPainelContrato(item?.Tipo ?? item?.TipoPainel ?? '');
        return tipoCatalogo === tipoContratoNormalizado;
      });
      if (itemMesmoTipo) return itemMesmoTipo;
    }

    return candidatos[0];
  }

  function formatarDataContratoAditivo(valor) {
    const texto = safeStr(valor || "").trim();
    if (!texto) return "—";
    const matchIso = texto.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (matchIso) {
      return `${matchIso[3]}/${matchIso[2]}/${matchIso[1]}`;
    }
    return texto;
  }

  function formatarNumeroContratoAditivo(valor, casas = 2) {
    const numero = converterValorContratoParaNumero(valor);
    if (!Number.isFinite(numero)) return "—";
    return numero.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  }

  function montarTextoResumoItemContratoAditivo(faceSelecionada) {
    if (!faceSelecionada) return "Selecione um CodPonto e uma Face do contrato para carregar as informações do item original.";

    const partes = [];
    const codPonto = safeStr(faceSelecionada.cod_ponto || faceSelecionada.CodPonto || "").trim();
    const codFace = safeStr(faceSelecionada.cod_face || faceSelecionada.CodFace || "").trim().toUpperCase();
    const tipo = safeStr(faceSelecionada.tipo_face || faceSelecionada.TipoPainel || faceSelecionada.tipo || "").trim();
    const cota = safeStr(faceSelecionada.cota || faceSelecionada.Cota || "").trim();
    const dataInicio = faceSelecionada.data_inicio_previsto || faceSelecionada.DataInicioPrevisto || null;
    const dataFim = faceSelecionada.data_termino_previsto || faceSelecionada.DataTerminoPrevisto || null;
    const precoAtual = converterValorContratoParaNumero(
      faceSelecionada.preco_venda_atual ??
      faceSelecionada.TotalLiquidoContratoAGBRCTACORDO ??
      faceSelecionada.faturamento_liquido_final_mensal ??
      faceSelecionada.valor_mensal ??
      null
    );

    if (codPonto) partes.push(`CodPonto ${codPonto}`);
    if (codFace) partes.push(`Face ${codFace}`);
    if (tipo) partes.push(tipo);
    if (cota) partes.push(`Cota ${cota}`);
    if (dataInicio || dataFim) partes.push(`${formatarDataContratoAditivo(dataInicio)} até ${formatarDataContratoAditivo(dataFim)}`);
    if (Number.isFinite(precoAtual)) partes.push(`Preço atual ${formatarMoedaBR(precoAtual)}`);

    return partes.length ? partes.join(" • ") : "Item do contrato selecionado.";
  }

  function renderizarInfoItemContratoAditivo(bloco, faceSelecionada = null, mensagem = "") {
    const info = bloco?.querySelector('[data-role="contrato-item-info"]');
    if (!info) return;

    info.innerHTML = "";

    if (!faceSelecionada) {
      info.appendChild(el("div", { class: "kb-contrato-item-vazio" }, [
        mensagem || "Selecione um CodPonto e uma Face do contrato para carregar o item original. Se for incluir um novo ponto/face, use o seletor manual de painéis abaixo."
      ]));
      return;
    }

    const precoAtual = converterValorContratoParaNumero(
      faceSelecionada.preco_venda_atual ??
      faceSelecionada.TotalLiquidoContratoAGBRCTACORDO ??
      faceSelecionada.faturamento_liquido_final_mensal ??
      faceSelecionada.valor_mensal ??
      null
    );
    const brutoMensal = converterValorContratoParaNumero(faceSelecionada.faturamento_bruto_mensal ?? faceSelecionada.FaturamentoBrutoMensal ?? null);
    const liquidoMensal = converterValorContratoParaNumero(faceSelecionada.valor_mensal ?? faceSelecionada.FaturamentoLiquidoMensal ?? null);
    const totalBruto = converterValorContratoParaNumero(faceSelecionada.total_bruto_contrato ?? faceSelecionada.TotalBrutoContrato ?? null);
    const valorPermuta = converterValorContratoParaNumero(faceSelecionada.valor_permuta ?? faceSelecionada.ValorPermuta ?? null);
    const percentualPermuta = converterValorContratoParaNumero(faceSelecionada.percentual_permuta ?? faceSelecionada.PercentualPermuta ?? null);

    const linhas = [
      ["CodPonto", safeStr(faceSelecionada.cod_ponto || faceSelecionada.CodPonto || "—")],
      ["Face", safeStr(faceSelecionada.cod_face || faceSelecionada.CodFace || "—").toUpperCase()],
      ["Tipo", safeStr(faceSelecionada.tipo_face || faceSelecionada.TipoPainel || faceSelecionada.tipo || "—")],
      ["Cidade", safeStr(faceSelecionada.cidade_exibicao || faceSelecionada.CidadeExibicao || "—")],
      ["Cota", safeStr(faceSelecionada.cota || faceSelecionada.Cota || "—")],
      ["Início", formatarDataContratoAditivo(faceSelecionada.data_inicio_previsto || faceSelecionada.DataInicioPrevisto)],
      ["Término", formatarDataContratoAditivo(faceSelecionada.data_termino_previsto || faceSelecionada.DataTerminoPrevisto)],
      ["Preço atual", Number.isFinite(precoAtual) ? formatarMoedaBR(precoAtual) : "—"],
      ["Bruto mensal", Number.isFinite(brutoMensal) ? formatarMoedaBR(brutoMensal) : "—"],
      ["Líquido mensal", Number.isFinite(liquidoMensal) ? formatarMoedaBR(liquidoMensal) : "—"],
      ["Total bruto", Number.isFinite(totalBruto) ? formatarMoedaBR(totalBruto) : "—"],
      ["Permuta", Number.isFinite(percentualPermuta) ? `${formatarNumeroContratoAditivo(percentualPermuta)}%` : (Number.isFinite(valorPermuta) ? formatarMoedaBR(valorPermuta) : "—")]
    ];

    info.appendChild(el("div", { class: "kb-contrato-item-resumo" }, [montarTextoResumoItemContratoAditivo(faceSelecionada)]));

    const grid = el("div", { class: "kb-contrato-item-info-grid" });
    linhas.forEach(([rotulo, valor]) => {
      grid.appendChild(el("div", { class: "kb-contrato-item-info-linha" }, [
        el("span", { class: "k" }, [rotulo]),
        el("span", { class: "v" }, [safeStr(valor || "—")])
      ]));
    });
    info.appendChild(grid);
  }

  function limparSelecaoContratoAditivoDoBloco(bloco, opcoes = {}) {
    if (!bloco) return;
    bloco.__itemContratoAditivoSelecionado = null;

    const selectCodFaceItem = bloco.querySelector('[data-role="select-codface-contrato-item"]');
    if (selectCodFaceItem) limparSelectComPlaceholder(selectCodFaceItem, "— Face do contrato —");
    renderizarInfoItemContratoAditivo(bloco, null, opcoes.mensagem || "Selecione um item existente do contrato ou escolha um novo painel/face no seletor manual abaixo.");
  }

  function preencherPontosContratoNoBloco(bloco, opcoes = {}) {
    const selectCodPontoItem = bloco?.querySelector('[data-role="select-codponto-contrato-item"]');
    if (!selectCodPontoItem) return;

    const fluxo = obterFluxoContratoAtual();
    limparSelectComPlaceholder(selectCodPontoItem, "— CodPonto do contrato —");

    const pontos = fluxo.id_contrato ? (pontosPorContratoCache.get(idNum(fluxo.id_contrato)) || []) : [];
    (Array.isArray(pontos) ? pontos : []).forEach((ponto) => {
      selectCodPontoItem.appendChild(el("option", { value: safeStr(ponto.cod_ponto || "").trim() }, [ponto.label || ponto.cod_ponto]));
    });

    selectCodPontoItem.appendChild(el("option", { value: VALOR_OPCAO_NOVO_PAINEL }, ["Novo Painel / Face"]));

    const valorDesejado = safeStr(opcoes.cod_ponto || bloco.__codPontoContratoItemDesejado || "").trim();
    if (valorDesejado && possuiOpcaoNoSelect(selectCodPontoItem, valorDesejado)) {
      selectCodPontoItem.value = valorDesejado;
    }
  }

  async function preencherFacesContratoNoBloco(bloco, codPonto, opcoes = {}) {
    const selectCodFaceItem = bloco?.querySelector('[data-role="select-codface-contrato-item"]');
    if (!selectCodFaceItem) return [];

    limparSelectComPlaceholder(selectCodFaceItem, "— Face do contrato —");
    const fluxo = obterFluxoContratoAtual();
    const codPontoNormalizado = safeStr(codPonto || "").trim();

    if (!fluxo.id_contrato || !codPontoNormalizado || codPontoNormalizado === VALOR_OPCAO_NOVO_PAINEL) {
      return [];
    }

    const faces = await carregarFacesDoContrato(fluxo.id_contrato, codPontoNormalizado);
    (Array.isArray(faces) ? faces : []).forEach((face) => {
      selectCodFaceItem.appendChild(el("option", { value: safeStr(face.cod_face || "").trim().toUpperCase() }, [face.label || face.cod_face]));
    });

    const valorDesejado = safeStr(opcoes.cod_face || bloco.__codFaceContratoItemDesejada || "").trim().toUpperCase();
    if (valorDesejado && possuiOpcaoNoSelect(selectCodFaceItem, valorDesejado)) {
      selectCodFaceItem.value = valorDesejado;
    }

    return faces;
  }

  async function reaplicarSelecaoContratoAditivoSalvaNoBloco(bloco) {
    if (!bloco || bloco.__reaplicandoContratoAditivo === true) return;

    const fluxo = obterFluxoContratoAtual();
    if (!fluxo.id_contrato || fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO) return;

    const codPontoDesejado = safeStr(bloco.__codPontoContratoItemDesejado || "").trim();
    const codFaceDesejada = safeStr(bloco.__codFaceContratoItemDesejada || "").trim().toUpperCase();

    if (!codPontoDesejado) return;

    bloco.__reaplicandoContratoAditivo = true;
    try {
      preencherPontosContratoNoBloco(bloco, { cod_ponto: codPontoDesejado });

      const selectCodPontoItem = bloco.querySelector('[data-role="select-codponto-contrato-item"]');
      const selectCodFaceItem = bloco.querySelector('[data-role="select-codface-contrato-item"]');
      selecionarValorOuAcrescentarOpcao(
        selectCodPontoItem,
        codPontoDesejado,
        codPontoDesejado === VALOR_OPCAO_NOVO_PAINEL ? "Novo Painel / Face" : codPontoDesejado
      );

      if (codPontoDesejado === VALOR_OPCAO_NOVO_PAINEL) {
        if (selectCodFaceItem) limparSelectComPlaceholder(selectCodFaceItem, "— Face do contrato —");
        renderizarInfoItemContratoAditivo(bloco, null, "Novo Painel / Face selecionado neste aditivo. Preencha o painel e a face no seletor manual abaixo.");
        return;
      }

      const faces = await preencherFacesContratoNoBloco(bloco, codPontoDesejado, { cod_face: codFaceDesejada });
      if (!codFaceDesejada) return;

      const faceSelecionada = (Array.isArray(faces) ? faces : []).find((face) => {
        return safeStr(face?.cod_face || face?.CodFace || "").trim().toUpperCase() === codFaceDesejada;
      }) || null;

      if (faceSelecionada) {
        marcarSelecaoContratoAditivoNoBloco(bloco, codPontoDesejado, codFaceDesejada, faceSelecionada);
      } else {
        selecionarValorOuAcrescentarOpcao(selectCodFaceItem, codFaceDesejada, codFaceDesejada);
        renderizarInfoItemContratoAditivo(bloco, null, "A face salva foi reaplicada, mas os detalhes do contrato não vieram no cache. Selecione novamente para recarregar os detalhes.");
      }
    } catch (erro) {
      console.warn("reaplicarSelecaoContratoAditivoSalvaNoBloco: falhou", erro);
    } finally {
      bloco.__reaplicandoContratoAditivo = false;
    }
  }

  function atualizarVisibilidadeContratoAditivoDoBloco(bloco) {
    const wrap = bloco?.querySelector('[data-role="wrap-item-contrato-aditivo"]');
    if (!wrap) return;

    const fluxo = obterFluxoContratoAtual();
    const deveMostrar = modalCardEstaNaFaseQuatro() && !!(fluxo.id_contrato && fluxo.modo_contrato === VALOR_MODO_CONTRATO_ADITIVO);
    wrap.hidden = !deveMostrar;

    if (!deveMostrar) {
      bloco.__itemContratoAditivoSelecionado = null;
      return;
    }

    preencherPontosContratoNoBloco(bloco);
    renderizarInfoItemContratoAditivo(bloco, bloco.__itemContratoAditivoSelecionado || null);
    reaplicarSelecaoContratoAditivoSalvaNoBloco(bloco);
  }

  function sincronizarSeletoresContratoAditivoEmTodosBlocos() {
    if (!painelFaceLista) return;
    painelFaceLista.querySelectorAll('.kb-painel-item').forEach((bloco) => {
      atualizarVisibilidadeContratoAditivoDoBloco(bloco);
    });
  }

  async function sincronizarCodPontoContratoCardNoPrimeiroBloco(codPonto) {
    const fluxo = obterFluxoContratoAtual();
    const codPontoNormalizado = safeStr(codPonto || "").trim();

    if (!painelFaceLista || !fluxo.id_contrato || fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO || !codPontoNormalizado) {
      return;
    }

    let bloco = painelFaceLista.querySelector('.kb-painel-item');
    if (!bloco) {
      bloco = criarPainelFaceItem();
      painelFaceLista.appendChild(bloco);
      atualizarTitulosPainelFace();
    }

    const wrapAditivo = bloco.querySelector('[data-role="wrap-item-contrato-aditivo"]');
    if (wrapAditivo) wrapAditivo.hidden = false;

    bloco.__codPontoContratoItemDesejado = codPontoNormalizado;
    bloco.__codFaceContratoItemDesejada = "";
    bloco.__itemContratoAditivoSelecionado = null;

    preencherPontosContratoNoBloco(bloco, { cod_ponto: codPontoNormalizado });

    const selectCodPontoItem = bloco.querySelector('[data-role="select-codponto-contrato-item"]');
    const selectCodFaceItem = bloco.querySelector('[data-role="select-codface-contrato-item"]');
    const pontoSelecionado = obterPontoContratoSelecionado(fluxo.id_contrato, codPontoNormalizado);
    const labelCodPonto = codPontoNormalizado === VALOR_OPCAO_NOVO_PAINEL
      ? "Novo Painel / Face"
      : (pontoSelecionado?.label || pontoSelecionado?.cod_ponto || codPontoNormalizado);

    selecionarValorOuAcrescentarOpcao(selectCodPontoItem, codPontoNormalizado, labelCodPonto);
    limparSelectComPlaceholder(selectCodFaceItem, "— Face do contrato —");

    if (codPontoNormalizado === VALOR_OPCAO_NOVO_PAINEL) {
      renderizarInfoItemContratoAditivo(bloco, null, "Novo Painel / Face selecionado automaticamente no primeiro bloco. Agora escolha o painel e a face no seletor manual abaixo.");
      return;
    }

    try {
      const faces = await preencherFacesContratoNoBloco(bloco, codPontoNormalizado);
      renderizarInfoItemContratoAditivo(
        bloco,
        null,
        faces.length
          ? "CodPonto do contrato selecionado automaticamente neste bloco. Agora escolha a face existente deste CodPonto."
          : "CodPonto do contrato selecionado automaticamente neste bloco, mas nenhuma face ativa foi encontrada para ele."
      );
    } catch (erro) {
      console.warn("sincronizarCodPontoContratoCardNoPrimeiroBloco: falhou ao carregar faces", erro);
      renderizarInfoItemContratoAditivo(bloco, null, "CodPonto selecionado automaticamente, mas não foi possível carregar as faces deste ponto.");
    }
  }

  function marcarSelecaoContratoAditivoNoBloco(bloco, codPonto, codFace, faceSelecionada = null) {
    if (!bloco) return;
    const selectCodPontoItem = bloco.querySelector('[data-role="select-codponto-contrato-item"]');
    const selectCodFaceItem = bloco.querySelector('[data-role="select-codface-contrato-item"]');

    const codPontoNormalizado = safeStr(codPonto || "").trim();
    const codFaceNormalizado = safeStr(codFace || "").trim().toUpperCase();

    if (selectCodPontoItem && codPontoNormalizado) {
      selecionarValorOuAcrescentarOpcao(selectCodPontoItem, codPontoNormalizado, codPontoNormalizado === VALOR_OPCAO_NOVO_PAINEL ? "Novo Painel / Face" : codPontoNormalizado);
    }

    if (selectCodFaceItem && codFaceNormalizado) {
      selecionarValorOuAcrescentarOpcao(selectCodFaceItem, codFaceNormalizado, faceSelecionada?.label || codFaceNormalizado);
    }

    bloco.__itemContratoAditivoSelecionado = faceSelecionada || null;
    renderizarInfoItemContratoAditivo(bloco, faceSelecionada || null);
  }

  async function aplicarPainelFaceContratoNoBloco(bloco, codPonto, codFace, dadosContrato = null) {
    const fluxo = obterFluxoContratoAtual();
    const codPontoNormalizado = safeStr(codPonto).trim();
    const codFaceNormalizado = safeStr(codFace).trim().toUpperCase();
    const pontoSelecionado = obterPontoContratoSelecionado(fluxo.id_contrato, codPontoNormalizado);
    const tipoPainelContrato = safeStr(pontoSelecionado?.tipo ?? pontoSelecionado?.TipoPainel ?? '').trim();

    if (!bloco || !codPontoNormalizado || !codFaceNormalizado) {
      return false;
    }

    const painelFace = encontrarPainelFacePorContrato(codPontoNormalizado, codFaceNormalizado, tipoPainelContrato);
    if (!painelFace) {
      setMensagemFluxoContrato(`O CodPonto ${codPontoNormalizado}${tipoPainelContrato ? ` (${tipoPainelContrato})` : ""} com a face ${codFaceNormalizado} existe no contrato, mas não foi encontrado no catálogo carregado do kanban.`, "alerta");
      return false;
    }

    const fonteContrato = dadosContrato && typeof dadosContrato === "object" ? dadosContrato : {};
    const precoAtualNormalizado = converterValorContratoParaNumero(
      fonteContrato.preco_venda_atual ??
      fonteContrato.TotalLiquidoContratoAGBRCTACORDO ??
      fonteContrato.faturamento_liquido_final_mensal ??
      fonteContrato.valor_mensal ??
      null
    );

    const dadosContratoNormalizados = {
      IDDimPaineisEuromidia: idNum(painelFace?.IDDimPaineisEuromidia ?? fonteContrato.id_painel ?? fonteContrato.IDPainelEuromidia ?? 0) || null,
      IDDimFacesPaineis: idNum(painelFace?.IDDimFacesPaineis ?? fonteContrato.id_dim_faces_paineis ?? fonteContrato.IDDimFacesPaineis ?? 0) || null,
      CodPonto: codPontoNormalizado,
      cod_ponto: codPontoNormalizado,
      CodFace: codFaceNormalizado,
      cod_face: codFaceNormalizado,
      TipoPainel: safeStr(tipoPainelContrato || painelFace?.Tipo || fonteContrato.tipo_face || '').trim() || null,
      tipo_painel: safeStr(tipoPainelContrato || painelFace?.Tipo || fonteContrato.tipo_face || '').trim() || null,
      DataInicio: safeStr(fonteContrato.data_inicio_previsto ?? fonteContrato.DataInicioPrevisto ?? "").trim() || null,
      data_inicio: safeStr(fonteContrato.data_inicio_previsto ?? fonteContrato.DataInicioPrevisto ?? "").trim() || null,
      DataFim: safeStr(fonteContrato.data_termino_previsto ?? fonteContrato.DataTerminoPrevisto ?? "").trim() || null,
      data_fim: safeStr(fonteContrato.data_termino_previsto ?? fonteContrato.DataTerminoPrevisto ?? "").trim() || null,
      PrecoVendaAtualContrato: Number.isFinite(precoAtualNormalizado) ? precoAtualNormalizado : null,
      preco_venda_atual: Number.isFinite(precoAtualNormalizado) ? precoAtualNormalizado : null
    };

    bloco.__itemContratoAditivoSelecionado = fonteContrato;
    marcarSelecaoContratoAditivoNoBloco(bloco, codPontoNormalizado, codFaceNormalizado, fonteContrato);
    await selecionarPainelCombobox(bloco, painelFace, false);
    await atualizarComercialDoBloco(bloco, dadosContratoNormalizados);
    renderizarInfoItemContratoAditivo(bloco, fonteContrato);
    return true;
  }

  async function aplicarPainelFaceContratoNoPrimeiroBloco(codPonto, codFace, dadosContrato = null) {
    const codPontoNormalizado = safeStr(codPonto).trim();
    const codFaceNormalizado = safeStr(codFace).trim().toUpperCase();

    if (!codPontoNormalizado || !codFaceNormalizado || !painelFaceLista) {
      return;
    }

    let bloco = painelFaceLista.querySelector('.kb-painel-item');
    if (!bloco) {
      bloco = criarPainelFaceItem();
      painelFaceLista.appendChild(bloco);
      atualizarTitulosPainelFace();
    }

    const aplicado = await aplicarPainelFaceContratoNoBloco(bloco, codPontoNormalizado, codFaceNormalizado, dadosContrato);
    if (aplicado) {
      const fluxo = obterFluxoContratoAtual();
      const pontoSelecionado = obterPontoContratoSelecionado(fluxo.id_contrato, codPontoNormalizado);
      const tipoPainelContrato = safeStr(pontoSelecionado?.tipo ?? pontoSelecionado?.TipoPainel ?? '').trim();
      setMensagemFluxoContrato(`Contrato existente selecionado. O painel ${codPontoNormalizado}${tipoPainelContrato ? ` (${tipoPainelContrato})` : ""} e a face ${codFaceNormalizado} foram carregados no primeiro bloco.`, "sucesso");
    }
  }

  async function carregarFluxoContratoParaEmpresa(idEmpresa, opcoes = {}) {
    const idEmp = idNum(idEmpresa);
    const idContratoSalvo = idNum(
      opcoes.id_contrato_existente ??
      opcoes.idContratoSelecionado ??
      opcoes.id_contrato ??
      0
    ) || null;
    const modoSalvo = normalizarModoContratoPersistido(
      opcoes.tipo_contrato_card ?? opcoes.modo_contrato ?? opcoes.tipo_contrato ?? "",
      idContratoSalvo ? VALOR_MODO_CONTRATO_ADITIVO : VALOR_MODO_CONTRATO_NOVO
    );
    const codPontoSalvo = safeStr(opcoes.cod_ponto_contrato ?? opcoes.codPontoContrato ?? "").trim();
    const codFaceSalva = safeStr(opcoes.cod_face_contrato ?? opcoes.codFaceContrato ?? "").trim().toUpperCase();
    const preservarSelecao = !!opcoes.preservarSelecao;
    const fluxoNovoPersistido = preservarSelecao && !idContratoSalvo && modoSalvo === VALOR_MODO_CONTRATO_NOVO;

    resetarFluxoContrato();

    if (!idEmp) {
      return;
    }

    if (wrapSelectContratoCard) wrapSelectContratoCard.hidden = false;
    setMensagemFluxoContrato("Buscando contratos da empresa...", "info");

    try {
      const contratos = await carregarContratosDaEmpresa(idEmp);
      montarSelectContratoCard(contratos);

      const contratoSalvoDisponivel = !!idContratoSalvo;

      if (preservarSelecao && (contratoSalvoDisponivel || fluxoNovoPersistido)) {
        if (contratoSalvoDisponivel) {
          const contratoSelecionado = (Array.isArray(contratos) ? contratos : []).find((contrato) => idNum(contrato?.id_contrato || 0) === idContratoSalvo) || null;
          selecionarValorOuAcrescentarOpcao(
            selectContratoCard,
            String(idContratoSalvo),
            contratoSelecionado?.label || `Contrato #${idContratoSalvo}`
          );
        } else {
          selecionarContratoSilenciosamente(VALOR_OPCAO_NOVO_CONTRATO);
        }

        montarSelectModoContratoCard(modoSalvo, contratoSalvoDisponivel);
        if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;
        selectModoContratoCard.value = modoSalvo;

        if (modoSalvo === VALOR_MODO_CONTRATO_ADITIVO) {
          if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = false;
          setMensagemFluxoContrato("Reaplicando o contrato salvo do card...", "info");

          const pontos = await carregarPontosDoContrato(idContratoSalvo);
          montarSelectPontosContratoCard(pontos);
          sincronizarSeletoresContratoAditivoEmTodosBlocos();

          if (codPontoSalvo) {
            const pontoSelecionado = (Array.isArray(pontos) ? pontos : []).find((ponto) => safeStr(ponto?.cod_ponto || "").trim() === codPontoSalvo) || null;
            selecionarValorOuAcrescentarOpcao(
              selectCodPontoContratoCard,
              codPontoSalvo,
              codPontoSalvo === VALOR_OPCAO_NOVO_PAINEL ? "Novo Painel" : (pontoSelecionado?.label || codPontoSalvo)
            );
          }

          if (!codPontoSalvo) {
            setMensagemFluxoContrato("Contrato salvo reaplicado. Agora selecione um CodPonto do contrato ou escolha Novo Painel.", "info");
            return;
          }

          if (codPontoSalvo === VALOR_OPCAO_NOVO_PAINEL) {
            if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = true;
            await sincronizarCodPontoContratoCardNoPrimeiroBloco(codPontoSalvo);
            setMensagemFluxoContrato("Contrato salvo reaplicado em modo Aditivo com Novo Painel selecionado.", "alerta");
            return;
          }

          if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = false;
          const faces = await carregarFacesDoContrato(idContratoSalvo, codPontoSalvo);
          montarSelectFacesContratoCard(faces);
          sincronizarSeletoresContratoAditivoEmTodosBlocos();
          await sincronizarCodPontoContratoCardNoPrimeiroBloco(codPontoSalvo);

          if (codFaceSalva) {
            const faceSelecionada = (Array.isArray(faces) ? faces : []).find((face) => safeStr(face?.cod_face || "").trim().toUpperCase() === codFaceSalva) || null;
            selecionarValorOuAcrescentarOpcao(
              selectCodFaceContratoCard,
              codFaceSalva,
              faceSelecionada?.label || codFaceSalva
            );
            await onCodFaceContratoCardChange({ confirmar: false });
            setMensagemFluxoContrato("Contrato, tipo da solicitação, CodPonto e CodFace salvos foram reaplicados no card.", "sucesso");
          } else {
            setMensagemFluxoContrato("Contrato salvo reaplicado. Agora selecione a face do CodPonto salvo.", "info");
          }

          return;
        }

        if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = true;
        if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = true;
        setMensagemFluxoContrato("Contrato da empresa e tipo da solicitação salvos foram reaplicados no card.", "sucesso");
        return;
      }

      if (contratos.length) {
        selecionarContratoSilenciosamente(VALOR_OPCAO_NOVO_CONTRATO);
        montarSelectModoContratoCard(VALOR_MODO_CONTRATO_NOVO, false);
        if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;
        setMensagemFluxoContrato(textoAvisoEmpresaJaTemContratos(), "alerta");
      } else {
        selecionarContratoSilenciosamente(VALOR_OPCAO_NOVO_CONTRATO);
        montarSelectModoContratoCard(VALOR_MODO_CONTRATO_NOVO, false);
        if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;
        setMensagemFluxoContrato("Nenhum contrato existente foi encontrado para esta empresa. O fluxo foi definido como Novo Contrato.", "info");
      }
    } catch (erro) {
      console.warn("carregarFluxoContratoParaEmpresa: falhou", erro);
      montarSelectContratoCard([]);

      if (preservarSelecao && (idContratoSalvo || fluxoNovoPersistido)) {
        if (idContratoSalvo) {
          selecionarValorOuAcrescentarOpcao(
            selectContratoCard,
            String(idContratoSalvo),
            `Contrato #${idContratoSalvo}`
          );
        } else {
          selecionarContratoSilenciosamente(VALOR_OPCAO_NOVO_CONTRATO);
        }
        montarSelectModoContratoCard(modoSalvo, !!idContratoSalvo);
        if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;
        selectModoContratoCard.value = modoSalvo;

        if (modoSalvo === VALOR_MODO_CONTRATO_ADITIVO) {
          if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = false;
          if (codPontoSalvo) {
            selecionarValorOuAcrescentarOpcao(
              selectCodPontoContratoCard,
              codPontoSalvo,
              codPontoSalvo === VALOR_OPCAO_NOVO_PAINEL ? "Novo Painel" : codPontoSalvo
            );
          }
          if (codPontoSalvo && codPontoSalvo !== VALOR_OPCAO_NOVO_PAINEL) {
            if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = false;
            if (codFaceSalva) {
              selecionarValorOuAcrescentarOpcao(selectCodFaceContratoCard, codFaceSalva, codFaceSalva);
            }
          }
        }

        setMensagemFluxoContrato("Não foi possível consultar novamente os contratos da empresa, mas o template reaplicou os valores salvos do card para você não perder o contexto.", "alerta");
        return;
      }

      selecionarContratoSilenciosamente(VALOR_OPCAO_NOVO_CONTRATO);
      montarSelectModoContratoCard(VALOR_MODO_CONTRATO_NOVO, false);
      if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;
      setMensagemFluxoContrato("Não foi possível consultar os contratos da empresa. O template caiu no fluxo seguro de Novo Contrato até o backend responder essa busca.", "alerta");
    }
  }

  async function onContratoCardChange() {
    sincronizarBuscaContratoComSelect();
    fecharListaContratosCombobox();

    const valorContrato = safeStr(selectContratoCard?.value || "").trim();

    limparSelectComPlaceholder(selectCodPontoContratoCard, "— Selecione o CodPonto —");
    limparSelectComPlaceholder(selectCodFaceContratoCard, "— Selecione o CodFace —");
    if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = true;
    if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = true;

    if (!valorContrato || valorContrato === VALOR_OPCAO_NOVO_CONTRATO) {
      montarSelectModoContratoCard(VALOR_MODO_CONTRATO_NOVO, false);
      if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;

      const empresaPossuiContratos = Array.isArray(contratosCardCatalogo)
        && contratosCardCatalogo.some((contrato) => idNum(contrato?.id_contrato || 0) > 0);

      if (empresaPossuiContratos) {
        setMensagemFluxoContrato(textoAvisoEmpresaJaTemContratos(), "alerta");
      } else {
        setMensagemFluxoContrato("Fluxo definido como Novo Contrato. Agora você pode escolher livremente os painéis e faces abaixo.", "info");
      }

      atualizarVisibilidadeDadosNovoContrato();
      sincronizarSeletoresContratoAditivoEmTodosBlocos();
      return;
    }

    const idContrato = idNum(valorContrato);
    if (!idContrato) {
      montarSelectModoContratoCard(VALOR_MODO_CONTRATO_NOVO, false);
      if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;
      setMensagemFluxoContrato("Contrato inválido. O fluxo voltou para Novo Contrato.", "alerta");
      atualizarVisibilidadeDadosNovoContrato();
      return;
    }

    montarSelectModoContratoCard(VALOR_MODO_CONTRATO_ADITIVO, true);
    selectModoContratoCard.value = VALOR_MODO_CONTRATO_ADITIVO;
    if (wrapSelectModoContratoCard) wrapSelectModoContratoCard.hidden = false;

    await onModoContratoCardChange();
  }

  async function onModoContratoCardChange() {
    const fluxo = obterFluxoContratoAtual();
    aplicarTipoDocumentoAditivoPadraoNosItensFormulario();

    limparSelectComPlaceholder(selectCodPontoContratoCard, "— Selecione o CodPonto —");
    limparSelectComPlaceholder(selectCodFaceContratoCard, "— Selecione o CodFace —");
    if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = true;

    if (!fluxo.id_contrato || fluxo.modo_contrato === VALOR_MODO_CONTRATO_NOVO) {
      if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = true;
      setMensagemFluxoContrato("Tipo definido como Novo Contrato. O vínculo será tratado como contrato novo no salvamento.", "info");
      atualizarVisibilidadeDadosNovoContrato();
      sincronizarSeletoresContratoAditivoEmTodosBlocos();
      return;
    }

    if (wrapSelectCodPontoContratoCard) wrapSelectCodPontoContratoCard.hidden = false;
    setMensagemFluxoContrato("Buscando os CodPonto do contrato para o fluxo de aditivo...", "info");

    try {
      const pontos = await carregarPontosDoContrato(fluxo.id_contrato);
      montarSelectPontosContratoCard(pontos);
      sincronizarSeletoresContratoAditivoEmTodosBlocos();
      setMensagemFluxoContrato("Selecione um CodPonto já existente do contrato ou escolha Novo Painel para incluir um painel novo no aditivo.", "info");
    } catch (erro) {
      console.warn("onModoContratoCardChange: falhou ao carregar pontos", erro);
      setMensagemFluxoContrato("Não foi possível carregar os CodPonto do contrato. Até o backend responder, você ainda consegue seguir com o preenchimento manual dos painéis abaixo.", "alerta");
    }

    atualizarVisibilidadeDadosNovoContrato();
  }

  async function onCodPontoContratoCardChange() {
    const fluxo = obterFluxoContratoAtual();

    limparSelectComPlaceholder(selectCodFaceContratoCard, "— Selecione o CodFace —");
    if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = true;

    if (!fluxo.id_contrato || fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO) {
      return;
    }

    if (!fluxo.cod_ponto_contrato) {
      setMensagemFluxoContrato("Selecione um CodPonto do contrato ou escolha Novo Painel.", "info");
      return;
    }

    if (fluxo.usar_novo_painel) {
      await sincronizarCodPontoContratoCardNoPrimeiroBloco(fluxo.cod_ponto_contrato);
      setMensagemFluxoContrato("Você escolheu Novo Painel dentro de um contrato existente. O salvamento continuará como Aditivo, mas com inclusão de um novo painel.", "alerta");
      return;
    }

    if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = false;
    setMensagemFluxoContrato("Buscando as faces do CodPonto selecionado...", "info");

    try {
      const faces = await carregarFacesDoContrato(fluxo.id_contrato, fluxo.cod_ponto_contrato);
      montarSelectFacesContratoCard(faces);
      sincronizarSeletoresContratoAditivoEmTodosBlocos();
      await sincronizarCodPontoContratoCardNoPrimeiroBloco(fluxo.cod_ponto_contrato);
      setMensagemFluxoContrato("CodPonto selecionado no contrato e aplicado automaticamente no primeiro bloco. Agora selecione a face já existente do contrato.", "info");
    } catch (erro) {
      console.warn("onCodPontoContratoCardChange: falhou ao carregar faces", erro);
      setMensagemFluxoContrato("Não foi possível carregar as faces do CodPonto selecionado. Até o backend responder, você ainda pode continuar a seleção manual abaixo.", "alerta");
    }
  }

  async function onCodFaceContratoCardChange(opcoes = {}) {
    const fluxo = obterFluxoContratoAtual();
    if (!fluxo.id_contrato || fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO) return;
    if (fluxo.usar_novo_painel) return;
    if (!fluxo.cod_ponto_contrato || !fluxo.cod_face_contrato) return;

    const chaveFaces = `${idNum(fluxo.id_contrato)}|${safeStr(fluxo.cod_ponto_contrato).trim()}`;
    const facesDisponiveis = facesPorContratoPontoCache.get(chaveFaces) || await carregarFacesDoContrato(fluxo.id_contrato, fluxo.cod_ponto_contrato);
    const faceSelecionada = encontrarFaceContratoEmLista(facesDisponiveis, fluxo.cod_face_contrato);

    if (!faceSelecionada) {
      setMensagemFluxoContrato(
        `CodPonto ${fluxo.cod_ponto_contrato} / CodFace ${fluxo.cod_face_contrato} não existe no contrato selecionado. Pode prosseguir como novo item do aditivo.`,
        "info"
      );
      return;
    }

    const deveConfirmar = opcoes.confirmar !== false;
    if (deveConfirmar) {
      const confirmado = confirmarCarregamentoItemContratoExistente({
        codPonto: fluxo.cod_ponto_contrato,
        codFace: fluxo.cod_face_contrato,
        faceSelecionada
      });

      if (!confirmado) {
        if (selectCodFaceContratoCard) selectCodFaceContratoCard.value = "";
        setMensagemFluxoContrato("Seleção cancelada. O item existente do contrato não foi carregado para edição.", "alerta");
        return;
      }
    }

    const precoAtualContrato = Number(faceSelecionada?.preco_venda_atual);
    if (Number.isFinite(precoAtualContrato)) {
      setMensagemFluxoContrato(
        `Face ${fluxo.cod_face_contrato} selecionada no contrato. Preço de venda atual no contrato: ${formatarMoedaBR(precoAtualContrato)}.`,
        "sucesso"
      );
    }

    await aplicarPainelFaceContratoNoPrimeiroBloco(fluxo.cod_ponto_contrato, fluxo.cod_face_contrato, faceSelecionada);
  }

  async function sincronizarTagTipoContratoDoCard(idCard, idTagDesejada, opcoes = {}) {
    const idCardNum = idNum(idCard);
    const idTag = idNum(idTagDesejada);
    if (!idCardNum || !idTag) return false;

    const idsTipoContrato = [ID_TAG_TIPO_CONTRATO_ADITIVO, ID_TAG_TIPO_CONTRATO_NOVO];
    const tagsAtuais = tagsDoCard(idCardNum);
    let alterou = false;

    for (const tagAtual of tagsAtuais) {
      const idTagAtual = idNum(tagAtual?.IDDimKanbanTag || 0);
      if (!idsTipoContrato.includes(idTagAtual)) continue;
      if (idTagAtual === idTag) continue;

      const rDel = await fetchKanbanComTimeout(`/kanban/api/cards/${idCardNum}/tags/${idTagAtual}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf }
      }, TIMEOUT_FETCH_POS_SAVE_KANBAN_MS);
      const jDel = await rDel.json().catch(() => null);
      if (!rDel.ok || !jDel || !jDel.ok) {
        throw new Error((jDel && (jDel.msg || jDel.erro)) || `Erro ao remover a tag ${idTagAtual}.`);
      }
      alterou = true;
    }

    const temDesejada = tagsDoCard(idCardNum).some((tagAtual) => idNum(tagAtual?.IDDimKanbanTag || 0) === idTag);
    if (!temDesejada) {
      const rAdd = await fetchKanbanComTimeout(`/kanban/api/cards/${idCardNum}/tags`, {
        method: "POST",
        credentials: "same-origin",
        headers: headersJSON,
        body: JSON.stringify({ id_tag: idTag })
      }, TIMEOUT_FETCH_POS_SAVE_KANBAN_MS);
      const jAdd = await rAdd.json().catch(() => null);
      if (!rAdd.ok || !jAdd || !jAdd.ok) {
        throw new Error((jAdd && (jAdd.msg || jAdd.erro)) || `Erro ao aplicar a tag ${idTag}.`);
      }
      alterou = true;
    }

    if (alterou || opcoes.forcarSincronizacao === true) {
      await sincronizarTagsDoCardAberto(idCardNum, { redesenhar: opcoes.redesenhar !== false });
    }

    return alterou;
  }

  function setMensagemCadastroEmpresa(texto, tipo = "info") {
    if (!msgCadastroEmpresa) return;

    const mensagem = safeStr(texto).trim();
    msgCadastroEmpresa.className = "kb-cadastro-empresa-status";
    msgCadastroEmpresa.textContent = mensagem;

    if (!mensagem) {
      return;
    }

    if (tipo === "erro") msgCadastroEmpresa.classList.add("is-erro");
    else if (tipo === "sucesso") msgCadastroEmpresa.classList.add("is-sucesso");
    else msgCadastroEmpresa.classList.add("is-info");
  }

  async function carregarEmpresasProprietarias() {
    if (empresasProprietariasCatalogo.length) return;

    const r = await fetch(`/kanban/api/empresas-proprietarias`, { credentials: "same-origin" });
    const j = await r.json().catch(() => null);

    if (!r.ok || !j || !j.ok) {
      throw new Error((j && (j.msg || j.erro)) || `Erro ao carregar empresas proprietárias (HTTP ${r.status})`);
    }

    empresasProprietariasCatalogo = Array.isArray(j.empresas_proprietarias) ? j.empresas_proprietarias : [];

    cadEmpresaProprietaria.innerHTML = "";
    cadEmpresaProprietaria.appendChild(el("option", { value: "" }, ["— Selecione —"]));

    empresasProprietariasCatalogo.forEach(item => {
      const id = Number(item?.IDEmpresaProprietaria || 0);
      const razao = safeStr(item?.RazaoSocial || "").trim() || `Empresa #${id}`;
      const cnpj = mascaraCnpj(item?.CNPJ || "");
      const texto = cnpj ? `${razao} | ${cnpj}` : razao;
      cadEmpresaProprietaria.appendChild(el("option", { value: String(id) }, [texto]));
    });
  }

  function obterEmpresaAtualDoCard() {
    const idEmp = idNum(selectEmpresaCard?.value || 0);
    if (idEmp && empresasPorId.has(idEmp)) {
      return empresasPorId.get(idEmp);
    }

    return obterCardPorId(cardAbertoId) || null;
  }

  function limparFormularioCadastroEmpresa() {
    cadEmpresaId.value = "";
    cadEmpresaCnpj.value = "";
    cadEmpresaProprietaria.value = "";
    cadEmpresaRazaoSocial.value = "";
    cadEmpresaNomeFantasia.value = "";
    cadEmpresaEmail.value = "";
    cadEmpresaBitCliente.value = "";
    cadEmpresaTelefone1.value = "";
    cadEmpresaTelefone2.value = "";
    cadEmpresaPais.value = "";
    cadEmpresaCep.value = "";
    cadEmpresaUf.value = "";
    cadEmpresaMunicipio.value = "";
    cadEmpresaBairro.value = "";
    cadEmpresaLogradouro.value = "";
    cadEmpresaNumero.value = "";
    cadEmpresaComplemento.value = "";
    cadEmpresaCnae.value = "";
    cadEmpresaCodigoPorte.value = "";
    cadEmpresaPorte.value = "";
    cadEmpresaDescricaoCnae.value = "";
    cadEmpresaNaturezaJuridica.value = "";
    cadEmpresaCapitalSocial.value = "";
    cadEmpresaIdentificadorMatrizFilial.value = "";
    cadEmpresaDescricaoIdentificadorMatrizFilial.value = "";
    cadEmpresaDescricaoSituacaoCadastral.value = "";
    cadEmpresaDescricaoMotivoSituacaoCadastral.value = "";
    cadEmpresaDescricaoTipoLogradouro.value = "";
    cadEmpresaLatitude.value = "";
    cadEmpresaLongitude.value = "";
    cadEmpresaDataInicioAtividades.value = "";
    cadEmpresaDataSituacaoEspecial.value = "";
    cadEmpresaDataOpcaoPeloSimples.value = "";
    cadEmpresaDataSituacaoCadastral.value = "";
    cadEmpresaDataExclusaoSimples.value = "";
    cadEmpresaDataAtualizacao.value = "";
    empresaCadastroUltimoCnpjConsultado = "";
  }

  function preencherFormularioCadastroEmpresa(empresa) {
    const emp = empresa || {};

    cadEmpresaId.value = safeStr(emp.IDEmpresa || "");
    cadEmpresaCnpj.value = mascaraCnpj(emp.CNPJ || "");
    cadEmpresaProprietaria.value = emp.IDEmpresaProprietaria !== null && emp.IDEmpresaProprietaria !== undefined ? String(emp.IDEmpresaProprietaria) : "";
    cadEmpresaRazaoSocial.value = safeStr(emp.RazaoSocial || "");
    cadEmpresaNomeFantasia.value = safeStr(emp.NomeFantasia || "");
    cadEmpresaEmail.value = safeStr(emp.Email || "");
    cadEmpresaBitCliente.value = emp.BitCliente === null || emp.BitCliente === undefined || emp.BitCliente === "" ? "" : String(Number(emp.BitCliente));
    cadEmpresaTelefone1.value = safeStr(emp.TelefoneContato1 || "");
    cadEmpresaTelefone2.value = safeStr(emp.TelefoneContato2 || "");
    cadEmpresaPais.value = safeStr(emp.Pais || "");
    cadEmpresaCep.value = safeStr(emp.CEP || "");
    cadEmpresaUf.value = safeStr(emp.UF || "");
    cadEmpresaMunicipio.value = safeStr(emp.Municipio || "");
    cadEmpresaBairro.value = safeStr(emp.Bairro || "");
    cadEmpresaLogradouro.value = safeStr(emp.Logradouro || "");
    cadEmpresaNumero.value = safeStr(emp.Numero || "");
    cadEmpresaComplemento.value = safeStr(emp.Complemento || "");
    cadEmpresaCnae.value = safeStr(emp.CNAE || "");
    cadEmpresaCodigoPorte.value = emp.CodigoPorte === null || emp.CodigoPorte === undefined ? "" : String(emp.CodigoPorte);
    cadEmpresaPorte.value = safeStr(emp.Porte || "");
    cadEmpresaDescricaoCnae.value = safeStr(emp.DescricaoCnae || "");
    cadEmpresaNaturezaJuridica.value = safeStr(emp.NaturezaJuridica || "");
    cadEmpresaCapitalSocial.value = emp.CapitalSocial === null || emp.CapitalSocial === undefined ? "" : String(emp.CapitalSocial);
    cadEmpresaIdentificadorMatrizFilial.value = emp.IdentificadorMatrizFilial === null || emp.IdentificadorMatrizFilial === undefined ? "" : String(emp.IdentificadorMatrizFilial);
    cadEmpresaDescricaoIdentificadorMatrizFilial.value = safeStr(emp.DescricaoIdentificadorMatrizFilial || "");
    cadEmpresaDescricaoSituacaoCadastral.value = safeStr(emp.DescricaoSituacaoCadastral || "");
    cadEmpresaDescricaoMotivoSituacaoCadastral.value = safeStr(emp.DescricaoMotivoSituacaoCadastral || "");
    cadEmpresaDescricaoTipoLogradouro.value = safeStr(emp.DescricaoTipoLogradouro || "");
    cadEmpresaLatitude.value = emp.Latitude === null || emp.Latitude === undefined ? "" : String(emp.Latitude);
    cadEmpresaLongitude.value = emp.Longitude === null || emp.Longitude === undefined ? "" : String(emp.Longitude);
    cadEmpresaDataInicioAtividades.value = safeStr(emp.DataInicioAtividades || "").slice(0, 10);
    cadEmpresaDataSituacaoEspecial.value = safeStr(emp.DataSituacaoEspecial || "").slice(0, 10);
    cadEmpresaDataOpcaoPeloSimples.value = safeStr(emp.DataOpcaoPeloSimples || "").slice(0, 10);
    cadEmpresaDataSituacaoCadastral.value = safeStr(emp.DataSituacaoCadastral || "").slice(0, 10);
    cadEmpresaDataExclusaoSimples.value = safeStr(emp.DataExclusaoSimples || "").slice(0, 10);
    cadEmpresaDataAtualizacao.value = safeStr(emp.DataAtualizacao || "");

    empresaCadastroUltimoCnpjConsultado = normalizaCnpj(cadEmpresaCnpj.value || "");
  }

  function coletarFormularioCadastroEmpresa() {
    return {
      IDEmpresa: cadEmpresaId.value ? Number(cadEmpresaId.value) : null,
      IDEmpresaProprietaria: cadEmpresaProprietaria.value ? Number(cadEmpresaProprietaria.value) : null,
      CNPJ: cadEmpresaCnpj.value || "",
      UF: cadEmpresaUf.value || null,
      CEP: cadEmpresaCep.value || null,
      CodigoPorte: cadEmpresaCodigoPorte.value || null,
      Pais: cadEmpresaPais.value || null,
      Email: cadEmpresaEmail.value || null,
      Porte: cadEmpresaPorte.value || null,
      Bairro: cadEmpresaBairro.value || null,
      Numero: cadEmpresaNumero.value || null,
      TelefoneContato1: cadEmpresaTelefone1.value || null,
      Municipio: cadEmpresaMunicipio.value || null,
      Logradouro: cadEmpresaLogradouro.value || null,
      CNAE: cadEmpresaCnae.value || null,
      Complemento: cadEmpresaComplemento.value || null,
      RazaoSocial: cadEmpresaRazaoSocial.value || null,
      NomeFantasia: cadEmpresaNomeFantasia.value || null,
      CapitalSocial: cadEmpresaCapitalSocial.value || null,
      TelefoneContato2: cadEmpresaTelefone2.value || null,
      NaturezaJuridica: cadEmpresaNaturezaJuridica.value || null,
      DescricaoCnae: cadEmpresaDescricaoCnae.value || null,
      DataInicioAtividades: cadEmpresaDataInicioAtividades.value || null,
      DataSituacaoEspecial: cadEmpresaDataSituacaoEspecial.value || null,
      DataOpcaoPeloSimples: cadEmpresaDataOpcaoPeloSimples.value || null,
      DataSituacaoCadastral: cadEmpresaDataSituacaoCadastral.value || null,
      DataExclusaoSimples: cadEmpresaDataExclusaoSimples.value || null,
      IdentificadorMatrizFilial: cadEmpresaIdentificadorMatrizFilial.value || null,
      DescricaoSituacaoCadastral: cadEmpresaDescricaoSituacaoCadastral.value || null,
      DescricaoMotivoSituacaoCadastral: cadEmpresaDescricaoMotivoSituacaoCadastral.value || null,
      DescricaoIdentificadorMatrizFilial: cadEmpresaDescricaoIdentificadorMatrizFilial.value || null,
      DescricaoTipoLogradouro: cadEmpresaDescricaoTipoLogradouro.value || null,
      Latitude: cadEmpresaLatitude.value || null,
      Longitude: cadEmpresaLongitude.value || null,
      BitCliente: cadEmpresaBitCliente.value === "" ? null : Number(cadEmpresaBitCliente.value),
    };
  }

  function atualizarCatalogoEmpresa(empresa) {
    if (!empresa) return;

    const idEmpresa = idNum(empresa.IDEmpresa ?? empresa.ID ?? empresa.id_empresa ?? 0);
    if (!idEmpresa) return;

    registrarEmpresasNoCatalogo([Object.assign({}, empresa || {}, {
      IDEmpresa: idEmpresa,
      ID: idEmpresa
    })]);

    garantirOpcaoEmpresaNoSelect(idEmpresa);
    montarSelectEmpresas();
  }

  async function consultarCadastroEmpresa(parametros = {}) {
    const idEmpresa = idNum(parametros.idEmpresa || 0);
    const cnpjDigits = normalizaCnpj(parametros.cnpj || "");
    const query = new URLSearchParams();

    if (idEmpresa > 0) {
      query.set("id_empresa", String(idEmpresa));
    } else if (cnpjDigits.length === 14) {
      query.set("cnpj", cnpjDigits);
    } else {
      throw new Error("Informe um ID de empresa válido ou um CNPJ com 14 dígitos para consultar.");
    }

    const url = `/kanban/api/empresas/cadastro?${query.toString()}`;
    const r = await fetch(url, {
      credentials: "same-origin",
      signal: parametros.signal || null,
    });
    const j = await r.json().catch(() => null);

    if (!r.ok || !j || !j.ok) {
      throw new Error((j && (j.msg || j.erro)) || `Erro ao consultar empresa (HTTP ${r.status})`);
    }

    return j;
  }

  async function buscarCadastroEmpresaPorCnpj(cnpjInformado, opcoes = {}) {
    const cnpjDigits = normalizaCnpj(cnpjInformado);
    if (cnpjDigits.length !== 14) {
      if (empresaCadastroConsultaController) {
        empresaCadastroConsultaController.abort();
        empresaCadastroConsultaController = null;
      }
      if (!opcoes.silencioso) {
        setMensagemCadastroEmpresa("Digite um CNPJ completo com 14 dígitos para buscar.", "info");
      }
      return null;
    }

    if (empresaCadastroConsultaController) {
      empresaCadastroConsultaController.abort();
    }

    const controller = new AbortController();
    empresaCadastroConsultaController = controller;

    setMensagemCadastroEmpresa("Consultando cadastro da empresa...", "info");

    try {
      const resposta = await consultarCadastroEmpresa({ cnpj: cnpjDigits, signal: controller.signal });

      if (empresaCadastroConsultaController !== controller) {
        return null;
      }

      if (resposta.encontrado && resposta.empresa) {
        preencherFormularioCadastroEmpresa(resposta.empresa);
        setMensagemCadastroEmpresa(
          resposta.origem === "banco"
            ? "Cadastro encontrado no banco de dados."
            : "Cadastro encontrado na API Minha Receita e preenchido no formulário.",
          "sucesso"
        );
        return resposta.empresa;
      }

      if (!opcoes.silencioso) {
        setMensagemCadastroEmpresa("CNPJ não encontrado no banco e nem na API. Você pode preencher manualmente.", "info");
      }
      return null;
    } catch (erro) {
      if (erro?.name === "AbortError") {
        return null;
      }
      throw erro;
    } finally {
      if (empresaCadastroConsultaController === controller) {
        empresaCadastroConsultaController = null;
      }
    }
  }

  async function abrirModalCadastroEmpresa() {
    try {
      await carregarEmpresasProprietarias();
      limparFormularioCadastroEmpresa();
      setMensagemCadastroEmpresa("", "info");

      const idEmpresaSelecionada = idNum(selectEmpresaCard?.value || 0);
      const empresaAtual = obterEmpresaAtualDoCard();
      const idEmpresaAtual = idEmpresaSelecionada || idNum(empresaAtual?.IDEmpresa || empresaAtual?.IDEmpresaRelacionadaCard || 0);
      const idEmpresaProprietariaAtual = idNum(empresaAtual?.IDEmpresaProprietaria || 0);

      if (idEmpresaProprietariaAtual) {
        cadEmpresaProprietaria.value = String(idEmpresaProprietariaAtual);
      }

      if (idEmpresaAtual) {
        const resposta = await consultarCadastroEmpresa({ idEmpresa: idEmpresaAtual });
        if (resposta?.empresa) {
          preencherFormularioCadastroEmpresa(resposta.empresa);
          setMensagemCadastroEmpresa("Cadastro carregado para edição.", "info");
        }
      }

      modalCadastroEmpresa.style.display = "block";
    } catch (erro) {
      setMensagemCadastroEmpresa(String(erro), "erro");
      modalCadastroEmpresa.style.display = "block";
    }
  }

  function fecharModalCadastroEmpresa() {
    if (!modalCadastroEmpresa) return;
    modalCadastroEmpresa.style.display = "none";
    setMensagemCadastroEmpresa("", "info");
    window.clearTimeout(empresaCadastroConsultaTimer);
    if (empresaCadastroConsultaController) {
      empresaCadastroConsultaController.abort();
      empresaCadastroConsultaController = null;
    }
  }

  function agendarBuscaAutomaticaCadastroEmpresa() {
    window.clearTimeout(empresaCadastroConsultaTimer);
    const cnpjDigits = normalizaCnpj(cadEmpresaCnpj.value || "");

    if (cnpjDigits.length !== 14) {
      empresaCadastroUltimoCnpjConsultado = "";
      if (empresaCadastroConsultaController) {
        empresaCadastroConsultaController.abort();
        empresaCadastroConsultaController = null;
      }
      return;
    }

    if (empresaCadastroUltimoCnpjConsultado === cnpjDigits) {
      return;
    }

    empresaCadastroConsultaTimer = window.setTimeout(async () => {
      try {
        await buscarCadastroEmpresaPorCnpj(cnpjDigits, { silencioso: true, idEmpresa: idNum(cadEmpresaId.value || 0) });
        empresaCadastroUltimoCnpjConsultado = cnpjDigits;
      } catch (erro) {
        setMensagemCadastroEmpresa(String(erro), "erro");
      }
    }, 300);
  }

  async function salvarCadastroEmpresa() {
    const cnpjDigits = normalizaCnpj(cadEmpresaCnpj.value || "");
    if (cnpjDigits.length !== 14) {
      setMensagemCadastroEmpresa("CNPJ é obrigatório e deve ter 14 dígitos.", "erro");
      cadEmpresaCnpj.focus();
      return;
    }

    const payload = coletarFormularioCadastroEmpresa();
    payload.CNPJ = cnpjDigits;

    btnSalvarCadastroEmpresa.disabled = true;
    setMensagemCadastroEmpresa("Salvando cadastro da empresa...", "info");

    try {
      const r = await fetch(`/kanban/api/empresas/cadastro/salvar`, {
        method: "POST",
        credentials: "same-origin",
        headers: headersJSON,
        body: JSON.stringify(payload),
      });

      const j = await r.json().catch(() => null);
      if (!r.ok || !j || !j.ok) {
        throw new Error((j && (j.msg || j.erro)) || `Erro ao salvar empresa (HTTP ${r.status})`);
      }

      if (j.empresa) {
        preencherFormularioCadastroEmpresa(j.empresa);
        atualizarCatalogoEmpresa(j.empresa);
        if (selectEmpresaCard) {
          selectEmpresaCard.value = String(j.empresa.IDEmpresa || "");
          sincronizarBuscaEmpresaComSelect();
        }
        setEmpresaPreviewByObj(j.empresa);
      }

      setMensagemCadastroEmpresa(j.msg || "Empresa salva com sucesso.", "sucesso");
    } catch (erro) {
      setMensagemCadastroEmpresa(String(erro), "erro");
    } finally {
      btnSalvarCadastroEmpresa.disabled = false;
    }
  }

  function formatarMoedaBR(valor){
    const num = Number(valor);
    if (!Number.isFinite(num)) return "—";
    return num.toLocaleString("pt-BR", { style:"currency", currency:"BRL" });
  }

  function formatarPercentualBR(valor){
    const num = Number(valor);
    if (!Number.isFinite(num)) return "—";
    return `${num.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  }

  function formatarNumeroBrSemSimbolo(valor, casas = 2){
    const num = Number(valor);
    if (!Number.isFinite(num)) return "";
    return num.toLocaleString("pt-BR", {
      minimumFractionDigits: casas,
      maximumFractionDigits: casas
    });
  }

  function formatarNumeroBR(valor, casas = 2){
    return formatarNumeroBrSemSimbolo(valor, casas) || "—";
  }

  function formatarValorContabilParaInput(valor){
    return formatarNumeroBrSemSimbolo(valor, 2);
  }

  function formatarPercentualParaInput(valor){
    return formatarNumeroBrSemSimbolo(valor, 2);
  }

  function calcularPercentualDescontoPorNovoValor(valorBase, novoValor){
    const base = Number(valorBase);
    const novo = Number(novoValor);

    if (!Number.isFinite(base) || base === 0 || !Number.isFinite(novo)) {
      return null;
    }

    return ((base - novo) / base) * 100;
  }

  function calcularNovoValorPorPercentual(valorBase, percentual){
    const base = Number(valorBase);
    const perc = Number(percentual);

    if (!Number.isFinite(base) || !Number.isFinite(perc)) {
      return null;
    }

    return base * (1 - (perc / 100));
  }

  function renderizarResumoComercial(resumo){
    resumoComercial = resumo && typeof resumo === "object" ? Object.assign({}, resumo) : null;

    if (kpiAtendimentosAtivos) {
      kpiAtendimentosAtivos.textContent = resumoComercial
        ? String(idNum(resumoComercial.QuantidadeAtendimentosAtivos))
        : "—";
    }

    if (kpiAprovacaoPreco) {
      kpiAprovacaoPreco.textContent = resumoComercial
        ? String(idNum(resumoComercial.QuantidadeAprovacaoPreco))
        : "—";
    }

    if (kpiCustoTotal && PODE_VER_CUSTO_MARGEM) {
      kpiCustoTotal.textContent = resumoComercial
        ? formatarMoedaBR(resumoComercial.ValorCustoTotal)
        : "—";
    }

    if (kpiVendaTotal) {
      kpiVendaTotal.textContent = resumoComercial
        ? formatarMoedaBR(resumoComercial.ValorVendaTotal)
        : "—";
    }

    if (kpiMargemPercentual && PODE_VER_CUSTO_MARGEM) {
      const margem = resumoComercial ? Number(resumoComercial.MargemPercentualTotal) : NaN;
      kpiMargemPercentual.textContent = Number.isFinite(margem) ? formatarPercentualBR(margem) : "—";
      kpiMargemPercentual.classList.remove("positivo", "negativo");
      if (Number.isFinite(margem)) {
        kpiMargemPercentual.classList.add(margem >= 0 ? "positivo" : "negativo");
      }
    }
  }

  let temporizadorResumoComercial = null;
  let resumoComercialEmAndamento = false;
  let resumoComercialPendente = false;

  async function recarregarResumoComercial(){
    if (resumoComercialEmAndamento) {
      resumoComercialPendente = true;
      return false;
    }

    const sequenciaAtual = ++sequenciaResumoComercial;
    resumoComercialEmAndamento = true;
    resumoComercialPendente = false;

    try {
      if (haFiltroAtivo() && obterIdsCardsVisiveisAtuais().length === 0) {
        renderizarResumoComercial(resumoComercialZerado());
        return true;
      }

      const queryFiltros = montarQueryResumoComercialFiltrado();
      const separador = queryFiltros ? `?${queryFiltros}` : "";
      const r = await fetch(`/kanban/api/kanbans/${ID_KANBAN}/resumo-comercial${separador}`, { credentials: "same-origin" });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j || !j.ok) return false;
      if (sequenciaAtual !== sequenciaResumoComercial) return false;
      renderizarResumoComercial(j.resumo_comercial || null);
      return true;
    } catch (_erro) {
      return false;
    } finally {
      if (sequenciaAtual === sequenciaResumoComercial) {
        resumoComercialEmAndamento = false;
        if (resumoComercialPendente && !document.hidden) {
          resumoComercialPendente = false;
          agendarRecarregarResumoComercial(900);
        }
      }
    }
  }


function agendarRecarregarResumoComercial(atrasoMs = 450){
  if (temporizadorResumoComercial) {
    window.clearTimeout(temporizadorResumoComercial);
  }

  temporizadorResumoComercial = window.setTimeout(async () => {
    temporizadorResumoComercial = null;

    if (document.hidden) return;
    await recarregarResumoComercial();
  }, Math.max(0, Number(atrasoMs) || 0));
}



function parseNumeroInput(valor){
  if (valor === null || valor === undefined) return null;

  if (typeof valor === "number") {
    return Number.isFinite(valor) ? valor : null;
  }

  let txt = safeStr(valor)
    .replace(/\u00A0/g, " ")
    .replace(/R\$\s*/gi, "")
    .trim();

  if (!txt) return null;

  txt = txt.replace(/\s+/g, "");

  const temVirgula = txt.includes(",");
  const temPonto = txt.includes(".");

  /*
    Regras:
    1) Se tiver vírgula e ponto:
       - se a vírgula estiver por último, assumo formato BR: 2.400,00
       - se o ponto estiver por último, assumo formato decimal padrão: 2400.00
    2) Se tiver só vírgula:
       - assumo decimal BR: 12,5 -> 12.5
    3) Se tiver só ponto:
       - assumo decimal normal: 2400.00
       - se tiver vários pontos, trato os anteriores como separadores de milhar
  */

  if (temVirgula && temPonto) {
    if (txt.lastIndexOf(",") > txt.lastIndexOf(".")) {
      txt = txt.replace(/\./g, "").replace(",", ".");
    } else {
      txt = txt.replace(/,/g, "");
    }
  } else if (temVirgula) {
    txt = txt.replace(",", ".");
  } else if (temPonto) {
    const partes = txt.split(".");
    if (partes.length > 2) {
      const parteDecimal = partes.pop();
      txt = partes.join("") + "." + parteDecimal;
    }
  }

  const num = Number(txt);
  return Number.isFinite(num) ? num : null;
}

function formatarNumeroParaInput(valor){
  if (valor === null || valor === undefined) return "";

  if (typeof valor === "number") {
    return Number.isFinite(valor) ? String(valor) : "";
  }

  const txt = safeStr(valor).trim();
  if (!txt) return "";

  const num = parseNumeroInput(txt);
  return num === null ? "" : String(num);
}


  function atualizarTitulosPainelFace(){
    const itens = [...(painelFaceLista?.querySelectorAll('.kb-painel-item') || [])];
    itens.forEach((item, idx) => {
      const titulo = item.querySelector('[data-role="titulo-item"]');
      if (!titulo) return;

      const exibicoes = obterExibicoesDiaSelecionadasDoBloco(item)
        .map((valor) => safeStr(valor || '').trim())
        .filter(Boolean);

      const sufixoExibicoes = exibicoes.length === 1
        ? ` • ${exibicoes[0]}`
        : (exibicoes.length > 1 ? ` • ${exibicoes.join(', ')}` : '');

      titulo.textContent = `Painel / Face ${idx + 1}${sufixoExibicoes}`;
    });
  }

  function textoOpcaoPainel(item){
    const codPonto = safeStr(item?.CodPonto || item?.cod_ponto || "").trim();
    const logradouro = safeStr(item?.Logradouro || item?.logradouro || "").trim();
    const tipo = safeStr(item?.Tipo || item?.tipo || "").trim();
    const cidade = safeStr(item?.Cidade || item?.cidade || "").trim();

    const partes = [codPonto, logradouro, tipo, cidade].filter(Boolean);
    return partes.length ? partes.join(" • ") : "—";
  }

  function obterChavePainelFaceCatalogo(item){
    const idPainel = idNum(item?.IDDimPaineisEuromidia ?? item?.id_painel ?? 0);
    const codFace = safeStr(item?.CodFace ?? item?.cod_face ?? '').trim().toUpperCase();
    if (!idPainel || !codFace) return '';
    return `${idPainel}|${codFace}`;
  }

  function resumoPainelFace(item){
    const logradouro = safeStr(item?.Logradouro ?? item?.logradouro ?? '').trim();
    const cidade = safeStr(item?.Cidade ?? item?.cidade ?? '').trim();
    const bairro = safeStr(item?.Bairro ?? item?.bairro ?? '').trim();
    const tipo = safeStr(item?.Tipo ?? item?.tipo ?? item?.TipoPainel ?? item?.tipo_painel ?? '').trim();
    return [logradouro, cidade, bairro, tipo].filter(Boolean).join(' • ') || '—';
  }

  function textoOpcaoPainelFace(item){
    const codFace = safeStr(item?.CodFace ?? item?.cod_face ?? '').trim().toUpperCase();
    const resumo = resumoPainelFace(item);
    return [codFace, resumo].filter(Boolean).join(' • ') || '—';
  }

  function atualizarMapaPainelFacesCatalogo(){
    painelFacesPorChave = new Map();
    for (const item of (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : [])) {
      const chave = obterChavePainelFaceCatalogo(item);
      if (!chave || painelFacesPorChave.has(chave)) continue;
      painelFacesPorChave.set(chave, Object.assign({}, item || {}));
    }
  }

  function reconstruirPaineisCatalogoAPartirDasFaces(){
    const mapa = new Map();

    (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : []).forEach((face) => {
      const idPainel = idNum(face?.IDDimPaineisEuromidia ?? 0);
      if (!idPainel || mapa.has(idPainel)) return;

      mapa.set(idPainel, {
        IDDimPaineisEuromidia: idPainel,
        CodPonto: safeStr(face?.CodPonto ?? '').trim() || null,
        Tipo: safeStr(face?.Tipo ?? '').trim() || null,
        Cidade: safeStr(face?.Cidade ?? '').trim() || null,
        UF: safeStr(face?.UF ?? '').trim() || null,
        Logradouro: safeStr(face?.Logradouro ?? '').trim() || null,
        Bairro: safeStr(face?.Bairro ?? '').trim() || null,
        Numero: safeStr(face?.Numero ?? '').trim() || null,
        CEP: safeStr(face?.CEP ?? '').trim() || null,
        QuantidadeFaces: idNum(face?.QuantidadeFaces ?? 0) || null,
        BitAtivo: typeof face?.BitAtivo === 'undefined' ? 1 : idNum(face?.BitAtivo ?? 0),
      });
    });

    paineisCatalogo = Array.from(mapa.values());
  }

  async function carregarCatalogoPainelFaces(opcoes = {}){
    const forcar = opcoes?.forcar === true || opcoes?.force === true;

    if (!(kanbanCfg && kanbanCfg.MostrarPainelFaceNoCard)) {
      painelFacesCatalogo = [];
      paineisCatalogo = [];
      catalogoPainelFacesCarregado = false;
      promessaCatalogoPainelFaces = null;
      atualizarMapaPainelFacesCatalogo();
      return [];
    }

    if (!forcar && catalogoPainelFacesCarregado) {
      return painelFacesCatalogo;
    }

    if (!forcar && promessaCatalogoPainelFaces) {
      return promessaCatalogoPainelFaces;
    }

    promessaCatalogoPainelFaces = (async () => {
      try {
        const resultado = await fetchJsonKanban('/kanban/api/painel-faces/catalogo');
        const j = resultado.corpo;

        if (!respostaJsonKanbanOk(resultado)) {
          console.warn('carregarCatalogoPainelFaces: resposta inválida', detalhesFalhaJsonKanban(resultado));
          if (!Array.isArray(painelFacesCatalogo) || !painelFacesCatalogo.length) {
            painelFacesCatalogo = [];
            paineisCatalogo = [];
            atualizarMapaPainelFacesCatalogo();
          }
          return painelFacesCatalogo;
        }

        painelFacesCatalogo = Array.isArray(j.painel_faces)
          ? j.painel_faces.map((item) => Object.assign({}, item || {}))
          : [];
        catalogoPainelFacesCarregado = true;
        reconstruirPaineisCatalogoAPartirDasFaces();
        atualizarMapaPainelFacesCatalogo();
        sincronizarPainelFacesVisiveisAposCatalogo();
        return painelFacesCatalogo;
      } catch (erro) {
        console.warn('carregarCatalogoPainelFaces: falhou', erro);
        if (!Array.isArray(painelFacesCatalogo) || !painelFacesCatalogo.length) {
          painelFacesCatalogo = [];
          paineisCatalogo = [];
          atualizarMapaPainelFacesCatalogo();
        }
        return painelFacesCatalogo;
      } finally {
        promessaCatalogoPainelFaces = null;
      }
    })();

    return promessaCatalogoPainelFaces;
  }

  function localizarPainelFaceCatalogo(criterios = {}){
    const idPainel = idNum(criterios?.id_painel ?? criterios?.IDDimPaineisEuromidia ?? 0) || null;
    const codPonto = safeStr(criterios?.cod_ponto ?? criterios?.CodPonto ?? '').trim();
    const codFace = safeStr(criterios?.cod_face ?? criterios?.CodFace ?? '').trim().toUpperCase();
    const tipoPainel = safeStr(criterios?.tipo_painel ?? criterios?.TipoPainel ?? criterios?.Tipo ?? '').trim();
    if (!codFace && !idPainel && !codPonto) return null;

    if (idPainel && codFace) {
      const chave = `${idPainel}|${codFace}`;
      if (painelFacesPorChave.has(chave)) {
        return painelFacesPorChave.get(chave) || null;
      }
    }

    const candidatos = (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : []).filter((item) => {
      const codFaceItem = safeStr(item?.CodFace ?? '').trim().toUpperCase();
      if (codFace && codFaceItem !== codFace) return false;
      if (idPainel && idNum(item?.IDDimPaineisEuromidia ?? 0) !== idPainel) return false;
      if (codPonto && safeStr(item?.CodPonto ?? '').trim() !== codPonto) return false;
      return true;
    });

    if (!candidatos.length) return null;
    if (candidatos.length === 1) return candidatos[0];

    const tipoNormalizado = normalizarTextoComparacaoPainelContrato(tipoPainel);
    if (tipoNormalizado) {
      const mesmoTipo = candidatos.find((item) => {
        const tipoItem = normalizarTextoComparacaoPainelContrato(item?.Tipo ?? item?.TipoPainel ?? '');
        return tipoItem === tipoNormalizado;
      });
      if (mesmoTipo) return mesmoTipo;
    }

    return candidatos[0];
  }

  function preencherSelectFiltroPainelFace(selectEl, valores, placeholder){
    if (!selectEl) return;
    const valorAtual = safeStr(selectEl.value || '').trim();
    const valoresLista = Array.isArray(valores) ? valores : [];
    const valoresValidos = new Set();

    selectEl.innerHTML = '';
    selectEl.appendChild(el('option', { value:'' }, [placeholder]));
    valoresLista.forEach((valor) => {
      const texto = safeStr(valor || '').trim();
      if (!texto || valoresValidos.has(texto)) return;
      valoresValidos.add(texto);
      selectEl.appendChild(el('option', { value:texto }, [texto]));
    });

    // Não deixa o valor salvo sumir enquanto o catálogo ainda está carregando
    // ou quando a lista dependente cidade/tipo ainda está sendo reconstruída.
    if (valorAtual) {
      if (!valoresValidos.has(valorAtual)) {
        selectEl.appendChild(el('option', { value:valorAtual }, [valorAtual]));
      }
      selectEl.value = valorAtual;
    }
  }

  function obterPrimeiroCampoPainelFace(dados, campos){
    if (!dados || typeof dados !== 'object') return '';

    for (const campo of (Array.isArray(campos) ? campos : [])) {
      const valor = dados?.[campo];
      if (valor !== null && valor !== undefined && safeStr(valor).trim() !== '') {
        return valor;
      }
    }

    return '';
  }

  function extrairCidadePainelFaceSalva(dados){
    return safeStr(obterPrimeiroCampoPainelFace(dados, [
      'Cidade', 'cidade', 'CidadePainel', 'cidade_painel', 'CidadeFace', 'cidade_face'
    ])).trim();
  }

  function extrairTipoPainelFaceSalvo(dados){
    return safeStr(obterPrimeiroCampoPainelFace(dados, [
      'TipoPainel', 'tipo_painel', 'Tipo', 'tipo', 'Produto', 'produto', 'TipoProduto', 'tipo_produto'
    ])).trim();
  }

  function extrairPeriodoExibicaoSalvo(dados){
    return safeStr(obterPrimeiroCampoPainelFace(dados, [
      'PeriodoExibicao', 'periodo_exibicao', 'PeriodoCampanha', 'periodo_campanha'
    ])).trim();
  }

  function extrairExibicoesDiaSalvas(dados){
    return safeStr(obterPrimeiroCampoPainelFace(dados, [
      'ExibicoesDia', 'exibicoes_dia', 'InsercoesDia', 'insercoes_dia', 'Cota', 'cota'
    ])).trim();
  }

  function extrairIdPrecoSalvoPainelFace(dados){
    return idNum(obterPrimeiroCampoPainelFace(dados, [
      'IDDimTabelaPrecosEuromidia', 'id_preco', 'IDTabelaPreco', 'id_tabela_preco'
    ])) || null;
  }

  function garantirOpcaoSelectSemDuplicar(selectEl, valor, label = ''){
    if (!selectEl) return false;
    const valorTexto = safeStr(valor || '').trim();
    if (!valorTexto) return false;

    if (!possuiOpcaoNoSelect(selectEl, valorTexto)) {
      selectEl.appendChild(el('option', { value:valorTexto }, [safeStr(label || valorTexto).trim() || valorTexto]));
    }
    return true;
  }

  function selecionarValorSelectPreservandoOpcoes(selectEl, valor, label = ''){
    if (!selectEl) return false;
    const valorTexto = safeStr(valor || '').trim();
    if (!valorTexto) {
      selectEl.value = '';
      return false;
    }

    garantirOpcaoSelectSemDuplicar(selectEl, valorTexto, label || valorTexto);
    selectEl.value = valorTexto;
    return true;
  }

  function preencherSeletoresTabelaPrecoComDadosSalvos(bloco, dadosSalvos = null, opcoes = {}){
    if (!bloco || !dadosSalvos) return;

    const selectExibicoes = bloco.querySelector('[data-role="select-exibicoes-dia"]');
    const selectPeriodo = bloco.querySelector('[data-role="select-periodo-exibicao"]');
    const selectPreco = bloco.querySelector('[data-role="select-preco"]');
    const wrapExibicoes = bloco.querySelector('[data-role="wrap-exibicoes-dia"]');
    const wrapPeriodo = bloco.querySelector('[data-role="wrap-periodo-exibicao"]');

    const exibicoesDia = extrairExibicoesDiaSalvas(dadosSalvos);
    const exibicoesLista = normalizarListaExibicoesDia(exibicoesDia);
    const exibicaoPrincipal = exibicoesLista[0] || '';
    const periodo = extrairPeriodoExibicaoSalvo(dadosSalvos);
    const idPreco = extrairIdPrecoSalvoPainelFace(dadosSalvos);

    if (exibicoesLista.length && selectExibicoes) {
      if (wrapExibicoes) wrapExibicoes.hidden = false;
      exibicoesLista.forEach((valorExibicao) => {
        garantirOpcaoSelectSemDuplicar(selectExibicoes, valorExibicao, labelExibicoesDiaDoPreco(valorExibicao));
      });
      selecionarValoresNoSelect(selectExibicoes, exibicoesLista, { sincronizarDropdown: false });
      aplicarGrupoVisualExibicoesDiaNoBloco(bloco, exibicoesLista, exibicaoPrincipal);
      atualizarDropdownExibicoesDia(selectExibicoes);
    }

    if (periodo && selectPeriodo) {
      if (wrapPeriodo) wrapPeriodo.hidden = false;
      selecionarValorSelectPreservandoOpcoes(selectPeriodo, periodo, periodo);
    }

    if (idPreco && selectPreco) {
      const labelPreco = [periodo, exibicaoPrincipal ? labelExibicoesDiaDoPreco(exibicaoPrincipal) : '']
        .filter(Boolean)
        .join(' | ') || String(idPreco);
      definirValorSelectOculto(selectPreco, String(idPreco), labelPreco);
    }

    if (opcoes.atualizarResumo !== false && selectExibicoes) {
      atualizarResumoDropdownExibicoesDia(selectExibicoes);
    }
  }

  function aplicarDadosSalvosRapidosPainelFace(bloco, dadosSalvos = null){
    if (!bloco || !dadosSalvos) return;

    const cidadeSalva = extrairCidadePainelFaceSalva(dadosSalvos);
    const tipoSalvo = extrairTipoPainelFaceSalvo(dadosSalvos);
    const idPainelSalvo = idNum(dadosSalvos?.IDDimPaineisEuromidia ?? dadosSalvos?.id_painel ?? 0) || null;
    const codPontoSalvo = safeStr(dadosSalvos?.CodPonto ?? dadosSalvos?.cod_ponto ?? '').trim();
    const codFaceSalva = safeStr(dadosSalvos?.CodFace ?? dadosSalvos?.cod_face ?? '').trim().toUpperCase();

    const selectCidade = bloco.querySelector('[data-role="select-filtro-cidade"]');
    const selectTipo = bloco.querySelector('[data-role="select-filtro-tipo"]');
    const selectPainel = bloco.querySelector('[data-role="select-painel"]');
    const selectFace = bloco.querySelector('[data-role="select-face"]');
    const inputBusca = bloco.querySelector('[data-role="input-painel-busca"]');

    if (cidadeSalva) selecionarValorSelectPreservandoOpcoes(selectCidade, cidadeSalva, cidadeSalva);
    if (tipoSalvo) selecionarValorSelectPreservandoOpcoes(selectTipo, tipoSalvo, tipoSalvo);

    if (idPainelSalvo) {
      const labelPainel = codPontoSalvo
        ? `${codPontoSalvo}${tipoSalvo ? ` • ${tipoSalvo}` : ''}${cidadeSalva ? ` • ${cidadeSalva}` : ''}`
        : String(idPainelSalvo);
      definirValorSelectOculto(selectPainel, String(idPainelSalvo), labelPainel);
    }

    if (codFaceSalva) {
      definirValorSelectOculto(selectFace, codFaceSalva, codFaceSalva);
      atualizarSelectFaceVisualDoBloco(bloco);
    }

    if (inputBusca && (codPontoSalvo || codFaceSalva || tipoSalvo || cidadeSalva)) {
      inputBusca.value = [codPontoSalvo, codFaceSalva, tipoSalvo, cidadeSalva].filter(Boolean).join(' • ');
    }

    const painelPreview = localizarPainelFaceCatalogo({
      id_painel: idPainelSalvo,
      cod_ponto: codPontoSalvo,
      cod_face: codFaceSalva,
      tipo_painel: tipoSalvo,
    }) || {
      IDDimPaineisEuromidia: idPainelSalvo,
      CodPonto: codPontoSalvo,
      CodFace: codFaceSalva,
      Tipo: tipoSalvo,
      Cidade: cidadeSalva,
      UF: safeStr(dadosSalvos?.UF ?? dadosSalvos?.uf ?? '').trim(),
      Logradouro: safeStr(dadosSalvos?.Logradouro ?? dadosSalvos?.logradouro ?? '').trim(),
      Bairro: safeStr(dadosSalvos?.Bairro ?? dadosSalvos?.bairro ?? '').trim(),
      Numero: safeStr(dadosSalvos?.Numero ?? dadosSalvos?.numero ?? '').trim(),
      QuantidadeFaces: idNum(dadosSalvos?.QuantidadeFaces ?? dadosSalvos?.quantidade_faces ?? 0) || null,
    };

    if (painelPreview && (painelPreview.CodPonto || painelPreview.CodFace || painelPreview.Tipo || painelPreview.Cidade)) {
      preencherPreviewPainel(bloco, painelPreview);
    }

    preencherSeletoresTabelaPrecoComDadosSalvos(bloco, dadosSalvos);
  }

  function sincronizarPainelFacesVisiveisAposCatalogo(){
    if (!painelFaceWrap?.classList.contains('is-on')) return;
    const blocos = Array.from(painelFaceLista?.querySelectorAll('.kb-painel-item') || []);
    if (!blocos.length) return;

    blocos.forEach((bloco) => {
      const dadosSalvos = bloco.__dadosPainelFaceOriginais || null;
      try {
        sincronizarFiltrosPainelFaceDoBloco(bloco, { manterSelecionadoInvalido: true });
        const painelSelecionado = obterPainelFaceSelecionadoDoBloco(bloco);
        if (!painelSelecionado && dadosSalvos) {
          aplicarDadosSalvosRapidosPainelFace(bloco, dadosSalvos);
          atualizarFacesDoBloco(bloco, dadosSalvos).catch((erro) => {
            console.warn('sincronizarPainelFacesVisiveisAposCatalogo: falhou ao reconciliar bloco', erro);
          });
        } else {
          sincronizarBuscaPainelComSelect(bloco);
          preencherSeletoresTabelaPrecoComDadosSalvos(bloco, dadosSalvos, { atualizarResumo: true });
        }
      } catch (erro) {
        console.warn('sincronizarPainelFacesVisiveisAposCatalogo: falhou', erro);
      }
    });
  }

  function definirValorSelectOculto(selectEl, valor, label = null){
    if (!selectEl) return;
    const valorTexto = safeStr(valor || '').trim();
    const placeholder = safeStr(selectEl.dataset.placeholder || '').trim() || '—';
    selectEl.innerHTML = '';
    selectEl.appendChild(el('option', { value:'' }, [placeholder]));
    if (valorTexto) {
      selectEl.appendChild(el('option', { value:valorTexto }, [safeStr(label || valorTexto)]));
      selectEl.value = valorTexto;
    } else {
      selectEl.value = '';
    }
  }

  function obterPainelFaceSelecionadoDoBloco(bloco){
    const selectPainel = bloco?.querySelector('[data-role="select-painel"]');
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const idPainel = idNum(selectPainel?.value || 0);
    const codFace = safeStr(selectFace?.value || '').trim().toUpperCase();
    if (!idPainel || !codFace) return null;
    return painelFacesPorChave.get(`${idPainel}|${codFace}`) || null;
  }

  function itemPainelFaceAtendeFiltros(item, filtros = {}, considerarTexto = true){
    if (!item) return false;

    const cidadeFiltro = safeStr(filtros?.cidade || '').trim();
    const tipoFiltro = safeStr(filtros?.tipo || '').trim();
    const termo = safeStr(filtros?.texto || '').trim();

    const cidadeItem = safeStr(item?.Cidade ?? '').trim();
    const tipoItem = safeStr(item?.Tipo ?? item?.TipoPainel ?? '').trim();

    if (cidadeFiltro && cidadeItem !== cidadeFiltro) return false;
    if (tipoFiltro && tipoItem !== tipoFiltro) return false;

    if (!considerarTexto || !termo) return true;

    const termoNormalizado = normalizarTexto(termo);
    const campos = [
      safeStr(item?.CodFace ?? ''),
      safeStr(item?.CodPonto ?? ''),
      safeStr(item?.Logradouro ?? ''),
      safeStr(item?.Cidade ?? ''),
      safeStr(item?.Bairro ?? ''),
      safeStr(item?.Tipo ?? item?.TipoPainel ?? ''),
      textoOpcaoPainelFace(item),
    ];

    return campos.some((campo) => normalizarTexto(campo).includes(termoNormalizado));
  }

  function listarCidadesPainelFaceDoBloco(bloco){
    const selectTipo = bloco?.querySelector('[data-role="select-filtro-tipo"]');
    const tipoSelecionado = safeStr(selectTipo?.value || '').trim();
    const conjunto = new Set();

    for (const item of (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : [])) {
      const tipoItem = safeStr(item?.Tipo ?? item?.TipoPainel ?? '').trim();
      if (tipoSelecionado && tipoItem !== tipoSelecionado) continue;
      const cidade = safeStr(item?.Cidade ?? '').trim();
      if (cidade) conjunto.add(cidade);
    }

    return Array.from(conjunto).sort((a, b) => a.localeCompare(b, 'pt-BR'));
  }

  function listarTiposPainelFaceDoBloco(bloco){
    const selectCidade = bloco?.querySelector('[data-role="select-filtro-cidade"]');
    const cidadeSelecionada = safeStr(selectCidade?.value || '').trim();
    const conjunto = new Set();

    for (const item of (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : [])) {
      const cidadeItem = safeStr(item?.Cidade ?? '').trim();
      if (cidadeSelecionada && cidadeItem !== cidadeSelecionada) continue;
      const tipo = safeStr(item?.Tipo ?? item?.TipoPainel ?? '').trim();
      if (tipo) conjunto.add(tipo);
    }

    return Array.from(conjunto).sort((a, b) => a.localeCompare(b, 'pt-BR'));
  }

  function sincronizarFiltrosPainelFaceDoBloco(bloco, opcoes = {}){
    const selectCidade = bloco?.querySelector('[data-role="select-filtro-cidade"]');
    const selectTipo = bloco?.querySelector('[data-role="select-filtro-tipo"]');
    if (!selectCidade || !selectTipo) return;

    const cidadeAtual = safeStr(selectCidade.value || '').trim();
    const tipoAtual = safeStr(selectTipo.value || '').trim();

    const cidades = listarCidadesPainelFaceDoBloco(bloco);
    preencherSelectFiltroPainelFace(selectCidade, cidades, '— Cidade —');
    if (cidadeAtual && cidades.includes(cidadeAtual)) {
      selectCidade.value = cidadeAtual;
    }

    const tipos = listarTiposPainelFaceDoBloco(bloco);
    preencherSelectFiltroPainelFace(selectTipo, tipos, '— Tipo Produto —');
    if (tipoAtual && tipos.includes(tipoAtual)) {
      selectTipo.value = tipoAtual;
    }

    const selecionado = obterPainelFaceSelecionadoDoBloco(bloco);
    if (!opcoes.manterSelecionadoInvalido && selecionado) {
      const filtrosAtuais = {
        cidade: safeStr(selectCidade.value || '').trim(),
        tipo: safeStr(selectTipo.value || '').trim(),
      };
      if (!itemPainelFaceAtendeFiltros(selecionado, filtrosAtuais, false)) {
        limparSelecaoPainelFaceDoBloco(bloco, { resetarFiltros: false, manterBusca: false, dispararChange: true });
        return;
      }
    }

    sincronizarBuscaPainelComSelect(bloco);
  }

  function filtrarPaineisCombobox(bloco, texto){
    const selectCidade = bloco?.querySelector('[data-role="select-filtro-cidade"]');
    const selectTipo = bloco?.querySelector('[data-role="select-filtro-tipo"]');

    const filtros = {
      cidade: safeStr(selectCidade?.value || '').trim(),
      tipo: safeStr(selectTipo?.value || '').trim(),
      texto: safeStr(texto || '').trim(),
    };

    return (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : [])
      .filter((item) => itemPainelFaceAtendeFiltros(item, filtros, true))
      .slice(0, LIMITE_PAINEL_FACES_COMBOBOX);
  }

  function montarSelectPaineisNoElemento(selectEl){
    if (!selectEl) return;
    definirValorSelectOculto(selectEl, '', null);
  }

  function abrirListaPaineisCombobox(bloco){
    const combo = bloco?.querySelector('[data-role="combo-painel"]');
    const lista = bloco?.querySelector('[data-role="lista-painel-busca"]');
    const input = bloco?.querySelector('[data-role="input-painel-busca"]');
    if (!combo || !lista) return;

    combo.classList.add('is-open');
    lista.hidden = false;
    renderizarListaPaineisCombobox(bloco, input?.value || '');
  }

  function fecharListaPaineisCombobox(bloco){
    const combo = bloco?.querySelector('[data-role="combo-painel"]');
    const lista = bloco?.querySelector('[data-role="lista-painel-busca"]');
    if (!combo || !lista) return;

    combo.classList.remove('is-open');
    lista.hidden = true;
  }

  function obterTextoPainelSelecionado(bloco){
    const item = obterPainelFaceSelecionadoDoBloco(bloco);
    return item ? textoOpcaoPainelFace(item) : '';
  }

  function sincronizarBuscaPainelComSelect(bloco){
    const input = bloco?.querySelector('[data-role="input-painel-busca"]');
    if (!input) return;
    input.value = obterTextoPainelSelecionado(bloco);
  }

  function obterChavePainelFaceSelecionadaDoBloco(bloco){
    const selectPainel = bloco?.querySelector('[data-role="select-painel"]');
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const idPainel = idNum(selectPainel?.value || 0);
    const codFace = safeStr(selectFace?.value || '').trim().toUpperCase();
    return idPainel && codFace ? `${idPainel}|${codFace}` : '';
  }

  function listarChavesPainelFacesSelecionadas(){
    const chaves = new Set();
    for (const blocoItem of (painelFaceLista?.querySelectorAll('.kb-painel-item') || [])) {
      const chave = obterChavePainelFaceSelecionadaDoBloco(blocoItem);
      if (chave) chaves.add(chave);
    }
    return chaves;
  }

  function encontrarBlocoPainelFacePorChave(chave){
    const chaveBusca = safeStr(chave || '').trim().toUpperCase();
    if (!chaveBusca) return null;

    for (const blocoItem of (painelFaceLista?.querySelectorAll('.kb-painel-item') || [])) {
      const chaveBloco = obterChavePainelFaceSelecionadaDoBloco(blocoItem).toUpperCase();
      if (chaveBloco === chaveBusca) return blocoItem;
    }

    return null;
  }

  function encontrarBlocoPainelFaceVazio(blocoPreferencial = null){
    if (blocoPreferencial && !obterChavePainelFaceSelecionadaDoBloco(blocoPreferencial)) {
      return blocoPreferencial;
    }

    for (const blocoItem of (painelFaceLista?.querySelectorAll('.kb-painel-item') || [])) {
      if (!obterChavePainelFaceSelecionadaDoBloco(blocoItem)) return blocoItem;
    }

    return null;
  }

  async function garantirBlocoPainelFaceSelecionado(blocoOrigem, item){
    if (!painelFaceLista || !item) return null;

    const chave = obterChavePainelFaceCatalogo(item);
    if (!chave) return null;

    const blocoExistente = encontrarBlocoPainelFacePorChave(chave);
    if (blocoExistente) return blocoExistente;

    let blocoDestino = encontrarBlocoPainelFaceVazio(blocoOrigem);

    if (!blocoDestino) {
      blocoDestino = criarPainelFaceItem();
      painelFaceLista.appendChild(blocoDestino);
      atualizarVisibilidadeContratoAditivoDoBloco(blocoDestino);
    }

    await selecionarPainelCombobox(blocoDestino, item, true);
    atualizarTitulosPainelFace();

    return blocoDestino;
  }

  function removerBlocoPainelFaceSelecionadoPorChave(chave){
    const blocoSelecionado = encontrarBlocoPainelFacePorChave(chave);
    if (!blocoSelecionado) return;

    const blocos = [...(painelFaceLista?.querySelectorAll('.kb-painel-item') || [])];

    if (blocos.length <= 1) {
      limparSelecaoPainelFaceDoBloco(blocoSelecionado, {
        resetarFiltros: false,
        manterBusca: false,
        dispararChange: true,
      });
    } else {
      destruirCalendariosReservaDoBloco(blocoSelecionado);
      blocoSelecionado.remove();
    }

    atualizarTitulosPainelFace();

    if (typeof agendarSincronizacaoFormularioSolicitacao === 'function') {
      agendarSincronizacaoFormularioSolicitacao();
    }
  }

  function bloquearReconciliacaoPainelFace(){
    timestampBloqueioReconciliacaoPainelFace = Date.now() + JANELA_BLOQUEIO_RECONCILIACAO_PAINEL_FACE_MS;
  }

  function reconciliacaoPainelFaceEstaBloqueada(){
    return Date.now() < Number(timestampBloqueioReconciliacaoPainelFace || 0);
  }

  async function alternarPainelFaceComboboxMultiplo(bloco, item, textoBusca = ''){
    const chave = obterChavePainelFaceCatalogo(item);
    if (!chave) return;

    bloquearReconciliacaoPainelFace();

    const combo = bloco?.querySelector('[data-role="combo-painel"]');
    const lista = bloco?.querySelector('[data-role="lista-painel-busca"]');
    const input = bloco?.querySelector('[data-role="input-painel-busca"]');
    const termoOriginal = safeStr(textoBusca ?? input?.value ?? '').trim();

    if (encontrarBlocoPainelFacePorChave(chave)) {
      removerBlocoPainelFaceSelecionadoPorChave(chave);
    } else {
      await garantirBlocoPainelFaceSelecionado(bloco, item);
    }

    if (combo && lista) {
      combo.classList.add('is-open');
      lista.hidden = false;
    }

    if (input) {
      input.value = termoOriginal;
      input.focus();
    }

    renderizarListaPaineisCombobox(bloco, termoOriginal);
  }

  function renderizarListaPaineisCombobox(bloco, texto){
    const lista = bloco?.querySelector('[data-role="lista-painel-busca"]');
    if (!lista) return;

    const filtrados = filtrarPaineisCombobox(bloco, texto);
    bloco.__paineisResultadoComboboxAtual = filtrados.slice();

    const chavesSelecionadas = listarChavesPainelFacesSelecionadas();
    lista.innerHTML = '';

    if (!filtrados.length) {
      lista.appendChild(el('div', { class:'kb-combobox-vazio' }, ['Nenhuma face encontrada para os filtros selecionados.']));
      return;
    }

    filtrados.forEach((item) => {
      const chave = obterChavePainelFaceCatalogo(item);
      if (!chave) return;

      const jaSelecionado = chavesSelecionadas.has(chave);
      const checkboxAttrs = {
        type:'checkbox',
        class:'kb-painel-multi-check kb-painel-face-opcao-check',
        tabindex:'-1',
        'aria-hidden':'true'
      };

      if (jaSelecionado) {
        checkboxAttrs.checked = 'checked';
      }

      const botao = el('button', {
        type:'button',
        class:`kb-combobox-opcao kb-painel-face-opcao${jaSelecionado ? ' is-selected' : ''}`,
        'data-painel-face-chave': chave
      }, [
        el('input', checkboxAttrs),
        el('div', { class:'kb-painel-face-opcao-texto' }, [
          el('strong', {}, [safeStr(item?.CodFace || '—')]),
          el('span', {}, [resumoPainelFace(item)])
        ])
      ]);

      botao.addEventListener('mousedown', async (evento) => {
        evento.preventDefault();
        evento.stopPropagation();
        bloquearReconciliacaoPainelFace();
        await alternarPainelFaceComboboxMultiplo(bloco, item, texto);
      });

      botao.addEventListener('click', (evento) => {
        evento.preventDefault();
        evento.stopPropagation();
        bloquearReconciliacaoPainelFace();
      });

      lista.appendChild(botao);
    });
  }

  function limparSelecaoPainelFaceDoBloco(bloco, opcoes = {}){
    const selectPainel = bloco?.querySelector('[data-role="select-painel"]');
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const input = bloco?.querySelector('[data-role="input-painel-busca"]');
    const selectCidade = bloco?.querySelector('[data-role="select-filtro-cidade"]');
    const selectTipo = bloco?.querySelector('[data-role="select-filtro-tipo"]');
    const resetarFiltros = !!opcoes.resetarFiltros;
    const manterBusca = !!opcoes.manterBusca;
    const dispararChange = !!opcoes.dispararChange;

    definirValorSelectOculto(selectPainel, '', null);
    definirValorSelectOculto(selectFace, '', null);
    atualizarSelectFaceVisualDoBloco(bloco);

    if (resetarFiltros) {
      if (selectCidade) selectCidade.value = '';
      if (selectTipo) selectTipo.value = '';
      sincronizarFiltrosPainelFaceDoBloco(bloco, { manterSelecionadoInvalido: true });
    }

    if (input && !manterBusca) input.value = '';
    fecharListaPaineisCombobox(bloco);
    preencherPreviewPainel(bloco, null);
    limparComercialBloco(bloco);

    if (dispararChange && selectFace) {
      selectFace.dispatchEvent(new Event('change', { bubbles: true }));
    }

    agendarSincronizacaoFormularioSolicitacao();
  }

  async function selecionarPainelCombobox(bloco, itemOuChave, dispararChange = true){
    const selectPainel = bloco?.querySelector('[data-role="select-painel"]');
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const selectCidade = bloco?.querySelector('[data-role="select-filtro-cidade"]');
    const selectTipo = bloco?.querySelector('[data-role="select-filtro-tipo"]');
    const input = bloco?.querySelector('[data-role="input-painel-busca"]');
    if (!selectPainel || !selectFace) return;

    const item = typeof itemOuChave === 'string'
      ? (painelFacesPorChave.get(itemOuChave) || null)
      : (itemOuChave || null);

    const valorAnterior = `${idNum(selectPainel.value || 0)}|${safeStr(selectFace.value || '').trim().toUpperCase()}`;

    if (!item) {
      limparSelecaoPainelFaceDoBloco(bloco, { resetarFiltros: false, manterBusca: false, dispararChange });
      return;
    }

    const idPainel = idNum(item?.IDDimPaineisEuromidia ?? item?.id_painel ?? 0);
    const codFace = safeStr(item?.CodFace ?? item?.cod_face ?? '').trim().toUpperCase();
    if (!idPainel || !codFace) {
      limparSelecaoPainelFaceDoBloco(bloco, { resetarFiltros: false, manterBusca: false, dispararChange });
      return;
    }

    if (selectCidade) selectCidade.value = safeStr(item?.Cidade ?? '').trim();
    if (selectTipo) selectTipo.value = safeStr(item?.Tipo ?? item?.TipoPainel ?? '').trim();
    sincronizarFiltrosPainelFaceDoBloco(bloco, { manterSelecionadoInvalido: true });

    definirValorSelectOculto(selectPainel, String(idPainel), textoOpcaoPainel(item));
    definirValorSelectOculto(selectFace, codFace, codFace);
    atualizarSelectFaceVisualDoBloco(bloco);

    if (input) {
      input.value = textoOpcaoPainelFace(item);
    }

    preencherPreviewPainel(bloco, item);
    fecharListaPaineisCombobox(bloco);

    const valorNovo = `${idPainel}|${codFace}`;
    if (dispararChange && valorNovo !== valorAnterior) {
      selectFace.dispatchEvent(new Event('change', { bubbles: true }));
    }

    agendarSincronizacaoFormularioSolicitacao();
  }

  function localizarPainelFacePorTextoDigitado(bloco, texto){
    const bruto = safeStr(texto || '').trim();
    if (!bruto) return null;

    const textoNormalizado = normalizarTexto(bruto);
    const listaAtual = Array.isArray(bloco?.__paineisResultadoComboboxAtual) ? bloco.__paineisResultadoComboboxAtual : [];

    return listaAtual.find((item) => normalizarTexto(textoOpcaoPainelFace(item)) === textoNormalizado) || null;
  }

  function reconciliarBuscaPainelDigitada(bloco){
    const input = bloco?.querySelector('[data-role="input-painel-busca"]');
    if (!input) return;

    if (reconciliacaoPainelFaceEstaBloqueada()) {
      return;
    }

    const textoDigitado = safeStr(input.value || '').trim();

    if (!textoDigitado) {
      if (obterChavePainelFaceSelecionadaDoBloco(bloco)) {
        sincronizarBuscaPainelComSelect(bloco);
      }
      fecharListaPaineisCombobox(bloco);
      return;
    }

    const painelFace = localizarPainelFacePorTextoDigitado(bloco, textoDigitado);
    if (painelFace) {
      selecionarPainelCombobox(bloco, painelFace, true).catch((erro) => {
        console.warn('reconciliarBuscaPainelDigitada: falhou ao selecionar face', erro);
      });
      return;
    }

    sincronizarBuscaPainelComSelect(bloco);
    fecharListaPaineisCombobox(bloco);
  }

  function limparSelectFacesNoElemento(selectEl){
    if (!selectEl) return;
    definirValorSelectOculto(selectEl, '', null);
  }

  function limparSelectPrecosNoElemento(selectEl){
    if (!selectEl) return;
    selectEl.innerHTML = "";
    selectEl.appendChild(el("option", { value:"" }, ["— Preço automático —"]));
  }

  function montarSelectFacesNoElemento(selectEl, faces){
    limparSelectFacesNoElemento(selectEl);
    (Array.isArray(faces) ? faces : []).forEach(f => {
      const codFace = safeStr(f?.CodFace ?? f?.codFace ?? "").trim().toUpperCase();
      if (!codFace) return;
      const desc = safeStr(f?.Face ?? f?.Label ?? "").trim();
      const label = desc ? `${codFace} • ${desc}` : codFace;
      selectEl.appendChild(el("option", { value: codFace }, [label]));
    });
  }

  function atualizarSelectFaceVisualDoBloco(bloco, opcoes = {}){
    const wrapSelectFace = bloco?.querySelector('[data-role="wrap-select-face"]');
    if (wrapSelectFace) {
      wrapSelectFace.hidden = true;
      wrapSelectFace.style.display = 'none';
    }
  }

  function montarSelectPrecosNoElemento(selectEl, precos){
    limparSelectPrecosNoElemento(selectEl);
    (Array.isArray(precos) ? precos : []).forEach(p => {
      const idPreco = idNum(p?.IDDimTabelaPrecosEuromidia);
      if (!idPreco) return;
      const label = [
        safeStr(p?.PeriodoExibicao || "—"),
        (p?.ExibicoesDia ?? "—"),
        formatarMoedaBR(p?.Valor)
      ].join(" | " );
      selectEl.appendChild(el("option", { value:String(idPreco) }, [label]));
    });
  }

  function obterPrecosComerciaisDoBloco(bloco){
    return Array.isArray(bloco?.__dadosComerciais?.precos) ? bloco.__dadosComerciais.precos.slice() : [];
  }

  function obterTipoPainelComercialDoBloco(bloco){
    return safeStr(
      bloco?.__dadosComerciais?.painel?.Tipo ??
      bloco?.__dadosComerciais?.face?.Tipo ??
      obterPainelFaceSelecionadoDoBloco(bloco)?.Tipo ??
      ''
    ).trim();
  }

  function painelFaceUsaInsercoesDigitais(bloco){
    const precos = obterPrecosComerciaisDoBloco(bloco);
    if (precos.some((preco) => preco?.ExibicoesDia !== null && preco?.ExibicoesDia !== undefined && safeStr(String(preco?.ExibicoesDia)).trim() !== '')) {
      return true;
    }

    const tipoNormalizado = normalizarTextoComparacaoPainelContrato(obterTipoPainelComercialDoBloco(bloco));
    return tipoNormalizado === 'painel digital';
  }

  function chaveExibicoesDiaDoPreco(preco, usarInsercoesDigitais = false){
    if (!usarInsercoesDigitais){
      return 'FULL';
    }

    const exibicoes = safeStr(preco?.ExibicoesDia ?? '').trim();
    return exibicoes || 'FULL';
  }

  function labelExibicoesDiaDoPreco(chave){
    return safeStr(chave || '').trim().toUpperCase() === 'FULL' ? 'Full' : safeStr(chave || '').trim();
  }

  function obterValoresSelecionadosSelect(selectEl){
    if (!selectEl) return [];

    if (selectEl.multiple) {
      return Array.from(selectEl.selectedOptions || [])
        .map((option) => safeStr(option?.value || '').trim())
        .filter(Boolean);
    }

    const valor = safeStr(selectEl.value || '').trim();
    return valor ? [valor] : [];
  }

  function obterDropdownExibicoesDia(selectEl){
    const wrap = selectEl?.closest?.('[data-role="wrap-exibicoes-dia"]');
    return wrap?.querySelector?.('[data-role="dd-exibicoes-dia"]') || null;
  }

  function obterBlocoDoSelectExibicoesDia(selectEl){
    return selectEl?.closest?.('.kb-painel-item') || null;
  }

  function normalizarListaExibicoesDia(valores){
    const lista = Array.isArray(valores) ? valores : [valores];
    return Array.from(new Set(
      lista
        .flatMap((valor) => safeStr(valor || '').split(','))
        .map((valor) => safeStr(valor || '').trim())
        .filter(Boolean)
    ));
  }

  function obterGrupoVisualExibicoesDiaDoBloco(bloco){
    const textoGrupo = safeStr(bloco?.dataset?.exibicoesDiaGrupo || '').trim();
    return normalizarListaExibicoesDia(textoGrupo);
  }

  function obterExibicaoDiaAtivaDoBloco(bloco){
    return safeStr(bloco?.dataset?.exibicaoDiaAtiva || '').trim();
  }

  function aplicarGrupoVisualExibicoesDiaNoBloco(bloco, valoresGrupo, exibicaoAtiva){
    if (!bloco) return;

    /*
     * Importante: quando o usuário seleciona mais de uma inserção/dia,
     * a tela divide em blocos separados. Depois da divisão, cada bloco
     * deve mostrar somente a própria inserção ativa, e não o grupo completo
     * "540, 1080" no resumo visual.
     */
    delete bloco.dataset.exibicoesDiaGrupo;

    const ativa = safeStr(exibicaoAtiva || '').trim();
    if (ativa) {
      bloco.dataset.exibicaoDiaAtiva = ativa;
    } else {
      delete bloco.dataset.exibicaoDiaAtiva;
    }

    const selectExibicoesDia = bloco.querySelector('[data-role="select-exibicoes-dia"]');
    if (selectExibicoesDia) {
      atualizarResumoDropdownExibicoesDia(selectExibicoesDia);
    }
  }

  function limparGrupoVisualExibicoesDiaDoSelect(selectEl){
    const bloco = obterBlocoDoSelectExibicoesDia(selectEl);
    if (!bloco) return;
    delete bloco.dataset.exibicoesDiaGrupo;
    delete bloco.dataset.exibicaoDiaAtiva;
  }

  function obterValoresVisuaisDropdownExibicoesDia(selectEl){
    const bloco = obterBlocoDoSelectExibicoesDia(selectEl);
    const ativa = obterExibicaoDiaAtivaDoBloco(bloco);
    if (ativa) return [ativa];
    return obterValoresSelecionadosSelect(selectEl);
  }

  function listarOpcoesUnicasSelectExibicoesDia(selectEl){
    const mapa = new Map();

    Array.from(selectEl?.options || []).forEach((option) => {
      const valor = safeStr(option?.value || '').trim();
      if (!valor || option.disabled || mapa.has(valor)) return;

      const label = safeStr(option?.textContent || option?.value || '').trim() || valor;
      mapa.set(valor, { valor, label });
    });

    return Array.from(mapa.values());
  }

  function atualizarResumoDropdownExibicoesDia(selectEl){
    const dd = obterDropdownExibicoesDia(selectEl);
    const resumo = dd?.querySelector('[data-role="exibicoes-dia-resumo"]');
    if (!resumo) return;

    const opcoes = listarOpcoesUnicasSelectExibicoesDia(selectEl);
    const valoresVisuais = obterValoresVisuaisDropdownExibicoesDia(selectEl);
    const valores = new Set(valoresVisuais);
    const labelsSelecionadas = opcoes
      .filter((opcao) => valores.has(opcao.valor))
      .map((opcao) => opcao.label)
      .filter(Boolean);

    const labelsFinais = labelsSelecionadas.length
      ? labelsSelecionadas
      : valoresVisuais.filter(Boolean);

    if (!labelsFinais.length) {
      resumo.textContent = '— Inserções / dia —';
      resumo.title = '';
      return;
    }

    const texto = labelsFinais.join(', ');
    resumo.textContent = texto;
    resumo.title = texto;
  }

  function atualizarDropdownExibicoesDia(selectEl){
    const dd = obterDropdownExibicoesDia(selectEl);
    if (!selectEl || !dd) return;

    const lista = dd.querySelector('[data-role="exibicoes-dia-lista"]');
    const busca = dd.querySelector('[data-role="exibicoes-dia-busca"]');
    if (!lista) return;

    const termoBusca = normalizarTexto(busca?.value || '');
    const opcoes = listarOpcoesUnicasSelectExibicoesDia(selectEl);
    const selecionados = new Set(obterValoresVisuaisDropdownExibicoesDia(selectEl));

    lista.innerHTML = '';

    if (!opcoes.length) {
      lista.appendChild(el('div', { class:'dd-vazio' }, ['Nenhuma inserção disponível.']));
      atualizarResumoDropdownExibicoesDia(selectEl);
      return;
    }

    const valoresOpcoes = opcoes.map((opcao) => opcao.valor);
    const todosMarcados = valoresOpcoes.length > 0 && valoresOpcoes.every((valor) => selecionados.has(valor));

    const checkboxTodos = el('input', {
      type:'checkbox',
      'data-role':'exibicoes-dia-todos'
    });
    checkboxTodos.checked = todosMarcados;

    const labelTodos = el('label', { class:'dd-item dd-item-todos' }, [
      checkboxTodos,
      el('span', {}, ['(Todas)'])
    ]);
    lista.appendChild(labelTodos);

    checkboxTodos.addEventListener('change', (evento) => {
      const marcado = Boolean(evento.currentTarget?.checked);
      const valores = marcado ? valoresOpcoes : [valoresOpcoes[0]].filter(Boolean);
      limparGrupoVisualExibicoesDiaDoSelect(selectEl);
      selecionarValoresNoSelect(selectEl, valores, { sincronizarDropdown: false });
      selectEl.dispatchEvent(new Event('change', { bubbles: true }));
    });

    let totalVisiveis = 0;

    opcoes.forEach((opcao) => {
      const textoFiltro = normalizarTexto(`${opcao.label} ${opcao.valor}`);
      if (termoBusca && !textoFiltro.includes(termoBusca)) return;

      totalVisiveis += 1;
      const checkbox = el('input', {
        type:'checkbox',
        value: opcao.valor,
        'data-role':'exibicoes-dia-opcao'
      });
      checkbox.checked = selecionados.has(opcao.valor);

      const item = el('label', { class:'dd-item' }, [
        checkbox,
        el('span', {}, [opcao.label])
      ]);

      checkbox.addEventListener('change', (evento) => {
        const atuais = new Set(obterValoresVisuaisDropdownExibicoesDia(selectEl));
        const valor = safeStr(evento.currentTarget?.value || '').trim();
        if (!valor) return;

        if (evento.currentTarget.checked) {
          atuais.add(valor);
        } else {
          atuais.delete(valor);
        }

        if (!atuais.size) {
          atuais.add(valor);
          evento.currentTarget.checked = true;
        }

        limparGrupoVisualExibicoesDiaDoSelect(selectEl);
        selecionarValoresNoSelect(selectEl, Array.from(atuais), { sincronizarDropdown: false });
        selectEl.dispatchEvent(new Event('change', { bubbles: true }));
      });

      lista.appendChild(item);
    });

    if (!totalVisiveis) {
      lista.appendChild(el('div', { class:'dd-vazio' }, ['Nenhuma inserção encontrada.']));
    }

    if (busca && busca.dataset.listenerExibicoesDia !== '1') {
      busca.addEventListener('input', () => atualizarDropdownExibicoesDia(selectEl));
      busca.dataset.listenerExibicoesDia = '1';
    }

    atualizarResumoDropdownExibicoesDia(selectEl);
  }

  function selecionarValoresNoSelect(selectEl, valores, opcoes = {}){
    if (!selectEl) return;

    const selecionados = new Set(
      (Array.isArray(valores) ? valores : [valores])
        .map((valor) => safeStr(valor || '').trim())
        .filter(Boolean)
    );

    if (selectEl.multiple) {
      Array.from(selectEl.options || []).forEach((option) => {
        option.selected = selecionados.has(safeStr(option.value || '').trim());
      });
      if (opcoes?.sincronizarDropdown !== false) {
        atualizarDropdownExibicoesDia(selectEl);
      }
      return;
    }

    const primeiro = Array.from(selecionados)[0] || '';
    selectEl.value = primeiro;
    if (opcoes?.sincronizarDropdown !== false) {
      atualizarDropdownExibicoesDia(selectEl);
    }
  }

  function preencherSelectOpcoesSimples(selectEl, opcoes, placeholder){
    if (!selectEl) return;

    const valoresAtuais = obterValoresSelecionadosSelect(selectEl);
    const opcoesLista = Array.isArray(opcoes) ? opcoes : [];
    const valoresValidos = new Set(
      opcoesLista
        .map((opcao) => safeStr(opcao?.valor ?? '').trim())
        .filter(Boolean)
    );

    selectEl.innerHTML = '';
    if (placeholder) {
      const attrsPlaceholder = { value:'' };
      if (selectEl.multiple) {
        attrsPlaceholder.disabled = 'disabled';
      }
      selectEl.appendChild(el('option', attrsPlaceholder, [placeholder]));
    }

    const opcoesUnicas = new Map();
    opcoesLista.forEach((opcao) => {
      const valor = safeStr(opcao?.valor ?? '').trim();
      if (!valor || opcoesUnicas.has(valor)) return;

      const label = safeStr(opcao?.label ?? valor).trim() || valor;
      opcoesUnicas.set(valor, { valor, label });
    });

    opcoesUnicas.forEach((opcao) => {
      selectEl.appendChild(el('option', { value: opcao.valor }, [opcao.label]));
    });

    const valoresParaRestaurar = Array.from(new Set(valoresAtuais.filter((valor) => valoresValidos.has(valor))));
    selecionarValoresNoSelect(selectEl, valoresParaRestaurar);
    atualizarDropdownExibicoesDia(selectEl);
  }

  function listarOpcoesExibicoesDiaDoBloco(bloco, periodoFiltrado = ''){
    const usarInsercoesDigitais = painelFaceUsaInsercoesDigitais(bloco);
    const periodoNormalizado = safeStr(periodoFiltrado || '').trim();
    const mapa = new Map();

    for (const preco of obterPrecosComerciaisDoBloco(bloco)) {
      const periodoPreco = safeStr(preco?.PeriodoExibicao || '').trim();
      if (periodoNormalizado && periodoPreco !== periodoNormalizado) continue;

      const chave = chaveExibicoesDiaDoPreco(preco, usarInsercoesDigitais);
      if (!chave || mapa.has(chave)) continue;
      mapa.set(chave, { valor: chave, label: labelExibicoesDiaDoPreco(chave) });
    }

    return Array.from(mapa.values()).sort((a, b) => a.label.localeCompare(b.label, 'pt-BR', { numeric: true }));
  }

  function listarOpcoesPeriodoDoBloco(bloco, exibicoesFiltradas = ''){
    const usarInsercoesDigitais = painelFaceUsaInsercoesDigitais(bloco);
    const exibicoesNormalizadas = safeStr(exibicoesFiltradas || '').trim();
    const mapa = new Map();

    for (const preco of obterPrecosComerciaisDoBloco(bloco)) {
      const chaveExibicoes = chaveExibicoesDiaDoPreco(preco, usarInsercoesDigitais);
      if (exibicoesNormalizadas && chaveExibicoes !== exibicoesNormalizadas) continue;

      const periodo = safeStr(preco?.PeriodoExibicao || '').trim();
      if (!periodo || mapa.has(periodo)) continue;
      mapa.set(periodo, { valor: periodo, label: periodo });
    }

    return Array.from(mapa.values()).sort((a, b) => a.label.localeCompare(b.label, 'pt-BR'));
  }

  function listarOpcoesPeriodoPorExibicoesDoBloco(bloco, exibicoesSelecionadas = []){
    const selecionadas = new Set(
      (Array.isArray(exibicoesSelecionadas) ? exibicoesSelecionadas : [exibicoesSelecionadas])
        .map((valor) => safeStr(valor || '').trim())
        .filter(Boolean)
    );

    if (selecionadas.size <= 1) {
      return listarOpcoesPeriodoDoBloco(bloco, Array.from(selecionadas)[0] || '');
    }

    const usarInsercoesDigitais = painelFaceUsaInsercoesDigitais(bloco);
    const mapa = new Map();

    for (const preco of obterPrecosComerciaisDoBloco(bloco)) {
      const chaveExibicoes = chaveExibicoesDiaDoPreco(preco, usarInsercoesDigitais);
      if (selecionadas.size && !selecionadas.has(chaveExibicoes)) continue;

      const periodo = safeStr(preco?.PeriodoExibicao || '').trim();
      if (!periodo || mapa.has(periodo)) continue;
      mapa.set(periodo, { valor: periodo, label: periodo });
    }

    return Array.from(mapa.values()).sort((a, b) => a.label.localeCompare(b.label, 'pt-BR'));
  }

  function obterIdFaceAtualDoBloco(bloco){
    const painelFace = obterPainelFaceSelecionadoDoBloco(bloco) || null;
    return idNum(
      painelFace?.IDDimFacesPaineis ??
      bloco?.__dadosComerciais?.face?.IDDimFacesPaineis ??
      0
    ) || null;
  }

  function obterValorPrecoTabela(preco){
    const valor = Number(
      preco?.Valor ??
      preco?.valor ??
      preco?.ValorTabela ??
      preco?.valor_tabela ??
      null
    );
    return Number.isFinite(valor) ? valor : null;
  }

  function ordenarPrecosPorPrioridadeDoBloco(bloco, precos){
    const idFaceAtual = obterIdFaceAtualDoBloco(bloco);

    return (Array.isArray(precos) ? precos.slice() : []).sort((a, b) => {
      const idFaceA = idNum(a?.IDDimFacesPaineis ?? a?.id_face ?? 0) || null;
      const idFaceB = idNum(b?.IDDimFacesPaineis ?? b?.id_face ?? 0) || null;

      const prioridadeFaceA = idFaceAtual && idFaceA === idFaceAtual ? 0 : (idFaceA ? 1 : 2);
      const prioridadeFaceB = idFaceAtual && idFaceB === idFaceAtual ? 0 : (idFaceB ? 1 : 2);
      if (prioridadeFaceA !== prioridadeFaceB) return prioridadeFaceA - prioridadeFaceB;

      const ativoA = idNum(a?.BitAtivo ?? 0) === 1 ? 0 : 1;
      const ativoB = idNum(b?.BitAtivo ?? 0) === 1 ? 0 : 1;
      if (ativoA !== ativoB) return ativoA - ativoB;

      const dataA = new Date(a?.DataPublicacao || a?.DataAtualizacao || 0).getTime() || 0;
      const dataB = new Date(b?.DataPublicacao || b?.DataAtualizacao || 0).getTime() || 0;
      if (dataA !== dataB) return dataB - dataA;

      return (idNum(b?.IDDimTabelaPrecosEuromidia || 0) || 0) - (idNum(a?.IDDimTabelaPrecosEuromidia || 0) || 0);
    });
  }

  function localizarPrecoPorFiltrosDoBloco(bloco, filtros = {}){
    const precos = obterPrecosComerciaisDoBloco(bloco);
    const usarInsercoesDigitais = painelFaceUsaInsercoesDigitais(bloco);
    const idPreferido = idNum(filtros?.id_preco || 0) || null;
    const exibicoesDesejadas = safeStr(filtros?.exibicoes_dia || '').trim();
    const periodoDesejado = safeStr(filtros?.periodo_exibicao || '').trim();

    const candidatos = precos.filter((preco) => {
      const chaveExibicoes = chaveExibicoesDiaDoPreco(preco, usarInsercoesDigitais);
      const periodo = safeStr(preco?.PeriodoExibicao || '').trim();
      if (exibicoesDesejadas && chaveExibicoes !== exibicoesDesejadas) return false;
      if (periodoDesejado && periodo !== periodoDesejado) return false;
      return true;
    });

    const ordenados = ordenarPrecosPorPrioridadeDoBloco(bloco, candidatos);

    if (idPreferido) {
      const encontrado = ordenados.find((preco) => idNum(preco?.IDDimTabelaPrecosEuromidia) === idPreferido);
      if (encontrado) return encontrado;
    }

    return ordenados[0] || null;
  }

  function ocultarCamposTabelaPrecoDoBloco(bloco){
    const wrapExibicoes = bloco?.querySelector('[data-role="wrap-exibicoes-dia"]');
    const wrapPeriodo = bloco?.querySelector('[data-role="wrap-periodo-exibicao"]');
    const selectExibicoes = bloco?.querySelector('[data-role="select-exibicoes-dia"]');
    const selectPeriodo = bloco?.querySelector('[data-role="select-periodo-exibicao"]');

    if (wrapExibicoes) wrapExibicoes.hidden = true;
    if (wrapPeriodo) wrapPeriodo.hidden = true;
    if (selectExibicoes) preencherSelectOpcoesSimples(selectExibicoes, [], '— Inserções / dia —');
    if (selectPeriodo) preencherSelectOpcoesSimples(selectPeriodo, [], '— Período de campanha —');
  }

  function sincronizarSeletoresTabelaPrecoDoBloco(bloco, opcoes = {}){
    const selectPreco = bloco?.querySelector('[data-role="select-preco"]');
    const selectExibicoes = bloco?.querySelector('[data-role="select-exibicoes-dia"]');
    const selectPeriodo = bloco?.querySelector('[data-role="select-periodo-exibicao"]');
    const wrapExibicoes = bloco?.querySelector('[data-role="wrap-exibicoes-dia"]');
    const wrapPeriodo = bloco?.querySelector('[data-role="wrap-periodo-exibicao"]');

    if (!selectPreco || !selectExibicoes || !selectPeriodo) return;

    const precos = obterPrecosComerciaisDoBloco(bloco);
    if (!precos.length) {
      limparSelectPrecosNoElemento(selectPreco);
      ocultarCamposTabelaPrecoDoBloco(bloco);
      return;
    }

    const usarInsercoesDigitais = painelFaceUsaInsercoesDigitais(bloco);
    const idPrecoAtual = idNum(selectPreco.value || 0) || null;
    const idPrecoPreferido = idNum(opcoes?.id_preco || 0) || idPrecoAtual || null;
    const precoPreferido = idPrecoPreferido
      ? precos.find((preco) => idNum(preco?.IDDimTabelaPrecosEuromidia) === idPrecoPreferido) || null
      : null;

    const opcoesExibicoes = listarOpcoesExibicoesDiaDoBloco(bloco, '');
    const valoresValidosExibicoes = new Set(
      opcoesExibicoes
        .map((opcao) => safeStr(opcao?.valor || '').trim())
        .filter(Boolean)
    );

    const exibicaoAtiva = obterExibicaoDiaAtivaDoBloco(bloco);
    let exibicoesSelecionadas = exibicaoAtiva && valoresValidosExibicoes.has(exibicaoAtiva)
      ? [exibicaoAtiva]
      : obterValoresSelecionadosSelect(selectExibicoes)
          .filter((valor) => valoresValidosExibicoes.has(valor));

    let periodoDesejado = safeStr(selectPeriodo.value || '').trim();

    /*
     * Regra visual correta:
     * - ao carregar um painel/face novo, NÃO escolhe automaticamente 540, 1080, Full
     *   nem o primeiro período disponível;
     * - só restaura valores automaticamente quando existe preço salvo/preferido,
     *   por exemplo ao abrir um card já preenchido ou um item recém-dividido.
     */
    if (!exibicoesSelecionadas.length && precoPreferido) {
      const exibicoesPrecoPreferido = chaveExibicoesDiaDoPreco(precoPreferido, usarInsercoesDigitais);
      if (valoresValidosExibicoes.has(exibicoesPrecoPreferido)) {
        exibicoesSelecionadas = [exibicoesPrecoPreferido];
      }
    }

    if (!periodoDesejado && precoPreferido) {
      periodoDesejado = safeStr(precoPreferido?.PeriodoExibicao || '').trim();
    }

    let opcoesPeriodo = exibicoesSelecionadas.length
      ? listarOpcoesPeriodoPorExibicoesDoBloco(bloco, exibicoesSelecionadas)
      : listarOpcoesPeriodoDoBloco(bloco, '');

    if (!opcoesPeriodo.length) {
      opcoesPeriodo = listarOpcoesPeriodoDoBloco(bloco, '');
    }

    if (periodoDesejado && !opcoesPeriodo.some((opcao) => opcao.valor === periodoDesejado)) {
      periodoDesejado = '';
    }

    preencherSelectOpcoesSimples(selectExibicoes, opcoesExibicoes, '— Inserções / dia —');
    selecionarValoresNoSelect(selectExibicoes, exibicoesSelecionadas);

    preencherSelectOpcoesSimples(selectPeriodo, opcoesPeriodo, '— Período de campanha —');
    selectPeriodo.value = periodoDesejado || '';

    const exibicaoReferencia = exibicoesSelecionadas[0] || '';
    const exigeExibicoes = usarInsercoesDigitais && opcoesExibicoes.length > 0;
    const selecaoCompleta = Boolean(periodoDesejado) && (!exigeExibicoes || Boolean(exibicaoReferencia));

    const precoSelecionado = selecaoCompleta
      ? (
          localizarPrecoPorFiltrosDoBloco(bloco, {
            id_preco: idPrecoPreferido,
            exibicoes_dia: exibicaoReferencia,
            periodo_exibicao: periodoDesejado,
          }) || localizarPrecoPorFiltrosDoBloco(bloco, {
            exibicoes_dia: exibicaoReferencia,
            periodo_exibicao: periodoDesejado,
          }) || null
        )
      : null;

    if (precoSelecionado) {
      const exibicoesFinais = chaveExibicoesDiaDoPreco(precoSelecionado, usarInsercoesDigitais);
      const labelPreco = `${safeStr(precoSelecionado?.PeriodoExibicao || '—')} | ${labelExibicoesDiaDoPreco(exibicoesFinais)} | ${formatarMoedaBR(obterValorPrecoTabela(precoSelecionado))}`;

      definirValorSelectOculto(
        selectPreco,
        String(idNum(precoSelecionado?.IDDimTabelaPrecosEuromidia || 0) || ''),
        labelPreco
      );
    } else {
      limparSelectPrecosNoElemento(selectPreco);
    }

    if (wrapExibicoes) wrapExibicoes.hidden = false;
    if (wrapPeriodo) wrapPeriodo.hidden = false;
  }

  function preencherPreviewPainel(bloco, painel){
    const preview = bloco.querySelector('[data-role="painel-preview"]');
    if (!preview) return;

    const tem = !!(painel && (painel.CodPonto || painel.Tipo || painel.Cidade || painel.UF || painel.Logradouro));
    bloco.classList.toggle('has-painel', tem);
    preview.style.display = tem ? 'grid' : 'none';

    const setTxt = (role, valor) => {
      const alvo = bloco.querySelector(`[data-role="${role}"]`);
      if (alvo) alvo.textContent = valor;
    };

    if (!tem){
      setTxt('pn-codponto', '—');
      setTxt('pn-tipo', '—');
      setTxt('pn-cidadeuf', '—');
      setTxt('pn-endereco', '—');
      setTxt('pn-faces', '—');
      return;
    }

    setTxt('pn-codponto', String(painel.CodPonto || '—'));
    setTxt('pn-tipo', String(painel.Tipo || '—'));
    setTxt('pn-cidadeuf', `${painel.Cidade || '—'}/${painel.UF || '—'}`);
    setTxt('pn-endereco', `${painel.Logradouro || ''}${painel.Numero ? (', ' + painel.Numero) : ''}${painel.Bairro ? (' - ' + painel.Bairro) : ''}`.trim() || '—');
    setTxt('pn-faces', Number(painel.QuantidadeFaces || 0) ? String(painel.QuantidadeFaces) : '—');
  }

  function limparComercialBloco(bloco, mensagem = 'Selecione Cidade, Tipo Produto e CodFace para consultar custo e preços.', opcoes = {}){
    const wrap = bloco.querySelector('[data-role="comercial-wrap"]');
    const selectPreco = bloco.querySelector('[data-role="select-preco"]');
    const preservarSeletoresTabelaPreco = opcoes?.preservarSeletoresTabelaPreco === true;

    destruirCalendariosReservaDoBloco(bloco);

    if (wrap) wrap.innerHTML = `<div class="kb-comercial-vazio">${mensagem}</div>`;
    if (!preservarSeletoresTabelaPreco) {
      if (selectPreco) limparSelectPrecosNoElemento(selectPreco);
      ocultarCamposTabelaPrecoDoBloco(bloco);
    } else {
      preencherSeletoresTabelaPrecoComDadosSalvos(bloco, bloco.__dadosPainelFaceOriginais || null);
    }

    bloco.__dadosComerciais = null;
    bloco.__calendarioOcupacao = null;
    bloco.__calendarioOcupacaoErro = '';
    bloco.__datasReservaLiberadasManual = new Set();
  }

  async function carregarFacesDoPainel(idPainel){
    const id = idNum(idPainel);
    if (!id) return [];
    if (facesPorPainelId.has(id)) return facesPorPainelId.get(id) || [];

    facesCarregando = true;
    try{
      const arr = (Array.isArray(painelFacesCatalogo) ? painelFacesCatalogo : []).filter((item) => idNum(item?.IDDimPaineisEuromidia ?? 0) === id);
      facesPorPainelId.set(id, arr);
      return arr;
    } finally {
      facesCarregando = false;
    }
  }

  async function carregarComercialPainelFace(idPainel, codFace){
    const id = idNum(idPainel);
    const cod = encodeURIComponent(safeStr(codFace).trim());
    if (!id || !cod) return null;

    const chave = `${id}|${decodeURIComponent(cod)}`;
    if (comercialPorPainelFace.has(chave)) return comercialPorPainelFace.get(chave) || null;

    const r = await fetch(`/kanban/api/paineis/id/${id}/faces/${cod}/comercial`, { credentials: 'same-origin' });
    const j = await r.json().catch(() => null);
    if (!r.ok || !j || !j.ok){
      console.warn('carregarComercialPainelFace: falhou', { idPainel: id, codFace, http: r.status, body: j });
      return null;
    }

    comercialPorPainelFace.set(chave, j);
    return j;
  }

  async function atualizarFacesDoBloco(bloco, dadosSalvos = null){
    const selectPainel = bloco.querySelector('[data-role="select-painel"]');
    const selectFace = bloco.querySelector('[data-role="select-face"]');

    const idPainelAtual = idNum(selectPainel?.value || 0);
    const codFaceAtual = safeStr(selectFace?.value || '').trim().toUpperCase();
    const idPainelSalvo = idNum(dadosSalvos?.IDDimPaineisEuromidia ?? dadosSalvos?.id_painel ?? 0) || null;
    const codPontoSalvo = safeStr(dadosSalvos?.CodPonto ?? dadosSalvos?.cod_ponto ?? '').trim();
    const codFaceSalvo = safeStr(dadosSalvos?.CodFace ?? dadosSalvos?.cod_face ?? '').trim().toUpperCase();
    const tipoPainelSalvo = safeStr(dadosSalvos?.TipoPainel ?? dadosSalvos?.tipo_painel ?? '').trim();

    const painelFace = localizarPainelFaceCatalogo({
      id_painel: idPainelSalvo || idPainelAtual,
      cod_ponto: codPontoSalvo,
      cod_face: codFaceSalvo || codFaceAtual,
      tipo_painel: tipoPainelSalvo,
    });

    if (!painelFace) {
      if (dadosSalvos) {
        aplicarDadosSalvosRapidosPainelFace(bloco, dadosSalvos);
      }
      atualizarSelectFaceVisualDoBloco(bloco, { permitirSemPainel: true });
      const atual = obterPainelFaceSelecionadoDoBloco(bloco);
      preencherPreviewPainel(bloco, atual);
      if (!atual && !dadosSalvos) {
        limparComercialBloco(bloco);
      } else if (!atual && dadosSalvos) {
        limparComercialBloco(bloco, 'Consultando catálogo de painéis e faces...', { preservarSeletoresTabelaPreco: true });
      }
      return;
    }

    await selecionarPainelCombobox(bloco, painelFace, false);
    await atualizarComercialDoBloco(bloco, dadosSalvos);
  }

  async function atualizarComercialDoBloco(bloco, dadosSalvos = null){
    const selectPainel = bloco.querySelector('[data-role="select-painel"]');
    const selectFace = bloco.querySelector('[data-role="select-face"]');

    if (dadosSalvos && typeof dadosSalvos === 'object') {
      bloco.__dadosPainelFaceOriginais = Object.assign({}, bloco.__dadosPainelFaceOriginais || {}, dadosSalvos || {});
    }

    const idPainel = idNum(selectPainel?.value || 0);
    const painel = obterPainelFaceSelecionadoDoBloco(bloco) || paineisPorId.get(idPainel) || null;
    const codFace = safeStr(selectFace?.value || '').trim();
    const preservarSeletoresTabelaPreco = !!(dadosSalvos || bloco.__dadosPainelFaceOriginais);
    const sequenciaComercial = (idNum(bloco.__sequenciaComercial || 0) || 0) + 1;
    bloco.__sequenciaComercial = sequenciaComercial;

    preencherPreviewPainel(bloco, painel);
    limparComercialBloco(
      bloco,
      preservarSeletoresTabelaPreco ? 'Consultando custos e preços deste CodFace...' : 'Selecione Cidade, Tipo Produto e CodFace para consultar custo e preços.',
      { preservarSeletoresTabelaPreco }
    );

    if (!idPainel || !painel || !codFace) return;

    const comercial = await carregarComercialPainelFace(idPainel, codFace);
    if (sequenciaComercial !== bloco.__sequenciaComercial) return;

    if (!comercial){
      limparComercialBloco(
        bloco,
        'Não foi possível consultar custos e preços para o CodFace selecionado.',
        { preservarSeletoresTabelaPreco }
      );
      return;
    }

    renderizarComercialBloco(bloco, comercial, dadosSalvos || bloco.__dadosPainelFaceOriginais || null);
  }




function obterIdReservaDoBloco(bloco){
  const input = bloco?.querySelector('[data-role="input-reserva-id"]');
  return idNum(input?.value || 0) || null;
}

function obterIdReservaDeObjeto(valor){
  return idNum(
    valor?.IDReserva ??
    valor?.id_reserva ??
    valor?.Reserva ??
    valor?.reserva ??
    0
  ) || null;
}

function itemTemReservaInformadaPeloUsuarioKanban(item){
  if (!item || typeof item !== 'object') return false;
  return item.ReservaInformadaPeloUsuario === true
    || item.reserva_informada_pelo_usuario === true
    || item.reservaInformadaPeloUsuario === true;
}

function limparReservaNaoInformadaPeloUsuarioKanban(item){
  if (!item || typeof item !== 'object') return item;
  if (itemTemReservaInformadaPeloUsuarioKanban(item)) return item;

  const limpo = Object.assign({}, item);
  limpo.IDReserva = null;
  limpo.id_reserva = null;
  limpo.Reserva = null;
  limpo.reserva = null;
  limpo.IDFatoOcupacaoPaineisEuromidia = null;
  limpo.ReservaInformadaPeloUsuario = false;
  limpo.reserva_informada_pelo_usuario = false;
  return limpo;
}

function formatarResumoReservaKanban(reserva){
  if (!reserva || typeof reserva !== 'object') return '';

  const idReserva = obterIdReservaDeObjeto(reserva);
  const codPonto = safeStr(reserva.CodPonto ?? reserva.cod_ponto ?? '').trim();
  const codFace = safeStr(reserva.CodFace ?? reserva.cod_face ?? '').trim().toUpperCase();
  const cota = safeStr(reserva.Cota ?? reserva.cota ?? reserva.ExibicoesDia ?? reserva.exibicoes_dia ?? '').trim();
  const dataInicio = normalizarDataParaInput(reserva.DataInicio ?? reserva.data_inicio ?? reserva.DataInicioReserva ?? reserva.data_inicio_reserva ?? '');
  const dataFim = normalizarDataParaInput(reserva.DataFim ?? reserva.data_fim ?? reserva.DataFimReserva ?? reserva.data_fim_reserva ?? '');
  const status = safeStr(reserva.Status ?? reserva.status ?? '').trim().toUpperCase();
  const vendedor = safeStr(reserva.Vendedor ?? reserva.vendedor ?? '').trim();
  const marca = safeStr(reserva.MarcaExibida ?? reserva.marca_exibida ?? '').trim();
  const empresa = reserva.EmpresaCliente ?? reserva.empresa_cliente ?? reserva.Empresa ?? reserva.empresa ?? null;
  const nomeEmpresa = safeStr(empresa?.RazaoSocial ?? empresa?.NomeFantasia ?? reserva.NomeCliente ?? reserva.nome_cliente ?? '').trim();

  const partes = [];
  if (idReserva) partes.push(`Reserva ${idReserva}`);
  if (status) partes.push(`Status ${status}`);
  if (marca) partes.push(`Marca ${marca}`);
  if (nomeEmpresa) partes.push(`Cliente ${nomeEmpresa}`);
  if (codPonto || codFace) partes.push(`CodPonto/CodFace ${codPonto || '—'} / ${codFace || '—'}`);
  if (cota) partes.push(`Inserções/dia ${cota}`);
  if (dataInicio || dataFim) partes.push(`${formatarDataIsoParaBr(dataInicio) || '—'} até ${formatarDataIsoParaBr(dataFim) || '—'}`);
  if (vendedor) partes.push(`Vendedor ${vendedor}`);

  return partes.join(' • ');
}

function mostrarInfoReservaNoBloco(bloco, texto, tipo = 'info'){
  return;
}

async function buscarReservaKanban(idReserva){
  const idReservaInt = idNum(idReserva || 0);
  if (!idReservaInt) {
    throw new Error('Informe um número de reserva válido.');
  }

  const resultado = await fetchJsonKanban(`/kanban/api/reservas/${idReservaInt}`);
  const corpo = resultado.corpo || {};

  if (!respostaJsonKanbanOk(resultado)) {
    throw new Error(corpo.msg || corpo.erro || detalhesFalhaJsonKanban(resultado) || 'Reserva inválida.');
  }

  const reserva = corpo.reserva || null;
  if (!reserva) {
    throw new Error('Reserva não encontrada.');
  }

  return reserva;
}


function obterEmpresaClienteDaReservaKanban(reserva){
  if (!reserva || typeof reserva !== 'object') return null;

  const empresa =
    reserva.EmpresaCliente ??
    reserva.empresa_cliente ??
    reserva.Cliente ??
    reserva.cliente ??
    reserva.Empresa ??
    reserva.empresa ??
    null;

  if (empresa && typeof empresa === 'object') {
    return empresa;
  }

  return null;
}

async function aplicarDadosClienteMarcaDaReservaKanban(reserva){
  if (!reserva || typeof reserva !== 'object') return;

  const marcaReserva = safeStr(
    reserva.MarcaExibida ??
    reserva.marca_exibida ??
    reserva.Marca ??
    reserva.marca ??
    ''
  ).trim();

  if (inputMarcaCard && marcaReserva) {
    inputMarcaCard.value = marcaReserva;
    inputMarcaCard.dispatchEvent(new Event('input', { bubbles: true }));
    inputMarcaCard.dispatchEvent(new Event('change', { bubbles: true }));
  }

  const empresaReserva = obterEmpresaClienteDaReservaKanban(reserva);
  if (empresaReserva) {
    atualizarCatalogoEmpresa(empresaReserva);
  }

  const idClienteReserva = idNum(
    reserva.IDCliente ??
    reserva.id_cliente ??
    empresaReserva?.IDEmpresa ??
    empresaReserva?.ID ??
    0
  ) || null;

  if (!idClienteReserva) return;

  try {
    await selecionarEmpresaPorIdComGarantia(idClienteReserva, true);
  } catch (erro) {
    console.warn('aplicarDadosClienteMarcaDaReservaKanban: falhou ao selecionar empresa da reserva', erro);
    mostrarMensagemCard(
      erro?.message ||
      'A reserva foi carregada, mas não consegui selecionar automaticamente a empresa do IDCliente.'
    );
  }
}


async function aplicarReservaNoBloco(bloco, reserva, opcoes = {}){
  if (!bloco || !reserva) return;

  const idReserva = obterIdReservaDeObjeto(reserva);
  const inputReserva = bloco.querySelector('[data-role="input-reserva-id"]');
  if (inputReserva && idReserva) {
    inputReserva.value = String(idReserva);
  }

  bloco.__reservaOcupacaoSelecionada = Object.assign({}, reserva || {});
  mostrarInfoReservaNoBloco(bloco, formatarResumoReservaKanban(reserva), opcoes.tipoInfo || 'ok');

  const codFaceReserva = safeStr(reserva.CodFace ?? reserva.cod_face ?? '').trim().toUpperCase();
  const codPontoReserva = safeStr(reserva.CodPonto ?? reserva.cod_ponto ?? '').trim();
  const idPainelReserva = idNum(reserva.IDPainelEuromidia ?? reserva.id_painel ?? reserva.IDDimPaineisEuromidia ?? 0) || null;
  const cotaReserva = safeStr(reserva.Cota ?? reserva.cota ?? reserva.ExibicoesDia ?? reserva.exibicoes_dia ?? '').trim();
  const dataHojeIso = obterDataAtualIsoLocal();
  const dataInicioReservaOriginal = normalizarDataParaInput(reserva.DataInicio ?? reserva.data_inicio ?? '');
  const dataFimReservaOriginal = normalizarDataParaInput(reserva.DataFim ?? reserva.data_fim ?? '');
  const dataInicioReserva = manterSomenteDataAtualOuFutura(dataInicioReservaOriginal);
  const dataFimReservaBase = manterSomenteDataAtualOuFutura(dataFimReservaOriginal);
  const dataFimReserva = dataInicioReserva && dataFimReservaBase && dataFimReservaBase >= dataInicioReserva
    ? dataFimReservaBase
    : '';

  if (
    (dataInicioReservaOriginal && dataInicioReservaOriginal < dataHojeIso) ||
    (dataFimReservaOriginal && dataFimReservaOriginal < dataHojeIso)
  ) {
    mostrarInfoReservaNoBloco(
      bloco,
      `A reserva possui data anterior a ${formatarDataIsoParaBr(dataHojeIso)}. Selecione um novo período válido.`,
      'erro'
    );
  }

  await aplicarDadosClienteMarcaDaReservaKanban(reserva);

  await carregarCatalogoPainelFaces();

  const itemCatalogo = localizarPainelFaceCatalogo({
    id_painel: idPainelReserva,
    cod_ponto: codPontoReserva,
    cod_face: codFaceReserva,
  });

  const dadosReserva = Object.assign({}, reserva || {}, {
    IDFatoOcupacaoPaineisEuromidia: idReserva,
    id_reserva: idReserva,
    IDDimPaineisEuromidia: itemCatalogo?.IDDimPaineisEuromidia ?? idPainelReserva,
    id_painel: itemCatalogo?.IDDimPaineisEuromidia ?? idPainelReserva,
    IDDimFacesPaineis: itemCatalogo?.IDDimFacesPaineis ?? reserva.IDDimFacesPaineis ?? reserva.id_face ?? null,
    id_face: itemCatalogo?.IDDimFacesPaineis ?? reserva.IDDimFacesPaineis ?? reserva.id_face ?? null,
    CodPonto: itemCatalogo?.CodPonto ?? codPontoReserva,
    cod_ponto: itemCatalogo?.CodPonto ?? codPontoReserva,
    CodFace: itemCatalogo?.CodFace ?? codFaceReserva,
    cod_face: itemCatalogo?.CodFace ?? codFaceReserva,
    ExibicoesDia: cotaReserva || null,
    exibicoes_dia: cotaReserva || null,
    DataInicio: dataInicioReserva || null,
    data_inicio: dataInicioReserva || null,
    DataFim: dataFimReserva || null,
    data_fim: dataFimReserva || null,
  });

  if (itemCatalogo) {
    selecionarPainelCombobox(bloco, itemCatalogo, false);
    await atualizarComercialDoBloco(bloco, dadosReserva);
  }

  const inputDataInicio = bloco.querySelector('[data-role="input-data-inicio"]');
  const inputDataFim = bloco.querySelector('[data-role="input-data-fim"]');
  if (inputDataInicio && dataInicioReserva) inputDataInicio.value = dataInicioReserva;
  if (inputDataFim && dataFimReserva) {
    inputDataFim.value = dataFimReserva;
    inputDataFim.min = obterMaiorDataIso(obterDataAtualIsoLocal(), dataInicioReserva) || obterDataAtualIsoLocal();
  }

  const selectExibicoesDia = bloco.querySelector('[data-role="select-exibicoes-dia"]');
  if (selectExibicoesDia && cotaReserva) {
    selecionarValoresNoSelect(selectExibicoesDia, [cotaReserva]);
    atualizarResumoDropdownExibicoesDia(selectExibicoesDia);
    sincronizarSeletoresTabelaPrecoDoBloco(bloco, { origem: 'reserva' });
    atualizarResumoComercial(bloco, { formatarCampos: true });
  }

  sincronizarRestricoesDataFimDoBloco(bloco);
  agendarSincronizacaoFormularioSolicitacao();
}

async function aplicarReservaDigitadaNoBloco(bloco){
  const inputReserva = bloco?.querySelector('[data-role="input-reserva-id"]');
  if (!inputReserva) return;

  const idReserva = idNum(inputReserva.value || 0) || null;
  if (!idReserva) {
    bloco.__reservaOcupacaoSelecionada = null;
    mostrarInfoReservaNoBloco(bloco, '', 'info');
    agendarSincronizacaoFormularioSolicitacao();
    return;
  }

  mostrarInfoReservaNoBloco(bloco, `Validando reserva ${idReserva}...`, 'info');

  try {
    const reserva = await buscarReservaKanban(idReserva);
    await aplicarReservaNoBloco(bloco, reserva, { tipoInfo: 'ok' });
  } catch (erro) {
    bloco.__reservaOcupacaoSelecionada = null;
    mostrarInfoReservaNoBloco(bloco, erro?.message || 'Reserva inválida.', 'erro');
    mostrarMensagemCard(erro?.message || 'Reserva inválida.');
  }
}

  function montarDadosPainelFaceParaExibicao(bloco, exibicoesDia, periodoPreferido = ''){
    const selectPainel = bloco?.querySelector('[data-role="select-painel"]');
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const idPainel = idNum(selectPainel?.value || 0) || null;
    const codFace = safeStr(selectFace?.value || '').trim().toUpperCase() || null;
    const periodoAtual = safeStr(periodoPreferido || bloco?.querySelector('[data-role="select-periodo-exibicao"]')?.value || '').trim();
    const exibicoesTexto = safeStr(exibicoesDia || '').trim();

    const preco = localizarPrecoPorFiltrosDoBloco(bloco, {
      exibicoes_dia: exibicoesTexto,
      periodo_exibicao: periodoAtual,
    }) || localizarPrecoPorFiltrosDoBloco(bloco, {
      exibicoes_dia: exibicoesTexto,
    }) || null;

    const periodoFinal = safeStr(preco?.PeriodoExibicao || periodoAtual || '').trim();
    const idPrecoFinal = idNum(preco?.IDDimTabelaPrecosEuromidia || 0) || null;
    const novoValor = parseNumeroInput(bloco?.querySelector('[data-role="input-novo-valor"]')?.value);
    const percentual = parseNumeroInput(bloco?.querySelector('[data-role="input-percentual"]')?.value);
    const dataInicio = normalizarDataParaInput(bloco?.querySelector('[data-role="input-data-inicio"]')?.value || '');
    const dataFim = normalizarDataParaInput(bloco?.querySelector('[data-role="input-data-fim"]')?.value || '');
    const idReserva = obterIdReservaDoBloco(bloco);
    const itemContratoAditivo = bloco?.__itemContratoAditivoSelecionado || null;
    const idItemContratoAditivo = idNum(itemContratoAditivo?.id_item_contrato ?? itemContratoAditivo?.IDFatoControleContratosItensEuromidia ?? 0) || null;
    const codPontoContratoAditivo = safeStr(itemContratoAditivo?.cod_ponto ?? itemContratoAditivo?.CodPonto ?? '').trim() || null;
    const codFaceContratoAditivo = safeStr(itemContratoAditivo?.cod_face ?? itemContratoAditivo?.CodFace ?? '').trim().toUpperCase() || null;

    const valorTabela = obterValorPrecoTabela(preco);
    let valorVendaFinal = valorTabela;

    if (novoValor !== null) {
      valorVendaFinal = novoValor;
    } else if (percentual !== null) {
      valorVendaFinal = calcularNovoValorPorPercentual(valorTabela, percentual);
    }

    return {
      IDDimPaineisEuromidia: idPainel,
      id_painel: idPainel,
      CodFace: codFace,
      cod_face: codFace,
      IDFatoOcupacaoPaineisEuromidia: idReserva,
      id_reserva: idReserva,
      Reserva: idReserva,
      IDDimTabelaPrecosEuromidia: idPrecoFinal,
      id_preco: idPrecoFinal,
      PeriodoExibicao: periodoFinal,
      periodo_exibicao: periodoFinal,
      ExibicoesDia: exibicoesTexto || null,
      exibicoes_dia: exibicoesTexto || null,
      Cota: exibicoesTexto || null,
      cota: exibicoesTexto || null,
      NovoValor: novoValor,
      novo_valor: novoValor,
      ValorTabela: valorTabela,
      valor_tabela: valorTabela,
      ValorVendaFinal: valorVendaFinal,
      valor_venda_final: valorVendaFinal,
      PercentualDesconto: novoValor !== null ? null : percentual,
      percentual_desconto: novoValor !== null ? null : percentual,
      DataInicio: dataInicio || null,
      data_inicio: dataInicio || null,
      DataFim: dataFim || null,
      data_fim: dataFim || null,
      origem_aditivo: itemContratoAditivo ? 'ITEM_CONTRATO_EXISTENTE' : null,
      id_item_contrato_aditivo: idItemContratoAditivo,
      CodPontoContratoAditivo: codPontoContratoAditivo,
      cod_ponto_contrato_aditivo: codPontoContratoAditivo,
      CodFaceContratoAditivo: codFaceContratoAditivo,
      cod_face_contrato_aditivo: codFaceContratoAditivo,
    };
  }

  function obterChaveBlocoPainelFacePeriodo(bloco, periodoPreferido = ''){
    const selectPainel = bloco?.querySelector('[data-role="select-painel"]');
    const selectFace = bloco?.querySelector('[data-role="select-face"]');
    const selectPeriodo = bloco?.querySelector('[data-role="select-periodo-exibicao"]');
    const painelFace = obterPainelFaceSelecionadoDoBloco(bloco) || null;

    const idPainel = idNum(selectPainel?.value || painelFace?.IDDimPaineisEuromidia || 0) || 0;
    const codFace = safeStr(selectFace?.value || painelFace?.CodFace || '').trim().toUpperCase();
    const periodo = safeStr(periodoPreferido || selectPeriodo?.value || '').trim();

    if (!idPainel || !codFace) return '';
    return `${idPainel}|${codFace}|${periodo}`;
  }

  function listarExibicoesJaRepresentadasEmOutrosBlocos(blocoReferencia, periodoPreferido = ''){
    const chaveReferencia = obterChaveBlocoPainelFacePeriodo(blocoReferencia, periodoPreferido);
    const exibicoes = new Set();
    if (!chaveReferencia || !painelFaceLista) return exibicoes;

    for (const outroBloco of painelFaceLista.querySelectorAll('.kb-painel-item')) {
      if (outroBloco === blocoReferencia) continue;
      if (obterChaveBlocoPainelFacePeriodo(outroBloco, periodoPreferido) !== chaveReferencia) continue;

      obterExibicoesDiaSelecionadasDoBloco(outroBloco)
        .map((valor) => safeStr(valor || '').trim())
        .filter(Boolean)
        .forEach((valor) => exibicoes.add(valor));
    }

    return exibicoes;
  }

  function dividirBlocoPorMultiplasExibicoesDia(bloco){
    const selectExibicoesDia = bloco?.querySelector('[data-role="select-exibicoes-dia"]');
    const selectPeriodoExibicao = bloco?.querySelector('[data-role="select-periodo-exibicao"]');
    if (!bloco || !selectExibicoesDia || bloco.__bloqueioDivisaoExibicoesDia) return false;

    const selecionadas = obterValoresSelecionadosSelect(selectExibicoesDia)
      .map((valor) => safeStr(valor || '').trim())
      .filter(Boolean);

    const unicas = Array.from(new Set(selecionadas));
    if (unicas.length <= 1) return false;

    const periodoAtual = safeStr(selectPeriodoExibicao?.value || '').trim();
    const exibicoesJaRepresentadas = listarExibicoesJaRepresentadasEmOutrosBlocos(bloco, periodoAtual);
    const exibicaoMantidaNoBloco = unicas.find((valor) => !exibicoesJaRepresentadas.has(valor)) || unicas[0];
    const dadosMantidos = montarDadosPainelFaceParaExibicao(bloco, exibicaoMantidaNoBloco, periodoAtual);
    const idPrecoMantido = idNum(dadosMantidos?.id_preco || dadosMantidos?.IDDimTabelaPrecosEuromidia || 0) || null;

    bloco.__bloqueioDivisaoExibicoesDia = true;
    selecionarValoresNoSelect(selectExibicoesDia, [exibicaoMantidaNoBloco]);
    aplicarGrupoVisualExibicoesDiaNoBloco(bloco, unicas, exibicaoMantidaNoBloco);
    sincronizarSeletoresTabelaPrecoDoBloco(bloco, { id_preco: idPrecoMantido || null, origem: 'divisao_exibicoes' });
    atualizarResumoDropdownExibicoesDia(selectExibicoesDia);
    atualizarResumoComercial(bloco, { formatarCampos: true });
    bloco.__bloqueioDivisaoExibicoesDia = false;

    let referenciaInsercao = bloco;
    unicas
      .filter((exibicoesDia) => exibicoesDia !== exibicaoMantidaNoBloco)
      .forEach((exibicoesDia) => {
        if (exibicoesJaRepresentadas.has(exibicoesDia)) return;

        const dadosClone = montarDadosPainelFaceParaExibicao(bloco, exibicoesDia, periodoAtual);
        const novoBloco = criarPainelFaceItem(dadosClone);
        novoBloco.dataset.origemDivisaoExibicoesDia = '1';
        aplicarGrupoVisualExibicoesDiaNoBloco(novoBloco, unicas, exibicoesDia);
        referenciaInsercao.insertAdjacentElement('afterend', novoBloco);
        referenciaInsercao = novoBloco;
        exibicoesJaRepresentadas.add(exibicoesDia);
      });

    atualizarTitulosPainelFace();
    sincronizarSeletoresContratoAditivoEmTodosBlocos();
    agendarSincronizacaoFormularioSolicitacao();
    mostrarMensagemCard('Inserções/dia separadas em itens diferentes para o mesmo CodPonto/CodFace.', 'info');
    return true;
  }

  function criarPainelFaceItem(dados = null){
    const bloco = el('div', { class:'kb-painel-item' }, [
      el('div', { class:'kb-painel-item-topo' }, [
        el('div', { class:'kb-painel-item-titulo', 'data-role':'titulo-item' }, ['Painel / Face']),
        el('div', { class:'kb-painel-item-acoes' }, [
          el('button', {
            class:'kb-btn sm kb-btn-orcamento',
            type:'button',
            'data-role':'btn-orcamento-item',
            title:'Abrir orçamento deste card'
          }, ['Orçamento']),
          el('button', { class:'kb-btn sm', type:'button', 'data-role':'btn-duplicar' }, ['+']),
          el('button', { class:'kb-btn sm danger', type:'button', 'data-role':'btn-remover' }, ['−'])
        ])
      ]),
      el('div', { class:'kb-contrato-item-wrap', 'data-role':'wrap-item-contrato-aditivo', hidden:'hidden' }, [
        el('div', { class:'kb-contrato-item-titulo' }, ['Item existente do contrato para aditivo']),
        el('div', { class:'kb-contrato-item-grid' }, [
          el('div', { class:'kb-campo' }, [
            el('div', { class:'kb-campo-label' }, ['CodPonto do contrato']),
            el('select', { class:'kb-select', 'data-role':'select-codponto-contrato-item' }, [
              el('option', { value:'' }, ['— CodPonto do contrato —'])
            ])
          ]),
          el('div', { class:'kb-campo' }, [
            el('div', { class:'kb-campo-label' }, ['Face do contrato']),
            el('select', { class:'kb-select', 'data-role':'select-codface-contrato-item' }, [
              el('option', { value:'' }, ['— Face do contrato —'])
            ])
          ]),
          el('div', { class:'kb-contrato-item-info', 'data-role':'contrato-item-info' }, [
            el('div', { class:'kb-contrato-item-vazio' }, ['Selecione um CodPonto e uma Face do contrato, ou use o seletor manual abaixo para incluir um novo painel/face.'])
          ])
        ])
      ]),
      el('div', { class:'kb-painel-reserva-wrap', 'data-role':'wrap-reserva-painel-face' }, [
        el('div', { class:'kb-campo' }, [
          el('div', { class:'kb-campo-label' }, ['Reserva']),
          el('input', {
            class:'kb-input kb-input-reserva-id',
            type:'number',
            inputmode:'numeric',
            min:'1',
            step:'1',
            autocomplete:'off',
            placeholder:'Ex: 18619',
            'data-role':'input-reserva-id'
          })
        ])
      ]),
      el('div', { class:'kb-painel-item-grid' }, [
        el('select', { class:'kb-select', 'data-role':'select-filtro-cidade' }),
        el('select', { class:'kb-select', 'data-role':'select-filtro-tipo' }),
        el('div', { class:'kb-combobox grow', 'data-role':'combo-painel' }, [
          el('input', {
            class:'kb-input kb-combobox-input',
            type:'text',
            autocomplete:'off',
            spellcheck:'false',
            placeholder:'Digite CodPonto, logradouro, cidade, bairro, tipo ou CodFace...',
            'data-role':'input-painel-busca'
          }),
          el('button', {
            class:'kb-combobox-toggle',
            type:'button',
            'data-role':'btn-toggle-painel',
            'aria-label':'Abrir lista de painéis e faces'
          }, ['▾']),
          el('div', { class:'kb-combobox-lista', hidden:'hidden', 'data-role':'lista-painel-busca' }),
          el('select', {
            class:'kb-select grow kb-select-hidden',
            tabindex:'-1',
            'aria-hidden':'true',
            'data-role':'select-painel',
            'data-placeholder':'— Selecione um painel —'
          }),
          el('select', {
            class:'kb-select grow kb-select-hidden',
            tabindex:'-1',
            'aria-hidden':'true',
            'data-role':'select-face',
            'data-placeholder':'— Face (CodFace) —'
          })
        ]),
        el('div', { class:'kb-campo', 'data-role':'wrap-exibicoes-dia', hidden:'hidden' }, [
          el('div', { class:'kb-campo-label' }, ['Inserções / dia']),
          el('details', { class:'dd kb-exibicoes-dd', 'data-role':'dd-exibicoes-dia' }, [
            el('summary', { class:'dd-summary' }, [
              el('span', { class:'dd-label', 'data-role':'exibicoes-dia-resumo' }, ['— Inserções / dia —']),
              el('span', { class:'dd-caret' }, ['▾'])
            ]),
            el('div', { class:'dd-panel' }, [
              el('input', {
                type:'text',
                class:'dd-search',
                placeholder:'Pesquisar...',
                autocomplete:'off',
                'data-role':'exibicoes-dia-busca'
              }),
              el('div', { class:'dd-hint' }, ['Selecione uma ou mais inserções por dia.']),
              el('div', { class:'dd-lista', 'data-role':'exibicoes-dia-lista' })
            ])
          ]),
          el('select', {
            class:'kb-select kb-select-hidden',
            'data-role':'select-exibicoes-dia',
            multiple:'multiple',
            tabindex:'-1',
            'aria-hidden':'true',
            style:'display:none;',
            title:'Selecione uma ou mais inserções por dia. Ao salvar, cada inserção selecionada vira um item separado do mesmo CodPonto/CodFace.'
          }, [
            el('option', { value:'', disabled:'disabled' }, ['— Inserções / dia —'])
          ])
        ]),
        el('div', { class:'kb-campo', 'data-role':'wrap-periodo-exibicao', hidden:'hidden' }, [
          el('div', { class:'kb-campo-label' }, ['Período de campanha']),
          el('select', { class:'kb-select', 'data-role':'select-periodo-exibicao' }, [
            el('option', { value:'' }, ['— Período de campanha —'])
          ])
        ])
      ]),
      el('div', { class:'kb-painel-preview', 'data-role':'painel-preview', style:'display:none;' }, [
        el('div', { class:'linha' }, [el('span', { class:'k' }, ['CodPonto:']), el('span', { class:'v', 'data-role':'pn-codponto' }, ['—'])]),
        el('div', { class:'linha' }, [el('span', { class:'k' }, ['Tipo:']), el('span', { class:'v', 'data-role':'pn-tipo' }, ['—'])]),
        el('div', { class:'linha' }, [el('span', { class:'k' }, ['Cidade/UF:']), el('span', { class:'v', 'data-role':'pn-cidadeuf' }, ['—'])]),
        el('div', { class:'linha' }, [el('span', { class:'k' }, ['Endereço:']), el('span', { class:'v', 'data-role':'pn-endereco' }, ['—'])]),
        el('div', { class:'linha' }, [el('span', { class:'k' }, ['Faces:']), el('span', { class:'v', 'data-role':'pn-faces' }, ['—'])])
      ]),
      el('div', { 'data-role':'comercial-wrap' }, [
        el('div', { class:'kb-comercial-vazio' }, ['Selecione Cidade, Tipo Produto e CodFace para consultar custo e preços.'])
      ])
    ]);

    bloco.__dadosPainelFaceOriginais = dados && typeof dados === 'object'
      ? Object.assign({}, dados || {})
      : null;

    const comboPainel = bloco.querySelector('[data-role="combo-painel"]');
    const inputPainelBusca = bloco.querySelector('[data-role="input-painel-busca"]');
    const btnTogglePainel = bloco.querySelector('[data-role="btn-toggle-painel"]');
    const selectPainel = bloco.querySelector('[data-role="select-painel"]');
    const selectFace = bloco.querySelector('[data-role="select-face"]');
    const selectFiltroCidade = bloco.querySelector('[data-role="select-filtro-cidade"]');
    const selectFiltroTipo = bloco.querySelector('[data-role="select-filtro-tipo"]');
    const selectExibicoesDia = bloco.querySelector('[data-role="select-exibicoes-dia"]');
    const selectPeriodoExibicao = bloco.querySelector('[data-role="select-periodo-exibicao"]');
    const inputReservaId = bloco.querySelector('[data-role="input-reserva-id"]');
    const idReservaSalva = obterIdReservaDeObjeto(dados || {});
    if (inputReservaId && idReservaSalva) {
      inputReservaId.value = String(idReservaSalva);
      bloco.__reservaOcupacaoSelecionada = Object.assign({}, dados || {}, { id_reserva: idReservaSalva });
      mostrarInfoReservaNoBloco(bloco, formatarResumoReservaKanban(bloco.__reservaOcupacaoSelecionada) || `Reserva ${idReservaSalva} vinculada ao card.`, 'info');
    }
    atualizarDropdownExibicoesDia(selectExibicoesDia);
    const selectCodPontoContratoItem = bloco.querySelector('[data-role="select-codponto-contrato-item"]');
    const selectCodFaceContratoItem = bloco.querySelector('[data-role="select-codface-contrato-item"]');

    const codPontoContratoAditivoSalvo = safeStr(
      dados?.CodPontoContratoAditivo ??
      dados?.cod_ponto_contrato_aditivo ??
      dados?.CodPontoContrato ??
      dados?.cod_ponto_contrato ??
      dados?.CodPonto ??
      dados?.cod_ponto ??
      ""
    ).trim();

    const codFaceContratoAditivoSalva = safeStr(
      dados?.CodFaceContratoAditivo ??
      dados?.cod_face_contrato_aditivo ??
      dados?.CodFaceContrato ??
      dados?.cod_face_contrato ??
      dados?.CodFace ??
      dados?.cod_face ??
      ""
    ).trim().toUpperCase();

    if (codPontoContratoAditivoSalvo) {
      bloco.__codPontoContratoItemDesejado = codPontoContratoAditivoSalvo;
    }

    if (codFaceContratoAditivoSalva) {
      bloco.__codFaceContratoItemDesejada = codFaceContratoAditivoSalva;
    }

    montarSelectPaineisNoElemento(selectPainel);
    limparSelectFacesNoElemento(selectFace);
    atualizarSelectFaceVisualDoBloco(bloco);
    sincronizarFiltrosPainelFaceDoBloco(bloco, { manterSelecionadoInvalido: true });
    preencherSelectFiltroPainelFace(selectFiltroCidade, listarCidadesPainelFaceDoBloco(bloco), '— Cidade —');
    preencherSelectFiltroPainelFace(selectFiltroTipo, listarTiposPainelFaceDoBloco(bloco), '— Tipo Produto —');
    aplicarDadosSalvosRapidosPainelFace(bloco, dados);

    bloco.querySelector('[data-role="btn-orcamento-item"]')?.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();

      const idCardAtual = idNum(cardAbertoId || 0);
      if (!idCardAtual) {
        mostrarMensagemCard('Salve ou abra um card válido para gerar o orçamento.');
        return;
      }

      await abrirOrcamentoCard(idCardAtual);
    });

    bloco.querySelector('[data-role="btn-duplicar"]')?.addEventListener('click', () => {
      const novoBloco = criarPainelFaceItem();
      painelFaceLista?.appendChild(novoBloco);
      atualizarTitulosPainelFace();
      atualizarVisibilidadeContratoAditivoDoBloco(novoBloco);
      novoBloco?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    });

    bloco.querySelector('[data-role="btn-remover"]')?.addEventListener('click', () => {
      bloco.remove();
      if (!(painelFaceLista?.querySelector('.kb-painel-item'))) {
        painelFaceLista?.appendChild(criarPainelFaceItem());
      }
      atualizarTitulosPainelFace();
    });

    inputReservaId?.addEventListener('change', () => {
      void aplicarReservaDigitadaNoBloco(bloco);
    });

    inputReservaId?.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      void aplicarReservaDigitadaNoBloco(bloco);
    });


    selectCodPontoContratoItem?.addEventListener('change', async () => {
      const fluxo = obterFluxoContratoAtual();
      const codPontoSelecionado = safeStr(selectCodPontoContratoItem.value || "").trim();
      bloco.__codPontoContratoItemDesejado = codPontoSelecionado;
      bloco.__codFaceContratoItemDesejada = "";
      limparSelecaoContratoAditivoDoBloco(bloco);

      if (!fluxo.id_contrato || fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO) {
        return;
      }

      if (!codPontoSelecionado) {
        renderizarInfoItemContratoAditivo(bloco, null, "Selecione um CodPonto do contrato ou escolha Novo Painel / Face para preencher manualmente.");
        return;
      }

      if (codPontoSelecionado === VALOR_OPCAO_NOVO_PAINEL) {
        limparSelectComPlaceholder(selectCodFaceContratoItem, "— Face do contrato —");
        renderizarInfoItemContratoAditivo(bloco, null, "Novo Painel / Face selecionado. Agora escolha o painel e a face no seletor manual abaixo.");
        setMensagemFluxoContrato("Novo Painel / Face no aditivo: o contrato continua como aditivo, mas este bloco será preenchido pelo seletor manual de painéis.", "alerta");
        return;
      }

      try {
        const faces = await preencherFacesContratoNoBloco(bloco, codPontoSelecionado);
        renderizarInfoItemContratoAditivo(bloco, null, faces.length ? "Agora selecione a face existente deste CodPonto." : "Nenhuma face ativa foi encontrada para este CodPonto no contrato.");
      } catch (erro) {
        console.warn('selectCodPontoContratoItem: falhou ao carregar faces do contrato', erro);
        renderizarInfoItemContratoAditivo(bloco, null, "Não foi possível carregar as faces deste CodPonto. Você ainda pode preencher pelo seletor manual abaixo.");
      }
    });

    selectCodFaceContratoItem?.addEventListener('change', async () => {
      const fluxo = obterFluxoContratoAtual();
      const codPontoSelecionado = safeStr(selectCodPontoContratoItem?.value || "").trim();
      const codFaceSelecionado = safeStr(selectCodFaceContratoItem?.value || "").trim().toUpperCase();
      bloco.__codPontoContratoItemDesejado = codPontoSelecionado;
      bloco.__codFaceContratoItemDesejada = codFaceSelecionado;

      if (!fluxo.id_contrato || fluxo.modo_contrato !== VALOR_MODO_CONTRATO_ADITIVO) return;
      if (!codPontoSelecionado || !codFaceSelecionado || codPontoSelecionado === VALOR_OPCAO_NOVO_PAINEL) return;

      const chaveFaces = `${idNum(fluxo.id_contrato)}|${safeStr(codPontoSelecionado).trim()}`;
      const facesDisponiveis = facesPorContratoPontoCache.get(chaveFaces) || await carregarFacesDoContrato(fluxo.id_contrato, codPontoSelecionado);
      const faceSelecionada = encontrarFaceContratoEmLista(facesDisponiveis, codFaceSelecionado);

      if (!faceSelecionada) {
        bloco.__itemContratoAditivoSelecionado = null;
        renderizarInfoItemContratoAditivo(
          bloco,
          null,
          `CodPonto ${codPontoSelecionado} / CodFace ${codFaceSelecionado} não existe neste contrato. O sistema vai tratar como inclusão de novo item no aditivo.`
        );
        setMensagemFluxoContrato("Face não encontrada no contrato selecionado. Pode prosseguir como novo item do aditivo.", "info");
        return;
      }

      const confirmado = confirmarCarregamentoItemContratoExistente({
        codPonto: codPontoSelecionado,
        codFace: codFaceSelecionado,
        faceSelecionada
      });

      if (!confirmado) {
        if (selectCodFaceContratoItem) selectCodFaceContratoItem.value = "";
        bloco.__itemContratoAditivoSelecionado = null;
        renderizarInfoItemContratoAditivo(
          bloco,
          null,
          "Seleção cancelada. O CodFace existe no contrato, mas os dados não foram carregados para edição."
        );
        setMensagemFluxoContrato("Seleção cancelada. Nenhum item existente do contrato foi carregado.", "alerta");
        return;
      }

      const aplicado = await aplicarPainelFaceContratoNoBloco(bloco, codPontoSelecionado, codFaceSelecionado, faceSelecionada);
      if (aplicado) {
        selecionarValorOuAcrescentarOpcao(selectCodPontoContratoCard, codPontoSelecionado, codPontoSelecionado);
        if (wrapSelectCodFaceContratoCard) wrapSelectCodFaceContratoCard.hidden = false;
        const facesGlobais = facesPorContratoPontoCache.get(chaveFaces) || [];
        montarSelectFacesContratoCard(facesGlobais);
        selecionarValorOuAcrescentarOpcao(selectCodFaceContratoCard, codFaceSelecionado, faceSelecionada.label || codFaceSelecionado);
        setMensagemFluxoContrato(`Item do contrato carregado neste bloco: ${montarTextoResumoItemContratoAditivo(faceSelecionada)}.`, "sucesso");
      }
    });


    btnTogglePainel?.addEventListener('click', () => {
      const listaPainel = bloco.querySelector('[data-role="lista-painel-busca"]');
      if (listaPainel?.hidden) abrirListaPaineisCombobox(bloco);
      else {
        sincronizarBuscaPainelComSelect(bloco);
        fecharListaPaineisCombobox(bloco);
      }
    });

    selectFiltroCidade?.addEventListener('change', () => {
      sincronizarFiltrosPainelFaceDoBloco(bloco);
      atualizarSelectFaceVisualDoBloco(bloco, { permitirSemPainel: true });
      if (comboPainel?.classList.contains('is-open')) {
        renderizarListaPaineisCombobox(bloco, inputPainelBusca?.value || '');
      }
    });

    selectFiltroTipo?.addEventListener('change', () => {
      sincronizarFiltrosPainelFaceDoBloco(bloco);
      atualizarSelectFaceVisualDoBloco(bloco, { permitirSemPainel: true });
      if (comboPainel?.classList.contains('is-open')) {
        renderizarListaPaineisCombobox(bloco, inputPainelBusca?.value || '');
      }
    });

    inputPainelBusca?.addEventListener('focus', () => {
      abrirListaPaineisCombobox(bloco);
    });

    inputPainelBusca?.addEventListener('click', () => {
      abrirListaPaineisCombobox(bloco);
    });

    inputPainelBusca?.addEventListener('input', () => {
      abrirListaPaineisCombobox(bloco);
      renderizarListaPaineisCombobox(bloco, inputPainelBusca.value || '');
    });

    inputPainelBusca?.addEventListener('keydown', (evento) => {
      if (evento.key === 'Enter') {
        evento.preventDefault();
        const primeira = bloco.__paineisResultadoComboboxAtual?.[0] || filtrarPaineisCombobox(bloco, inputPainelBusca.value || '')[0] || null;
        if (primeira) {
          alternarPainelFaceComboboxMultiplo(bloco, primeira, inputPainelBusca.value || '').catch((erro) => {
            console.warn('keydown painel: falhou ao alternar face', erro);
          });
          return;
        }
        reconciliarBuscaPainelDigitada(bloco);
        return;
      }

      if (evento.key === 'Escape') {
        evento.preventDefault();
        sincronizarBuscaPainelComSelect(bloco);
        fecharListaPaineisCombobox(bloco);
        return;
      }

      if (evento.key === 'ArrowDown') {
        abrirListaPaineisCombobox(bloco);
      }
    });

    selectFace?.addEventListener('change', async () => {
      const codFaceManual = safeStr(selectFace?.value || "").trim().toUpperCase();
      const itemContratoAtual = bloco.__itemContratoAditivoSelecionado || null;
      const codFaceContratoAtual = safeStr(itemContratoAtual?.cod_face || itemContratoAtual?.CodFace || "").trim().toUpperCase();

      if (itemContratoAtual && codFaceManual && codFaceManual !== codFaceContratoAtual) {
        bloco.__itemContratoAditivoSelecionado = null;
        renderizarInfoItemContratoAditivo(bloco, null, "Você alterou manualmente a face neste bloco. Para usar um item existente do contrato, selecione novamente CodPonto e Face no seletor do aditivo.");
      }

      atualizarSelectFaceVisualDoBloco(bloco);
      await atualizarComercialDoBloco(bloco);
      await validarPainelFaceManualAditivoNoBloco(bloco);

      const dataInicioAtual = safeStr(bloco.querySelector('[data-role="input-data-inicio"]')?.value || '').trim();
      const dataFimAtual = safeStr(bloco.querySelector('[data-role="input-data-fim"]')?.value || '').trim();
      limparCacheCalendarioOcupacao(codFaceManual);
      await inicializarCalendarioReservaDoBloco(bloco, {
        dataInicio: dataInicioAtual,
        dataFim: dataFimAtual,
      });
    });

    selectExibicoesDia?.addEventListener('change', () => {
      /*
       * Regra de negócio: cada inserção/dia selecionada precisa virar um bloco visual
       * separado do mesmo CodPonto/CodFace. Ex.: 540 e 1080 = dois itens.
       * A função de divisão já protege contra duplicidade quando o usuário clica
       * em "(Todas)" mais de uma vez.
       */
      if (dividirBlocoPorMultiplasExibicoesDia(bloco)) {
        return;
      }

      sincronizarSeletoresTabelaPrecoDoBloco(bloco, { origem: 'exibicoes' });
      atualizarResumoDropdownExibicoesDia(selectExibicoesDia);
      atualizarResumoComercial(bloco, { formatarCampos: true });
      redesenharCalendariosReservaDoBloco(bloco);
      atualizarTitulosPainelFace();
      agendarSincronizacaoFormularioSolicitacao();
    });

    selectPeriodoExibicao?.addEventListener('change', () => {
      sincronizarSeletoresTabelaPrecoDoBloco(bloco, { origem: 'periodo' });
      atualizarResumoComercial(bloco, { formatarCampos: true });
      atualizarTitulosPainelFace();
      agendarSincronizacaoFormularioSolicitacao();
    });

    if (dados){
      if (idReservaSalva) {
        void aplicarReservaNoBloco(bloco, Object.assign({}, dados || {}, { id_reserva: idReservaSalva }), { tipoInfo: 'info' });
      } else {
        void atualizarFacesDoBloco(bloco, dados);
      }
    } else {
      atualizarSelectFaceVisualDoBloco(bloco);
    }

    atualizarVisibilidadeContratoAditivoDoBloco(bloco);
    return bloco;
  }

  function obterPrecoSelecionadoDoBloco(bloco){
    const selectPreco = bloco?.querySelector('[data-role="select-preco"]');
    const idPreco = idNum(selectPreco?.value || 0) || null;
    const precos = Array.isArray(bloco?.__dadosComerciais?.precos) ? bloco.__dadosComerciais.precos : [];

    const selectExibicoesDia = bloco?.querySelector('[data-role="select-exibicoes-dia"]');
    const selectPeriodoExibicao = bloco?.querySelector('[data-role="select-periodo-exibicao"]');
    const exibicaoAtiva = obterExibicaoDiaAtivaDoBloco(bloco);
    const exibicoesReferencia = exibicaoAtiva || obterValoresSelecionadosSelect(selectExibicoesDia)[0] || '';
    const periodoReferencia = safeStr(selectPeriodoExibicao?.value || '').trim();
    const usarInsercoesDigitais = painelFaceUsaInsercoesDigitais(bloco);
    const opcoesExibicoes = listarOpcoesExibicoesDiaDoBloco(bloco, '');
    const exigeExibicoes = usarInsercoesDigitais && opcoesExibicoes.length > 0;
    const selecaoCompleta = Boolean(periodoReferencia) && (!exigeExibicoes || Boolean(exibicoesReferencia));

    if (!selecaoCompleta) {
      return idPreco ? (precos.find(p => idNum(p.IDDimTabelaPrecosEuromidia) === idPreco) || null) : null;
    }

    const precoPelaSelecaoAtual = localizarPrecoPorFiltrosDoBloco(bloco, {
      exibicoes_dia: exibicoesReferencia,
      periodo_exibicao: periodoReferencia,
    });

    if (precoPelaSelecaoAtual) {
      const idPrecoCorreto = idNum(precoPelaSelecaoAtual?.IDDimTabelaPrecosEuromidia || 0) || null;
      if (selectPreco && idPrecoCorreto && idPrecoCorreto !== idPreco) {
        const exibicoesFinais = chaveExibicoesDiaDoPreco(precoPelaSelecaoAtual, usarInsercoesDigitais);
        definirValorSelectOculto(
          selectPreco,
          String(idPrecoCorreto),
          `${safeStr(precoPelaSelecaoAtual?.PeriodoExibicao || '—')} | ${labelExibicoesDiaDoPreco(exibicoesFinais)} | ${formatarMoedaBR(obterValorPrecoTabela(precoPelaSelecaoAtual))}`
        );
      }
      return precoPelaSelecaoAtual;
    }

    return idPreco ? (precos.find(p => idNum(p.IDDimTabelaPrecosEuromidia) === idPreco) || null) : null;
  }

  function obterPrimeiroValorObjeto(objeto, nomesCampos){
    if (!objeto || typeof objeto !== 'object') return '';

    for (const nome of nomesCampos || []) {
      const valor = objeto[nome];
      if (valor !== null && valor !== undefined && safeStr(valor).trim() !== '') {
        return valor;
      }
    }

    return '';
  }

  function copiarTextoParaAreaTransferencia(texto, mensagemSucesso){
    const valor = safeStr(texto || '').trim();
    if (!valor) return;

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(valor)
        .then(() => mostrarMensagemCard(mensagemSucesso || 'Copiado para a área de transferência.', 'sucesso'))
        .catch(() => mostrarMensagemCard('Não consegui copiar automaticamente. Selecione e copie manualmente.', 'alerta'));
      return;
    }

    const temporario = document.createElement('textarea');
    temporario.value = valor;
    temporario.setAttribute('readonly', 'readonly');
    temporario.style.position = 'fixed';
    temporario.style.left = '-9999px';
    document.body.appendChild(temporario);
    temporario.select();

    try {
      document.execCommand('copy');
      mostrarMensagemCard(mensagemSucesso || 'Copiado para a área de transferência.', 'sucesso');
    } catch (erro) {
      console.warn('copiarTextoParaAreaTransferencia: falhou', erro);
      mostrarMensagemCard('Não consegui copiar automaticamente. Selecione e copie manualmente.', 'alerta');
    } finally {
      temporario.remove();
    }
  }

  function montarBlocoCheckinPublico(valoresSalvos = null){
    const dados = valoresSalvos && typeof valoresSalvos === 'object' ? valoresSalvos : {};

    const tokenPublico = safeStr(obterPrimeiroValorObjeto(dados, [
      'TokenPublicoCheckin',
      'token_publico_checkin',
      'TokenPublico',
      'token_publico'
    ])).trim();

    const urlBruta = safeStr(obterPrimeiroValorObjeto(dados, [
      'UrlCheckinPublico',
      'url_checkin_publico',
      'URLCheckinPublico',
      'url_publica_checkin'
    ])).trim();

    const urlCheckin = montarUrlKanban(
      urlBruta || (tokenPublico ? `/paineis/checkin/publico/${encodeURIComponent(tokenPublico)}` : '')
    );

    if (!urlCheckin) {
      return document.createDocumentFragment();
    }

    const box = el('div', { class:'kb-checkin-publico', 'data-role':'checkin-publico-wrap' }, []);

    const valorUrl = el('div', { class:'kb-checkin-publico-valor kb-checkin-publico-url', title:urlCheckin }, [urlCheckin]);
    const linkAbrir = el('a', {
      class:'kb-btn sm kb-checkin-publico-acao',
      href:urlCheckin,
      target:'_blank',
      rel:'noopener noreferrer'
    }, ['Abrir']);
    const btnCopiarUrl = el('button', { class:'kb-btn sm', type:'button' }, ['Copiar URL']);
    btnCopiarUrl.addEventListener('click', () => copiarTextoParaAreaTransferencia(urlCheckin, 'URL do check-in copiada.'));

    const grid = el('div', { class:'kb-checkin-publico-grid kb-checkin-publico-grid-url-unica' }, [
      el('div', { class:'kb-campo kb-checkin-publico-campo' }, [
        el('div', { class:'kb-campo-label' }, ['URL Check-in']),
        el('div', { class:'kb-checkin-publico-linha' }, [valorUrl, linkAbrir, btnCopiarUrl])
      ])
    ]);

    box.appendChild(grid);

    return box;
  }




function atualizarResumoComercial(bloco, opcoes = {}){
  const kpiCusto = bloco.querySelector('[data-role="kpi-custo"]');
  const kpiPrecoAtual = bloco.querySelector('[data-role="kpi-preco-atual"]');
  const kpiMargemBase = bloco.querySelector('[data-role="kpi-margem-base"]');
  const kpiValorFinal = bloco.querySelector('[data-role="kpi-valor-final"]');
  const kpiMargemFinal = bloco.querySelector('[data-role="kpi-margem-final"]');

  const inputNovoValor = bloco.querySelector('[data-role="input-novo-valor"]');
  const inputPercentual = bloco.querySelector('[data-role="input-percentual"]');

  const precoSel = obterPrecoSelecionadoDoBloco(bloco);
  const custo = Number(bloco.__dadosComerciais?.custo?.Valor);
  const valorBaseTabela = obterValorPrecoTabela(precoSel);

  const novoValorInformado = parseNumeroInput(inputNovoValor?.value);
  const percentualInformado = parseNumeroInput(inputPercentual?.value);

  const origemEdicao = opcoes.origemEdicao || bloco.__origemEdicaoComercial || null;
  const formatarCampos = opcoes.formatarCampos === true;
  const preservarNovoValorEmDigitacao = opcoes.preservarNovoValorEmDigitacao === true;
  const preservarPercentualEmDigitacao = opcoes.preservarPercentualEmDigitacao === true;

  if (kpiCusto && PODE_VER_CUSTO_MARGEM){
    kpiCusto.textContent = Number.isFinite(custo) ? formatarMoedaBR(custo) : '—';
  }

  if (kpiPrecoAtual){
    kpiPrecoAtual.textContent = valorBaseTabela === null ? '—' : formatarMoedaBR(valorBaseTabela);
  }

  const margemBase = valorBaseTabela !== null && Number.isFinite(custo)
    ? (valorBaseTabela - custo)
    : null;

  const margemBasePerc =
    margemBase !== null &&
    valorBaseTabela !== null &&
    valorBaseTabela !== 0
      ? (margemBase / valorBaseTabela) * 100
      : null;

  if (kpiMargemBase && PODE_VER_CUSTO_MARGEM){
    kpiMargemBase.textContent =
      margemBasePerc === null
        ? '—'
        : formatarPercentualBR(margemBasePerc);

    kpiMargemBase.classList.remove('positivo', 'negativo');
    if (margemBase !== null){
      kpiMargemBase.classList.add(margemBase >= 0 ? 'positivo' : 'negativo');
    }
  }

  let valorFinal = null;
  let percentualCalculado = null;
  let novoValorCalculado = null;

  if (origemEdicao === 'novo_valor'){
    if (novoValorInformado !== null) {
      novoValorCalculado = novoValorInformado;
      valorFinal = novoValorInformado;
      percentualCalculado = calcularPercentualDescontoPorNovoValor(valorBaseTabela, novoValorInformado);
    } else if (valorBaseTabela !== null) {
      valorFinal = valorBaseTabela;
    }
  } else if (origemEdicao === 'percentual'){
    if (percentualInformado !== null) {
      percentualCalculado = percentualInformado;
      novoValorCalculado = calcularNovoValorPorPercentual(valorBaseTabela, percentualInformado);
      valorFinal = novoValorCalculado;
    } else if (valorBaseTabela !== null) {
      valorFinal = valorBaseTabela;
    }
  } else {
    if (novoValorInformado !== null) {
      novoValorCalculado = novoValorInformado;
      valorFinal = novoValorInformado;
      percentualCalculado = calcularPercentualDescontoPorNovoValor(valorBaseTabela, novoValorInformado);
      bloco.__origemEdicaoComercial = 'novo_valor';
    } else if (percentualInformado !== null) {
      percentualCalculado = percentualInformado;
      novoValorCalculado = calcularNovoValorPorPercentual(valorBaseTabela, percentualInformado);
      valorFinal = novoValorCalculado;
      bloco.__origemEdicaoComercial = 'percentual';
    } else if (valorBaseTabela !== null) {
      valorFinal = valorBaseTabela;
      bloco.__origemEdicaoComercial = null;
    }
  }

  if (inputNovoValor && !preservarNovoValorEmDigitacao){
    const deveAtualizarNovoValor =
      origemEdicao === 'percentual' ||
      (origemEdicao === null && novoValorCalculado !== null) ||
      formatarCampos;

    if (deveAtualizarNovoValor){
      if (novoValorCalculado === null){
        if (origemEdicao === 'percentual' || formatarCampos) {
          inputNovoValor.value = '';
        }
      } else {
        inputNovoValor.value = formatarValorContabilParaInput(novoValorCalculado);
      }
    } else if (formatarCampos && novoValorInformado !== null){
      inputNovoValor.value = formatarValorContabilParaInput(novoValorInformado);
    }
  }

  if (inputPercentual && !preservarPercentualEmDigitacao){
    const deveAtualizarPercentual =
      origemEdicao === 'novo_valor' ||
      (origemEdicao === null && percentualCalculado !== null) ||
      formatarCampos;

    if (deveAtualizarPercentual){
      if (percentualCalculado === null){
        if (origemEdicao === 'novo_valor' || formatarCampos) {
          inputPercentual.value = '';
        }
      } else {
        inputPercentual.value = formatarPercentualParaInput(percentualCalculado);
      }
    } else if (formatarCampos && percentualInformado !== null){
      inputPercentual.value = formatarPercentualParaInput(percentualInformado);
    }
  }

  const margemFinal = (valorFinal !== null && Number.isFinite(custo))
    ? (valorFinal - custo)
    : null;

  const margemFinalPerc =
    margemFinal !== null &&
    Number.isFinite(valorFinal) &&
    valorFinal !== 0
      ? (margemFinal / valorFinal) * 100
      : null;

  if (kpiValorFinal){
    kpiValorFinal.textContent = valorFinal === null ? '—' : formatarMoedaBR(valorFinal);
  }

  if (kpiMargemFinal && PODE_VER_CUSTO_MARGEM){
    kpiMargemFinal.textContent =
      margemFinalPerc === null
        ? '—'
        : formatarPercentualBR(margemFinalPerc);

    kpiMargemFinal.classList.remove('positivo', 'negativo');
    if (margemFinal !== null){
      kpiMargemFinal.classList.add(margemFinal >= 0 ? 'positivo' : 'negativo');
    }
  }
}






function renderizarComercialBloco(bloco, comercial, valoresSalvos = null){
  const wrap = bloco.querySelector('[data-role="comercial-wrap"]');
  if (!wrap) return;

  let selectPreco = bloco.querySelector('[data-role="select-preco"]');
  if (!selectPreco){
    selectPreco = el('select', { class:'kb-select kb-select-hidden', tabindex:'-1', 'aria-hidden':'true', 'data-role':'select-preco' });
    bloco.appendChild(selectPreco);
  }

  const dataInicioSalva = safeStr(
    valoresSalvos?.DataInicioReserva ||
    valoresSalvos?.data_inicio_reserva ||
    valoresSalvos?.DataInicio ||
    valoresSalvos?.data_inicio ||
    valoresSalvos?.DataInicioPrevisto ||
    valoresSalvos?.data_inicio_previsto ||
    ''
  ).trim();

  const dataFimSalva = safeStr(
    valoresSalvos?.DataFimReserva ||
    valoresSalvos?.data_fim_reserva ||
    valoresSalvos?.DataFim ||
    valoresSalvos?.data_fim ||
    valoresSalvos?.DataTerminoPrevisto ||
    valoresSalvos?.data_termino_previsto ||
    ''
  ).trim();

  const precoVendaAtualContrato = converterValorContratoParaNumero(
    valoresSalvos?.PrecoVendaAtualContrato ??
    valoresSalvos?.preco_venda_atual_contrato ??
    valoresSalvos?.preco_venda_atual ??
    valoresSalvos?.TotalLiquidoContratoAGBRCTACORDO ??
    null
  );

  destruirCalendariosReservaDoBloco(bloco);
  bloco.__dadosComerciais = comercial || null;
  bloco.__dadosContratoAtual = {
    preco_venda_atual: Number.isFinite(precoVendaAtualContrato) ? precoVendaAtualContrato : null,
    data_inicio: dataInicioSalva || null,
    data_fim: dataFimSalva || null
  };

  montarSelectPrecosNoElemento(selectPreco, comercial?.precos || []);
  wrap.innerHTML = '';

  const camposResumoComercialInicial = [];

  if (PODE_VER_CUSTO_MARGEM) {
    camposResumoComercialInicial.push(
      el('div', { class:'kb-campo' }, [
        el('div', { class:'kb-campo-label' }, ['Custo atual']),
        el('div', { class:'kb-kpi', 'data-role':'kpi-custo' }, ['—'])
      ])
    );
  }

  camposResumoComercialInicial.push(
    el('div', { class:'kb-campo' }, [
      el('div', { class:'kb-campo-label' }, ['Valor período']),
      el('div', { class:'kb-kpi', 'data-role':'kpi-preco-atual' }, ['—'])
    ])
  );

  if (PODE_VER_CUSTO_MARGEM) {
    camposResumoComercialInicial.push(
      el('div', { class:'kb-campo' }, [
        el('div', { class:'kb-campo-label' }, ['Margem atual']),
        el('div', { class:'kb-kpi', 'data-role':'kpi-margem-base' }, ['—'])
      ])
    );
  }

  wrap.appendChild(el('div', { class:'kb-painel-item-grid-2' }, camposResumoComercialInicial));

  wrap.appendChild(el('div', { class:'kb-row' }, [
    el('div', { class:'kb-campo grow' }, [
      el('div', { class:'kb-campo-label' }, ['Data de início']),
      el('input', {
        class:'kb-input',
        'data-role':'input-data-inicio',
        type:'text',
        inputmode:'none',
        autocomplete:'off',
        placeholder:'dd/mm/aaaa',
        min: obterDataAtualIsoLocal()
      })
    ]),
    el('div', { class:'kb-campo grow' }, [
      el('div', { class:'kb-campo-label' }, ['Data até']),
      el('input', {
        class:'kb-input',
        'data-role':'input-data-fim',
        type:'text',
        inputmode:'none',
        autocomplete:'off',
        placeholder:'dd/mm/aaaa',
        min: obterDataAtualIsoLocal()
      })
    ])
  ]));

  wrap.appendChild(
    el(
      'div',
      {
        class:'kb-disponibilidade',
        'data-role':'reserva-disponibilidade',
        'data-tipo':'info',
        hidden:'hidden'
      },
      ['Selecione uma face para consultar a disponibilidade do calendário.']
    )
  );

  const camposResumoComercialFinal = [
    el('div', { class:'kb-campo' }, [
      el('div', { class:'kb-campo-label' }, ['Novo valor']),
      el('input', {
        class:'kb-input',
        'data-role':'input-novo-valor',
        type:'text',
        inputmode:'decimal',
        autocomplete:'off',
        placeholder:'Ex: 1.500,00'
      })
    ]),
    el('div', { class:'kb-campo' }, [
      el('div', { class:'kb-campo-label' }, ['Desconto %']),
      el('input', {
        class:'kb-input',
        'data-role':'input-percentual',
        type:'text',
        inputmode:'decimal',
        autocomplete:'off',
        placeholder:'Ex: 10,00'
      })
    ]),
    el('div', { class:'kb-campo' }, [
      el('div', { class:'kb-campo-label' }, ['Valor negociado período']),
      el('div', { class:'kb-kpi', 'data-role':'kpi-valor-final' }, ['—'])
    ])
  ];

  if (PODE_VER_CUSTO_MARGEM) {
    camposResumoComercialFinal.push(
      el('div', { class:'kb-campo' }, [
        el('div', { class:'kb-campo-label' }, ['Margem final']),
        el('div', { class:'kb-kpi', 'data-role':'kpi-margem-final' }, ['—'])
      ])
    );
  }

  wrap.appendChild(el('div', { class:'kb-painel-item-grid-3' }, camposResumoComercialFinal));

  wrap.appendChild(montarBlocoCheckinPublico(valoresSalvos));

  const inputNovoValor = bloco.querySelector('[data-role="input-novo-valor"]');
  const inputPercentual = bloco.querySelector('[data-role="input-percentual"]');
  const inputDataInicio = bloco.querySelector('[data-role="input-data-inicio"]');
  const inputDataFim = bloco.querySelector('[data-role="input-data-fim"]');

  selectPreco.addEventListener('change', () => {
    atualizarResumoComercial(bloco, { formatarCampos: true });
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputNovoValor?.addEventListener('input', () => {
    bloco.__origemEdicaoComercial = 'novo_valor';
    atualizarResumoComercial(bloco, {
      origemEdicao: 'novo_valor',
      preservarNovoValorEmDigitacao: true
    });
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputNovoValor?.addEventListener('blur', () => {
    atualizarResumoComercial(bloco, { origemEdicao: 'novo_valor', formatarCampos: true });
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputPercentual?.addEventListener('input', () => {
    bloco.__origemEdicaoComercial = 'percentual';
    atualizarResumoComercial(bloco, {
      origemEdicao: 'percentual',
      preservarPercentualEmDigitacao: true
    });
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputPercentual?.addEventListener('blur', () => {
    atualizarResumoComercial(bloco, { origemEdicao: 'percentual', formatarCampos: true });
    agendarSincronizacaoFormularioSolicitacao();
  });

  let idPrecoSalvo = 0;
  if (valoresSalvos){
    idPrecoSalvo = idNum(
      valoresSalvos.IDDimTabelaPrecosEuromidia ||
      valoresSalvos.id_preco ||
      valoresSalvos.IDTabelaPreco ||
      0
    );

    const novoValorSalvo =
      valoresSalvos.NovoValor ??
      valoresSalvos.novo_valor ??
      null;

    const percentualSalvo =
      valoresSalvos.PercentualDesconto ??
      valoresSalvos.percentual_desconto ??
      null;

    if (
      novoValorSalvo !== null &&
      novoValorSalvo !== undefined &&
      safeStr(String(novoValorSalvo)).trim() !== ''
    ){
      bloco.__origemEdicaoComercial = 'novo_valor';
    } else if (
      percentualSalvo !== null &&
      percentualSalvo !== undefined &&
      safeStr(String(percentualSalvo)).trim() !== ''
    ){
      bloco.__origemEdicaoComercial = 'percentual';
    } else {
      bloco.__origemEdicaoComercial = null;
    }

    if (inputNovoValor) {
      inputNovoValor.value =
        novoValorSalvo !== null && novoValorSalvo !== undefined
          ? formatarValorContabilParaInput(novoValorSalvo)
          : '';
    }

    if (inputPercentual) {
      inputPercentual.value =
        percentualSalvo !== null && percentualSalvo !== undefined
          ? formatarPercentualParaInput(percentualSalvo)
          : '';
    }

    if (inputDataInicio) {
      inputDataInicio.value = dataInicioSalva || '';
    }

    if (inputDataFim) {
      inputDataFim.value = dataFimSalva || '';
      inputDataFim.min = obterMaiorDataIso(obterDataAtualIsoLocal(), dataInicioSalva) || obterDataAtualIsoLocal();
    }

    if (
      novoValorSalvo !== null &&
      novoValorSalvo !== undefined &&
      safeStr(String(novoValorSalvo)).trim() !== ''
    ){
      inputPercentual.value = '';
    }
  }

  sincronizarSeletoresTabelaPrecoDoBloco(bloco, { id_preco: idPrecoSalvo || null });
  sincronizarRestricoesDataFimDoBloco(bloco);
  atualizarResumoComercial(bloco);
  void inicializarCalendarioReservaDoBloco(bloco, valoresSalvos);
}


  function coletarPainelFacesDoFormulario(){
    const itens = [];

    for (const bloco of (painelFaceLista?.querySelectorAll('.kb-painel-item') || [])){
      const idPainel = idNum(bloco.querySelector('[data-role="select-painel"]')?.value || 0);
      const painelFace = obterPainelFaceSelecionadoDoBloco(bloco) || null;
      const codFace = safeStr(bloco.querySelector('[data-role="select-face"]')?.value || '').trim().toUpperCase();
      const periodoSelecionado = safeStr(bloco.querySelector('[data-role="select-periodo-exibicao"]')?.value || '').trim();
      const idPreco = idNum(bloco.querySelector('[data-role="select-preco"]')?.value || 0) || null;
      const novoValor = parseNumeroInput(bloco.querySelector('[data-role="input-novo-valor"]')?.value);
      const percentual = parseNumeroInput(bloco.querySelector('[data-role="input-percentual"]')?.value);
      const dataInicio = normalizarDataParaInput(bloco.querySelector('[data-role="input-data-inicio"]')?.value || '');
      const dataFim = normalizarDataParaInput(bloco.querySelector('[data-role="input-data-fim"]')?.value || '');
      const idReserva = obterIdReservaDoBloco(bloco);
      const exibicoesParaGerar = obterExibicoesDiaSelecionadasDoBloco(bloco);

      if (!idReserva && !idPainel && !codFace && !idPreco && !exibicoesParaGerar.some(Boolean) && novoValor === null && percentual === null && !dataInicio && !dataFim) continue;

      const precoVendaAtualContrato = Number(bloco.__dadosContratoAtual?.preco_venda_atual);
      const itemContratoAditivo = bloco.__itemContratoAditivoSelecionado || null;
      const idItemContratoAditivo = idNum(itemContratoAditivo?.id_item_contrato ?? itemContratoAditivo?.IDFatoControleContratosItensEuromidia ?? 0) || null;
      const codPontoContratoAditivo = safeStr(itemContratoAditivo?.cod_ponto ?? itemContratoAditivo?.CodPonto ?? "").trim() || null;
      const codFaceContratoAditivo = safeStr(itemContratoAditivo?.cod_face ?? itemContratoAditivo?.CodFace ?? "").trim().toUpperCase() || null;

      for (const exibicoesDia of exibicoesParaGerar) {
        const dadosComerciais = calcularDadosComerciaisPainelFacePorExibicao(bloco, exibicoesDia, periodoSelecionado);
        const idPrecoFinal = idNum(dadosComerciais?.id_preco || 0) || idPreco;

        itens.push({
          IDDimPaineisEuromidia: idPainel || null,
          id_painel: idPainel || null,
          IDDimFacesPaineis: idNum(painelFace?.IDDimFacesPaineis ?? 0) || null,
          id_face: idNum(painelFace?.IDDimFacesPaineis ?? 0) || null,
          CodPonto: safeStr(painelFace?.CodPonto || "").trim() || null,
          cod_ponto: safeStr(painelFace?.CodPonto || "").trim() || null,
          CodFace: codFace || null,
          cod_face: codFace || null,
          IDFatoOcupacaoPaineisEuromidia: idReserva || null,
          id_reserva: idReserva || null,
          Reserva: idReserva || null,
          TipoPainel: safeStr(painelFace?.Tipo || "").trim() || null,
          tipo_painel: safeStr(painelFace?.Tipo || "").trim() || null,
          IDDimTabelaPrecosEuromidia: idPrecoFinal || null,
          id_preco: idPrecoFinal || null,
          PeriodoExibicao: dadosComerciais.periodo_exibicao || periodoSelecionado || null,
          periodo_exibicao: dadosComerciais.periodo_exibicao || periodoSelecionado || null,
          ExibicoesDia: dadosComerciais.exibicoes_dia || null,
          exibicoes_dia: dadosComerciais.exibicoes_dia || null,
          Cota: dadosComerciais.Cota || dadosComerciais.cota || dadosComerciais.exibicoes_dia || null,
          cota: dadosComerciais.Cota || dadosComerciais.cota || dadosComerciais.exibicoes_dia || null,
          ValorTabela: dadosComerciais.valor_tabela,
          valor_tabela: dadosComerciais.valor_tabela,
          ValorVendaFinal: dadosComerciais.valor_venda_final,
          valor_venda_final: dadosComerciais.valor_venda_final,
          NovoValor: dadosComerciais.novo_valor,
          novo_valor: dadosComerciais.novo_valor,
          PercentualDesconto: dadosComerciais.percentual_desconto,
          percentual_desconto: dadosComerciais.percentual_desconto,
          preco_venda_atual_contrato: Number.isFinite(precoVendaAtualContrato) ? precoVendaAtualContrato : null,
          data_inicio: dataInicio || null,
          data_fim: dataFim || null,
          origem_aditivo: itemContratoAditivo ? "ITEM_CONTRATO_EXISTENTE" : null,
          id_item_contrato_aditivo: idItemContratoAditivo,
          cod_ponto_contrato_aditivo: codPontoContratoAditivo,
          cod_face_contrato_aditivo: codFaceContratoAditivo,
        });
      }
    }

    return itens;
  }

  function preencherPainelFacesDoCard(lista, opcoes = {}){
    if (!painelFaceLista) return;
    painelFaceLista.innerHTML = '';

    /*
     * Regra crítica: não injeto IDReserva do card nos blocos de painel/face.
     * O campo Reserva só pode aparecer preenchido quando vier no próprio item,
     * ou seja, quando o usuário realmente digitou/selecionou uma reserva.
     * Isso evita o bug de abrir/mover para a fase 4 e o sistema inventar uma reserva.
     */
    const arr = Array.isArray(lista) && lista.length
      ? lista.map((item) => limparReservaNaoInformadaPeloUsuarioKanban(item))
      : [null];

    arr.forEach(item => painelFaceLista.appendChild(criarPainelFaceItem(item)));
    atualizarTitulosPainelFace();
    sincronizarSeletoresContratoAditivoEmTodosBlocos();
  }

  function ativarPainelFaceSeNecessario(){
    if (!painelFaceWrap) return;
    const on = !!(kanbanCfg && kanbanCfg.MostrarPainelFaceNoCard);
    painelFaceWrap.classList.toggle('is-on', on);
    if (!on) return;

    paineisPorId = new Map();
    for (const p of (paineisCatalogo || [])){
      const id = Number(p?.IDDimPaineisEuromidia || p?.ID || 0);
      if (id > 0) paineisPorId.set(id, p);
    }

    atualizarMapaPainelFacesCatalogo();

    if (!(painelFaceLista?.querySelector('.kb-painel-item'))) {
      preencherPainelFacesDoCard([]);
    }
  }




function montarUrlPorTemplate(template, marcador, valor, parametros = {}){
  const valorSeguro = encodeURIComponent(String(valor || "").trim());
  const templateSeguro = safeStr(template || "").trim();
  const caminhoBase = templateSeguro
    ? templateSeguro.replace(marcador, valorSeguro)
    : "";

  const pares = [];
  Object.entries(parametros || {}).forEach(([chave, valorParametro]) => {
    if (valorParametro === undefined || valorParametro === null || valorParametro === false || valorParametro === "") return;
    pares.push(`${encodeURIComponent(chave)}=${encodeURIComponent(String(valorParametro))}`);
  });

  return pares.length ? `${caminhoBase}?${pares.join("&")}` : caminhoBase;
}

function montarUrlApiCardDetalhe(idCard, opcoes = {}){
  const id = idNum(idCard);
  const template = URL_API_CARD_DETALHE_TEMPLATE || "/kanban/api/cards/__ID_CARD__";
  const parametros = { fresh: opcoes.fresh ? 1 : "" };

  // Quando peço fresh, também mando um cache-buster. Isso evita o navegador/proxy
  // devolver detalhe antigo do card e causar falso conflito de VersaoConcorrencia.
  if (opcoes.fresh) {
    parametros._ts = Date.now();
  }

  return montarUrlPorTemplate(template, "__ID_CARD__", id, parametros);
}

async function buscarDetalheCard(idCard, opcoes = {}) {
  const id = idNum(idCard);
  if (!id) return null;

  const url = montarUrlApiCardDetalhe(id, opcoes);
  const headers = opcoes.fresh
    ? { "Cache-Control": "no-cache", "Pragma": "no-cache" }
    : {};

  try {
    const resultado = await fetchJsonKanban(url, {
      headers,
      timeoutMs: Number(opcoes.timeoutMs || opcoes.timeout_ms || 0) || TIMEOUT_FETCH_PADRAO_KANBAN_MS
    });
    const j = resultado.corpo;

    if (!respostaJsonKanbanOk(resultado)) {
      console.warn("buscarDetalheCard: resposta inválida", detalhesFalhaJsonKanban(resultado));
      return null;
    }

    return j;
  } catch (erro) {
    console.warn("buscarDetalheCard: falhou", erro);
    return null;
  }
}


function extrairVersaoConcorrenciaCard(card) {
  if (!card || typeof card !== "object") return "";

  return safeStr(
    card.VersaoConcorrenciaHex ||
    card.VersaoConcorrencia ||
    card.versao_concorrencia ||
    card.versaoConcorrencia ||
    card.VersaoConcorrenciaHexSql ||
    ""
  ).trim();
}

function aplicarVersaoConcorrenciaCardAberto(card, origem = "") {
  if (!card || typeof card !== "object") return false;

  const idCardVersao = idNum(
    card.IDFatoKanbanCard ??
    card.id_card ??
    card.idCard ??
    card.IDCard ??
    0
  );

  if (!idCardVersao || idNum(cardAbertoId) !== idCardVersao) return false;
  if (!modalCard || modalCard.style.display !== "block") return false;

  const versaoNova = extrairVersaoConcorrenciaCard(card);
  if (!versaoNova) return false;

  if (versaoNova !== safeStr(versaoConcorrenciaCardAberto)) {
    console.debug("VersaoConcorrencia do card aberto sincronizada.", {
      id_card: idCardVersao,
      origem: origem || null,
      versao_anterior: safeStr(versaoConcorrenciaCardAberto),
      versao_nova: versaoNova
    });
  }

  versaoConcorrenciaCardAberto = versaoNova;
  return true;
}

function ehRespostaConcorrenciaCard(resposta, corpo) {
  const status = idNum(resposta?.status || 0);
  if (status !== 409) return false;

  const msg = safeStr(corpo?.msg || corpo?.erro || "").toLowerCase();
  return !!(
    corpo?.card_atual ||
    corpo?.motivo === "versao_concorrencia_desatualizada" ||
    msg.includes("concorr") ||
    msg.includes("alterado") ||
    msg.includes("outra operação") ||
    msg.includes("outra operacao")
  );
}

async function atualizarVersaoConcorrenciaParaRetrySalvar(idCard, corpoConflito = null) {
  const idC = idNum(idCard);
  if (!idC) {
    return { ok: false, msg: "Card inválido para atualizar a versão de concorrência." };
  }

  if (cardAbertoConflitoExterno) {
    return { ok: false, msg: "Existe uma atualização externa marcada para este card. Reabra o card antes de salvar." };
  }

  let detalhe = null;

  try {
    detalhe = await buscarDetalheCard(idC, { fresh: true, timeoutMs: 60000 });
  } catch (erro) {
    console.warn("Falha ao buscar detalhe fresh para retry de concorrência.", erro);
  }

  if (!detalhe || !detalhe.card) {
    const cardAtualConflito = corpoConflito?.card_atual ? normalizarCardServidor(corpoConflito.card_atual) : null;
    if (cardAtualConflito) {
      detalhe = { card: cardAtualConflito, tags: Array.isArray(corpoConflito?.tags) ? corpoConflito.tags : null };
    }
  }

  if (!detalhe || !detalhe.card) {
    return { ok: false, msg: "Não consegui recarregar a versão atual do card para salvar novamente." };
  }

  const cardServidor = normalizarCardServidor(detalhe.card);
  const idCardServidor = idNum(cardServidor.IDFatoKanbanCard || 0);
  if (idCardServidor !== idC) {
    return { ok: false, msg: "O servidor devolveu outro card ao tentar sincronizar a versão." };
  }

  const faseServidor = idNum(cardServidor.IDDimKanbanFaseAtual || 0);
  const faseModal = idNum(modalCard?.dataset?.idFaseAtual || obterCardPorId(idC)?.IDDimKanbanFaseAtual || 0);
  if (faseModal && faseServidor && faseModal !== faseServidor) {
    inserirOuAtualizarCardLocal(cardServidor);
    cardAbertoConflitoExterno = true;
    return {
      ok: false,
      msg: "O card mudou de fase enquanto estava aberto. Reabra o card antes de salvar para não gravar dados na fase errada."
    };
  }

  inserirOuAtualizarCardLocal(cardServidor);

  if (Array.isArray(detalhe.tags)) {
    setTagsDoCard(idC, detalhe.tags);
  }

  if (Array.isArray(detalhe.notas)) {
    mapaNotasPorCard.set(
      idC,
      detalhe.notas.map(n => Object.assign({}, n || {}))
    );
  }

  const versaoNova = extrairVersaoConcorrenciaCard(cardServidor);
  if (!versaoNova) {
    return { ok: false, msg: "O backend ainda não devolveu VersaoConcorrencia para este card." };
  }

  aplicarVersaoConcorrenciaCardAberto(cardServidor, "retry_salvar_409");
  return { ok: true, versao: versaoNova, card: cardServidor };
}

  async function enriquecerCardsCarregados(cardsNovos, mapaTagsLote = null){
    const lista = Array.isArray(cardsNovos) ? cardsNovos : [];
    if (!lista.length) return;

    const mapaTags = mapaTagsLote instanceof Map ? mapaTagsLote : new Map();
    let alterouTags = false;

    lista.forEach(card => {
      const idCard = idNum(card?.IDFatoKanbanCard);
      if (!idCard) return;

      const tagsInline = Array.isArray(card?.tags)
        ? card.tags
        : (Array.isArray(card?.Tags) ? card.Tags : null);

      if (Array.isArray(tagsInline)) {
        setTagsDoCard(idCard, tagsInline, { reconstruir: false });
        alterouTags = true;
        return;
      }

      if (mapaTags.has(idCard)) {
        setTagsDoCard(idCard, mapaTags.get(idCard) || [], { reconstruir: false });
        alterouTags = true;
      }
    });

    if (alterouTags) {
      reconstruirIndicesCards();
    }
  }




async function carregarLoteServidorDaFase(idFase, limite = TAM_LOTE_POR_FASE, opcoes = {}) {
  const idF = idNum(idFase);
  const st = estadoFase.get(idF);
  if (!st || st.carregandoServidor || st.cargaCompleta) return 0;

  st.carregandoServidor = true;

  try {
    const offset = idNum(st.offsetServidor || 0);
    const limit = Math.max(1, Math.min(idNum(limite) || TAM_LOTE_POR_FASE, 100));
    const fresh = !!opcoes.fresh;
    const rapido = !!opcoes.rapido;

    const params = new URLSearchParams();
    params.set("id_fase", String(idF));
    params.set("offset", String(offset));
    params.set("limit", String(limit));
    if (fresh) params.set("fresh", "1");
    if (rapido) params.set("rapido", "1");

    const url = `/kanban/api/kanbans/${ID_KANBAN}/cards?${params.toString()}`;

    const r = await fetchKanbanComTimeout(url, { credentials: "same-origin" }, TIMEOUT_FETCH_PADRAO_KANBAN_MS);
    const j = await r.json().catch(() => null);

    if (!r.ok || !j || !j.ok) {
      console.warn("carregarLoteServidorDaFase falhou", {
        idFase: idF,
        http: r.status,
        body: j,
        fresh
      });
      return 0;
    }

    const mapaTagsLote = new Map();
    const cardTagsLote = Array.isArray(j.card_tags) ? j.card_tags : [];
    cardTagsLote.forEach(item => {
      const idCard = idNum(item?.IDFatoKanbanCard);
      if (!idCard) return;
      const arr = mapaTagsLote.get(idCard) || [];
      arr.push(Object.assign({}, item || {}));
      mapaTagsLote.set(idCard, arr);
    });

    const cardsNovos = Array.isArray(j.cards) ? j.cards.map(normalizarCardServidor) : [];
    cardsNovos.forEach(card => inserirOuAtualizarCardLocal(card, { reconstruir: false }));

    if (typeof j.total !== "undefined") {
      const fase = fasePorId(idF);
      if (fase) {
        fase.QuantidadeCardsTotal = idNum(j.total);
        fase.CargaTotalDesconhecida = false;
      }
      st.totalServidor = idNum(j.total);
      st.cargaTotalDesconhecida = false;
    }

    st.offsetServidor = offset + cardsNovos.length;
    st.cargaCompleta = st.offsetServidor >= idNum(st.totalServidor);

    if (cardsNovos.length) {
      await enriquecerCardsCarregados(cardsNovos, mapaTagsLote);
    }

    reconstruirIndicesCards();
    return cardsNovos.length;
  } finally {
    st.carregandoServidor = false;
  }
}




  resetarFluxoContrato();

  async function processarComConcorrenciaKanban(itens, limite, worker){
    const lista = Array.isArray(itens) ? itens.slice() : [];
    const concorrencia = Math.max(1, Math.min(idNum(limite) || 2, 6));
    let indice = 0;

    const trabalhadores = Array.from({ length: Math.min(concorrencia, lista.length || 1) }, async () => {
      while (indice < lista.length) {
        const item = lista[indice++];
        await worker(item);
      }
    });

    await Promise.all(trabalhadores);
  }

  function aplicarCatalogosBasicosKanban(payload = {}){
    const j = payload || {};

    if (j.kanban_cfg) {
      kanbanCfg = j.kanban_cfg || {};
    }

    if (Array.isArray(j.tags)) {
      tagsCatalogo = j.tags.map(t => Object.assign({}, t || {}));
    }

    if (Array.isArray(j.vendedores)) {
      vendedoresCatalogo = j.vendedores.map(v => Object.assign({}, v || {}));
    }

    if (Array.isArray(j.tipos_cliente_desconto)) {
      tiposClienteDescontoCatalogo = j.tipos_cliente_desconto.map(t => Object.assign({}, t || {}));
      tiposClienteDescontoPorId = new Map();
      tiposClienteDescontoCatalogo.forEach(item => {
        const idTipo = idNum(item?.IDDimKanbanTipoClienteDesconto || item?.IDDimTipoCliente || 0);
        if (idTipo) tiposClienteDescontoPorId.set(idTipo, item);
      });
      montarSelectTipoClienteDesconto();
    }

    if (Array.isArray(j.origens_atendimento)) {
      origensAtendimentoCatalogo = j.origens_atendimento.map(t => Object.assign({}, t || {}));
      origensAtendimentoPorId = new Map();
      origensAtendimentoCatalogo.forEach(item => {
        const idOrigem = idNum(item?.IDDimOrigemAtendimento || 0);
        if (idOrigem) origensAtendimentoPorId.set(idOrigem, item);
      });
      montarSelectOrigemAtendimento();
    }

    if (Array.isArray(j.tipos_documento)) {
      tiposDocumentoCatalogo = j.tipos_documento.map(t => Object.assign({}, t || {}));
      tiposDocumentoPorId = new Map();
      tiposDocumentoCatalogo.forEach(item => {
        const idTipoDocumento = idNum(item?.IDDimTipoDocumento || 0);
        if (idTipoDocumento) tiposDocumentoPorId.set(idTipoDocumento, item);
      });
    }

    if (Array.isArray(j.paineis)) {
      paineisCatalogo = j.paineis;
    }

    if (idNum(j.versao_kanban || 0)) {
      versaoKanbanAtual = idNum(j.versao_kanban || versaoKanbanAtual || 0);
    }

    ativarPainelFaceSeNecessario();
    renderizarFiltrosMultiselect();

    if (j.vendedores_deferido) {
      window.setTimeout(() => {
        if (!document.hidden) carregarVendedoresKanbanDeferido();
      }, 1200);
    }

    const deveCarregarCatalogoPainelFaces = !!(kanbanCfg && kanbanCfg.MostrarPainelFaceNoCard);
    if (deveCarregarCatalogoPainelFaces) {
      carregarCatalogoPainelFaces().catch((erro) => {
        console.warn('catalogos painel_faces em segundo plano: falhou', erro);
      });
    }
  }

  async function carregarCatalogosBasicosKanban(){
    const resultado = await fetchJsonKanban(`/kanban/api/kanbans/${ID_KANBAN}/catalogos`);
    const j = resultado.corpo;

    if (!respostaJsonKanbanOk(resultado)) {
      console.warn("catalogos do kanban falharam", detalhesFalhaJsonKanban(resultado));
      return false;
    }

    aplicarCatalogosBasicosKanban(j);
    return true;
  }

  async function carregarPrimeirosCardsPorFaseRapido(){
    const idsFases = fases
      .map(f => idNum(f?.IDDimKanbanFase || 0))
      .filter(Boolean);

    if (!idsFases.length) return;

    await processarComConcorrenciaKanban(idsFases, 3, async (idFase) => {
      if (document.hidden) return;

      await preencherCardsInicial(idFase, TAM_LOTE_POR_FASE, false, {
        ignorarAutoPreenchimento: true,
        rapido: true,
      });

      atualizarResumoBusca();
    });

    atualizarResumoBusca();
    renderizarFiltrosMultiselect();
    agendarPrecarregamentoFasesOciosas(900);
    agendarRecarregarResumoComercial(1200);
  }

  async function carregarEstruturaRapidaKanban(){
    try {
      const resultadoFases = await fetchJsonKanban(`/kanban/api/kanbans/${ID_KANBAN}/fases`);
      const jFases = resultadoFases.corpo;

      if (!respostaJsonKanbanOk(resultadoFases) || !Array.isArray(jFases?.fases)) {
        console.warn("estrutura rápida do kanban falhou", detalhesFalhaJsonKanban(resultadoFases));
        return false;
      }

      fases = jFases.fases.map(f => Object.assign({}, f || {}, {
        QuantidadeCardsTotal: 0,
        QuantidadeCardsCarregadosInicialmente: 0,
        QuantidadeCardsRestantes: 0,
        CargaInicialCompleta: false,
        CargaTotalDesconhecida: true,
      }));

      cards = [];
      mapaTagsPorCard = new Map();
      mapaNotasPorCard = new Map();
      filtrosCardsKanban = [];

      reconstruirIndicesCards();
      renderizarFiltrosMultiselect();
      renderBoardCompleto({ adiarCargaInicial: true });
      atualizarResumoBusca();

      ultimaAtualizacaoAoVivoMs = Date.now();

      carregarCatalogosBasicosKanban().catch((erro) => {
        console.warn("Catálogos básicos do Kanban falharam em segundo plano", erro);
      });

      carregarPrimeirosCardsPorFaseRapido().catch((erro) => {
        console.warn("Carga rápida dos primeiros cards falhou; tentando carga completa", erro);
        carregar({ fresh: true }).catch((erroCompleto) => {
          console.error("Carga completa de fallback também falhou", erroCompleto);
          mostrarMensagemBoard("Não consegui carregar os cards. Confira no DevTools qual requisição ficou vermelha na aba Network.");
        });
      });

      return true;
    } catch (erro) {
      console.warn("Falha na abertura rápida do Kanban", erro);
      return false;
    }
  }

  async function carregar(opcoes = {}) {
    const paramsDados = new URLSearchParams();
    paramsDados.set("limit_inicial", String(TAM_LOTE_POR_FASE));
    if (opcoes && opcoes.fresh === true) {
      paramsDados.set("fresh", "1");
    }

    const urlDados = `/kanban/api/kanbans/${ID_KANBAN}/dados?${paramsDados.toString()}`;
    let resultadoDados = null;

    try {
      resultadoDados = await fetchJsonKanban(urlDados);
    } catch (erro) {
      console.error("Falha de rede ao carregar dados iniciais do kanban", erro);
      mostrarMensagemBoard("Não consegui carregar os cards. A resposta do servidor foi interrompida antes de terminar. Recarregue a página e veja o log do servidor para identificar se houve timeout, restart do gunicorn ou resposta grande demais.");
      throw erro;
    }

    const j = resultadoDados.corpo;

    if (!respostaJsonKanbanOk(resultadoDados)) {
      console.warn("carregar dados iniciais do kanban falhou", detalhesFalhaJsonKanban(resultadoDados));
      mostrarMensagemBoard(j?.msg || "Não consegui carregar os cards. A API inicial do kanban não retornou JSON válido.");
      return;
    }

    versaoKanbanAtual = idNum(j.versao_kanban || j.VersaoKanban || versaoKanbanAtual || 0);
    ultimaAtualizacaoAoVivoMs = Date.now();

    fases = Array.isArray(j.fases) ? j.fases.map(f => Object.assign({}, f || {})) : [];
    cards = Array.isArray(j.cards)
      ? j.cards.map(normalizarCardServidor).filter(cardPertenceAoVendedorLogado)
      : [];
    tagsCatalogo = Array.isArray(j.tags) ? j.tags.map(t => Object.assign({}, t || {})) : [];
    vendedoresCatalogo = Array.isArray(j.vendedores) ? j.vendedores.map(v => Object.assign({}, v || {})) : [];
    tiposClienteDescontoCatalogo = Array.isArray(j.tipos_cliente_desconto) ? j.tipos_cliente_desconto.map(t => Object.assign({}, t || {})) : [];
    tiposClienteDescontoPorId = new Map();
    tiposClienteDescontoCatalogo.forEach(item => {
      const idTipo = idNum(item?.IDDimKanbanTipoClienteDesconto || item?.IDDimTipoCliente || 0);
      if (idTipo) tiposClienteDescontoPorId.set(idTipo, item);
    });

    origensAtendimentoCatalogo = Array.isArray(j.origens_atendimento) ? j.origens_atendimento.map(t => Object.assign({}, t || {})) : [];
    origensAtendimentoPorId = new Map();
    origensAtendimentoCatalogo.forEach(item => {
      const idOrigem = idNum(item?.IDDimOrigemAtendimento || 0);
      if (idOrigem) origensAtendimentoPorId.set(idOrigem, item);
    });

    tiposDocumentoCatalogo = Array.isArray(j.tipos_documento) ? j.tipos_documento.map(t => Object.assign({}, t || {})) : [];
    tiposDocumentoPorId = new Map();
    tiposDocumentoCatalogo.forEach(item => {
      const idTipoDocumento = idNum(item?.IDDimTipoDocumento || 0);
      if (idTipoDocumento) tiposDocumentoPorId.set(idTipoDocumento, item);
    });

    kanbanCfg = j.kanban_cfg || {};
    paineisCatalogo = Array.isArray(j.paineis) ? j.paineis : [];
    const deveCarregarCatalogoPainelFaces = !!(kanbanCfg && kanbanCfg.MostrarPainelFaceNoCard);
    if (!deveCarregarCatalogoPainelFaces) {
      painelFacesCatalogo = [];
      atualizarMapaPainelFacesCatalogo();
    }
    renderizarResumoComercial(j.resumo_comercial || null);

    ativarPainelFaceSeNecessario();
    montarSelectTipoClienteDesconto();
    montarSelectOrigemAtendimento();

    mapaTagsPorCard = new Map();
    mapaNotasPorCard = new Map();
    (j.card_tags || []).forEach(ct => {
      const idCard = idNum(ct.IDFatoKanbanCard);
      const arr = mapaTagsPorCard.get(idCard) || [];
      arr.push(ct);
      mapaTagsPorCard.set(idCard, arr);
    });

    filtrosCardsKanban = Array.isArray(j.filtros_cards)
      ? j.filtros_cards.map(normalizarFiltroCardKanban).filter(item => idNum(item.IDFatoKanbanCard))
      : [];

    if (!filtrosCardsKanban.length) {
      filtrosCardsKanban = cards.map(card => normalizarFiltroCardKanban(Object.assign({}, card, {
        Tags: tagsDoCard(card.IDFatoKanbanCard)
      })));
    }

    reconstruirIndicesCards();
    renderizarFiltrosMultiselect();
    renderBoardCompleto();
    atualizarResumoBusca();

    if (j.vendedores_deferido) {
      window.setTimeout(() => {
        if (!document.hidden) carregarVendedoresKanbanDeferido();
      }, 1400);
    }

    // Performance: o resumo comercial não segura mais a abertura do quadro.
    // A tela aparece primeiro; os KPIs vêm logo em seguida por endpoint próprio/cacheado.
    agendarRecarregarResumoComercial(1200);

    if (deveCarregarCatalogoPainelFaces) {
      carregarCatalogoPainelFaces().catch((erro) => {
        console.warn('init painel_faces em segundo plano: falhou', erro);
      });
    }
  }

  function renderBoardCompleto(opcoes = {}) {
    const adiarCargaInicial = !!opcoes.adiarCargaInicial;

    reconstruirIndicesCards();
    limparRecursosScrollbarFase();
    board.innerHTML = "";
    estadoFase.clear();

    fases.forEach((f, idx) => {
      const idFase = idNum(f.IDDimKanbanFase);
      const cargaTotalDesconhecida = f.CargaTotalDesconhecida === true;
      const qtdTotalServidor = cargaTotalDesconhecida
        ? Math.max(0, contarCardsNaFaseCarregados(idFase))
        : idNum(f.QuantidadeCardsTotal || contarCardsNaFaseCarregados(idFase));
      const qtdCarregadaInicial = idNum(f.QuantidadeCardsCarregadosInicialmente || contarCardsNaFaseCarregados(idFase));
      const colAccent = obterCorFase(f, idx);
      const colText = obterCorTextoFase(f, colAccent);

      const col = el("div", {class:"kb-col", "data-fase": idFase});
      col.style.setProperty("--col-accent", colAccent);
      col.style.setProperty("--col-head-bg", colAccent);
      col.style.setProperty("--col-text", colText);

      const botoesAcaoFase = [];

      if (USUARIO_PODE_GERENCIAR_FASES_E_TAGS) {
        botoesAcaoFase.push(
          el("button", {class:"kb-col-edit", title:"Editar fase", onclick: () => abrirModalEditarFase(idFase)}, ["✎"]),
          el("button", {class:"kb-col-del", title:"Inativar fase", onclick: () => abrirModalInativarFase(idFase)}, ["−"])
        );
      }

      botoesAcaoFase.push(
        el("button", {class:"kb-add", title:"Adicionar card", onclick: () => criarCardPrompt(idFase)}, ["+"])
      );

      const head = el("div", {class:"kb-col-head"}, [
        el("div", {class:"kb-col-title"}, [
          el("strong", {title: f.NomeFase || ""}, [`${(f.NomeFase || "—")} (${qtdTotalServidor})`]),
          el("div", {class:"kb-col-sub"}, [
            el("span", {class:"kb-count"}, [String(f.TipoFase || "").toLowerCase()])
          ])
        ]),
        el("div", {class:"kb-col-actions"}, botoesAcaoFase)
      ]);

      const body = el("div", {class:"kb-col-body"}, []);
      const drop = el("div", {class:"kb-drop", "data-drop":"1", "data-fase": idFase}, []);
      const spacerTopo = el("div", {class:"kb-virtual-spacer kb-virtual-spacer-top", "aria-hidden":"true", style:"height:0px;display:none;"}, []);
      const spacerBase = el("div", {class:"kb-virtual-spacer kb-virtual-spacer-bottom", "aria-hidden":"true", style:"height:0px;display:none;"}, []);
      const sentinel = el("div", {class:"kb-loadmore", style:"display:none;", role:"button", tabindex:"0"}, ["Carregando..."]);

      drop.appendChild(spacerTopo);
      drop.appendChild(spacerBase);
      drop.appendChild(sentinel);
      body.appendChild(drop);
      configurarScrollbarFase(drop, body);
      col.appendChild(head);
      col.appendChild(body);
      board.appendChild(col);

      estadoFase.set(idFase, {
        visiveis: TAM_LOTE_POR_FASE,
        rendered: 0,
        dropEl: drop,
        sentinelEl: sentinel,
        spacerTopoEl: spacerTopo,
        spacerBaseEl: spacerBase,
        loading: false,
        carregandoServidor: false,
        colAccent,
        dragDepth: 0,
        offsetServidor: qtdCarregadaInicial,
        totalServidor: qtdTotalServidor,
        cargaTotalDesconhecida: cargaTotalDesconhecida,
        cargaCompleta: cargaTotalDesconhecida ? false : (!!f.CargaInicialCompleta || qtdCarregadaInicial >= qtdTotalServidor)
      });

      drop.addEventListener("dragenter", (e) => {
        const cardIdEvento = idNum(cardArrastandoId || e.dataTransfer?.getData("text/plain") || 0);
        if (!cardIdEvento) return;

        e.preventDefault();

        const st = estadoFase.get(idFase);
        if (!st) return;

        st.dragDepth = (st.dragDepth || 0) + 1;
        drop.classList.add("is-over");
      });

      drop.addEventListener("dragover", (e) => {
        const cardIdEvento = idNum(cardArrastandoId || 0);
        if (!cardIdEvento) return;

        e.preventDefault();

        if (e.dataTransfer) {
          e.dataTransfer.dropEffect = "move";
        }

        if (!drop.classList.contains("is-over")) {
          drop.classList.add("is-over");
        }
      });

      drop.addEventListener("dragleave", () => {
        const st = estadoFase.get(idFase);
        if (!st) return;

        st.dragDepth = Math.max(0, (st.dragDepth || 0) - 1);
        if (st.dragDepth === 0) {
          drop.classList.remove("is-over");
        }
      });

      drop.addEventListener("drop", async (e) => {
        e.preventDefault();

        const st = estadoFase.get(idFase);
        if (st) st.dragDepth = 0;
        drop.classList.remove("is-over");

        const cardId = idNum(cardArrastandoId || e.dataTransfer?.getData("text/plain") || 0);
        if (!cardId) {
          encerrarModoDrag();
          return;
        }

        try {
          await moverCard(cardId, idFase, "LAST");
        } finally {
          encerrarModoDrag();
        }
      });

      drop.addEventListener("scroll", () => {
        const st = estadoFase.get(idFase);
        if (!st) return;

        agendarAtualizacaoJanelaVirtualFase(idFase);

        if (st.loading) return;

        const temOverflow = (drop.scrollHeight - drop.clientHeight) > 20;
        if (!temOverflow) return;

        const nearBottom = (drop.scrollTop + drop.clientHeight) >= (drop.scrollHeight - 160);
        if (!nearBottom) return;

        void carregarMaisCardsDaFase(idFase);
      }, { passive: true });

      sentinel.addEventListener("click", () => {
        void carregarMaisCardsDaFase(idFase);
      });

      sentinel.addEventListener("keydown", (evento) => {
        if (evento.key === "Enter" || evento.key === " ") {
          evento.preventDefault();
          void carregarMaisCardsDaFase(idFase);
        }
      });
    });

    fases.forEach(f => {
      const idFase = idNum(f.IDDimKanbanFase);
      preencherCabecalhoFase(idFase);
      // Performance: não faço auto-preenchimento agressivo na primeira pintura.
      // Antes, fases sem overflow podiam disparar várias chamadas /cards em paralelo logo na abertura.
      if (!adiarCargaInicial) {
        void preencherCardsInicial(idFase, TAM_LOTE_POR_FASE, false, { ignorarAutoPreenchimento: true });
      } else {
        atualizarSentinelaFase(idFase);
      }
    });

    // Depois que a primeira pintura acontece, faço um pré-carregamento leve, sequencial e cancelável
    // para deixar a rolagem futura mais rápida sem travar a abertura do Kanban.
    if (!adiarCargaInicial) {
      agendarPrecarregamentoFasesOciosas();
    }

    removerControlesGestaoFasesParaVendedor();
    atualizarModoDesempenhoKanban();
  }

  let temporizadorPrecarregamentoFases = null;

  function agendarPrecarregamentoFasesOciosas(atrasoMs = 700){
    if (temporizadorPrecarregamentoFases) {
      window.clearTimeout(temporizadorPrecarregamentoFases);
    }

    temporizadorPrecarregamentoFases = window.setTimeout(() => {
      temporizadorPrecarregamentoFases = null;
      void precarregarFasesOciosasSequencialmente();
    }, Math.max(250, Number(atrasoMs) || 700));
  }

  async function esperarJanelaOciosaKanban(timeout = 900){
    if (typeof window.requestIdleCallback === "function") {
      await new Promise(resolve => window.requestIdleCallback(resolve, { timeout }));
      return;
    }

    await new Promise(resolve => window.setTimeout(resolve, Math.min(Math.max(timeout, 120), 900)));
  }

  async function precarregarFasesOciosasSequencialmente(){
    if (document.hidden || haFiltroAtivo()) return;

    const idsFases = fases
      .map(f => idNum(f?.IDDimKanbanFase || 0))
      .filter(Boolean);

    for (const idFase of idsFases) {
      if (document.hidden || haFiltroAtivo()) return;

      const st = estadoFase.get(idFase);
      if (!st || st.carregandoServidor || st.loading || st.cargaCompleta) continue;
      if (!faseAindaTemMaisConteudo(idFase)) continue;

      await esperarJanelaOciosaKanban(700);
      if (document.hidden || haFiltroAtivo()) return;

      await carregarLoteServidorDaFase(idFase, TAM_LOTE_POR_FASE, { ignorarAutoPreenchimento: true });
      reconstruirIndicesCards();
      preencherCabecalhoFase(idFase);
      atualizarSentinelaFase(idFase);
    }
  }

  function preencherCabecalhoFase(idFase){
    const idF = idNum(idFase);
    const f = fasePorId(idF);
    const st = estadoFase.get(idF);
    if (!f || !st) return;

    const qtdVisivel = contarCardsNaFaseVisiveis(idF);
    const qtdCarregado = contarCardsNaFaseCarregados(idF);
    const cargaTotalDesconhecida = st.cargaTotalDesconhecida === true || f.CargaTotalDesconhecida === true;
    const qtdTotalServidor = cargaTotalDesconhecida
      ? Math.max(idNum(st.totalServidor || 0), qtdCarregado)
      : Math.max(idNum(f.QuantidadeCardsTotal || 0), qtdCarregado);
    st.totalServidor = qtdTotalServidor;

    const col = board.querySelector(`.kb-col[data-fase="${idF}"]`);
    if (!col) return;

    const strong = col.querySelector(".kb-col-title strong");
    if (strong){
      if (haFiltroAtivo()) {
        strong.textContent = `${(f.NomeFase || "—")} (${qtdVisivel})`;
      } else if (cargaTotalDesconhecida) {
        strong.textContent = `${(f.NomeFase || "—")} (${qtdCarregado || "..."})`;
      } else {
        strong.textContent = `${(f.NomeFase || "—")} (${qtdTotalServidor})`;
      }
    }

    const countEl = col.querySelector(".kb-count");
    if (countEl){
      if (haFiltroAtivo()){
        countEl.textContent = `${String(f.TipoFase || "").toLowerCase()} • ${qtdVisivel} visível${qtdVisivel === 1 ? "" : "eis"} entre ${qtdCarregado} carregado${qtdCarregado === 1 ? "" : "s"}`;
      } else if (cargaTotalDesconhecida) {
        countEl.textContent = `${String(f.TipoFase || "").toLowerCase()} • carregando cards...`;
      } else if (qtdCarregado !== qtdTotalServidor) {
        countEl.textContent = `${String(f.TipoFase || "").toLowerCase()} • ${qtdCarregado}/${qtdTotalServidor} carregados`;
      } else {
        countEl.textContent = `${String(f.TipoFase || "").toLowerCase()} • ${qtdTotalServidor} carregados`;
      }
    }
  }




  function faseAindaTemMaisConteudo(idFase){
    const idF = idNum(idFase);
    const st = estadoFase.get(idF);
    if (!st) return false;

    const lista = listaCardsDaFase(idF);
    if ((st.rendered || 0) < lista.length) {
      return true;
    }

    const totalServidorNaFase = Math.max(
      idNum(st.totalServidor || 0),
      idNum(fasePorId(idF)?.QuantidadeCardsTotal || 0),
      contarCardsNaFaseCarregados(idF)
    );

    return !st.cargaCompleta && contarCardsNaFaseCarregados(idF) < totalServidorNaFase;
  }

  async function garantirPreenchimentoMinimoDaFase(idFase, opcoes = {}){
    const idF = idNum(idFase);
    const st = estadoFase.get(idF);
    if (!st || st.loading || st.carregandoServidor) return;
    if (haFiltroAtivo()) return;

    const maxTentativas = Math.max(1, Math.min(idNum(opcoes.maxTentativas) || 6, 20));
    let tentativa = 0;

    while (tentativa < maxTentativas) {
      await new Promise(resolve => requestAnimationFrame(resolve));

      const estadoAtual = estadoFase.get(idF);
      if (!estadoAtual || estadoAtual.loading || estadoAtual.carregandoServidor) {
        return;
      }

      const temOverflow = (estadoAtual.dropEl.scrollHeight - estadoAtual.dropEl.clientHeight) > 20;
      if (temOverflow || !faseAindaTemMaisConteudo(idF)) {
        return;
      }

      tentativa += 1;
      await carregarMaisCardsDaFase(
        idF,
        false,
        TAM_LOTE_POR_FASE,
        Object.assign({}, opcoes, {
          ignorarAutoPreenchimento: true,
          maxTentativas: 0,
        })
      );
    }
  }





async function preencherCardsInicial(idFase, quantidadeDesejada = TAM_LOTE_POR_FASE, manterScroll = false, opcoes = {}) {
  const idF = idNum(idFase);
  const st = estadoFase.get(idF);
  if (!st) return;

  const scrollTopAnterior = manterScroll ? st.dropEl.scrollTop : 0;
  const qtdDesejadaNormalizada = Math.max(TAM_LOTE_POR_FASE, idNum(quantidadeDesejada) || TAM_LOTE_POR_FASE);

  let lista = listaCardsDaFase(idF);
  if (lista.length < qtdDesejadaNormalizada && !st.cargaCompleta) {
    const qtdFaltante = Math.max(TAM_LOTE_POR_FASE, qtdDesejadaNormalizada - lista.length);
    await carregarLoteServidorDaFase(idF, qtdFaltante, opcoes);
    lista = listaCardsDaFase(idF);
  }

  st.visiveis = qtdDesejadaNormalizada;
  sincronizarCardsRenderizadosDaFase(idF, qtdDesejadaNormalizada);

  if (!opcoes.ignorarAutoPreenchimento) {
    await garantirPreenchimentoMinimoDaFase(idF, opcoes);
  }

  if (manterScroll) {
    requestAnimationFrame(() => {
      const maxScroll = Math.max(0, st.dropEl.scrollHeight - st.dropEl.clientHeight);
      st.dropEl.scrollTop = Math.min(scrollTopAnterior, maxScroll);
      if (typeof st.dropEl._kbAtualizarScrollbarFase === "function") st.dropEl._kbAtualizarScrollbarFase();
    });
  }
}



  async function carregarMaisCardsDaFase(idFase, force = false, incremento = TAM_LOTE_POR_FASE, opcoes = {}) {
  const idF = idNum(idFase);
  const st = estadoFase.get(idF);
  if (!st || st.loading) return;

  st.loading = true;
  st.sentinelEl.style.display = "block";
  st.sentinelEl.textContent = "Carregando...";

  try {
    const qtdIncremento = Math.max(TAM_LOTE_POR_FASE, idNum(incremento) || TAM_LOTE_POR_FASE);
    const qtdAtual = force
      ? 0
      : Math.max(TAM_LOTE_POR_FASE, st.visiveis || st.rendered || 0);
    const qtdDesejada = force
      ? qtdIncremento
      : qtdAtual + qtdIncremento;

    let lista = listaCardsDaFase(idF);

    if (lista.length < qtdDesejada && !st.cargaCompleta) {
      const qtdFaltante = Math.max(TAM_LOTE_POR_FASE, qtdDesejada - lista.length);
      await carregarLoteServidorDaFase(idF, qtdFaltante, opcoes);
      lista = listaCardsDaFase(idF);
    }

    if (st.cargaCompleta && qtdAtual >= lista.length && !force) {
      st.visiveis = Math.max(st.visiveis || 0, lista.length);
      sincronizarCardsRenderizadosDaFase(idF, st.visiveis);
      return;
    }

    st.visiveis = qtdDesejada;
    sincronizarCardsRenderizadosDaFase(idF, qtdDesejada);
  } finally {
    st.loading = false;
    preencherCabecalhoFase(idF);
    if (haFiltroAtivo()) {
      renderizarFiltrosMultiselect();
      agendarRecarregarResumoComercial(150);
    }
  }

  if (!opcoes.ignorarAutoPreenchimento) {
    await garantirPreenchimentoMinimoDaFase(idF, opcoes);
  }
}




  function obterAssinaturaVisualCard(card){
    const c = card || {};
    const idCard = idNum(c.IDFatoKanbanCard || 0);
    const idFase = idNum(c.IDDimKanbanFaseAtual || c.id_fase_atual || 0);
    const versao = safeStr(c.VersaoConcorrenciaHex || c.versao_concorrencia || "");
    const tags = tagsDoCard(idCard)
      .map(tag => `${idNum(tag?.IDDimKanbanTag || 0)}:${safeStr(tag?.NomeTag || "").trim()}:${safeStr(tag?.CorHex || "").trim()}`)
      .sort()
      .join("|");

    return [idCard, idFase, versao, tags].join("::");
  }

  function atualizarSentinelaFase(idFase){
    const idF = idNum(idFase);
    const st = estadoFase.get(idF);
    if (!st || !st.sentinelEl) return;

    const lista = listaCardsDaFase(idF);
    const totalCarregadoNaFase = contarCardsNaFaseCarregados(idF);
    const fase = fasePorId(idF);
    const cargaTotalDesconhecida = st.cargaTotalDesconhecida === true || fase?.CargaTotalDesconhecida === true;
    const totalServidorNaFase = cargaTotalDesconhecida
      ? Math.max(idNum(st.totalServidor || 0), totalCarregadoNaFase)
      : Math.max(
          idNum(st.totalServidor || 0),
          idNum(fase?.QuantidadeCardsTotal || 0),
          totalCarregadoNaFase
        );

    st.totalServidor = totalServidorNaFase;
    if (!cargaTotalDesconhecida) {
      st.cargaCompleta = st.cargaCompleta || totalCarregadoNaFase >= totalServidorNaFase;
    }

    if (lista.length === 0) {
      st.sentinelEl.style.display = "block";
      st.sentinelEl.textContent = cargaTotalDesconhecida
        ? "Carregando cards desta fase..."
        : (haFiltroAtivo()
            ? "Sem cards carregados para os filtros atuais nesta fase."
            : "Sem cards nesta fase.");
      return;
    }

    if (st.rendered < lista.length) {
      st.sentinelEl.style.display = "block";
      st.sentinelEl.innerHTML = "";
      st.sentinelEl.appendChild(document.createTextNode("Mostrando "));
      st.sentinelEl.appendChild(el("strong", {}, [String(st.rendered)]));
      st.sentinelEl.appendChild(document.createTextNode(" de "));
      st.sentinelEl.appendChild(el("strong", {}, [String(lista.length)]));
      st.sentinelEl.appendChild(document.createTextNode(" carregados — role ou clique aqui para ver mais"));
      return;
    }

    if (!st.cargaCompleta) {
      st.sentinelEl.style.display = "block";
      st.sentinelEl.textContent = cargaTotalDesconhecida
        ? `Mostrando ${lista.length} carregados. Buscando total da fase...`
        : `Mostrando ${lista.length} carregados de ${totalServidorNaFase}. Role dentro da fase ou clique aqui para buscar mais.`;
      return;
    }

    st.sentinelEl.style.display = "none";
    st.sentinelEl.textContent = "";
  }

  function faseDeveUsarVirtualizacao(qtdRenderizarLogico){
    return idNum(qtdRenderizarLogico || 0) > LIMITE_CARDS_PARA_VIRTUALIZAR_FASE;
  }

  function garantirEstruturaVirtualFase(st){
    if (!st || !st.dropEl) return;

    if (st.spacerTopoEl && st.spacerTopoEl.parentNode !== st.dropEl) {
      st.dropEl.insertBefore(st.spacerTopoEl, st.dropEl.firstChild || null);
    }

    if (st.sentinelEl && st.sentinelEl.parentNode !== st.dropEl) {
      st.dropEl.appendChild(st.sentinelEl);
    }

    if (st.spacerBaseEl && st.spacerBaseEl.parentNode !== st.dropEl) {
      st.dropEl.insertBefore(st.spacerBaseEl, st.sentinelEl || null);
    }

    if (st.spacerTopoEl && st.dropEl.firstElementChild !== st.spacerTopoEl) {
      st.dropEl.insertBefore(st.spacerTopoEl, st.dropEl.firstChild || null);
    }

    if (st.spacerBaseEl && st.sentinelEl && st.spacerBaseEl.nextSibling !== st.sentinelEl) {
      st.dropEl.insertBefore(st.spacerBaseEl, st.sentinelEl);
    }
  }

  function obterAlturaEstimadaVirtual(st){
    const alturaAtual = Number(st?.alturaEstimadaCardVirtual || 0);
    if (Number.isFinite(alturaAtual) && alturaAtual >= 120) return alturaAtual;
    return ALTURA_ESTIMADA_CARD_VIRTUAL;
  }

  function atualizarAlturaEstimadaVirtual(st){
    if (!st || !st.dropEl) return;

    const cardsDom = Array.from(st.dropEl.querySelectorAll('.kb-card[data-card]')).slice(0, 8);
    if (!cardsDom.length) return;

    const somaAlturas = cardsDom.reduce((acc, no) => acc + Math.max(80, no.offsetHeight || ALTURA_ESTIMADA_CARD_VIRTUAL), 0);
    const mediaAlturaCard = somaAlturas / cardsDom.length;
    const gap = 10;
    const estimada = Math.round(mediaAlturaCard + gap);

    if (Number.isFinite(estimada) && estimada >= 120) {
      const anterior = obterAlturaEstimadaVirtual(st);
      st.alturaEstimadaCardVirtual = Math.round((anterior * 0.70) + (estimada * 0.30));
    }
  }

  function calcularJanelaVirtualFase(st, qtdRenderizarLogico){
    const totalLogico = Math.max(0, idNum(qtdRenderizarLogico || 0));
    const altura = obterAlturaEstimadaVirtual(st);
    const alturaViewport = Math.max(420, st?.dropEl?.clientHeight || 0);
    const scrollTop = Math.max(0, st?.dropEl?.scrollTop || 0);
    const quantidadeTela = Math.max(
      LIMITE_CARDS_DOM_POR_FASE_VIRTUAL,
      Math.ceil(alturaViewport / altura) + (OVERSCAN_CARDS_VIRTUAIS * 2)
    );

    let inicio = Math.max(0, Math.floor(scrollTop / altura) - OVERSCAN_CARDS_VIRTUAIS);
    let fim = Math.min(totalLogico, inicio + quantidadeTela);

    if ((fim - inicio) < quantidadeTela && fim === totalLogico) {
      inicio = Math.max(0, fim - quantidadeTela);
    }

    return { inicio, fim, altura };
  }

  function agendarAtualizacaoJanelaVirtualFase(idFase){
    const idF = idNum(idFase);
    const st = estadoFase.get(idF);
    if (!st || !st.virtualizado) return;
    if (st.rafVirtual !== null && st.rafVirtual !== undefined) return;

    st.rafVirtual = window.requestAnimationFrame(() => {
      st.rafVirtual = null;
      sincronizarCardsRenderizadosDaFase(idF, st.visiveis || st.rendered || TAM_LOTE_POR_FASE, { atualizarJanelaVirtual: true });
    });
  }

  function sincronizarCardsRenderizadosDaFase(idFase, quantidadeDesejada = null, opcoes = {}){
    const idF = idNum(idFase);
    const st = estadoFase.get(idF);
    if (!st) return;

    garantirEstruturaVirtualFase(st);

    const lista = listaCardsDaFase(idF);
    const qtdDesejadaNormalizada = Math.max(
      TAM_LOTE_POR_FASE,
      idNum(quantidadeDesejada) || st.visiveis || st.rendered || TAM_LOTE_POR_FASE
    );
    const qtdRenderizarLogico = Math.min(qtdDesejadaNormalizada, lista.length);
    const usarVirtualizacao = faseDeveUsarVirtualizacao(qtdRenderizarLogico);

    st.virtualizado = usarVirtualizacao;
    st.dropEl.classList.toggle("kb-drop-virtualizado", usarVirtualizacao);

    let indiceInicio = 0;
    let indiceFim = qtdRenderizarLogico;
    let alturaVirtual = obterAlturaEstimadaVirtual(st);

    if (usarVirtualizacao) {
      const janela = calcularJanelaVirtualFase(st, qtdRenderizarLogico);
      indiceInicio = janela.inicio;
      indiceFim = janela.fim;
      alturaVirtual = janela.altura;

      if (st.spacerTopoEl) {
        st.spacerTopoEl.style.display = "block";
        st.spacerTopoEl.style.height = `${Math.max(0, indiceInicio * alturaVirtual)}px`;
      }
      if (st.spacerBaseEl) {
        st.spacerBaseEl.style.display = "block";
        st.spacerBaseEl.style.height = `${Math.max(0, (qtdRenderizarLogico - indiceFim) * alturaVirtual)}px`;
      }
    } else {
      if (st.spacerTopoEl) {
        st.spacerTopoEl.style.height = "0px";
        st.spacerTopoEl.style.display = "none";
      }
      if (st.spacerBaseEl) {
        st.spacerBaseEl.style.height = "0px";
        st.spacerBaseEl.style.display = "none";
      }
    }

    const nosAtuais = new Map();
    const duplicadosParaRemover = [];

    st.dropEl.querySelectorAll('.kb-card[data-card]').forEach(no => {
      const idCardNo = idNum(no.dataset.card || 0);
      if (!idCardNo) return;

      const noExistente = nosAtuais.get(idCardNo);
      if (noExistente) {
        duplicadosParaRemover.push(no);
        return;
      }

      nosAtuais.set(idCardNo, no);
    });

    duplicadosParaRemover.forEach(no => no.remove());

    const fragmento = document.createDocumentFragment();

    for (let i = indiceInicio; i < indiceFim; i += 1) {
      const card = lista[i];
      const idCard = idNum(card?.IDFatoKanbanCard || 0);
      if (!idCard) continue;

      const assinatura = obterAssinaturaVisualCard(card);
      let noCard = nosAtuais.get(idCard) || null;

      if (noCard && safeStr(noCard.dataset.renderSignature) !== assinatura) {
        noCard.remove();
        noCard = null;
      }

      if (!noCard) {
        noCard = renderCard(card, idF, st.colAccent);
      }

      noCard.dataset.renderSignature = assinatura;
      noCard.dataset.virtualIndex = String(i);
      fragmento.appendChild(noCard);
      nosAtuais.delete(idCard);
    }

    nosAtuais.forEach(no => no.remove());

    garantirEstruturaVirtualFase(st);
    const referenciaInsercao = usarVirtualizacao && st.spacerBaseEl ? st.spacerBaseEl : st.sentinelEl;
    st.dropEl.insertBefore(fragmento, referenciaInsercao || null);

    st.rendered = qtdRenderizarLogico;
    st.cardsNoDom = Math.max(0, indiceFim - indiceInicio);
    st.visiveis = qtdDesejadaNormalizada;
    st.virtualInicio = indiceInicio;
    st.virtualFim = indiceFim;

    atualizarAlturaEstimadaVirtual(st);
    atualizarSentinelaFase(idF);
    preencherCabecalhoFase(idF);

    if (typeof st.dropEl._kbAtualizarScrollbarFase === "function") {
      st.dropEl._kbAtualizarScrollbarFase();
    }
    atualizarModoDesempenhoKanban();
  }



  function renderCard(c, idFase, colAccent){
    const idF = idNum(idFase);
    const f = fasePorId(idF) || {};
    const tgs = tagsDoCard(c.IDFatoKanbanCard);

    const tagsLinha = el("div", {class:"kb-tags"},
      tgs.map(t => el("span", {
        class:"kb-tag",
        title: t.NomeTag || "",
        style: estiloTag(t.CorHex)
      }, [
        el("span", {class:"kb-tag-text"}, [t.NomeTag || ""])
      ]))
    );

    const meta = el("div", {class:"kb-card-meta"}, []);

    if (c.Departamento) {
      meta.appendChild(
        el("div", {class:"kb-meta-row"}, [
          el("span", {class:"kb-meta-key"}, ["DEPARTAMENTO:"]),
          el("span", {class:"kb-meta-val", title: String(c.Departamento)}, [String(c.Departamento)])
        ])
      );
    }

    if (c.Valor) {
      meta.appendChild(
        el("div", {class:"kb-meta-row"}, [
          el("span", {class:"kb-meta-key"}, ["VALOR:"]),
          el("span", {class:"kb-meta-val", title: String(c.Valor)}, [String(c.Valor)])
        ])
      );
    }

    const textoFace = obterTextoFaceCard(c);
    if (textoFace) {
      meta.appendChild(
        el("div", {class:"kb-meta-row"}, [
          el("span", {class:"kb-meta-key"}, ["FACE:"]),
          el("span", {class:"kb-meta-val", title: textoFace}, [textoFace])
        ])
      );
    }

    const fasePill = el("span", { class:"kb-phase-pill", title: f.NomeFase || "" }, [
      el("span", {class:"kb-phase-dot", style:`--phase-accent:${colAccent}; background:${colAccent};`}, []),
      `${f.NomeFase || "—"}`
    ]);

    const idBadge = el("span", { class:"kb-card-badge kb-card-badge-id", title:`ID do card ${c.IDFatoKanbanCard || ""}` }, [
      `#${c.IDFatoKanbanCard || "—"}`
    ]);

    const tipoClienteId = derivarIdTipoClienteDescontoDoCard(c);
    const tipoClienteTema = obterTemaTipoClienteDesconto(tipoClienteId);
    const tipoClienteDesconto = nomeTipoClienteDescontoDoCard(c);
    const origemAtendimentoId = derivarIdOrigemAtendimentoDoCard(c);
    const origemAtendimentoNome = nomeOrigemAtendimentoDoCard(c);
    const origemAtendimentoTema = obterTemaOrigemAtendimento(origemAtendimentoId || origemAtendimentoNome);
    const tipoClienteBadge = tipoClienteDesconto
      ? el("span", {
          class:"kb-card-badge kb-card-badge-tipo",
          title: tipoClienteDesconto,
          style:`--tipo-cliente-bg:${tipoClienteTema.bg}; --tipo-cliente-fg:${tipoClienteTema.fg}; --tipo-cliente-bd:${tipoClienteTema.bd};`
        }, [tipoClienteDesconto])
      : null;

    const origemAtendimentoBadge = origemAtendimentoNome
      ? el("span", {
          class:"kb-card-badge kb-card-badge-tipo",
          title: origemAtendimentoNome,
          style:`--tipo-cliente-bg:${origemAtendimentoTema.bg}; --tipo-cliente-fg:${origemAtendimentoTema.fg}; --tipo-cliente-bd:${origemAtendimentoTema.bd};`
        }, [origemAtendimentoNome])
      : null;

    const btnDel = el("button", {
      class:"kb-card-del",
      title:"Remover card",
      type:"button",
      "data-acao-card":"remover",
      "data-card": c.IDFatoKanbanCard
    }, ["−"]);

    const topInfo = el("div", {class:"kb-card-top-left"}, [
      fasePill,
      el("div", {class:"kb-card-head-meta"}, [idBadge])
    ]);

    const topRow = el("div", {class:"kb-card-phase"}, [topInfo, btnDel]);

    const idEmp = c.IDEmpresaRelacionadaCard || c.IDCliente || c.IDEmpresaRelacionada || c.IDEmpresa || null;
    const empresaCatalogoCard = idEmp ? obterEmpresaCatalogoPorId(idEmp) : null;
    const empRazao = safeStr(
      c.EmpresaRazaoSocial ||
      c.RazaoSocial ||
      c.NomeEmpresa ||
      empresaCatalogoCard?.RazaoSocial ||
      empresaCatalogoCard?.EmpresaRazaoSocial ||
      empresaCatalogoCard?.NomeFantasia ||
      ""
    ).trim();
    const empCnpj = mascaraCnpj(
      c.EmpresaCNPJ ||
      c.CNPJ ||
      empresaCatalogoCard?.CNPJ ||
      empresaCatalogoCard?.EmpresaCNPJ ||
      ""
    );
    const empSetor = safeStr(
      c.EmpresaSetor ||
      c.SegmentoSetor ||
      c.CnaeSetor ||
      c.setor_cnae ||
      c.Setor ||
      empresaCatalogoCard?.Setor ||
      empresaCatalogoCard?.EmpresaSetor ||
      empresaCatalogoCard?.CnaeSetor ||
      ""
    ).trim();
    const empClasse = safeStr(
      c.EmpresaClasse ||
      c.SegmentoClasse ||
      c.CnaeClasse ||
      c.classe_cnae ||
      c.Classe ||
      empresaCatalogoCard?.Classe ||
      empresaCatalogoCard?.EmpresaClasse ||
      empresaCatalogoCard?.CnaeClasse ||
      ""
    ).trim();

    const itensEmpresa = [];
    const adicionarCampoEmpresa = (rotulo, valor) => {
      const texto = safeStr(valor || "").trim();
      if (!texto) return;
      itensEmpresa.push(
        el("div", {class:"kb-empresa-item"}, [
          el("span", {class:"kb-empresa-k"}, [rotulo]),
          el("span", {class:"kb-empresa-v", title: texto}, [texto])
        ])
      );
    };

    adicionarCampoEmpresa("Razão:", empRazao);
    adicionarCampoEmpresa("CNPJ:", empCnpj);
    adicionarCampoEmpresa("Setor:", empSetor);
    adicionarCampoEmpresa("Classe:", empClasse);

    if (tipoClienteBadge) {
      itensEmpresa.push(
        el("div", {class:"kb-empresa-item kb-empresa-item-tipo"}, [
          el("span", {
            class:"kb-card-badge kb-card-badge-tipo kb-card-badge-tipo-empresa",
            title: tipoClienteDesconto,
            style:`--tipo-cliente-bg:${tipoClienteTema.bg}; --tipo-cliente-fg:${tipoClienteTema.fg}; --tipo-cliente-bd:${tipoClienteTema.bd};`
          }, [tipoClienteDesconto])
        ])
      );
    }

    if (origemAtendimentoBadge) {
      itensEmpresa.push(
        el("div", {class:"kb-empresa-item kb-empresa-item-tipo"}, [
          el("span", {
            class:"kb-card-badge kb-card-badge-tipo kb-card-badge-tipo-empresa",
            title: origemAtendimentoNome,
            style:`--tipo-cliente-bg:${origemAtendimentoTema.bg}; --tipo-cliente-fg:${origemAtendimentoTema.fg}; --tipo-cliente-bd:${origemAtendimentoTema.bd};`
          }, [origemAtendimentoNome])
        ])
      );
    }

    const blocoEmpresa = itensEmpresa.length ? el("div", {class:"kb-empresa"}, [
      el("div", {class:"kb-empresa-grid"}, itensEmpresa)
    ]) : null;

    const quantidadePaineis = idNum(c.QuantidadePaineisUnicos || c.QuantidadePaineisVinculados || 0);
    const valorTotalPaineis = Number(c.ValorTotalPaineis || 0);
    const exibirResumoPaineis = quantidadePaineis > 0 || valorTotalPaineis > 0;

    const blocoResumoPaineis = exibirResumoPaineis ? el("div", {
      class:"kb-card-paineis",
      title:`${quantidadePaineis} painel${quantidadePaineis === 1 ? "" : "éis"} • ${formatarMoedaBR(valorTotalPaineis)}`
    }, [
      el("img", {
        class:"kb-card-paineis-logo",
        src:URL_IMAGEM_PAINEL_PUBLICITARIO,
        alt:"Painel publicitário",
        loading:"lazy",
        decoding:"async"
      }),
      el("div", {class:"kb-card-paineis-texto"}, [
        el("span", {class:"kb-card-paineis-quantidade"}, [
          `${quantidadePaineis} ${quantidadePaineis === 1 ? "Painel" : "Paineis"}`
        ]),
        el("span", {class:"kb-card-paineis-separador"}, ["•"]),
        el("span", {class:"kb-card-paineis-valor"}, [formatarMoedaBR(valorTotalPaineis)])
      ])
    ]) : null;

    const cardChildren = [
      topRow,
      el("div", {class:"kb-card-title"}, [c.Titulo || "—"]),
      meta
    ];

    if (blocoEmpresa) cardChildren.push(blocoEmpresa);
    cardChildren.push(tagsLinha);
    if (blocoResumoPaineis) cardChildren.push(blocoResumoPaineis);

    const corEspecialCard = obterCorEspecialDoCard(c);
    const atributosCard = {
      class:`kb-card${corEspecialCard ? " is-tag-alerta" : ""}`,
      draggable:"true",
      "data-card": c.IDFatoKanbanCard,
      "data-fase": idF,
      "data-render-signature": obterAssinaturaVisualCard(c)
    };

    if (corEspecialCard) {
      atributosCard.style = `--kb-card-alert-color:${corEspecialCard};`;
    }

    const card = el("div", atributosCard, cardChildren);

    return card;
  }

  function configurarEventosDelegadosCardsKanban(){
    if (!board || board.dataset.eventosCardsDelegados === "1") return;

    board.addEventListener("click", (evento) => {
      const botaoRemover = evento.target.closest?.(".kb-card-del[data-acao-card='remover']");
      if (!botaoRemover || !board.contains(botaoRemover)) return;

      evento.preventDefault();
      evento.stopPropagation();

      const cardEl = botaoRemover.closest(".kb-card[data-card]");
      abrirModalRemoverCard(idNum(botaoRemover.dataset.card || cardEl?.dataset.card || 0));
    });

    board.addEventListener("dblclick", (evento) => {
      if (evento.target.closest?.("button, a, input, select, textarea, label")) return;

      const cardEl = evento.target.closest?.(".kb-card[data-card]");
      if (!cardEl || !board.contains(cardEl)) return;

      abrirCard(idNum(cardEl.dataset.card || 0));
    });

    board.addEventListener("dragstart", (evento) => {
      const cardEl = evento.target.closest?.(".kb-card[data-card]");
      if (!cardEl || !board.contains(cardEl)) return;

      const idCard = idNum(cardEl.dataset.card || 0);
      const idFase = idNum(cardEl.dataset.fase || cardEl.closest(".kb-drop[data-fase]")?.dataset.fase || 0);
      if (!idCard || !idFase) return;

      evento.dataTransfer?.setData("text/plain", String(idCard));
      iniciarModoDrag(idCard, idFase, cardEl, evento);
    });

    board.addEventListener("dragend", (evento) => {
      const cardEl = evento.target.closest?.(".kb-card[data-card]");
      if (!cardEl || !board.contains(cardEl)) return;
      encerrarModoDrag();
    });

    board.dataset.eventosCardsDelegados = "1";
  }

  function redesenharFases(idsFase, manterScroll = true){
    const ids = [...new Set((Array.isArray(idsFase) ? idsFase : [idsFase]).map(idNum).filter(Boolean))];
    ids.forEach(idFase => {
      preencherCabecalhoFase(idFase);
      const st = estadoFase.get(idFase);
      const qtd = st ? Math.max(TAM_LOTE_POR_FASE, st.visiveis) : TAM_LOTE_POR_FASE;
      void preencherCardsInicial(idFase, qtd, manterScroll);
    });
    atualizarResumoBusca();
  }

  async function redesenharFasesSincronizado(idsFase, mapaQuantidades = null, manterScroll = true, opcoes = {}) {
    const ids = [...new Set((Array.isArray(idsFase) ? idsFase : [idsFase]).map(idNum).filter(Boolean))];

    for (const idFase of ids) {
      preencherCabecalhoFase(idFase);

      const st = estadoFase.get(idFase);
      const qtdPadrao = st ? Math.max(TAM_LOTE_POR_FASE, st.visiveis || st.rendered || 0) : TAM_LOTE_POR_FASE;
      const qtd = mapaQuantidades instanceof Map && mapaQuantidades.has(idFase)
        ? Math.max(TAM_LOTE_POR_FASE, idNum(mapaQuantidades.get(idFase)) || TAM_LOTE_POR_FASE)
        : qtdPadrao;

      await preencherCardsInicial(idFase, qtd, manterScroll, opcoes);
    }

    atualizarResumoBusca();
  }

  async function criarCardPrompt(idFase) {
    const titulo = prompt("Nome do card:");
    if (!titulo || titulo.trim().length < 2) return;

    const clientRequestId = gerarClientRequestIdKanban("card_create");
    registrarEventoLocalPendente(clientRequestId);

    const r = await fetchKanbanComTimeout(`/kanban/api/kanbans/${ID_KANBAN}/cards`, {
      method:"POST",
      credentials: "same-origin",
      headers: headersJSON,
      body: JSON.stringify({titulo: titulo.trim(), id_fase: idFase, client_request_id: clientRequestId})
    }, TIMEOUT_FETCH_CRITICO_KANBAN_MS);

    const j = await r.json().catch(() => null);
    if (!r.ok || !j || !j.ok) {
      mostrarMensagemBoard(mensagemErroHttp(r, j, "Não foi possível criar o card."));
      console.warn("criarCardPrompt falhou", { http: r.status, body: j, idFase });
      return;
    }

    limparMensagemBoard();

    const cardCriado = j.card ? normalizarCardServidor(j.card) : null;
    const idCardCriado = idNum(cardCriado?.IDFatoKanbanCard || j.id || j.id_card || 0);
    const idFaseCriada = idNum(cardCriado?.IDDimKanbanFaseAtual || j.id_fase || idFase || 0);

    if (cardCriado && idCardCriado && idFaseCriada) {
      const jaExistiaLocalmente = !!obterCardPorId(idCardCriado);
      inserirOuAtualizarCardLocal(cardCriado, {
        reposicionarNoTopoDaFase: !jaExistiaLocalmente,
        reconstruir: !jaExistiaLocalmente
      });

      if (Array.isArray(j.tags)) {
        setTagsDoCard(idCardCriado, j.tags);
      }

      if (Array.isArray(j.notas)) {
        mapaNotasPorCard.set(
          idCardCriado,
          j.notas.map(n => Object.assign({}, n || {}))
        );
      }

      if (!jaExistiaLocalmente) {
        ajustarQuantidadeFaseLocal(idFaseCriada, 1);
        redesenharFasesLocalmente([idFaseCriada], null, true);
        destacarCardNaFase(idCardCriado, idFaseCriada);
      }

      agendarRecarregarResumoComercial(1200);
      return;
    }

    await carregar();
  }


  function esperarKanban(ms){
    return new Promise(resolve => window.setTimeout(resolve, Math.max(0, Number(ms) || 0)));
  }

  async function acompanharMovimentoAssincronoCard(idCard, idFaseOrigem, idFaseDestino, mapaQuantidades = null, opcoes = {}) {
    const idC = idNum(idCard);
    const idOrigem = idNum(idFaseOrigem);
    const idDestino = idNum(idFaseDestino);
    const maxTentativas = Math.max(3, idNum(opcoes.maxTentativas || 12));
    const intervaloMs = Math.max(600, idNum(opcoes.intervaloMs || 1200));

    if (!idC || !idDestino) return false;

    for (let tentativa = 1; tentativa <= maxTentativas; tentativa += 1) {
      await esperarKanban(intervaloMs);

      const detalhe = await buscarDetalheCard(idC, { fresh: true });
      const cardServidor = detalhe?.card ? normalizarCardServidor(detalhe.card) : null;

      if (!cardServidor) {
        continue;
      }

      const idFaseServidor = idNum(cardServidor.IDDimKanbanFaseAtual || 0);
      const saiuDoQuadro = cardDeveSairDoQuadro(cardServidor, idFaseServidor);

      if (Array.isArray(detalhe.tags)) {
        if (saiuDoQuadro) removerTagsDoCard(idC);
        else setTagsDoCard(idC, detalhe.tags);
      }

      if (Array.isArray(detalhe.notas)) {
        mapaNotasPorCard.set(idC, detalhe.notas.map(n => Object.assign({}, n || {})));
      }

      if (saiuDoQuadro) {
        removerCardLocal(idC);
      } else {
        inserirOuAtualizarCardLocal(cardServidor, { reposicionarNoTopoDaFase: true });
      }

      if (idFaseServidor === idDestino || saiuDoQuadro) {
        redesenharFasesLocalmente([idOrigem, idDestino, idFaseServidor].filter(Boolean), mapaQuantidades, true);
        if (!saiuDoQuadro) destacarCardNaFase(idC, idFaseServidor);
        limparMensagemBoard();
        return true;
      }
    }

    await redesenharFasesSincronizado([idOrigem, idDestino].filter(Boolean), mapaQuantidades, true, { fresh: true });
    mostrarMensagemBoard("O movimento foi aceito pelo servidor, mas ainda não apareceu na fase final. A tela vai sincronizar pelo tempo real ou no próximo recarregamento.", "alerta", 7000);
    return false;
  }

  async function obterVersaoAtualDoCard(idCard, opcoes = {}) {
  const fresh = !!opcoes.fresh;
  const id = idNum(idCard);

  if (!fresh) {
    const cardLocal = obterCardPorId(id);
    if (cardLocal && safeStr(cardLocal.VersaoConcorrenciaHex)) {
      return cardLocal;
    }
  }

  const detalhe = await buscarDetalheCard(id, { fresh });
  if (!detalhe || !detalhe.card) return null;

  inserirOuAtualizarCardLocal(detalhe.card);
  setTagsDoCard(id, detalhe.tags || []);

  if (Array.isArray(detalhe.notas)) {
    mapaNotasPorCard.set(
      id,
      detalhe.notas.map(n => Object.assign({}, n || {}))
    );
  }

  return normalizarCardServidor(detalhe.card);
}



function obterSolicitacaoContratoAtualParaMovimento(idCardMovido) {
  const idC = idNum(idCardMovido);

  if (!idC) return null;
  if (idNum(cardAbertoId) !== idC) return null;
  if (!modalCard || modalCard.style.display !== "block") return null;

  try {
    const solicitacao = obterSolicitacaoContratoParaPayload();
    if (!solicitacao || typeof solicitacao !== "object") return null;

    const temConteudo = estruturaFormularioSolicitacaoTemConteudo(solicitacao.header)
      || estruturaFormularioSolicitacaoTemConteudo(solicitacao.item)
      || estruturaFormularioSolicitacaoTemConteudo(solicitacao.itens)
      || estruturaFormularioSolicitacaoTemConteudo(solicitacao.contato_cliente_direto)
      || estruturaFormularioSolicitacaoTemConteudo(solicitacao.contatoClienteDireto);

    return temConteudo ? solicitacao : null;
  } catch (erro) {
    console.warn("obterSolicitacaoContratoAtualParaMovimento: falha ao coletar formulário antes de mover", erro);
    return null;
  }
}


async function moverCard(idCard, idFasePara, posicao) {
  const idC = idNum(idCard);
  const idDestino = idNum(idFasePara);

  if (!idC || !idDestino) return false;
  if (movimentosCardsPendentes.has(idC)) return false;

  movimentosCardsPendentes.add(idC);

  let movimentoLocal = null;

  try {
    let cardAtual = obterCardPorId(idC);

    if (!cardAtual || !safeStr(cardAtual.VersaoConcorrenciaHex)) {
      cardAtual = await obterVersaoAtualDoCard(idC, { fresh: true });
    }

    if (!cardAtual || !safeStr(cardAtual.VersaoConcorrenciaHex)) {
      mostrarMensagemBoard("A versão mais recente do card não está carregada. Recarregando o quadro...");
      await carregar();
      return false;
    }

    const idFaseOrigem = idNum(cardAtual.IDDimKanbanFaseAtual || faseOrigemArrasteId || 0);
    if (!idFaseOrigem) {
      await carregar();
      return false;
    }

    if (idFaseOrigem === idDestino) {
      limparMensagemBoard();
      return true;
    }

    const stOrigemAntes = estadoFase.get(idFaseOrigem);
    const stDestinoAntes = estadoFase.get(idDestino);

    const qtdOrigemManter = stOrigemAntes
      ? Math.max(TAM_LOTE_POR_FASE, stOrigemAntes.rendered || stOrigemAntes.visiveis || 0)
      : TAM_LOTE_POR_FASE;

    const qtdDestinoManter = stDestinoAntes
      ? Math.max(TAM_LOTE_POR_FASE, (stDestinoAntes.rendered || stDestinoAntes.visiveis || 0) + 1)
      : TAM_LOTE_POR_FASE;

    const mapaQuantidades = new Map();
    mapaQuantidades.set(idFaseOrigem, qtdOrigemManter);
    mapaQuantidades.set(idDestino, qtdDestinoManter);

    movimentoLocal = moverCardLocalmente(idC, idDestino);
    if (movimentoLocal.ok) {
      redesenharFasesLocalmente([idFaseOrigem, idDestino], mapaQuantidades, true);
      destacarCardNaFase(idC, idDestino);
    }

    const textoNotaMovimento = ((inputNotaTexto && inputNotaTexto.value) || "").trim();
    const clientRequestIdMovimento = gerarClientRequestIdKanban("card_move");
    registrarEventoLocalPendente(clientRequestIdMovimento, 30000);
    const solicitacaoContratoMovimento = obterSolicitacaoContratoAtualParaMovimento(idC);
    const painelFacesMovimento = idNum(cardAbertoId) === idC
      ? coletarPainelFacesDoFormulario()
      : null;
    const idReservaMovimento = Array.isArray(painelFacesMovimento)
      ? (painelFacesMovimento.map((item) => obterIdReservaDeObjeto(item)).find(Boolean) || null)
      : null;

    const payload = {
      id_fase_para: idDestino,
      posicao: posicao || "LAST",
      versao_concorrencia: safeStr(cardAtual.VersaoConcorrenciaHex),
      observacao: textoNotaMovimento,
      client_request_id: clientRequestIdMovimento
    };

    if (idNum(cardAbertoId) === idC) {
      const marcaMovimento = safeStr(inputMarcaCard?.value || "").trim();
      const telefoneMovimento = normalizarTelefoneContato(inputTelefoneCard?.value || "") || null;
      const emailMovimento = safeStr(inputEmailCard?.value || "").trim();

      if (marcaMovimento) payload.marca = marcaMovimento;
      if (telefoneMovimento) payload.telefone = telefoneMovimento;
      if (emailMovimento) payload.email = emailMovimento;
    }

    if (solicitacaoContratoMovimento) {
      payload.solicitacao_contrato = solicitacaoContratoMovimento;
    }

    if (Array.isArray(painelFacesMovimento)) {
      payload.painel_faces = painelFacesMovimento;
      payload.IDReserva = idReservaMovimento || null;
      payload.id_reserva = idReservaMovimento || null;
    }

    const r = await fetchKanbanComTimeout(`/kanban/api/cards/${idC}/mover`, {
      method: "POST",
      credentials: "same-origin",
      headers: headersJSON,
      body: JSON.stringify(payload)
    }, TIMEOUT_FETCH_CRITICO_KANBAN_MS);

    const j = await r.json().catch(() => null);

    if (!(j && j.ok)) {
      let idsParaSincronizar = [idFaseOrigem, idDestino];

      if (r.status === 409 && j && j.card_atual) {
        const cardAtualServidor = normalizarCardServidor(j.card_atual);

        if (cardDeveSairDoQuadro(cardAtualServidor)) {
          removerCardLocal(idC);
        } else {
          inserirOuAtualizarCardLocal(cardAtualServidor);
          idsParaSincronizar.push(idNum(cardAtualServidor.IDDimKanbanFaseAtual));
        }
      } else if (movimentoLocal?.ok) {
        restaurarMovimentoCardLocal(movimentoLocal);
        redesenharFasesLocalmente([idFaseOrigem, idDestino], mapaQuantidades, true);
      }

      mostrarMensagemBoard(mensagemErroHttp(r, j, "Não foi possível mover o card."));
      console.warn("moverCard falhou", {
        http: r.status,
        body: j,
        idCard: idC,
        idFasePara: idDestino
      });

      await redesenharFasesSincronizado(idsParaSincronizar, null, true, { fresh: true });
      return false;
    }

    if (j.processamento_assincrono) {
      mostrarMensagemBoard(j.msg || "Movimento aceito e em processamento. Mantive o card visualmente na fase de destino e vou sincronizar automaticamente.", "alerta", 5000);
      void acompanharMovimentoAssincronoCard(idC, idFaseOrigem, idDestino, mapaQuantidades, { maxTentativas: 15, intervaloMs: 1200 });
      return true;
    }

    let detalheFinal = null;

    if (j.card) {
      inserirOuAtualizarCardLocal(j.card, { reposicionarNoTopoDaFase: true });
    }

    if (Array.isArray(j.tags)) {
      setTagsDoCard(idC, j.tags);
    }

    if (Array.isArray(j.notas)) {
      mapaNotasPorCard.set(
        idC,
        j.notas.map(n => Object.assign({}, n || {}))
      );
    }

    if (!j.card || !Array.isArray(j.tags)) {
      detalheFinal = await buscarDetalheCard(idC, { fresh: true });

      if (detalheFinal?.card) {
        inserirOuAtualizarCardLocal(detalheFinal.card, { reposicionarNoTopoDaFase: true });
      }

      if (Array.isArray(detalheFinal?.tags)) {
        setTagsDoCard(idC, detalheFinal.tags);
      }

      if (Array.isArray(detalheFinal?.notas)) {
        mapaNotasPorCard.set(
          idC,
          detalheFinal.notas.map(n => Object.assign({}, n || {}))
        );
      }
    }

    const cardFinal = normalizarCardServidor(
      detalheFinal?.card || j.card || obterCardPorId(idC) || cardAtual
    );

    if (idNum(cardAbertoId) === idC && modalCard?.style?.display === "block") {
      const idFaseFinalModal = idNum(cardFinal.IDDimKanbanFaseAtual || idDestino);
      const jaPassouFaseFormulario = modalCard.dataset.jaPassouFaseFormularioContrato === "1"
        || idFaseFinalModal === ID_FASE_FORMULARIO_CONTRATO
        || idNum(idFaseOrigem) === ID_FASE_FORMULARIO_CONTRATO
        || idNum(idDestino) === ID_FASE_FORMULARIO_CONTRATO
        || idNum(cardFinal.BitJaPassouPelaFaseFormularioContrato ?? cardFinal.bit_ja_passou_pela_fase_formulario_contrato ?? 0) === 1;

      modalCard.dataset.idFaseAtual = String(idFaseFinalModal || "");
      modalCard.dataset.jaPassouFaseFormularioContrato = jaPassouFaseFormulario ? "1" : "0";
      atualizarVisibilidadeFormularioSolicitacaoContrato();
      atualizarVisibilidadeEmpresasRelacionadasCard();
    }

    const cardSaiDoQuadro = cardDeveSairDoQuadro(cardFinal);

    if (cardSaiDoQuadro) {
      removerCardLocal(idC);
      removerTagsDoCard(idC);
    } else {
      inserirOuAtualizarCardLocal(cardFinal, { reposicionarNoTopoDaFase: true });
    }

    const idFaseDestinoFinal = idNum(cardFinal.IDDimKanbanFaseAtual || idDestino);

    const mapaQuantidadesFinais = new Map();
    mapaQuantidadesFinais.set(idFaseOrigem, qtdOrigemManter);
    mapaQuantidadesFinais.set(idFaseDestinoFinal, qtdDestinoManter);

    if (j.card && Array.isArray(j.tags)) {
      redesenharFasesLocalmente(
        [idFaseOrigem, idFaseDestinoFinal],
        mapaQuantidadesFinais,
        true
      );
    } else {
      await redesenharFasesSincronizado(
        [idFaseOrigem, idFaseDestinoFinal],
        mapaQuantidadesFinais,
        true,
        { fresh: true }
      );
    }

    if (!cardSaiDoQuadro) {
      destacarCardNaFase(idC, idFaseDestinoFinal);
    }

    if (inputNotaTexto && textoNotaMovimento) {
      inputNotaTexto.value = "";
    }

    limparMensagemBoard();
    return true;
  } catch (erroMovimento) {
    console.warn("moverCard: falha de comunicação", erroMovimento);
    if (movimentoLocal?.ok) {
      restaurarMovimentoCardLocal(movimentoLocal);
      redesenharFasesLocalmente([movimentoLocal.origem, movimentoLocal.destino].filter(Boolean), null, true);
    }
    mostrarMensagemBoard(mensagemErroComunicacaoKanban(erroMovimento, "Erro de comunicação ao mover o card. Tente novamente."), "alerta", 7000);
    return false;
  } finally {
    movimentosCardsPendentes.delete(idC);
  }
}





  function abrirModalRemoverCard(idCard){
    cardParaRemover = idCard;
    msgRemoverCard.style.display = "none";
    motivoRemocao.value = "";
    descricaoRemocao.value = "";
    modalRemoverCard.style.display = "block";
  }

  function fecharModalRemoverCard(){
    modalRemoverCard.style.display = "none";
    cardParaRemover = null;
  }

  btnFecharRemoverCard.addEventListener("click", fecharModalRemoverCard);
  modalRemoverCard.addEventListener("click", (e) => {
    if (e.target === modalRemoverCard) fecharModalRemoverCard();
  });

  btnConfirmarRemoverCard.addEventListener("click", async () => {
    msgRemoverCard.style.display = "none";

    if (btnConfirmarRemoverCard.dataset.enviando === "1") return;

    const idCardRemover = idNum(cardParaRemover);
    const motivo = (motivoRemocao.value || "").trim();
    const desc = (descricaoRemocao.value || "").trim();

    if (!idCardRemover){
      msgRemoverCard.textContent = "Card inválido.";
      msgRemoverCard.style.display = "block";
      return;
    }

    if (!motivo){
      msgRemoverCard.textContent = "Selecione um motivo.";
      msgRemoverCard.style.display = "block";
      return;
    }

    if (desc.length < 3){
      msgRemoverCard.textContent = "Descreva o motivo (mínimo 3 caracteres).";
      msgRemoverCard.style.display = "block";
      return;
    }

    const cardAntes = obterCardPorId(idCardRemover);
    const idFaseAntesLocal = idNum(cardAntes?.IDDimKanbanFaseAtual || 0);
    const textoOriginalBotao = btnConfirmarRemoverCard.textContent;

    btnConfirmarRemoverCard.dataset.enviando = "1";
    btnConfirmarRemoverCard.disabled = true;
    btnConfirmarRemoverCard.textContent = "Removendo...";

    try {
      const res = await apiPost(`/kanban/api/cards/${idCardRemover}/inativar`, { motivo, descricao: desc });

      if (!res.ok){
        msgRemoverCard.textContent = (res.body && (res.body.msg || res.body.erro)) || `Erro ao remover (HTTP ${res.http}).`;
        msgRemoverCard.style.display = "block";
        return;
      }

      const idFaseAntesResposta = idNum(res.body?.id_fase_de || res.body?.id_fase_atual || 0);
      removerCardInativadoLocal(idCardRemover, idFaseAntesLocal || idFaseAntesResposta);
      fecharModalRemoverCard();
    } catch (erro) {
      console.error("Falha ao inativar card", erro);
      msgRemoverCard.textContent = "Erro de comunicação ao remover o card. Tente novamente.";
      msgRemoverCard.style.display = "block";
    } finally {
      btnConfirmarRemoverCard.dataset.enviando = "0";
      btnConfirmarRemoverCard.disabled = false;
      btnConfirmarRemoverCard.textContent = textoOriginalBotao || "Remover";
    }
  });

  function abrirModalInativarFase(idFase){
    if (!USUARIO_PODE_GERENCIAR_FASES_E_TAGS || !modalInativarFase) return;
    faseParaInativar = idFase;
    if (msgInativarFase) msgInativarFase.style.display = "none";
    modalInativarFase.style.display = "block";
  }

  function fecharModalInativarFase(){
    if (modalInativarFase) modalInativarFase.style.display = "none";
    faseParaInativar = null;
  }

  btnFecharInativarFase?.addEventListener("click", fecharModalInativarFase);
  modalInativarFase?.addEventListener("click", (e) => {
    if (e.target === modalInativarFase) fecharModalInativarFase();
  });

  btnConfirmarInativarFase?.addEventListener("click", async () => {
    if (!USUARIO_PODE_GERENCIAR_FASES_E_TAGS) return;
    if (msgInativarFase) msgInativarFase.style.display = "none";

    if (!faseParaInativar){
      if (msgInativarFase) {
        msgInativarFase.textContent = "Fase inválida.";
        msgInativarFase.style.display = "block";
      }
      return;
    }

    const res = await apiPost(`/kanban/api/fases/${faseParaInativar}/inativar`, {});

    if (!res.ok){
      const qtdAtivos = idNum(res.body?.QuantidadeCardsAtivos || 0);
      if (msgInativarFase) {
        msgInativarFase.textContent = qtdAtivos > 0
          ? `${(res.body && (res.body.msg || res.body.erro)) || "Não foi possível inativar a fase."} Cards ativos encontrados: ${qtdAtivos}.`
          : ((res.body && (res.body.msg || res.body.erro)) || `Erro ao inativar fase (HTTP ${res.http}).`);
        msgInativarFase.style.display = "block";
      }
      return;
    }

    fecharModalInativarFase();
    await carregar();
  });


  function limparEstadoCardAberto(){
    cardAbertoId = null;
    formularioSolicitacaoLiberadoNestaAbertura = false;
    versaoConcorrenciaCardAberto = "";
    cardAbertoConflitoExterno = false;
    estadoInicialCardAberto = null;
    fluxoContratoPersistidoCardAberto = null;
    if (modalCard) {
      modalCard.dataset.idFaseAtual = "";
      modalCard.dataset.jaPassouFaseFormularioContrato = "0";
    }
    msgCard.style.display = "none";
    msgCard.textContent = "";
    limparDadosNovoContratoFormulario();
    atualizarVisibilidadeDadosNovoContrato();
    atualizarVisibilidadeFormularioSolicitacaoContrato();
    atualizarEstadoSalvarCard();
  }

  function fecharModalCard(){
    const idCardFechado = idNum(cardAbertoId || inputIdCard?.value || 0);
    const tinhaBloqueioCarteira = Boolean(
      empresaPrincipalBloqueadaCarteiraAtual ||
      selectEmpresaCard?.dataset?.carteiraBloqueada === "1" ||
      safeStr(selectEmpresaCard?.dataset?.msgCarteiraBloqueada || "").trim()
    );

    if (modalCard) {
      modalCard.style.display = "none";
      modalCard.dataset.idFaseAtual = "";
      modalCard.dataset.jaPassouFaseFormularioContrato = "0";
    }

    if (tinhaBloqueioCarteira) {
      limparSelecaoEmpresaBloqueadaSemPopup("");
    }

    limparEstadoCardAberto();

    if (idCardFechado && tinhaBloqueioCarteira) {
      solicitarAtualizacaoAoVivoCardDescartado(idCardFechado);
    }
  }

  function fecharModalOrcamentoCard(){
    if (modalOrcamentoCard) {
      modalOrcamentoCard.style.display = "none";
    }
    cardOrcamentoAbertoId = null;
    if (orcamentoCardConteudo) {
      orcamentoCardConteudo.innerHTML = "";
    }
  }

  btnFecharCard.addEventListener("click", () => {
    fecharModalCard();
  });

  modalCard.addEventListener("click", (e) => {
    if (e.target === modalCard) {
      fecharModalCard();
    }
  });

  btnFecharOrcamentoCard?.addEventListener("click", () => {
    fecharModalOrcamentoCard();
  });

  btnImprimirOrcamentoCard?.addEventListener("click", () => {
    imprimirOrcamentoCard();
  });

  modalOrcamentoCard?.addEventListener("click", (e) => {
    if (e.target === modalOrcamentoCard) {
      fecharModalOrcamentoCard();
    }
  });

  selectEmpresaCard.addEventListener("change", async () => {
    sincronizarBuscaEmpresaComSelect();
    const idEmp = selectEmpresaCard.value || "";

    if (USUARIO_TEM_BLOQUEIO_CARTEIRA && idEmp && selectEmpresaCard.dataset.validandoCarteira !== "1") {
      const valorAnteriorSeguro = safeStr(selectEmpresaCard.dataset.valorCarteiraPermitido || "").trim();
      selectEmpresaCard.dataset.validandoCarteira = "1";
      try {
        const empresaSelecionada = obterEmpresaCatalogoPorId(idEmp);
        const validacaoCarteira = await validarEmpresaCarteiraVendedorPorId(idEmp, empresaSelecionada);
        if (!validacaoCarteira.permitida) {
          const empresaBloqueada = validacaoCarteira.empresa || empresaSelecionada || { NomeVendedorCarteira: "Vendedor responsável" };
          const mensagemBloqueio = safeStr(validacaoCarteira.msg || empresaBloqueada?.MensagemBloqueioCarteiraVendedor || "").trim() || mensagemEmpresaBloqueadaCarteira(empresaBloqueada);
          mostrarAvisoEmpresaBloqueadaCarteira(empresaBloqueada, mensagemBloqueio, { exibirPopup: true });
          limparSelecaoEmpresaBloqueadaSemPopup(valorAnteriorSeguro);
          return;
        } else {
          definirBloqueioCarteiraEmpresaPrincipal(null);
          selectEmpresaCard.dataset.valorCarteiraPermitido = safeStr(idEmp).trim();
        }
      } finally {
        selectEmpresaCard.dataset.validandoCarteira = "0";
      }
    } else if (!idEmp) {
      definirBloqueioCarteiraEmpresaPrincipal(null);
      selectEmpresaCard.dataset.valorCarteiraPermitido = "";
    }

    const tipoClienteAtual = safeStr(selectTipoClienteDescontoCard?.value || "").trim();
    const origemAtual = safeStr(selectOrigemAtendimentoCard?.value || "").trim();

    setEmpresaPreviewById(idEmp);
    montarSelectTipoClienteDesconto(tipoClienteAtual);
    montarSelectOrigemAtendimento(origemAtual);

    if (!idEmp) {
      limparDadosNovoContratoFormulario();
    } else {
      await aplicarSegmentoAutomaticoDaEmpresaSelecionada(idEmp);
    }

    await carregarFluxoContratoParaEmpresa(idEmp);
    atualizarVisibilidadeDadosNovoContrato();
    aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente();
    sincronizarCnpjAgenciaHeaderComAgenciaEfetiva();
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputSegmentoCardBusca?.addEventListener("focus", () => {
    abrirListaCnaesCombobox();
    buscarCnaesRemoto(inputSegmentoCardBusca.value || "").catch((erro) => {
      console.warn("focus segmento: falhou", erro);
    });
  });

  inputSegmentoCardBusca?.addEventListener("click", () => {
    abrirListaCnaesCombobox();
    buscarCnaesRemoto(inputSegmentoCardBusca.value || "").catch((erro) => {
      console.warn("click segmento: falhou", erro);
    });
  });

  inputSegmentoCardBusca?.addEventListener("input", () => {
    abrirListaCnaesCombobox();
    const termo = inputSegmentoCardBusca.value || "";
    if (!safeStr(termo).trim()) {
      renderizarListaCnaesCombobox(termo);
      buscarCnaesRemoto(termo).catch((erro) => {
        console.warn("input segmento vazio: falhou", erro);
      });
      return;
    }

    renderizarListaCnaesCombobox(termo);
    buscarCnaesRemoto(termo).catch((erro) => {
      console.warn("input segmento remoto: falhou", erro);
    });
  });

  inputSegmentoCardBusca?.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      const primeira = cnaesResultadoComboboxAtual[0] || filtrarCnaesCombobox(inputSegmentoCardBusca.value || "")[0] || null;
      if (primeira && primeira.IDDimCnaes) {
        selecionarCnaePorIdComGarantia(String(primeira.IDDimCnaes), true).catch((erro) => {
          console.warn("keydown segmento: falhou ao selecionar primeira opção", erro);
        });
      } else {
        reconciliarBuscaSegmentoDigitada();
      }
      return;
    }

    if (evento.key === "Escape") {
      fecharListaCnaesCombobox();
    }
  });

  inputSegmentoCardBusca?.addEventListener("blur", () => {
    window.setTimeout(() => {
      reconciliarBuscaSegmentoDigitada();
    }, 120);
  });

  btnToggleSegmentoCard?.addEventListener("click", () => {
    if (listaSegmentoCardBusca?.hidden) {
      abrirListaCnaesCombobox();
      buscarCnaesRemoto(inputSegmentoCardBusca?.value || "").catch((erro) => {
        console.warn("toggle segmento: falhou", erro);
      });
      return;
    }
    fecharListaCnaesCombobox();
  });

  inputEmpresaCardBusca?.addEventListener("focus", () => {
    abrirListaEmpresasCombobox();
    buscarEmpresasRemoto(inputEmpresaCardBusca.value || "").catch((erro) => {
      console.warn("focus empresa: falhou", erro);
    });
  });

  inputEmpresaCardBusca?.addEventListener("click", () => {
    abrirListaEmpresasCombobox();
    buscarEmpresasRemoto(inputEmpresaCardBusca.value || "").catch((erro) => {
      console.warn("click empresa: falhou", erro);
    });
  });

  inputEmpresaCardBusca?.addEventListener("input", () => {
    abrirListaEmpresasCombobox();

    const termo = inputEmpresaCardBusca.value || "";
    const termoDigits = normalizaCnpj(termo);

    if (!safeStr(termo).trim()) {
      buscarEmpresasRemoto("").catch((erro) => {
        console.warn("input empresa vazio: falhou", erro);
      });
      return;
    }

    if (safeStr(termo).trim().length >= 2 || termoDigits.length >= 4) {
      buscarEmpresasRemoto(termo).catch((erro) => {
        console.warn("input empresa remoto: falhou", erro);
        renderizarListaEmpresasCombobox(termo);
      });
      return;
    }

    renderizarListaEmpresasCombobox(termo);
  });

  inputEmpresaCardBusca?.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      const primeira = empresasResultadoComboboxAtual[0] || filtrarEmpresasCombobox(inputEmpresaCardBusca.value || "")[0] || null;
      if (primeira) {
        const id = safeStr(primeira?.IDEmpresa ?? primeira?.IDEmpresaProprietaria ?? primeira?.ID ?? "").trim();
        if (id) {
          selecionarEmpresaCombobox(id, true).catch((erro) => {
            console.warn("keydown empresa: falhou ao selecionar primeira opção", erro);
          });
          return;
        }
      }
      reconciliarBuscaEmpresaDigitada();
      return;
    }

    if (evento.key === "Escape") {
      evento.preventDefault();
      sincronizarBuscaEmpresaComSelect();
      fecharListaEmpresasCombobox();
      return;
    }

    if (evento.key === "ArrowDown") {
      abrirListaEmpresasCombobox();
    }
  });

  inputAgenciaCardBusca?.addEventListener("focus", () => {
    abrirListaAgenciasCombobox();
    buscarEmpresasRemoto(inputAgenciaCardBusca.value || "", {
      tipo: "agencia",
      listaDestino: listaAgenciaCardBusca,
      renderizador: renderizarListaAgenciasCombobox,
    }).catch((erro) => {
      console.warn("focus agencia: falhou", erro);
    });
  });

  inputAgenciaCardBusca?.addEventListener("click", () => {
    abrirListaAgenciasCombobox();
    buscarEmpresasRemoto(inputAgenciaCardBusca.value || "", {
      tipo: "agencia",
      listaDestino: listaAgenciaCardBusca,
      renderizador: renderizarListaAgenciasCombobox,
    }).catch((erro) => {
      console.warn("click agencia: falhou", erro);
    });
  });

  inputAgenciaCardBusca?.addEventListener("input", () => {
    abrirListaAgenciasCombobox();

    const termo = inputAgenciaCardBusca.value || "";
    const termoDigits = normalizaCnpj(termo);

    if (!safeStr(termo).trim()) {
      buscarEmpresasRemoto("", {
        tipo: "agencia",
        listaDestino: listaAgenciaCardBusca,
        renderizador: renderizarListaAgenciasCombobox,
      }).catch((erro) => {
        console.warn("input agencia vazio: falhou", erro);
      });
      return;
    }

    if (safeStr(termo).trim().length >= 2 || termoDigits.length >= 4) {
      buscarEmpresasRemoto(termo, {
        tipo: "agencia",
        listaDestino: listaAgenciaCardBusca,
        renderizador: renderizarListaAgenciasCombobox,
      }).catch((erro) => {
        console.warn("input agencia remoto: falhou", erro);
        renderizarListaAgenciasCombobox(termo);
      });
      return;
    }

    renderizarListaAgenciasCombobox(termo);
  });

  inputAgenciaCardBusca?.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      const primeira = agenciasResultadoComboboxAtual[0] || filtrarEmpresasCombobox(inputAgenciaCardBusca.value || "")[0] || null;
      if (primeira) {
        const id = safeStr(primeira?.IDEmpresa ?? primeira?.IDEmpresaProprietaria ?? primeira?.ID ?? "").trim();
        if (id) {
          selecionarAgenciaPorIdComGarantia(id, false).catch((erro) => {
            console.warn("keydown agencia: falhou ao selecionar primeira opção", erro);
          });
          return;
        }
      }
      reconciliarBuscaAgenciaDigitada();
      return;
    }

    if (evento.key === "Escape") {
      evento.preventDefault();
      sincronizarBuscaAgenciaComSelect();
      fecharListaAgenciasCombobox();
      return;
    }
  });

  btnToggleAgenciaCard?.addEventListener("click", () => {
    if (listaAgenciaCardBusca?.hidden) abrirListaAgenciasCombobox();
    else {
      sincronizarBuscaAgenciaComSelect();
      fecharListaAgenciasCombobox();
    }
  });

  btnLimparAgencia?.addEventListener("click", () => {
    selecionarAgenciaPorIdComGarantia("", false).catch((erro) => {
      console.warn("btnLimparAgencia: falhou", erro);
    });
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputClienteDiretoCardBusca?.addEventListener("focus", () => {
    abrirListaClienteDiretoCombobox();
    buscarEmpresasRemoto(inputClienteDiretoCardBusca.value || "", {
      tipo: "cliente_direto",
      listaDestino: listaClienteDiretoCardBusca,
      renderizador: renderizarListaClienteDiretoCombobox,
    }).catch((erro) => {
      console.warn("focus cliente direto: falhou", erro);
    });
  });

  inputClienteDiretoCardBusca?.addEventListener("click", () => {
    abrirListaClienteDiretoCombobox();
    buscarEmpresasRemoto(inputClienteDiretoCardBusca.value || "", {
      tipo: "cliente_direto",
      listaDestino: listaClienteDiretoCardBusca,
      renderizador: renderizarListaClienteDiretoCombobox,
    }).catch((erro) => {
      console.warn("click cliente direto: falhou", erro);
    });
  });

  inputClienteDiretoCardBusca?.addEventListener("input", () => {
    abrirListaClienteDiretoCombobox();
    const termo = inputClienteDiretoCardBusca.value || "";
    const termoDigits = normalizaCnpj(termo);
    if (!safeStr(termo).trim()) {
      buscarEmpresasRemoto("", {
        tipo: "cliente_direto",
        listaDestino: listaClienteDiretoCardBusca,
        renderizador: renderizarListaClienteDiretoCombobox,
      }).catch((erro) => {
        console.warn("input cliente direto vazio: falhou", erro);
      });
      return;
    }
    if (safeStr(termo).trim().length >= 2 || termoDigits.length >= 4) {
      buscarEmpresasRemoto(termo, {
        tipo: "cliente_direto",
        listaDestino: listaClienteDiretoCardBusca,
        renderizador: renderizarListaClienteDiretoCombobox,
      }).catch((erro) => {
        console.warn("input cliente direto remoto: falhou", erro);
        renderizarListaClienteDiretoCombobox(termo);
      });
      return;
    }
    renderizarListaClienteDiretoCombobox(termo);
  });

  inputClienteDiretoCardBusca?.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      const primeira = clientesDiretoResultadoComboboxAtual[0] || filtrarEmpresasCombobox(inputClienteDiretoCardBusca.value || "")[0] || null;
      if (primeira) {
        const id = safeStr(primeira?.IDEmpresa ?? primeira?.IDEmpresaProprietaria ?? primeira?.ID ?? "").trim();
        if (id) {
          selecionarClienteDiretoPorIdComGarantia(id, false).catch((erro) => {
            console.warn("keydown cliente direto: falhou ao selecionar primeira opção", erro);
          });
          return;
        }
      }
      reconciliarBuscaClienteDiretoDigitada();
      return;
    }

    if (evento.key === "Escape") {
      evento.preventDefault();
      sincronizarBuscaClienteDiretoComSelect();
      fecharListaClienteDiretoCombobox();
      return;
    }
  });

  btnToggleClienteDiretoCard?.addEventListener("click", () => {
    if (listaClienteDiretoCardBusca?.hidden) abrirListaClienteDiretoCombobox();
    else {
      sincronizarBuscaClienteDiretoComSelect();
      fecharListaClienteDiretoCombobox();
    }
  });

  btnLimparClienteDireto?.addEventListener("click", () => {
    selecionarClienteDiretoPorIdComGarantia("", false).catch((erro) => {
      console.warn("btnLimparClienteDireto: falhou", erro);
    });
  });

  inputBureauCardBusca?.addEventListener("focus", () => {
    abrirListaBureauCombobox();
    buscarEmpresasRemoto(inputBureauCardBusca.value || "", {
      tipo: "bureau",
      listaDestino: listaBureauCardBusca,
      renderizador: renderizarListaBureauCombobox,
    }).catch((erro) => {
      console.warn("focus bureau: falhou", erro);
    });
  });

  inputBureauCardBusca?.addEventListener("click", () => {
    abrirListaBureauCombobox();
    buscarEmpresasRemoto(inputBureauCardBusca.value || "", {
      tipo: "bureau",
      listaDestino: listaBureauCardBusca,
      renderizador: renderizarListaBureauCombobox,
    }).catch((erro) => {
      console.warn("click bureau: falhou", erro);
    });
  });

  inputBureauCardBusca?.addEventListener("input", () => {
    abrirListaBureauCombobox();
    const termo = inputBureauCardBusca.value || "";
    const termoDigits = normalizaCnpj(termo);
    if (!safeStr(termo).trim()) {
      buscarEmpresasRemoto("", {
        tipo: "bureau",
        listaDestino: listaBureauCardBusca,
        renderizador: renderizarListaBureauCombobox,
      }).catch((erro) => {
        console.warn("input bureau vazio: falhou", erro);
      });
      return;
    }
    if (safeStr(termo).trim().length >= 2 || termoDigits.length >= 4) {
      buscarEmpresasRemoto(termo, {
        tipo: "bureau",
        listaDestino: listaBureauCardBusca,
        renderizador: renderizarListaBureauCombobox,
      }).catch((erro) => {
        console.warn("input bureau remoto: falhou", erro);
        renderizarListaBureauCombobox(termo);
      });
      return;
    }
    renderizarListaBureauCombobox(termo);
  });

  inputBureauCardBusca?.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      const primeira = bureauResultadoComboboxAtual[0] || filtrarEmpresasCombobox(inputBureauCardBusca.value || "")[0] || null;
      if (primeira) {
        const id = safeStr(primeira?.IDEmpresa ?? primeira?.IDEmpresaProprietaria ?? primeira?.ID ?? "").trim();
        if (id) {
          selecionarBureauPorIdComGarantia(id, false).catch((erro) => {
            console.warn("keydown bureau: falhou ao selecionar primeira opção", erro);
          });
          return;
        }
      }
      reconciliarBuscaBureauDigitada();
      return;
    }

    if (evento.key === "Escape") {
      evento.preventDefault();
      sincronizarBuscaBureauComSelect();
      fecharListaBureauCombobox();
      return;
    }
  });

  btnToggleBureauCard?.addEventListener("click", () => {
    if (listaBureauCardBusca?.hidden) abrirListaBureauCombobox();
    else {
      sincronizarBuscaBureauComSelect();
      fecharListaBureauCombobox();
    }
  });

  btnLimparBureau?.addEventListener("click", () => {
    selecionarBureauPorIdComGarantia("", false).catch((erro) => {
      console.warn("btnLimparBureau: falhou", erro);
    });
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputIntermediarioCardBusca?.addEventListener("focus", () => {
    abrirListaIntermediarioCombobox();
    buscarEmpresasRemoto(inputIntermediarioCardBusca.value || "", {
      tipo: "intermediario",
      listaDestino: listaIntermediarioCardBusca,
      renderizador: renderizarListaIntermediarioCombobox,
    }).catch((erro) => {
      console.warn("focus intermediário: falhou", erro);
    });
  });

  inputIntermediarioCardBusca?.addEventListener("click", () => {
    abrirListaIntermediarioCombobox();
    buscarEmpresasRemoto(inputIntermediarioCardBusca.value || "", {
      tipo: "intermediario",
      listaDestino: listaIntermediarioCardBusca,
      renderizador: renderizarListaIntermediarioCombobox,
    }).catch((erro) => {
      console.warn("click intermediário: falhou", erro);
    });
  });

  inputIntermediarioCardBusca?.addEventListener("input", () => {
    abrirListaIntermediarioCombobox();
    const termo = inputIntermediarioCardBusca.value || "";
    const termoDigits = normalizaCnpj(termo);
    if (!safeStr(termo).trim()) {
      buscarEmpresasRemoto("", {
        tipo: "intermediario",
        listaDestino: listaIntermediarioCardBusca,
        renderizador: renderizarListaIntermediarioCombobox,
      }).catch((erro) => {
        console.warn("input intermediário vazio: falhou", erro);
      });
      return;
    }
    if (safeStr(termo).trim().length >= 2 || termoDigits.length >= 4) {
      buscarEmpresasRemoto(termo, {
        tipo: "intermediario",
        listaDestino: listaIntermediarioCardBusca,
        renderizador: renderizarListaIntermediarioCombobox,
      }).catch((erro) => {
        console.warn("input intermediário remoto: falhou", erro);
        renderizarListaIntermediarioCombobox(termo);
      });
      return;
    }
    renderizarListaIntermediarioCombobox(termo);
  });

  inputIntermediarioCardBusca?.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      const primeira = intermediariosResultadoComboboxAtual[0] || filtrarEmpresasCombobox(inputIntermediarioCardBusca.value || "")[0] || null;
      if (primeira) {
        const id = safeStr(primeira?.IDEmpresa ?? primeira?.IDEmpresaProprietaria ?? primeira?.ID ?? "").trim();
        if (id) {
          selecionarIntermediarioPorIdComGarantia(id, false).catch((erro) => {
            console.warn("keydown intermediário: falhou ao selecionar primeira opção", erro);
          });
          return;
        }
      }
      reconciliarBuscaIntermediarioDigitada();
      return;
    }

    if (evento.key === "Escape") {
      evento.preventDefault();
      sincronizarBuscaIntermediarioComSelect();
      fecharListaIntermediarioCombobox();
      return;
    }
  });

  btnToggleIntermediarioCard?.addEventListener("click", () => {
    if (listaIntermediarioCardBusca?.hidden) abrirListaIntermediarioCombobox();
    else {
      sincronizarBuscaIntermediarioComSelect();
      fecharListaIntermediarioCombobox();
    }
  });

  btnLimparIntermediario?.addEventListener("click", () => {
    selecionarIntermediarioPorIdComGarantia("", false).catch((erro) => {
      console.warn("btnLimparIntermediario: falhou", erro);
    });
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputTelefoneCard?.addEventListener("input", () => {
    inputTelefoneCard.value = normalizarTelefoneContato(inputTelefoneCard.value || "");
  });

  btnToggleEmpresaCard?.addEventListener("click", () => {
    if (listaEmpresaCardBusca?.hidden) abrirListaEmpresasCombobox();
    else {
      sincronizarBuscaEmpresaComSelect();
      fecharListaEmpresasCombobox();
    }
  });

  document.addEventListener("click", (evento) => {
    const clicouDentroListaPainelFace = !!evento.target?.closest?.('[data-role="lista-painel-busca"], .kb-painel-face-opcao, .kb-painel-face-opcao-check');

    if (comboEmpresaCard && !comboEmpresaCard.contains(evento.target)) {
      reconciliarBuscaEmpresaDigitada();
    }

    if (comboContratoCard && !wrapSelectContratoCard?.hidden && !comboContratoCard.contains(evento.target)) {
      reconciliarBuscaContratoDigitada();
    }

    if (comboAgenciaCard && !comboAgenciaCard.contains(evento.target)) {
      reconciliarBuscaAgenciaDigitada();
    }

    if (comboClienteDiretoCard && !comboClienteDiretoCard.contains(evento.target)) {
      reconciliarBuscaClienteDiretoDigitada();
    }

    if (comboBureauCard && !comboBureauCard.contains(evento.target)) {
      reconciliarBuscaBureauDigitada();
    }

    if (comboIntermediarioCard && !comboIntermediarioCard.contains(evento.target)) {
      reconciliarBuscaIntermediarioDigitada();
    }

    if (clicouDentroListaPainelFace || reconciliacaoPainelFaceEstaBloqueada()) {
      return;
    }

    for (const bloco of (painelFaceLista?.querySelectorAll('.kb-painel-item') || [])) {
      const comboPainel = bloco.querySelector('[data-role="combo-painel"]');
      if (comboPainel && !comboPainel.contains(evento.target)) {
        reconciliarBuscaPainelDigitada(bloco);
      }
    }
  });

  btnLimparEmpresa.addEventListener("click", async () => {
    const tipoClienteAtual = safeStr(selectTipoClienteDescontoCard?.value || "").trim();
    const origemAtual = safeStr(selectOrigemAtendimentoCard?.value || "").trim();

    await selecionarEmpresaPorIdComGarantia("", true);
    setEmpresaPreviewByObj(null);
    montarSelectTipoClienteDesconto(tipoClienteAtual);
    montarSelectOrigemAtendimento(origemAtual);
    atualizarVisibilidadeEmpresasRelacionadasCard();

    resetarFluxoContrato();
    agendarSincronizacaoFormularioSolicitacao();
  });

  selectTipoClienteDescontoCard?.addEventListener("change", () => {
    atualizarVisibilidadeEmpresasRelacionadasCard();
    aplicarVisibilidadeCamposFormularioSolicitacaoPorTipoCliente();
    sincronizarCnpjAgenciaHeaderComAgenciaEfetiva();
    agendarSincronizacaoFormularioSolicitacao();
  });

  selectOrigemAtendimentoCard?.addEventListener("change", () => {
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputMarcaCard?.addEventListener("input", () => {
    sincronizarMarcaExibidaHeaderComInputTopo();
    agendarSincronizacaoFormularioSolicitacao();
  });

  inputMarcaCard?.addEventListener("change", () => {
    sincronizarMarcaExibidaHeaderComInputTopo();
    agendarSincronizacaoFormularioSolicitacao();
  });

  function registrarAlteracaoCampoFormularioSolicitacao(evento) {
    const alvo = evento?.target || null;
    if (!alvo?.closest?.("#wrapFormularioSolicitacaoContrato")) return;

    const wrapCampo = alvo.closest?.(".kb-contrato-campo");
    if (wrapCampo && safeStr(alvo.value || "").trim()) {
      wrapCampo.classList.remove("is-invalido");
    }

    agendarSincronizacaoFormularioSolicitacao();

    if (typeof atualizarEstadoSalvarCard === "function") {
      atualizarEstadoSalvarCard();
    }
  }

  document.addEventListener("input", registrarAlteracaoCampoFormularioSolicitacao);
  document.addEventListener("change", registrarAlteracaoCampoFormularioSolicitacao);

  inputContratoCardBusca?.addEventListener("focus", () => {
    abrirListaContratosCombobox();
  });

  inputContratoCardBusca?.addEventListener("click", () => {
    abrirListaContratosCombobox();
  });

  inputContratoCardBusca?.addEventListener("input", () => {
    abrirListaContratosCombobox();
    renderizarListaContratosCombobox(inputContratoCardBusca.value || "");
  });

  inputContratoCardBusca?.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      const primeira = contratosResultadoComboboxAtual[0] || filtrarContratosCombobox(inputContratoCardBusca.value || "")[0] || null;
      if (primeira?.id_contrato) {
        selecionarContratoCombobox(primeira.id_contrato, true);
        return;
      }
      reconciliarBuscaContratoDigitada();
      return;
    }

    if (evento.key === "Escape") {
      evento.preventDefault();
      sincronizarBuscaContratoComSelect();
      fecharListaContratosCombobox();
      return;
    }

    if (evento.key === "ArrowDown") {
      abrirListaContratosCombobox();
    }
  });

  btnToggleContratoCard?.addEventListener("click", () => {
    if (listaContratoCardBusca?.hidden) abrirListaContratosCombobox();
    else {
      sincronizarBuscaContratoComSelect();
      fecharListaContratosCombobox();
    }
  });

  selectContratoCard?.addEventListener("change", async () => {
    await onContratoCardChange();
  });

  selectModoContratoCard?.addEventListener("change", async () => {
    aplicarTipoDocumentoAditivoPadraoNosItensFormulario();
    await onModoContratoCardChange();
  });

  selectCodPontoContratoCard?.addEventListener("change", async () => {
    await onCodPontoContratoCardChange();
  });

  selectCodFaceContratoCard?.addEventListener("change", async () => {
    await onCodFaceContratoCardChange();
  });

  btnAbrirCadastroEmpresa?.addEventListener("click", async () => {
    await abrirModalCadastroEmpresa();
  });

  btnFecharCadastroEmpresa?.addEventListener("click", () => {
    fecharModalCadastroEmpresa();
  });

  modalCadastroEmpresa?.addEventListener("click", (e) => {
    if (e.target === modalCadastroEmpresa) {
      fecharModalCadastroEmpresa();
    }
  });

  cadEmpresaCnpj?.addEventListener("input", () => {
    const digits = normalizaCnpj(cadEmpresaCnpj.value || "");
    cadEmpresaCnpj.value = mascaraCnpj(digits);

    const cnpjAtualDoRegistro = normalizaCnpj(empresaCadastroUltimoCnpjConsultado || "");
    if (digits !== cnpjAtualDoRegistro) {
      cadEmpresaId.value = "";
    }

    agendarBuscaAutomaticaCadastroEmpresa();
  });

  btnSalvarCadastroEmpresa?.addEventListener("click", async () => {
    await salvarCadastroEmpresa();
  });

  btnAdicionarPainelFace?.addEventListener("click", () => {
    const novoBloco = criarPainelFaceItem();
    painelFaceLista?.appendChild(novoBloco);
    atualizarTitulosPainelFace();
    atualizarVisibilidadeContratoAditivoDoBloco(novoBloco);
  });

  function mostrarMensagemCard(texto){
    msgCard.textContent = safeStr(texto);
    msgCard.style.display = "block";
  }

  function atualizarEstadoSalvarCard(){
    atualizarVisibilidadeFormularioSolicitacaoContrato();
    if (!btnSalvarCard) return;

    const temVersao = !!safeStr(versaoConcorrenciaCardAberto);
    const podeSalvar = temVersao && !cardAbertoConflitoExterno && !salvandoCardAtual && !carregandoCardAtual;

    // Carteira bloqueada não desabilita o botão.
    // O vendedor precisa conseguir clicar para receber o popup; o salvamento é barrado no clique e no backend.
    btnSalvarCard.disabled = !podeSalvar;
    btnSalvarCard.title = podeSalvar
      ? ""
      : (salvandoCardAtual
          ? "Salvando alterações do card..."
          : (carregandoCardAtual
              ? "Aguarde o detalhe completo do card carregar antes de salvar."
              : (!temVersao
                  ? "Este card foi carregado sem versão de concorrência. O backend precisa devolver essa versão para permitir salvar."
                  : "Este card recebeu atualização externa. Reabra o card antes de salvar.")));
  }


  function criarCampoOrcamento(label, valor){
    return el("div", {class:"kb-orcamento-campo"}, [
      el("span", {class:"kb-orcamento-campo-k"}, [label]),
      el("span", {class:"kb-orcamento-campo-v", title: safeStr(valor || "—")}, [safeStr(valor || "—") || "—"])
    ]);
  }

  function criarGaleriaOrcamento(imagens, altBase){
    const lista = Array.isArray(imagens) && imagens.length ? imagens : [{ url:URL_IMAGEM_PAINEL_PUBLICITARIO, fallback:true }];
    let indiceAtual = 0;

    const imagem = el("img", {
      class:"kb-orcamento-imagem",
      src: safeStr(lista[0]?.url || URL_IMAGEM_PAINEL_PUBLICITARIO),
      alt: altBase,
      loading:"lazy",
      decoding:"async"
    });

    imagem.addEventListener("error", () => {
      imagem.src = URL_IMAGEM_PAINEL_PUBLICITARIO;
    });

    const indicador = el("span", {class:"kb-orcamento-galeria-indicador"}, [`1 / ${lista.length}`]);
    const wrapImagem = el("div", {class:"kb-orcamento-imagem-wrap"}, [imagem]);

    function atualizarGaleria(){
      const item = lista[indiceAtual] || lista[0] || {};
      imagem.src = safeStr(item.url || URL_IMAGEM_PAINEL_PUBLICITARIO);
      indicador.textContent = `${indiceAtual + 1} / ${lista.length}`;
    }

    if (lista.length > 1){
      const btnPrev = el("button", {
        class:"kb-orcamento-galeria-btn prev",
        type:"button",
        onclick: () => {
          indiceAtual = (indiceAtual - 1 + lista.length) % lista.length;
          atualizarGaleria();
        }
      }, ["‹"]);

      const btnNext = el("button", {
        class:"kb-orcamento-galeria-btn next",
        type:"button",
        onclick: () => {
          indiceAtual = (indiceAtual + 1) % lista.length;
          atualizarGaleria();
        }
      }, ["›"]);

      wrapImagem.appendChild(el("div", {class:"kb-orcamento-galeria-nav"}, [btnPrev, btnNext, indicador]));
    } else {
      wrapImagem.appendChild(indicador);
    }

    return el("div", {class:"kb-orcamento-galeria"}, [wrapImagem]);
  }

  function renderizarOrcamentoCard(payload){
    if (!orcamentoCardConteudo) return;

    const dados = payload && typeof payload === "object" ? payload : {};
    const resumo = dados.resumo && typeof dados.resumo === "object" ? dados.resumo : {};
    const empresa = dados.empresa && typeof dados.empresa === "object" ? dados.empresa : {};
    const cabecalho = dados.cabecalho && typeof dados.cabecalho === "object" ? dados.cabecalho : {};
    const itens = Array.isArray(dados.itens) ? dados.itens : [];
    const tituloCard = safeStr(dados.titulo_card || `Card ${idNum(dados.id_card)}`).trim() || `Card ${idNum(dados.id_card)}`;
    const descricaoCard = safeStr(dados.descricao_card || "").trim();
    const empresaNome = safeStr(empresa.razao_social || "").trim();

    const urlCabecalho =
      safeStr(cabecalho.url || URL_CABECALHO_ORCAMENTO_PADRAO).trim();

    const altCabecalho =
      safeStr(cabecalho.alt || "Cabeçalho do orçamento Euromídia").trim() || "Cabeçalho do orçamento Euromídia";

    orcamentoCardConteudo.innerHTML = "";

    const blocoTitulos = el("div", {class:"kb-orcamento-head-titulos"}, [
      el("span", {class:"kb-orcamento-kicker"}, ["Razão Social"]),
      el("h2", {class:"kb-orcamento-titulo", title: empresaNome || ""}, [empresaNome || "Empresa não vinculada"]),
      el("p", {class:"kb-orcamento-subtitulo"}, [
        tituloCard,
        descricaoCard ? ` • ${descricaoCard}` : ""
      ])
    ]);

    const headChildren = [];

    if (urlCabecalho){
      const imagemCabecalho = el("img", {
        class:"kb-orcamento-head-logo",
        src: urlCabecalho,
        alt: altCabecalho,
        loading:"eager",
        decoding:"async"
      });

      imagemCabecalho.addEventListener("error", () => {
        imagemCabecalho.style.display = "none";
      });

      headChildren.push(
        el("div", {class:"kb-orcamento-head-marca"}, [imagemCabecalho])
      );
    }

    headChildren.push(
      el("div", {class:"kb-orcamento-head-corpo"}, [
        blocoTitulos
      ])
    );

    const head = el("section", {
      class:`kb-orcamento-head${urlCabecalho ? "" : " sem-marca"}`
    }, headChildren);

    const blocoEmpresa = el("section", {class:"kb-orcamento-empresa"}, [
      el("p", {class:"kb-orcamento-bloco-titulo"}, ["Dados da empresa"]),
      el("div", {class:"kb-orcamento-empresa-grid"}, [
        criarCampoOrcamento("Razão social", empresa.razao_social),
        criarCampoOrcamento("CNPJ", mascaraCnpj(empresa.cnpj || ""))
      ])
    ]);

    const lista = el("section", {class:"kb-orcamento-lista"}, []);

    if (!itens.length){
      lista.appendChild(el("div", {class:"kb-orcamento-vazio"}, [
        "Este card ainda não possui painéis vinculados para montar o orçamento."
      ]));
    } else {
      itens.forEach((item, indice) => {
        const imagens = Array.isArray(item.imagens) ? item.imagens : [];
        const galeria = criarGaleriaOrcamento(imagens, `Painel ${indice + 1}`);
        const valorExibido = Number(item.valor_exibido || 0);

        const descontoTexto = item.percentual_desconto != null
          ? `${formatarNumeroBR(Number(item.percentual_desconto), 2)}%`
          : "—";

        const valorTotalTexto = item.valor_total != null
          ? formatarMoedaBR(Number(item.valor_total))
          : (item.preco_venda_atual != null ? formatarMoedaBR(Number(item.preco_venda_atual)) : "—");

        const valorNegociadoTexto = item.valor_negociado != null
          ? formatarMoedaBR(Number(item.valor_negociado))
          : (item.valor_final != null ? formatarMoedaBR(Number(item.valor_final)) : "—");

        const artigo = el("article", {class:"kb-orcamento-item"}, [
          galeria,
          el("div", {class:"kb-orcamento-item-info"}, [
            el("div", {class:"kb-orcamento-item-topo"}, [
              el("span", {class:"kb-orcamento-item-indice"}, [`Item ${indice}`])
            ]),
            el("div", {class:"kb-orcamento-item-resumo-linha"}, [
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["CodFace"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [safeStr(item.cod_face || "—") || "—"])
              ]),
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["Tipo produto"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [safeStr(item.tipo_produto || item.tipo_painel || "—") || "—"])
              ]),
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["Município"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [safeStr(item.municipio || item.cidade || "—") || "—"])
              ]),
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["UF"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [safeStr(item.uf || "—") || "—"])
              ]),
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["Localização"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [safeStr(item.localizacao || item.endereco || "—") || "—"])
              ]),
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["Valor total"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [valorTotalTexto])
              ]),
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["Desconto"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [descontoTexto])
              ]),
              el("div", {class:"kb-orcamento-item-resumo-campo"}, [
                el("span", {class:"kb-orcamento-item-resumo-k"}, ["Valor negociado"]),
                el("span", {class:"kb-orcamento-item-resumo-v"}, [valorNegociadoTexto])
              ])
            ]),
            el("div", {class:"kb-orcamento-dados-grid"}, [
              criarCampoOrcamento("Painel", safeStr(item.nome_painel || `Painel ${indice}`)),
              criarCampoOrcamento("Período de campanha", safeStr(item.periodo_exibicao || "Período de campanha não informado") + (item.exibicoes_dia ? ` • ${item.exibicoes_dia} exibições/dia` : ""))
            ])
          ])
        ]);

        lista.appendChild(artigo);
      });
    }

    orcamentoCardConteudo.appendChild(head);
    orcamentoCardConteudo.appendChild(blocoEmpresa);
    orcamentoCardConteudo.appendChild(lista);
  }


  function montarHtmlImpressaoOrcamentoCard(){
    const conteudoAtual = orcamentoCardConteudo?.innerHTML || "";
    if (!safeStr(conteudoAtual).trim()){
      return "";
    }

    const tituloDocumento = cardOrcamentoAbertoId
      ? `Orçamento do card ${cardOrcamentoAbertoId}`
      : "Orçamento comercial";

    const estilosImpressao = `
      *{ box-sizing:border-box; }
      html, body{
        margin:0;
        padding:0;
        background:#f5f7fb;
        color:#0f172a;
        font-family: Arial, Helvetica, sans-serif;
      }
      body{
        padding:24px;
      }
      .kb-orcamento-print-page{
        max-width:1180px;
        margin:0 auto;
        display:grid;
        gap:16px;
      }
      .kb-orcamento-wrap{
        display:grid;
        gap:16px;
      }
      .kb-orcamento-head{
        display:grid;
        grid-template-columns:1fr;
        gap:0;
        align-items:stretch;
        padding:0;
        border:1px solid rgba(15,23,42,.12);
        border-radius:20px;
        background:linear-gradient(180deg, rgba(255,255,255,.98) 0%, rgba(248,250,252,.98) 100%);
        box-shadow:none;
        overflow:hidden;
      }
      .kb-orcamento-head.sem-marca{
        padding-top:14px;
      }
      .kb-orcamento-head-marca{
        min-width:0;
        min-height:260px;
        height:260px;
        padding:0;
        margin:0;
        border:0;
        border-bottom:1px solid rgba(11,78,162,.10);
        background:#0b43ad;
        display:flex;
        align-items:center;
        justify-content:center;
        overflow:hidden;
      }
      .kb-orcamento-head-logo{
        width:100%;
        height:100%;
        object-fit:cover;
        object-position:center center;
        display:block;
      }
      .kb-orcamento-head-corpo{
        display:grid;
        grid-template-columns:minmax(0, 1fr) minmax(300px, 380px);
        gap:16px;
        align-items:end;
        padding:0 18px 18px 18px;
        margin-top:-72px;
        position:relative;
        z-index:2;
      }
      .kb-orcamento-head-titulos{
        min-width:0;
        display:grid;
        align-content:end;
        gap:6px;
        padding:18px 20px;
        border:1px solid rgba(15,23,42,.12);
        border-radius:18px;
        background:rgba(255,255,255,.96);
      }
      .kb-orcamento-kicker{
        font-size:12px;
        font-weight:900;
        text-transform:uppercase;
        letter-spacing:.06em;
        color:rgba(15,23,42,.58);
      }
      .kb-orcamento-titulo{
        margin:0;
        font-size:30px;
        line-height:1.08;
        letter-spacing:-.03em;
        color:#0f172a;
        font-weight:950;
      }
      .kb-orcamento-subtitulo{
        margin:0;
        font-size:13px;
        line-height:1.4;
        color:rgba(15,23,42,.70);
      }
      .kb-orcamento-resumo{
        display:grid;
        grid-template-columns:repeat(2, minmax(140px, 1fr));
        gap:10px;
        min-width:320px;
      }
      .kb-orcamento-resumo-item,
      .kb-orcamento-empresa,
      .kb-orcamento-vazio,
      .kb-orcamento-item{
        border:1px solid rgba(15,23,42,.12);
        border-radius:18px;
        background:#ffffff;
      }
      .kb-orcamento-resumo-item{
        padding:12px 14px;
        display:grid;
        gap:6px;
      }
      .kb-orcamento-resumo-k,
      .kb-orcamento-bloco-titulo,
      .kb-orcamento-campo-k{
        font-size:11px;
        font-weight:900;
        text-transform:uppercase;
        letter-spacing:.05em;
        color:rgba(15,23,42,.56);
      }
      .kb-orcamento-resumo-v{
        font-size:20px;
        font-weight:950;
        color:#0f172a;
      }
      .kb-orcamento-empresa{
        padding:16px 18px;
        display:grid;
        gap:12px;
      }
      .kb-orcamento-empresa-grid,
      .kb-orcamento-dados-grid{
        display:grid;
        grid-template-columns:repeat(2, minmax(0, 1fr));
        gap:10px 14px;
      }
      .kb-orcamento-campo{
        min-width:0;
        display:grid;
        gap:4px;
      }
      .kb-orcamento-campo-v{
        font-size:14px;
        font-weight:800;
        color:#0f172a;
        line-height:1.35;
        word-break:break-word;
      }
      .kb-orcamento-lista{
        display:grid;
        gap:16px;
      }
      .kb-orcamento-item{
        padding:16px;
        display:grid;
        grid-template-columns:1fr;
        gap:14px;
        align-items:start;
        break-inside:avoid;
        page-break-inside:avoid;
      }
      .kb-orcamento-galeria{
        display:grid;
        gap:10px;
      }
      .kb-orcamento-imagem-wrap{
        position:relative;
        border-radius:18px;
        overflow:hidden;
        border:1px solid rgba(15,23,42,.12);
        background:#ffffff;
        aspect-ratio:16 / 7;
        display:flex;
        align-items:center;
        justify-content:center;
        padding:10px;
      }
      .kb-orcamento-item-info{
        display:grid;
        gap:12px;
      }
      .kb-orcamento-item-indice{
        display:inline-flex;
        align-items:center;
        justify-content:center;
        min-height:28px;
        padding:6px 12px;
        border-radius:999px;
        border:1px solid rgba(11,78,162,.18);
        background:rgba(11,78,162,.08);
        color:#0b3f84;
        font-size:12px;
        font-weight:950;
        letter-spacing:.04em;
        text-transform:uppercase;
        width:max-content;
      }
      .kb-orcamento-item-resumo-linha{
        display:grid;
        grid-template-columns:repeat(4, minmax(0, 1fr));
        gap:10px 12px;
        padding:14px 16px;
        border:1px solid rgba(15,23,42,.10);
        border-radius:16px;
        background:linear-gradient(180deg, rgba(255,255,255,.98) 0%, rgba(248,250,252,.98) 100%);
      }
      .kb-orcamento-item-resumo-campo{
        min-width:0;
        display:grid;
        gap:4px;
      }
      .kb-orcamento-item-resumo-k{
        font-size:11px;
        font-weight:950;
        text-transform:uppercase;
        letter-spacing:.05em;
        color:rgba(15,23,42,.56);
      }
      .kb-orcamento-item-resumo-v{
        font-size:14px;
        font-weight:900;
        color:#0f172a;
        line-height:1.35;
        word-break:break-word;
      }
      .kb-orcamento-imagem{
        width:100%;
        height:100%;
        object-fit:contain;
        object-position:center center;
        display:block;
        background:#ffffff;
      }
      .kb-orcamento-galeria-nav{
        position:absolute;
        inset:0;
        pointer-events:none;
      }
      .kb-orcamento-galeria-btn{
        display:none !important;
      }
      .kb-orcamento-galeria-indicador{
        position:absolute;
        left:50%;
        bottom:12px;
        transform:translateX(-50%);
        padding:5px 10px;
        border-radius:999px;
        background:rgba(15,23,42,.65);
        color:#fff;
        font-size:12px;
        font-weight:900;
        letter-spacing:.02em;
      }
      .kb-orcamento-item-titulo{
        margin:0;
        font-size:21px;
        line-height:1.08;
        letter-spacing:-.02em;
        color:#0f172a;
        font-weight:950;
      }
      .kb-orcamento-item-codigo{
        margin:4px 0 0 0;
        color:rgba(15,23,42,.70);
        font-size:12px;
        font-weight:900;
      }
      .kb-orcamento-preco{
        min-width:200px;
        border:1px solid rgba(11,78,162,.16);
        border-radius:16px;
        background:linear-gradient(180deg, rgba(11,78,162,.06) 0%, rgba(255,255,255,.98) 100%);
        padding:12px 14px;
        display:grid;
        gap:4px;
        text-align:right;
      }
      .kb-orcamento-preco-k{
        font-size:11px;
        font-weight:900;
        text-transform:uppercase;
        letter-spacing:.05em;
        color:rgba(15,23,42,.58);
      }
      .kb-orcamento-preco-v{
        font-size:24px;
        font-weight:950;
        color:#0b3f84;
        line-height:1;
        letter-spacing:-.03em;
      }
      .kb-orcamento-preco-meta{
        font-size:12px;
        font-weight:800;
        color:rgba(15,23,42,.68);
      }
      @page{
        size:auto;
        margin:12mm;
      }
      @media print{
        body{
          padding:0;
          background:#ffffff;
        }
      }
      @media (max-width: 900px){
        .kb-orcamento-head-corpo{
          grid-template-columns:1fr;
        }
        .kb-orcamento-resumo,
        .kb-orcamento-empresa-grid,
        .kb-orcamento-dados-grid,
        .kb-orcamento-item-resumo-linha{
          grid-template-columns:1fr;
        }
      }
    `;

    return `<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>${tituloDocumento}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>${estilosImpressao}</style>
</head>
<body>
  <main class="kb-orcamento-print-page">
    ${conteudoAtual}
  </main>
</body>
</html>`;
  }

  function removerIframeImpressaoOrcamento(){
    const iframeAnterior = document.getElementById("kb-orcamento-print-frame");
    if (iframeAnterior) {
      iframeAnterior.remove();
    }
  }

  function aguardarRecursosIframeImpressao(iframeImpressao){
    return new Promise((resolve) => {
      try {
        const doc = iframeImpressao?.contentDocument || iframeImpressao?.contentWindow?.document;
        if (!doc) {
          resolve();
          return;
        }

        const imagens = Array.from(doc.images || []);
        if (!imagens.length) {
          resolve();
          return;
        }

        let pendentes = 0;
        let finalizado = false;

        const concluir = () => {
          if (finalizado) return;
          finalizado = true;
          resolve();
        };

        const verificar = () => {
          if (pendentes <= 0) {
            concluir();
          }
        };

        imagens.forEach((imagem) => {
          if (imagem.complete) {
            return;
          }

          pendentes += 1;

          const aoFinalizar = () => {
            pendentes -= 1;
            verificar();
          };

          imagem.addEventListener("load", aoFinalizar, { once: true });
          imagem.addEventListener("error", aoFinalizar, { once: true });
        });

        verificar();
        setTimeout(concluir, 1200);
      } catch (erro) {
        console.error("Erro ao aguardar recursos do orçamento para impressão:", erro);
        resolve();
      }
    });
  }

  function imprimirOrcamentoCard(){
    const htmlImpressao = montarHtmlImpressaoOrcamentoCard();

    if (!htmlImpressao){
      mostrarMensagemBoard("Abra um orçamento antes de imprimir ou gerar o PDF.");
      return;
    }

    removerIframeImpressaoOrcamento();

    const iframeImpressao = document.createElement("iframe");
    iframeImpressao.id = "kb-orcamento-print-frame";
    iframeImpressao.setAttribute("title", "Impressão do orçamento");
    iframeImpressao.setAttribute("aria-hidden", "true");
    iframeImpressao.style.position = "fixed";
    iframeImpressao.style.right = "0";
    iframeImpressao.style.bottom = "0";
    iframeImpressao.style.width = "0";
    iframeImpressao.style.height = "0";
    iframeImpressao.style.border = "0";
    iframeImpressao.style.opacity = "0";
    iframeImpressao.style.pointerEvents = "none";
    iframeImpressao.style.visibility = "hidden";

    const limpar = () => {
      setTimeout(() => {
        removerIframeImpressaoOrcamento();
      }, 800);
    };

    iframeImpressao.onload = async () => {
      try {
        await aguardarRecursosIframeImpressao(iframeImpressao);

        const janelaImpressao = iframeImpressao.contentWindow;
        if (!janelaImpressao) {
          throw new Error("Janela interna de impressão não disponível.");
        }

        try {
          janelaImpressao.onafterprint = limpar;
        } catch (erroAfterPrint) {
          console.warn("Não foi possível registrar onafterprint no iframe do orçamento:", erroAfterPrint);
        }

        setTimeout(() => {
          try {
            janelaImpressao.focus();
            janelaImpressao.print();
          } catch (erroImpressao) {
            console.error("Erro ao abrir a impressão do orçamento:", erroImpressao);
            mostrarMensagemBoard("Não foi possível abrir a impressão do orçamento.");
            limpar();
          }
        }, 120);

        setTimeout(limpar, 60000);
      } catch (erro) {
        console.error("Erro ao preparar a impressão do orçamento:", erro);
        mostrarMensagemBoard("Não foi possível preparar a impressão do orçamento.");
        limpar();
      }
    };

    document.body.appendChild(iframeImpressao);

    try {
      iframeImpressao.srcdoc = htmlImpressao;
    } catch (erroSrcdoc) {
      try {
        const doc = iframeImpressao.contentDocument || iframeImpressao.contentWindow?.document;
        if (!doc) {
          throw erroSrcdoc;
        }
        doc.open();
        doc.write(htmlImpressao);
        doc.close();
      } catch (erroFallback) {
        console.error("Erro ao montar o iframe de impressão do orçamento:", erroFallback);
        mostrarMensagemBoard("Não foi possível preparar a impressão do orçamento.");
        limpar();
      }
    }
  }

  function preencherCardBasicoModal(card, opcoes = {}){
    const cardNormalizado = normalizarCardServidor(card || {});
    const idCardBasico = idNum(cardNormalizado.IDFatoKanbanCard || cardAbertoId || 0);

    if (!idCardBasico) return;

    if (modalCard) {
      modalCard.dataset.idFaseAtual = String(idNum(cardNormalizado.IDDimKanbanFaseAtual || 0) || "");
      modalCard.dataset.jaPassouFaseFormularioContrato = idNum(
        cardNormalizado.BitJaPassouPelaFaseFormularioContrato ??
        cardNormalizado.bit_ja_passou_pela_fase_formulario_contrato ??
        0
      ) === 1 ? "1" : "0";
    }

    atualizarCabecalhoModalCard(cardNormalizado);

    if (inputIdCard) {
      inputIdCard.value = String(idCardBasico || "");
    }

    if (inputTituloCard) {
      inputTituloCard.value = cardNormalizado.Titulo ?? "";
    }

    if (inputDescricaoCard) {
      inputDescricaoCard.value = cardNormalizado.Descricao ?? "";
    }

    if (inputMarcaCard) {
      inputMarcaCard.value = cardNormalizado.Marca ?? "";
      sincronizarMarcaExibidaHeaderComInputTopo();
    }

    if (inputTelefoneCard) {
      inputTelefoneCard.value = normalizarTelefoneContato(cardNormalizado.Telefone ?? "");
    }

    if (inputEmailCard) {
      inputEmailCard.value = cardNormalizado.Email ?? "";
    }

    if (selectResponsavelCard) {
      const idResponsavel = cardNormalizado.IDVendedorUsuario ?? cardNormalizado.IDDimUsuarios ?? "";
      selectResponsavelCard.value = idResponsavel !== null && idResponsavel !== undefined ? String(idResponsavel) : "";
    }

    if (selectOrigemCard) {
      const idOrigem = cardNormalizado.IDDimKanbanOrigem ?? "";
      selectOrigemCard.value = idOrigem !== null && idOrigem !== undefined ? String(idOrigem) : "";
    }

    if (selectMotivoEncerramentoCard) {
      const idMotivo = cardNormalizado.IDDimKanbanMotivoEncerramento ?? "";
      selectMotivoEncerramentoCard.value = idMotivo !== null && idMotivo !== undefined ? String(idMotivo) : "";
    }

    if (inputMotivoEncerramentoObsCard) {
      inputMotivoEncerramentoObsCard.value = cardNormalizado.MotivoEncerramentoObs ?? "";
    }

    if (selectStatusCard) {
      const statusAtual = cardNormalizado.StatusCard ?? "";
      selectStatusCard.value = statusAtual !== null && statusAtual !== undefined ? String(statusAtual) : "";
    }

    if (selectTipoClienteDescontoCard) {
      const idTipoCliente = cardNormalizado.IDDimKanbanTipoClienteDesconto ?? cardNormalizado.IDDimTipoCliente ?? "";
      montarSelectTipoClienteDesconto(idTipoCliente);
      selectTipoClienteDescontoCard.value = idTipoCliente !== null && idTipoCliente !== undefined ? String(idTipoCliente) : "";
    }

    if (selectOrigemAtendimentoCard) {
      const idOrigemAtendimento = cardNormalizado.IDDimOrigemAtendimento ?? "";
      montarSelectOrigemAtendimento(idOrigemAtendimento);
      selectOrigemAtendimentoCard.value = idOrigemAtendimento !== null && idOrigemAtendimento !== undefined ? String(idOrigemAtendimento) : "";
    }

    if (selectTagCard && selectTagCard.options.length <= 0) {
      selectTagCard.innerHTML = "";
      tagsCatalogo.forEach(t => {
        selectTagCard.appendChild(
          el("option", { value: t.IDDimKanbanTag }, [t.NomeTag])
        );
      });
    }

    const tagsBasicas = Array.isArray(opcoes.tags) ? opcoes.tags : tagsDoCard(idCardBasico);
    renderTagsNoCard(tagsBasicas);

    const notasBasicas = Array.isArray(opcoes.notas) ? opcoes.notas : (mapaNotasPorCard.get(idCardBasico) || []);
    renderNotas(notasBasicas);

    atualizarVisibilidadeDadosNovoContrato();
    atualizarVisibilidadeEmpresasRelacionadasCard();
    atualizarVisibilidadeFormularioSolicitacaoContrato();
  }

  async function abrirOrcamentoCard(idCard){
    const id = idNum(idCard);
    if (!id) {
      mostrarMensagemBoard("Não foi possível identificar o card para gerar o orçamento.");
      return;
    }

    const urlOrcamento = montarUrlPorTemplate(
      URL_API_CARD_ORCAMENTO_TEMPLATE || "/kanban/api/cards/__ID_CARD__/orcamento",
      "__ID_CARD__",
      id
    );
    const resultado = await fetchJsonKanban(urlOrcamento);
    const resposta = resultado.resposta;
    const corpo = resultado.corpo;

    if (!respostaJsonKanbanOk(resultado)){
      console.warn("abrirOrcamentoCard: resposta inválida", detalhesFalhaJsonKanban(resultado));
      mostrarMensagemBoard(mensagemErroHttp(resposta, corpo, "Não foi possível gerar o orçamento deste card."));
      return;
    }

    cardOrcamentoAbertoId = id;
    renderizarOrcamentoCard(corpo);
    if (modalOrcamentoCard) {
      modalOrcamentoCard.style.display = "block";
    }
  }

  async function abrirCard(idCard) {
    const sequenciaAtualAbertura = ++sequenciaAberturaCard;
    carregandoCardAtual = true;

    try {
      cardAbertoId = idNum(idCard);
      formularioSolicitacaoLiberadoNestaAbertura = false;
      snapshotSolicitacaoEditavelAtual = null;
      vendedorLogadoSolicitacaoAtual = null;
      versaoConcorrenciaCardAberto = "";
      cardAbertoConflitoExterno = false;
      if (modalCard) {
        modalCard.dataset.idFaseAtual = "";
        modalCard.dataset.jaPassouFaseFormularioContrato = "0";
      }
      atualizarVisibilidadeFormularioSolicitacaoContrato();
      if (msgCard) {
        msgCard.style.display = "none";
        msgCard.textContent = "";
      }
      atualizarEstadoSalvarCard();

      // UX/performance: abre o modal na hora e pinta o que já existe no board.
      // O detalhe completo vem em seguida com fresh=1, sem depender do catálogo de empresas terminar.
      if (modalCard) {
        modalCard.style.display = "block";
      }

      const cardLocalInicial = obterCardPorId(cardAbertoId);
      if (cardLocalInicial) {
        preencherCardBasicoModal(cardLocalInicial, {
          tags: tagsDoCard(cardAbertoId),
          notas: mapaNotasPorCard.get(idNum(cardAbertoId)) || []
        });
        mostrarMensagemCard("Atualizando detalhes completos do card...");
      } else {
        mostrarMensagemCard("Carregando detalhes do card...");
      }

      const promessaEmpresas = carregarEmpresas().catch((erro) => {
        console.warn("abrirCard: pré-carga de empresas falhou. O detalhe do card continuará carregando.", erro);
        return false;
      });

      // Começa a carregar o catálogo de Cidade/Tipo/CodFace em paralelo com o detalhe do card.
      // Assim os selects já chegam prontos quando os blocos de painel/face forem renderizados.
      const promessaPainelFacesCard = painelFaceWrap?.classList.contains("is-on")
        ? carregarCatalogoPainelFaces().catch((erro) => {
            console.warn("abrirCard: pré-carga do catálogo de painel/face falhou.", erro);
            return [];
          })
        : Promise.resolve([]);

      const j = await buscarDetalheCard(idCard, { fresh: true, timeoutMs: 60000 });

      if (sequenciaAtualAbertura !== sequenciaAberturaCard || idNum(cardAbertoId) !== idNum(idCard)) {
        return;
      }

      if (!j || !j.card) {
        versaoConcorrenciaCardAberto = "";
        cardAbertoConflitoExterno = true;
        mostrarMensagemCard("Não foi possível atualizar os detalhes do card. Mantive o que já estava na tela, mas bloqueei o salvamento para evitar gravar dados antigos. Feche e abra o card novamente.");
        return;
      }

    const cardNormalizado = normalizarCardServidor(j.card);
    registrarEmpresaPrincipalDoCardNoCatalogo(cardNormalizado);
    if (modalCard) {
      modalCard.dataset.idFaseAtual = String(idNum(cardNormalizado.IDDimKanbanFaseAtual || 0) || "");
      modalCard.dataset.jaPassouFaseFormularioContrato = idNum(
        cardNormalizado.BitJaPassouPelaFaseFormularioContrato ??
        cardNormalizado.bit_ja_passou_pela_fase_formulario_contrato ??
        0
      ) === 1 ? "1" : "0";
      atualizarVisibilidadeFormularioSolicitacaoContrato();
    }
    atualizarCabecalhoModalCard(cardNormalizado);
    const fluxoPersistido = extrairFluxoContratoPersistidoDoCard(cardNormalizado, {
      tags: Array.isArray(j.tags) ? j.tags : [],
      snapshot: j?.solicitacao_snapshot_editavel || null,
      painelFaces: Array.isArray(j.painel_faces) ? j.painel_faces : (Array.isArray(j.painelFaces) ? j.painelFaces : [])
    });
    fluxoContratoPersistidoCardAberto = Object.assign({}, fluxoPersistido || {});

    /*
     * Aqui eu uso a versão canônica já normalizada pelo próprio template.
     * No kanban, a concorrência é tratada por VersaoConcorrenciaHex
     * (rowversion/hex do backend), não por um número simples inventado no front.
     */
    versaoConcorrenciaCardAberto = extrairVersaoConcorrenciaCard(cardNormalizado);
    aplicarVersaoConcorrenciaCardAberto(cardNormalizado, "abrir_card");

    /*
     * Eu só preencho os campos que realmente existem neste template.
     * O erro anterior aconteceu porque foram referenciados elementos de outro modal
     * (inputIdCard, selectResponsavelCard, selectStatusCard etc.) sem garantir
     * que eles existiam aqui.
     */
    if (inputIdCard) {
      inputIdCard.value = String(cardNormalizado.IDFatoKanbanCard || "");
    }

    if (inputTituloCard) {
      inputTituloCard.value = cardNormalizado.Titulo ?? "";
    }

    if (inputDescricaoCard) {
      inputDescricaoCard.value = cardNormalizado.Descricao ?? "";
    }

    if (selectEmpresaCard) {
      await promessaEmpresas;
      if (sequenciaAtualAbertura !== sequenciaAberturaCard || idNum(cardAbertoId) !== idNum(idCard)) {
        return;
      }

      const empresasRelacionadas = obterEmpresasRelacionadasDoCard(cardNormalizado);
      const idEmpresaPrincipal = empresasRelacionadas.principal ?? "";
      await selecionarEmpresaPorIdComGarantia(idEmpresaPrincipal !== null && idEmpresaPrincipal !== undefined ? String(idEmpresaPrincipal) : "", false);
      setEmpresaPreviewById(selectEmpresaCard.value || "");
      await carregarFluxoContratoParaEmpresa(selectEmpresaCard.value || "", {
        preservarSelecao: true,
        id_contrato_existente: fluxoPersistido.id_contrato_existente,
        tipo_contrato_card: fluxoPersistido.tipo_contrato_card,
        cod_ponto_contrato: fluxoPersistido.cod_ponto_contrato,
        cod_face_contrato: fluxoPersistido.cod_face_contrato
      });

      await Promise.all([
        selecionarAgenciaPorIdComGarantia(empresasRelacionadas.agencia ? String(empresasRelacionadas.agencia) : "", false),
        selecionarClienteDiretoPorIdComGarantia(empresasRelacionadas.clienteDireto ? String(empresasRelacionadas.clienteDireto) : "", false),
        selecionarBureauPorIdComGarantia(empresasRelacionadas.bureau ? String(empresasRelacionadas.bureau) : "", false),
        selecionarIntermediarioPorIdComGarantia(empresasRelacionadas.intermediario ? String(empresasRelacionadas.intermediario) : "", false)
      ]);
    } else {
      setEmpresaPreviewByObj(null);
      resetarFluxoContrato();
    }

    if (inputMarcaCard) {
      inputMarcaCard.value = cardNormalizado.Marca ?? "";
      sincronizarMarcaExibidaHeaderComInputTopo();
    }

    if (inputTelefoneCard) {
      inputTelefoneCard.value = normalizarTelefoneContato(cardNormalizado.Telefone ?? "");
    }

    if (inputEmailCard) {
      inputEmailCard.value = cardNormalizado.Email ?? "";
    }

    atualizarVisibilidadeDadosNovoContrato();
    atualizarVisibilidadeEmpresasRelacionadasCard();

    if (selectTipoClienteDescontoCard) {
      const idTipoCliente = cardNormalizado.IDDimKanbanTipoClienteDesconto ?? cardNormalizado.IDDimTipoCliente ?? "";
      montarSelectTipoClienteDesconto(idTipoCliente);
      selectTipoClienteDescontoCard.value = idTipoCliente !== null && idTipoCliente !== undefined ? String(idTipoCliente) : "";
      atualizarVisibilidadeEmpresasRelacionadasCard();
    }

    if (selectOrigemAtendimentoCard) {
      const idOrigemAtendimento = cardNormalizado.IDDimOrigemAtendimento ?? "";
      montarSelectOrigemAtendimento(idOrigemAtendimento);
      selectOrigemAtendimentoCard.value = idOrigemAtendimento !== null && idOrigemAtendimento !== undefined ? String(idOrigemAtendimento) : "";
    }

    if (selectSegmentoCard) {
      await restaurarSegmentoCard(cardNormalizado);
    }

    if (selectResponsavelCard) {
      const idResponsavel = cardNormalizado.IDVendedorUsuario ?? cardNormalizado.IDDimUsuarios ?? "";
      selectResponsavelCard.value = idResponsavel !== null && idResponsavel !== undefined ? String(idResponsavel) : "";
    }

    if (selectOrigemCard) {
      const idOrigem = cardNormalizado.IDDimKanbanOrigem ?? "";
      selectOrigemCard.value = idOrigem !== null && idOrigem !== undefined ? String(idOrigem) : "";
    }

    if (selectMotivoEncerramentoCard) {
      const idMotivo = cardNormalizado.IDDimKanbanMotivoEncerramento ?? "";
      selectMotivoEncerramentoCard.value = idMotivo !== null && idMotivo !== undefined ? String(idMotivo) : "";
    }

    if (inputMotivoEncerramentoObsCard) {
      inputMotivoEncerramentoObsCard.value = cardNormalizado.MotivoEncerramentoObs ?? "";
    }

    if (selectStatusCard) {
      const statusAtual = cardNormalizado.StatusCard ?? "";
      selectStatusCard.value = statusAtual !== null && statusAtual !== undefined ? String(statusAtual) : "";
    }

    if (selectTagCard) {
      selectTagCard.innerHTML = "";
      tagsCatalogo.forEach(t => {
        selectTagCard.appendChild(
          el("option", { value: t.IDDimKanbanTag }, [t.NomeTag])
        );
      });
    }

    renderTagsNoCard(Array.isArray(j.tags) ? j.tags : []);
    renderNotas(Array.isArray(j.notas) ? j.notas : []);

    if (inputNotaTexto) {
      inputNotaTexto.value = "";
    }

    if (painelFaceWrap?.classList.contains("is-on")) {
      await promessaPainelFacesCard;
      const painelFaces =
        (Array.isArray(j.painel_faces) && j.painel_faces) ||
        (Array.isArray(j.painelFaces) && j.painelFaces) ||
        [];
      preencherPainelFacesDoCard(painelFaces);
    }

    preencherFormularioSolicitacaoContrato(j?.solicitacao_snapshot_editavel || null, cardNormalizado, j?.vendedor_logado_solicitacao || null);
    atualizarVisibilidadeFormularioSolicitacaoContrato();

    estadoInicialCardAberto = montarEstadoEdicaoCardAtual();
    atualizarEstadoSalvarCard();

    if (!versaoConcorrenciaCardAberto) {
      console.warn("abrirCard sem versão de concorrência", {
        idCard,
        cardNormalizado,
        respostaCompleta: j
      });

      mostrarMensagemCard(
        "O card foi carregado para visualização, mas o backend não enviou a versão de concorrência. Você pode visualizar, porém não consegue salvar até o backend devolver essa versão."
      );
    }

    if (msgCard) {
      msgCard.style.display = "none";
      msgCard.textContent = "";
    }
    modalCard.style.display = "block";
    } catch (erro) {
      console.error("abrirCard: falha inesperada", erro);
      versaoConcorrenciaCardAberto = "";
      cardAbertoConflitoExterno = true;
      mostrarMensagemCard("Erro inesperado ao abrir o card. Mantive o salvamento bloqueado para evitar gravar dados incompletos. Veja o console para o detalhe técnico.");
    } finally {
      if (sequenciaAtualAbertura === sequenciaAberturaCard) {
        carregandoCardAtual = false;
        atualizarEstadoSalvarCard();
      }
    }
  }

  function renderTagsNoCard(tags) {
  const box = document.getElementById("tagsNoCard");

  if (!box) {
    console.warn("renderTagsNoCard: elemento #tagsNoCard não encontrado no DOM.");
    return;
  }

  box.innerHTML = "";

  if (!Array.isArray(tags) || tags.length === 0) {
    return;
  }

  tags.forEach(tagAtual => {
    if (!tagAtual || typeof tagAtual !== "object") {
      return;
    }

    const idTag = idNum(
      tagAtual.IDDimKanbanTag ??
      tagAtual.idDimKanbanTag ??
      tagAtual.id ??
      0
    );

    const nomeTag = String(
      tagAtual.NomeTag ??
      tagAtual.nomeTag ??
      ""
    ).trim();

    const corHex = String(
      tagAtual.CorHex ??
      tagAtual.corHex ??
      ""
    ).trim();

    const pill = el(
      "span",
      {
        class: "kb-tag",
        title: nomeTag,
        style: estiloTag(corHex)
      },
      [
        el("span", { class: "kb-tag-text" }, [nomeTag])
      ]
    );

    const btnRemover = el(
      "button",
      {
        class: "kb-btn sm",
        type: "button",
        title: nomeTag ? `Remover tag ${nomeTag}` : "Remover tag"
      },
      ["x"]
    );

    btnRemover.addEventListener("click", () => {
      if (!idTag) {
        console.warn("renderTagsNoCard: tag sem ID válido para remoção.", tagAtual);
        return;
      }

      removerTagDoCard(idTag);
    });

    const wrap = el(
      "div",
      {
        style: "display:flex;gap:6px;align-items:center;flex-wrap:wrap;"
      },
      [pill, btnRemover]
    );

    box.appendChild(wrap);
  });
}



  async function sincronizarTagsDoCardAberto(idCard, opcoes = {}) {
    const idC = idNum(idCard);
    if (!idC) return false;

    const detalhe = await buscarDetalheCard(idC, { fresh: true });
    if (!detalhe || !detalhe.card) return false;

    const cardServidor = normalizarCardServidor(detalhe.card);
    inserirOuAtualizarCardLocal(cardServidor);
    aplicarVersaoConcorrenciaCardAberto(cardServidor, "sincronizar_tags");

    const tagsAtualizadas = Array.isArray(detalhe.tags) ? detalhe.tags : [];
    setTagsDoCard(idC, tagsAtualizadas);
    renderizarFiltrosMultiselect();

    if (idNum(cardAbertoId) === idC && modalCard.style.display === "block") {
      renderTagsNoCard(tagsAtualizadas);

      if (modalCard) {
        modalCard.dataset.idFaseAtual = String(idNum(cardServidor.IDDimKanbanFaseAtual || 0) || "");
        modalCard.dataset.jaPassouFaseFormularioContrato = idNum(
          cardServidor.BitJaPassouPelaFaseFormularioContrato ??
          cardServidor.bit_ja_passou_pela_fase_formulario_contrato ??
          0
        ) === 1 ? "1" : "0";
        atualizarVisibilidadeFormularioSolicitacaoContrato();
      }
      atualizarVisibilidadeEmpresasRelacionadasCard();

      if (selectTagCard && idNum(opcoes.idTagSelecionada)) {
        selectTagCard.value = String(idNum(opcoes.idTagSelecionada));
      }

      cardAbertoConflitoExterno = false;
      msgCard.style.display = "none";
      msgCard.textContent = "";
      atualizarEstadoSalvarCard();
    }

    if (opcoes.redesenhar !== false) {
      const idFaseAtual = idNum(
        cardServidor.IDDimKanbanFaseAtual ||
        obterCardPorId(idC)?.IDDimKanbanFaseAtual ||
        0
      );

      if (idFaseAtual) {
        redesenharFasesLocalmente([idFaseAtual], null, true);
      }
    }

    agendarRecarregarResumoComercial();
    return true;
  }

  async function removerTagDoCard(idTag) {
    const idCard = idNum(cardAbertoId);
    const idTagNum = idNum(idTag);
    if (!idCard || !idTagNum) return;

    const r = await fetchKanbanComTimeout(`/kanban/api/cards/${idCard}/tags/${idTagNum}`, {
      method:"DELETE",
      credentials: "same-origin",
      headers: {"X-CSRFToken": csrf}
    }, TIMEOUT_FETCH_POS_SAVE_KANBAN_MS);

    const j = await r.json().catch(() => null);
    if (!j || !j.ok) {
      mostrarMensagemCard((j && (j.msg || j.erro)) || "Erro ao remover tag.");
      return;
    }

    await sincronizarTagsDoCardAberto(idCard, { redesenhar: true });
  }

  document.getElementById("btnAddTag").addEventListener("click", async () => {
    const idCard = idNum(cardAbertoId);
    const idTag = parseInt(document.getElementById("selectTagCard").value || "0", 10);
    if (!idCard || !idTag) return;

    const r = await fetchKanbanComTimeout(`/kanban/api/cards/${idCard}/tags`, {
      method:"POST",
      credentials: "same-origin",
      headers: headersJSON,
      body: JSON.stringify({id_tag: idTag})
    }, TIMEOUT_FETCH_POS_SAVE_KANBAN_MS);

    const j = await r.json().catch(() => null);
    if (!j || !j.ok) {
      mostrarMensagemCard((j && (j.msg || j.erro)) || "Erro ao adicionar tag.");
      return;
    }

    await sincronizarTagsDoCardAberto(idCard, {
      idTagSelecionada: idTag,
      redesenhar: true
    });
  });

  async function salvarNotaSeTiverTexto(idCardOverride = null, textoOverride = null) {
    const inp = document.getElementById("notaTexto");
    const texto = (textoOverride !== null && textoOverride !== undefined)
      ? safeStr(textoOverride).trim()
      : safeStr(inp?.value || "").trim();
    if (texto.length < 2) return { ok: true, fez: false };

    const idCardNota = idNum(idCardOverride || cardAbertoId);
    if (!idCardNota) {
      return { ok: false, msg: "Não foi possível identificar o card para salvar a nota." };
    }

    const clientRequestIdNota = gerarClientRequestIdKanban("card_note");
    registrarEventoLocalPendente(clientRequestIdNota, 30000);

    const r = await fetchKanbanComTimeout(`/kanban/api/cards/${idCardNota}/notas`, {
      method:"POST",
      credentials: "same-origin",
      headers: headersJSON,
      body: JSON.stringify({texto, tipo:"OBS", client_request_id: clientRequestIdNota})
    }, TIMEOUT_FETCH_POS_SAVE_KANBAN_MS);

    const j = await r.json().catch(() => null);
    if (!j || !j.ok) {
      return { ok: false, msg: (j && (j.msg || j.erro)) || `Erro ao salvar nota (HTTP ${r.status})` };
    }

    if (inp && (textoOverride === null || textoOverride === undefined)) {
      inp.value = "";
    }
    return { ok: true, fez: true };
  }

  function validarFluxoContratoFase4ParaSalvar(){
    if (!modalCardEstaNaFaseQuatro()) {
      return { ok: true };
    }

    const fluxo = resolverFluxoContratoParaSalvamento();
    const modoSelecionado = safeStr(selectModoContratoCard?.value || fluxo.modo_contrato || "").trim();

    if (modoSelecionado === VALOR_MODO_CONTRATO_ADITIVO && !idNum(fluxo.id_contrato || 0)) {
      return {
        ok: false,
        msg: "Para salvar como Aditivo, selecione primeiro um contrato existente no campo Contrato da empresa."
      };
    }

    return { ok: true };
  }

  function validarReservasPainelFacesFormulario(){
    for (const bloco of (painelFaceLista?.querySelectorAll('.kb-painel-item') || [])){
      const titulo = safeStr(bloco.querySelector('[data-role="titulo-item"]')?.textContent || '').trim() || 'Painel / Face';
      const idPainel = idNum(bloco.querySelector('[data-role="select-painel"]')?.value || 0);
      const codFace = safeStr(bloco.querySelector('[data-role="select-face"]')?.value || '').trim();
      const idPreco = idNum(bloco.querySelector('[data-role="select-preco"]')?.value || 0) || null;
      const dataInicio = safeStr(bloco.querySelector('[data-role="input-data-inicio"]')?.value || '').trim();
      const dataFim = safeStr(bloco.querySelector('[data-role="input-data-fim"]')?.value || '').trim();

      const temContextoPainel = !!(idPainel || codFace || idPreco);
      if (!temContextoPainel && !dataInicio && !dataFim){
        continue;
      }

      if ((dataInicio && !dataFim) || (!dataInicio && dataFim)){
        return { ok: false, msg: `${titulo}: preencha Data de início e Data até.` };
      }

      const dataHojeIso = obterDataAtualIsoLocal();

      if (dataInicio && dataInicio < dataHojeIso){
        return { ok: false, msg: `${titulo}: a Data de início não pode ser anterior a ${formatarDataIsoParaBr(dataHojeIso)}.` };
      }

      if (dataFim && dataFim < dataHojeIso){
        return { ok: false, msg: `${titulo}: a Data até não pode ser anterior a ${formatarDataIsoParaBr(dataHojeIso)}.` };
      }

      if (dataInicio && dataFim && dataFim < dataInicio){
        return { ok: false, msg: `${titulo}: a Data até não pode ser menor que a Data de início.` };
      }

      if (dataInicio && dataFim && bloco.__calendarioOcupacao && !intervaloEstaDisponivelNoBloco(bloco, dataInicio, dataFim)){
        return {
          ok: false,
          msg: `${titulo}: ${montarMensagemFalhaEncaixeFragmentado(bloco, dataInicio, dataFim)}`
        };
      }
    }

    return { ok: true };
  }

  function normalizarPainelFacesParaComparacao(lista){
    return (Array.isArray(lista) ? lista : [])
      .map((item, indice) => ({
        ordem: idNum(item?.Ordem ?? item?.ordem ?? (indice + 1)),
        id_painel: idNum(item?.IDDimPaineisEuromidia ?? item?.id_painel ?? 0) || null,
        id_face: idNum(item?.IDDimFacesPaineis ?? item?.id_face ?? 0) || null,
        id_reserva: idNum(item?.id_reserva ?? item?.IDReserva ?? item?.Reserva ?? 0) || null,
        cod_ponto: safeStr(item?.CodPonto ?? item?.cod_ponto ?? "").trim(),
        cod_face: safeStr(item?.CodFace ?? item?.cod_face ?? "").trim().toUpperCase(),
        tipo_painel: safeStr(item?.TipoPainel ?? item?.tipo_painel ?? "").trim(),
        ano_custo: idNum(item?.AnoCusto ?? item?.ano_custo ?? 0) || null,
        custo_tabela: safeStr(item?.CustoTabela ?? item?.custo_tabela ?? "").trim(),
        id_tabela_preco: idNum(item?.IDDimTabelaPrecosEuromidia ?? item?.id_tabela_preco ?? item?.id_preco ?? 0) || null,
        periodo_exibicao: safeStr(item?.PeriodoExibicao ?? item?.periodo_exibicao ?? "").trim(),
        exibicoes_dia: safeStr(item?.ExibicoesDia ?? item?.exibicoes_dia ?? item?.Cota ?? item?.cota ?? "").trim(),
        cota: safeStr(item?.Cota ?? item?.cota ?? item?.ExibicoesDia ?? item?.exibicoes_dia ?? "").trim(),
        valor_tabela: safeStr(item?.ValorTabela ?? item?.valor_tabela ?? "").trim(),
        tabela: safeStr(item?.Tabela ?? item?.tabela ?? "").trim(),
        politica_trocas: safeStr(item?.PoliticaTrocas ?? item?.politica_trocas ?? "").trim(),
        valor_troca: safeStr(item?.ValorTroca ?? item?.valor_troca ?? "").trim(),
        novo_valor: safeStr(item?.NovoValor ?? item?.novo_valor ?? "").trim(),
        percentual_desconto: safeStr(item?.PercentualDesconto ?? item?.percentual_desconto ?? "").trim(),
        valor_venda_final: safeStr(item?.ValorVendaFinal ?? item?.valor_venda_final ?? "").trim(),
        margem_valor: safeStr(item?.MargemValor ?? item?.margem_valor ?? "").trim(),
        margem_percentual: safeStr(item?.MargemPercentual ?? item?.margem_percentual ?? "").trim(),
        data_inicio: safeStr(item?.DataInicio ?? item?.data_inicio ?? "").trim(),
        data_fim: safeStr(item?.DataFim ?? item?.data_fim ?? "").trim(),
        origem_aditivo: safeStr(item?.OrigemAditivo ?? item?.origem_aditivo ?? "").trim(),
        id_item_contrato_aditivo: idNum(item?.IDFatoControleContratosItensEuromidia ?? item?.id_item_contrato_aditivo ?? item?.id_item_contrato ?? 0) || null,
        cod_ponto_contrato_aditivo: safeStr(item?.CodPontoContratoAditivo ?? item?.cod_ponto_contrato_aditivo ?? "").trim(),
        cod_face_contrato_aditivo: safeStr(item?.CodFaceContratoAditivo ?? item?.cod_face_contrato_aditivo ?? "").trim().toUpperCase()
      }))
      .filter((item) => {
        return !!(
          item.id_painel || item.id_face || item.id_reserva || item.cod_ponto || item.cod_face || item.tipo_painel ||
          item.ano_custo || item.custo_tabela || item.id_tabela_preco || item.periodo_exibicao ||
          item.exibicoes_dia || item.cota || item.valor_tabela || item.tabela || item.politica_trocas ||
          item.valor_troca || item.novo_valor || item.percentual_desconto || item.valor_venda_final ||
          item.margem_valor || item.margem_percentual || item.data_inicio || item.data_fim ||
          item.origem_aditivo || item.id_item_contrato_aditivo || item.cod_ponto_contrato_aditivo || item.cod_face_contrato_aditivo
        );
      })
      .sort((a, b) => a.ordem - b.ordem);
  }

  function montarEstadoEdicaoCardAtual(){
    const fluxoContratoAtual = resolverFluxoContratoParaSalvamento();
    const usarVinculoContratoExistente = !fluxoContratoAtual.usar_novo_contrato && !!idNum(fluxoContratoAtual.id_contrato);
    const painelFaceLigado = !!(painelFaceWrap && painelFaceWrap.classList.contains("is-on"));
    const painelFaces = painelFaceLigado ? normalizarPainelFacesParaComparacao(coletarPainelFacesDoFormulario()) : [];
    const solicitacaoContrato = obterSolicitacaoContratoParaPayload();
    const tipoDocumentoAtual = obterTipoDocumentoSelecionadoNoHeaderSolicitacao();

    const idTipoClienteAtual = selectTipoClienteDescontoCard?.value ? Number(selectTipoClienteDescontoCard.value) : null;
    const empresasRelacionadasPayload = coletarEmpresasRelacionadasPermitidasParaPayload(idTipoClienteAtual);

    return {
      titulo: safeStr(document.getElementById("cardTitulo")?.value || "").trim(),
      descricao: safeStr(document.getElementById("cardDescricao")?.value || ""),
      id_empresa: selectEmpresaCard?.value ? Number(selectEmpresaCard.value) : null,
      nome_empresa: safeStr(obterTextoEmpresaSelecionada(selectEmpresaCard?.value || "") || inputEmpresaCardBusca?.value || "").trim() || null,
      id_tipo_cliente_desconto: idTipoClienteAtual,
      id_origem_atendimento: selectOrigemAtendimentoCard?.value ? Number(selectOrigemAtendimentoCard.value) : null,
      id_dim_cnaes: selectSegmentoCard?.value ? Number(selectSegmentoCard.value) : null,
      id_dim_tipo_documento: tipoDocumentoAtual.IDDimTipoDocumento || null,
      tipo_documento: tipoDocumentoAtual.TipoDocumento || null,
      id_contrato_existente: usarVinculoContratoExistente ? fluxoContratoAtual.id_contrato : null,
      id_controle_contrato: usarVinculoContratoExistente ? fluxoContratoAtual.id_contrato : null,
      tipo_contrato_card: usarVinculoContratoExistente ? VALOR_MODO_CONTRATO_ADITIVO : VALOR_MODO_CONTRATO_NOVO,
      cod_ponto_contrato: usarVinculoContratoExistente ? safeStr(fluxoContratoAtual.cod_ponto_contrato || "").trim() : null,
      cod_face_contrato: usarVinculoContratoExistente ? safeStr(fluxoContratoAtual.cod_face_contrato || "").trim().toUpperCase() : null,
      id_empresa_agencia: empresasRelacionadasPayload.id_empresa_agencia,
      id_empresa_cliente_direto: empresasRelacionadasPayload.id_empresa_cliente_direto,
      id_empresa_bureau: empresasRelacionadasPayload.id_empresa_bureau,
      id_empresa_intermediario: empresasRelacionadasPayload.id_empresa_intermediario,
      marca: safeStr(inputMarcaCard?.value || "").trim() || null,
      telefone: normalizarTelefoneContato(inputTelefoneCard?.value || "") || null,
      email: safeStr(inputEmailCard?.value || "").trim() || null,
      solicitacao_contrato: solicitacaoContrato,
      painel_faces: painelFaces
    };
  }

  function estadosEdicaoCardSaoIguais(estadoA, estadoB){
    return JSON.stringify(estadoA || {}) === JSON.stringify(estadoB || {});
  }

  btnSalvarCard.addEventListener("click", async () => {
    if (salvandoCardAtual) return;

    if (carregandoCardAtual) {
      mostrarMensagemCard("Aguarde o card terminar de carregar antes de salvar. Isso evita gravar dados vazios ou antigos.");
      atualizarEstadoSalvarCard();
      return;
    }

    const titulo = (document.getElementById("cardTitulo").value || "").trim();
    const descricao = document.getElementById("cardDescricao").value || "";
    const idEmpresa = selectEmpresaCard.value ? Number(selectEmpresaCard.value) : null;
    const idTipoClienteDesconto = selectTipoClienteDescontoCard?.value ? Number(selectTipoClienteDescontoCard.value) : null;
    const idOrigemAtendimento = selectOrigemAtendimentoCard?.value ? Number(selectOrigemAtendimentoCard.value) : null;
    const empresaPrincipalSelecionada = idEmpresa ? obterEmpresaCatalogoPorId(idEmpresa) : null;

    msgCard.style.display = "none";
    msgCard.textContent = "";

    if (empresaPrincipalBloqueadaCarteiraAtual) {
      mostrarAvisoEmpresaBloqueadaCarteira(
        empresaPrincipalBloqueadaCarteiraAtual,
        empresaPrincipalBloqueadaCarteiraAtual.MensagemBloqueioCarteiraVendedor,
        { exibirPopup: false }
      );
      return;
    }

    if (idEmpresa) {
      const validacaoCarteiraSalvar = await validarEmpresaCarteiraVendedorPorId(idEmpresa, empresaPrincipalSelecionada);
      if (!validacaoCarteiraSalvar.permitida) {
        const empresaBloqueada = validacaoCarteiraSalvar.empresa || empresaPrincipalSelecionada || { NomeVendedorCarteira: "Vendedor responsável" };
        const mensagemBloqueio = safeStr(validacaoCarteiraSalvar.msg || empresaBloqueada?.MensagemBloqueioCarteiraVendedor || "").trim() || mensagemEmpresaBloqueadaCarteira(empresaBloqueada);
        definirBloqueioCarteiraEmpresaPrincipal(empresaBloqueada, mensagemBloqueio);
        mostrarAvisoEmpresaBloqueadaCarteira(empresaBloqueada, mensagemBloqueio, { exibirPopup: false });
        return;
      }
      definirBloqueioCarteiraEmpresaPrincipal(null);
    }

    if (!cardAbertoId || !safeStr(versaoConcorrenciaCardAberto)) {
      mostrarMensagemCard("A versão atual do card não está carregada. O detalhe abriu para visualização, mas o backend não devolveu a versão de concorrência necessária para salvar.");
      atualizarEstadoSalvarCard();
      return;
    }

    if (cardAbertoConflitoExterno) {
      mostrarMensagemCard("Este card recebeu atualização externa enquanto estava aberto. Reabra o card antes de salvar para evitar sobrescrita.");
      atualizarEstadoSalvarCard();
      return;
    }

    const validacaoReservas = validarReservasPainelFacesFormulario();
    if (!validacaoReservas.ok){
      mostrarMensagemCard(validacaoReservas.msg || "Existem datas inválidas no painel / face.");
      return;
    }

    const validacaoFluxoContratoFase4 = validarFluxoContratoFase4ParaSalvar();
    if (!validacaoFluxoContratoFase4.ok){
      mostrarMensagemCard(validacaoFluxoContratoFase4.msg || "Existem pendências no fluxo de contrato.");
      return;
    }

    const idCardSalvo = idNum(cardAbertoId);

    if (!idTipoClienteDesconto) {
      mostrarMensagemCard("Tipo de cliente é obrigatório.");
      return;
    }

    const idFaseAtualCardAberto = idNum(obterCardPorId(idCardSalvo)?.IDDimKanbanFaseAtual || 0);
    const validacaoEmpresasFase4 = validarEmpresasRelacionadasFase4Formulario(idTipoClienteDesconto, idFaseAtualCardAberto);
    if (!validacaoEmpresasFase4.ok) {
      mostrarMensagemCard(validacaoEmpresasFase4.msg || "Existem pendências de empresas relacionadas para a fase 4.");
      return;
    }

    const validacaoFormularioContrato = validarCamposObrigatoriosFormularioSolicitacaoContrato();
    if (!validacaoFormularioContrato.ok) {
      mostrarMensagemCard(validacaoFormularioContrato.msg || "Preencha os campos obrigatórios do formulário do contrato.");
      return;
    }

    const estadoAtualEdicao = montarEstadoEdicaoCardAtual();
    const clientRequestIdSalvar = gerarClientRequestIdKanban("card_update");
    registrarEventoLocalPendente(clientRequestIdSalvar);

    const payload = {
      ...estadoAtualEdicao,
      versao_concorrencia: versaoConcorrenciaCardAberto,
      client_request_id: clientRequestIdSalvar
    };

    const notaPendente = safeStr(inputNotaTexto?.value || "").trim();
    if (estadoInicialCardAberto && estadosEdicaoCardSaoIguais(estadoInicialCardAberto, estadoAtualEdicao) && !notaPendente) {
      fecharModalCard();
      return;
    }

    if (!Array.isArray(payload.painel_faces)) {
      payload.painel_faces = [];
    }

    const textoOriginalBotaoSalvar = btnSalvarCard ? btnSalvarCard.textContent : "";
    salvandoCardAtual = true;
    if (btnSalvarCard) {
      btnSalvarCard.disabled = true;
      btnSalvarCard.textContent = "Salvando...";
      btnSalvarCard.title = "Salvando alterações do card...";
    }

    try {
      let r = await fetchKanbanComTimeout(`/kanban/api/cards/${idCardSalvo}`, {
        method:"PUT",
        credentials: "same-origin",
        headers: headersJSON,
        body: JSON.stringify(payload)
      }, TIMEOUT_FETCH_CRITICO_KANBAN_MS);

      let j = await r.json().catch(() => null);

      if ((!j || !j.ok) && ehRespostaConcorrenciaCard(r, j)) {
        const retryVersao = await atualizarVersaoConcorrenciaParaRetrySalvar(idCardSalvo, j);

        if (retryVersao.ok && retryVersao.versao) {
          payload.versao_concorrencia = retryVersao.versao;
          versaoConcorrenciaCardAberto = retryVersao.versao;
          registrarEventoLocalPendente(clientRequestIdSalvar, 30000);

          r = await fetchKanbanComTimeout(`/kanban/api/cards/${idCardSalvo}`, {
            method:"PUT",
            credentials: "same-origin",
            headers: headersJSON,
            body: JSON.stringify(payload)
          }, TIMEOUT_FETCH_CRITICO_KANBAN_MS);

          j = await r.json().catch(() => null);
        } else if (retryVersao.msg) {
          mostrarMensagemCard(retryVersao.msg);
          atualizarEstadoSalvarCard();
          return;
        }
      }

      if (!j || !j.ok) {
        if (r.status === 409) {
          if (j && j.card_atual) {
            const cardAtualConflito = normalizarCardServidor(j.card_atual);
            inserirOuAtualizarCardLocal(cardAtualConflito);
            aplicarVersaoConcorrenciaCardAberto(cardAtualConflito, "conflito_409");
            redesenharFasesLocalmente([idNum(cardAtualConflito.IDDimKanbanFaseAtual)], null, true);
          }
          mostrarMensagemCard((j && j.msg) || "Este card foi alterado por outro usuário. Reabra o card antes de salvar novamente.");
          cardAbertoConflitoExterno = true;
          atualizarEstadoSalvarCard();
          return;
        }

        mostrarMensagemCard((j && j.msg) || "Erro ao salvar");
        return;
      }

      if (j.card) {
        const cardAtualizado = normalizarCardServidor(j.card);
        atualizarCabecalhoModalCard(cardAtualizado);
        inserirOuAtualizarCardLocal(cardAtualizado);
        aplicarVersaoConcorrenciaCardAberto(cardAtualizado, "salvar_sucesso");
      }

      if (Array.isArray(j.tags)) {
        setTagsDoCard(idCardSalvo, j.tags);
        if (idNum(cardAbertoId) === idCardSalvo && modalCard.style.display === "block") {
          renderTagsNoCard(j.tags);
        }
      }

      if (Array.isArray(j.notas) && !j.payload_minimo) {
        mapaNotasPorCard.set(
          idCardSalvo,
          j.notas.map(n => Object.assign({}, n || {}))
        );
      }

      // A sincronização principal do card terminou aqui. Fecho o modal imediatamente
      // e deixo tarefas auxiliares (nota, redesenho e resumo) em segundo plano.
      // Isso evita o botão ficar preso em "Salvando..." por causa de endpoints pós-save,
      // renderizações pesadas ou Socket/Celery lento.
      const notaParaSalvarAposFechar = notaPendente;
      const codFacesParaLimparCache = extrairCodFacesPainelFaces(payload.painel_faces);
      const cardAtual = obterCardPorId(idCardSalvo);
      const idFaseAtual = idNum(cardAtual?.IDDimKanbanFaseAtual || j?.card?.IDDimKanbanFaseAtual || 0);
      const mensagemSucessoSalvar = j?.msg || "Alterações salvas";

      fecharModalCard();
      mostrarMensagemBoard(mensagemSucessoSalvar, "sucesso", 2500);

      window.setTimeout(async () => {
        const avisosPosSalvamento = [];

        try {
          const resNota = await salvarNotaSeTiverTexto(idCardSalvo, notaParaSalvarAposFechar);
          if (!resNota.ok) {
            avisosPosSalvamento.push(resNota.msg || "O card foi salvo, mas a nota não foi gravada.");
          }
        } catch (erroNota) {
          avisosPosSalvamento.push(mensagemErroComunicacaoKanban(erroNota, "O card foi salvo, mas houve falha de comunicação ao gravar a nota."));
        }

        try {
          // A sincronização de Aditivo/Novo Contrato já é feita no backend durante o PUT do card
          // (_sincronizar_tipo_contrato_card com aplicar_tags=True).
          // Aqui eu confio nas tags retornadas pelo próprio salvamento principal.
          limparCacheCalendarioOcupacao(codFacesParaLimparCache);

          if (idFaseAtual) {
            redesenharFasesLocalmente([idFaseAtual], null, true);
          } else {
            await carregar();
          }
        } catch (erroPosSave) {
          console.warn("Card salvo, mas houve falha ao atualizar o quadro após salvar.", erroPosSave);
          avisosPosSalvamento.push("O card foi salvo, mas o quadro pode precisar ser atualizado.");
        }

        if (avisosPosSalvamento.length) {
          mostrarMensagemBoard(`${mensagemSucessoSalvar} ${avisosPosSalvamento.join(" ")}`, "alerta", 7000);
        }

        agendarRecarregarResumoComercial();
      }, 0);
    } catch (erroSalvar) {
      console.warn("Falha de comunicação ao salvar card", erroSalvar);
      mostrarMensagemCard(mensagemErroComunicacaoKanban(erroSalvar, "Erro de comunicação ao salvar o card. Tente novamente."));
    } finally {
      salvandoCardAtual = false;
      if (btnSalvarCard) {
        btnSalvarCard.textContent = textoOriginalBotaoSalvar || "Salvar";
      }
      atualizarEstadoSalvarCard();
    }
  });

  function renderNotas(notas) {
    if (!listaNotas) return;

    listaNotas.innerHTML = "";

    (Array.isArray(notas) ? notas : []).forEach(n => {
      listaNotas.appendChild(el("div", {class:"kb-nota"}, [
        el("small", {}, [String(n.CriadoEm || "")]),
        el("div", {style:"margin-top:6px;"}, [n.Texto || ""])
      ]));
    });
  }

  if (buscaKanban){
    buscaKanban.addEventListener("input", () => {
      clearTimeout(debounceBuscaTimer);

      debounceBuscaTimer = setTimeout(() => {
        termoBusca = buscaKanban.value || "";
        aplicarBusca();
      }, 120);

      agendarSugestoesBuscaKanban();
    });

    buscaKanban.addEventListener("focus", () => {
      if (safeStr(buscaKanban.value || "").trim().length >= MIN_CARACTERES_SUGESTAO_BUSCA_KANBAN) {
        agendarSugestoesBuscaKanban();
      }
    });

    buscaKanban.addEventListener("keydown", async (evento) => {
      if (!listaBuscaKanban || listaBuscaKanban.hidden) return;

      if (evento.key === "Escape") {
        evento.preventDefault();
        fecharListaSugestoesBuscaKanban();
        return;
      }

      if (evento.key === "ArrowDown") {
        evento.preventDefault();
        definirItemAtivoSugestaoBuscaKanban(indiceSugestaoBuscaKanbanAtiva + 1);
        return;
      }

      if (evento.key === "ArrowUp") {
        evento.preventDefault();
        definirItemAtivoSugestaoBuscaKanban(
          indiceSugestaoBuscaKanbanAtiva <= 0
            ? sugestoesBuscaKanbanAtual.length - 1
            : indiceSugestaoBuscaKanbanAtiva - 1
        );
        return;
      }

      if (evento.key === "Enter" && indiceSugestaoBuscaKanbanAtiva >= 0) {
        evento.preventDefault();
        const item = sugestoesBuscaKanbanAtual[indiceSugestaoBuscaKanbanAtiva];
        if (item) await selecionarSugestaoBuscaKanban(item);
      }
    });
  }

  btnFiltroVendedor?.addEventListener("click", (evento) => {
    evento.preventDefault();
    evento.stopPropagation();
    alternarMenuFiltro(filtroVendedorWrap, menuFiltroVendedor, inputFiltroVendedor);
  });

  btnFiltroTag?.addEventListener("click", (evento) => {
    evento.preventDefault();
    evento.stopPropagation();
    alternarMenuFiltro(filtroTagWrap, menuFiltroTag, inputFiltroTag);
  });

  inputFiltroVendedor?.addEventListener("input", () => {
    renderizarFiltrosMultiselect();
  });

  inputFiltroTag?.addEventListener("input", () => {
    renderizarFiltrosMultiselect();
  });

  menuFiltroVendedor?.addEventListener("click", (evento) => {
    evento.stopPropagation();
  });

  menuFiltroTag?.addEventListener("click", (evento) => {
    evento.stopPropagation();
  });

  document.addEventListener("click", (evento) => {
    const alvo = evento.target;
    if (comboBuscaKanban && comboBuscaKanban.contains(alvo)) return;
    if (filtroVendedorWrap && filtroVendedorWrap.contains(alvo)) return;
    if (filtroTagWrap && filtroTagWrap.contains(alvo)) return;
    fecharListaSugestoesBuscaKanban();
    fecharMenusFiltros();
  });

  if (btnLimparBusca){
    btnLimparBusca.addEventListener("click", () => {
      termoBusca = "";
      vendedoresSelecionados.clear();
      tagsSelecionadasFiltro.clear();

      if (buscaKanban) {
        buscaKanban.value = "";
        buscaKanban.focus();
      }

      fecharListaSugestoesBuscaKanban();

      if (inputFiltroVendedor) {
        inputFiltroVendedor.value = "";
      }

      if (inputFiltroTag) {
        inputFiltroTag.value = "";
      }

      renderizarFiltrosMultiselect();
      aplicarBusca();
    });
  }

  document.getElementById("btnNovaFase")?.addEventListener("click", () => {
    if (!USUARIO_PODE_GERENCIAR_FASES_E_TAGS) return;
    abrirModalNovaFase();
  });

  document.getElementById("btnFecharFase")?.addEventListener("click", () => {
    if (modalFase) modalFase.style.display = "none";
    resetModalFase();
  });

  modalFase?.addEventListener("click", (e) => {
    if (e.target === modalFase) {
      modalFase.style.display = "none";
      resetModalFase();
    }
  });

  faseUsarCor?.addEventListener("change", () => {
    if (faseCorHex) faseCorHex.disabled = !faseUsarCor.checked;
  });

  btnLimparCorFase?.addEventListener("click", () => {
    if (faseUsarCor) faseUsarCor.checked = false;
    if (faseCorHex) {
      faseCorHex.value = "#0B4EA2";
      faseCorHex.disabled = true;
    }
  });

  document.getElementById("btnSalvarFase")?.addEventListener("click", async () => {
    if (!USUARIO_PODE_GERENCIAR_FASES_E_TAGS) return;
    const nome = (faseNomeInput?.value || "").trim();
    const tipo = faseTipoSelect?.value || "ATIVA";
    const corHex = faseUsarCor?.checked ? normalizarCorHex(faseCorHex?.value || "") : null;
    const corTextoHex = corHex ? corTextoPorFundo(corHex) : null;

    msgFase.style.display = "none";
    msgFase.textContent = "";

    const payload = { nome, tipo, cor_hex: corHex, cor_texto_hex: corTextoHex };
    const url = faseEditandoId
      ? `/kanban/api/fases/${faseEditandoId}`
      : `/kanban/api/kanbans/${ID_KANBAN}/fases`;
    const method = faseEditandoId ? "PUT" : "POST";

    const r = await fetchKanbanComTimeout(url, {
      method,
      credentials: "same-origin",
      headers: headersJSON,
      body: JSON.stringify(payload)
    }, TIMEOUT_FETCH_PADRAO_KANBAN_MS);

    const j = await r.json().catch(() => null);
    if (!j || !j.ok) {
      msgFase.textContent = (j && (j.msg || j.erro)) || `Erro (HTTP ${r.status})`;
      msgFase.style.display = "block";
      return;
    }

    aplicarFaseRetornadaServidor(j.fase);
    if (modalFase) modalFase.style.display = "none";
    resetModalFase();

    if (j.fase) {
      renderBoardCompleto();
      atualizarResumoBusca();
    } else {
      await carregar();
    }
  });

  function atualizarTagsCatalogoSeNecessario(tag){
    const idTag = idNum(tag?.IDDimKanbanTag);
    if (!idTag) return;

    const idx = tagsCatalogo.findIndex(t => idNum(t.IDDimKanbanTag) === idTag);
    if (idx >= 0) {
      tagsCatalogo[idx] = Object.assign({}, tagsCatalogo[idx], tag);
    } else {
      tagsCatalogo.push(Object.assign({}, tag));
    }

    if (cardAbertoId) {
      const selectTagCard = document.getElementById("selectTagCard");
      const valorAtual = safeStr(selectTagCard.value);
      selectTagCard.innerHTML = "";
      tagsCatalogo.forEach(t => {
        selectTagCard.appendChild(el("option", {value: t.IDDimKanbanTag}, [t.NomeTag]));
      });
      if (valorAtual) selectTagCard.value = valorAtual;
    }
  }

  function tratarAtualizacaoExternaNoCardAberto(idCard, mensagem){
    if (!cardAbertoId || idNum(cardAbertoId) !== idNum(idCard) || modalCard.style.display !== "block") return;

    // Durante abertura/salvamento, o próprio fluxo do usuário pode gerar eventos de socket
    // enquanto o detalhe fresh ainda está chegando. Não trato isso como conflito externo.
    if (carregandoCardAtual || salvandoCardAtual) return;

    cardAbertoConflitoExterno = true;
    mostrarMensagemCard(mensagem || "Este card foi atualizado por outro usuário enquanto estava aberto. Reabra o card antes de salvar.");
  }

  async function sincronizarCardPorDetalhe(idCard, redesenhar = true){
    const cardAntes = obterCardPorId(idCard);
    const idFaseAntes = idNum(cardAntes?.IDDimKanbanFaseAtual || 0);

    const detalhe = await buscarDetalheCard(idCard, { fresh: true });
    if (!detalhe || !detalhe.card) return;

    const cardDepois = inserirOuAtualizarCardLocal(detalhe.card);
    aplicarVersaoConcorrenciaCardAberto(cardDepois || detalhe.card, "sincronizar_detalhe");
    setTagsDoCard(idCard, detalhe.tags || []);

    if (Array.isArray(detalhe.notas)) {
      mapaNotasPorCard.set(
        idNum(idCard),
        detalhe.notas.map(n => Object.assign({}, n || {}))
      );
    }

    if (redesenhar) {
      const idsFases = [idFaseAntes, idNum(cardDepois?.IDDimKanbanFaseAtual || detalhe.card.IDDimKanbanFaseAtual || 0)].filter(Boolean);
      redesenharFasesLocalmente(idsFases, null, true);
    }

    agendarRecarregarResumoComercial();
  }

  function transportesSocketKanban(){
    // Importante: este kanban roda atrás de Gunicorn/Docker e o erro visto no console
    // acontece no long-polling com sid. Então não abrimos polling aqui.
    // A conexão é WebSocket direto; se o servidor não aceitar WebSocket, o kanban
    // continua sincronizando pelo endpoint de versão, sem ficar martelando /socket.io.
    return ["websocket"];
  }

  function desativarSocketKanbanAposErroTransporte(){
    if (socketDesativadoPorErroTransporte) return false;
    socketDesativadoPorErroTransporte = true;
    socketConectando = false;
    socketConectado = false;

    try {
      if (socketKanban) {
        socketKanban.removeAllListeners();
        socketKanban.disconnect();
      }
    } catch (_erro) {}

    socketKanban = null;
    return true;
  }

  async function sincronizarQuadroEnquantoSocketIndisponivel(){
    if (socketConectado || socketConectando || document.hidden || sincronizacaoSemSocketEmAndamento) return;
    sincronizacaoSemSocketEmAndamento = true;

    try {
      await carregar({ fresh: true });
    } catch (erro) {
      console.warn("Falha ao sincronizar kanban sem Socket.IO", erro);
    } finally {
      sincronizacaoSemSocketEmAndamento = false;
    }
  }

  async function verificarVersaoKanbanPeriodicamente(){
    if (document.hidden || verificacaoVersaoKanbanEmAndamento || salvandoCardAtual || carregandoCardAtual) return;

    verificacaoVersaoKanbanEmAndamento = true;

    try {
      const resultado = await fetchJsonKanban(`/kanban/api/kanbans/${ID_KANBAN}/versao?_ts=${Date.now()}`, {
        headers: { "Cache-Control": "no-cache", "Pragma": "no-cache" },
        timeoutMs: 8000
      });

      if (!respostaJsonKanbanOk(resultado)) {
        return;
      }

      const versaoServidor = idNum(resultado.corpo?.versao_kanban || 0);
      if (!versaoServidor) return;

      if (!versaoKanbanAtual) {
        versaoKanbanAtual = versaoServidor;
        return;
      }

      if (versaoServidor !== versaoKanbanAtual) {
        versaoKanbanAtual = versaoServidor;
        await carregar({ fresh: true });
      } else if (!socketConectado && Date.now() - ultimaAtualizacaoAoVivoMs > 15000) {
        // Socket fora: mantenho a tela viva sem obrigar o usuário a sair/entrar.
        await sincronizarQuadroEnquantoSocketIndisponivel();
      }
    } catch (erro) {
      console.warn("Falha ao verificar versão do kanban para sincronização ao vivo.", erro);
    } finally {
      verificacaoVersaoKanbanEmAndamento = false;
    }
  }

  function conectarSocketKanban(){
    if (typeof window.io !== "function") {
      console.warn("Socket.IO client não carregado no template do kanban.");
      mostrarMensagemBoard("Tempo real indisponível agora. O kanban continua funcionando via HTTP, mas sem atualização automática entre sessões.");
      return;
    }

    if (socketConectando) {
      console.debug("conectarSocketKanban ignorado: já existe uma conexão em andamento.");
      return;
    }

    if (socketKanban && (socketKanban.connected || socketKanban.active)) {
      console.debug("conectarSocketKanban ignorado: socket já está ativo.");
      return;
    }

    if (socketKanban) {
      try {
        socketKanban.removeAllListeners();
        socketKanban.disconnect();
      } catch (_erro) {
      }
      socketKanban = null;
    }

    socketConectando = true;
    socketConectado = false;

    socketKanban = window.io(SOCKET_IO_NAMESPACE, {
      path: SOCKET_IO_PATH,
      transports: transportesSocketKanban(),
      upgrade: false,
      rememberUpgrade: false,
      tryAllTransports: false,
      withCredentials: true,
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 2000,
      reconnectionDelayMax: 15000,
      timeout: 15000,
      autoConnect: true,
      forceNew: true
    });

    socketKanban.on("connect", () => {
      socketConectando = false;
      socketConectado = true;
      socketDesativadoPorErroTransporte = false;
      ultimaAtualizacaoAoVivoMs = Date.now();
      limparMensagemBoard();
      socketKanban.emit("entrar_kanban", { id_kanban: ID_KANBAN });
    });

    if (socketKanban.io && typeof socketKanban.io.on === "function") {
      socketKanban.io.on("reconnect", () => {
        socketConectando = false;
        socketConectado = true;
        try {
          socketKanban.emit("entrar_kanban", { id_kanban: ID_KANBAN });
        } catch (erro) {
          console.warn("Falha ao reentrar na sala do kanban após reconexão.", erro);
        }
        carregar({ fresh: true }).catch((erro) => console.warn("Falha ao sincronizar kanban após reconexão do socket.", erro));
      });
    }

    socketKanban.on("disconnect", (reason) => {
      socketConectando = false;
      socketConectado = false;
      console.warn("Socket do kanban desconectado", { reason });
    });

    socketKanban.on("connect_error", (erro) => {
      socketConectando = false;
      socketConectado = false;
      console.warn("connect_error socket kanban", {
        message: erro?.message || null,
        description: erro?.description || null,
        type: erro?.type || null,
        context: erro?.context || null
      });

      if (desativarSocketKanbanAposErroTransporte()) {
        mostrarMensagemBoard("Tempo real do kanban indisponível no momento. As ações continuam funcionando e a tela vai sincronizar automaticamente em intervalos curtos.");
      }
    });

    socketKanban.on("socket_ack", (payload) => {
      console.debug("kanban socket ack", payload || {});
    });

    socketKanban.on("socket_erro", (payload) => {
      console.warn("kanban socket erro", payload || {});
      const msg = payload?.msg || payload?.erro;
      if (msg) mostrarMensagemBoard(msg);
    });

    socketKanban.on("card_edicao_descartada", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card || 0);
      if (!idCard) {
        await carregar();
        return;
      }

      try {
        await sincronizarCardPorDetalhe(idCard, true);
      } catch (erro) {
        console.warn("Falha ao sincronizar card após descarte de edição. Vou recarregar o quadro.", erro);
        await carregar();
      }
    });

    socketKanban.on("card_atualizado", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card);
      const eventoLocal = eventoSocketVeioDesteCliente(payload);
      const cardAntes = obterCardPorId(idCard);
      if (eventoLocal && idCard && cardAntes && !salvandoCardAtual) {
        return;
      }
      const idFaseAntes = idNum(cardAntes?.IDDimKanbanFaseAtual || 0);
      const cardPayload = payload.card ? normalizarCardServidor(payload.card) : null;

      if (!cardPayload) {
        await carregar();
        agendarRecarregarResumoComercial();
        return;
      }

      if (USUARIO_EH_VENDEDOR && !cardPertenceAoVendedorLogado(cardPayload)) {
        removerCardLocal(idCard);
        if (idFaseAntes) agendarRedesenhoFasesTempoReal([idFaseAntes], null, true);
        agendarRecarregarResumoComercial();
        return;
      }

      const cardSaiDoQuadro = !!(cardPayload && cardDeveSairDoQuadro(cardPayload));

      if (cardSaiDoQuadro) {
        removerCardLocal(idCard);
      } else if (cardPayload) {
        inserirOuAtualizarCardLocal(cardPayload, { reposicionarNoTopoDaFase: true });
      }

      if (idCard && Array.isArray(payload.tags)) {
        if (cardSaiDoQuadro) {
          removerTagsDoCard(idCard);
        } else {
          setTagsDoCard(idCard, payload.tags);
        }
      }

      if (idCard && Array.isArray(payload.notas)) {
        mapaNotasPorCard.set(
          idCard,
          payload.notas.map(n => Object.assign({}, n || {}))
        );
      }

      if (idCard && movimentosCardsPendentes.has(idCard)) {
        return;
      }

      if (!(salvandoCardAtual && idNum(cardAbertoId) === idCard)) {
        tratarAtualizacaoExternaNoCardAberto(
          idCard,
          "Este card foi atualizado por outro usuário enquanto estava aberto. Reabra o card antes de salvar."
        );
      }

      const faseIdDepois = idNum(
        payload.card?.IDDimKanbanFaseAtual ||
        obterCardPorId(idCard)?.IDDimKanbanFaseAtual
      );
      const idsFases = [idFaseAntes, faseIdDepois].filter(Boolean);

      if (!movimentosCardsPendentes.has(idCard) && idFaseAntes && faseIdDepois && idFaseAntes !== faseIdDepois) {
        ajustarQuantidadeFaseLocal(idFaseAntes, -1);
        ajustarQuantidadeFaseLocal(faseIdDepois, 1);
      }

      if (idsFases.length) {
        const mapaQuantidadesAoVivo = montarMapaQuantidadesAtualizacaoAoVivo(
          idsFases,
          faseIdDepois,
          (!cardAntes && !cardSaiDoQuadro) ? 1 : 0
        );
        agendarRedesenhoFasesTempoReal(idsFases, mapaQuantidadesAoVivo, true);
        if (!cardSaiDoQuadro && faseIdDepois) {
          destacarCardNaFase(idCard, faseIdDepois);
        }
      } else {
        await carregar();
      }

      agendarRecarregarResumoComercial();
    });

    socketKanban.on("card_movido", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card);
      const idFaseDe = idNum(payload.id_fase_de);
      const idFasePara = idNum(payload.id_fase_para);
      const eventoLocal = eventoSocketVeioDesteCliente(payload);

      const cardPayload = payload.card ? normalizarCardServidor(payload.card) : null;

      if (!cardPayload) {
        await carregar();
        agendarRecarregarResumoComercial();
        return;
      }

      if (USUARIO_EH_VENDEDOR && !cardPertenceAoVendedorLogado(cardPayload)) {
        removerCardLocal(idCard);
        const idsFasesRemocao = [idFaseDe, idFasePara].filter(Boolean);
        if (idsFasesRemocao.length) agendarRedesenhoFasesTempoReal(idsFasesRemocao, null, true);
        agendarRecarregarResumoComercial();
        return;
      }

      const cardSaiDoQuadro = !!(
        (cardPayload && cardDeveSairDoQuadro(cardPayload, idFasePara)) ||
        (!cardPayload && faseEhFinalDoQuadro(idFasePara))
      );

      if (cardSaiDoQuadro) {
        removerCardLocal(idCard);
      } else if (cardPayload) {
        inserirOuAtualizarCardLocal(cardPayload, { reposicionarNoTopoDaFase: true });
      } else if (idCard && idFasePara) {
        const cardLocal = obterCardPorId(idCard);
        if (cardLocal) {
          cardLocal.IDDimKanbanFaseAtual = idFasePara;
        }
      }

      if (idCard && Array.isArray(payload.tags)) {
        if (cardSaiDoQuadro) {
          removerTagsDoCard(idCard);
        } else {
          setTagsDoCard(idCard, payload.tags);
        }
      }

      if (idCard && Array.isArray(payload.notas)) {
        mapaNotasPorCard.set(
          idCard,
          payload.notas.map(n => Object.assign({}, n || {}))
        );
      }

      if (idCard && movimentosCardsPendentes.has(idCard)) {
        return;
      }

      if (eventoLocal) {
        agendarRecarregarResumoComercial(900);
        return;
      }

      tratarAtualizacaoExternaNoCardAberto(
        idCard,
        "Este card foi movido por outro usuário enquanto estava aberto. Reabra o card antes de salvar."
      );

      const idsFases = [idFaseDe, idFasePara].filter(Boolean);

      if (!movimentosCardsPendentes.has(idCard) && idFaseDe && idFasePara && idFaseDe !== idFasePara) {
        ajustarQuantidadeFaseLocal(idFaseDe, -1);
        ajustarQuantidadeFaseLocal(idFasePara, 1);
      }

      if (idsFases.length) {
        const mapaQuantidadesAoVivo = montarMapaQuantidadesAtualizacaoAoVivo(
          idsFases,
          idFasePara,
          cardSaiDoQuadro ? 0 : 1
        );
        agendarRedesenhoFasesTempoReal(idsFases, mapaQuantidadesAoVivo, true);
        if (!cardSaiDoQuadro && idFasePara) {
          destacarCardNaFase(idCard, idFasePara);
        }
      } else {
        await carregar();
      }

      agendarRecarregarResumoComercial(900);
    });

    socketKanban.on("card_criado", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card || payload.id || payload.card?.IDFatoKanbanCard || 0);
      const eventoLocal = eventoSocketVeioDesteCliente(payload);
      const cardAntes = idCard ? obterCardPorId(idCard) : null;
      if (eventoLocal && cardAntes) {
        return;
      }
      const idFaseAntes = idNum(cardAntes?.IDDimKanbanFaseAtual || 0);
      const cardPayload = payload.card ? normalizarCardServidor(payload.card) : null;

      if (!idCard || !cardPayload) {
        await carregar();
        agendarRecarregarResumoComercial();
        return;
      }

      const idFaseDepois = idNum(
        cardPayload.IDDimKanbanFaseAtual ||
        payload.id_fase_para ||
        payload.id_fase ||
        0
      );

      if (USUARIO_EH_VENDEDOR && !cardPertenceAoVendedorLogado(cardPayload)) {
        removerCardLocal(idCard);
        const idsFasesRemocao = [idFaseAntes, idFaseDepois].filter(Boolean);
        if (idsFasesRemocao.length) agendarRedesenhoFasesTempoReal(idsFasesRemocao, null, true);
        agendarRecarregarResumoComercial();
        return;
      }

      const cardSaiDoQuadro = cardDeveSairDoQuadro(cardPayload);

      if (cardSaiDoQuadro) {
        removerCardLocal(idCard);
      } else {
        inserirOuAtualizarCardLocal(cardPayload, { reposicionarNoTopoDaFase: true });
      }

      if (idCard && Array.isArray(payload.tags)) {
        if (cardSaiDoQuadro) {
          removerTagsDoCard(idCard);
        } else {
          setTagsDoCard(idCard, payload.tags);
        }
      }

      if (idCard && Array.isArray(payload.notas)) {
        mapaNotasPorCard.set(
          idCard,
          payload.notas.map(n => Object.assign({}, n || {}))
        );
      }

      if (!cardAntes && !cardSaiDoQuadro && idFaseDepois) {
        ajustarQuantidadeFaseLocal(idFaseDepois, 1);
      }

      const idsFases = [idFaseAntes, idFaseDepois].filter(Boolean);
      if (idsFases.length) {
        const mapaQuantidadesAoVivo = montarMapaQuantidadesAtualizacaoAoVivo(
          idsFases,
          idFaseDepois,
          (!cardAntes && !cardSaiDoQuadro) ? 1 : 0
        );
        agendarRedesenhoFasesTempoReal(idsFases, mapaQuantidadesAoVivo, true);
        if (!cardSaiDoQuadro && idFaseDepois) {
          destacarCardNaFase(idCard, idFaseDepois);
        }
      } else {
        await carregar();
      }

      agendarRecarregarResumoComercial(900);
    });

    socketKanban.on("card_inativado", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card);
      if (!idCard) return;
      const idFaseAntes = idNum(payload.id_fase_de || obterCardPorId(idCard)?.IDDimKanbanFaseAtual || 0);
      removerCardInativadoLocal(idCard, idFaseAntes);
      tratarAtualizacaoExternaNoCardAberto(idCard, "Este card foi inativado por outro usuário.");
    });

    socketKanban.on("tag_criada", (payload = {}) => {
      atualizarTagsCatalogoSeNecessario(payload);
    });

    socketKanban.on("card_tag_adicionada", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card);
      if (!idCard) return;

      if (payload.card || Array.isArray(payload.tags)) {
        const cardAntes = obterCardPorId(idCard);
        const idFaseAntes = idNum(cardAntes?.IDDimKanbanFaseAtual || 0);
        const cardServidor = payload.card ? normalizarCardServidor(payload.card) : cardAntes;
        if (cardServidor) inserirOuAtualizarCardLocal(cardServidor);
        if (Array.isArray(payload.tags)) setTagsDoCard(idCard, payload.tags);

        if (idNum(cardAbertoId) === idCard && modalCard.style.display === "block" && Array.isArray(payload.tags)) {
          renderTagsNoCard(payload.tags);
        }

        const idFaseDepois = idNum(cardServidor?.IDDimKanbanFaseAtual || idFaseAntes || 0);
        if (idFaseDepois) agendarRedesenhoFasesTempoReal([idFaseAntes, idFaseDepois].filter(Boolean), null, true);
        agendarRecarregarResumoComercial();
        return;
      }

      await sincronizarTagsDoCardAberto(idCard, { redesenhar: true });
    });

    socketKanban.on("card_tag_removida", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card);
      if (!idCard) return;

      if (payload.card || Array.isArray(payload.tags)) {
        const cardAntes = obterCardPorId(idCard);
        const idFaseAntes = idNum(cardAntes?.IDDimKanbanFaseAtual || 0);
        const cardServidor = payload.card ? normalizarCardServidor(payload.card) : cardAntes;
        if (cardServidor) inserirOuAtualizarCardLocal(cardServidor);
        if (Array.isArray(payload.tags)) setTagsDoCard(idCard, payload.tags);

        if (idNum(cardAbertoId) === idCard && modalCard.style.display === "block" && Array.isArray(payload.tags)) {
          renderTagsNoCard(payload.tags);
        }

        const idFaseDepois = idNum(cardServidor?.IDDimKanbanFaseAtual || idFaseAntes || 0);
        if (idFaseDepois) agendarRedesenhoFasesTempoReal([idFaseAntes, idFaseDepois].filter(Boolean), null, true);
        agendarRecarregarResumoComercial();
        return;
      }

      await sincronizarTagsDoCardAberto(idCard, { redesenhar: true });
    });

    socketKanban.on("card_nota_criada", async (payload = {}) => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      const idCard = idNum(payload.id_card);
      const eventoLocal = eventoSocketVeioDesteCliente(payload);
      if (!idCard) return;

      if (payload.nota) {
        const notasAtuais = mapaNotasPorCard.get(idCard) || [];
        const idNotaNova = idNum(payload.nota.IDFatoKanbanCardNota || payload.nota.IDFatoKanbanCardObservacoes || 0);
        const jaExisteNota = idNotaNova && notasAtuais.some(n => idNum(n.IDFatoKanbanCardNota || n.IDFatoKanbanCardObservacoes || 0) === idNotaNova);
        if (!jaExisteNota) {
          mapaNotasPorCard.set(idCard, [Object.assign({}, payload.nota || {}), ...notasAtuais]);
        }
      }

      if (idNum(cardAbertoId) === idCard && modalCard.style.display === "block") {
        renderNotas(mapaNotasPorCard.get(idCard) || []);

        if (!eventoLocal && !salvandoCardAtual) {
          cardAbertoConflitoExterno = true;
          mostrarMensagemCard("Uma nota foi adicionada a este card em outra sessão. Reabra o card antes de salvar.");
        }
      }

      if (payload.card || Array.isArray(payload.tags)) {
        const cardServidor = payload.card ? normalizarCardServidor(payload.card) : null;
        if (cardServidor) inserirOuAtualizarCardLocal(cardServidor);
        if (Array.isArray(payload.tags)) setTagsDoCard(idCard, payload.tags);
        const idFase = idNum(cardServidor?.IDDimKanbanFaseAtual || obterCardPorId(idCard)?.IDDimKanbanFaseAtual || 0);
        if (idFase) agendarRedesenhoFasesTempoReal([idFase], null, true);
        agendarRecarregarResumoComercial();
        return;
      }

      if (!payload.nota) {
        await sincronizarCardPorDetalhe(idCard, true);
      }
    });

    socketKanban.on("fase_criada", async () => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      await carregar();
    });

    socketKanban.on("fase_atualizada", async () => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      await carregar();
    });

    socketKanban.on("fase_inativada", async () => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      await carregar();
    });

    socketKanban.on("fases_reordenadas", async () => {
      ultimaAtualizacaoAoVivoMs = Date.now();
      await carregar();
    });

    socketKanban.on("kanban_inativado", () => {
      mostrarMensagemBoard("Este kanban foi inativado em outra sessão.");
      window.setTimeout(() => {
        window.location.reload();
      }, 1200);
    });
  }

  window.addEventListener("beforeunload", () => {
    if (socketKanban) {
      try {
        if (socketConectado) {
          socketKanban.emit("sair_kanban", { id_kanban: ID_KANBAN });
        }
        socketKanban.removeAllListeners();
        socketKanban.disconnect();
      } catch (_erro) {
      }
    }
    socketKanban = null;
    socketConectado = false;
    socketConectando = false;
  });

  async function inicializarKanban(){
    if (kanbanInicializado) return;
    kanbanInicializado = true;

    configurarEventosDelegadosCardsKanban();

    // Inicio o download do cliente Socket.IO em paralelo com a carga do quadro.
    // Não bloqueia a primeira pintura e deixa o tempo real pronto mais cedo.
    const promessaClienteSocket = garantirClienteSocketIo();

    const carregouEstruturaRapida = await carregarEstruturaRapidaKanban();
    if (!carregouEstruturaRapida) {
      await carregar();
    }

    // Catálogo de empresas é carregado sob demanda no modal. Fazer isso na abertura
    // concorria com /dados, resumo comercial e Socket.IO, causando pequenos travamentos.
    window.setTimeout(async () => {
      if (document.hidden) return;
      await esperarJanelaOciosaKanban(1200);
      carregarEmpresas().catch((erro) => console.warn("Pré-carga ociosa de empresas falhou", erro));
    }, 3500);

    window.setInterval(() => {
      agendarRecarregarResumoComercial(1200);
    }, 60000);

    window.setInterval(() => {
      void verificarVersaoKanbanPeriodicamente();
    }, 5000);

    window.addEventListener("focus", () => {
      void verificarVersaoKanbanPeriodicamente();
    });

    const clienteSocketOk = await promessaClienteSocket;
    if (!clienteSocketOk) {
      mostrarMensagemBoard("Não foi possível carregar a biblioteca do tempo real. O kanban continua operando normalmente, mas sem atualização automática entre sessões.");
      return;
    }
    conectarSocketKanban();
  }

  configurarScrollKanban();
  inicializarKanban().catch((erro) => {
    console.error("Falha ao inicializar kanban", erro);
    if (typeof mostrarMensagemBoard === "function") {
      mostrarMensagemBoard("Falha ao inicializar o Kanban. Confira no DevTools qual requisição ficou vermelha na aba Network.");
    }
  });
})();


