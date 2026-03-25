(function (window, document) {
  "use strict";

  const utils = window.MLPipelineDashboardUtils;
  if (!utils) {
    throw new Error("utils.js precisa ser carregado antes de lista.js");
  }

  function iniciarListaPipelines(configuracaoFrontend = {}) {
    const prefixo = configuracaoFrontend.url_prefixo || "";
    const rotaListaApi = `${prefixo}${configuracaoFrontend.rota_lista_api || "/api/pipelines"}`;
    const rotaDetalhe = `${prefixo}/pipelines`;

    const elementos = {
      campoDagId: document.getElementById("campoDagId"),
      campoStatus: document.getElementById("campoStatus"),
      campoLimite: document.getElementById("campoLimite"),
      btnAplicar: document.getElementById("btnAplicar"),
      btnLimpar: document.getElementById("btnLimpar"),
      mensagemStatus: document.getElementById("mensagemStatus"),
      listaPipelines: document.getElementById("listaPipelines"),
      kpiTotal: document.getElementById("kpiTotal"),
      kpiSuccess: document.getElementById("kpiSuccess"),
      kpiFailed: document.getElementById("kpiFailed"),
      kpiRunning: document.getElementById("kpiRunning"),
    };

    if (!elementos.listaPipelines) {
      return;
    }

    function montarUrlDetalhe(item) {
      const dagId = encodeURIComponent(String(item.dag_id || ""));
      const parametros = new URLSearchParams();
      if (item.run_id) parametros.set("run_id", item.run_id);
      return `${rotaDetalhe}/${dagId}${parametros.toString() ? `?${parametros.toString()}` : ""}`;
    }

    function montarChipsTags(tags) {
      const lista = utils.arraySeguro(tags).filter(Boolean);
      if (!lista.length) {
        return '<span class="ml-chip">Sem tags</span>';
      }
      return lista.slice(0, 8).map((tag) => `<span class="ml-chip">${utils.escaparHtml(tag)}</span>`).join("");
    }

    function atualizarKpis(itens) {
      const linhas = utils.arraySeguro(itens);
      const total = linhas.length;
      const success = linhas.filter((item) => utils.normalizarStatus(item.status) === "success").length;
      const failed = linhas.filter((item) => utils.normalizarStatus(item.status) === "failed").length;
      const running = linhas.filter((item) => ["running", "queued"].includes(utils.normalizarStatus(item.status))).length;

      utils.atribuirTexto(elementos.kpiTotal, total, "0");
      utils.atribuirTexto(elementos.kpiSuccess, success, "0");
      utils.atribuirTexto(elementos.kpiFailed, failed, "0");
      utils.atribuirTexto(elementos.kpiRunning, running, "0");
    }

    function montarCard(item) {
      const descricao = utils.escaparHtml(utils.truncarTexto(item.descricao_curta || item.descricao || "Sem descrição cadastrada.", 180));
      const tags = montarChipsTags(item.dag_tags || item.tags || []);
      return `
        <article class="ml-card-pipeline">
          <div class="ml-card-pipeline-topo">
            <div class="ml-card-pipeline-titulo">
              <h3 title="${utils.escaparHtml(item.dag_id || "-")}">${utils.escaparHtml(item.nome || item.dag_id || "-")}</h3>
              <div class="ml-card-pipeline-descricao">${descricao}</div>
            </div>
            <span class="ml-badge-status ${utils.classeStatus(item.status)}">${utils.escaparHtml(item.status || "unknown")}</span>
          </div>

          <div class="ml-grid-meta">
            <div class="ml-meta"><small>DAG ID</small><strong>${utils.escaparHtml(item.dag_id || "-")}</strong></div>
            <div class="ml-meta"><small>Owner</small><strong>${utils.escaparHtml(item.owner || "-")}</strong></div>
            <div class="ml-meta"><small>Run ID</small><strong>${utils.escaparHtml(item.run_id || "-")}</strong></div>
            <div class="ml-meta"><small>Tipo da run</small><strong>${utils.escaparHtml(item.run_type || "-")}</strong></div>
            <div class="ml-meta"><small>Execution date</small><strong>${utils.escaparHtml(utils.formatarDataHora(item.execution_date))}</strong></div>
            <div class="ml-meta"><small>Duração (s)</small><strong>${utils.escaparHtml(item.duration_seconds ?? "-")}</strong></div>
            <div class="ml-meta"><small>Início</small><strong>${utils.escaparHtml(utils.formatarDataHora(item.start_date))}</strong></div>
            <div class="ml-meta"><small>Fim</small><strong>${utils.escaparHtml(utils.formatarDataHora(item.end_date))}</strong></div>
          </div>

          <div class="ml-chips">${tags}</div>

          <div class="ml-card-pipeline-rodape">
            <div class="ml-card-pipeline-info">
              Última execução registrada no catálogo do plugin. Abra o detalhe para ver tasks, objetos, documentação,
              healthcheck, lineage e métricas do pipeline.
            </div>
            <a class="ml-btn ml-btn--primario" href="${montarUrlDetalhe(item)}">Abrir painel</a>
          </div>
        </article>
      `;
    }

    async function carregarLista() {
      const parametros = {
        dag_id: utils.texto(elementos.campoDagId?.value),
        status: utils.texto(elementos.campoStatus?.value),
        limite: utils.texto(elementos.campoLimite?.value) || "50",
      };

      utils.atribuirTexto(elementos.mensagemStatus, "Consultando pipelines de Machine Learning...");
      utils.atribuirHtml(elementos.listaPipelines, '<div class="ml-estado-vazio">Carregando lista...</div>');

      try {
        const url = utils.abrirUrlComParametros(rotaListaApi, parametros);
        const payload = await utils.obterJson(url);
        const itens = utils.arraySeguro(payload.itens);

        atualizarKpis(itens);

        if (!itens.length) {
          utils.atribuirHtml(
            elementos.listaPipelines,
            '<div class="ml-estado-vazio">Nenhum pipeline de Machine Learning foi encontrado para os filtros atuais.</div>',
          );
          utils.atribuirTexto(elementos.mensagemStatus, "Consulta concluída sem resultados.");
          return;
        }

        utils.atribuirHtml(elementos.listaPipelines, itens.map(montarCard).join(""));
        utils.atribuirTexto(elementos.mensagemStatus, `${itens.length} pipeline(s) retornado(s) pela API.`);
      } catch (erro) {
        console.error(erro);
        atualizarKpis([]);
        utils.atribuirHtml(
          elementos.listaPipelines,
          `<div class="ml-estado-vazio">Erro ao carregar a lista de pipelines.<br>${utils.escaparHtml(erro.message || "Erro desconhecido.")}</div>`,
        );
        utils.atribuirTexto(elementos.mensagemStatus, "A consulta falhou. Veja o console do navegador para detalhes.");
      }
    }

    function limparFiltros() {
      if (elementos.campoDagId) elementos.campoDagId.value = "";
      if (elementos.campoStatus) elementos.campoStatus.value = "";
      if (elementos.campoLimite) elementos.campoLimite.value = "50";
      carregarLista();
    }

    elementos.btnAplicar?.addEventListener("click", carregarLista);
    elementos.btnLimpar?.addEventListener("click", limparFiltros);
    elementos.campoDagId?.addEventListener("keydown", (evento) => {
      if (evento.key === "Enter") {
        evento.preventDefault();
        carregarLista();
      }
    });

    carregarLista();
  }

  window.MLPipelineDashboardLista = { iniciarListaPipelines };
})(window, document);