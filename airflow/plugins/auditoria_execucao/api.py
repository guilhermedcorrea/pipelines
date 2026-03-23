from __future__ import annotations

import math
import re
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from auditoria_execucao.servico_auditoria import ServicoAuditoriaExecucao


BASE_DIR = Path(__file__).resolve().parent
PASTA_TEMPLATES = BASE_DIR / "templates"
PASTA_STATIC = BASE_DIR / "static"

app = FastAPI(title="Auditoria de Execução Airflow")

templates = Jinja2Templates(directory=str(PASTA_TEMPLATES))
app.mount("/static", StaticFiles(directory=str(PASTA_STATIC)), name="auditoria_static")

REGEX_IDENTIFICADOR_SQL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ROOTS_ARQUIVOS_PERMITIDOS = [
    Path("/opt/airflow/Artefatos").resolve(),
    Path("/opt/airflow/sharepoint_teste").resolve(),
    Path("/opt/airflow/Dados").resolve(),
]


def _validar_conexao(conexao_id: str) -> str:
    """
    Eu limito a visualização à conexão oficial da auditoria.
    """
    conexao_id_limpa = (conexao_id or "").strip()

    if conexao_id_limpa != ServicoAuditoriaExecucao.conn_id_sql:
        raise HTTPException(
            status_code=400,
            detail="Conexão inválida para visualização de tabela.",
        )

    return conexao_id_limpa


def _validar_identificador_sql(valor: str, nome_campo: str) -> str:
    """
    Eu valido banco, schema e tabela para impedir injeção em identificadores SQL.
    """
    valor_limpo = (valor or "").strip()

    if not valor_limpo:
        raise HTTPException(
            status_code=400,
            detail=f"O campo '{nome_campo}' é obrigatório.",
        )

    if not REGEX_IDENTIFICADOR_SQL.fullmatch(valor_limpo):
        raise HTTPException(
            status_code=400,
            detail=f"O campo '{nome_campo}' possui valor inválido.",
        )

    return valor_limpo


def _montar_nome_qualificado(
    banco: str | None,
    schema: str,
    tabela: str,
) -> str:
    """
    Eu monto o nome qualificado do objeto SQL Server.

    Casos suportados:
    - sem banco: [schema].[tabela]
    - com banco: [banco].[schema].[tabela]
    """
    schema_validado = _validar_identificador_sql(schema, "schema")
    tabela_validada = _validar_identificador_sql(tabela, "tabela")

    if banco and str(banco).strip():
        banco_validado = _validar_identificador_sql(str(banco).strip(), "banco")
        return f"[{banco_validado}].[{schema_validado}].[{tabela_validada}]"

    return f"[{schema_validado}].[{tabela_validada}]"




def _normalizar_valor_json(valor: Any) -> Any:
    """
    Eu converto tipos comuns vindos do SQL Server para formatos seguros em JSON.
    """
    if valor is None:
        return None

    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, datetime):
        return valor.isoformat(sep=" ")

    if isinstance(valor, date):
        return valor.isoformat()

    if isinstance(valor, time):
        return valor.isoformat()

    if isinstance(valor, bytes):
        try:
            return valor.decode("utf-8", errors="replace")
        except Exception:
            return str(valor)

    return valor


def _normalizar_linha(linha: dict[str, Any]) -> dict[str, Any]:
    """
    Eu normalizo todos os valores de uma linha.
    """
    return {chave: _normalizar_valor_json(valor) for chave, valor in linha.items()}


def _obter_total_linhas(nome_qualificado: str) -> int:
    """
    Eu conto o total de linhas do objeto.
    """
    sql = text(
        f"""
        SELECT COUNT_BIG(1) AS total_linhas
        FROM {nome_qualificado}
        """
    )

    engine = ServicoAuditoriaExecucao.obter_engine()

    try:
        with engine.connect() as conexao:
            resultado = conexao.execute(sql)
            linha = resultado.mappings().first()
            if not linha:
                return 0
            return int(linha.get("total_linhas") or 0)
    finally:
        engine.dispose()


def _obter_colunas_tabela(nome_qualificado: str) -> list[str]:
    """
    Eu descubro as colunas via TOP (0), sem ler dados desnecessários.
    """
    sql = text(
        f"""
        SELECT TOP (0) *
        FROM {nome_qualificado}
        """
    )

    engine = ServicoAuditoriaExecucao.obter_engine()

    try:
        with engine.connect() as conexao:
            resultado = conexao.execute(sql)
            return list(resultado.keys())
    finally:
        engine.dispose()


def _obter_amostra_tabela(nome_qualificado: str, limite: int) -> list[dict[str, Any]]:
    """
    Eu trago uma amostra pequena da tabela.
    """
    sql = text(
        f"""
        SELECT TOP ({int(limite)}) *
        FROM {nome_qualificado}
        """
    )

    engine = ServicoAuditoriaExecucao.obter_engine()

    try:
        with engine.connect() as conexao:
            resultado = conexao.execute(sql)
            linhas = resultado.mappings().all()
            return [_normalizar_linha(dict(linha)) for linha in linhas]
    finally:
        engine.dispose()


def _obter_linhas_paginadas(
    nome_qualificado: str,
    pagina: int,
    tamanho_pagina: int,
) -> list[dict[str, Any]]:
    """
    Eu trago linhas paginadas usando OFFSET/FETCH.

    Observação:
    Em SQL Server, OFFSET exige ORDER BY. Como o plugin não conhece a chave
    natural do objeto, uso ORDER BY (SELECT 1) para inspeção operacional.
    """
    offset = (pagina - 1) * tamanho_pagina

    sql = text(
        f"""
        SELECT *
        FROM {nome_qualificado}
        ORDER BY (SELECT 1)
        OFFSET :offset ROWS
        FETCH NEXT :tamanho_pagina ROWS ONLY
        """
    )

    parametros = {
        "offset": int(offset),
        "tamanho_pagina": int(tamanho_pagina),
    }

    engine = ServicoAuditoriaExecucao.obter_engine()

    try:
        with engine.connect() as conexao:
            resultado = conexao.execute(sql, parametros)
            linhas = resultado.mappings().all()
            return [_normalizar_linha(dict(linha)) for linha in linhas]
    finally:
        engine.dispose()


def _garantir_tabela_existe(nome_qualificado: str) -> None:
    """
    Eu valido se o objeto pode ser lido pela conexão atual.
    """
    sql = text(
        f"""
        SELECT TOP (0) *
        FROM {nome_qualificado}
        """
    )

    engine = ServicoAuditoriaExecucao.obter_engine()

    try:
        with engine.connect() as conexao:
            conexao.execute(sql)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Não consegui acessar o objeto {nome_qualificado}. "
                f"Verifique se ele existe nessa conexão e se há permissão de leitura. "
                f"Erro técnico: {str(exc)}"
            ),
        ) from exc
    finally:
        engine.dispose()


def _resolver_caminho_arquivo_seguro(caminho: str) -> Path:
    """
    Eu libero download apenas para arquivos dentro de roots autorizadas.
    """
    if not caminho or not caminho.strip():
        raise HTTPException(status_code=400, detail="Caminho de arquivo inválido.")

    caminho_resolvido = Path(caminho).expanduser().resolve()

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


@app.get("/health", response_class=JSONResponse)
async def health() -> dict:
    return {"status": "ok"}


@app.get("/painel", response_class=HTMLResponse)
async def painel(
    request: Request,
    dag_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limite: int = Query(default=50),
):
    execucoes = ServicoAuditoriaExecucao.listar_execucoes_recentes(
        dag_id=dag_id,
        status=status,
        limite=limite,
    )

    total = len(execucoes)
    total_success = sum(1 for item in execucoes if item.get("status") == "SUCCESS")
    total_failed = sum(1 for item in execucoes if item.get("status") == "FAILED")
    total_running = sum(1 for item in execucoes if item.get("status") == "RUNNING")

    return templates.TemplateResponse(
        "painel.html",
        {
            "request": request,
            "execucoes": execucoes,
            "filtro_dag_id": dag_id or "",
            "filtro_status": status or "",
            "limite": limite,
            "cards": {
                "total": total,
                "success": total_success,
                "failed": total_failed,
                "running": total_running,
            },
        },
    )


@app.get("/dag/{dag_id}/run/{run_id}", response_class=HTMLResponse)
async def detalhe_run(request: Request, dag_id: str, run_id: str):
    detalhe = ServicoAuditoriaExecucao.obter_detalhe_run(dag_id=dag_id, run_id=run_id)

    return templates.TemplateResponse(
        "detalhe_run.html",
        {
            "request": request,
            "dag": detalhe.get("dag"),
            "tasks": detalhe.get("tasks", []),
        },
    )


@app.get("/api/dag/{dag_id}/run/{run_id}", response_class=JSONResponse)
async def detalhe_run_api(dag_id: str, run_id: str):
    return ServicoAuditoriaExecucao.obter_detalhe_run(dag_id=dag_id, run_id=run_id)


@app.get("/api/tabela/preview", response_class=JSONResponse)
async def preview_tabela(
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    limite: int = Query(default=20, ge=1, le=100),
):
    """
    Eu retorno uma amostra da tabela para o modal de preview.
    """
    conexao_validada = _validar_conexao(conexao_id)
    nome_qualificado = _montar_nome_qualificado(banco, schema, tabela)

    _garantir_tabela_existe(nome_qualificado)

    try:
        colunas = _obter_colunas_tabela(nome_qualificado)
        linhas = _obter_amostra_tabela(nome_qualificado, limite)
        total_linhas = _obter_total_linhas(nome_qualificado)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Erro ao carregar preview da tabela {nome_qualificado}. "
                f"Erro técnico: {str(exc)}"
            ),
        ) from exc

    return {
        "conexao_id": conexao_validada,
        "banco": banco,
        "schema": schema,
        "tabela": tabela,
        "limite_amostra": limite,
        "total_linhas": total_linhas,
        "colunas": colunas,
        "linhas": linhas,
    }


@app.get("/api/tabela/dados", response_class=JSONResponse)
async def dados_tabela_paginada(
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    pagina: int = Query(default=1, ge=1),
    tamanho_pagina: int = Query(default=50, ge=1, le=200),
):
    """
    Eu retorno linhas paginadas da tabela.
    """
    conexao_validada = _validar_conexao(conexao_id)
    nome_qualificado = _montar_nome_qualificado(banco, schema, tabela)

    _garantir_tabela_existe(nome_qualificado)

    try:
        total_linhas = _obter_total_linhas(nome_qualificado)
        colunas = _obter_colunas_tabela(nome_qualificado)
        linhas = _obter_linhas_paginadas(
            nome_qualificado=nome_qualificado,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Erro ao carregar dados paginados da tabela {nome_qualificado}. "
                f"Erro técnico: {str(exc)}"
            ),
        ) from exc

    total_paginas = max(1, math.ceil(total_linhas / tamanho_pagina)) if tamanho_pagina else 1

    return {
        "conexao_id": conexao_validada,
        "banco": banco,
        "schema": schema,
        "tabela": tabela,
        "pagina": pagina,
        "tamanho_pagina": tamanho_pagina,
        "total_linhas": total_linhas,
        "total_paginas": total_paginas,
        "colunas": colunas,
        "linhas": linhas,
    }


@app.get("/tabela", response_class=HTMLResponse)
async def visualizar_tabela(
    request: Request,
    conexao_id: str = Query(...),
    schema: str = Query(...),
    tabela: str = Query(...),
    banco: str | None = Query(default=None),
    pagina: int = Query(default=1, ge=1),
    tamanho_pagina: int = Query(default=50, ge=1, le=200),
):
    """
    Eu renderizo a tela completa da tabela com paginação.
    """
    conexao_validada = _validar_conexao(conexao_id)
    nome_qualificado = _montar_nome_qualificado(banco, schema, tabela)

    _garantir_tabela_existe(nome_qualificado)

    try:
        total_linhas = _obter_total_linhas(nome_qualificado)
        colunas = _obter_colunas_tabela(nome_qualificado)
        linhas = _obter_linhas_paginadas(
            nome_qualificado=nome_qualificado,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Erro ao carregar a visualização completa da tabela {nome_qualificado}. "
                f"Erro técnico: {str(exc)}"
            ),
        ) from exc

    total_paginas = max(1, math.ceil(total_linhas / tamanho_pagina)) if tamanho_pagina else 1

    if pagina > total_paginas and total_paginas > 0:
        raise HTTPException(
            status_code=400,
            detail="Página solicitada está fora do intervalo disponível.",
        )

    pagina_anterior = pagina - 1 if pagina > 1 else None
    proxima_pagina = pagina + 1 if pagina < total_paginas else None

    return templates.TemplateResponse(
        "tabela_dados.html",
        {
            "request": request,
            "conexao_id": conexao_validada,
            "banco": banco,
            "schema": schema,
            "tabela": tabela,
            "colunas": colunas,
            "linhas": linhas,
            "pagina": pagina,
            "tamanho_pagina": tamanho_pagina,
            "total_linhas": total_linhas,
            "total_paginas": total_paginas,
            "pagina_anterior": pagina_anterior,
            "proxima_pagina": proxima_pagina,
            "nome_qualificado": nome_qualificado,
        },
    )


@app.get("/arquivo/download")
async def download_arquivo(caminho: str = Query(...)):
    caminho_resolvido = _resolver_caminho_arquivo_seguro(caminho)

    return FileResponse(
        path=str(caminho_resolvido),
        filename=caminho_resolvido.name,
        media_type="application/octet-stream",
    )