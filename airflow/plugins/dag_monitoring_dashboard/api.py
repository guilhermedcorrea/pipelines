from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dag_monitoring_dashboard.services.dag_service import (
    listar_dags_auditoria,
    obter_dashboard_dag,
)
from dag_monitoring_dashboard.services.sql_preview_service import (
    listar_tabela_real,
    obter_metadados_tabela,
    obter_preview_tabela,
)


BASE_DIR = Path(__file__).resolve().parent
PASTA_TEMPLATES = BASE_DIR / "templates"
PASTA_STATIC = BASE_DIR / "static"

ROOTS_ARQUIVOS_PERMITIDOS = [
    Path("/opt/airflow/Artefatos").resolve(),
    Path("/opt/airflow/sharepoint_teste").resolve(),
    Path("/opt/airflow/Dados").resolve(),
    Path("/opt/airflow").resolve(),
]

app = FastAPI(title="Dag Monitoring Dashboard")
templates = Jinja2Templates(directory=str(PASTA_TEMPLATES))

if PASTA_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(PASTA_STATIC)), name="dag_monitoring_static")


def criar_app_fastapi() -> FastAPI:
    """Eu devolvo a aplicação FastAPI para o plugin do Airflow."""
    return app


def _renderizar_dashboard_html(dag_id: str, run_id: str | None) -> str:
    """Eu leio o dashboard.html e substituo os placeholders estáticos."""
    caminho_template = PASTA_TEMPLATES / "dashboard.html"

    if not caminho_template.exists():
        raise HTTPException(status_code=500, detail="Template dashboard.html não encontrado.")

    html = caminho_template.read_text(encoding="utf-8")
    html = html.replace("__DAG_ID__", str(dag_id or ""))
    html = html.replace("__RUN_ID__", str(run_id or ""))

    return html


def _resolver_caminho_arquivo_seguro(caminho: str) -> Path:
    """Eu libero download apenas para arquivos dentro de roots autorizadas."""
    caminho_limpo = str(caminho or "").strip()
    if not caminho_limpo:
        raise HTTPException(status_code=400, detail="Caminho de arquivo inválido.")

    caminho_resolvido = Path(caminho_limpo).expanduser().resolve()

    permitido = any(
        raiz == caminho_resolvido or raiz in caminho_resolvido.parents
        for raiz in ROOTS_ARQUIVOS_PERMITIDOS
    )

    if not permitido:
        raise HTTPException(
            status_code=403,
            detail="Arquivo fora das pastas permitidas para download.",
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


def _renderizar_lista_html() -> str:
    """
    Eu renderizo a tela HTML da listagem.

    Regra de negócio desta rota:
    - esta tela é somente a lista
    - não misturo cards de execução aqui
    - clique na linha abre o detalhe da execução
    """
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Dag Monitoring - Lista</title>
  <style>
    :root{
      --bg:#f5f7fb;
      --card:#ffffff;
      --border:#dbe3ef;
      --text:#1f2937;
      --muted:#64748b;
      --primary:#1e3a8a;
      --primary-soft:#e8eefc;
      --success:#15803d;
      --warning:#b45309;
      --danger:#b91c1c;
      --shadow:0 8px 24px rgba(15,23,42,.08);
      --radius:16px;
    }

    *{ box-sizing:border-box; }

    body{
      margin:0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background:var(--bg);
      color:var(--text);
    }

    .pagina{
      max-width: 1680px;
      margin: 0 auto;
      padding: 20px;
    }

    .topo{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:var(--radius);
      padding:20px;
      box-shadow:var(--shadow);
      margin-bottom:16px;
    }

    .topo h1{
      margin:0;
      font-size:30px;
      color:var(--primary);
      line-height:1.1;
    }

    .topo p{
      margin:8px 0 0 0;
      color:var(--muted);
      font-size:15px;
      font-weight:700;
    }

    .card{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:var(--radius);
      box-shadow:var(--shadow);
      overflow:hidden;
      margin-bottom:16px;
    }

    .card-header{
      padding:16px 18px;
      border-bottom:1px solid var(--border);
      background:linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
    }

    .card-header h2{
      margin:0;
      font-size:20px;
      color:var(--primary);
    }

    .card-header p{
      margin:6px 0 0 0;
      font-size:13px;
      color:var(--muted);
      font-weight:700;
    }

    .card-body{
      padding:16px 18px 18px 18px;
    }

    .filtros{
      display:grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap:12px;
      align-items:end;
    }

    .campo{
      grid-column: span 4;
      min-width:0;
    }

    .campo-sm{
      grid-column: span 2;
    }

    .campo-lg{
      grid-column: span 6;
    }

    label{
      display:block;
      margin-bottom:6px;
      font-size:12px;
      font-weight:1000;
      color:var(--muted);
      text-transform:uppercase;
      letter-spacing:.04em;
    }

    input[type="text"],
    select{
      width:100%;
      border:1px solid var(--border);
      background:#fff;
      border-radius:12px;
      padding:10px 12px;
      font-size:13px;
      font-weight:800;
      color:var(--text);
      outline:none;
    }

    input[type="text"]:focus,
    select:focus{
      border-color:rgba(30,58,138,.35);
      box-shadow:0 0 0 4px rgba(30,58,138,.08);
    }

    .acoes{
      display:flex;
      gap:10px;
      flex-wrap:wrap;
      align-items:center;
    }

    .btn{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      border:none;
      border-radius:12px;
      padding:10px 14px;
      font-size:13px;
      font-weight:1000;
      cursor:pointer;
      text-decoration:none;
    }

    .btn-primary{
      background:var(--primary);
      color:#fff;
    }

    .btn-secondary{
      background:#eef2f7;
      color:#334155;
      border:1px solid var(--border);
    }

    .resumo{
      display:flex;
      gap:10px;
      flex-wrap:wrap;
      margin-bottom:14px;
    }

    .chip{
      display:inline-flex;
      align-items:center;
      gap:6px;
      padding:8px 12px;
      background:var(--primary-soft);
      color:var(--primary);
      border:1px solid rgba(30,58,138,.15);
      border-radius:999px;
      font-size:12px;
      font-weight:900;
    }

    .overflow-x{
      width:100%;
      overflow-x:auto;
      -webkit-overflow-scrolling:touch;
    }

    .tbl{
      width:100%;
      min-width:1280px;
      border-collapse:separate;
      border-spacing:0;
      background:#fff;
      border:1px solid var(--border);
      border-radius:14px;
      overflow:hidden;
      table-layout:auto;
    }

    .tbl thead th{
      text-align:left;
      background:#f7fbff;
      color:#334155;
      border-bottom:1px solid var(--border);
      padding:12px 10px;
      font-size:12px;
      font-weight:1000;
      white-space:nowrap;
    }

    .tbl tbody td{
      border-bottom:1px solid rgba(15,23,42,.06);
      padding:10px 10px;
      font-size:13px;
      vertical-align:top;
      white-space:nowrap;
    }

    .tbl tbody tr{
      cursor:pointer;
    }

    .tbl tbody tr:hover{
      background:rgba(30,58,138,.035);
    }

    .link-detalhe{
      color:var(--primary);
      font-weight:1000;
      text-decoration:none;
    }

    .status{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      border-radius:999px;
      padding:6px 10px;
      font-size:12px;
      font-weight:1000;
      width:max-content;
    }

    .status.success{
      background:#e7f7ed;
      color:var(--success);
    }

    .status.failed{
      background:#fdecec;
      color:var(--danger);
    }

    .status.running,
    .status.queued,
    .status.scheduled{
      background:#fff4e5;
      color:var(--warning);
    }

    .status.default{
      background:#eef2f7;
      color:#475569;
    }

    .vazio{
      padding:26px;
      text-align:center;
      font-size:14px;
      font-weight:900;
      color:var(--muted);
    }

    .erro{
      padding:16px;
      border:1px solid #fecaca;
      background:#fff1f2;
      color:#991b1b;
      border-radius:14px;
      font-size:14px;
      font-weight:900;
      display:none;
      margin-bottom:14px;
    }

    .loading{
      font-size:14px;
      font-weight:900;
      color:var(--muted);
    }

    @media (max-width: 1200px){
      .campo{ grid-column: span 6; }
      .campo-sm{ grid-column: span 3; }
      .campo-lg{ grid-column: span 12; }
    }

    @media (max-width: 800px){
      .campo,
      .campo-sm,
      .campo-lg{
        grid-column: span 12;
      }

      .tbl{
        min-width:960px;
      }
    }
  </style>
</head>
<body>
  <div class="pagina">
    <div class="topo">
      <h1>Dag Monitoring</h1>
      <p>Lista de execuções reais das DAGs monitoradas</p>
    </div>

    <div id="erro-box" class="erro"></div>

    <div class="card">
      <div class="card-header">
        <h2>Filtros</h2>
        <p>Pesquise por DAG, filtre por status e ajuste o limite retornado</p>
      </div>
      <div class="card-body">
        <form id="form-filtros" class="filtros">
          <div class="campo campo-lg">
            <label for="dag_id">DAG</label>
            <input id="dag_id" name="dag_id" type="text" placeholder="Pesquisar dag_id..." />
          </div>

          <div class="campo campo-sm">
            <label for="status">Status</label>
            <select id="status" name="status">
              <option value="">Todos</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
              <option value="running">Running</option>
              <option value="queued">Queued</option>
              <option value="scheduled">Scheduled</option>
            </select>
          </div>

          <div class="campo campo-sm">
            <label for="limite">Limite</label>
            <select id="limite" name="limite">
              <option value="20">20</option>
              <option value="50" selected>50</option>
              <option value="100">100</option>
              <option value="200">200</option>
            </select>
          </div>

          <div class="campo campo-sm">
            <div class="acoes">
              <button type="submit" class="btn btn-primary">Aplicar</button>
              <button type="button" id="btn-limpar" class="btn btn-secondary">Limpar</button>
            </div>
          </div>
        </form>
      </div>
    </div>

    <div class="resumo">
      <span class="chip" id="resumo-total">Total: 0</span>
      <span class="chip" id="resumo-sucesso">Sucesso: 0</span>
      <span class="chip" id="resumo-falha">Falha: 0</span>
      <span class="chip" id="resumo-execucao">Executando/Fila: 0</span>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>Lista de execuções</h2>
        <p>Clique em uma linha para abrir o detalhe da execução</p>
      </div>
      <div class="card-body">
        <div id="loading-lista" class="loading">Carregando execuções...</div>

        <div class="overflow-x">
          <table class="tbl">
            <thead>
              <tr>
                <th>DAG</th>
                <th>Run ID</th>
                <th>Status</th>
                <th>Run Type</th>
                <th>Execution Date</th>
                <th>Start Date</th>
                <th>End Date</th>
                <th>Duração (s)</th>
                <th>Ação</th>
              </tr>
            </thead>
            <tbody id="lista-body">
              <tr>
                <td colspan="9" class="vazio">Carregando execuções...</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <script>
    const estadoLista = {
      itens: [],
    };

    function escaparHtml(valor) {
      return String(valor ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function classeStatus(status) {
      const texto = String(status || "").toLowerCase();

      if (texto === "success") return "success";
      if (texto === "failed" || texto === "upstream_failed") return "failed";
      if (texto === "running" || texto === "queued" || texto === "scheduled") return "running";
      return "default";
    }

    function humanizarStatus(status) {
      const texto = String(status || "").toLowerCase();

      if (!texto) return "Sem status";
      if (texto === "success") return "Sucesso";
      if (texto === "failed") return "Falha";
      if (texto === "upstream_failed") return "Falha upstream";
      if (texto === "running") return "Executando";
      if (texto === "queued") return "Na fila";
      if (texto === "scheduled") return "Agendada";
      return status;
    }

    function montarUrl(base, parametros) {
      const url = new URL(base, window.location.href);

      Object.entries(parametros || {}).forEach(([chave, valor]) => {
        if (valor === undefined || valor === null || String(valor).trim() === "") {
          url.searchParams.delete(chave);
          return;
        }
        url.searchParams.set(chave, String(valor));
      });

      return url.toString();
    }

    function abrirDetalhe(item) {
      const url = montarUrl("./detalhe", {
        dag_id: item.dag_id,
        run_id: item.run_id,
      });

      window.location.href = url;
    }

    function mostrarErro(mensagem) {
      const box = document.getElementById("erro-box");
      if (!box) return;

      box.textContent = mensagem || "Erro inesperado.";
      box.style.display = "block";
    }

    function esconderErro() {
      const box = document.getElementById("erro-box");
      if (!box) return;

      box.textContent = "";
      box.style.display = "none";
    }

    function atualizarResumo(itens) {
      const sucesso = itens.filter(item => String(item.status || "").toLowerCase() === "success").length;
      const falha = itens.filter(item => ["failed", "upstream_failed"].includes(String(item.status || "").toLowerCase())).length;
      const execucao = itens.filter(item => ["running", "queued", "scheduled"].includes(String(item.status || "").toLowerCase())).length;

      document.getElementById("resumo-total").textContent = `Total: ${itens.length}`;
      document.getElementById("resumo-sucesso").textContent = `Sucesso: ${sucesso}`;
      document.getElementById("resumo-falha").textContent = `Falha: ${falha}`;
      document.getElementById("resumo-execucao").textContent = `Executando/Fila: ${execucao}`;
    }

    function renderizarTabela(itens) {
      const body = document.getElementById("lista-body");
      const loading = document.getElementById("loading-lista");

      if (loading) {
        loading.style.display = "none";
      }

      if (!body) return;

      if (!Array.isArray(itens) || itens.length === 0) {
        body.innerHTML = `
          <tr>
            <td colspan="9" class="vazio">Nenhuma execução encontrada para os filtros atuais.</td>
          </tr>
        `;
        return;
      }

      body.innerHTML = itens.map(item => `
        <tr data-dag-id="${escaparHtml(item.dag_id || "")}" data-run-id="${escaparHtml(item.run_id || "")}">
          <td>${escaparHtml(item.dag_id || "-")}</td>
          <td>${escaparHtml(item.run_id || "-")}</td>
          <td><span class="status ${classeStatus(item.status)}">${escaparHtml(humanizarStatus(item.status))}</span></td>
          <td>${escaparHtml(item.run_type || "-")}</td>
          <td>${escaparHtml(item.execution_date || "-")}</td>
          <td>${escaparHtml(item.start_date || "-")}</td>
          <td>${escaparHtml(item.end_date || "-")}</td>
          <td>${escaparHtml(item.duration_seconds ?? "-")}</td>
          <td><a class="link-detalhe" href="./detalhe?dag_id=${encodeURIComponent(item.dag_id || "")}&run_id=${encodeURIComponent(item.run_id || "")}">Abrir</a></td>
        </tr>
      `).join("");

      body.querySelectorAll("tr[data-dag-id]").forEach(linha => {
        linha.addEventListener("click", function(ev) {
          const alvo = ev.target;
          if (alvo && alvo.tagName === "A") {
            return;
          }

          abrirDetalhe({
            dag_id: linha.dataset.dagId,
            run_id: linha.dataset.runId,
          });
        });
      });
    }

    async function carregarLista() {
      esconderErro();

      const dagId = document.getElementById("dag_id")?.value || "";
      const status = document.getElementById("status")?.value || "";
      const limite = document.getElementById("limite")?.value || "50";

      const url = montarUrl("./api/lista", {
        dag_id: dagId,
        status: status,
        limite: limite,
      });

      const resposta = await fetch(url, {
        headers: { "Accept": "application/json" }
      });

      if (!resposta.ok) {
        const texto = await resposta.text();
        throw new Error(texto || "Erro ao carregar lista de execuções.");
      }

      const payload = await resposta.json();
      const itens = Array.isArray(payload.itens) ? payload.itens : [];

      estadoLista.itens = itens;

      atualizarResumo(itens);
      renderizarTabela(itens);
    }

    function configurarEventos() {
      const form = document.getElementById("form-filtros");
      const btnLimpar = document.getElementById("btn-limpar");

      if (form) {
        form.addEventListener("submit", async function(ev) {
          ev.preventDefault();
          try {
            const loading = document.getElementById("loading-lista");
            if (loading) {
              loading.style.display = "block";
              loading.textContent = "Carregando execuções...";
            }
            await carregarLista();
          } catch (erro) {
            mostrarErro(erro.message || "Erro ao carregar lista.");
          }
        });
      }

      if (btnLimpar) {
        btnLimpar.addEventListener("click", async function() {
          document.getElementById("dag_id").value = "";
          document.getElementById("status").value = "";
          document.getElementById("limite").value = "50";

          try {
            const loading = document.getElementById("loading-lista");
            if (loading) {
              loading.style.display = "block";
              loading.textContent = "Carregando execuções...";
            }
            await carregarLista();
          } catch (erro) {
            mostrarErro(erro.message || "Erro ao limpar filtros.");
          }
        });
      }
    }

    document.addEventListener("DOMContentLoaded", async function() {
      try {
        configurarEventos();
        await carregarLista();
      } catch (erro) {
        mostrarErro(erro.message || "Erro inesperado ao carregar a lista.");
      }
    });
  </script>
</body>
</html>
    """


@app.get("/", response_class=HTMLResponse)
async def raiz():
    """
    Eu mando o usuário para a tela HTML de lista.
    """
    return RedirectResponse(url="./lista")


@app.get("/health", response_class=JSONResponse)
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/lista", response_class=HTMLResponse)
async def lista_html():
    """
    Eu renderizo a página visual da listagem.
    """
    return HTMLResponse(content=_renderizar_lista_html())


@app.get("/api/lista", response_class=JSONResponse)
async def lista_execucoes_api(
    dag_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limite: int = Query(default=50),
):
    """
    Eu listo execuções reais da auditoria em JSON.
    """
    try:
        return listar_dags_auditoria(
            dag_id=dag_id,
            status=status,
            limite=limite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao listar execuções da auditoria. Erro técnico: {str(exc)}",
        ) from exc


@app.get("/detalhe", response_class=HTMLResponse)
async def detalhe_html(
    dag_id: str = Query(...),
    run_id: str | None = Query(default=None),
):
    """Eu renderizo a tela principal do dashboard detalhado."""
    html = _renderizar_dashboard_html(dag_id=dag_id, run_id=run_id)
    return HTMLResponse(content=html)


@app.get("/api", response_class=JSONResponse)
async def detalhe_api(
    dag_id: str = Query(...),
    run_id: str | None = Query(default=None),
):
    """Eu devolvo o JSON principal da DAG para o dashboard front-end."""
    try:
        return obter_dashboard_dag(dag_id=dag_id, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao montar dashboard da DAG {dag_id}. Erro técnico: {str(exc)}",
        ) from exc


@app.get("/api/table-preview", response_class=JSONResponse)
async def table_preview(
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    limite: int = Query(default=20, ge=1, le=100),
):
    """Eu devolvo uma amostra rápida da tabela para preview no modal."""
    try:
        return obter_preview_tabela(
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
            detail=f"Erro ao carregar preview da tabela {schema}.{tabela}. Erro técnico: {str(exc)}",
        ) from exc


@app.get("/api/table-data", response_class=JSONResponse)
async def table_data(
    request: Request,
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    pagina: int = Query(default=1, ge=1),
    tamanho_pagina: int = Query(default=50, ge=1, le=200),
    ordenar_por: str | None = Query(default=None),
    direcao: str | None = Query(default="asc"),
    q: str | None = Query(default=None),
):
    """Eu devolvo dados paginados com filtros dinâmicos em JSON."""
    try:
        metadados = obter_metadados_tabela(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
        )

        filtros_categoricos, filtros_datas_de, filtros_datas_ate = _extrair_filtros_http(
            request=request,
            filtros_relevantes=metadados["filtros_relevantes"],
        )

        return listar_tabela_real(
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
            detail=f"Erro ao carregar dados da tabela {schema}.{tabela}. Erro técnico: {str(exc)}",
        ) from exc


@app.get("/tabela", response_class=HTMLResponse)
async def tabela_html(
    request: Request,
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    pagina: int = Query(default=1, ge=1),
    tamanho_pagina: int = Query(default=50, ge=1, le=200),
    ordenar_por: str | None = Query(default=None),
    direcao: str | None = Query(default="asc"),
    q: str | None = Query(default=None),
):
    """Eu renderizo a tela completa da tabela com paginação e filtros relevantes."""
    try:
        metadados = obter_metadados_tabela(
            conn_id=conexao_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
        )

        filtros_categoricos, filtros_datas_de, filtros_datas_ate = _extrair_filtros_http(
            request=request,
            filtros_relevantes=metadados["filtros_relevantes"],
        )

        dados = listar_tabela_real(
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
            ordenar_por=dados["ordenar_por"],
            direcao=dados["direcao"],
            texto_busca=dados["texto_busca"],
        )

        return templates.TemplateResponse(
            "tabela_dados.html",
            {
                "request": request,
                "metadados": metadados,
                "dados": dados,
                "query_base": query_base,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao renderizar tabela {schema}.{tabela}. Erro técnico: {str(exc)}",
        ) from exc


@app.get("/arquivo/download")
async def arquivo_download(caminho: str = Query(...)):
    """Eu devolvo o arquivo para download seguro."""
    caminho_resolvido = _resolver_caminho_arquivo_seguro(caminho)

    return FileResponse(
        path=str(caminho_resolvido),
        filename=caminho_resolvido.name,
        media_type="application/octet-stream",
    )