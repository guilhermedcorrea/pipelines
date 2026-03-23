(() => {
    "use strict";

    const seletorBotoesTabela = ".botao-preview-tabela";
    const modal = document.getElementById("modal-preview-tabela");
    const botaoFechar = document.getElementById("modal-preview-fechar");
    const subtitulo = document.getElementById("modal-preview-tabela-subtitulo");
    const estado = document.getElementById("modal-preview-estado");
    const resumo = document.getElementById("modal-preview-resumo");
    const tabelaWrapper = document.getElementById("modal-preview-tabela-wrapper");
    const tabela = document.getElementById("modal-preview-tabela-dados");
    const tabelaHead = tabela?.querySelector("thead");
    const tabelaBody = tabela?.querySelector("tbody");
    const botaoAbrirCompleto = document.getElementById("modal-preview-abrir-completo");

    if (
        !modal ||
        !botaoFechar ||
        !subtitulo ||
        !estado ||
        !resumo ||
        !tabelaWrapper ||
        !tabelaHead ||
        !tabelaBody ||
        !botaoAbrirCompleto
    ) {
        return;
    }

    const esconder = (elemento) => {
        elemento.classList.add("hidden");
    };

    const mostrar = (elemento) => {
        elemento.classList.remove("hidden");
    };

    const limparTabela = () => {
        tabelaHead.innerHTML = "";
        tabelaBody.innerHTML = "";
    };

    const abrirModal = () => {
        modal.classList.remove("hidden");
        modal.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
    };

    const fecharModal = () => {
        modal.classList.add("hidden");
        modal.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
    };

    const escaparHtml = (valor) => {
        return String(valor ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    };

    const renderizarResumo = (dados) => {
        const banco = dados?.banco ?? "-";
        const schema = dados?.schema ?? "-";
        const tabelaNome = dados?.tabela ?? "-";
        const totalLinhas = dados?.total_linhas ?? "-";
        const quantidadeColunas = Array.isArray(dados?.colunas) ? dados.colunas.length : "-";

        resumo.innerHTML = `
            <div class="modal-preview__card">
                <span class="rotulo">Banco</span>
                <strong>${escaparHtml(banco)}</strong>
            </div>
            <div class="modal-preview__card">
                <span class="rotulo">Schema</span>
                <strong>${escaparHtml(schema)}</strong>
            </div>
            <div class="modal-preview__card">
                <span class="rotulo">Tabela</span>
                <strong>${escaparHtml(tabelaNome)}</strong>
            </div>
            <div class="modal-preview__card">
                <span class="rotulo">Colunas</span>
                <strong>${escaparHtml(quantidadeColunas)}</strong>
            </div>
            <div class="modal-preview__card">
                <span class="rotulo">Total de linhas</span>
                <strong>${escaparHtml(totalLinhas)}</strong>
            </div>
        `;

        mostrar(resumo);
    };

    const renderizarTabela = (dados) => {
        limparTabela();

        const colunas = Array.isArray(dados?.colunas) ? dados.colunas : [];
        const linhas = Array.isArray(dados?.linhas) ? dados.linhas : [];

        if (!colunas.length) {
            estado.textContent = "Não foi possível identificar as colunas da tabela.";
            mostrar(estado);
            esconder(tabelaWrapper);
            return;
        }

        const trHead = document.createElement("tr");

        colunas.forEach((coluna) => {
            const th = document.createElement("th");
            th.textContent = coluna;
            trHead.appendChild(th);
        });

        tabelaHead.appendChild(trHead);

        if (!linhas.length) {
            const tr = document.createElement("tr");
            const td = document.createElement("td");

            td.colSpan = colunas.length;
            td.className = "vazio";
            td.textContent = "Nenhuma linha encontrada para a amostra.";

            tr.appendChild(td);
            tabelaBody.appendChild(tr);
        } else {
            linhas.forEach((linha) => {
                const tr = document.createElement("tr");

                colunas.forEach((coluna) => {
                    const td = document.createElement("td");
                    const valor = linha?.[coluna];

                    if (valor === null || valor === undefined || valor === "") {
                        td.textContent = "-";
                    } else if (typeof valor === "object") {
                        td.textContent = JSON.stringify(valor);
                    } else {
                        td.textContent = String(valor);
                    }

                    tr.appendChild(td);
                });

                tabelaBody.appendChild(tr);
            });
        }

        esconder(estado);
        mostrar(tabelaWrapper);
    };

    const mostrarErro = (mensagem) => {
        limparTabela();
        resumo.innerHTML = "";
        esconder(resumo);
        esconder(tabelaWrapper);
        estado.textContent = mensagem;
        mostrar(estado);
    };

    const montarParametrosTabela = ({ conexaoId, banco, schema, tabelaNome }) => {
        const parametros = new URLSearchParams();

        parametros.set("conexao_id", conexaoId);
        parametros.set("schema", schema);
        parametros.set("tabela", tabelaNome);

        if (banco && String(banco).trim()) {
            parametros.set("banco", String(banco).trim());
        }

        return parametros;
    };

    const carregarPreview = async ({ conexaoId, banco, schema, tabelaNome, texto }) => {
        abrirModal();

        subtitulo.textContent = texto || (banco ? `${banco}.${schema}.${tabelaNome}` : `${schema}.${tabelaNome}`);
        estado.textContent = "Carregando preview...";
        mostrar(estado);
        esconder(resumo);
        esconder(tabelaWrapper);
        limparTabela();

        const parametros = montarParametrosTabela({
            conexaoId,
            banco,
            schema,
            tabelaNome,
        });

        const urlPreview = `/auditoria-execucao/api/tabela/preview?${parametros.toString()}`;
        const urlCompleta = `/auditoria-execucao/tabela?${parametros.toString()}`;

        botaoAbrirCompleto.href = urlCompleta;
        botaoAbrirCompleto.dataset.href = urlCompleta;

        try {
            const resposta = await fetch(urlPreview, {
                method: "GET",
                headers: {
                    Accept: "application/json",
                },
            });

            if (!resposta.ok) {
                let detalheErro = `Falha ao buscar preview da tabela. HTTP ${resposta.status}.`;

                try {
                    const erroJson = await resposta.json();
                    if (erroJson?.detail) {
                        detalheErro = String(erroJson.detail);
                    }
                } catch (_) {
                    /* Eu ignoro falha ao ler o corpo do erro */
                }

                throw new Error(detalheErro);
            }

            const dados = await resposta.json();

            renderizarResumo(dados);
            renderizarTabela(dados);
        } catch (erro) {
            console.error("Erro ao carregar preview da tabela.", erro);
            mostrarErro(erro?.message || "Erro ao carregar preview da tabela.");
        }
    };

    document.addEventListener("click", (evento) => {
        const botaoTabela = evento.target.closest(seletorBotoesTabela);

        if (botaoTabela) {
            evento.preventDefault();

            carregarPreview({
                conexaoId: botaoTabela.dataset.conexaoId,
                banco: botaoTabela.dataset.banco || "",
                schema: botaoTabela.dataset.schema,
                tabelaNome: botaoTabela.dataset.tabela,
                texto: botaoTabela.dataset.texto,
            });

            return;
        }

        const alvoFechar = evento.target.closest("[data-fechar-modal='true']");
        if (alvoFechar) {
            fecharModal();
            return;
        }

        if (evento.target.closest("#modal-preview-abrir-completo")) {
            evento.preventDefault();

            const url = botaoAbrirCompleto.dataset.href || botaoAbrirCompleto.getAttribute("href");
            if (url && url !== "#") {
                window.location.href = url;
            }
        }
    });

    botaoFechar.addEventListener("click", fecharModal);

    document.addEventListener("keydown", (evento) => {
        if (evento.key === "Escape" && !modal.classList.contains("hidden")) {
            fecharModal();
        }
    });
})();