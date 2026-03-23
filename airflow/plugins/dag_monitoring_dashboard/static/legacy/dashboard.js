const estadoDashboard = {
  dag: null,
  taskSelecionada: null,
};

function obterConfig() {
  const config = window.DAG_MONITORING_CONFIG || {};

  return {
    dagId: String(config.dagIdTemplate || "").trim(),
    runId: String(config.runIdTemplate || "").trim(),
    apiBase: String(config.apiBase || "./api").trim(),
    detalheBase: String(config.detalheBase || "./detalhe").trim(),
    listaBase: String(config.listaBase || "./lista").trim(),
    tablePreviewBase: String(config.tablePreviewBase || "./api/table-preview").trim(),
    tableDataBase: String(config.tableDataBase || "./api/table-data").trim(),
    tableViewBase: String(config.tableViewBase || "./tabela").trim(),
    fileDownloadBase: String(config.fileDownloadBase || "./arquivo/download").trim(),
  };
}

function montarUrl(base, parametros) {
  const url = new URL(base, window.location.href);

  Object.entries(parametros || {}).forEach(([chave, valor]) => {
    if (valor === undefined || valor === null || valor === "") {
      return;
    }
    url.searchParams.set(chave, String(valor));
  });

  return url.toString();
}

function escaparHtml(valor) {
  return String(valor ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function humanizarStatus(status) {
  const texto = String(status || "").toLowerCase();

  if (!texto) return "Sem status";
  if (texto === "success" || texto === "sucesso") return "Sucesso";
  if (texto === "failed" || texto === "falha") return "Falha";
  if (texto === "upstream_failed") return "Falha upstream";
  if (texto === "running") return "Executando";
  if (texto === "queued") return "Na fila";
  if (texto === "scheduled") return "Agendada";
  if (texto === "skipped") return "Ignorada";

  return status;
}

function classeStatus(status) {
  const texto = String(status || "").toLowerCase();

  if (texto.includes("success") || texto.includes("sucesso")) return "sucesso";
  if (texto.includes("running") || texto.includes("executando")) return "executando";
  if (texto.includes("queued") || texto.includes("scheduled")) return "fila";
  if (texto.includes("failed") || texto.includes("falha")) return "falha";
  if (texto.includes("skipped")) return "ignorada";

  return "neutro";
}

function mostrarErro(mensagem) {
  const titulo = document.getElementById("modal-info-titulo");
  const conteudo = document.getElementById("modal-info-conteudo");
  const modal = document.getElementById("modal-info");

  if (titulo) titulo.textContent = "Erro";
  if (conteudo) conteudo.innerHTML = `<div style="font-weight:900;color:#991b1b;">${escaparHtml(mensagem)}</div>`;
  if (modal) modal.classList.add("aberto");
}

function abrirModalInfo(tituloTexto, html) {
  const titulo = document.getElementById("modal-info-titulo");
  const conteudo = document.getElementById("modal-info-conteudo");
  const modal = document.getElementById("modal-info");

  if (titulo) titulo.textContent = tituloTexto;
  if (conteudo) conteudo.innerHTML = html;
  if (modal) modal.classList.add("aberto");
}

function fecharModalInfo() {
  const modal = document.getElementById("modal-info");
  if (modal) modal.classList.remove("aberto");
}

function abrirModalTabela(tituloTexto, html) {
  const titulo = document.getElementById("modal-tabela-titulo");
  const conteudo = document.getElementById("modal-tabela-conteudo");
  const modal = document.getElementById("modal-tabela");

  if (titulo) titulo.textContent = tituloTexto;
  if (conteudo) conteudo.innerHTML = html;
  if (modal) modal.classList.add("aberto");
}

function fecharModalTabela() {
  const modal = document.getElementById("modal-tabela");
  if (modal) modal.classList.remove("aberto");
}

function preencherResumoDag(dag) {
  document.getElementById("dag-id").textContent = dag.dag_id || "-";
  document.getElementById("dag-status").textContent = humanizarStatus(dag.status);
  document.getElementById("dag-ultima").textContent = dag.ultima_execucao || "-";
  document.getElementById("dag-proxima").textContent = dag.proxima_execucao || "-";
  document.getElementById("dag-descricao-curta").textContent = dag.descricao_curta || dag.dag_descricao || "-";
  document.getElementById("descricao-detalhe").textContent = dag.descricao || dag.dag_descricao || "-";
  document.getElementById("agendamento").textContent = dag.agendamento || "-";
  document.getElementById("inicio").textContent = dag.inicio || "-";
  document.getElementById("execucao-detalhe").textContent = dag.ultima_execucao || "-";
  document.getElementById("owner-dag").textContent = dag.owner || "-";

  const tagsBox = document.getElementById("tags-dag");
  if (tagsBox) {
    const tags = Array.isArray(dag.tags) ? dag.tags : [];
    if (tags.length === 0) {
      tagsBox.innerHTML = `<span class="tag">Sem tags</span>`;
    } else {
      tagsBox.innerHTML = tags
        .map(tag => `<span class="tag">${escaparHtml(tag)}</span>`)
        .join("");
    }
  }

  const health = dag.health || {};
  document.getElementById("health-dag").textContent = humanizarStatus(health.dag || dag.status);
  document.getElementById("health-tasks").textContent = health.tasks_saudaveis || "-";
  document.getElementById("health-broker").textContent = health.broker || "-";
  document.getElementById("health-fila").textContent = health.fila || "-";
  document.getElementById("health-executando").textContent = health.executando || "-";
  document.getElementById("health-workers").textContent = health.workers_online || "-";
}

function renderizarPipeline(tasks) {
  const container = document.getElementById("pipeline");
  if (!container) return;

  container.innerHTML = "";

  tasks.forEach((task, indice) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `pipeline-card ${classeStatus(task.status)}`;
    card.dataset.taskId = task.task_id || task.id || "";

    card.innerHTML = `
      <div class="pipeline-card-titulo">${escaparHtml(task.nome || task.task_id || "Task")}</div>
      <div class="pipeline-card-subtitulo">${escaparHtml(task.subtitulo || task.tipo || "-")}</div>
      <div class="pipeline-card-status ${classeStatus(task.status)}">${escaparHtml(humanizarStatus(task.status))}</div>
    `;

    card.addEventListener("click", () => selecionarTask(card.dataset.taskId));
    container.appendChild(card);

    if (indice < tasks.length - 1) {
      const seta = document.createElement("div");
      seta.className = "pipeline-seta";
      seta.innerHTML = "→";
      container.appendChild(seta);
    }
  });
}

function renderizarTabelaAmostra(task) {
  const badgeFonte = document.getElementById("fonte-dados");
  const tabela = document.getElementById("tabela-amostra");

  if (!badgeFonte || !tabela) return;

  const tabelaDados = task.tabela || task.table || task.table_preview || {};
  const fonte = tabelaDados.fonte || task.fonte_dados || "Não registrada";
  const colunas = Array.isArray(tabelaDados.colunas) ? tabelaDados.colunas : [];
  const linhas = Array.isArray(tabelaDados.linhas) ? tabelaDados.linhas : [];

  badgeFonte.textContent = `Fonte: ${fonte || "Não registrada"}`;

  if (colunas.length === 0 || linhas.length === 0) {
    tabela.innerHTML = `
      <thead>
        <tr><th>Amostra</th></tr>
      </thead>
      <tbody>
        <tr><td>Nenhuma amostra real foi registrada por esta task.</td></tr>
      </tbody>
    `;
    return;
  }

  const thead = `
    <thead>
      <tr>
        ${colunas.map(coluna => `<th>${escaparHtml(coluna)}</th>`).join("")}
      </tr>
    </thead>
  `;

  const tbody = `
    <tbody>
      ${linhas.map(linha => {
        const valores = Array.isArray(linha) ? linha : [linha];
        return `
          <tr>
            ${valores.map(valor => `<td>${escaparHtml(valor)}</td>`).join("")}
          </tr>
        `;
      }).join("")}
    </tbody>
  `;

  tabela.innerHTML = thead + tbody;
}

function renderizarSql(task) {
  const codigo = document.getElementById("codigo-sql");
  if (!codigo) return;

  codigo.textContent = task.sql || task.sql_preview || "-";
}

function renderizarMetricas(task) {
  const metricas = task.metricas || {};

  document.getElementById("metricas-linhas").textContent = metricas.linhas_processadas || "-";
  document.getElementById("metricas-tempo").textContent = metricas.tempo_execucao || "-";
  document.getElementById("metricas-tentativas").textContent = metricas.tentativas || "-";
  document.getElementById("metricas-status").textContent = humanizarStatus(metricas.ultimo_status || task.status);
}

function renderizarResumoTask(task) {
  document.getElementById("task-titulo").textContent = task.nome || task.task_id || "Task";
  document.getElementById("task-subtitulo").textContent = task.subtitulo || task.tipo || "-";
  document.getElementById("resumo-etapa").textContent = task.etapa || "-";
  document.getElementById("resumo-objetivo").textContent = task.objetivo || "-";
  document.getElementById("resumo-tipo").textContent = task.tipo || task.operator || "-";
  document.getElementById("resumo-operacao").textContent = task.operacao || "-";
  document.getElementById("task-resumo").textContent = task.descricao || "-";

  renderizarMetricas(task);
  renderizarTabelaAmostra(task);
  renderizarSql(task);
  renderizarObjetos(task);
}

function montarHtmlPreviewTabela(preview) {
  const colunas = Array.isArray(preview.colunas) ? preview.colunas : [];
  const linhas = Array.isArray(preview.linhas) ? preview.linhas : [];

  let html = `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <span class="badge-objeto">Conexão: ${escaparHtml(preview.conn_id || "-")}</span>
        <span class="badge-objeto">Objeto: ${escaparHtml(preview.nome_qualificado || `${preview.schema}.${preview.tabela}`)}</span>
        <span class="badge-objeto">Total linhas: ${escaparHtml(preview.total_linhas)}</span>
      </div>
    </div>
  `;

  if (colunas.length === 0 || linhas.length === 0) {
    html += `<div style="font-weight:900;color:#64748b;">Nenhuma linha encontrada para o preview.</div>`;
    return html;
  }

  html += `
    <div class="tabela-wrapper">
      <table class="preview-table">
        <thead>
          <tr>
            ${colunas.map(coluna => `<th>${escaparHtml(coluna)}</th>`).join("")}
          </tr>
        </thead>
        <tbody>
          ${linhas.map(linha => `
            <tr>
              ${colunas.map(coluna => `<td>${escaparHtml(linha[coluna])}</td>`).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
    <div style="margin-top:14px;display:flex;justify-content:flex-end;">
      <button id="botao-abrir-tabela-completa" class="botao-primario" type="button">Abrir tabela completa</button>
    </div>
  `;

  return html;
}

async function abrirPreviewTabela(objeto) {
  const config = obterConfig();
  const url = montarUrl(config.tablePreviewBase, {
    conexao_id: objeto.conn_id,
    banco: objeto.banco,
    schema: objeto.schema,
    tabela: objeto.tabela,
    limite: 20,
  });

  const resposta = await fetch(url, { headers: { "Accept": "application/json" } });
  if (!resposta.ok) {
    const texto = await resposta.text();
    throw new Error(texto || "Não consegui carregar preview da tabela.");
  }

  const preview = await resposta.json();
  abrirModalTabela(preview.nome_qualificado || objeto.nome || "Tabela", montarHtmlPreviewTabela(preview));

  const botaoAbrir = document.getElementById("botao-abrir-tabela-completa");
  if (botaoAbrir) {
    botaoAbrir.addEventListener("click", function(){
      abrirTabelaCompleta(objeto);
    });
  }
}

function abrirTabelaCompleta(objeto) {
  const config = obterConfig();

  const url = montarUrl(config.tableViewBase, {
    conexao_id: objeto.conn_id,
    banco: objeto.banco,
    schema: objeto.schema,
    tabela: objeto.tabela,
    pagina: 1,
    tamanho_pagina: 50,
  });

  window.open(url, "_blank");
}

function baixarArquivo(objeto) {
  const config = obterConfig();
  const url = montarUrl(config.fileDownloadBase, {
    caminho: objeto.caminho_arquivo,
  });

  window.open(url, "_blank");
}

function montarDescricaoObjeto(objeto) {
  const partes = [];

  if (objeto.descricao) {
    partes.push(`<div class="objeto-descricao">${escaparHtml(objeto.descricao)}</div>`);
  } else {
    partes.push(`<div class="objeto-descricao">-</div>`);
  }

  if (objeto.direcao) {
    partes.push(`<div class="objeto-meta"><strong>Direção:</strong> ${escaparHtml(objeto.direcao)}</div>`);
  }

  if (objeto.conn_id) {
    partes.push(`<div class="objeto-meta"><strong>Conexão:</strong> ${escaparHtml(objeto.conn_id)}</div>`);
  }

  if (objeto.schema && objeto.tabela) {
    partes.push(`<div class="objeto-meta"><strong>Referência:</strong> ${escaparHtml(`${objeto.schema}.${objeto.tabela}`)}</div>`);
  }

  if (objeto.caminho_arquivo) {
    partes.push(`<div class="objeto-meta"><strong>Arquivo:</strong> ${escaparHtml(objeto.caminho_arquivo)}</div>`);
  }

  return partes.join("");
}

function renderizarObjetos(task) {
  const container = document.getElementById("objetos-task");
  if (!container) return;

  const objetos = Array.isArray(task.objetos) ? task.objetos : [];
  container.innerHTML = "";

  if (objetos.length === 0) {
    container.innerHTML = `
      <div class="objeto-card objeto-card-vazio">
        <div class="objeto-descricao">Nenhum objeto real registrado para esta task.</div>
      </div>
    `;
    return;
  }

  objetos.forEach((objeto) => {
    const tipo = String(objeto.tipo || "Objeto").trim();
    const isArquivo = Boolean(objeto.downloadable && objeto.caminho_arquivo);
    const isTabela = Boolean(objeto.visualizavel && objeto.schema && objeto.tabela && objeto.conn_id);

    const card = document.createElement("div");
    card.className = "objeto-card";
    card.tabIndex = 0;

    if (isArquivo) {
      card.classList.add("objeto-card-download");
      card.title = "Clique para baixar o arquivo";
    } else if (isTabela) {
      card.classList.add("objeto-card-tabela");
      card.title = "Clique para preview e dê duplo clique para abrir a tabela completa";
    }

    card.innerHTML = `
      <span class="badge-objeto">${escaparHtml(tipo)}</span>
      <h4>${escaparHtml(objeto.nome || "-")}</h4>
      ${montarDescricaoObjeto(objeto)}
    `;

    if (isArquivo) {
      card.addEventListener("click", function(){
        baixarArquivo(objeto);
      });
    } else if (isTabela) {
      card.addEventListener("click", async function(){
        try {
          await abrirPreviewTabela(objeto);
        } catch (erro) {
          mostrarErro(erro.message || "Erro ao abrir preview da tabela.");
        }
      });

      card.addEventListener("dblclick", function(){
        abrirTabelaCompleta(objeto);
      });

      card.addEventListener("keydown", function(ev){
        if (ev.key === "Enter") {
          abrirTabelaCompleta(objeto);
        }
      });
    } else {
      card.addEventListener("click", function(){
        abrirModalInfo(
          objeto.nome || "Objeto",
          `
            <div style="display:grid;gap:10px;">
              <div><strong>Tipo:</strong> ${escaparHtml(tipo)}</div>
              <div><strong>Nome:</strong> ${escaparHtml(objeto.nome || "-")}</div>
              <div><strong>Descrição:</strong> ${escaparHtml(objeto.descricao || "-")}</div>
            </div>
          `
        );
      });
    }

    container.appendChild(card);
  });
}

function selecionarTask(taskId) {
  const tasks = Array.isArray(estadoDashboard.dag?.tasks) ? estadoDashboard.dag.tasks : [];
  const task = tasks.find(item => (item.task_id || item.id) === taskId);

  if (!task) {
    return;
  }

  estadoDashboard.taskSelecionada = task;

  document.querySelectorAll(".pipeline-card").forEach(card => {
    card.classList.toggle("ativo", card.dataset.taskId === taskId);
  });

  renderizarResumoTask(task);
}

async function carregarDashboard() {
  const config = obterConfig();

  if (!config.dagId) {
    mostrarErro("dag_id não foi informado no template.");
    return;
  }

  const url = montarUrl(config.apiBase, {
    dag_id: config.dagId,
    run_id: config.runId,
  });

  const resposta = await fetch(url, { headers: { "Accept": "application/json" } });

  if (!resposta.ok) {
    const texto = await resposta.text();
    throw new Error(texto || "Erro ao carregar dados do dashboard.");
  }

  const dag = await resposta.json();
  estadoDashboard.dag = dag;

  preencherResumoDag(dag);
  renderizarPipeline(Array.isArray(dag.tasks) ? dag.tasks : []);

  const primeiraTask = Array.isArray(dag.tasks) && dag.tasks.length > 0 ? dag.tasks[0] : null;
  if (primeiraTask) {
    selecionarTask(primeiraTask.task_id || primeiraTask.id);
  }
}

function configurarTabs() {
  const tabs = document.querySelectorAll(".tab");
  const conteudos = document.querySelectorAll(".tab-content");

  tabs.forEach(tab => {
    tab.addEventListener("click", function(){
      const alvo = tab.getAttribute("data-tab");

      tabs.forEach(item => item.classList.remove("ativa"));
      conteudos.forEach(item => item.classList.remove("ativa"));

      tab.classList.add("ativa");
      const conteudo = document.getElementById(`tab-${alvo}`);
      if (conteudo) {
        conteudo.classList.add("ativa");
      }
    });
  });
}

function configurarDocumentacao() {
  const linkGuia = document.getElementById("link-guia");
  const linkUpsert = document.getElementById("link-upsert");

  if (linkGuia) {
    linkGuia.addEventListener("click", function(ev){
      ev.preventDefault();

      const task = estadoDashboard.taskSelecionada;
      const itens = Array.isArray(task?.guia_transformacoes) ? task.guia_transformacoes : [];

      if (itens.length === 0) {
        abrirModalInfo("Guia das Transformações", "<div style='font-weight:900;color:#64748b;'>Nenhum guia registrado para esta task.</div>");
        return;
      }

      abrirModalInfo(
        "Guia das Transformações",
        `
          <div style="display:grid;gap:12px;">
            ${itens.map(item => `
              <div style="padding:12px;border:1px solid rgba(15,23,42,.08);border-radius:12px;background:#fff;">
                <div style="font-weight:1000;color:#1e3a8a;margin-bottom:6px;">${escaparHtml(item.titulo || "Transformação")}</div>
                <div style="font-weight:800;color:#334155;">${escaparHtml(item.descricao || "-")}</div>
              </div>
            `).join("")}
          </div>
        `
      );
    });
  }

  if (linkUpsert) {
    linkUpsert.addEventListener("click", function(ev){
      ev.preventDefault();

      const task = estadoDashboard.taskSelecionada;
      const itens = Array.isArray(task?.regras_upsert) ? task.regras_upsert : [];

      if (itens.length === 0) {
        abrirModalInfo("Regras de Upsert", "<div style='font-weight:900;color:#64748b;'>Nenhuma regra de upsert registrada para esta task.</div>");
        return;
      }

      abrirModalInfo(
        "Regras de Upsert",
        `
          <div style="display:grid;gap:12px;">
            ${itens.map(item => `
              <div style="padding:12px;border:1px solid rgba(15,23,42,.08);border-radius:12px;background:#fff;">
                <div style="font-weight:1000;color:#1e3a8a;margin-bottom:6px;">${escaparHtml(item.titulo || "Regra")}</div>
                <div style="font-weight:800;color:#334155;">${escaparHtml(item.descricao || "-")}</div>
              </div>
            `).join("")}
          </div>
        `
      );
    });
  }
}

function configurarModais() {
  const botaoFecharInfo = document.getElementById("modal-info-fechar");
  const botaoFecharTabela = document.getElementById("modal-tabela-fechar");
  const modalInfo = document.getElementById("modal-info");
  const modalTabela = document.getElementById("modal-tabela");

  if (botaoFecharInfo) {
    botaoFecharInfo.addEventListener("click", fecharModalInfo);
  }

  if (botaoFecharTabela) {
    botaoFecharTabela.addEventListener("click", fecharModalTabela);
  }

  if (modalInfo) {
    modalInfo.addEventListener("click", function(ev){
      if (ev.target === modalInfo) {
        fecharModalInfo();
      }
    });
  }

  if (modalTabela) {
    modalTabela.addEventListener("click", function(ev){
      if (ev.target === modalTabela) {
        fecharModalTabela();
      }
    });
  }

  document.addEventListener("keydown", function(ev){
    if (ev.key === "Escape") {
      fecharModalInfo();
      fecharModalTabela();
    }
  });
}

document.addEventListener("DOMContentLoaded", async function(){
  try {
    configurarTabs();
    configurarDocumentacao();
    configurarModais();
    await carregarDashboard();
  } catch (erro) {
    mostrarErro(erro.message || "Erro inesperado ao carregar o dashboard.");
  }
});