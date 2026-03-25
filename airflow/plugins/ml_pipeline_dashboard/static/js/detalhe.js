(function (window, document) {
  "use strict";

  const utils = window.MLPipelineDashboardUtils;
  const charts = window.MLPipelineDashboardCharts;

  if (!utils) {
    throw new Error("utils.js precisa ser carregado antes de detalhe.js");
  }

  function iniciarDetalhePipeline(configuracao = {}) {
    const estado = {
      dashboard: null,
      tasks: [],
      taskSelecionada: null,
    };

    const dagId = configuracao.dagId || window.DAG_ID || "";
    const runId = configuracao.runId || window.RUN_ID || "";
    const frontend = configuracao.frontend || window.CONFIG || {};
    const prefixo = frontend.url_prefixo || "";
    const rotaDashboard = `${prefixo}/api/pipelines/${encodeURIComponent(dagId)}` + (runId ? `?run_id=${encodeURIComponent(runId)}` : "");

    const elementos = {
      tituloDag: document.getElementById("tituloDag"),
      subtituloDag: document.getElementById("subtituloDag"),
      heroBadges: document.getElementById("heroBadges"),
      btnRecarregar: document.getElementById("btnRecarregar"),
      btnCopiarJson: document.getElementById("btnCopiarJson"),
      kpiStatus: document.getElementById("kpiStatus"),
      kpiTasks: document.getElementById("kpiTasks"),
      kpiSuccess: document.getElementById("kpiSuccess"),
      kpiFalhas: document.getElementById("kpiFalhas"),
      kpiModelo: document.getElementById("kpiModelo"),
      kpiDominio: document.getElementById("kpiDominio"),
      listaEtapas: document.getElementById("listaEtapas"),
      resumoDocumentacao: document.getElementById("resumoDocumentacao"),
      resumoModelo: document.getElementById("resumoModelo"),
      metricasResumo: document.getElementById("metricasResumo"),
      listaEntradas: document.getElementById("listaEntradas"),
      listaSaidas: document.getElementById("listaSaidas"),
      tituloTaskSelecionada: document.getElementById("tituloTaskSelecionada"),
      descricaoTaskSelecionada: document.getElementById("descricaoTaskSelecionada"),
      metricasTask: document.getElementById("metricasTask"),
      sqlTask: document.getElementById("sqlTask"),
      amostraTask: document.getElementById("amostraTask"),
      listaObjetosEntrada: document.getElementById("listaObjetosEntrada"),
      listaObjetosSaida: document.getElementById("listaObjetosSaida"),
      graficoMetricasPipeline: document.getElementById("graficoMetricasPipeline"),
      graficoStatusTasks: document.getElementById("graficoStatusTasks"),
      textoHealth: document.getElementById("textoHealth"),
      cardsHealth: document.getElementById("cardsHealth"),
      jsonCompleto: document.getElementById("jsonCompleto"),
      tabs: Array.from(document.querySelectorAll(".tab")),
      abas: Array.from(document.querySelectorAll(".aba")),
    };

    function ativarAba(idAba) {
      elementos.tabs.forEach((tab) => {
        tab.classList.toggle("ativa", tab.dataset.aba === idAba);
      });
      elementos.abas.forEach((aba) => {
        aba.classList.toggle("ativa", aba.id === idAba);
      });
    }

    function chip(texto, classe = "") {
      return `<span class="chip ${classe}">${utils.escaparHtml(texto)}</span>`;
    }

    function contarStatus(tasks) {
      const resumo = { success: 0, failed: 0, running: 0, queued: 0, outros: 0 };
      utils.arraySeguro(tasks).forEach((task) => {
        const status = utils.normalizarStatus(task?.status);
        if (status === "success") resumo.success += 1;
        else if (status === "failed") resumo.failed += 1;
        else if (status === "running") resumo.running += 1;
        else if (status === "queued") resumo.queued += 1;
        else resumo.outros += 1;
      });
      return resumo;
    }

    function formatarValorMetrica(valor) {
      if (valor === null || valor === undefined || valor === "") return "-";
      if (typeof valor === "number") {
        if (Math.abs(valor) <= 1 && Math.abs(valor) !== Math.floor(Math.abs(valor))) {
          return utils.formatarNumero(valor, 4);
        }
        if (Number.isInteger(valor)) return utils.formatarInteiro(valor);
        return utils.formatarNumero(valor, 4);
      }
      return String(valor);
    }

    function montarMetricasCards(metricas) {
      let entradas = [];

      if (Array.isArray(metricas)) {
        entradas = metricas.map((item) => ({
          chave: item.label || item.chave || item.metric_label || "Métrica",
          valor: item.valor,
        }));
      } else {
        entradas = Object.entries(utils.objetoSeguro(metricas)).map(([chave, valor]) => ({ chave, valor }));
      }

      if (!entradas.length) {
        return '<div class="estado-vazio">Nenhuma métrica consolidada foi encontrada.</div>';
      }

      return entradas.map((item) => `
        <div class="metrica">
          <small>${utils.escaparHtml(item.chave)}</small>
          <strong>${utils.escaparHtml(formatarValorMetrica(item.valor))}</strong>
        </div>
      `).join("");
    }

    function montarTabelaHtml(colunas, linhas) {
      const cols = utils.arraySeguro(colunas);
      const rows = utils.arraySeguro(linhas);
      if (!cols.length || !rows.length) {
        return '<div class="estado-vazio">Nenhuma amostra tabular disponível para esta task.</div>';
      }

      const head = cols.map((col) => `<th>${utils.escaparHtml(col)}</th>`).join("");
      const body = rows.map((linha) => {
        const registro = utils.objetoSeguro(linha);
        return `<tr>${cols.map((col) => `<td>${utils.escaparHtml(registro[col] ?? "-")}</td>`).join("")}</tr>`;
      }).join("");

      return `
        <div class="tabela-wrap">
          <table>
            <thead><tr>${head}</tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      `;
    }

    function montarUrlTabela(objeto) {
      const item = utils.objetoSeguro(objeto);
      if (!item.conn_id || !item.schema || !item.tabela) return "";
      return utils.abrirUrlComParametros(`${prefixo}${frontend.rota_tabela_html || "/tabela"}`, {
        conexao_id: item.conn_id,
        banco: item.banco,
        schema: item.schema,
        tabela: item.tabela,
      });
    }

    function montarUrlDownload(objeto) {
      const item = utils.objetoSeguro(objeto);
      const caminho = utils.texto(item.caminho_arquivo || item.path || item.file_path);
      if (!caminho) return "";
      return utils.abrirUrlComParametros(`${prefixo}${frontend.rota_download || "/arquivo/download"}`, { caminho });
    }

    function montarObjetoHtml(objeto) {
      const item = utils.objetoSeguro(objeto);
      const nome = item.nome || item.nome_amigavel || item.tabela || item.caminho_arquivo || item.tipo || "Objeto";
      const urlTabela = montarUrlTabela(item);
      const urlDownload = montarUrlDownload(item);

      return `
        <article class="objeto">
          <div class="objeto-topo">
            <div>
              <h4>${utils.escaparHtml(nome)}</h4>
              <div class="chips" style="margin-top:8px;">
                ${chip(item.tipo || "objeto")}
                ${chip(`direção: ${item.direcao || item.grupo || "apoio"}`)}
              </div>
            </div>
          </div>

          <div class="objeto-meta">
            <div><strong>Conn ID:</strong> ${utils.escaparHtml(item.conn_id || "-")}</div>
            <div><strong>Banco:</strong> ${utils.escaparHtml(item.banco || "-")}</div>
            <div><strong>Schema:</strong> ${utils.escaparHtml(item.schema || "-")}</div>
            <div><strong>Tabela:</strong> ${utils.escaparHtml(item.tabela || "-")}</div>
            <div><strong>Procedure:</strong> ${utils.escaparHtml(item.procedure || "-")}</div>
            <div><strong>Caminho:</strong> ${utils.escaparHtml(item.caminho_arquivo || item.path || "-")}</div>
          </div>

          <div class="objeto-acoes">
            ${urlTabela ? `<a class="btn-mini" href="${urlTabela}" target="_blank" rel="noopener">Abrir tabela</a>` : ""}
            ${urlDownload ? `<a class="btn-mini" href="${urlDownload}">Baixar arquivo</a>` : ""}
          </div>
        </article>
      `;
    }

    function montarListaObjetos(lista) {
      const itens = utils.arraySeguro(lista);
      if (!itens.length) {
        return '<div class="estado-vazio">Nenhum objeto encontrado.</div>';
      }
      return itens.map(montarObjetoHtml).join("");
    }

    function renderizarHero(dashboard) {
      const pipeline = utils.objetoSeguro(dashboard.pipeline);
      utils.atribuirTexto(elementos.tituloDag, dashboard.nome || dashboard.dag_id || dagId, dagId || "-");
      utils.atribuirTexto(
        elementos.subtituloDag,
        pipeline.objetivo_negocio || dashboard.descricao_curta || pipeline.documentacao_dag || "Pipeline de Machine Learning com observabilidade técnica, lineage, health e métricas.",
      );

      const badges = [
        chip(`dag_id: ${dashboard.dag_id || dagId}`, "chip-primario"),
        chip(`run_id: ${dashboard.run_id || runId || "-"}`),
        chip(`status: ${dashboard.status || "unknown"}`),
        chip(`tipo: ${dashboard.tipo_pipeline || pipeline.tipo_pipeline || "-"}`),
        chip(`subtipo: ${dashboard.subtipo_pipeline || pipeline.subtipo_pipeline || "-"}`),
      ];

      utils.arraySeguro(dashboard.tags).forEach((tag) => {
        badges.push(chip(`tag: ${tag}`));
      });

      utils.atribuirHtml(elementos.heroBadges, badges.join(""));
    }

    function renderizarKpis(dashboard) {
      const tasks = utils.arraySeguro(dashboard.tasks);
      const resumo = contarStatus(tasks);
      const modelo = utils.objetoSeguro(dashboard.modelo || utils.objetoSeguro(dashboard.pipeline).modelo);

      utils.atribuirTexto(elementos.kpiStatus, dashboard.status || "-", "-");
      utils.atribuirTexto(elementos.kpiTasks, tasks.length, "0");
      utils.atribuirTexto(elementos.kpiSuccess, resumo.success, "0");
      utils.atribuirTexto(elementos.kpiFalhas, resumo.failed, "0");
      utils.atribuirTexto(elementos.kpiModelo, modelo.nome_modelo || modelo.nome || "-", "-");
      utils.atribuirTexto(elementos.kpiDominio, dashboard.dominio || utils.objetoSeguro(dashboard.pipeline).dominio || "-", "-");
    }

    function renderizarListaEtapas(tasks) {
      const itens = utils.arraySeguro(tasks);
      if (!itens.length) {
        utils.atribuirHtml(elementos.listaEtapas, '<div class="estado-vazio">Nenhuma task encontrada nesta execução.</div>');
        return;
      }

      utils.atribuirHtml(
        elementos.listaEtapas,
        itens.map((task, indice) => `
          <article class="etapa ${indice === 0 ? "ativa" : ""}" data-task-id="${utils.escaparHtml(task.task_id)}">
            <div class="etapa-topo">
              <div class="etapa-titulo">
                <span class="etapa-numero">${task.ordem_pipeline || indice + 1}</span>
                <div>
                  <h3>${utils.escaparHtml(task.nome_amigavel || task.nome || task.task_id || "-")}</h3>
                  <small>${utils.escaparHtml(task.tipo_etapa_ml || task.tipo || task.categoria_task || "-")}</small>
                </div>
              </div>
              <span class="badge-status ${utils.classeStatus(task.status).replace("ml-", "")}">${utils.escaparHtml(task.status || "unknown")}</span>
            </div>
            <small>${utils.escaparHtml(task.objetivo || task.descricao || "Sem objetivo documentado.")}</small>
          </article>
        `).join(""),
      );

      Array.from(document.querySelectorAll(".etapa[data-task-id]"))
        .forEach((elemento) => {
          elemento.addEventListener("click", () => selecionarTask(elemento.getAttribute("data-task-id")));
        });
    }

    function renderizarResumo(dashboard) {
      const pipeline = utils.objetoSeguro(dashboard.pipeline);
      const documentacao = utils.objetoSeguro(dashboard.documentacao || pipeline.documentacao);
      const modelo = utils.objetoSeguro(dashboard.modelo || pipeline.modelo);
      const metricasPrincipais = utils.objetoSeguro(modelo.metricas_principais);

      const textoDocumentacao = [
        documentacao.dag_descricao,
        documentacao.documentacao_dag,
        pipeline.objetivo_negocio,
        documentacao.explicacao_execucao,
      ].filter(Boolean).join("\n\n");

      const textoModelo = [
        `Nome do modelo: ${modelo.nome_modelo || modelo.nome || "-"}`,
        `Família: ${modelo.familia_modelo || "-"}`,
        `Versão: ${modelo.versao_modelo || "-"}`,
        `Variável alvo: ${modelo.variavel_alvo || pipeline.variavel_alvo || "-"}`,
        `Métricas principais encontradas: ${Object.keys(metricasPrincipais).length}`,
        `Artefatos relacionados: ${utils.arraySeguro(modelo.artefatos_relacionados).length}`,
      ].join("\n");

      utils.atribuirTexto(elementos.resumoDocumentacao, textoDocumentacao || "Sem documentação consolidada.");
      utils.atribuirTexto(elementos.resumoModelo, textoModelo || "Sem definição de modelo registrada.");
      utils.atribuirHtml(elementos.metricasResumo, montarMetricasCards(metricasPrincipais));
      utils.atribuirHtml(elementos.listaEntradas, montarListaObjetos(dashboard.entradas));
      utils.atribuirHtml(elementos.listaSaidas, montarListaObjetos(dashboard.saidas));
      utils.atribuirHtml(elementos.listaObjetosEntrada, montarListaObjetos(dashboard.entradas));
      utils.atribuirHtml(elementos.listaObjetosSaida, montarListaObjetos([
        ...utils.arraySeguro(dashboard.saidas),
        ...utils.arraySeguro(dashboard.artefatos_apoio),
      ]));
    }

    function selecionarTask(taskId) {
      const task = utils.arraySeguro(estado.tasks).find((item) => String(item.task_id) === String(taskId));
      if (!task) return;
      estado.taskSelecionada = task;

      Array.from(document.querySelectorAll(".etapa[data-task-id]"))
        .forEach((elemento) => {
          elemento.classList.toggle("ativa", elemento.getAttribute("data-task-id") === String(taskId));
        });

      utils.atribuirTexto(elementos.tituloTaskSelecionada, task.nome_amigavel || task.nome || task.task_id || "Task", "Task");
      utils.atribuirTexto(elementos.descricaoTaskSelecionada, task.descricao || task.objetivo || "Sem descrição registrada.");
      utils.atribuirHtml(elementos.metricasTask, montarMetricasCards(task.metricas_extras || task.metricas || {}));
      utils.atribuirTexto(elementos.sqlTask, utils.texto(task.sql || task.sql_preview || "") || "Sem SQL registrado.");
      utils.atribuirHtml(
        elementos.amostraTask,
        montarTabelaHtml(utils.arraySeguro(utils.objetoSeguro(task.tabela).colunas), utils.arraySeguro(utils.objetoSeguro(task.tabela).linhas)),
      );
    }

    function renderizarHealth(dashboard) {
      const health = utils.objetoSeguro(dashboard.health);
      const cards = [
        ["Success", health.success],
        ["Failed", health.failed],
        ["Running", health.running],
        ["Queued", health.queued],
        ["Total", health.total_tasks],
      ];

      const texto = [
        `Status geral da DAG: ${dashboard.status || "-"}`,
        `Tasks em sucesso: ${health.success ?? 0}`,
        `Tasks com falha: ${health.failed ?? 0}`,
        `Tasks executando: ${health.running ?? 0}`,
        `Tasks na fila: ${health.queued ?? 0}`,
      ].join("\n");

      utils.atribuirTexto(elementos.textoHealth, texto, "Sem dados de health disponíveis.");
      utils.atribuirHtml(
        elementos.cardsHealth,
        cards.map(([nome, valor]) => `
          <div class="health-card">
            <small>${utils.escaparHtml(nome)}</small>
            <strong>${utils.escaparHtml(String(valor ?? 0))}</strong>
            <span style="color:var(--muted); font-size:.85rem;">Contador operacional consolidado.</span>
          </div>
        `).join(""),
      );
    }

    function renderizarJson(dashboard) {
      utils.atribuirTexto(elementos.jsonCompleto, JSON.stringify(dashboard, null, 2), "{}");
    }

    function renderizarGraficos(dashboard) {
      if (!charts) return;
      const modelo = utils.objetoSeguro(dashboard.modelo || utils.objetoSeguro(dashboard.pipeline).modelo);
      const metricasPrincipais = Object.entries(utils.objetoSeguro(modelo.metricas_principais))
        .filter(([, valor]) => Number.isFinite(Number(valor)))
        .map(([chave, valor]) => ({ task: "Pipeline", metric_label: chave, valor: Number(valor) }));

      charts.renderizarBarrasMetricas(elementos.graficoMetricasPipeline, metricasPrincipais, "Métricas principais do pipeline");
      charts.renderizarStatusTasks(elementos.graficoStatusTasks, dashboard.tasks, "Distribuição de status das tasks");
    }

    function conectarTabs() {
      elementos.tabs.forEach((tab) => {
        tab.addEventListener("click", () => ativarAba(tab.dataset.aba));
      });
    }

    async function copiarJson() {
      if (!estado.dashboard) return;
      try {
        await utils.copiarTexto(JSON.stringify(estado.dashboard, null, 2));
        const textoOriginal = elementos.btnCopiarJson?.textContent || "Copiar JSON";
        if (elementos.btnCopiarJson) {
          elementos.btnCopiarJson.textContent = "JSON copiado";
          window.setTimeout(() => {
            elementos.btnCopiarJson.textContent = textoOriginal;
          }, 1600);
        }
      } catch (erro) {
        console.error(erro);
      }
    }

    async function carregarDashboard() {
      if (elementos.btnRecarregar) elementos.btnRecarregar.disabled = true;
      try {
        const dashboard = await utils.obterJson(rotaDashboard);
        estado.dashboard = dashboard;
        estado.tasks = utils.arraySeguro(dashboard.tasks);

        renderizarHero(dashboard);
        renderizarKpis(dashboard);
        renderizarListaEtapas(estado.tasks);
        renderizarResumo(dashboard);
        renderizarHealth(dashboard);
        renderizarJson(dashboard);
        renderizarGraficos(dashboard);

        if (estado.tasks.length) {
          selecionarTask(estado.tasks[0].task_id);
        }
      } catch (erro) {
        console.error(erro);
        const mensagem = `Falha ao carregar o dashboard da DAG ${dagId}. ${erro.message || "Erro desconhecido."}`;
        if (elementos.listaEtapas) {
          utils.atribuirHtml(elementos.listaEtapas, `<div class="estado-vazio">${utils.escaparHtml(mensagem)}</div>`);
        }
        utils.atribuirTexto(elementos.resumoDocumentacao, mensagem);
        utils.atribuirTexto(elementos.jsonCompleto, mensagem);
      } finally {
        if (elementos.btnRecarregar) elementos.btnRecarregar.disabled = false;
      }
    }

    conectarTabs();
    ativarAba("abaResumo");
    elementos.btnRecarregar?.addEventListener("click", carregarDashboard);
    elementos.btnCopiarJson?.addEventListener("click", copiarJson);
    carregarDashboard();
  }

  window.MLPipelineDashboardDetalhe = { iniciarDetalhePipeline };
})(window, document);