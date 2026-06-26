import base64
import json
import logging
import os
import unicodedata
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pendulum
from sqlalchemy import text

try:
    import requests
except ImportError:
    requests = None

try:
    from airflow.decorators import dag, task
    from airflow.operators.python import get_current_context
except ImportError:
    from airflow.sdk import dag, task, get_current_context

from SqlServer import HookSqlServer


TABELA_WEBHOOK_EVENTO_D4 = "[Integracao].[Silver].[FatoContratoD4WebhookEvento]"
TABELA_CONTRATO_D4 = "[Integracao].[Silver].[FatoContratoD4]"
TABELA_SIGNATARIO_D4 = "[Integracao].[Silver].[FatoContratoD4Signatario]"
TABELA_HISTORICO_D4 = "[Integracao].[Silver].[DimHistoricoContratosD4]"
TABELA_CONTROLE_CONTRATOS = "[Integracao].[Silver].[FatoControleContratosEuromidia]"
TABELA_CONTROLE_CONTRATOS_ITENS = "[Integracao].[Silver].[FatoControleContratosItensEuromidia]"
TABELA_STATUS_CONTRATOS = "[Integracao].[Silver].[DimStatusContratos]"
TABELA_SOLICITACAO_CONTRATO = "[Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]"
TABELA_CONTRATO_CARD = "[Integracao].[Silver].[FatoContratoCardEuromidia]"

ID_EMPRESA_PROPRIETARIA_EUROMIDIA = 3

ID_STATUS_D4_PROCESSANDO = 1
ID_STATUS_D4_AGUARDANDO_SIGNATARIOS = 2
ID_STATUS_D4_AGUARDANDO_ASSINATURAS = 3
ID_STATUS_D4_FINALIZADO = 4
ID_STATUS_D4_ARQUIVADO = 5
ID_STATUS_D4_CANCELADO = 6
ID_STATUS_D4_EDITANDO = 7

ID_STATUS_CONTRATO_DOCUMENTO_GERADO = 3
ID_STATUS_CONTRATO_PENDENTE_ENVIO = 4
ID_STATUS_CONTRATO_ENVIADO_ASSINATURA = 5
ID_STATUS_CONTRATO_EM_ASSINATURA = 6
ID_STATUS_CONTRATO_ATIVO = 7
ID_STATUS_CONTRATO_CONCLUIDO = 8
ID_STATUS_CONTRATO_CANCELADO = 9


TABELA_ARQUIVOS_CONTRATOS = "[Integracao].[Silver].[FatoArquivosContratosEuromidia]"


def env_bool(nome_variavel: str, padrao: str = "1") -> bool:
    valor = str(os.getenv(nome_variavel, padrao) or padrao).strip().lower()
    return valor in {"1", "true", "sim", "yes", "y", "on"}


def pasta_pdf_local_contrato_d4() -> Path:
    """Pasta física compartilhada onde os PDFs de contratos D4Sign ficam salvos."""
    pasta = str(
        os.getenv("D4SIGN_PDF_LOCAL_PASTA_CONTRATO")
        or "/home/euromidia/projetos/pipelines/FlaskApp/Contratos/Euromidia/Anexos/Contrato"
    ).strip()
    return Path(pasta)


def url_anexo_contrato_pdf(nome_arquivo: str) -> str:
    return f"Contrato/{Path(str(nome_arquivo or '')).name}"


def limpar_nome_base_arquivo(valor: Any) -> str:
    texto = str(valor or "").strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace("/", "_").replace("\\", "_")
    texto = "".join(c if c.isalnum() or c in {"_", "-", "."} else "_" for c in texto)
    texto = "_".join(parte for parte in texto.split("_") if parte)
    if not texto:
        texto = "contrato_d4sign"
    return texto[:120]


def uuid_d4_valido(valor: Any) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    # UUID D4Sign costuma vir com hífen, mas aceito texto seguro para não perder documento antigo.
    texto = texto.replace(" ", "")
    return texto[:100]


def montar_nome_pdf_local_contrato_d4(contrato: dict[str, Any], nome_documento_d4: str | None = None) -> str | None:
    id_controle = converter_int(contrato.get("IDFatoControleContratosEuromidia"))
    id_d4 = converter_int(contrato.get("IDFatoContratoD4"))
    uuid_doc = uuid_d4_valido(contrato.get("UUIDDocumentoD4"))
    if not id_controle or not id_d4 or not uuid_doc:
        return None
    nome_base = limpar_nome_base_arquivo(nome_documento_d4 or contrato.get("NomeDocumentoD4") or "contrato_d4sign")
    return f"{id_controle}_D4_{id_d4}_{uuid_doc}_{nome_base}.pdf"


def validar_bytes_pdf(dados_pdf: bytes) -> None:
    if not dados_pdf:
        raise RuntimeError("Download D4Sign retornou arquivo vazio.")
    if not bytes(dados_pdf[:5]).startswith(b"%PDF-"):
        raise RuntimeError(f"Download D4Sign não retornou PDF válido. Início={bytes(dados_pdf[:80])!r}")


def caminho_pdf_valido(caminho: Path | None) -> bool:
    try:
        if caminho is None:
            return False
        caminho = Path(caminho)
        if not caminho.exists() or not caminho.is_file() or caminho.stat().st_size <= 0:
            return False
        with open(caminho, "rb") as arquivo:
            return arquivo.read(5).startswith(b"%PDF-")
    except Exception:
        return False


def obter_credenciais_d4sign_pdf() -> tuple[str, str]:
    token_api = str(os.getenv("TOKEN_D4SIGN") or "").strip()
    crypt_key = str(os.getenv("CRYPTKEY_D4SIGN") or "").strip()
    if not token_api:
        raise RuntimeError("TOKEN_D4SIGN não encontrado no ambiente do Airflow.")
    if not crypt_key:
        raise RuntimeError("CRYPTKEY_D4SIGN não encontrado no ambiente do Airflow.")
    return token_api, crypt_key


def base_url_d4sign_pdf() -> str:
    return str(os.getenv("BASE_URL_D4SIGN") or "https://secure.d4sign.com.br/api/v1").strip().rstrip("/")


def timeout_d4sign_pdf() -> int:
    try:
        return max(5, int(str(os.getenv("D4SIGN_TIMEOUT_SEGUNDOS", "30") or "30").strip()))
    except Exception:
        return 30


def post_d4sign_pdf(caminho: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("A biblioteca requests não está instalada no ambiente do Airflow.")

    token_api, crypt_key = obter_credenciais_d4sign_pdf()
    url = f"{base_url_d4sign_pdf()}{caminho}"
    resposta = requests.post(
        url,
        params={"tokenAPI": token_api, "cryptKey": crypt_key},
        json=payload or {},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout_d4sign_pdf(),
    )

    try:
        dados = resposta.json()
    except Exception:
        dados = {"resposta_texto": resposta.text}

    if not resposta.ok:
        raise RuntimeError(f"Erro POST D4Sign. Caminho={caminho}. Status={resposta.status_code}. Resposta={dados}")

    return dados if isinstance(dados, dict) else {"resposta": dados}


def extrair_url_download_pdf_d4sign(objeto: Any) -> str | None:
    chaves_url = {"url", "download", "downloadurl", "download_url", "urldownload", "url_download", "link", "linkdownload", "link_download"}
    if isinstance(objeto, dict):
        for chave, valor in objeto.items():
            chave_norm = normalizar_texto(chave).replace("_", "").replace("-", "")
            if chave_norm in chaves_url and isinstance(valor, str) and valor.strip().lower().startswith(("http://", "https://")):
                return valor.strip()
        for valor in objeto.values():
            achou = extrair_url_download_pdf_d4sign(valor)
            if achou:
                return achou
    if isinstance(objeto, list):
        for item in objeto:
            achou = extrair_url_download_pdf_d4sign(item)
            if achou:
                return achou
    if isinstance(objeto, str) and objeto.strip().lower().startswith(("http://", "https://")):
        return objeto.strip()
    return None


def extrair_base64_pdf_d4sign(objeto: Any) -> str | None:
    chaves_base64 = {"base64", "filebase64", "file_base64", "base64file", "base64_file", "base64binaryfile", "base64_binary_file", "arquivo", "documento"}
    if isinstance(objeto, dict):
        for chave, valor in objeto.items():
            chave_norm = normalizar_texto(chave).replace("_", "").replace("-", "")
            if chave_norm in chaves_base64 and isinstance(valor, str):
                texto = valor.strip()
                if texto.startswith("data:application/pdf;base64,"):
                    texto = texto.split(",", 1)[1].strip()
                if len(texto) > 100:
                    return texto
        for valor in objeto.values():
            achou = extrair_base64_pdf_d4sign(valor)
            if achou:
                return achou
    if isinstance(objeto, list):
        for item in objeto:
            achou = extrair_base64_pdf_d4sign(item)
            if achou:
                return achou
    return None


def baixar_pdf_url_temporaria(url_download: str) -> bytes:
    if requests is None:
        raise RuntimeError("A biblioteca requests não está instalada no ambiente do Airflow.")
    resposta = requests.get(
        str(url_download).strip(),
        headers={"Accept": "application/pdf,application/octet-stream,*/*"},
        timeout=timeout_d4sign_pdf(),
    )
    try:
        resposta.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Falha ao baixar PDF pela URL temporária D4Sign. Status={resposta.status_code}. Resposta={(resposta.text or '')[:1000]}") from exc
    dados_pdf = resposta.content or b""
    validar_bytes_pdf(dados_pdf)
    return dados_pdf


def executar_download_pdf_d4sign(uuid_documento_d4: str) -> bytes:
    uuid_limpo = uuid_d4_valido(uuid_documento_d4)
    if not uuid_limpo:
        raise RuntimeError("UUIDDocumentoD4 vazio para download do PDF D4Sign.")
    if requests is None:
        raise RuntimeError("A biblioteca requests não está instalada no ambiente do Airflow.")

    token_api, crypt_key = obter_credenciais_d4sign_pdf()
    url = f"{base_url_d4sign_pdf()}/documents/{uuid_limpo}/download"
    resposta = requests.post(
        url,
        params={"tokenAPI": token_api, "cryptKey": crypt_key},
        json={"type": "pdf", "language": "pt", "encoding": False},
        headers={
            "Accept": "application/pdf,application/octet-stream,application/json,*/*",
            "Content-Type": "application/json",
        },
        timeout=timeout_d4sign_pdf(),
    )

    conteudo = resposta.content or b""
    if resposta.ok and conteudo[:5].startswith(b"%PDF-"):
        validar_bytes_pdf(conteudo)
        return conteudo

    try:
        resposta_download = resposta.json()
    except Exception:
        resposta_download = {"resposta_texto": (resposta.text or "")[:2000]}

    if not resposta.ok:
        raise RuntimeError(
            f"Erro POST D4Sign. Caminho=/documents/{uuid_limpo}/download. "
            f"Status={resposta.status_code}. Resposta={resposta_download}"
        )

    url_download = extrair_url_download_pdf_d4sign(resposta_download)
    if url_download:
        return baixar_pdf_url_temporaria(url_download)

    conteudo_base64 = extrair_base64_pdf_d4sign(resposta_download)
    if conteudo_base64:
        dados_pdf = base64.b64decode(conteudo_base64, validate=False)
        validar_bytes_pdf(dados_pdf)
        return dados_pdf

    raise RuntimeError(f"D4Sign não retornou PDF direto, URL nem base64 para download do PDF. Resposta={resposta_download}")

def gravar_pdf_local_atomicamente(dados_pdf: bytes, nome_arquivo: str) -> Path:
    validar_bytes_pdf(dados_pdf)
    pasta = pasta_pdf_local_contrato_d4()
    pasta.mkdir(parents=True, exist_ok=True)
    destino = (pasta / Path(nome_arquivo).name).resolve()
    pasta_resolvida = pasta.resolve()
    if destino != pasta_resolvida and pasta_resolvida not in destino.parents:
        raise RuntimeError("Caminho de destino do PDF ficou fora da pasta permitida.")
    temporario = destino.with_name(f".{destino.name}.{uuid.uuid4().hex}.tmp")
    with open(temporario, "wb") as arquivo:
        arquivo.write(dados_pdf)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    os.replace(str(temporario), str(destino))
    return destino


def buscar_registro_pdf_arquivo(
    conexao,
    *,
    id_controle: int,
    nome_arquivo: str,
    id_contrato_d4: int | None = None,
) -> dict[str, Any] | None:
    url_anexo = url_anexo_contrato_pdf(nome_arquivo)
    id_d4 = converter_int(id_contrato_d4)
    linha = conexao.execute(
        text(f"""
            SELECT TOP (1)
                   IDFatoArquivosContratos,
                   IDFatoControleContratosEuromidia,
                   IDFatoKanbanCard,
                   IDFatoContratoD4,
                   NomeArquivo,
                   UrlAnexo,
                   Extensao,
                   TamanhoArquivo,
                   MesAno,
                   DataAtualizado
              FROM {TABELA_ARQUIVOS_CONTRATOS} WITH (UPDLOCK, HOLDLOCK)
             WHERE IDFatoControleContratosEuromidia = :id_controle
               AND (
                    LOWER(LTRIM(RTRIM(ISNULL(NomeArquivo, '')))) = LOWER(:nome_arquivo)
                 OR LOWER(LTRIM(RTRIM(ISNULL(UrlAnexo, '')))) = LOWER(:url_anexo)
                 OR (
                        :id_contrato_d4 IS NOT NULL
                    AND IDFatoContratoD4 = :id_contrato_d4
                    )
               )
             ORDER BY
                    CASE
                        WHEN :id_contrato_d4 IS NOT NULL AND IDFatoContratoD4 = :id_contrato_d4 THEN 0
                        ELSE 1
                    END,
                    IDFatoArquivosContratos DESC;
        """),
        {
            "id_controle": int(id_controle),
            "nome_arquivo": Path(nome_arquivo).name,
            "url_anexo": url_anexo,
            "id_contrato_d4": int(id_d4) if id_d4 else None,
        },
    ).mappings().first()
    return dict(linha) if linha else None

def registrar_pdf_tabela_arquivos(conexao, *, contrato: dict[str, Any], nome_arquivo: str, tamanho_arquivo: int) -> dict[str, Any]:
    id_controle = converter_int(contrato.get("IDFatoControleContratosEuromidia"))
    id_card = converter_int(contrato.get("IDFatoKanbanCard"))
    id_d4 = converter_int(contrato.get("IDFatoContratoD4"))
    if not id_controle:
        raise RuntimeError("Sem IDFatoControleContratosEuromidia para gravar FatoArquivosContratosEuromidia.")
    if not id_d4:
        raise RuntimeError("Sem IDFatoContratoD4 para gravar FatoArquivosContratosEuromidia.")

    nome_final = Path(nome_arquivo).name
    url_anexo = url_anexo_contrato_pdf(nome_final)
    mes_ano = pendulum.now("America/Sao_Paulo").strftime("%Y-%m")
    tamanho = float(tamanho_arquivo or 0)
    row_existente = buscar_registro_pdf_arquivo(conexao, id_controle=int(id_controle), nome_arquivo=nome_final, id_contrato_d4=int(id_d4))

    if row_existente:
        id_arquivo = int(row_existente["IDFatoArquivosContratos"])
        conexao.execute(
            text(f"""
                UPDATE {TABELA_ARQUIVOS_CONTRATOS}
                   SET IDFatoControleContratosEuromidia = :id_controle,
                       IDFatoKanbanCard = :id_card,
                       IDFatoContratoD4 = :id_d4,
                       NomeArquivo = :nome_arquivo,
                       UrlAnexo = :url_anexo,
                       Extensao = 'pdf',
                       TamanhoArquivo = :tamanho,
                       MesAno = :mes_ano,
                       DataAtualizado = SYSDATETIME()
                 WHERE IDFatoArquivosContratos = :id_arquivo;
            """),
            {
                "id_arquivo": id_arquivo,
                "id_controle": int(id_controle),
                "id_card": int(id_card) if id_card else None,
                "id_d4": int(id_d4),
                "nome_arquivo": nome_final,
                "url_anexo": url_anexo,
                "tamanho": tamanho,
                "mes_ano": mes_ano,
            },
        )
        acao = "atualizado"
    else:
        linha = conexao.execute(
            text(f"""
                INSERT INTO {TABELA_ARQUIVOS_CONTRATOS}
                (
                    IDFatoControleContratosEuromidia,
                    IDFatoKanbanCard,
                    IDFatoContratoD4,
                    NomeArquivo,
                    UrlAnexo,
                    Extensao,
                    TamanhoArquivo,
                    MesAno,
                    DataAtualizado
                )
                OUTPUT INSERTED.IDFatoArquivosContratos AS IDFatoArquivosContratos
                VALUES
                (
                    :id_controle,
                    :id_card,
                    :id_d4,
                    :nome_arquivo,
                    :url_anexo,
                    'pdf',
                    :tamanho,
                    :mes_ano,
                    SYSDATETIME()
                );
            """),
            {
                "id_controle": int(id_controle),
                "id_card": int(id_card) if id_card else None,
                "id_d4": int(id_d4),
                "nome_arquivo": nome_final,
                "url_anexo": url_anexo,
                "tamanho": tamanho,
                "mes_ano": mes_ano,
            },
        ).mappings().first()
        if not linha or linha.get("IDFatoArquivosContratos") is None:
            raise RuntimeError("PDF salvo, mas não recuperei IDFatoArquivosContratos inserido.")
        id_arquivo = int(linha["IDFatoArquivosContratos"])
        acao = "inserido"

    return {
        "ok": True,
        "status": acao,
        "id_fato_arquivos_contratos": int(id_arquivo),
        "id_fato_controle_contratos": int(id_controle),
        "id_fato_kanban_card": int(id_card) if id_card else None,
        "id_fato_contrato_d4": int(id_d4),
        "nome_arquivo": nome_final,
        "url_anexo": url_anexo,
        "extensao": "pdf",
        "tamanho_arquivo": tamanho,
        "mes_ano": mes_ano,
    }


def garantir_pdf_local_contrato_d4(conexao, contrato: dict[str, Any], nome_documento_d4: str | None = None) -> dict[str, Any]:
    """Garante exatamente o que a tela precisa: PDF físico + linha na FatoArquivosContratosEuromidia."""
    if not env_bool("D4SIGN_GARANTIR_PDF_LOCAL_DAGS_HABILITADO", "1"):
        return {"ok": False, "status": "desabilitado"}

    id_controle = converter_int(contrato.get("IDFatoControleContratosEuromidia"))
    id_d4 = converter_int(contrato.get("IDFatoContratoD4"))
    uuid_doc = uuid_d4_valido(contrato.get("UUIDDocumentoD4"))
    if not id_controle or not id_d4 or not uuid_doc:
        return {
            "ok": False,
            "status": "sem_chaves_para_pdf",
            "id_controle": id_controle,
            "id_fato_contrato_d4": id_d4,
            "uuid_documento_d4": uuid_doc,
        }

    nome_arquivo = montar_nome_pdf_local_contrato_d4(contrato, nome_documento_d4)
    if not nome_arquivo:
        return {"ok": False, "status": "sem_nome_arquivo_pdf", "id_controle": id_controle, "id_fato_contrato_d4": id_d4}

    caminho = (pasta_pdf_local_contrato_d4() / Path(nome_arquivo).name).resolve()
    row_existente = buscar_registro_pdf_arquivo(conexao, id_controle=int(id_controle), nome_arquivo=nome_arquivo, id_contrato_d4=int(id_d4))

    if row_existente and caminho_pdf_valido(caminho):
        return {
            "ok": True,
            "status": "ja_existia_sem_download",
            "download_d4sign_executado": False,
            "id_fato_arquivos_contratos": int(row_existente["IDFatoArquivosContratos"]),
            "id_fato_controle_contratos": int(id_controle),
            "id_fato_contrato_d4": int(id_d4),
            "uuid_documento_d4": uuid_doc,
            "nome_arquivo": Path(nome_arquivo).name,
            "url_anexo": url_anexo_contrato_pdf(nome_arquivo),
            "caminho_arquivo": str(caminho),
        }

    if caminho_pdf_valido(caminho):
        registro = registrar_pdf_tabela_arquivos(conexao, contrato=contrato, nome_arquivo=nome_arquivo, tamanho_arquivo=int(caminho.stat().st_size))
        return {**registro, "status": "arquivo_existente_registrado_sem_download", "download_d4sign_executado": False, "uuid_documento_d4": uuid_doc, "caminho_arquivo": str(caminho)}

    dados_pdf = executar_download_pdf_d4sign(uuid_doc)
    caminho_salvo = gravar_pdf_local_atomicamente(dados_pdf, nome_arquivo)
    registro = registrar_pdf_tabela_arquivos(conexao, contrato=contrato, nome_arquivo=nome_arquivo, tamanho_arquivo=int(caminho_salvo.stat().st_size))
    return {**registro, "status": "baixado_salvo_registrado", "download_d4sign_executado": True, "uuid_documento_d4": uuid_doc, "caminho_arquivo": str(caminho_salvo)}


def evento_deve_atualizar_pdf_assinado_d4(
    *,
    tipo_evento: str | None,
    payload: dict[str, Any],
    status_evento: dict[str, Any],
) -> bool:
    """Decido se o webhook deve forçar substituição do PDF local pela versão atual/assinada.

    A garantia comum do PDF não baixa de novo quando já existe arquivo físico válido.
    Para eventos de assinatura/finalização, isso não serve, porque o arquivo existente pode ser
    a versão enviada para assinatura. Nesses eventos, a DAG baixa novamente da D4Sign e sobrescreve
    o mesmo arquivo usado pela tela.
    """
    if not env_bool("D4SIGN_ATUALIZAR_PDF_ASSINADO_WEBHOOK_HABILITADO", "1"):
        return False

    tipo_normalizado = normalizar_texto(tipo_evento)
    evento_historico = normalizar_texto(status_evento.get("EventoHistorico"))
    nome_fase = normalizar_texto(status_evento.get("NomeFaseD4"))
    type_post = converter_int(payload.get("type_post"))

    if converter_int(status_evento.get("IDDimStatusD4")) == ID_STATUS_D4_FINALIZADO:
        return True

    if converter_int(status_evento.get("IDDimStatusContratos")) == ID_STATUS_CONTRATO_ATIVO:
        return True

    if type_post in {1, 4}:
        return True

    texto = f"{tipo_normalizado} {evento_historico} {nome_fase}"
    gatilhos = [
        "finished document",
        "finalizado",
        "assinado",
        "signed",
        "completed",
        "complete",
        "concluded",
        "concluido",
    ]
    return any(gatilho in texto for gatilho in gatilhos)


def atualizar_pdf_local_assinado_contrato_d4(
    conexao,
    contrato: dict[str, Any],
    nome_documento_d4: str | None = None,
) -> dict[str, Any]:
    """Baixa novamente o PDF da D4Sign e sobrescreve o arquivo local com a versão atual/assinada.

    Esta função é intencionalmente diferente de garantir_pdf_local_contrato_d4:
    - garantir_pdf_local_contrato_d4 não baixa se o arquivo já existe;
    - esta função força o download quando o webhook indica assinatura/finalização.

    O nome do arquivo é preservado quando já existe linha em FatoArquivosContratosEuromidia, para
    o botão de download continuar apontando para a mesma UrlAnexo.
    """
    if not env_bool("D4SIGN_GARANTIR_PDF_LOCAL_DAGS_HABILITADO", "1"):
        return {"ok": False, "status": "desabilitado"}

    id_controle = converter_int(contrato.get("IDFatoControleContratosEuromidia"))
    id_d4 = converter_int(contrato.get("IDFatoContratoD4"))
    uuid_doc = uuid_d4_valido(contrato.get("UUIDDocumentoD4"))

    if not id_controle or not id_d4 or not uuid_doc:
        return {
            "ok": False,
            "status": "sem_chaves_para_atualizar_pdf_assinado",
            "id_controle": id_controle,
            "id_fato_contrato_d4": id_d4,
            "uuid_documento_d4": uuid_doc,
        }

    nome_arquivo_padrao = montar_nome_pdf_local_contrato_d4(contrato, nome_documento_d4)
    if not nome_arquivo_padrao:
        return {
            "ok": False,
            "status": "sem_nome_arquivo_pdf_assinado",
            "id_controle": id_controle,
            "id_fato_contrato_d4": id_d4,
            "uuid_documento_d4": uuid_doc,
        }

    row_existente = buscar_registro_pdf_arquivo(
        conexao,
        id_controle=int(id_controle),
        nome_arquivo=nome_arquivo_padrao,
        id_contrato_d4=int(id_d4),
    )

    nome_final = Path(nome_arquivo_padrao).name
    if row_existente and str(row_existente.get("NomeArquivo") or "").strip():
        nome_existente = Path(str(row_existente.get("NomeArquivo") or "")).name
        if nome_existente.lower().endswith(".pdf"):
            nome_final = nome_existente

    caminho_antes = pasta_pdf_local_contrato_d4() / nome_final
    tamanho_anterior = None
    if caminho_antes.exists() and caminho_antes.is_file():
        try:
            tamanho_anterior = int(caminho_antes.stat().st_size)
        except Exception:
            tamanho_anterior = None

    dados_pdf = executar_download_pdf_d4sign(uuid_doc)
    caminho_salvo = gravar_pdf_local_atomicamente(dados_pdf, nome_final)
    tamanho_novo = int(caminho_salvo.stat().st_size)

    registro = registrar_pdf_tabela_arquivos(
        conexao,
        contrato=contrato,
        nome_arquivo=nome_final,
        tamanho_arquivo=tamanho_novo,
    )

    return {
        **registro,
        "status": "pdf_assinado_atualizado",
        "download_d4sign_executado": True,
        "sobrescreveu_arquivo_existente": tamanho_anterior is not None,
        "tamanho_arquivo_anterior": tamanho_anterior,
        "tamanho_arquivo_novo": tamanho_novo,
        "uuid_documento_d4": uuid_doc,
        "caminho_arquivo": str(caminho_salvo),
    }


def garantir_pdfs_locais_contratos_d4(hook_sql: HookSqlServer) -> dict[str, Any]:
    """Varre contratos D4 ativos e preenche PDF/tabela quando estiver faltando."""
    if not env_bool("D4SIGN_GARANTIR_PDF_LOCAL_DAGS_HABILITADO", "1"):
        return {"habilitado": False, "status": "desabilitado"}

    limite = obter_int_env("D4SIGN_GARANTIR_PDF_LOCAL_LIMITE_POR_EXECUCAO", 50)
    limite = max(1, min(limite, 500))

    contratos = hook_sql.executar_select(
        f"""
        SELECT TOP ({limite})
            d.IDFatoContratoD4,
            d.IDDimStatusD4,
            d.IDEmpresa AS IDEmpresa,
            d.IDDimCofreD4,
            d.IDFatoControleContratosEuromidia,
            d.IDFatoKanbanCard,
            d.IDDimStatusContratos,
            d.IDDimModeloContratoD4,
            d.IDDimTipoDocumento,
            d.UUIDDocumentoD4,
            d.UUIDCofreD4,
            d.NomeDocumentoD4,
            d.NomeCofreD4,
            d.IDFaseD4,
            d.NomeFaseD4,
            d.TipoArquivoD4,
            d.QuantidadePaginas,
            d.TamanhoArquivoD4,
            d.StatusComentarioD4,
            d.CanceladoPorD4,
            d.DataCriacao,
            d.DataAtualizacao,
            d.BitAtivo
        FROM {TABELA_CONTRATO_D4} d
        WHERE ISNULL(d.BitAtivo, 1) = 1
          AND d.IDFatoControleContratosEuromidia IS NOT NULL
          AND NULLIF(LTRIM(RTRIM(d.UUIDDocumentoD4)), '') IS NOT NULL
        ORDER BY
            CASE WHEN EXISTS (
                SELECT 1
                  FROM {TABELA_ARQUIVOS_CONTRATOS} a
                 WHERE a.IDFatoControleContratosEuromidia = d.IDFatoControleContratosEuromidia
                   AND a.IDFatoContratoD4 = d.IDFatoContratoD4
            ) THEN 1 ELSE 0 END,
            ISNULL(d.DataAtualizacao, d.DataCriacao) DESC,
            d.IDFatoContratoD4 DESC;
        """
    )

    resultados: list[dict[str, Any]] = []
    erros: list[dict[str, Any]] = []
    engine = hook_sql.obter_engine()
    for contrato in contratos:
        try:
            with engine.begin() as conexao:
                resultado = garantir_pdf_local_contrato_d4(conexao, dict(contrato), contrato.get("NomeDocumentoD4"))
            resultados.append(resultado)
        except Exception as exc:
            logging.exception(
                "D4SIGN_PDF_LOCAL_DAG | falha ao garantir PDF local | IDFatoContratoD4=%s | IDFatoControle=%s | UUID=%s",
                contrato.get("IDFatoContratoD4"),
                contrato.get("IDFatoControleContratosEuromidia"),
                contrato.get("UUIDDocumentoD4"),
            )
            erros.append({
                "IDFatoContratoD4": contrato.get("IDFatoContratoD4"),
                "IDFatoControleContratosEuromidia": contrato.get("IDFatoControleContratosEuromidia"),
                "UUIDDocumentoD4": contrato.get("UUIDDocumentoD4"),
                "erro": str(exc),
            })

    return {
        "habilitado": True,
        "contratos_candidatos_pdf": len(contratos),
        "pdfs_ok": len([r for r in resultados if r.get("ok")]),
        "pdfs_com_erro": len(erros),
        "resultados_pdf_local": resultados[:100],
        "erros_pdf_local": erros[:50],
    }

def obter_int_env(nome_variavel: str, padrao: int) -> int:
    """Leio inteiro do ambiente sem quebrar o parse da DAG se vier vazio ou inválido."""
    try:
        return int(str(os.getenv(nome_variavel, str(padrao)) or str(padrao)).strip())
    except Exception:
        return int(padrao)


ID_STATUS_ASSINATURA_D4_PENDENTE = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_PENDENTE", 1)
ID_STATUS_ASSINATURA_D4_VISUALIZOU = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_VISUALIZOU", 2)
ID_STATUS_ASSINATURA_D4_ASSINADO = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_ASSINADO", 3)
ID_STATUS_ASSINATURA_D4_EMAIL_ERRO = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_EMAIL_ERRO", 4)


TAGS_DAG = ["Euromidia", "Contratos", "D4Sign", "Webhook", "SQLServer"]

DOCUMENTACAO_DAG = """
# Processador de webhook D4Sign

Esta DAG não consulta todos os contratos da D4Sign.

Fluxo:
1. O Flask recebe o webhook da D4Sign.
2. O Flask insere o payload em `[Integracao].[Silver].[FatoContratoD4WebhookEvento]`.
3. O Flask dispara esta DAG via API do Airflow, enviando `id_evento_webhook` no `dag_run.conf`.
4. A DAG lê exatamente esse evento, atualiza:
   - `[Integracao].[Silver].[FatoContratoD4]`
   - `[Integracao].[Silver].[FatoControleContratosEuromidia]`
   - `[Integracao].[Silver].[FatoControleContratosItensEuromidia]`
   - `[Integracao].[Silver].[FatoContratoD4Signatario]`
   - `[Integracao].[Silver].[DimHistoricoContratosD4]`
5. A DAG marca o evento como `PROCESSADO`.
6. Se o webhook indicar assinatura/finalização, a DAG baixa novamente o PDF da D4Sign e sobrescreve o arquivo local com a versão atual/assinada.

Regra importante:
- `Finished document` da D4Sign vira contrato `Concluido` na Euromídia.
- `Signed` da D4Sign não ativa o contrato sozinho; mantém `Em Assinatura` e a conclusão segue o status oficial da D4Sign/API.
- `Canceled` da D4Sign vira contrato `Cancelado` na Euromídia.

Esta DAG roda a cada 1 minuto e também pode ser acionada sob demanda pelo Flask.
"""


def normalizar_texto(valor: Any) -> str:
    """Normalizo texto para comparar mensagens sem acento, caixa e espaços extras."""
    if valor is None:
        return ""

    texto = str(valor).strip().lower()
    texto = " ".join(texto.split())
    texto_sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(
        caractere for caractere in texto_sem_acento
        if not unicodedata.combining(caractere)
    )


def converter_int(valor: Any) -> int | None:
    """Converto valor para inteiro quando isso faz sentido."""
    if valor is None or isinstance(valor, bool):
        return None

    if isinstance(valor, int):
        return valor

    texto = str(valor).strip()
    if not texto:
        return None

    try:
        return int(float(texto.replace(",", ".")))
    except Exception:
        return None


def limitar_texto(valor: Any, limite: int) -> str | None:
    """Converto para texto e limito tamanho antes de gravar no SQL Server."""
    if valor is None:
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    return texto[:limite]


def buscar_valor_recursivo(dados: Any, nomes_chaves: list[str]) -> Any:
    """Procuro um campo no payload mesmo que ele venha dentro de outro objeto."""
    nomes = {normalizar_texto(nome) for nome in nomes_chaves}

    if isinstance(dados, dict):
        for chave, valor in dados.items():
            if normalizar_texto(chave) in nomes and valor not in (None, ""):
                return valor

            encontrado = buscar_valor_recursivo(valor, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    if isinstance(dados, list):
        for item in dados:
            encontrado = buscar_valor_recursivo(item, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    return None


def carregar_payload(payload_json: str | None) -> dict[str, Any]:
    """Carrego o PayloadJson do webhook."""
    if not payload_json:
        return {}

    try:
        dados = json.loads(payload_json)
    except Exception:
        return {}

    return dados if isinstance(dados, dict) else {"payload": dados}


def obter_conf_dag() -> dict[str, Any]:
    """Leio a configuração enviada pelo Flask ao disparar a DAG."""
    try:
        contexto = get_current_context()
        dag_run = contexto.get("dag_run")
        conf = getattr(dag_run, "conf", None) or {}
        return conf if isinstance(conf, dict) else {}
    except Exception:
        return {}


def extrair_uuid_documento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio o UUID do documento usando tabela e payload."""
    return limitar_texto(
        evento.get("UUIDDocumentoD4")
        or buscar_valor_recursivo(
            payload,
            [
                "uuid",
                "uuidDoc",
                "uuid_document",
                "uuid_documento",
                "UUIDDocumentoD4",
                "uuidDocument",
                "document_uuid",
            ],
        ),
        100,
    )


def extrair_tipo_evento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio o tipo/mensagem do evento."""
    return limitar_texto(
        evento.get("TipoEventoD4")
        or buscar_valor_recursivo(
            payload,
            [
                "message",
                "event",
                "evento",
                "eventName",
                "event_name",
                "tipoEvento",
                "tipo_evento",
                "status",
            ],
        ),
        150,
    )


def extrair_nome_documento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio o nome do documento."""
    return limitar_texto(
        evento.get("NomeDocumentoD4")
        or buscar_valor_recursivo(
            payload,
            [
                "document_name",
                "documentName",
                "nameDoc",
                "name_document",
                "nome_documento",
                "NomeDocumentoD4",
                "name",
            ],
        ),
        255,
    )


def extrair_data_evento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio a data do evento como texto ISO."""
    return limitar_texto(
        evento.get("DataEventoD4Texto")
        or buscar_valor_recursivo(
            payload,
            [
                "event_datetime",
                "eventDatetime",
                "event_date_time",
                "signed_at",
                "date",
                "data",
                "created_at",
                "datetime",
            ],
        ),
        100,
    )



def valor_bool_para_bit(valor: Any) -> int:
    """Converto valores booleanos/textuais comuns para 0 ou 1."""
    if isinstance(valor, bool):
        return 1 if valor else 0

    if isinstance(valor, int):
        return 1 if valor == 1 else 0

    texto = normalizar_texto(valor)
    if texto in {"1", "true", "sim", "yes", "y", "on", "s", "ok", "enviado", "sent", "assinado", "signed", "visualizado", "viewed"}:
        return 1

    return 0


def coletar_dicionarios_com_email(dados: Any) -> list[dict[str, Any]]:
    """Coleto qualquer objeto do payload que pareça representar um signatário."""
    encontrados: list[dict[str, Any]] = []

    if isinstance(dados, dict):
        tem_email = buscar_valor_recursivo(dados, ["email", "EmailSignatario"])
        if tem_email:
            encontrados.append(dados)

        for valor in dados.values():
            encontrados.extend(coletar_dicionarios_com_email(valor))

    elif isinstance(dados, list):
        for item in dados:
            encontrados.extend(coletar_dicionarios_com_email(item))

    return encontrados


def primeiro_valor_signatario(dados: dict[str, Any], nomes_chaves: list[str], limite: int | None = None) -> Any:
    """Pego o primeiro campo existente no signatário, mesmo com nomes diferentes."""
    valor = buscar_valor_recursivo(dados, nomes_chaves)
    if limite is not None:
        return limitar_texto(valor, limite)
    return valor


def resolver_status_assinatura_signatario(
    *,
    tipo_evento: str | None,
    payload: dict[str, Any],
    signatario: dict[str, Any],
    bit_email_enviado: int,
    bit_visualizou: int,
    bit_assinado: int,
) -> int:
    """Resolvo status do signatário sem travar em um único layout da D4Sign."""
    texto_evento = normalizar_texto(tipo_evento)
    texto_status = normalizar_texto(
        primeiro_valor_signatario(
            signatario,
            [
                "status",
                "status_assinatura",
                "signature_status",
                "statusSigner",
                "status_signer",
                "message",
            ],
        )
    )

    texto_base = f"{texto_evento} {texto_status}"

    if bit_assinado or "signed" in texto_base or "assinado" in texto_base or "finished document" in texto_base:
        return ID_STATUS_ASSINATURA_D4_ASSINADO

    if bit_visualizou or "view" in texto_base or "visualiz" in texto_base or "read" in texto_base or "opened" in texto_base:
        return ID_STATUS_ASSINATURA_D4_VISUALIZOU

    if "erro" in texto_base or "error" in texto_base or "failed" in texto_base:
        return ID_STATUS_ASSINATURA_D4_EMAIL_ERRO

    if bit_email_enviado:
        return ID_STATUS_ASSINATURA_D4_PENDENTE

    return ID_STATUS_ASSINATURA_D4_PENDENTE


def extrair_signatarios(payload: dict[str, Any], tipo_evento: str | None, data_evento: str | None) -> list[dict[str, Any]]:
    """Extraio o máximo possível dos signatários do webhook D4Sign."""
    candidatos: list[dict[str, Any]] = []

    for chave in [
        "signers",
        "signer",
        "signatarios",
        "signatario",
        "recipients",
        "recipient",
        "subscribers",
        "subscriber",
        "participants",
        "participant",
    ]:
        valor = payload.get(chave)
        if isinstance(valor, list):
            candidatos.extend([item for item in valor if isinstance(item, dict)])
        elif isinstance(valor, dict):
            candidatos.append(valor)

    candidatos.extend(coletar_dicionarios_com_email(payload))

    tipo_normalizado = normalizar_texto(tipo_evento)
    resultado: list[dict[str, Any]] = []
    emails_vistos: set[str] = set()

    for signatario in candidatos:
        email = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "email",
                    "EmailSignatario",
                    "email_signatario",
                    "emailSigner",
                    "signer_email",
                ],
            ),
            255,
        )

        if not email:
            continue

        email_chave = email.strip().lower()
        if email_chave in emails_vistos:
            continue

        emails_vistos.add(email_chave)

        data_envio = primeiro_valor_signatario(
            signatario,
            [
                "sent_at",
                "send_at",
                "sended_at",
                "email_sent_at",
                "data_envio",
                "DataEnvioD4",
                "created_at",
                "createdAt",
            ],
            100,
        )

        data_primeira_visualizacao = primeiro_valor_signatario(
            signatario,
            [
                "first_viewed_at",
                "first_view_at",
                "first_opened_at",
                "viewed_at",
                "opened_at",
                "visualized_at",
                "DataPrimeiraVisualizacaoD4",
                "data_primeira_visualizacao",
            ],
            100,
        )

        data_ultima_visualizacao = primeiro_valor_signatario(
            signatario,
            [
                "last_viewed_at",
                "last_view_at",
                "last_opened_at",
                "viewed_at",
                "opened_at",
                "visualized_at",
                "DataUltimaVisualizacaoD4",
                "data_ultima_visualizacao",
            ],
            100,
        )

        data_assinatura = primeiro_valor_signatario(
            signatario,
            [
                "signed_at",
                "signedAt",
                "signature_date",
                "signatureDate",
                "date_signed",
                "DataAssinaturaD4",
                "data_assinatura",
            ],
            100,
        )

        bit_assinado = 1 if (
            data_assinatura
            or "signed" in tipo_normalizado
            or "assinado" in tipo_normalizado
            or "finished document" in tipo_normalizado
            or converter_int(payload.get("type_post")) in {1, 4}
        ) else 0

        bit_visualizou = 1 if (
            data_primeira_visualizacao
            or data_ultima_visualizacao
            or "view" in tipo_normalizado
            or "visualiz" in tipo_normalizado
            or "opened" in tipo_normalizado
            or "read" in tipo_normalizado
        ) else 0

        bit_email_enviado = 1 if (
            data_envio
            or valor_bool_para_bit(primeiro_valor_signatario(signatario, ["email_sent", "BitEmailEnviado", "sent"]))
            or "sent" in tipo_normalizado
            or "enviado" in tipo_normalizado
        ) else 0

        telefone = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "phone",
                    "telephone",
                    "telefone",
                    "celular",
                    "mobile",
                    "whatsapp",
                    "phone_number",
                    "TelefoneSignatario",
                ],
            ),
            50,
        )

        ip_assinatura = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "ip",
                    "ip_address",
                    "ipAddress",
                    "signature_ip",
                    "IpAssinaturaD4",
                    "ip_assinatura",
                ],
            )
            or primeiro_valor_signatario(
                payload,
                ["ip", "ip_address", "ipAddress", "signature_ip", "IpAssinaturaD4"],
            ),
            100,
        )

        geolocalizacao = primeiro_valor_signatario(
            signatario,
            [
                "geolocation",
                "geo_location",
                "location",
                "assinatura_geolocalizacao",
                "GeolocalizacaoAssinaturaD4",
            ],
        )

        if isinstance(geolocalizacao, (dict, list)):
            geolocalizacao = json.dumps(geolocalizacao, ensure_ascii=False, default=str)

        user_agent = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "user_agent",
                    "userAgent",
                    "signature_user_agent",
                    "UserAgentAssinaturaD4",
                ],
            )
            or primeiro_valor_signatario(
                payload,
                ["user_agent", "userAgent", "signature_user_agent", "UserAgentAssinaturaD4"],
            ),
            500,
        )

        status_envio = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "email_status",
                    "status_email",
                    "StatusEnvioEmailD4",
                    "send_status",
                    "status_envio",
                ],
            ),
            100,
        )

        mensagem_envio = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "email_message",
                    "message_email",
                    "MensagemEnvioEmailD4",
                    "send_message",
                    "mensagem_envio",
                    "error_message",
                ],
            ),
            1000,
        )

        bit_contato_principal = valor_bool_para_bit(
            primeiro_valor_signatario(
                signatario,
                [
                    "main",
                    "principal",
                    "is_main",
                    "main_contact",
                    "BitContatoPrincipal",
                    "contato_principal",
                ],
            )
        )

        registro = {
            "KeySignerD4": limitar_texto(
                primeiro_valor_signatario(
                    signatario,
                    [
                        "uuid",
                        "key_signer",
                        "keySigner",
                        "signer_key",
                        "uuid_signer",
                        "id",
                        "KeySignerD4",
                    ],
                ),
                100,
            ),
            "EmailSignatario": email,
            "NomeSignatario": limitar_texto(
                primeiro_valor_signatario(
                    signatario,
                    [
                        "name",
                        "nome",
                        "full_name",
                        "fullName",
                        "signer_name",
                        "NomeSignatario",
                    ],
                ),
                255,
            ),
            "DocumentoSignatario": limitar_texto(
                primeiro_valor_signatario(
                    signatario,
                    [
                        "identification_number",
                        "document",
                        "documento",
                        "cpf",
                        "cnpj",
                        "DocumentoSignatario",
                    ],
                ),
                100,
            ),
            "TelefoneSignatario": telefone,
            "BitContatoPrincipal": bit_contato_principal,
            "IDDimStatusAssinaturaD4": resolver_status_assinatura_signatario(
                tipo_evento=tipo_evento,
                payload=payload,
                signatario=signatario,
                bit_email_enviado=bit_email_enviado,
                bit_visualizou=bit_visualizou,
                bit_assinado=bit_assinado,
            ),
            "BitEmailEnviado": bit_email_enviado,
            "StatusEnvioEmailD4": status_envio,
            "MensagemEnvioEmailD4": mensagem_envio,
            "BitVisualizouDocumento": bit_visualizou,
            "DataPrimeiraVisualizacaoD4": limitar_texto(data_primeira_visualizacao, 100),
            "DataUltimaVisualizacaoD4": limitar_texto(data_ultima_visualizacao, 100),
            "BitAssinado": bit_assinado,
            "DataEnvioD4": limitar_texto(data_envio, 100),
            "DataAssinaturaD4": limitar_texto(data_assinatura or (data_evento if bit_assinado else None), 100),
            "IpAssinaturaD4": ip_assinatura,
            "GeolocalizacaoAssinaturaD4": limitar_texto(geolocalizacao, 500),
            "UserAgentAssinaturaD4": user_agent,
            "SegundosAtePrimeiraVisualizacao": converter_int(
                primeiro_valor_signatario(signatario, ["seconds_to_first_view", "SegundosAtePrimeiraVisualizacao"])
            ),
            "SegundosAteAssinatura": converter_int(
                primeiro_valor_signatario(signatario, ["seconds_to_signature", "SegundosAteAssinatura"])
            ),
        }

        resultado.append(registro)

    return resultado


def resolver_status_por_evento(tipo_evento: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Transformo o evento da D4Sign nos status que serão gravados nas tabelas."""
    tipo_normalizado = normalizar_texto(tipo_evento)
    type_post = converter_int(payload.get("type_post"))

    if type_post == 1 or "finished document" in tipo_normalizado or "finalizado" in tipo_normalizado:
        return {
            "IDDimStatusD4": ID_STATUS_D4_FINALIZADO,
            "IDFaseD4": ID_STATUS_D4_FINALIZADO,
            "NomeFaseD4": "Finalizado",
            "IDDimStatusContratos": ID_STATUS_CONTRATO_CONCLUIDO,
            "EventoHistorico": "Finished document",
        }

    if type_post == 4 or "signed" in tipo_normalizado or "assinado" in tipo_normalizado:
        # IMPORTANTE:
        # O webhook "Signed" pode ser evento de assinatura de signatário, não garantia
        # de que o contrato inteiro já deve virar Ativo.
        # Enquanto a D4Sign estiver em Aguardando Assinaturas, o contrato interno fica
        # como Em Assinatura. A ativação/conclusão deve seguir o status oficial D4/API.
        return {
            "IDDimStatusD4": ID_STATUS_D4_AGUARDANDO_ASSINATURAS,
            "IDFaseD4": ID_STATUS_D4_AGUARDANDO_ASSINATURAS,
            "NomeFaseD4": "Aguardando Assinaturas",
            "IDDimStatusContratos": ID_STATUS_CONTRATO_EM_ASSINATURA,
            "EventoHistorico": "Signed",
        }

    if type_post == 3 or "cancel" in tipo_normalizado or "cancelado" in tipo_normalizado:
        return {
            "IDDimStatusD4": ID_STATUS_D4_CANCELADO,
            "IDFaseD4": ID_STATUS_D4_CANCELADO,
            "NomeFaseD4": "Cancelado",
            "IDDimStatusContratos": ID_STATUS_CONTRATO_CANCELADO,
            "EventoHistorico": "Canceled",
        }

    return {
        "IDDimStatusD4": None,
        "IDFaseD4": None,
        "NomeFaseD4": limitar_texto(tipo_evento, 90),
        "IDDimStatusContratos": None,
        "EventoHistorico": limitar_texto(tipo_evento, 150) or "Webhook D4Sign",
    }


def reservar_eventos(conexao, id_evento_webhook: int | None, limite_eventos: int) -> list[dict[str, Any]]:
    """Marco eventos pendentes como PROCESSANDO para evitar processamento duplicado."""
    if id_evento_webhook:
        sql = text(f"""
            ;WITH eventos AS (
                SELECT TOP (1)
                    *
                FROM {TABELA_WEBHOOK_EVENTO_D4} WITH (READPAST, UPDLOCK, ROWLOCK)
                WHERE IDFatoContratoD4WebhookEvento = :IDEvento
                  AND BitProcessado = 0
                  AND StatusProcessamento IN ('PENDENTE', 'REPROCESSAR')
                ORDER BY
                    COALESCE(
                        TRY_CONVERT(datetime2(7), DataEventoD4Texto, 126),
                        TRY_CONVERT(datetime2(7), JSON_VALUE(PayloadJson, '$.event_datetime'), 126),
                        DataRecebimento
                    ) ASC,
                    IDFatoContratoD4WebhookEvento ASC
            )
            UPDATE eventos
            SET
                StatusProcessamento = 'PROCESSANDO',
                MensagemErro = NULL,
                DataProcessamento = NULL
            OUTPUT
                INSERTED.IDFatoContratoD4WebhookEvento,
                INSERTED.IDFatoContratoD4,
                INSERTED.UUIDDocumentoD4,
                INSERTED.TipoEventoD4,
                INSERTED.NomeDocumentoD4,
                INSERTED.DataEventoD4Texto,
                INSERTED.PayloadJson,
                INSERTED.DataRecebimento;
        """)
        return list(conexao.execute(sql, {"IDEvento": int(id_evento_webhook)}).mappings().all())

    sql = text(f"""
        ;WITH eventos AS (
            SELECT TOP (:LimiteEventos)
                *
            FROM {TABELA_WEBHOOK_EVENTO_D4} WITH (READPAST, UPDLOCK, ROWLOCK)
            WHERE BitProcessado = 0
              AND StatusProcessamento IN ('PENDENTE', 'REPROCESSAR')
            ORDER BY
                COALESCE(
                    TRY_CONVERT(datetime2(7), DataEventoD4Texto, 126),
                    TRY_CONVERT(datetime2(7), JSON_VALUE(PayloadJson, '$.event_datetime'), 126),
                    DataRecebimento
                ) ASC,
                IDFatoContratoD4WebhookEvento ASC
        )
        UPDATE eventos
        SET
            StatusProcessamento = 'PROCESSANDO',
            MensagemErro = NULL,
            DataProcessamento = NULL
        OUTPUT
            INSERTED.IDFatoContratoD4WebhookEvento,
            INSERTED.IDFatoContratoD4,
            INSERTED.UUIDDocumentoD4,
            INSERTED.TipoEventoD4,
            INSERTED.NomeDocumentoD4,
            INSERTED.DataEventoD4Texto,
            INSERTED.PayloadJson,
            INSERTED.DataRecebimento;
    """)
    return list(conexao.execute(sql, {"LimiteEventos": int(limite_eventos)}).mappings().all())


def buscar_contrato_por_uuid(conexao, uuid_documento: str) -> dict[str, Any] | None:
    """Encontro o contrato D4 interno pelo UUID enviado no webhook.

    Também resolvo o IDFatoKanbanCard pelo vínculo em FatoContratoCardEuromidia quando
    FatoContratoD4.IDFatoKanbanCard estiver nulo. Isso mantém o PDF registrado com o card certo.
    """
    sql = text(f"""
        SELECT TOP (1)
            d.IDFatoContratoD4,
            d.IDDimStatusD4,
            d.IDEmpresa,
            d.IDDimCofreD4,
            d.IDFatoControleContratosEuromidia,
            COALESCE(d.IDFatoKanbanCard, card.IDFatoKanbanCard) AS IDFatoKanbanCard,
            d.IDDimStatusContratos,
            d.IDDimModeloContratoD4,
            d.IDDimTipoDocumento,
            d.UUIDDocumentoD4,
            d.UUIDCofreD4,
            d.NomeDocumentoD4,
            d.NomeCofreD4,
            d.IDFaseD4,
            d.NomeFaseD4,
            d.TipoArquivoD4,
            d.QuantidadePaginas,
            d.TamanhoArquivoD4,
            d.StatusComentarioD4,
            d.CanceladoPorD4,
            d.DataCriacao,
            d.DataAtualizacao,
            d.BitAtivo
        FROM {TABELA_CONTRATO_D4} d
        OUTER APPLY (
            SELECT TOP (1)
                cc.IDFatoKanbanCard
            FROM {TABELA_CONTRATO_CARD} cc WITH (READPAST)
            WHERE cc.IDFatoControleContratosEuromidia = d.IDFatoControleContratosEuromidia
              AND cc.IDFatoKanbanCard IS NOT NULL
            ORDER BY
                cc.DataAtualizacao DESC,
                cc.IDFatoContratoCardEuromidia DESC
        ) card
        WHERE d.UUIDDocumentoD4 = :UUIDDocumentoD4
          AND ISNULL(d.BitAtivo, 1) = 1
        ORDER BY d.IDFatoContratoD4 DESC;
    """)
    linha = conexao.execute(sql, {"UUIDDocumentoD4": uuid_documento}).mappings().first()
    return dict(linha) if linha else None

def atualizar_contrato_d4(
    conexao,
    *,
    contrato: dict[str, Any],
    status_evento: dict[str, Any],
    nome_documento: str | None,
) -> None:
    """Atualizo a visão atual do documento na FatoContratoD4."""
    sql = text(f"""
        UPDATE {TABELA_CONTRATO_D4}
        SET
            IDDimStatusD4 = COALESCE(:IDDimStatusD4, IDDimStatusD4),
            IDFaseD4 = COALESCE(:IDFaseD4, IDFaseD4),
            NomeFaseD4 = COALESCE(:NomeFaseD4, NomeFaseD4),
            IDDimStatusContratos = COALESCE(:IDDimStatusContratos, IDDimStatusContratos),
            NomeDocumentoD4 = COALESCE(:NomeDocumentoD4, NomeDocumentoD4),
            DataAtualizacao = SYSDATETIME()
        WHERE IDFatoContratoD4 = :IDFatoContratoD4;
    """)
    conexao.execute(
        sql,
        {
            "IDFatoContratoD4": contrato["IDFatoContratoD4"],
            "IDDimStatusD4": status_evento.get("IDDimStatusD4"),
            "IDFaseD4": status_evento.get("IDFaseD4"),
            "NomeFaseD4": status_evento.get("NomeFaseD4"),
            "IDDimStatusContratos": status_evento.get("IDDimStatusContratos"),
            "NomeDocumentoD4": nome_documento,
        },
    )


def resolver_status_final_euromidia(status_atual: Any, status_novo: Any) -> int | None:
    """Resolvo o status final sem deixar webhook atrasado voltar a esteira."""
    id_status_atual = converter_int(status_atual)
    id_status_novo = converter_int(status_novo)

    if id_status_novo is None:
        return id_status_atual

    # Cancelado é terminal.
    if id_status_atual == ID_STATUS_CONTRATO_CANCELADO or id_status_novo == ID_STATUS_CONTRATO_CANCELADO:
        return ID_STATUS_CONTRATO_CANCELADO

    # Concluido também não deve regredir por evento atrasado.
    if id_status_atual == ID_STATUS_CONTRATO_CONCLUIDO:
        return ID_STATUS_CONTRATO_CONCLUIDO

    if id_status_atual is None:
        return id_status_novo

    # Permite a progressão Signed -> Ativo e Finished document -> Concluido.
    if id_status_novo > id_status_atual:
        return id_status_novo

    return id_status_atual


def buscar_status_atual_controle_euromidia(conexao, id_fato_controle_contrato: int) -> int | None:
    """Leio o status atual da tabela principal da esteira do contrato."""
    sql = text(f"""
        SELECT TOP (1)
            IDDimStatusContratos
        FROM {TABELA_CONTROLE_CONTRATOS} WITH (UPDLOCK, ROWLOCK)
        WHERE IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
          AND ISNULL(BitAtivo, 1) = 1;
    """)
    linha = conexao.execute(
        sql,
        {"IDFatoControleContratosEuromidia": id_fato_controle_contrato},
    ).mappings().first()

    if not linha:
        return None

    return converter_int(linha.get("IDDimStatusContratos"))



def inserir_historico_d4(
    conexao,
    *,
    contrato: dict[str, Any],
    status_evento: dict[str, Any],
    data_evento: str | None,
) -> dict[str, Any]:
    """Insiro histórico do webhook sem duplicar o último status idêntico."""
    id_controle = converter_int(contrato.get("IDFatoControleContratosEuromidia"))
    id_status_contrato = converter_int(status_evento.get("IDDimStatusContratos") or contrato.get("IDDimStatusContratos"))
    id_status_d4 = converter_int(status_evento.get("IDDimStatusD4") or contrato.get("IDDimStatusD4"))

    if not id_controle:
        return {"historico_inserido": False, "motivo": "sem_id_controle"}

    sql = text(f"""
        ;WITH UltimoHistorico AS (
            SELECT TOP (1)
                IDDimStatusContratos,
                IDDimStatusD4
            FROM {TABELA_HISTORICO_D4} WITH (READPAST)
            WHERE IDEmpresaProprietaria = :IDEmpresaProprietaria
              AND IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
            ORDER BY DataStatus DESC, IDDimHistoricoContratos DESC
        )
        INSERT INTO {TABELA_HISTORICO_D4}
        (
            IDEmpresaProprietaria,
            IDFatoControleContratosEuromidia,
            IDDimStatusContratos,
            IDDimStatusD4,
            DataStatus
        )
        OUTPUT INSERTED.IDDimHistoricoContratos AS IDDimHistoricoContratos
        SELECT
            :IDEmpresaProprietaria,
            :IDFatoControleContratosEuromidia,
            :IDDimStatusContratos,
            :IDDimStatusD4,
            COALESCE(TRY_CONVERT(datetime2(3), :DataEvento, 126), SYSDATETIME())
        WHERE NOT EXISTS (
            SELECT 1
            FROM UltimoHistorico
            WHERE ISNULL(IDDimStatusContratos, -1) = ISNULL(:IDDimStatusContratos, -1)
              AND ISNULL(IDDimStatusD4, -1) = ISNULL(:IDDimStatusD4, -1)
        );
    """)

    linha = conexao.execute(
        sql,
        {
            "IDEmpresaProprietaria": ID_EMPRESA_PROPRIETARIA_EUROMIDIA,
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": id_status_contrato,
            "IDDimStatusD4": id_status_d4,
            "DataEvento": data_evento,
        },
    ).mappings().first()

    return {
        "historico_inserido": bool(linha),
        "id_historico": int(linha["IDDimHistoricoContratos"]) if linha and linha.get("IDDimHistoricoContratos") is not None else None,
        "id_controle": id_controle,
        "id_status_contrato": id_status_contrato,
        "id_status_d4": id_status_d4,
    }


def propagar_status_contrato_euromidia(
    conexao,
    *,
    id_fato_controle_contrato: int | None,
    id_status_contrato: int | None,
    id_fato_kanban_card: int | None = None,
) -> dict[str, Any]:
    """Atualizo cabeçalho, itens e solicitação para a tela acompanhar o webhook."""
    id_controle = converter_int(id_fato_controle_contrato)
    id_card = converter_int(id_fato_kanban_card)
    id_status_novo = converter_int(id_status_contrato)

    if not id_controle or id_status_novo is None:
        return {
            "propagou": False,
            "motivo": "sem_id_controle_ou_sem_status",
            "id_controle": id_controle,
            "id_status_novo": id_status_novo,
        }

    status_atual = buscar_status_atual_controle_euromidia(conexao, id_controle)
    status_final = resolver_status_final_euromidia(status_atual, id_status_novo)

    if status_final is None:
        return {
            "propagou": False,
            "motivo": "status_final_nulo",
            "id_controle": id_controle,
            "status_atual": status_atual,
            "id_status_novo": id_status_novo,
        }

    resultado_cabecalho = conexao.execute(
        text(f"""
            UPDATE {TABELA_CONTROLE_CONTRATOS}
            SET
                IDDimStatusContratos = :IDDimStatusContratos,
                DataAtualizacao = SYSDATETIME()
            WHERE IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
              AND ISNULL(BitAtivo, 1) = 1
              AND (
                    IDDimStatusContratos IS NULL
                    OR IDDimStatusContratos <> :IDDimStatusContratos
              );
        """),
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": status_final,
        },
    )

    resultado_itens = conexao.execute(
        text(f"""
            UPDATE itens
            SET
                itens.Status = status_contrato.Status,
                itens.DataAtualizacao = SYSDATETIME()
            FROM {TABELA_CONTROLE_CONTRATOS_ITENS} itens
            INNER JOIN {TABELA_STATUS_CONTRATOS} status_contrato
                ON status_contrato.IDDimStatusContratos = :IDDimStatusContratos
               AND status_contrato.IDEmpresaProprietaria = :IDEmpresaProprietaria
            WHERE itens.IDFatoControleContratoEuromidia = :IDFatoControleContratosEuromidia
              AND ISNULL(itens.BitAtivo, 1) = 1
              AND (
                    itens.Status IS NULL
                    OR LTRIM(RTRIM(itens.Status)) <> LTRIM(RTRIM(status_contrato.Status))
              );
        """),
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": status_final,
            "IDEmpresaProprietaria": ID_EMPRESA_PROPRIETARIA_EUROMIDIA,
        },
    )

    resultado_solicitacao = conexao.execute(
        text(f"""
            UPDATE solicitacao
            SET
                solicitacao.IDDimStatusContratos = :IDDimStatusContratos,
                solicitacao.DataAtualizacao = SYSDATETIME()
            FROM {TABELA_SOLICITACAO_CONTRATO} solicitacao
            WHERE ISNULL(solicitacao.BitAtivo, 1) = 1
              AND (
                    solicitacao.IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
                    OR (
                        :IDFatoKanbanCard IS NOT NULL
                        AND solicitacao.IDFatoKanbanCard = :IDFatoKanbanCard
                    )
              )
              AND (
                    solicitacao.IDDimStatusContratos IS NULL
                    OR solicitacao.IDDimStatusContratos <> :IDDimStatusContratos
              );
        """),
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDFatoKanbanCard": id_card,
            "IDDimStatusContratos": status_final,
        },
    )

    return {
        "propagou": True,
        "id_controle": id_controle,
        "id_card": id_card,
        "status_atual": status_atual,
        "status_evento": id_status_novo,
        "status_final": status_final,
        "cabecalho_atualizado": int(resultado_cabecalho.rowcount or 0),
        "itens_atualizados": int(resultado_itens.rowcount or 0),
        "solicitacoes_atualizadas": int(resultado_solicitacao.rowcount or 0),
    }


def upsert_signatario_d4(
    conexao,
    *,
    contrato: dict[str, Any],
    signatario: dict[str, Any],
    payload_json: str,
) -> str:
    """Atualizo ou crio signatário, preenchendo todos os campos disponíveis."""
    email = signatario.get("EmailSignatario")
    if not email:
        return "ignorado_sem_email"

    params = {
        "IDFatoContratoD4": contrato.get("IDFatoContratoD4"),
        "IDFatoControleContratosEuromidia": contrato.get("IDFatoControleContratosEuromidia"),
        "IDFatoKanbanCard": contrato.get("IDFatoKanbanCard"),
        "IDEmpresa": contrato.get("IDEmpresa"),
        "KeySignerD4": signatario.get("KeySignerD4"),
        "EmailSignatario": email,
        "NomeSignatario": signatario.get("NomeSignatario"),
        "DocumentoSignatario": signatario.get("DocumentoSignatario"),
        "TelefoneSignatario": signatario.get("TelefoneSignatario"),
        "BitContatoPrincipal": int(signatario.get("BitContatoPrincipal") or 0),
        "IDDimStatusAssinaturaD4": signatario.get("IDDimStatusAssinaturaD4"),
        "BitEmailEnviado": int(signatario.get("BitEmailEnviado") or 0),
        "StatusEnvioEmailD4": signatario.get("StatusEnvioEmailD4"),
        "MensagemEnvioEmailD4": signatario.get("MensagemEnvioEmailD4"),
        "BitVisualizouDocumento": int(signatario.get("BitVisualizouDocumento") or 0),
        "DataPrimeiraVisualizacaoD4": signatario.get("DataPrimeiraVisualizacaoD4"),
        "DataUltimaVisualizacaoD4": signatario.get("DataUltimaVisualizacaoD4"),
        "BitAssinado": int(signatario.get("BitAssinado") or 0),
        "DataEnvioD4": signatario.get("DataEnvioD4"),
        "DataAssinaturaD4": signatario.get("DataAssinaturaD4"),
        "IpAssinaturaD4": signatario.get("IpAssinaturaD4"),
        "GeolocalizacaoAssinaturaD4": signatario.get("GeolocalizacaoAssinaturaD4"),
        "UserAgentAssinaturaD4": signatario.get("UserAgentAssinaturaD4"),
        "SegundosAtePrimeiraVisualizacao": signatario.get("SegundosAtePrimeiraVisualizacao"),
        "SegundosAteAssinatura": signatario.get("SegundosAteAssinatura"),
        "PayloadJson": payload_json,
    }

    sql_update = text(f"""
        UPDATE {TABELA_SIGNATARIO_D4}
        SET
            KeySignerD4 = COALESCE(:KeySignerD4, KeySignerD4),
            NomeSignatario = COALESCE(:NomeSignatario, NomeSignatario),
            DocumentoSignatario = COALESCE(:DocumentoSignatario, DocumentoSignatario),
            TelefoneSignatario = COALESCE(:TelefoneSignatario, TelefoneSignatario),
            BitContatoPrincipal = CASE WHEN :BitContatoPrincipal = 1 THEN 1 ELSE BitContatoPrincipal END,
            IDDimStatusAssinaturaD4 = COALESCE(:IDDimStatusAssinaturaD4, IDDimStatusAssinaturaD4),
            BitEmailEnviado = CASE WHEN :BitEmailEnviado = 1 THEN 1 ELSE BitEmailEnviado END,
            StatusEnvioEmailD4 = COALESCE(:StatusEnvioEmailD4, StatusEnvioEmailD4),
            MensagemEnvioEmailD4 = COALESCE(:MensagemEnvioEmailD4, MensagemEnvioEmailD4),
            BitVisualizouDocumento = CASE WHEN :BitVisualizouDocumento = 1 THEN 1 ELSE BitVisualizouDocumento END,
            DataPrimeiraVisualizacaoD4 = COALESCE(
                DataPrimeiraVisualizacaoD4,
                TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126)
            ),
            DataUltimaVisualizacaoD4 = COALESCE(
                TRY_CONVERT(datetime2(7), :DataUltimaVisualizacaoD4, 126),
                DataUltimaVisualizacaoD4
            ),
            BitAssinado = CASE WHEN :BitAssinado = 1 THEN 1 ELSE BitAssinado END,
            DataEnvioD4 = COALESCE(
                DataEnvioD4,
                TRY_CONVERT(datetime2(7), :DataEnvioD4, 126)
            ),
            DataAssinaturaD4 = CASE
                WHEN :BitAssinado = 1
                    THEN COALESCE(
                        TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126),
                        DataAssinaturaD4,
                        SYSDATETIME()
                    )
                ELSE DataAssinaturaD4
            END,
            IpAssinaturaD4 = COALESCE(:IpAssinaturaD4, IpAssinaturaD4),
            GeolocalizacaoAssinaturaD4 = COALESCE(:GeolocalizacaoAssinaturaD4, GeolocalizacaoAssinaturaD4),
            UserAgentAssinaturaD4 = COALESCE(:UserAgentAssinaturaD4, UserAgentAssinaturaD4),
            SegundosAtePrimeiraVisualizacao = COALESCE(
                :SegundosAtePrimeiraVisualizacao,
                CASE
                    WHEN DataEnvioD4 IS NOT NULL
                     AND COALESCE(TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126), DataPrimeiraVisualizacaoD4) IS NOT NULL
                        THEN DATEDIFF(
                            SECOND,
                            DataEnvioD4,
                            COALESCE(TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126), DataPrimeiraVisualizacaoD4)
                        )
                    ELSE SegundosAtePrimeiraVisualizacao
                END
            ),
            SegundosAteAssinatura = COALESCE(
                :SegundosAteAssinatura,
                CASE
                    WHEN DataEnvioD4 IS NOT NULL
                     AND COALESCE(TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126), DataAssinaturaD4) IS NOT NULL
                        THEN DATEDIFF(
                            SECOND,
                            DataEnvioD4,
                            COALESCE(TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126), DataAssinaturaD4)
                        )
                    ELSE SegundosAteAssinatura
                END
            ),
            PayloadUltimaConsultaD4 = :PayloadJson,
            DataUltimaConsultaD4 = SYSDATETIME(),
            DataAtualizacao = SYSDATETIME(),
            BitAtivo = 1
        WHERE IDFatoContratoD4 = :IDFatoContratoD4
          AND LOWER(LTRIM(RTRIM(EmailSignatario))) = LOWER(LTRIM(RTRIM(:EmailSignatario)));
    """)
    resultado = conexao.execute(sql_update, params)

    if resultado.rowcount and resultado.rowcount > 0:
        return "update"

    sql_insert = text(f"""
        INSERT INTO {TABELA_SIGNATARIO_D4}
        (
            IDFatoContratoD4,
            IDFatoControleContratosEuromidia,
            IDFatoKanbanCard,
            IDEmpresa,
            KeySignerD4,
            EmailSignatario,
            NomeSignatario,
            DocumentoSignatario,
            TelefoneSignatario,
            BitContatoPrincipal,
            IDDimStatusAssinaturaD4,
            BitEmailEnviado,
            StatusEnvioEmailD4,
            MensagemEnvioEmailD4,
            BitVisualizouDocumento,
            DataPrimeiraVisualizacaoD4,
            DataUltimaVisualizacaoD4,
            BitAssinado,
            DataEnvioD4,
            DataAssinaturaD4,
            IpAssinaturaD4,
            GeolocalizacaoAssinaturaD4,
            UserAgentAssinaturaD4,
            SegundosAtePrimeiraVisualizacao,
            SegundosAteAssinatura,
            PayloadUltimaConsultaD4,
            DataUltimaConsultaD4,
            DataCriacao,
            DataAtualizacao,
            BitAtivo
        )
        VALUES
        (
            :IDFatoContratoD4,
            :IDFatoControleContratosEuromidia,
            :IDFatoKanbanCard,
            :IDEmpresa,
            :KeySignerD4,
            :EmailSignatario,
            :NomeSignatario,
            :DocumentoSignatario,
            :TelefoneSignatario,
            :BitContatoPrincipal,
            :IDDimStatusAssinaturaD4,
            :BitEmailEnviado,
            :StatusEnvioEmailD4,
            :MensagemEnvioEmailD4,
            :BitVisualizouDocumento,
            TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126),
            TRY_CONVERT(datetime2(7), :DataUltimaVisualizacaoD4, 126),
            :BitAssinado,
            TRY_CONVERT(datetime2(7), :DataEnvioD4, 126),
            CASE
                WHEN :BitAssinado = 1
                    THEN COALESCE(TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126), SYSDATETIME())
                ELSE TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126)
            END,
            :IpAssinaturaD4,
            :GeolocalizacaoAssinaturaD4,
            :UserAgentAssinaturaD4,
            :SegundosAtePrimeiraVisualizacao,
            :SegundosAteAssinatura,
            :PayloadJson,
            SYSDATETIME(),
            SYSDATETIME(),
            SYSDATETIME(),
            1
        );
    """)
    conexao.execute(sql_insert, params)
    return "insert"


def marcar_evento_processado(conexao, *, id_evento: int, id_fato_contrato_d4: int) -> None:
    """Marco o evento como processado."""
    sql = text(f"""
        UPDATE {TABELA_WEBHOOK_EVENTO_D4}
        SET
            IDFatoContratoD4 = :IDFatoContratoD4,
            BitProcessado = 1,
            StatusProcessamento = 'PROCESSADO',
            MensagemErro = NULL,
            DataProcessamento = SYSDATETIME()
        WHERE IDFatoContratoD4WebhookEvento = :IDEvento;
    """)
    conexao.execute(sql, {"IDFatoContratoD4": id_fato_contrato_d4, "IDEvento": id_evento})


def marcar_evento_erro(conexao, *, id_evento: int, status: str, mensagem: str) -> None:
    """Marco evento como erro/documento não encontrado."""
    sql = text(f"""
        UPDATE {TABELA_WEBHOOK_EVENTO_D4}
        SET
            BitProcessado = 1,
            StatusProcessamento = :StatusProcessamento,
            MensagemErro = :MensagemErro,
            DataProcessamento = SYSDATETIME()
        WHERE IDFatoContratoD4WebhookEvento = :IDEvento;
    """)
    conexao.execute(
        sql,
        {
            "IDEvento": id_evento,
            "StatusProcessamento": limitar_texto(status, 50),
            "MensagemErro": limitar_texto(mensagem, 2000),
        },
    )


def processar_evento(conexao, evento: dict[str, Any]) -> dict[str, Any]:
    """Processo um evento de webhook e atualizo as tabelas finais."""
    id_evento = int(evento["IDFatoContratoD4WebhookEvento"])
    payload_json = str(evento.get("PayloadJson") or "{}")
    payload = carregar_payload(payload_json)

    uuid_documento = extrair_uuid_documento(evento, payload)
    tipo_evento = extrair_tipo_evento(evento, payload)
    nome_documento = extrair_nome_documento(evento, payload)
    data_evento = extrair_data_evento(evento, payload)

    if not uuid_documento:
        marcar_evento_erro(
            conexao,
            id_evento=id_evento,
            status="ERRO",
            mensagem="Não consegui extrair UUIDDocumentoD4 do webhook.",
        )
        return {"id_evento": id_evento, "status": "erro_sem_uuid"}

    contrato = buscar_contrato_por_uuid(conexao, uuid_documento)
    if not contrato:
        marcar_evento_erro(
            conexao,
            id_evento=id_evento,
            status="DOCUMENTO_NAO_ENCONTRADO",
            mensagem=f"UUIDDocumentoD4 não encontrado na FatoContratoD4: {uuid_documento}",
        )
        return {"id_evento": id_evento, "status": "documento_nao_encontrado", "uuid": uuid_documento}

    status_evento = resolver_status_por_evento(tipo_evento, payload)
    signatarios = extrair_signatarios(payload, tipo_evento, data_evento)

    atualizar_contrato_d4(
        conexao,
        contrato=contrato,
        status_evento=status_evento,
        nome_documento=nome_documento,
    )

    propagacao_euromidia = propagar_status_contrato_euromidia(
        conexao,
        id_fato_controle_contrato=contrato.get("IDFatoControleContratosEuromidia"),
        id_fato_kanban_card=contrato.get("IDFatoKanbanCard"),
        id_status_contrato=status_evento.get("IDDimStatusContratos"),
    )

    status_evento_historico = dict(status_evento)
    if propagacao_euromidia.get("propagou") and propagacao_euromidia.get("status_final") is not None:
        status_evento_historico["IDDimStatusContratos"] = propagacao_euromidia.get("status_final")

    inserir_historico_d4(
        conexao,
        contrato=contrato,
        status_evento=status_evento_historico,
        data_evento=data_evento,
    )

    acoes_signatarios: list[str] = []
    for signatario in signatarios:
        acao = upsert_signatario_d4(
            conexao,
            contrato=contrato,
            signatario=signatario,
            payload_json=payload_json,
        )
        acoes_signatarios.append(acao)

    try:
        contrato_pdf = dict(contrato)
        contrato_pdf["NomeDocumentoD4"] = nome_documento or contrato.get("NomeDocumentoD4")

        if evento_deve_atualizar_pdf_assinado_d4(
            tipo_evento=tipo_evento,
            payload=payload,
            status_evento=status_evento,
        ):
            pdf_local = atualizar_pdf_local_assinado_contrato_d4(
                conexao,
                contrato_pdf,
                nome_documento or contrato.get("NomeDocumentoD4"),
            )
        else:
            pdf_local = garantir_pdf_local_contrato_d4(
                conexao,
                contrato_pdf,
                nome_documento or contrato.get("NomeDocumentoD4"),
            )
    except Exception as exc:
        logging.exception(
            "D4SIGN_PDF_LOCAL_WEBHOOK | webhook processado, mas falhei ao garantir/atualizar PDF local | id_evento=%s | uuid=%s | id_contrato=%s | tipo_evento=%s",
            id_evento,
            uuid_documento,
            contrato.get("IDFatoControleContratosEuromidia"),
            tipo_evento,
        )
        pdf_local = {"ok": False, "status": "erro_pdf_local_webhook", "erro": str(exc)}

    marcar_evento_processado(
        conexao,
        id_evento=id_evento,
        id_fato_contrato_d4=int(contrato["IDFatoContratoD4"]),
    )

    return {
        "id_evento": id_evento,
        "uuid": uuid_documento,
        "id_fato_contrato_d4": int(contrato["IDFatoContratoD4"]),
        "tipo_evento": tipo_evento,
        "status": "processado",
        "id_status_d4": status_evento.get("IDDimStatusD4"),
        "id_status_contrato": status_evento.get("IDDimStatusContratos"),
        "propagacao_euromidia": propagacao_euromidia,
        "signatarios": len(signatarios),
        "acoes_signatarios": acoes_signatarios,
        "pdf_local": pdf_local,
    }


@dag(
    dag_id="pipeline_processar_webhook_d4sign",
    description="Processa eventos recebidos via webhook D4Sign e atualiza contrato, signatários e histórico.",
    doc_md=DOCUMENTACAO_DAG,
    tags=TAGS_DAG,
    schedule="*/1 * * * *",
    start_date=pendulum.datetime(2026, 6, 9, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "Euromidia",
        "retries": 1,
        "retry_delay": timedelta(minutes=1),
    },
)
def pipeline_processar_webhook_d4sign():
    @task(task_id="processar_eventos_webhook")
    def processar_eventos_webhook() -> dict[str, Any]:
        """Reservo eventos pendentes e atualizo as tabelas finais."""
        conf = obter_conf_dag()
        id_evento_webhook = converter_int(conf.get("id_evento_webhook"))

        limite_eventos = converter_int(os.getenv("D4SIGN_WEBHOOK_LIMITE_EVENTOS_POR_EXECUCAO", "50")) or 50
        limite_eventos = max(1, min(limite_eventos, 500))

        hook_sql = HookSqlServer()
        engine = hook_sql.obter_engine()

        with engine.begin() as conexao:
            eventos = reservar_eventos(
                conexao,
                id_evento_webhook=id_evento_webhook,
                limite_eventos=limite_eventos,
            )

        logging.info("Eventos reservados para processamento: %s", len(eventos))

        resultados: list[dict[str, Any]] = []
        erros: list[dict[str, Any]] = []

        for evento in eventos:
            id_evento = int(evento["IDFatoContratoD4WebhookEvento"])

            try:
                with engine.begin() as conexao:
                    resultado = processar_evento(conexao, dict(evento))
                    resultados.append(resultado)

            except Exception as erro:
                logging.exception("Erro ao processar webhook D4Sign. id_evento=%s", id_evento)
                erros.append({"id_evento": id_evento, "erro": str(erro)})

                with engine.begin() as conexao:
                    marcar_evento_erro(
                        conexao,
                        id_evento=id_evento,
                        status="ERRO",
                        mensagem=str(erro),
                    )

        resumo = {
            "id_evento_conf": id_evento_webhook,
            "eventos_reservados": len(eventos),
            "eventos_processados": len(resultados),
            "eventos_com_erro": len(erros),
            "resultados": resultados[:50],
            "erros": erros[:20],
        }

        logging.info("Resumo pipeline_processar_webhook_d4sign: %s", resumo)
        return resumo

    processar_eventos_webhook()


pipeline_processar_webhook_d4sign()
