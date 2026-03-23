(() => {
    "use strict";

    const selecionar = (seletor, raiz = document) => raiz.querySelector(seletor);
    const selecionarTodos = (seletor, raiz = document) => Array.from(raiz.querySelectorAll(seletor));

    const normalizarTexto = (valor) =>
        String(valor || "")
            .trim()
            .toLowerCase()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "");

    const classificarScore = (score) => {
        const numero = Number(score);
        if (Number.isNaN(numero)) {
            return "";
        }
        if (numero >= 80) {
            return "score-bom";
        }
        if (numero >= 60) {
            return "score-medio";
        }
        return "score-ruim";
    };

    const classificarStatus = (status) => {
        const valor = normalizarTexto(status);

        if (valor.includes("healthy")) {
            return "badge badge-healthy";
        }
        if (valor.includes("degraded")) {
            return "badge badge-degraded";
        }
        if (valor.includes("critical")) {
            return "badge badge-critical";
        }
        if (valor.includes("paused")) {
            return "badge badge-paused";
        }
        if (valor.includes("warning")) {
            return "badge badge-warning";
        }
        if (valor.includes("ok")) {
            return "badge badge-ok";
        }
        if (valor.includes("down")) {
            return "badge badge-down";
        }
        if (valor.includes("unstable")) {
            return "badge badge-unstable";
        }

        return "badge";
    };

    const aplicarBadges = () => {
        selecionarTodos("[data-health-status]").forEach((elemento) => {
            const valor = elemento.getAttribute("data-health-status") || elemento.textContent || "";
            elemento.className = classificarStatus(valor);
        });

        selecionarTodos("[data-dependency-status]").forEach((elemento) => {
            const valor = elemento.getAttribute("data-dependency-status") || elemento.textContent || "";
            elemento.className = classificarStatus(valor);
        });

        selecionarTodos("[data-data-quality-status]").forEach((elemento) => {
            const valor = elemento.getAttribute("data-data-quality-status") || elemento.textContent || "";
            elemento.className = classificarStatus(valor);
        });
    };

    const aplicarCoresScore = () => {
        selecionarTodos("[data-health-score]").forEach((elemento) => {
            const valor = elemento.getAttribute("data-health-score") || elemento.textContent || "";
            elemento.classList.add("valor-score");
            const classe = classificarScore(valor);
            if (classe) {
                elemento.classList.add(classe);
            }
        });
    };

    const formatarTabela = () => {
        const tabela = selecionar("table");
        if (!tabela) {
            return;
        }

        tabela.classList.add("tabela-health");

        const linhasCabecalho = selecionarTodos("thead th", tabela);
        linhasCabecalho.forEach((th) => {
            const titulo = normalizarTexto(th.textContent);

            if (titulo.includes("dag")) {
                th.classList.add("coluna-dag");
            }
        });

        const linhasCorpo = selecionarTodos("tbody tr", tabela);

        linhasCorpo.forEach((tr) => {
            const colunas = selecionarTodos("td", tr);
            if (colunas.length < 8) {
                return;
            }

            const colunaDag = colunas[0];
            const colunaIdDag = colunas[1];
            const colunaScore = colunas[2];
            const colunaStatus = colunas[3];
            const colunaLastRun = colunas[4];
            const colunaDuration = colunas[5];
            const colunaLastFailure = colunas[6];
            const colunaDependency = colunas[7];
            const colunaDataQuality = colunas[8];

            colunaDag.classList.add("coluna-dag");

            const nomeVisivel = (colunaDag.textContent || "").trim();
            const dagId = (colunaIdDag.textContent || "").trim();

            colunaDag.innerHTML = `
                <div class="nome-dag">${nomeVisivel || dagId || "-"}</div>
                <div class="subtexto-dag">${dagId || "-"}</div>
            `;

            colunaIdDag.classList.add("oculto");

            colunaScore.setAttribute("data-health-score", (colunaScore.textContent || "").trim());

            const textoStatus = (colunaStatus.textContent || "").trim();
            colunaStatus.innerHTML = `<span data-health-status="${textoStatus}">${textoStatus || "-"}</span>`;

            const textoDependency = (colunaDependency.textContent || "").trim();
            colunaDependency.innerHTML = `<span data-dependency-status="${textoDependency}">${textoDependency || "-"}</span>`;

            if (colunaDataQuality) {
                const textoDataQuality = (colunaDataQuality.textContent || "").trim();
                colunaDataQuality.innerHTML = `<span data-data-quality-status="${textoDataQuality}">${textoDataQuality || "-"}</span>`;
            }

            if (!((colunaLastFailure.textContent || "").trim())) {
                colunaLastFailure.textContent = "—";
            }
            if (!((colunaDuration.textContent || "").trim())) {
                colunaDuration.textContent = "—";
            }
            if (!((colunaLastRun.textContent || "").trim())) {
                colunaLastRun.textContent = "—";
            }
        });
    };

    const encontrarBlocoPorTitulo = (textoParcial) => {
        const candidatos = selecionarTodos("body h1, body h2, body h3, body p, body div, body section");
        const alvo = normalizarTexto(textoParcial);

        return candidatos.find((elemento) => normalizarTexto(elemento.textContent).includes(alvo)) || null;
    };

    const aplicarLayoutBase = () => {
        document.body.classList.add("pipeline-health-body");

        const corpoOriginal = document.body.innerHTML;

        if (selecionar(".pagina")) {
            return;
        }

        document.body.innerHTML = `<div class="pagina">${corpoOriginal}</div>`;
    };

    const estilizarBlocosTexto = () => {
        const pagina = selecionar(".pagina");
        if (!pagina) {
            return;
        }

        const html = pagina.innerHTML;

        pagina.innerHTML = html
            .replace(
                /Pipeline Health Monitor/g,
                `<div class="topo"><div class="topo-esquerda"><div class="icone-titulo">❤</div><div><h1>Pipeline Health Monitor</h1><p>Monitor avançado de saúde operacional dos pipelines do Airflow</p></div></div><div class="topo-direita"></div></div>`
            )
            .replace(/Monitor avançado de saúde operacional dos pipelines do Airflow/g, "");
    };

    const montarCardsMetricas = () => {
        const pagina = selecionar(".pagina");
        if (!pagina) {
            return;
        }

        const texto = pagina.textContent || "";

        const extrairNumero = (rotulo) => {
            const regex = new RegExp(`${rotulo}\\s*(\\d+)`, "i");
            const match = texto.match(regex);
            return match ? match[1] : "0";
        };

        const total = extrairNumero("Total Pipelines");
        const healthy = extrairNumero("Healthy");
        const degraded = extrairNumero("Degraded");
        const critical = extrairNumero("Critical");
        const paused = extrairNumero("Paused");
        const incidentes = extrairNumero("Incidents Last 24h");

        const grade = document.createElement("section");
        grade.className = "grade-resumo";
        grade.innerHTML = `
            <article class="card-metrica">
                <span class="rotulo">Total de pipelines</span>
                <span class="valor">${total}</span>
                <span class="subtexto">Pipelines monitorados</span>
            </article>
            <article class="card-metrica healthy">
                <span class="rotulo">Healthy</span>
                <span class="valor">${healthy}</span>
                <span class="subtexto">Saudáveis</span>
            </article>
            <article class="card-metrica degraded">
                <span class="rotulo">Degraded</span>
                <span class="valor">${degraded}</span>
                <span class="subtexto">Com degradação</span>
            </article>
            <article class="card-metrica critical">
                <span class="rotulo">Critical</span>
                <span class="valor">${critical}</span>
                <span class="subtexto">Críticos</span>
            </article>
            <article class="card-metrica paused">
                <span class="rotulo">Paused</span>
                <span class="valor">${paused}</span>
                <span class="subtexto">Pausados</span>
            </article>
            <article class="card-metrica critical">
                <span class="rotulo">Incidentes 24h</span>
                <span class="valor">${incidentes}</span>
                <span class="subtexto">Alertas recentes</span>
            </article>
        `;

        const topo = selecionar(".topo");
        if (topo && !selecionar(".grade-resumo")) {
            topo.insertAdjacentElement("afterend", grade);
        }
    };

    const embrulharTabela = () => {
        const tabela = selecionar("table");
        if (!tabela) {
            return;
        }

        if (tabela.closest(".cartao")) {
            return;
        }

        const secao = document.createElement("section");
        secao.className = "secao";
        secao.innerHTML = `
            <div class="secao-titulo">
                <h2>Pipeline Health</h2>
                <p>Visão consolidada dos pipelines monitorados</p>
            </div>
            <div class="cartao">
                <div class="tabela-wrapper"></div>
            </div>
        `;

        const wrapper = secao.querySelector(".tabela-wrapper");
        tabela.parentNode.insertBefore(secao, tabela);
        wrapper.appendChild(tabela);
    };

    const montarPainelInferior = () => {
        const pagina = selecionar(".pagina");
        if (!pagina || selecionar(".grade-inferior")) {
            return;
        }

        const texto = pagina.textContent || "";

        const linhas = texto
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean);

        const dependencias = [];
        const alertas = [];

        for (let i = 0; i < linhas.length; i += 1) {
            const atual = linhas[i];

            if (
                /sql server|omie api|sharepoint|s3 bucket|postgres airflow|redis broker/i.test(atual)
            ) {
                const status = linhas[i + 1] || "";
                const latencia = linhas[i + 2] || "";
                dependencias.push({
                    nome: atual,
                    status,
                    latencia,
                });
            }

            if (
                /api timeout|warning|health score critico|db connection error|token expired/i.test(atual)
            ) {
                alertas.push(atual);
            }
        }

        const secao = document.createElement("section");
        secao.className = "grade-inferior";

        const htmlDependencias = dependencias.length
            ? dependencias
                  .map(
                      (item) => `
                <div class="item-status">
                    <div class="linha-status-topo">
                        <span class="nome-dependencia">${item.nome}</span>
                        <span data-dependency-status="${item.status}">${item.status || "-"}</span>
                    </div>
                    <div class="latencia">${item.latencia || "Latência: —"}</div>
                </div>
            `
                  )
                  .join("")
            : `<div class="mensagem-vazia">Nenhuma dependência encontrada.</div>`;

        const htmlAlertas = alertas.length
            ? alertas
                  .map(
                      (item) => `
                <div class="item-alerta">
                    <span class="icone-alerta">!</span>
                    <div class="texto-alerta">${item}</div>
                </div>
            `
                  )
                  .join("")
            : `<div class="mensagem-vazia">Nenhum alerta recente.</div>`;

        secao.innerHTML = `
            <section class="secao">
                <div class="secao-titulo">
                    <h2>Dependency Status</h2>
                    <p>Saúde operacional das integrações</p>
                </div>
                <div class="cartao">
                    <div class="lista-status">
                        ${htmlDependencias}
                    </div>
                </div>
            </section>
            <section class="secao">
                <div class="secao-titulo">
                    <h2>Health Alerts</h2>
                    <p>Incidentes e eventos recentes</p>
                </div>
                <div class="cartao">
                    <div class="lista-alertas">
                        ${htmlAlertas}
                    </div>
                </div>
            </section>
        `;

        const tabelaSecao = selecionar(".secao .cartao");
        if (tabelaSecao) {
            tabelaSecao.closest(".secao").insertAdjacentElement("afterend", secao);
        } else {
            pagina.appendChild(secao);
        }
    };

    const esconderTextoCruDuplicado = () => {
        const pagina = selecionar(".pagina");
        if (!pagina) {
            return;
        }

        selecionarTodos(".pagina > *").forEach((elemento) => {
            const texto = normalizarTexto(elemento.textContent || "");

            if (
                texto === "monitor avancado de saude operacional dos pipelines do airflow" ||
                texto === "pipeline health monitor" ||
                texto === "operational summary" ||
                texto === "dependency status" ||
                texto === "health alerts"
            ) {
                elemento.classList.add("oculto");
            }
        });
    };

    const inicializar = () => {
        aplicarLayoutBase();
        estilizarBlocosTexto();
        montarCardsMetricas();
        formatarTabela();
        embrulharTabela();
        montarPainelInferior();
        aplicarBadges();
        aplicarCoresScore();
        esconderTextoCruDuplicado();
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", inicializar);
    } else {
        inicializar();
    }
})();
