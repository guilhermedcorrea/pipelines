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

      textoHealth: document.getElementById("textoHealth"),
      cardsHealth: document.getElementById("cardsHealth"),
      jsonCompleto: document.getElementById("jsonCompleto"),
      textoMetricasResumo: document.getElementById("textoMetricasResumo"),

      dashboardMlDinamico: document.getElementById("dashboardMlDinamico"),

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
      const resumo = {
        success: 0,
        failed: 0,
        running: 0,
        queued: 0,
        outros: 0,
      };

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

    function formatarValorMetrica(valor, formato = "auto") {
      return utils.formatarValorPorFormato(valor, formato);
    }

    function montarMetricasCards(metricas) {
      let entradas = [];

      if (Array.isArray(metricas)) {
        entradas = metricas.map((item) => ({
          chave: item.label || item.chave || item.metric_label || "Métrica",
          valor: item.valor,
          formato: item.formato || "auto",
        }));
      } else {
        entradas = Object.entries(utils.objetoSeguro(metricas)).map(([chave, valor]) => ({
          chave,
          valor,
          formato: "auto",
        }));
      }

      if (!entradas.length) {
        return '<div class="estado-vazio">Nenhuma métrica consolidada foi encontrada.</div>';
      }

      return entradas
        .map(
          (item) => `
            <div class="metrica">
              <small>${utils.escaparHtml(item.chave)}</small>
              <strong>${utils.escaparHtml(formatarValorMetrica(item.valor, item.formato))}</strong>
            </div>
          `,
        )
        .join("");
    }

    function montarTabelaHtml(colunas, linhas) {
      const cols = utils.arraySeguro(colunas);
      const rows = utils.arraySeguro(linhas);

      if (!cols.length || !rows.length) {
        return '<div class="estado-vazio">Nenhuma amostra tabular disponível.</div>';
      }

      const head = cols.map((col) => `<th>${utils.escaparHtml(col)}</th>`).join("");
      const body = rows
        .map((linha) => {
          const registro = utils.objetoSeguro(linha);
          return `<tr>${cols
            .map((col) => `<td>${utils.escaparHtml(registro[col] ?? "-")}</td>`)
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
      const itens = utils.arraySeguro(linhas).filter((item) => item && item.valor !== undefined);

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
                      <td>${utils.escaparHtml(item.campo)}</td>
                      <td>${utils.escaparHtml(item.valor ?? "-")}</td>
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
      const model = utils.objetoSeguro(modelo);
      const pipe = utils.objetoSeguro(pipeline);
      const metricasPrincipais = utils.objetoSeguro(model.metricas_principais);

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
          valor: utils.arraySeguro(model.artefatos_relacionados).length || 0,
        },
      ];

      return montarTabelaInfo(linhas);
    }

    function montarInfoTask(task) {
      const taskObj = utils.objetoSeguro(task);
      const tabela = utils.objetoSeguro(taskObj.tabela);

      const linhas = [
        { campo: "Task ID", valor: taskObj.task_id || "-" },
        { campo: "Nome amigável", valor: taskObj.nome_amigavel || taskObj.nome || "-" },
        { campo: "Status", valor: taskObj.status || "-" },
        { campo: "Tipo de etapa ML", valor: taskObj.tipo_etapa_ml || taskObj.tipo || taskObj.categoria_task || "-" },
        { campo: "Objetivo", valor: taskObj.objetivo || "-" },
        { campo: "Tentativas", valor: utils.objetoSeguro(taskObj.metricas).tentativas ?? "-" },
        { campo: "Tempo de execução", valor: utils.objetoSeguro(taskObj.metricas).tempo_execucao ?? "-" },
        { campo: "Qtd. colunas da amostra", valor: utils.arraySeguro(tabela.colunas).length || 0 },
        { campo: "Qtd. linhas da amostra", valor: utils.arraySeguro(tabela.linhas).length || 0 },
      ];

      return montarTabelaInfo(linhas);
    }

    function montarUrlTabela(objeto) {
      const item = utils.objetoSeguro(objeto);

      if (!item.conn_id || !item.schema || !item.tabela) return "";

      return utils.abrirUrlComParametros(
        `${prefixo}${frontend.rota_tabela_html || "/tabela"}`,
        {
          conexao_id: item.conn_id,
          banco: item.banco,
          schema: item.schema,
          tabela: item.tabela,
        },
      );
    }

    function montarUrlDownload(objeto) {
      const item = utils.objetoSeguro(objeto);
      const caminho = utils.texto(item.caminho_arquivo || item.path || item.file_path);

      if (!caminho) return "";

      return utils.abrirUrlComParametros(
        `${prefixo}${frontend.rota_download || "/arquivo/download"}`,
        { caminho },
      );
    }

    function montarObjetoHtml(objeto) {
      const item = utils.objetoSeguro(objeto);
      const nome =
        item.nome ||
        item.nome_amigavel ||
        item.tabela ||
        item.caminho_arquivo ||
        item.tipo ||
        "Objeto";

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
            ${
              urlTabela
                ? `<a class="btn-mini" href="${urlTabela}" target="_blank" rel="noopener">Abrir tabela</a>`
                : ""
            }
            ${
              urlDownload
                ? `<a class="btn-mini" href="${urlDownload}">Baixar arquivo</a>`
                : ""
            }
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

    function obterModelo(dashboard) {
      return utils.objetoSeguro(
        dashboard.modelo || utils.objetoSeguro(dashboard.pipeline).modelo,
      );
    }

    function obterDashboardSpec(dashboard) {
      const modelo = obterModelo(dashboard);
      return (
        utils.objetoSeguro(dashboard.dashboard_spec) ||
        utils.objetoSeguro(modelo.dashboard_spec) ||
        utils.objetoSeguro(utils.objetoSeguro(dashboard.pipeline).dashboard_spec)
      );
    }

    function renderizarHero(dashboard) {
      const pipeline = utils.objetoSeguro(dashboard.pipeline);

      utils.atribuirTexto(
        elementos.tituloDag,
        dashboard.nome || dashboard.dag_id || dagId,
        dagId || "-",
      );

      utils.atribuirTexto(
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

      utils.arraySeguro(dashboard.tags).forEach((tag) => {
        badges.push(chip(`tag: ${tag}`));
      });

      utils.atribuirHtml(elementos.heroBadges, badges.join(""));
    }

    function renderizarKpis(dashboard) {
      const tasks = utils.arraySeguro(dashboard.tasks);
      const resumo = contarStatus(tasks);
      const modelo = obterModelo(dashboard);

      utils.atribuirTexto(elementos.kpiStatus, dashboard.status || "-", "-");
      utils.atribuirTexto(elementos.kpiTasks, tasks.length, "0");
      utils.atribuirTexto(elementos.kpiSuccess, resumo.success, "0");
      utils.atribuirTexto(elementos.kpiFalhas, resumo.failed, "0");
      utils.atribuirTexto(elementos.kpiModelo, modelo.nome_modelo || modelo.nome || "-", "-");
      utils.atribuirTexto(
        elementos.kpiDominio,
        dashboard.dominio || utils.objetoSeguro(dashboard.pipeline).dominio || "-",
        "-",
      );
    }

    function montarFluxoTasks(tasks) {
      const itens = utils.arraySeguro(tasks);

      if (!itens.length) {
        return '<div class="estado-vazio">Nenhuma task encontrada nesta execução.</div>';
      }

      return `
        <div class="fluxo-linha">
          ${itens
            .map((task, indice) => {
              const classeStatus = utils.classeStatus(task.status).replace("ml-", "");
              const taskId = utils.escaparHtml(task.task_id);
              const nome = utils.escaparHtml(task.nome_amigavel || task.nome || task.task_id || "-");
              const tipo = utils.escaparHtml(task.tipo_etapa_ml || task.tipo || task.categoria_task || "-");
              const objetivo = utils.escaparHtml(task.objetivo || task.descricao || "Sem objetivo documentado.");

              return `
                <div class="fluxo-item-wrap">
                  <article class="fluxo-item ${indice === 0 ? "ativa" : ""}" data-task-id="${taskId}">
                    <div class="fluxo-topo">
                      <span class="fluxo-indice">${task.ordem_pipeline || indice + 1}</span>
                    </div>

                    <h3>${nome}</h3>
                    <small class="fluxo-tipo">${tipo}</small>

                    <div class="fluxo-badge-wrap">
                      <span class="badge-status ${classeStatus}">
                        ${utils.escaparHtml(task.status || "unknown")}
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
      const cards = Array.from(
        document.querySelectorAll(".fluxo-item[data-task-id], .etapa[data-task-id]"),
      );

      cards.forEach((elemento) => {
        elemento.addEventListener("click", () => {
          selecionarTask(elemento.getAttribute("data-task-id"));
        });
      });
    }

    function renderizarListaEtapas(tasks) {
      const itens = utils.arraySeguro(tasks);

      if (elementos.listaFluxoTasks) {
        utils.atribuirHtml(elementos.listaFluxoTasks, montarFluxoTasks(itens));
      }

      if (elementos.listaEtapas) {
        if (!itens.length) {
          utils.atribuirHtml(
            elementos.listaEtapas,
            '<div class="estado-vazio">Nenhuma task encontrada nesta execução.</div>',
          );
        } else {
          utils.atribuirHtml(
            elementos.listaEtapas,
            itens
              .map(
                (task, indice) => `
                  <article class="etapa ${indice === 0 ? "ativa" : ""}" data-task-id="${utils.escaparHtml(task.task_id)}">
                    <div class="etapa-topo">
                      <div class="etapa-titulo">
                        <span class="etapa-numero">${task.ordem_pipeline || indice + 1}</span>
                        <div>
                          <h3>${utils.escaparHtml(task.nome_amigavel || task.nome || task.task_id || "-")}</h3>
                          <small>${utils.escaparHtml(task.tipo_etapa_ml || task.tipo || task.categoria_task || "-")}</small>
                        </div>
                      </div>
                      <span class="badge-status ${utils.classeStatus(task.status).replace("ml-", "")}">
                        ${utils.escaparHtml(task.status || "unknown")}
                      </span>
                    </div>
                    <small>${utils.escaparHtml(task.objetivo || task.descricao || "Sem objetivo documentado.")}</small>
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
      const pipeline = utils.objetoSeguro(dashboard.pipeline);
      const documentacao = utils.objetoSeguro(dashboard.documentacao || pipeline.documentacao);
      const modelo = obterModelo(dashboard);
      const metricasPrincipais = utils.objetoSeguro(modelo.metricas_principais);

      const textoDocumentacao = [
        documentacao.dag_descricao,
        documentacao.documentacao_dag,
        pipeline.objetivo_negocio,
        documentacao.explicacao_execucao,
      ]
        .filter(Boolean)
        .join("\n\n");

      utils.atribuirTexto(
        elementos.resumoDocumentacao,
        textoDocumentacao || "Sem documentação consolidada.",
      );

      if (elementos.tabelaModelo) {
        utils.atribuirHtml(elementos.tabelaModelo, montarTabelaModelo(modelo, pipeline));
      }

      utils.atribuirHtml(elementos.metricasResumo, montarMetricasCards(metricasPrincipais));
      utils.atribuirHtml(elementos.listaEntradas, montarListaObjetos(dashboard.entradas));
      utils.atribuirHtml(elementos.listaSaidas, montarListaObjetos(dashboard.saidas));
      utils.atribuirHtml(elementos.listaObjetosEntrada, montarListaObjetos(dashboard.entradas));
      utils.atribuirHtml(
        elementos.listaObjetosSaida,
        montarListaObjetos([
          ...utils.arraySeguro(dashboard.saidas),
          ...utils.arraySeguro(dashboard.artefatos_apoio),
        ]),
      );

      if (elementos.textoMetricasResumo) {
        const textoMetricas = Object.entries(metricasPrincipais)
          .map(([chave, valor]) => `${chave}: ${formatarValorMetrica(valor)}`)
          .join("\n");

        elementos.textoMetricasResumo.textContent =
          textoMetricas || "Nenhuma métrica principal consolidada foi encontrada.";
      }
    }

    function selecionarTask(taskId) {
      const task = utils
        .arraySeguro(estado.tasks)
        .find((item) => String(item.task_id) === String(taskId));

      if (!task) return;

      estado.taskSelecionada = task;

      Array.from(
        document.querySelectorAll(".etapa[data-task-id], .fluxo-item[data-task-id]"),
      ).forEach((elemento) => {
        elemento.classList.toggle(
          "ativa",
          elemento.getAttribute("data-task-id") === String(taskId),
        );
      });

      utils.atribuirTexto(
        elementos.tituloTaskSelecionada,
        task.nome_amigavel || task.nome || task.task_id || "Task",
        "Task",
      );

      utils.atribuirTexto(
        elementos.descricaoTaskSelecionada,
        task.descricao || task.objetivo || "Sem descrição registrada.",
      );

      if (elementos.tabelaInfoTask) {
        utils.atribuirHtml(elementos.tabelaInfoTask, montarInfoTask(task));
      }

      utils.atribuirHtml(
        elementos.metricasTask,
        montarMetricasCards(task.metricas_extras || task.metricas || {}),
      );

      utils.atribuirTexto(
        elementos.sqlTask,
        utils.texto(task.sql || task.sql_preview || "") || "Sem SQL registrado.",
      );

      utils.atribuirHtml(
        elementos.amostraTask,
        montarTabelaHtml(
          utils.arraySeguro(utils.objetoSeguro(task.tabela).colunas),
          utils.arraySeguro(utils.objetoSeguro(task.tabela).linhas),
        ),
      );

      if (elementos.listaObjetosEntrada || elementos.listaObjetosSaida) {
        const objetos = utils.arraySeguro(task.objetos);

        const entradas = objetos.filter((item) => {
          const direcao = utils.texto(item?.direcao || item?.grupo).toLowerCase();
          return direcao === "entrada";
        });

        const saidas = objetos.filter((item) => {
          const direcao = utils.texto(item?.direcao || item?.grupo).toLowerCase();
          return direcao !== "entrada";
        });

        if (elementos.listaObjetosEntrada) {
          utils.atribuirHtml(elementos.listaObjetosEntrada, montarListaObjetos(entradas));
        }

        if (elementos.listaObjetosSaida) {
          utils.atribuirHtml(elementos.listaObjetosSaida, montarListaObjetos(saidas));
        }
      }
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

      utils.atribuirTexto(
        elementos.textoHealth,
        texto,
        "Sem dados de health disponíveis.",
      );

      utils.atribuirHtml(
        elementos.cardsHealth,
        cards
          .map(
            ([nome, valor]) => `
              <div class="health-card">
                <small>${utils.escaparHtml(nome)}</small>
                <strong>${utils.escaparHtml(String(valor ?? 0))}</strong>
                <span style="color:var(--muted); font-size:.85rem;">Contador operacional consolidado.</span>
              </div>
            `,
          )
          .join(""),
      );
    }

    function renderizarJson(dashboard) {
      utils.atribuirTexto(
        elementos.jsonCompleto,
        JSON.stringify(dashboard, null, 2),
        "{}",
      );
    }

    function renderizarGraficosGenericos(dashboard) {
      if (!charts) return;

      const modelo = obterModelo(dashboard);

      const metricasPrincipais = Object.entries(utils.objetoSeguro(modelo.metricas_principais))
        .filter(([, valor]) => Number.isFinite(Number(valor)))
        .map(([chave, valor]) => ({
          task: "Pipeline",
          metric_label: chave,
          valor: Number(valor),
        }));

      charts.renderizarBarrasMetricas(
        elementos.graficoMetricasPipeline,
        metricasPrincipais,
        "Métricas principais do pipeline",
      );

      charts.renderizarStatusTasks(
        elementos.graficoStatusTasks,
        dashboard.tasks,
        "Distribuição de status das tasks",
      );
    }

    function montarWidgetKpis(widget) {
      const itens = utils.arraySeguro(widget.itens);

      if (!itens.length) {
        return '<div class="estado-vazio">Nenhum KPI configurado para esta seção.</div>';
      }

      const quantidadeColunas = Number(widget.colunas) || 4;

      return `
        <div class="dashboard-kpis-grid" style="grid-template-columns:repeat(${quantidadeColunas}, minmax(0, 1fr));">
          ${itens
            .map((item) => {
              const valorFormatado = formatarValorMetrica(item.valor, item.formato || "auto");
              const descricaoCurta = utils.texto(item.descricao_curta || item.descricao);
              const detalhe = utils.texto(item.explicacao_detalhada || "");

              return `
                <article class="dashboard-kpi-card">
                  <small>${utils.escaparHtml(item.titulo || "Métrica")}</small>
                  <strong>${utils.escaparHtml(valorFormatado)}</strong>
                  <p>${utils.escaparHtml(descricaoCurta || "Sem descrição disponível.")}</p>
                  ${
                    detalhe
                      ? `
                        <details class="dashboard-detalhe-metrica">
                          <summary>Ver explicação detalhada</summary>
                          <div>${utils.escaparHtml(detalhe)}</div>
                        </details>
                      `
                      : ""
                  }
                </article>
              `;
            })
            .join("")}
        </div>
      `;
    }

    function montarWidgetTextoDetalhado(widget) {
      const itens = utils.arraySeguro(widget.itens);

      if (!itens.length) {
        return '<div class="estado-vazio">Nenhum texto detalhado disponível.</div>';
      }

      return `
        <div class="dashboard-textos-grid">
          ${itens
            .map(
              (item) => `
                <article class="dashboard-texto-card">
                  <h4>${utils.escaparHtml(item.titulo || "Explicação")}</h4>
                  <p>${utils.escaparHtml(item.conteudo || "")}</p>
                </article>
              `,
            )
            .join("")}
        </div>
      `;
    }

    function montarWidgetTabela(widget) {
      return montarTabelaHtml(widget.colunas, widget.linhas);
    }

    function renderizarWidget(widget) {
      const idWidget = utils.gerarIdUnico("widget");
      const tipo = utils.texto(widget?.tipo);
      let corpoInterno = "";

      if (tipo === "grupo_kpis") {
        corpoInterno = montarWidgetKpis(widget);
      } else if (tipo === "texto_detalhado") {
        corpoInterno = montarWidgetTextoDetalhado(widget);
      } else if (tipo === "tabela") {
        corpoInterno = montarWidgetTabela(widget);
      } else if (tipo === "grafico_plotly") {
        corpoInterno = `<div class="grafico grafico-analitico" id="${idWidget}"></div>`;
      } else {
        corpoInterno = `<div class="estado-vazio">Tipo de widget não suportado: ${utils.escaparHtml(tipo)}</div>`;
      }

      return `
        <article class="dashboard-widget" data-widget-id="${utils.escaparHtml(idWidget)}">
          ${
            widget.titulo
              ? `<div class="dashboard-widget-topo"><h4>${utils.escaparHtml(widget.titulo)}</h4>${
                  widget.descricao ? `<p>${utils.escaparHtml(widget.descricao)}</p>` : ""
                }</div>`
              : ""
          }
          <div class="dashboard-widget-body">
            ${corpoInterno}
          </div>
        </article>
      `;
    }

    function renderizarDashboardAnalitico(dashboard) {
      if (!elementos.dashboardMlDinamico) return;

      const spec = obterDashboardSpec(dashboard);
      const secoes = utils.arraySeguro(spec.secoes);

      if (!secoes.length) {
        utils.atribuirHtml(
          elementos.dashboardMlDinamico,
          '<div class="estado-vazio">Nenhum dashboard analítico específico foi publicado por esta DAG.</div>',
        );
        return;
      }

      const widgetsParaRenderizar = [];

      const html = `
        <div class="dashboard-ml">
          <header class="dashboard-ml-topo">
            <h3>${utils.escaparHtml(spec.titulo || "Dashboard Analítico")}</h3>
            <p>${utils.escaparHtml(spec.subtitulo || "")}</p>
          </header>

          ${secoes
            .map((secao) => {
              const widgetsHtml = utils.arraySeguro(secao.widgets)
                .map((widget) => {
                  const widgetId = utils.gerarIdUnico("widget");
                  const tipo = utils.texto(widget?.tipo);
                  let corpoInterno = "";

                  if (tipo === "grupo_kpis") {
                    corpoInterno = montarWidgetKpis(widget);
                  } else if (tipo === "texto_detalhado") {
                    corpoInterno = montarWidgetTextoDetalhado(widget);
                  } else if (tipo === "tabela") {
                    corpoInterno = montarWidgetTabela(widget);
                  } else if (tipo === "grafico_plotly") {
                    corpoInterno = `<div class="grafico grafico-analitico" id="${widgetId}"></div>`;
                    widgetsParaRenderizar.push({ widgetId, widget });
                  } else {
                    corpoInterno = `<div class="estado-vazio">Tipo de widget não suportado: ${utils.escaparHtml(tipo)}</div>`;
                  }

                  return `
                    <article class="dashboard-widget" data-widget-id="${utils.escaparHtml(widgetId)}">
                      ${
                        widget.titulo
                          ? `<div class="dashboard-widget-topo"><h4>${utils.escaparHtml(widget.titulo)}</h4>${
                              widget.descricao ? `<p>${utils.escaparHtml(widget.descricao)}</p>` : ""
                            }</div>`
                          : ""
                      }
                      <div class="dashboard-widget-body">
                        ${corpoInterno}
                      </div>
                    </article>
                  `;
                })
                .join("");

              return `
                <section class="dashboard-secao" id="secao-${utils.slugify(secao.id || secao.titulo || "secao")}">
                  <div class="dashboard-secao-topo">
                    <h4>${utils.escaparHtml(secao.titulo || "Seção")}</h4>
                    ${
                      secao.descricao
                        ? `<p>${utils.escaparHtml(secao.descricao)}</p>`
                        : ""
                    }
                  </div>
                  <div class="dashboard-secao-conteudo">
                    ${widgetsHtml}
                  </div>
                </section>
              `;
            })
            .join("")}
        </div>
      `;

      utils.atribuirHtml(elementos.dashboardMlDinamico, html);

      widgetsParaRenderizar.forEach(({ widgetId, widget }) => {
        const container = document.getElementById(widgetId);
        if (!container) return;
        charts.renderizarGraficoWidget(container, widget);
      });
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
      if (elementos.btnRecarregar) {
        elementos.btnRecarregar.disabled = true;
      }

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
        renderizarGraficosGenericos(dashboard);
        renderizarDashboardAnalitico(dashboard);

        if (estado.tasks.length) {
          selecionarTask(estado.tasks[0].task_id);
        }
      } catch (erro) {
        console.error(erro);

        const mensagem = `Falha ao carregar o dashboard da DAG ${dagId}. ${erro.message || "Erro desconhecido."}`;

        [
          elementos.listaFluxoTasks,
          elementos.listaEtapas,
          elementos.dashboardMlDinamico,
          elementos.metricasResumo,
        ].forEach((elemento) => {
          if (!elemento) return;
          utils.atribuirHtml(
            elemento,
            `<div class="estado-vazio">${utils.escaparHtml(mensagem)}</div>`,
          );
        });
      } finally {
        if (elementos.btnRecarregar) {
          elementos.btnRecarregar.disabled = false;
        }
      }
    }

    function conectarAcoes() {
      if (elementos.btnRecarregar) {
        elementos.btnRecarregar.addEventListener("click", carregarDashboard);
      }

      if (elementos.btnCopiarJson) {
        elementos.btnCopiarJson.addEventListener("click", copiarJson);
      }
    }

    conectarTabs();
    conectarAcoes();
    ativarAba("abaResumo");
    carregarDashboard();
  }

  window.MLPipelineDashboardDetalhe = {
    iniciarDetalhePipeline,
  };
})(window, document);