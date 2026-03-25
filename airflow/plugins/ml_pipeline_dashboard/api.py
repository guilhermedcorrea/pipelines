from __future__ import annotations

import importlib
from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ml_pipeline_dashboard.config import CONFIGURACAO, PASTA_STATIC, PASTA_TEMPLATES, obter_contexto_frontend


app = FastAPI(title=CONFIGURACAO.titulo_plugin)
templates = Jinja2Templates(directory=str(PASTA_TEMPLATES))

if PASTA_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(PASTA_STATIC)), name="ml_pipeline_dashboard_static")


def criar_app_fastapi() -> FastAPI:
    """Eu devolvo a aplicação FastAPI para o plugin principal do Airflow."""
    return app


def _resolver_funcao(modulos: tuple[str, ...], nomes_funcoes: tuple[str, ...]) -> Callable[..., Any] | None:
    """Eu tento localizar uma função em vários módulos sem quebrar a importação do plugin."""
    for nome_modulo in modulos:
        try:
            modulo = importlib.import_module(nome_modulo)
        except Exception:
            continue

        for nome_funcao in nomes_funcoes:
            funcao = getattr(modulo, nome_funcao, None)
            if callable(funcao):
                return funcao

    return None


_LISTAR_PIPELINES = _resolver_funcao(
    modulos=(
        "ml_pipeline_dashboard.services.ml_dashboard_service",
        "ml_pipeline_dashboard.services.dag_service",
    ),
    nomes_funcoes=("listar_pipelines_ml", "listar_dags_auditoria"),
)

_OBTER_DASHBOARD = _resolver_funcao(
    modulos=(
        "ml_pipeline_dashboard.services.ml_dashboard_service",
        "ml_pipeline_dashboard.services.dag_service",
    ),
    nomes_funcoes=("obter_dashboard_pipeline_ml", "obter_dashboard_dag"),
)

_OBTER_DETALHE_TASK = _resolver_funcao(
    modulos=(
        "ml_pipeline_dashboard.services.ml_dashboard_service",
        "ml_pipeline_dashboard.services.task_service",
    ),
    nomes_funcoes=("obter_detalhe_task_pipeline_ml", "obter_detalhe_task"),
)

_OBTER_PREVIEW_TABELA_TASK = _resolver_funcao(
    modulos=(
        "ml_pipeline_dashboard.services.ml_dashboard_service",
        "ml_pipeline_dashboard.services.task_service",
    ),
    nomes_funcoes=("obter_preview_tabela_task_pipeline_ml", "obter_preview_tabela_task"),
)

_OBTER_TABELA_TASK = _resolver_funcao(
    modulos=(
        "ml_pipeline_dashboard.services.ml_dashboard_service",
        "ml_pipeline_dashboard.services.task_service",
    ),
    nomes_funcoes=("obter_tabela_paginada_task_pipeline_ml", "obter_tabela_paginada_task"),
)

_OBTER_PREVIEW_TABELA = _resolver_funcao(
    modulos=("ml_pipeline_dashboard.services.sql_preview_service",),
    nomes_funcoes=("obter_preview_tabela",),
)

_LISTAR_TABELA_REAL = _resolver_funcao(
    modulos=("ml_pipeline_dashboard.services.sql_preview_service",),
    nomes_funcoes=("listar_tabela_real",),
)

_OBTER_METADADOS_TABELA = _resolver_funcao(
    modulos=("ml_pipeline_dashboard.services.sql_preview_service",),
    nomes_funcoes=("obter_metadados_tabela",),
)


def _erro_servico_nao_implementado(nome_servico: str) -> HTTPException:
    """Eu devolvo um erro explícito quando a API depende de um serviço ainda não criado."""
    return HTTPException(
        status_code=501,
        detail=(
            f"O serviço '{nome_servico}' ainda não está disponível no pacote "
            "ml_pipeline_dashboard.services. Crie o service correspondente antes de usar esta rota."
        ),
    )


def _normalizar_tag(valor: Any) -> str:
    """Eu normalizo tag para comparar sem diferença de caixa e separadores."""
    texto = str(valor or "").strip().lower()
    return texto.replace("-", "").replace("_", "").replace(" ", "")


def _normalizar_lista_tags(tags: Any) -> list[str]:
    """Eu aceito tags em vários formatos para não depender de um contrato rígido demais."""
    if tags is None:
        return []

    if isinstance(tags, str):
        valor = tags.strip()
        return [valor] if valor else []

    if isinstance(tags, (list, tuple, set)):
        resultado: list[str] = []
        for item in tags:
            texto = str(item or "").strip()
            if texto:
                resultado.append(texto)
        return resultado

    return []


def _extrair_tags_item(item: dict[str, Any]) -> list[str]:
    """Eu procuro tags nos campos mais prováveis do payload."""
    candidatos = (
        item.get("tags"),
        item.get("pipeline_tags"),
        item.get("dag_tags"),
        item.get("metadata", {}).get("tags") if isinstance(item.get("metadata"), dict) else None,
        item.get("dashboard", {}).get("tags") if isinstance(item.get("dashboard"), dict) else None,
    )

    for candidato in candidatos:
        tags = _normalizar_lista_tags(candidato)
        if tags:
            return tags

    return []


def _eh_pipeline_machine_learning(item: dict[str, Any]) -> bool:
    """Eu filtro pipelines de ML por tags e, se necessário, por heurística do dag_id."""
    tags_item = {_normalizar_tag(tag) for tag in _extrair_tags_item(item)}
    tags_ml = {_normalizar_tag(tag) for tag in CONFIGURACAO.tags_pipeline_ml}

    if tags_item and tags_item.intersection(tags_ml):
        return True

    dag_id = str(item.get("dag_id") or item.get("id") or "").strip().lower()
    if not dag_id:
        return False

    return any(palavra in dag_id for palavra in CONFIGURACAO.palavras_chave_heuristica_ml)


def _filtrar_payload_para_ml(payload: dict[str, Any]) -> dict[str, Any]:
    """Eu filtro a lista para retornar apenas DAGs compatíveis com Machine Learning."""
    itens = payload.get("itens") if isinstance(payload, dict) else None
    if not isinstance(itens, list):
        return payload

    itens_filtrados = [item for item in itens if isinstance(item, dict) and _eh_pipeline_machine_learning(item)]

    retorno = dict(payload)
    retorno["itens"] = itens_filtrados
    retorno["total"] = len(itens_filtrados)
    retorno["filtro_aplicado"] = "machine_learning"

    return retorno


def _resolver_caminho_arquivo_seguro(caminho: str) -> Path:
    """Eu permito download apenas dentro das raízes declaradas no config.py."""
    caminho_limpo = str(caminho or "").strip()
    if not caminho_limpo:
        raise HTTPException(status_code=400, detail="Caminho de arquivo inválido.")

    caminho_resolvido = Path(caminho_limpo).expanduser().resolve()

    permitido = any(
        raiz == caminho_resolvido or raiz in caminho_resolvido.parents
        for raiz in CONFIGURACAO.roots_arquivos_permitidos
    )

    if not permitido:
        raise HTTPException(
            status_code=403,
            detail="Arquivo fora das raízes permitidas para download.",
        )

    if not caminho_resolvido.exists() or not caminho_resolvido.is_file():
        raise HTTPException(
            status_code=404,
            detail="Arquivo não encontrado para download.",
        )

    return caminho_resolvido


def _extrair_filtros_http(
    request: Request,
    filtros_relevantes: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    """
    Eu leio filtros dinâmicos da query string.

    Convenção:
    - categóricos: f_<coluna>=valor1&f_<coluna>=valor2
    - data inicial: de_<coluna>=2026-03-01
    - data final:   ate_<coluna>=2026-03-31
    """
    query_params = request.query_params

    filtros_categoricos: dict[str, list[str]] = {}
    filtros_datas_de: dict[str, str] = {}
    filtros_datas_ate: dict[str, str] = {}

    for item in filtros_relevantes.get("categoricos", []):
        chave = str(item.get("chave") or "").strip()
        if not chave:
            continue

        valores = [str(v).strip() for v in query_params.getlist(f"f_{chave}") if str(v).strip()]
        if valores:
            filtros_categoricos[chave] = valores

    for item in filtros_relevantes.get("datas", []):
        chave = str(item.get("chave") or "").strip()
        if not chave:
            continue

        valor_de = str(query_params.get(f"de_{chave}") or "").strip()
        valor_ate = str(query_params.get(f"ate_{chave}") or "").strip()

        if valor_de:
            filtros_datas_de[chave] = valor_de

        if valor_ate:
            filtros_datas_ate[chave] = valor_ate

    return filtros_categoricos, filtros_datas_de, filtros_datas_ate


def _montar_querystring_base_tabela(
    conn_id: str,
    banco: str | None,
    schema: str,
    tabela: str,
    request: Request,
    filtros_relevantes: dict[str, Any],
    tamanho_pagina: int,
    ordenar_por: str,
    direcao: str,
    texto_busca: str,
) -> str:
    """Eu reconstruo a query string preservando filtros dinâmicos."""
    filtros_categoricos, filtros_datas_de, filtros_datas_ate = _extrair_filtros_http(
        request=request,
        filtros_relevantes=filtros_relevantes,
    )

    pares: list[tuple[str, str]] = [
        ("conexao_id", conn_id),
        ("schema", schema),
        ("tabela", tabela),
        ("tamanho_pagina", str(tamanho_pagina)),
        ("ordenar_por", ordenar_por or ""),
        ("direcao", direcao or "asc"),
        ("q", texto_busca or ""),
    ]

    if banco:
        pares.append(("banco", banco))

    for coluna, valores in filtros_categoricos.items():
        for valor in valores:
            pares.append((f"f_{coluna}", valor))

    for coluna, valor in filtros_datas_de.items():
        pares.append((f"de_{coluna}", valor))

    for coluna, valor in filtros_datas_ate.items():
        pares.append((f"ate_{coluna}", valor))

    return urlencode(pares, doseq=True)


def _nome_template_existe(nome_template: str) -> bool:
    """Eu verifico se o template físico existe antes de tentar renderizá-lo."""
    return (PASTA_TEMPLATES / nome_template).exists()


def _renderizar_lista_fallback_html() -> str:
    """Eu entrego uma tela mínima funcional mesmo que o HTML definitivo ainda não exista."""
    contexto = obter_contexto_frontend()
    paleta = contexto["paleta"]

    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{escape(CONFIGURACAO.titulo_html_lista)}</title>
  <style>
    :root {{
      --bg:{paleta["fundo_primario"]};
      --bg2:{paleta["fundo_secundario"]};
      --card:{paleta["card"]};
      --cardHover:{paleta["card_hover"]};
      --border:{paleta["borda"]};
      --text:{paleta["texto_primario"]};
      --muted:{paleta["texto_secundario"]};
      --primary:{paleta["roxo_primario"]};
      --primaryHover:{paleta["roxo_hover"]};
      --cyan:{paleta["ciano_destaque"]};
      --blue:{paleta["azul_destaque"]};
      --green:{paleta["verde_sucesso"]};
      --yellow:{paleta["amarelo_alerta"]};
      --red:{paleta["vermelho_falha"]};
      --shadow:{paleta["sombra_roxa"]};
    }}

    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: radial-gradient(circle at top right, rgba(110,86,207,.18), transparent 28%), var(--bg);
      color:var(--text);
    }}
    .pagina {{
      width:min(1480px, calc(100vw - 32px));
      margin:0 auto;
      padding:24px 0 40px;
    }}
    .hero {{
      background:linear-gradient(180deg, rgba(21,31,56,.96), rgba(17,24,45,.96));
      border:1px solid var(--border);
      border-radius:24px;
      padding:24px;
      box-shadow:var(--shadow);
      margin-bottom:18px;
    }}
    .hero h1 {{ margin:0 0 8px 0; font-size:30px; }}
    .hero p {{ margin:0; color:var(--muted); line-height:1.6; }}
    .barra {{
      display:flex; gap:12px; flex-wrap:wrap; margin-top:16px;
    }}
    .entrada, .select {{
      background:var(--bg2);
      color:var(--text);
      border:1px solid var(--border);
      border-radius:14px;
      padding:12px 14px;
      min-height:44px;
    }}
    .entrada {{ min-width:260px; flex:1 1 320px; }}
    .select {{ min-width:180px; }}
    .botao {{
      border:none;
      border-radius:14px;
      padding:12px 16px;
      background:linear-gradient(135deg, var(--primary), var(--blue));
      color:white;
      font-weight:700;
      cursor:pointer;
    }}
    .resumo {{
      display:grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap:12px;
      margin-bottom:16px;
    }}
    .kpi {{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:18px;
      padding:16px;
      box-shadow:var(--shadow);
    }}
    .kpi small {{ display:block; color:var(--muted); margin-bottom:8px; }}
    .kpi strong {{ font-size:28px; }}
    .lista {{
      display:grid;
      gap:14px;
    }}
    .card {{
      background:linear-gradient(180deg, rgba(21,31,56,.98), rgba(17,24,45,.98));
      border:1px solid var(--border);
      border-radius:20px;
      padding:18px;
      box-shadow:var(--shadow);
      transition:transform .16s ease, border-color .16s ease, background .16s ease;
    }}
    .card:hover {{
      transform:translateY(-2px);
      background:linear-gradient(180deg, rgba(26,39,72,.98), rgba(17,24,45,.98));
      border-color:rgba(59,130,246,.55);
    }}
    .card-topo {{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:12px;
    }}
    .dag-id {{
      margin:0 0 4px 0;
      font-size:20px;
      color:var(--text);
      word-break:break-word;
    }}
    .badge {{
      display:inline-flex;
      align-items:center;
      gap:6px;
      min-height:28px;
      border-radius:999px;
      padding:6px 10px;
      font-size:12px;
      font-weight:700;
      border:1px solid transparent;
    }}
    .status-success {{ background:rgba(34,197,94,.14); color:#97f0b3; border-color:rgba(34,197,94,.30); }}
    .status-failed {{ background:rgba(239,68,68,.14); color:#ffb0b0; border-color:rgba(239,68,68,.30); }}
    .status-running {{ background:rgba(59,130,246,.14); color:#b6d6ff; border-color:rgba(59,130,246,.30); }}
    .status-unknown {{ background:rgba(245,158,11,.14); color:#ffe1a1; border-color:rgba(245,158,11,.30); }}
    .metadados {{
      display:grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap:12px;
      margin:14px 0;
    }}
    .item-meta {{
      background:rgba(255,255,255,.02);
      border:1px solid rgba(255,255,255,.06);
      border-radius:16px;
      padding:12px;
    }}
    .item-meta small {{ display:block; color:var(--muted); margin-bottom:6px; }}
    .item-meta span {{ display:block; font-weight:700; word-break:break-word; }}
    .acoes {{
      display:flex;
      gap:10px;
      flex-wrap:wrap;
      margin-top:14px;
    }}
    .link-acao {{
      text-decoration:none;
      color:white;
      background:linear-gradient(135deg, var(--primary), var(--blue));
      border-radius:12px;
      padding:10px 14px;
      font-weight:700;
    }}
    .vazio {{
      background:var(--card);
      border:1px dashed var(--border);
      border-radius:20px;
      padding:28px;
      text-align:center;
      color:var(--muted);
      box-shadow:var(--shadow);
    }}
  </style>
</head>
<body>
  <div class="pagina">
    <section class="hero">
      <h1>{escape(CONFIGURACAO.titulo_plugin)}</h1>
      <p>{escape(CONFIGURACAO.subtitulo_plugin)}</p>
      <div class="barra">
        <input class="entrada" id="filtroDag" placeholder="Filtrar por dag_id..." />
        <select class="select" id="filtroStatus">
          <option value="">Todos os status</option>
          <option value="success">success</option>
          <option value="failed">failed</option>
          <option value="running">running</option>
          <option value="queued">queued</option>
        </select>
        <button class="botao" id="btnAtualizar">Atualizar</button>
      </div>
    </section>

    <section class="resumo">
      <div class="kpi"><small>Total de pipelines ML</small><strong id="kpiTotal">-</strong></div>
      <div class="kpi"><small>Sucesso</small><strong id="kpiSuccess">-</strong></div>
      <div class="kpi"><small>Falha</small><strong id="kpiFailed">-</strong></div>
      <div class="kpi"><small>Executando</small><strong id="kpiRunning">-</strong></div>
    </section>

    <section class="lista" id="listaPipelines"></section>
  </div>

  <script>
    const prefixo = {contexto["url_prefixo"]!r};
    const rotaListaApi = prefixo + {contexto["rota_lista_api"]!r};
    const rotaDetalhe = prefixo + "/pipelines";

    function classeStatus(status) {{
      const valor = String(status || "").toLowerCase();
      if (["success", "successful", "sucesso"].includes(valor)) return "status-success";
      if (["failed", "failure", "erro"].includes(valor)) return "status-failed";
      if (["running", "queued"].includes(valor)) return "status-running";
      return "status-unknown";
    }}

    function textoSeguro(valor) {{
      return String(valor ?? "").replace(/[&<>"']/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[c]));
    }}

    function montarCard(item) {{
      const status = String(item.status || "unknown");
      const detalheHref = `${{rotaDetalhe}}/${{encodeURIComponent(item.dag_id)}}?run_id=${{encodeURIComponent(item.run_id || "")}}`;
      return `
        <article class="card">
          <div class="card-topo">
            <div>
              <h2 class="dag-id">${{textoSeguro(item.dag_id)}}</h2>
              <div style="color:var(--muted)">${{textoSeguro(item.run_id || "")}}</div>
            </div>
            <span class="badge ${{classeStatus(status)}}">${{textoSeguro(status)}}</span>
          </div>
          <div class="metadados">
            <div class="item-meta"><small>Tipo de run</small><span>${{textoSeguro(item.run_type || "-")}}</span></div>
            <div class="item-meta"><small>Execução</small><span>${{textoSeguro(item.execution_date || "-")}}</span></div>
            <div class="item-meta"><small>Início</small><span>${{textoSeguro(item.start_date || "-")}}</span></div>
            <div class="item-meta"><small>Duração (s)</small><span>${{textoSeguro(item.duration_seconds ?? "-")}}</span></div>
          </div>
          <div class="acoes">
            <a class="link-acao" href="${{detalheHref}}">Abrir painel</a>
          </div>
        </article>
      `;
    }}

    async function carregarLista() {{
      const dagId = document.getElementById("filtroDag").value.trim();
      const status = document.getElementById("filtroStatus").value.trim();

      const params = new URLSearchParams();
      if (dagId) params.set("dag_id", dagId);
      if (status) params.set("status", status);

      const resposta = await fetch(`${{rotaListaApi}}?${{params.toString()}}`, {{ credentials: "same-origin" }});
      if (!resposta.ok) {{
        throw new Error(`Falha ao carregar lista. HTTP ${{resposta.status}}`);
      }}

      const payload = await resposta.json();
      const itens = Array.isArray(payload.itens) ? payload.itens : [];

      document.getElementById("kpiTotal").textContent = String(itens.length);
      document.getElementById("kpiSuccess").textContent = String(itens.filter(x => String(x.status).toLowerCase() === "success").length);
      document.getElementById("kpiFailed").textContent = String(itens.filter(x => String(x.status).toLowerCase() === "failed").length);
      document.getElementById("kpiRunning").textContent = String(itens.filter(x => ["running", "queued"].includes(String(x.status).toLowerCase())).length);

      const lista = document.getElementById("listaPipelines");
      if (!itens.length) {{
        lista.innerHTML = `<div class="vazio">Nenhum pipeline de Machine Learning encontrado com os filtros atuais.</div>`;
        return;
      }}

      lista.innerHTML = itens.map(montarCard).join("");
    }}

    document.getElementById("btnAtualizar").addEventListener("click", () => {{
      carregarLista().catch((erro) => {{
        console.error(erro);
        document.getElementById("listaPipelines").innerHTML = `<div class="vazio">${{textoSeguro(erro.message || "Erro ao carregar lista.")}}</div>`;
      }});
    }});

    carregarLista().catch((erro) => {{
      console.error(erro);
      document.getElementById("listaPipelines").innerHTML = `<div class="vazio">${{textoSeguro(erro.message || "Erro ao carregar lista.")}}</div>`;
    }});
  </script>
</body>
</html>
"""


def _renderizar_detalhe_fallback_html(dag_id: str, run_id: str | None) -> str:
    """Eu entrego uma shell HTML simples para consultar o JSON do pipeline e exibir o resumo."""
    contexto = obter_contexto_frontend()
    paleta = contexto["paleta"]
    dag_id_escape = escape(dag_id)
    run_id_escape = escape(run_id or "")

    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{escape(CONFIGURACAO.titulo_html_detalhe)} - {dag_id_escape}</title>
  <style>
    :root {{
      --bg:{paleta["fundo_primario"]};
      --bg2:{paleta["fundo_secundario"]};
      --card:{paleta["card"]};
      --border:{paleta["borda"]};
      --text:{paleta["texto_primario"]};
      --muted:{paleta["texto_secundario"]};
      --primary:{paleta["roxo_primario"]};
      --blue:{paleta["azul_destaque"]};
      --cyan:{paleta["ciano_destaque"]};
      --green:{paleta["verde_sucesso"]};
      --yellow:{paleta["amarelo_alerta"]};
      --red:{paleta["vermelho_falha"]};
      --shadow:{paleta["sombra_roxa"]};
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: radial-gradient(circle at top right, rgba(110,86,207,.18), transparent 28%), var(--bg);
      color:var(--text);
    }}
    .pagina {{
      width:min(1480px, calc(100vw - 32px));
      margin:0 auto;
      padding:24px 0 40px;
    }}
    .topo, .card {{
      background:linear-gradient(180deg, rgba(21,31,56,.98), rgba(17,24,45,.98));
      border:1px solid var(--border);
      border-radius:24px;
      padding:20px;
      box-shadow:var(--shadow);
      margin-bottom:16px;
    }}
    .topo h1 {{ margin:0 0 8px 0; font-size:28px; }}
    .sub {{
      color:var(--muted);
      line-height:1.6;
      margin:0;
    }}
    .grade {{
      display:grid;
      grid-template-columns: 1.1fr .9fr;
      gap:16px;
    }}
    .bloco {{
      background:rgba(255,255,255,.02);
      border:1px solid rgba(255,255,255,.06);
      border-radius:18px;
      padding:16px;
      margin-top:14px;
    }}
    .chips {{
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      margin-top:12px;
    }}
    .chip {{
      padding:6px 10px;
      border-radius:999px;
      border:1px solid var(--border);
      background:rgba(255,255,255,.03);
      color:var(--muted);
      font-size:12px;
      font-weight:700;
    }}
    .lista-tarefas {{
      display:grid;
      gap:10px;
      margin-top:14px;
    }}
    .tarefa {{
      border:1px solid rgba(255,255,255,.07);
      background:rgba(255,255,255,.02);
      border-radius:16px;
      padding:12px 14px;
    }}
    .tarefa-topo {{
      display:flex;
      justify-content:space-between;
      gap:10px;
      align-items:flex-start;
    }}
    .badge {{
      display:inline-flex;
      min-height:28px;
      align-items:center;
      justify-content:center;
      border-radius:999px;
      padding:6px 10px;
      font-size:12px;
      font-weight:700;
    }}
    .success {{ background:rgba(34,197,94,.14); color:#97f0b3; }}
    .failed {{ background:rgba(239,68,68,.14); color:#ffb0b0; }}
    .running {{ background:rgba(59,130,246,.14); color:#b6d6ff; }}
    .unknown {{ background:rgba(245,158,11,.14); color:#ffe1a1; }}
    pre {{
      margin:0;
      white-space:pre-wrap;
      word-break:break-word;
      background:rgba(0,0,0,.18);
      border:1px solid rgba(255,255,255,.05);
      border-radius:16px;
      padding:14px;
      overflow:auto;
      max-height:520px;
    }}
    @media (max-width: 1080px) {{
      .grade {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="pagina">
    <section class="topo">
      <h1>{dag_id_escape}</h1>
      <p class="sub">Painel técnico do pipeline de Machine Learning. Esta é uma shell HTML de fallback. Quando você criar o template definitivo, esta mesma rota continuará funcionando.</p>
      <div class="chips">
        <span class="chip">run_id: {run_id_escape or "-"}</span>
        <span class="chip">prefixo: {escape(CONFIGURACAO.url_prefixo)}</span>
      </div>
    </section>

    <section class="grade">
      <article class="card">
        <h2 style="margin:0 0 12px 0;">Resumo</h2>
        <div id="resumoPipeline" class="bloco">Carregando...</div>

        <h2 style="margin:18px 0 12px 0;">Tasks</h2>
        <div id="listaTasks" class="lista-tarefas"></div>
      </article>

      <article class="card">
        <h2 style="margin:0 0 12px 0;">Payload JSON</h2>
        <pre id="payloadJson">Carregando...</pre>
      </article>
    </section>
  </div>

  <script>
    const dagId = {dag_id!r};
    const runId = {run_id!r};
    const prefixo = {contexto["url_prefixo"]!r};
    const url = `${{prefixo}}/api/pipelines/${{encodeURIComponent(dagId)}}` + (runId ? `?run_id=${{encodeURIComponent(runId)}}` : "");

    function classeStatus(status) {{
      const valor = String(status || "").toLowerCase();
      if (["success", "successful", "sucesso"].includes(valor)) return "success";
      if (["failed", "failure", "erro"].includes(valor)) return "failed";
      if (["running", "queued"].includes(valor)) return "running";
      return "unknown";
    }}

    function textoSeguro(valor) {{
      return String(valor ?? "").replace(/[&<>"']/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[c]));
    }}

    async function carregarDetalhe() {{
      const resposta = await fetch(url, {{ credentials: "same-origin" }});
      if (!resposta.ok) {{
        throw new Error(`Falha ao carregar dashboard. HTTP ${{resposta.status}}`);
      }}

      const payload = await resposta.json();
      document.getElementById("payloadJson").textContent = JSON.stringify(payload, null, 2);

      const resumo = `
        <div><strong>Nome:</strong> ${{textoSeguro(payload.nome || payload.dag_id || "-")}}</div>
        <div style="margin-top:8px;"><strong>Status:</strong> <span class="badge ${{classeStatus(payload.status)}}">${{textoSeguro(payload.status || "unknown")}}</span></div>
        <div style="margin-top:8px;"><strong>Owner:</strong> ${{textoSeguro(payload.owner || "-")}}</div>
        <div style="margin-top:8px;"><strong>Agendamento:</strong> ${{textoSeguro(payload.agendamento || "-")}}</div>
        <div style="margin-top:8px;"><strong>Início:</strong> ${{textoSeguro(payload.inicio || "-")}}</div>
        <div style="margin-top:8px;"><strong>Fim:</strong> ${{textoSeguro(payload.fim || "-")}}</div>
      `;
      document.getElementById("resumoPipeline").innerHTML = resumo;

      const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
      const lista = document.getElementById("listaTasks");
      if (!tasks.length) {{
        lista.innerHTML = `<div class="bloco">Nenhuma task encontrada para esta execução.</div>`;
        return;
      }}

      lista.innerHTML = tasks.map((task) => `
        <div class="tarefa">
          <div class="tarefa-topo">
            <div>
              <div style="font-weight:800;">${{textoSeguro(task.task_id || task.id || "-")}}</div>
              <div style="color:var(--muted); margin-top:4px;">${{textoSeguro(task.nome_amigavel || task.descricao || "")}}</div>
            </div>
            <span class="badge ${{classeStatus(task.status)}}">${{textoSeguro(task.status || "unknown")}}</span>
          </div>
        </div>
      `).join("");
    }}

    carregarDetalhe().catch((erro) => {{
      console.error(erro);
      document.getElementById("resumoPipeline").textContent = erro.message || "Erro ao carregar resumo.";
      document.getElementById("payloadJson").textContent = erro.message || "Erro ao carregar payload.";
    }});
  </script>
</body>
</html>
"""


def _chamar_listagem_pipelines(dag_id: str | None, status: str | None, limite: int) -> dict[str, Any]:
    """Eu encapsulo a chamada de listagem para lidar com pequenas diferenças de assinatura."""
    if _LISTAR_PIPELINES is None:
        raise _erro_servico_nao_implementado("listar_pipelines_ml")

    try:
        payload = _LISTAR_PIPELINES(dag_id=dag_id, status=status, limite=limite)
    except TypeError:
        payload = _LISTAR_PIPELINES(dag_id=dag_id, status=status, limite=limite, apenas_ml=True)

    if not isinstance(payload, dict):
        raise ValueError("O serviço de listagem de pipelines precisa devolver um dicionário.")

    return _filtrar_payload_para_ml(payload)


def _chamar_dashboard_pipeline(dag_id: str, run_id: str | None) -> dict[str, Any]:
    """Eu encapsulo a leitura do dashboard principal do pipeline."""
    if _OBTER_DASHBOARD is None:
        raise _erro_servico_nao_implementado("obter_dashboard_pipeline_ml")

    try:
        payload = _OBTER_DASHBOARD(dag_id=dag_id, run_id=run_id)
    except TypeError:
        payload = _OBTER_DASHBOARD(dag_id=dag_id)

    if not isinstance(payload, dict):
        raise ValueError("O serviço de dashboard do pipeline precisa devolver um dicionário.")

    return payload


def _chamar_detalhe_task(dag_id: str, task_id: str, run_id: str | None) -> dict[str, Any]:
    """Eu encapsulo a leitura da task para manter a API desacoplada do service concreto."""
    if _OBTER_DETALHE_TASK is None:
        raise _erro_servico_nao_implementado("obter_detalhe_task_pipeline_ml")

    try:
        payload = _OBTER_DETALHE_TASK(dag_id=dag_id, task_id=task_id, run_id=run_id)
    except TypeError:
        payload = _OBTER_DETALHE_TASK(dag_id=dag_id, task_id=task_id)

    if not isinstance(payload, dict):
        raise ValueError("O serviço de detalhe da task precisa devolver um dicionário.")

    return payload


@app.get("/", response_class=HTMLResponse)
async def raiz() -> RedirectResponse:
    """Eu redireciono para a listagem principal do plugin."""
    return RedirectResponse(url=f".{CONFIGURACAO.rota_lista_html}")


@app.get(CONFIGURACAO.rota_health, response_class=JSONResponse)
async def health() -> dict[str, Any]:
    """Eu exponho um healthcheck simples do lado da API do plugin."""
    return {
        "status": "ok",
        "plugin": CONFIGURACAO.nome_plugin,
        "url_prefixo": CONFIGURACAO.url_prefixo,
        "roots_arquivos_permitidos": [str(item) for item in CONFIGURACAO.roots_arquivos_permitidos],
    }


@app.get(CONFIGURACAO.rota_lista_html, response_class=HTMLResponse)
async def lista_pipelines_html(request: Request) -> HTMLResponse:
    """Eu renderizo a tela HTML da lista de pipelines de Machine Learning."""
    if _nome_template_existe(CONFIGURACAO.nome_template_lista):
        return templates.TemplateResponse(
            CONFIGURACAO.nome_template_lista,
            {
                "request": request,
                "configuracao_frontend": obter_contexto_frontend(),
            },
        )

    return HTMLResponse(content=_renderizar_lista_fallback_html())


@app.get(CONFIGURACAO.rota_lista_api, response_class=JSONResponse)
async def lista_pipelines_api(
    dag_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limite: int = Query(default=CONFIGURACAO.limite_listagem_padrao, ge=1, le=500),
) -> dict[str, Any]:
    """Eu listo apenas DAGs de Machine Learning em formato JSON."""
    try:
        return _chamar_listagem_pipelines(dag_id=dag_id, status=status, limite=limite)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao listar pipelines de Machine Learning. Erro técnico: {str(exc)}",
        ) from exc


@app.get("/pipelines/{dag_id}", response_class=HTMLResponse)
async def detalhe_pipeline_html(
    request: Request,
    dag_id: str,
    run_id: str | None = Query(default=None),
) -> HTMLResponse:
    """Eu renderizo a tela HTML do pipeline selecionado."""
    if _nome_template_existe(CONFIGURACAO.nome_template_detalhe):
        return templates.TemplateResponse(
            CONFIGURACAO.nome_template_detalhe,
            {
                "request": request,
                "dag_id": dag_id,
                "run_id": run_id,
                "configuracao_frontend": obter_contexto_frontend(),
            },
        )

    return HTMLResponse(content=_renderizar_detalhe_fallback_html(dag_id=dag_id, run_id=run_id))


@app.get("/api/pipelines/{dag_id}", response_class=JSONResponse)
async def detalhe_pipeline_api(
    dag_id: str,
    run_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Eu devolvo o JSON principal do pipeline detalhado."""
    try:
        return _chamar_dashboard_pipeline(dag_id=dag_id, run_id=run_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao montar dashboard do pipeline '{dag_id}'. Erro técnico: {str(exc)}",
        ) from exc


@app.get("/api/pipelines/{dag_id}/tasks/{task_id}", response_class=JSONResponse)
async def detalhe_task_api(
    dag_id: str,
    task_id: str,
    run_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Eu devolvo o JSON detalhado de uma task específica."""
    try:
        return _chamar_detalhe_task(dag_id=dag_id, task_id=task_id, run_id=run_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Erro ao montar detalhe da task '{task_id}' da DAG '{dag_id}'. "
                f"Erro técnico: {str(exc)}"
            ),
        ) from exc


@app.get("/api/pipelines/{dag_id}/tasks/{task_id}/table-preview", response_class=JSONResponse)
async def table_preview_task_api(
    dag_id: str,
    task_id: str,
    run_id: str | None = Query(default=None),
    limite: int = Query(default=CONFIGURACAO.limite_preview_padrao, ge=1, le=100),
) -> dict[str, Any]:
    """Eu devolvo um preview da tabela associada a uma task, se o service existir."""
    if _OBTER_PREVIEW_TABELA_TASK is None:
        raise _erro_servico_nao_implementado("obter_preview_tabela_task_pipeline_ml")

    try:
        return _OBTER_PREVIEW_TABELA_TASK(
            dag_id=dag_id,
            task_id=task_id,
            run_id=run_id,
            limite=limite,
        )
    except TypeError:
        try:
            return _OBTER_PREVIEW_TABELA_TASK(
                dag_id=dag_id,
                task_id=task_id,
                limite=limite,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Erro ao carregar preview da tabela da task '{task_id}' "
                f"da DAG '{dag_id}'. Erro técnico: {str(exc)}"
            ),
        ) from exc


@app.get("/api/pipelines/{dag_id}/tasks/{task_id}/table-data", response_class=JSONResponse)
async def table_data_task_api(
    request: Request,
    dag_id: str,
    task_id: str,
    run_id: str | None = Query(default=None),
    pagina: int = Query(default=1, ge=1),
    tamanho_pagina: int = Query(default=CONFIGURACAO.tamanho_pagina_padrao, ge=1, le=200),
    ordenar_por: str | None = Query(default=None),
    direcao: str | None = Query(default="asc"),
    q: str | None = Query(default=None),
) -> dict[str, Any]:
    """Eu devolvo a tabela real paginada associada a uma task, se o service existir."""
    if _OBTER_TABELA_TASK is None:
        raise _erro_servico_nao_implementado("obter_tabela_paginada_task_pipeline_ml")

    try:
        return _OBTER_TABELA_TASK(
            dag_id=dag_id,
            task_id=task_id,
            run_id=run_id,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            texto_busca=q,
            ordenar_por=ordenar_por,
            direcao=direcao,
            filtros_categoricos={},
            filtros_datas_de={},
            filtros_datas_ate={},
        )
    except TypeError:
        """
        Eu caio aqui quando o service já faz dedução completa dos filtros internamente
        e não precisa que a API passe todos os parâmetros opcionais.
        """
        try:
            return _OBTER_TABELA_TASK(
                dag_id=dag_id,
                task_id=task_id,
                run_id=run_id,
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
                texto_busca=q,
                ordenar_por=ordenar_por,
                direcao=direcao,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Erro ao carregar tabela real da task '{task_id}' "
                f"da DAG '{dag_id}'. Erro técnico: {str(exc)}"
            ),
        ) from exc


@app.get(CONFIGURACAO.rota_tabela_preview_api, response_class=JSONResponse)
async def table_preview_api(
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    limite: int = Query(default=CONFIGURACAO.limite_preview_padrao, ge=1, le=100),
) -> dict[str, Any]:
    """Eu devolvo um preview rápido de uma tabela real usando conn_id/schema/tabela."""
    if _OBTER_PREVIEW_TABELA is None:
        raise _erro_servico_nao_implementado("obter_preview_tabela")

    try:
        return _OBTER_PREVIEW_TABELA(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
            limite=limite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao carregar preview da tabela '{schema}.{tabela}'. Erro técnico: {str(exc)}",
        ) from exc


@app.get(CONFIGURACAO.rota_tabela_dados_api, response_class=JSONResponse)
async def table_data_api(
    request: Request,
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    pagina: int = Query(default=1, ge=1),
    tamanho_pagina: int = Query(default=CONFIGURACAO.tamanho_pagina_padrao, ge=1, le=200),
    ordenar_por: str | None = Query(default=None),
    direcao: str | None = Query(default="asc"),
    q: str | None = Query(default=None),
) -> dict[str, Any]:
    """Eu devolvo dados paginados de tabela com filtros dinâmicos e seguros."""
    if _OBTER_METADADOS_TABELA is None:
        raise _erro_servico_nao_implementado("obter_metadados_tabela")

    if _LISTAR_TABELA_REAL is None:
        raise _erro_servico_nao_implementado("listar_tabela_real")

    try:
        metadados = _OBTER_METADADOS_TABELA(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
        )

        filtros_categoricos, filtros_datas_de, filtros_datas_ate = _extrair_filtros_http(
            request=request,
            filtros_relevantes=metadados["filtros_relevantes"],
        )

        return _LISTAR_TABELA_REAL(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            texto_busca=q,
            ordenar_por=ordenar_por,
            direcao=direcao,
            filtros_categoricos=filtros_categoricos,
            filtros_datas_de=filtros_datas_de,
            filtros_datas_ate=filtros_datas_ate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao carregar dados da tabela '{schema}.{tabela}'. Erro técnico: {str(exc)}",
        ) from exc


@app.get(CONFIGURACAO.rota_tabela_html, response_class=HTMLResponse)
async def tabela_html(
    request: Request,
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    pagina: int = Query(default=1, ge=1),
    tamanho_pagina: int = Query(default=CONFIGURACAO.tamanho_pagina_padrao, ge=1, le=200),
    ordenar_por: str | None = Query(default=None),
    direcao: str | None = Query(default="asc"),
    q: str | None = Query(default=None),
) -> HTMLResponse:
    """Eu renderizo a tela completa da tabela, com template se existir e fallback caso contrário."""
    if _OBTER_METADADOS_TABELA is None:
        raise _erro_servico_nao_implementado("obter_metadados_tabela")

    if _LISTAR_TABELA_REAL is None:
        raise _erro_servico_nao_implementado("listar_tabela_real")

    try:
        metadados = _OBTER_METADADOS_TABELA(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
        )

        filtros_categoricos, filtros_datas_de, filtros_datas_ate = _extrair_filtros_http(
            request=request,
            filtros_relevantes=metadados["filtros_relevantes"],
        )

        dados = _LISTAR_TABELA_REAL(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            texto_busca=q,
            ordenar_por=ordenar_por,
            direcao=direcao,
            filtros_categoricos=filtros_categoricos,
            filtros_datas_de=filtros_datas_de,
            filtros_datas_ate=filtros_datas_ate,
        )

        query_base = _montar_querystring_base_tabela(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
            request=request,
            filtros_relevantes=metadados["filtros_relevantes"],
            tamanho_pagina=tamanho_pagina,
            ordenar_por=dados.get("ordenar_por") or "",
            direcao=dados.get("direcao") or "asc",
            texto_busca=dados.get("texto_busca") or "",
        )

        if _nome_template_existe(CONFIGURACAO.nome_template_tabela):
            return templates.TemplateResponse(
                CONFIGURACAO.nome_template_tabela,
                {
                    "request": request,
                    "metadados": metadados,
                    "dados": dados,
                    "query_base": query_base,
                    "configuracao_frontend": obter_contexto_frontend(),
                },
            )

        colunas = dados.get("colunas") or []
        linhas = dados.get("linhas") or []
        html_tabela = [
            "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8' />",
            f"<title>{escape(schema)}.{escape(tabela)}</title>",
            "<style>",
            "body{font-family:Inter,Segoe UI,Arial,sans-serif;background:#0B1020;color:#E8EEFF;margin:0;padding:24px;}",
            ".card{background:#151F38;border:1px solid #26345C;border-radius:20px;padding:20px;}",
            "table{width:100%;border-collapse:collapse;margin-top:16px;font-size:14px;}",
            "th,td{border:1px solid #26345C;padding:10px;text-align:left;vertical-align:top;}",
            "th{background:#11182D;position:sticky;top:0;}",
            ".muted{color:#A8B3D1;}",
            "</style></head><body>",
            f"<div class='card'><h1 style='margin-top:0;'>{escape(schema)}.{escape(tabela)}</h1>",
            f"<div class='muted'>Total de linhas filtradas: {escape(str(dados.get('total_linhas', 0)))}</div>",
            "<table><thead><tr>",
        ]

        for coluna in colunas:
            html_tabela.append(f"<th>{escape(str(coluna))}</th>")

        html_tabela.append("</tr></thead><tbody>")

        for linha in linhas:
            html_tabela.append("<tr>")
            for coluna in colunas:
                html_tabela.append(f"<td>{escape(str(linha.get(coluna, '')))}</td>")
            html_tabela.append("</tr>")

        html_tabela.append("</tbody></table></div></body></html>")
        return HTMLResponse(content="".join(html_tabela))
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao renderizar tabela '{schema}.{tabela}'. Erro técnico: {str(exc)}",
        ) from exc


@app.get(CONFIGURACAO.rota_download)
async def arquivo_download(caminho: str = Query(...)) -> FileResponse:
    """Eu devolvo o arquivo para download seguro."""
    caminho_resolvido = _resolver_caminho_arquivo_seguro(caminho)

    return FileResponse(
        path=str(caminho_resolvido),
        filename=caminho_resolvido.name,
        media_type="application/octet-stream",
    )