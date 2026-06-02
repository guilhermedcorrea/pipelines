(() => {
    "use strict";

    const SELETOR_CARDS = "[data-testid='card-list'] > *";
    const CLASSE_BLOCO = "bloco-logos-dag";
    const URL_API_DAG_TAGS = "/logos-dags-static/api/dag-tags";

    let mapaTagsLote = null;
    let promessaMapaTagsLote = null;

    const normalizar = (texto) =>
        (texto || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .trim();

    const criarLogo = (urlImagem, titulo, classeExtra = "") => {
        const logo = document.createElement("span");
        logo.className = `logo-pill ${classeExtra}`.trim();
        logo.title = titulo;
        logo.setAttribute("aria-label", titulo);
        logo.setAttribute("role", "img");
        logo.style.backgroundImage = `url("${urlImagem}")`;
        return logo;
    };

    const criarSeparador = () => {
        const sep = document.createElement("span");
        sep.className = "separador-logos";
        return sep;
    };

    const serializarTags = (tags) =>
        tags
            .map((tag) => normalizar(tag))
            .filter(Boolean)
            .sort()
            .join("|");

    const obterDagId = (card) => {
        const el = card.querySelector("[data-testid='dag-id']");
        return el ? (el.textContent || "").trim() : "";
    };

    const obterEstruturaGrupoEsquerdo = (card) => {
        const dagId = card.querySelector("[data-testid='dag-id']");
        const listaTags = card.querySelector("[data-testid='limited-items-list']");

        if (!dagId || !listaTags) {
            return null;
        }

        let atual = dagId.parentElement;

        while (atual && atual !== card) {
            if (atual.contains(dagId) && atual.contains(listaTags)) {
                return {
                    grupoEsquerdo: atual,
                    dagId,
                    listaTags,
                };
            }
            atual = atual.parentElement;
        }

        return null;
    };

    const temTagsDeLogo = (tags) => {
        const conjunto = new Set(tags.map(normalizar));
        return (
            conjunto.has("euromidia") ||
            conjunto.has("shempo") ||
            conjunto.has("sinamovel") ||
            conjunto.has("omie") ||
            conjunto.has("granatum") ||
            conjunto.has("d4sign") ||
            conjunto.has("sqlserver") ||
            conjunto.has("sql server") ||
            conjunto.has("sap") ||
            conjunto.has("sapb1") ||
            conjunto.has("auvo")
        );
    };

    const construirMapaTagsLote = async () => {
        if (mapaTagsLote) {
            return mapaTagsLote;
        }

        if (promessaMapaTagsLote) {
            return promessaMapaTagsLote;
        }

        promessaMapaTagsLote = fetch(URL_API_DAG_TAGS, {
            method: "GET",
            credentials: "same-origin",
            headers: {
                Accept: "application/json",
            },
        })
            .then(async (resposta) => {
                if (!resposta.ok) {
                    throw new Error(`Falha ao buscar tags dos DAGs: ${resposta.status}`);
                }

                const dados = await resposta.json();
                const itens = Array.isArray(dados.items) ? dados.items : [];
                const mapa = new Map();

                itens.forEach((dag) => {
                    const dagId = (dag.dag_id || "").trim();
                    if (!dagId) {
                        return;
                    }

                    const tags = Array.isArray(dag.tags)
                        ? dag.tags.map((tag) => normalizar(tag)).filter(Boolean)
                        : [];

                    mapa.set(dagId, tags);
                });

                mapaTagsLote = mapa;
                return mapa;
            })
            .catch((erro) => {
                console.error("Erro ao buscar tags dos DAGs no metadatabase:", erro);
                mapaTagsLote = new Map();
                return mapaTagsLote;
            })
            .finally(() => {
                promessaMapaTagsLote = null;
            });

        return promessaMapaTagsLote;
    };

    const montarBlocoLogos = (tags) => {
        const bloco = document.createElement("div");
        bloco.className = CLASSE_BLOCO;

        const temEuromidia = tags.includes("euromidia");
        const temShempo = tags.includes("shempo");
        const temSinamovel = tags.includes("sinamovel");
        const temOmie = tags.includes("omie");
        const temGranatum = tags.includes("granatum");
        const temD4Sign = tags.includes("d4sign");
        const temSQLServer = tags.includes("sqlserver") || tags.includes("sql server");
        const temSAPB1 = tags.includes("sap") || tags.includes("sapb1");
        const temAuvo = tags.includes("auvo");

        let quantidade = 0;
        let temEmpresa = false;

        if (temEuromidia) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoEmpresaProprietaria/euromidia.png",
                    "Euromidia",
                    "logo-empresa"
                )
            );
            quantidade += 1;
            temEmpresa = true;
        }

        if (temShempo) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoEmpresaProprietaria/Shempo.jpg",
                    "Shempo",
                    "logo-empresa"
                )
            );
            quantidade += 1;
            temEmpresa = true;
        }

        if (temSinamovel) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoEmpresaProprietaria/sinamovel.png",
                    "Sinamovel",
                    "logo-empresa"
                )
            );
            quantidade += 1;
            temEmpresa = true;
        }

        if (temEmpresa && (temOmie || temGranatum || temD4Sign || temSQLServer || temSAPB1 || temAuvo)) {
            bloco.appendChild(criarSeparador());
        }

        if (temOmie) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/omie.jpg",
                    "Omie",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        if (temGranatum) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/granatum.png",
                    "Granatum",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        if (temD4Sign) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/d4sign.jpg",
                    "D4Sign",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        if (temSQLServer) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/SQLServer.png",
                    "SQL Server",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        if (temSAPB1) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/SAPB1.png",
                    "SAP Business One",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        if (temAuvo) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/auvo.png",
                    "Auvo",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        return quantidade > 0 ? bloco : null;
    };

    const aplicarNoCard = async (card, mapaLote) => {
        const dagId = obterDagId(card);
        if (!dagId) {
            return;
        }

        const estrutura = obterEstruturaGrupoEsquerdo(card);
        if (!estrutura) {
            return;
        }

        const tags = mapaLote.get(dagId) || [];
        const assinatura = serializarTags(tags);

        const blocoExistente = estrutura.grupoEsquerdo.querySelector(`.${CLASSE_BLOCO}`);

        if (blocoExistente && blocoExistente.dataset.tagsAssinatura === assinatura) {
            return;
        }

        if (blocoExistente) {
            blocoExistente.remove();
        }

        if (!temTagsDeLogo(tags)) {
            return;
        }

        const blocoNovo = montarBlocoLogos(tags);
        if (!blocoNovo) {
            return;
        }

        blocoNovo.dataset.tagsAssinatura = assinatura;

        estrutura.grupoEsquerdo.classList.add("grupo-esquerdo-com-logos");
        estrutura.listaTags.insertAdjacentElement("afterend", blocoNovo);
    };

    const aplicarEmTodosOsCards = async () => {
        const mapaLote = await construirMapaTagsLote();
        const cards = Array.from(document.querySelectorAll(SELETOR_CARDS));

        await Promise.all(cards.map((card) => aplicarNoCard(card, mapaLote)));
    };

    let timer = null;

    const agendarAplicacao = () => {
        if (timer) {
            clearTimeout(timer);
        }

        timer = setTimeout(() => {
            aplicarEmTodosOsCards().catch((erro) => {
                console.error("Erro ao aplicar logos:", erro);
            });
        }, 150);
    };

    agendarAplicacao();

    const observer = new MutationObserver((mutacoes) => {
        const precisaReaplicar = mutacoes.some((mutacao) => {
            if (mutacao.type !== "childList") {
                return false;
            }

            const adicionados = Array.from(mutacao.addedNodes || []);
            return adicionados.some((node) => {
                if (!(node instanceof HTMLElement)) {
                    return false;
                }

                if (node.classList?.contains(CLASSE_BLOCO)) {
                    return false;
                }

                return (
                    node.matches?.(SELETOR_CARDS) ||
                    node.querySelector?.("[data-testid='dag-id']") ||
                    node.querySelector?.("[data-testid='card-list']")
                );
            });
        });

        if (precisaReaplicar) {
            agendarAplicacao();
        }
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true,
    });
})();
