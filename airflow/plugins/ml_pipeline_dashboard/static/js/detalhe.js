(function (window, document) {
  "use strict";

  const utils = window.MLPipelineDashboardUtils || {};
  const charts = window.MLPipelineDashboardCharts;

  if (!window.MLPipelineDashboardUtils) {
    throw new Error("utils.js precisa ser carregado antes de detalhe.js");
  }

  function iniciarDetalhePipeline(configuracao = {}) {
    const estado = {
      dashboard: null,
      tasks: [],
      taskSelecionada: null,
      sequenciaGrafico: 0,
    };

    const dagId = configuracao.dagId || window.DAG_ID || "";
    const runId = configuracao.runId || window.RUN_ID || "";
    const frontend = configuracao.frontend || window.CONFIG || {};
    const prefixo = frontend.url_prefixo || "";

    const rotaDashboard =
      `${prefixo}/api/pipelines/${encodeURIComponent(dagId)}` +
      (runId ? `?run_id=${encodeURIComponent(runId)}` : "");

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

      listaFluxoTasks: document.getElementById("listaFluxoTasks"),
      listaEtapas: document.getElementById("listaEtapas"),

      resumoDocumentacao: document.getElementById("resumoDocumentacao"),
      tabelaModelo: document.getElementById("tabelaModelo"),
      metricasResumo: document.getElementById("metricasResumo"),

      listaEntradas: document.getElementById("listaEntradas"),
      listaSaidas: document.getElementById("listaSaidas"),
      listaObjetosEntrada: document.getElementById("listaObjetosEntrada"),
      listaObjetosSaida: document.getElementById("listaObjetosSaida"),

      tituloTaskSelecionada: document.getElementById("tituloTaskSelecionada"),
      descricaoTaskSelecionada: document.getElementById("descricaoTaskSelecionada"),
      tabelaInfoTask: document.getElementById("tabelaInfoTask"),
      metricasTask: document.getElementById("metricasTask"),
      sqlTask: document.getElementById("sqlTask"),
      amostraTask: document.getElementById("amostraTask"),

      graficoMetricasPipeline: document.getElementById("graficoMetricasPipeline"),
      graficoStatusTasks: document.getElementById("graficoStatusTasks"),

      dashboardMlDinamico: document.getElementById("dashboardMlDinamico"),

      textoHealth: document.getElementById("textoHealth"),
      cardsHealth: document.getElementById("cardsHealth"),
      jsonCompleto: document.getElementById("jsonCompleto"),
      textoMetricasResumo: document.getElementById("textoMetricasResumo"),

      tabs: Array.from(document.querySelectorAll(".tab")),
      abas: Array.from(document.querySelectorAll(".aba")),
    };

    function escaparHtml(valor) {
      if (utils.escaparHtml) return utils.escaparHtml(valor);
      return String(valor ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function arraySeguro(valor) {
      if (utils.arraySeguro) return utils.arraySeguro(valor);
      return Array.isArray(valor) ? valor : [];
    }

    function objetoSeguro(valor) {
      if (utils.objetoSeguro) return utils.objetoSeguro(valor);
      return valor && typeof valor === "object" && !Array.isArray(valor) ? valor : {};
    }

    function textoSeguro(valor) {
      if (utils.texto) return utils.texto(valor);
      return String(valor ?? "").trim();
    }

    function atribuirHtml(elemento, html) {
      if (!elemento) return;
      if (utils.atribuirHtml) {
        utils.atribuirHtml(elemento, html);
        return;
      }
      elemento.innerHTML = html;
    }

    function atribuirTexto(elemento, valor, fallback = "-") {
      if (!elemento) return;
      if (utils.atribuirTexto) {
        utils.atribuirTexto(elemento, valor, fallback);
        return;
      }
      const texto = valor === null || valor === undefined || valor === "" ? fallback : String(valor);
      elemento.textContent = texto;
    }

    function formatarNumero(valor, casas = 4) {
      if (utils.formatarNumero) return utils.formatarNumero(valor, casas);
      const numero = Number(valor);
      if (!Number.isFinite(numero)) return "-";
      return numero.toLocaleString("pt-BR", {
        minimumFractionDigits: casas,
        maximumFractionDigits: casas,
      });
    }

    function formatarInteiro(valor) {
      if (utils.formatarInteiro) return utils.formatarInteiro(valor);
      const numero = Number(valor);
      if (!Number.isFinite(numero)) return "-";
      return numero.toLocaleString("pt-BR", { maximumFractionDigits: 0 });
    }

    function normalizarStatus(status) {
      if (utils.normalizarStatus) return utils.normalizarStatus(status);
      return String(status || "").trim().toLowerCase();
    }

    function classeStatus(status) {
      if (utils.classeStatus) return utils.classeStatus(status);
      const texto = normalizarStatus(status);
      if (texto.includes("success")) return "ml-status-success";
      if (texto.includes("failed")) return "ml-status-failed";
      if (texto.includes("running")) return "ml-status-running";
      if (texto.includes("queued")) return "ml-status-queued";
      return "ml-status-default";
    }

    function abrirUrlComParametros(urlBase, parametros) {
      if (utils.abrirUrlComParametros) return utils.abrirUrlComParametros(urlBase, parametros);
      const params = new URLSearchParams();
      Object.entries(objetoSeguro(parametros)).forEach(([chave, valor]) => {
        if (valor !== undefined && valor !== null && valor !== "") params.set(chave, valor);
      });
      const query = params.toString();
      return `${urlBase}${query ? `?${query}` : ""}`;
    }

    function ativarAba(idAba) {
      elementos.tabs.forEach((tab) => {
        tab.classList.toggle("ativa", tab.dataset.aba === idAba);
      });

      elementos.abas.forEach((aba) => {
        aba.classList.toggle("ativa", aba.id === idAba);
      });
    }

    function chip(texto, classe = "") {
      return `<span class="chip ${classe}">${escaparHtml(texto)}</span>`;
    }

    function contarStatus(tasks) {
      const resumo = {
        success: 0,
        failed: 0,
        running: 0,
        queued: 0,
        outros: 0,
      };

      arraySeguro(tasks).forEach((task) => {
        const status = normalizarStatus(task?.status);

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
          return formatarNumero(valor, 4);
        }

        if (Number.isInteger(valor)) {
          return formatarInteiro(valor);
        }

        return formatarNumero(valor, 4);
      }

      return String(valor);
    }

    function formatarValorDashboard(valor, formato = "") {
      const numero = Number(valor);
      if (!Number.isFinite(numero)) return formatarValorMetrica(valor);

      if (formato === "percentual_2") return `${formatarNumero(numero * 100, 2)}%`;
      if (formato === "percentual_1") return `${formatarNumero(numero * 100, 1)}%`;
      if (formato === "decimal_4") return formatarNumero(numero, 4);
      if (formato === "decimal_3") return formatarNumero(numero, 3);
      if (formato === "inteiro") return formatarInteiro(numero);

      if (Math.abs(numero) <= 1 && numero !== Math.trunc(numero)) {
        return formatarNumero(numero, 4);
      }

      if (Number.isInteger(numero)) return formatarInteiro(numero);
      return formatarNumero(numero, 4);
    }

    function montarMetricasCards(metricas) {
      let entradas = [];

      if (Array.isArray(metricas)) {
        entradas = metricas.map((item) => ({
          chave: item.label || item.chave || item.metric_label || "Métrica",
          valor: item.valor,
        }));
      } else {
        entradas = Object.entries(objetoSeguro(metricas)).map(([chave, valor]) => ({
          chave,
          valor,
        }));
      }

      if (!entradas.length) {
        return '<div class="estado-vazio">Nenhuma métrica consolidada foi encontrada.</div>';
      }

      return entradas
        .map(
          (item) => `
            <div class="metrica">
              <small>${escaparHtml(item.chave)}</small>
              <strong>${escaparHtml(formatarValorMetrica(item.valor))}</strong>
            </div>
          `,
        )
        .join("");
    }

    function montarTabelaHtml(colunas, linhas) {
      const cols = arraySeguro(colunas);
      const rows = arraySeguro(linhas);

      if (!cols.length || !rows.length) {
        return '<div class="estado-vazio">Nenhuma amostra tabular disponível para esta task.</div>';
      }

      const head = cols.map((col) => `<th>${escaparHtml(col)}</th>`).join("");

      const body = rows
        .map((linha) => {
          const registro = objetoSeguro(linha);

          return `<tr>${cols
            .map((col) => `<td>${escaparHtml(registro[col] ?? "-")}</td>`)
            .join("")}</tr>`;
        })
        .join("");

      return `
        <div class="tabela-wrap">
          <table class="tabela-clara">
            <thead><tr>${head}</tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      `;
    }

    function montarTabelaInfo(linhas) {
      const itens = arraySeguro(linhas).filter((item) => item && item.valor !== undefined);

      if (!itens.length) {
        return '<div class="estado-vazio">Nenhuma informação consolidada disponível.</div>';
      }

      return `
        <div class="info-table-wrap">
          <table class="info-table">
            <thead>
              <tr>
                <th>Campo</th>
                <th>Valor</th>
              </tr>
            </thead>
            <tbody>
              ${itens
                .map(
                  (item) => `
                    <tr>
                      <td>${escaparHtml(item.campo)}</td>
                      <td>${escaparHtml(item.valor ?? "-")}</td>
                    </tr>
                  `,
                )
                .join("")}
            </tbody>
          </table>
        </div>
      `;
    }

    function montarTabelaModelo(modelo, pipeline) {
      const model = objetoSeguro(modelo);
      const pipe = objetoSeguro(pipeline);
      const metricasPrincipais = objetoSeguro(model.metricas_principais);

      const linhas = [
        { campo: "Nome do modelo", valor: model.nome_modelo || model.nome || "-" },
        { campo: "Família", valor: model.familia_modelo || "-" },
        { campo: "Versão", valor: model.versao_modelo || "-" },
        { campo: "Variável alvo", valor: model.variavel_alvo || pipe.variavel_alvo || "-" },
        { campo: "Tipo do pipeline", valor: pipe.tipo_pipeline || "machine_learning" },
        { campo: "Subtipo do pipeline", valor: pipe.subtipo_pipeline || "-" },
        { campo: "Domínio", valor: pipe.dominio || "-" },
        { campo: "Qtd. métricas principais", valor: Object.keys(metricasPrincipais).length || 0 },
        {
          campo: "Qtd. artefatos do modelo",
          valor: arraySeguro(model.artefatos_relacionados).length || 0,
        },
      ];

      return montarTabelaInfo(linhas);
    }

    function montarInfoTask(task) {
      const taskObj = objetoSeguro(task);
      const tabela = objetoSeguro(taskObj.tabela);

      const linhas = [
        { campo: "Task ID", valor: taskObj.task_id || "-" },
        { campo: "Nome amigável", valor: taskObj.nome_amigavel || taskObj.nome || "-" },
        { campo: "Status", valor: taskObj.status || "-" },
        { campo: "Tipo de etapa ML", valor: taskObj.tipo_etapa_ml || taskObj.tipo || taskObj.categoria_task || "-" },
        { campo: "Objetivo", valor: taskObj.objetivo || "-" },
        { campo: "Tentativas", valor: objetoSeguro(taskObj.metricas).tentativas ?? "-" },
        { campo: "Tempo de execução", valor: objetoSeguro(taskObj.metricas).tempo_execucao ?? "-" },
        { campo: "Qtd. colunas da amostra", valor: arraySeguro(tabela.colunas).length || 0 },
        { campo: "Qtd. linhas da amostra", valor: arraySeguro(tabela.linhas).length || 0 },
      ];

      return montarTabelaInfo(linhas);
    }

    function montarUrlTabela(objeto) {
      const item = objetoSeguro(objeto);
      if (!item.conn_id || !item.schema || !item.tabela) return "";
      return abrirUrlComParametros(`${prefixo}${frontend.rota_tabela_html || "/tabela"}`, {
        conexao_id: item.conn_id,
        banco: item.banco,
        schema: item.schema,
        tabela: item.tabela,
      });
    }

    function montarUrlDownload(objeto) {
      const item = objetoSeguro(objeto);
      const caminho = textoSeguro(item.caminho_arquivo || item.path || item.file_path);
      if (!caminho) return "";
      return abrirUrlComParametros(`${prefixo}${frontend.rota_download || "/arquivo/download"}`, { caminho });
    }

    function montarObjetoHtml(objeto) {
      const item = objetoSeguro(objeto);
      const nome = item.nome || item.nome_amigavel || item.tabela || item.caminho_arquivo || item.tipo || "Objeto";
      const urlTabela = montarUrlTabela(item);
      const urlDownload = montarUrlDownload(item);

      return `
        <article class="objeto">
          <div class="objeto-topo">
            <div>
              <h4>${escaparHtml(nome)}</h4>
              <div class="chips" style="margin-top:8px;">
                ${chip(item.tipo || "objeto")}
                ${chip(`direção: ${item.direcao || item.grupo || "apoio"}`)}
              </div>
            </div>
          </div>

          <div class="objeto-meta">
            <div><strong>Conn ID:</strong> ${escaparHtml(item.conn_id || "-")}</div>
            <div><strong>Banco:</strong> ${escaparHtml(item.banco || "-")}</div>
            <div><strong>Schema:</strong> ${escaparHtml(item.schema || "-")}</div>
            <div><strong>Tabela:</strong> ${escaparHtml(item.tabela || "-")}</div>
            <div><strong>Procedure:</strong> ${escaparHtml(item.procedure || "-")}</div>
            <div><strong>Caminho:</strong> ${escaparHtml(item.caminho_arquivo || item.path || "-")}</div>
          </div>

          <div class="objeto-acoes">
            ${urlTabela ? `<a class="btn-mini" href="${urlTabela}" target="_blank" rel="noopener">Abrir tabela</a>` : ""}
            ${urlDownload ? `<a class="btn-mini" href="${urlDownload}">Baixar arquivo</a>` : ""}
          </div>
        </article>
      `;
    }

    function montarListaObjetos(lista) {
      const itens = arraySeguro(lista);
      if (!itens.length) {
        return '<div class="estado-vazio">Nenhum objeto encontrado.</div>';
      }
      return itens.map(montarObjetoHtml).join("");
    }

    function renderizarHero(dashboard) {
      const pipeline = objetoSeguro(dashboard.pipeline);

      atribuirTexto(elementos.tituloDag, dashboard.nome || dashboard.dag_id || dagId, dagId || "-");
      atribuirTexto(
        elementos.subtituloDag,
        pipeline.objetivo_negocio ||
          dashboard.descricao_curta ||
          pipeline.documentacao_dag ||
          "Pipeline de Machine Learning com observabilidade técnica, lineage, health e métricas.",
      );

      const badges = [
        chip(`dag_id: ${dashboard.dag_id || dagId}`, "chip-primario"),
        chip(`run_id: ${dashboard.run_id || runId || "-"}`),
        chip(`status: ${dashboard.status || "unknown"}`),
        chip(`tipo: ${dashboard.tipo_pipeline || pipeline.tipo_pipeline || "-"}`),
        chip(`subtipo: ${dashboard.subtipo_pipeline || pipeline.subtipo_pipeline || "-"}`),
      ];

      arraySeguro(dashboard.tags).forEach((tag) => {
        badges.push(chip(`tag: ${tag}`));
      });

      atribuirHtml(elementos.heroBadges, badges.join(""));
    }

    function renderizarKpis(dashboard) {
      const tasks = arraySeguro(dashboard.tasks);
      const resumo = contarStatus(tasks);
      const modelo = objetoSeguro(dashboard.modelo || objetoSeguro(dashboard.pipeline).modelo);

      atribuirTexto(elementos.kpiStatus, dashboard.status || "-", "-");
      atribuirTexto(elementos.kpiTasks, tasks.length, "0");
      atribuirTexto(elementos.kpiSuccess, resumo.success, "0");
      atribuirTexto(elementos.kpiFalhas, resumo.failed, "0");
      atribuirTexto(elementos.kpiModelo, modelo.nome_modelo || modelo.nome || "-", "-");
      atribuirTexto(elementos.kpiDominio, dashboard.dominio || objetoSeguro(dashboard.pipeline).dominio || "-", "-");
    }

    function montarFluxoTasks(tasks) {
      const itens = arraySeguro(tasks);

      if (!itens.length) {
        return '<div class="estado-vazio">Nenhuma task encontrada nesta execução.</div>';
      }

      return `
        <div class="fluxo-linha">
          ${itens
            .map((task, indice) => {
              const classe = classeStatus(task.status).replace("ml-", "");
              const taskId = escaparHtml(task.task_id);
              const nome = escaparHtml(task.nome_amigavel || task.nome || task.task_id || "-");
              const tipo = escaparHtml(task.tipo_etapa_ml || task.tipo || task.categoria_task || "-");
              const objetivo = escaparHtml(task.objetivo || task.descricao || "Sem objetivo documentado.");

              return `
                <div class="fluxo-item-wrap">
                  <article class="fluxo-item ${indice === 0 ? "ativa" : ""}" data-task-id="${taskId}">
                    <div class="fluxo-topo">
                      <span class="fluxo-indice">${task.ordem_pipeline || indice + 1}</span>
                    </div>

                    <h3>${nome}</h3>
                    <small class="fluxo-tipo">${tipo}</small>

                    <div class="fluxo-badge-wrap">
                      <span class="badge-status ${classe}">
                        ${escaparHtml(task.status || "unknown")}
                      </span>
                    </div>

                    <p class="fluxo-objetivo">${objetivo}</p>
                  </article>

                  ${indice < itens.length - 1 ? '<div class="fluxo-seta">→</div>' : ""}
                </div>
              `;
            })
            .join("")}
        </div>
      `;
    }

    function conectarEventosFluxo() {
      const cards = Array.from(document.querySelectorAll(".fluxo-item[data-task-id], .etapa[data-task-id]"));
      cards.forEach((elemento) => {
        elemento.addEventListener("click", () => {
          selecionarTask(elemento.getAttribute("data-task-id"));
        });
      });
    }

    function renderizarListaEtapas(tasks) {
      const itens = arraySeguro(tasks);

      if (elementos.listaFluxoTasks) {
        atribuirHtml(elementos.listaFluxoTasks, montarFluxoTasks(itens));
      }

      if (elementos.listaEtapas) {
        if (!itens.length) {
          atribuirHtml(elementos.listaEtapas, '<div class="estado-vazio">Nenhuma task encontrada nesta execução.</div>');
        } else {
          atribuirHtml(
            elementos.listaEtapas,
            itens
              .map(
                (task, indice) => `
                  <article class="etapa ${indice === 0 ? "ativa" : ""}" data-task-id="${escaparHtml(task.task_id)}">
                    <div class="etapa-topo">
                      <div class="etapa-titulo">
                        <span class="etapa-numero">${task.ordem_pipeline || indice + 1}</span>
                        <div>
                          <h3>${escaparHtml(task.nome_amigavel || task.nome || task.task_id || "-")}</h3>
                          <small>${escaparHtml(task.tipo_etapa_ml || task.tipo || task.categoria_task || "-")}</small>
                        </div>
                      </div>
                      <span class="badge-status ${classeStatus(task.status).replace("ml-", "")}">
                        ${escaparHtml(task.status || "unknown")}
                      </span>
                    </div>
                    <small>${escaparHtml(task.objetivo || task.descricao || "Sem objetivo documentado.")}</small>
                  </article>
                `,
              )
              .join(""),
          );
        }
      }

      conectarEventosFluxo();
    }

    function renderizarResumo(dashboard) {
      const pipeline = objetoSeguro(dashboard.pipeline);
      const documentacao = objetoSeguro(dashboard.documentacao || pipeline.documentacao);
      const modelo = objetoSeguro(dashboard.modelo || pipeline.modelo);
      const metricasPrincipais = objetoSeguro(modelo.metricas_principais);

      const textoDocumentacao = [
        documentacao.dag_descricao,
        documentacao.documentacao_dag,
        pipeline.objetivo_negocio,
        documentacao.explicacao_execucao,
      ]
        .filter(Boolean)
        .join("\n\n");

      atribuirTexto(elementos.resumoDocumentacao, textoDocumentacao || "Sem documentação consolidada.");

      if (elementos.tabelaModelo) {
        atribuirHtml(elementos.tabelaModelo, montarTabelaModelo(modelo, pipeline));
      }

      atribuirHtml(elementos.metricasResumo, montarMetricasCards(metricasPrincipais));
      atribuirHtml(elementos.listaEntradas, montarListaObjetos(dashboard.entradas));
      atribuirHtml(elementos.listaSaidas, montarListaObjetos(dashboard.saidas));
      atribuirHtml(elementos.listaObjetosEntrada, montarListaObjetos(dashboard.entradas));
      atribuirHtml(
        elementos.listaObjetosSaida,
        montarListaObjetos([...arraySeguro(dashboard.saidas), ...arraySeguro(dashboard.artefatos_apoio)]),
      );

      if (elementos.textoMetricasResumo) {
        const textoMetricas = Object.entries(metricasPrincipais)
          .map(([chave, valor]) => `${chave}: ${formatarValorMetrica(valor)}`)
          .join("\n");

        elementos.textoMetricasResumo.textContent = textoMetricas || "Nenhuma métrica principal consolidada foi encontrada.";
      }
    }

    function selecionarTask(taskId) {
      const task = arraySeguro(estado.tasks).find((item) => String(item.task_id) === String(taskId));
      if (!task) return;

      estado.taskSelecionada = task;

      Array.from(document.querySelectorAll(".etapa[data-task-id], .fluxo-item[data-task-id]")).forEach((elemento) => {
        elemento.classList.toggle("ativa", elemento.getAttribute("data-task-id") === String(taskId));
      });

      atribuirTexto(elementos.tituloTaskSelecionada, task.nome_amigavel || task.nome || task.task_id || "Task", "Task");
      atribuirTexto(elementos.descricaoTaskSelecionada, task.descricao || task.objetivo || "Sem descrição registrada.");

      if (elementos.tabelaInfoTask) {
        atribuirHtml(elementos.tabelaInfoTask, montarInfoTask(task));
      }

      atribuirHtml(elementos.metricasTask, montarMetricasCards(task.metricas_extras || task.metricas || {}));
      atribuirTexto(elementos.sqlTask, textoSeguro(task.sql || task.sql_preview || "") || "Sem SQL registrado.");

      atribuirHtml(
        elementos.amostraTask,
        montarTabelaHtml(arraySeguro(objetoSeguro(task.tabela).colunas), arraySeguro(objetoSeguro(task.tabela).linhas)),
      );

      if (elementos.listaObjetosEntrada || elementos.listaObjetosSaida) {
        const objetos = arraySeguro(task.objetos);

        const entradas = objetos.filter((item) => textoSeguro(item?.direcao || item?.grupo).toLowerCase() === "entrada");
        const saidas = objetos.filter((item) => textoSeguro(item?.direcao || item?.grupo).toLowerCase() !== "entrada");

        if (elementos.listaObjetosEntrada) {
          atribuirHtml(elementos.listaObjetosEntrada, montarListaObjetos(entradas));
        }

        if (elementos.listaObjetosSaida) {
          atribuirHtml(elementos.listaObjetosSaida, montarListaObjetos(saidas));
        }
      }

      renderizarDashboardAnalitico(estado.dashboard, task);
    }

    function renderizarHealth(dashboard) {
      const health = objetoSeguro(dashboard.health);

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

      atribuirTexto(elementos.textoHealth, texto, "Sem dados de health disponíveis.");
      atribuirHtml(
        elementos.cardsHealth,
        cards
          .map(
            ([nome, valor]) => `
              <div class="health-card">
                <small>${escaparHtml(nome)}</small>
                <strong>${escaparHtml(String(valor ?? 0))}</strong>
                <span style="color:var(--muted); font-size:.85rem;">Contador operacional consolidado.</span>
              </div>
            `,
          )
          .join(""),
      );
    }

    function renderizarJson(dashboard) {
      atribuirTexto(elementos.jsonCompleto, JSON.stringify(dashboard, null, 2), "{}");
    }

    function renderizarGraficos(dashboard) {
      if (!charts) return;

      const modelo = objetoSeguro(dashboard.modelo || objetoSeguro(dashboard.pipeline).modelo);
      const metricasPrincipais = Object.entries(objetoSeguro(modelo.metricas_principais))
        .filter(([, valor]) => Number.isFinite(Number(valor)))
        .map(([chave, valor]) => ({
          task: "Pipeline",
          metric_label: chave,
          valor: Number(valor),
        }));

      charts.renderizarBarrasMetricas(elementos.graficoMetricasPipeline, metricasPrincipais, "Métricas principais do pipeline");
      charts.renderizarStatusTasks(elementos.graficoStatusTasks, dashboard.tasks, "Distribuição de status das tasks");
    }

    function conectarTabs() {
      elementos.tabs.forEach((tab) => {
        tab.addEventListener("click", () => ativarAba(tab.dataset.aba));
      });
    }

    function garantirEstilosDashboardAnalitico() {
      if (document.getElementById("ml-dashboard-analitico-estilos")) return;

      const estilo = document.createElement("style");
      estilo.id = "ml-dashboard-analitico-estilos";
      estilo.textContent = `
        .ml-dashboard-analitico{display:flex;flex-direction:column;gap:18px;}
        .ml-dashboard-cabecalho{border:1px solid rgba(75,123,236,.18);background:linear-gradient(180deg,#fff,#f8fbff);border-radius:22px;padding:18px 20px;box-shadow:0 10px 30px rgba(23,50,92,.05);}
        .ml-dashboard-cabecalho h2{margin:0 0 8px 0;font-size:1.22rem;color:#17325c;}
        .ml-dashboard-cabecalho p{margin:0;color:#4f6586;line-height:1.72;}
        .ml-dashboard-meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;}
        .ml-dashboard-secao{border:1px solid rgba(75,123,236,.14);background:#fff;border-radius:22px;overflow:hidden;box-shadow:0 10px 30px rgba(23,50,92,.04);}
        .ml-dashboard-secao-topo{padding:18px 20px 14px;border-bottom:1px solid rgba(75,123,236,.10);background:linear-gradient(180deg,#fbfdff,#f7faff);}
        .ml-dashboard-secao-topo h3{margin:0 0 6px 0;font-size:1.08rem;color:#17325c;}
        .ml-dashboard-secao-topo p{margin:0;color:#587096;line-height:1.7;}
        .ml-dashboard-secao-corpo{padding:18px 20px 20px;display:flex;flex-direction:column;gap:16px;}
        .ml-widget{border:1px solid rgba(75,123,236,.12);border-radius:18px;background:linear-gradient(180deg,#fff,#fafcff);padding:16px;}
        .ml-widget-topo h4{margin:0 0 6px 0;font-size:1rem;color:#17325c;}
        .ml-widget-topo p{margin:0;color:#587096;line-height:1.68;}
        .ml-widget-grafico{min-height:380px;}
        .ml-kpi-grid{display:grid;grid-template-columns:repeat(var(--ml-kpi-colunas,4),minmax(0,1fr));gap:12px;}
        .ml-kpi-card{border:1px solid rgba(75,123,236,.10);border-radius:18px;padding:14px;background:linear-gradient(180deg,#fefeff,#f6f9ff);}
        .ml-kpi-card small{display:block;color:#5f7597;font-size:.77rem;font-weight:800;letter-spacing:.03em;margin-bottom:8px;line-height:1.4;}
        .ml-kpi-card strong{display:block;color:#17325c;font-size:1.22rem;line-height:1.25;margin-bottom:8px;word-break:break-word;}
        .ml-kpi-card p{margin:0;color:#607592;font-size:.9rem;line-height:1.58;}
        .ml-texto-detalhado{display:flex;flex-direction:column;gap:12px;}
        .ml-texto-detalhado article{border:1px solid rgba(75,123,236,.10);border-radius:18px;padding:14px;background:#fff;}
        .ml-texto-detalhado h4{margin:0 0 8px 0;color:#17325c;font-size:.98rem;}
        .ml-texto-detalhado p{margin:0;color:#4f6586;line-height:1.74;white-space:pre-wrap;}
        .ml-tabela-widget-wrap{width:100%;overflow:auto;border:1px solid rgba(75,123,236,.10);border-radius:18px;background:#fff;}
        .ml-tabela-widget{width:100%;border-collapse:collapse;min-width:780px;}
        .ml-tabela-widget thead th{background:#f3f7ff;color:#17325c;padding:12px 10px;text-align:left;font-size:.82rem;font-weight:900;border-bottom:1px solid rgba(75,123,236,.12);white-space:nowrap;position:sticky;top:0;z-index:1;}
        .ml-tabela-widget tbody td{padding:10px;border-bottom:1px solid rgba(75,123,236,.08);font-size:.86rem;color:#334a69;white-space:nowrap;vertical-align:top;}
        .ml-tabela-widget tbody tr:hover{background:#f9fbff;}
        .ml-widget-rodape{margin-top:10px;color:#6b7f9b;font-size:.84rem;line-height:1.55;}
        @media (max-width: 1200px){.ml-kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr));}}
        @media (max-width: 760px){.ml-kpi-grid{grid-template-columns:1fr;}.ml-dashboard-cabecalho,.ml-dashboard-secao-topo,.ml-dashboard-secao-corpo,.ml-widget{padding:14px;}.ml-widget-grafico{min-height:300px;}}
      `;
      document.head.appendChild(estilo);
    }

    function encontrarPrimeiroComDashboard(valor, profundidade = 0, visitados = new Set()) {
      if (!valor || profundidade > 8) return null;
      if (typeof valor !== "object") return null;
      if (visitados.has(valor)) return null;
      visitados.add(valor);

      const obj = objetoSeguro(valor);

      if (obj.dashboard_spec && arraySeguro(obj.dashboard_spec.secoes).length) {
        return {
          dashboardSpec: obj.dashboard_spec,
          payloadMetricas: obj.payload_metricas || obj,
          origem: obj,
        };
      }

      if (obj.payload_metricas && objetoSeguro(obj.payload_metricas).dashboard_spec) {
        const spec = objetoSeguro(obj.payload_metricas).dashboard_spec;
        if (arraySeguro(spec.secoes).length) {
          return {
            dashboardSpec: spec,
            payloadMetricas: obj.payload_metricas,
            origem: obj,
          };
        }
      }

      const candidatosPrioritarios = [
        obj.metricas_extras,
        obj.resumo,
        objetoSeguro(obj.resumo).metricas_extras,
        obj.pipeline,
        obj.modelo,
        objetoSeguro(obj.pipeline).modelo,
        obj.payload_metricas,
      ];

      for (const candidato of candidatosPrioritarios) {
        const achado = encontrarPrimeiroComDashboard(candidato, profundidade + 1, visitados);
        if (achado) return achado;
      }

      for (const chave of Object.keys(obj)) {
        const achado = encontrarPrimeiroComDashboard(obj[chave], profundidade + 1, visitados);
        if (achado) return achado;
      }

      return null;
    }

    function obterContextoDashboardAnalitico(dashboard, taskSelecionada = null) {
      const candidatos = [
        taskSelecionada,
        objetoSeguro(taskSelecionada).metricas_extras,
        dashboard,
        objetoSeguro(dashboard).metricas_extras,
        objetoSeguro(dashboard).resumo,
        objetoSeguro(objetoSeguro(dashboard).resumo).metricas_extras,
        objetoSeguro(dashboard).modelo,
        objetoSeguro(dashboard).pipeline,
        objetoSeguro(objetoSeguro(dashboard).pipeline).modelo,
        ...arraySeguro(objetoSeguro(dashboard).tasks),
      ];

      for (const candidato of candidatos) {
        const achado = encontrarPrimeiroComDashboard(candidato);
        if (achado) return achado;
      }

      return null;
    }

    function criarIdGrafico() {
      estado.sequenciaGrafico += 1;
      return `ml-dashboard-grafico-${estado.sequenciaGrafico}`;
    }

    function montarTabelaWidget(widget) {
      const colunas = arraySeguro(widget.colunas);
      const linhas = arraySeguro(widget.linhas);

      if (!colunas.length || !linhas.length) {
        return '<div class="estado-vazio">Nenhum dado tabular disponível para este bloco.</div>';
      }

      const head = colunas.map((coluna) => `<th>${escaparHtml(coluna)}</th>`).join("");
      const body = linhas
        .map((linha) => {
          const registro = objetoSeguro(linha);
          return `<tr>${colunas.map((coluna) => `<td>${escaparHtml(registro[coluna] ?? "-")}</td>`).join("")}</tr>`;
        })
        .join("");

      return `
        <div class="ml-tabela-widget-wrap">
          <table class="ml-tabela-widget">
            <thead><tr>${head}</tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      `;
    }

    function montarGrupoKpis(widget) {
      const itens = arraySeguro(widget.itens);
      if (!itens.length) {
        return '<div class="estado-vazio">Nenhum KPI publicado para este bloco.</div>';
      }

      const colunas = Number(widget.colunas) > 0 ? Number(widget.colunas) : Math.min(4, itens.length || 1);

      return `
        <div class="ml-kpi-grid" style="--ml-kpi-colunas:${colunas};">
          ${itens
            .map((item) => {
              const titulo = item.titulo || item.nome || item.label || "KPI";
              const valor = formatarValorDashboard(item.valor, item.formato || "");
              const descricao = item.descricao_curta || item.descricao || item.explicacao || "";
              return `
                <article class="ml-kpi-card">
                  <small>${escaparHtml(titulo)}</small>
                  <strong>${escaparHtml(valor)}</strong>
                  ${descricao ? `<p>${escaparHtml(descricao)}</p>` : ""}
                </article>
              `;
            })
            .join("")}
        </div>
      `;
    }

    function montarTextoDetalhado(widget) {
      const itens = arraySeguro(widget.itens);
      if (!itens.length) {
        return '<div class="estado-vazio">Nenhum texto detalhado foi publicado para este bloco.</div>';
      }

      return `
        <div class="ml-texto-detalhado">
          ${itens
            .map((item) => {
              const titulo = item.titulo || item.nome || "Explicação";
              const conteudo = item.conteudo || item.texto || item.descricao || "";
              return `
                <article>
                  <h4>${escaparHtml(titulo)}</h4>
                  <p>${escaparHtml(conteudo)}</p>
                </article>
              `;
            })
            .join("")}
        </div>
      `;
    }

    function montarWidgetBase(widget, conteudoInterno, idGrafico = "") {
      const titulo = widget.titulo ? `<h4>${escaparHtml(widget.titulo)}</h4>` : "";
      const descricao = widget.descricao ? `<p>${escaparHtml(widget.descricao)}</p>` : "";
      const rodape = widget.rodape ? `<div class="ml-widget-rodape">${escaparHtml(widget.rodape)}</div>` : "";
      return `
        <article class="ml-widget" ${idGrafico ? `data-grafico-id="${idGrafico}"` : ""}>
          ${(titulo || descricao) ? `<div class="ml-widget-topo">${titulo}${descricao}</div>` : ""}
          <div class="ml-widget-corpo">${conteudoInterno}</div>
          ${rodape}
        </article>
      `;
    }

    function montarWidget(widget) {
      const tipo = textoSeguro(widget.tipo).toLowerCase();

      if (tipo === "grupo_kpis") {
        return montarWidgetBase(widget, montarGrupoKpis(widget));
      }

      if (tipo === "texto_detalhado") {
        return montarWidgetBase(widget, montarTextoDetalhado(widget));
      }

      if (tipo === "tabela") {
        return montarWidgetBase(widget, montarTabelaWidget(widget));
      }

      if (tipo === "grafico_plotly") {
        const idGrafico = criarIdGrafico();
        return montarWidgetBase(widget, `<div class="ml-widget-grafico" id="${idGrafico}"></div>`, idGrafico);
      }

      return montarWidgetBase(
        widget,
        `<div class="estado-vazio">Tipo de widget não suportado pelo front-end: ${escaparHtml(widget.tipo || "desconhecido")}</div>`,
      );
    }

    function renderizarDashboardAnalitico(dashboard, taskSelecionada = null) {
      if (!elementos.dashboardMlDinamico) return;

      garantirEstilosDashboardAnalitico();
      estado.sequenciaGrafico = 0;

      const contexto = obterContextoDashboardAnalitico(dashboard, taskSelecionada);
      if (!contexto || !arraySeguro(objetoSeguro(contexto.dashboardSpec).secoes).length) {
        atribuirHtml(
          elementos.dashboardMlDinamico,
          '<div class="estado-vazio">Nenhum dashboard analítico específico foi publicado por esta DAG.</div>',
        );
        return;
      }

      const dashboardSpec = objetoSeguro(contexto.dashboardSpec);
      const payloadMetricas = objetoSeguro(contexto.payloadMetricas);
      const secoes = arraySeguro(dashboardSpec.secoes);
      const titulo =
        dashboardSpec.titulo ||
        payloadMetricas.titulo_dashboard ||
        payloadMetricas.nome_modelo ||
        "Dashboard analítico do modelo";
      const subtitulo =
        dashboardSpec.subtitulo ||
        payloadMetricas.subtitulo_dashboard ||
        payloadMetricas.familia_modelo ||
        "Painel analítico publicado pelo pipeline para inspeção completa das métricas, gráficos e comportamento do score.";
      const origemTask = objetoSeguro(taskSelecionada).task_id || objetoSeguro(contexto.origem).task_id || "-";
      const versao = payloadMetricas.versao_dashboard || dashboardSpec.versao || payloadMetricas.versao_modelo || "-";
      const quantidadeSecoes = secoes.length;

      const html = `
        <div class="ml-dashboard-analitico">
          <article class="ml-dashboard-cabecalho">
            <h2>${escaparHtml(titulo)}</h2>
            <p>${escaparHtml(subtitulo)}</p>
            <div class="ml-dashboard-meta">
              ${chip(`task origem: ${origemTask}`, "chip-primario")}
              ${chip(`seções: ${quantidadeSecoes}`)}
              ${chip(`versão: ${versao}`)}
              ${payloadMetricas.variavel_alvo ? chip(`alvo: ${payloadMetricas.variavel_alvo}`) : ""}
            </div>
          </article>

          ${secoes
            .map((secao) => {
              const widgets = arraySeguro(secao.widgets)
                .map((widget) => {
                  const htmlWidget = montarWidget(widget);
                  if (textoSeguro(widget.tipo).toLowerCase() === "grafico_plotly") {
                    const idGrafico = /id="([^"]+)"/.exec(htmlWidget)?.[1] || "";
                    return htmlWidget.replace(
                      '<article class="ml-widget"',
                      `<article class="ml-widget" data-widget-json='${escaparHtml(
                        JSON.stringify(widget),
                      )}' data-grafico-id="${escaparHtml(idGrafico)}"`,
                    );
                  }
                  return htmlWidget;
                })
                .join("");

              return `
                <section class="ml-dashboard-secao" data-secao-id="${escaparHtml(secao.id || "secao")}">
                  <div class="ml-dashboard-secao-topo">
                    <h3>${escaparHtml(secao.titulo || "Seção analítica")}</h3>
                    ${secao.descricao ? `<p>${escaparHtml(secao.descricao)}</p>` : ""}
                  </div>
                  <div class="ml-dashboard-secao-corpo">
                    ${widgets || '<div class="estado-vazio">Esta seção não trouxe widgets publicados.</div>'}
                  </div>
                </section>
              `;
            })
            .join("")}
        </div>
      `;

      atribuirHtml(elementos.dashboardMlDinamico, html);

      elementos.dashboardMlDinamico.querySelectorAll(".ml-widget[data-grafico-id]").forEach((card) => {
        const idGrafico = card.getAttribute("data-grafico-id");
        const grafico = document.getElementById(idGrafico);
        if (!grafico || !charts || typeof charts.renderizarGraficoPlotly !== "function") return;

        try {
          const widget = JSON.parse(card.getAttribute("data-widget-json") || "{}");
          charts.renderizarGraficoPlotly(grafico, widget);
        } catch (erro) {
          console.error("Falha ao renderizar widget analítico", erro);
          grafico.innerHTML = `<div class="estado-vazio">Falha ao renderizar gráfico: ${escaparHtml(
            erro.message || "erro desconhecido",
          )}</div>`;
        }
      });
    }

    async function copiarJson() {
      if (!estado.dashboard) return;

      try {
        if (utils.copiarTexto) {
          await utils.copiarTexto(JSON.stringify(estado.dashboard, null, 2));
        } else {
          await navigator.clipboard.writeText(JSON.stringify(estado.dashboard, null, 2));
        }

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
      if (elementos.btnRecarregar) {
        elementos.btnRecarregar.disabled = true;
      }

      try {
        const dashboard = await utils.obterJson(rotaDashboard);

        estado.dashboard = dashboard;
        estado.tasks = arraySeguro(dashboard.tasks);

        renderizarHero(dashboard);
        renderizarKpis(dashboard);
        renderizarListaEtapas(estado.tasks);
        renderizarResumo(dashboard);
        renderizarHealth(dashboard);
        renderizarJson(dashboard);
        renderizarGraficos(dashboard);
        renderizarDashboardAnalitico(dashboard);

        if (estado.tasks.length) {
          selecionarTask(estado.tasks[0].task_id);
        }
      } catch (erro) {
        console.error(erro);

        const mensagem = `Falha ao carregar o dashboard da DAG ${dagId}. ${erro.message || "Erro desconhecido."}`;

        if (elementos.listaFluxoTasks) {
          atribuirHtml(elementos.listaFluxoTasks, `<div class="estado-vazio">${escaparHtml(mensagem)}</div>`);
        }

        if (elementos.listaEtapas) {
          atribuirHtml(elementos.listaEtapas, `<div class="estado-vazio">${escaparHtml(mensagem)}</div>`);
        }

        atribuirTexto(elementos.resumoDocumentacao, mensagem);
        atribuirTexto(elementos.jsonCompleto, mensagem);
        atribuirHtml(elementos.dashboardMlDinamico, `<div class="estado-vazio">${escaparHtml(mensagem)}</div>`);
      } finally {
        if (elementos.btnRecarregar) {
          elementos.btnRecarregar.disabled = false;
        }
      }
    }

    conectarTabs();
    ativarAba("abaResumo");
    elementos.btnRecarregar?.addEventListener("click", carregarDashboard);
    elementos.btnCopiarJson?.addEventListener("click", copiarJson);
    carregarDashboard();
  }

  window.MLPipelineDashboardDetalhe = {
    iniciarDetalhePipeline,
  };
})(window, document);