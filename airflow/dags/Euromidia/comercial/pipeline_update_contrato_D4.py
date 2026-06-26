import base64
import json
import logging
import os
import time
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
except ImportError:
    from airflow.sdk import dag, task

from D4Sign import HookD4Sign
from SqlServer import HookSqlServer


MAPA_STATUS_D4_PADRAO: dict[int, str] = {
    1: "Processando",
    2: "Aguardando Signatários",
    3: "Aguardando Assinaturas",
    4: "Finalizado",
    5: "Arquivado",
    6: "Cancelado",
    7: "Editando",
}


TABELA_CONTRATO_D4 = "[Integracao].[Silver].[FatoContratoD4]"
TABELA_SIGNATARIO_D4 = "[Integracao].[Silver].[FatoContratoD4Signatario]"
TABELA_HISTORICO_D4 = "[Integracao].[Silver].[DimHistoricoContratosD4]"
TABELA_CONTROLE_CONTRATOS = "[Integracao].[Silver].[FatoControleContratosEuromidia]"
TABELA_CONTROLE_CONTRATOS_ITENS = "[Integracao].[Silver].[FatoControleContratosItensEuromidia]"
TABELA_STATUS_CONTRATOS = "[Integracao].[Silver].[DimStatusContratos]"
TABELA_SOLICITACAO_CONTRATO = "[Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]"
TABELA_CONTRATO_CARD = "[Integracao].[Silver].[FatoContratoCardEuromidia]"

ID_EMPRESA_PROPRIETARIA_EUROMIDIA = 3

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


def erro_d4sign_limite_ou_401(erro: Any) -> bool:
    """Identifico bloqueio/limite da D4Sign para parar novas chamadas na mesma execução."""
    texto = str(erro or "").lower()
    return (
        "status http: 401" in texto
        or "status=401" in texto
        or "status 401" in texto
        or "esta chave da api" in texto
        or "atingiu o tempo limite" in texto
        or "tempo limite para este método" in texto
        or "tempo limite para este metodo" in texto
    )


def pasta_pdf_local_contrato_d4() -> Path:
    """Pasta física compartilhada onde os PDFs de contratos D4Sign ficam salvos."""
    pasta = str(
        os.getenv("D4SIGN_PDF_LOCAL_PASTA_CONTRATO")
        or "/opt/airflow/FlaskApp/Contratos/Euromidia/Anexos/Contrato"
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
             ORDER BY IDFatoArquivosContratos DESC;
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


def garantir_pdf_local_contrato_d4(
    conexao,
    contrato: dict[str, Any],
    nome_documento_d4: str | None = None,
    *,
    permitir_download_d4sign: bool = True,
) -> dict[str, Any]:
    """Garante o registro em FatoArquivosContratosEuromidia e, quando possível, o PDF físico.

    Regra aplicada:
    - contrato existe em FatoControleContratosEuromidia;
    - card existe/vem vinculado;
    - documento existe na FatoContratoD4 com UUIDDocumentoD4;
    - se não existir linha na [Integracao].[Silver].[FatoArquivosContratosEuromidia], insere a linha;
    - depois tenta baixar/salvar o PDF e atualiza tamanho/nome/url.

    Importante: se a D4Sign estiver bloqueada ou a pasta estiver sem permissão, o INSERT inicial
    permanece gravado para o contrato não sumir da tabela de arquivos. A próxima execução tenta
    baixar novamente e atualiza o mesmo registro.
    """
    if not env_bool("D4SIGN_GARANTIR_PDF_LOCAL_DAGS_HABILITADO", "1"):
        return {"ok": False, "status": "desabilitado"}

    id_controle = converter_int(contrato.get("IDFatoControleContratosEuromidia"))
    id_card = converter_int(contrato.get("IDFatoKanbanCard"))
    id_d4 = converter_int(contrato.get("IDFatoContratoD4"))
    uuid_doc = uuid_d4_valido(contrato.get("UUIDDocumentoD4"))

    if not id_controle:
        return {"ok": False, "status": "sem_id_controle_para_pdf"}

    if not id_card:
        return {
            "ok": False,
            "status": "sem_card_vinculado_para_pdf",
            "id_fato_controle_contratos": id_controle,
            "id_fato_contrato_d4": id_d4,
            "uuid_documento_d4": uuid_doc,
        }

    if not id_d4 or not uuid_doc:
        return {
            "ok": False,
            "status": "sem_documento_d4_ou_uuid_para_pdf",
            "id_fato_controle_contratos": id_controle,
            "id_fato_kanban_card": id_card,
            "id_fato_contrato_d4": id_d4,
            "uuid_documento_d4": uuid_doc,
        }

    nome_arquivo = montar_nome_pdf_local_contrato_d4(contrato, nome_documento_d4)
    if not nome_arquivo:
        return {
            "ok": False,
            "status": "sem_nome_arquivo_pdf",
            "id_fato_controle_contratos": id_controle,
            "id_fato_kanban_card": id_card,
            "id_fato_contrato_d4": id_d4,
            "uuid_documento_d4": uuid_doc,
        }

    nome_final = Path(nome_arquivo).name
    row_existente = buscar_registro_pdf_arquivo(
        conexao,
        id_controle=int(id_controle),
        nome_arquivo=nome_final,
        id_contrato_d4=int(id_d4),
    )

    # Se a tabela já tem nome para esse IDFatoContratoD4, respeito esse nome e verifico exatamente esse PDF na pasta.
    # Isso evita criar duplicidade quando a linha já foi gravada antes com outro padrão de nome.
    if row_existente and str(row_existente.get("NomeArquivo") or "").strip():
        nome_existente = Path(str(row_existente.get("NomeArquivo") or "")).name
        if nome_existente.lower().endswith(".pdf"):
            nome_final = nome_existente

    caminho = (pasta_pdf_local_contrato_d4() / nome_final).resolve()

    # 1) Se já existe tabela + arquivo físico válido, não baixa de novo.
    if row_existente and caminho_pdf_valido(caminho):
        return {
            "ok": True,
            "status": "ja_existia_sem_download",
            "download_d4sign_executado": False,
            "id_fato_arquivos_contratos": int(row_existente["IDFatoArquivosContratos"]),
            "id_fato_controle_contratos": int(id_controle),
            "id_fato_kanban_card": int(id_card),
            "id_fato_contrato_d4": int(id_d4),
            "uuid_documento_d4": uuid_doc,
            "nome_arquivo": nome_final,
            "url_anexo": url_anexo_contrato_pdf(nome_final),
            "caminho_arquivo": str(caminho),
        }

    # 2) Se o PDF físico já existe, mas a tabela não existe ou está incompleta, registra/atualiza a tabela.
    if caminho_pdf_valido(caminho):
        registro = registrar_pdf_tabela_arquivos(
            conexao,
            contrato=contrato,
            nome_arquivo=nome_final,
            tamanho_arquivo=int(caminho.stat().st_size),
        )
        return {
            **registro,
            "status": "arquivo_existente_registrado_sem_download",
            "download_d4sign_executado": False,
            "uuid_documento_d4": uuid_doc,
            "caminho_arquivo": str(caminho),
        }

    # 3) Se NÃO existe na tabela correta, insere primeiro na FatoArquivosContratosEuromidia.
    #    Assim o contrato aparece na tabela mesmo se a D4Sign bloquear o download ou a pasta estiver sem permissão.
    registro_previo = None
    if not row_existente:
        registro_previo = registrar_pdf_tabela_arquivos(
            conexao,
            contrato=contrato,
            nome_arquivo=nome_final,
            tamanho_arquivo=0,
        )
        logging.info(
            "D4SIGN_PDF_LOCAL_DAG | INSERT inicial em %s | IDFatoArquivosContratos=%s | IDFatoControle=%s | IDFatoContratoD4=%s | arquivo=%s",
            TABELA_ARQUIVOS_CONTRATOS,
            registro_previo.get("id_fato_arquivos_contratos"),
            id_controle,
            id_d4,
            nome_final,
        )

    # 4) Se a API da D4Sign já bloqueou nesta execução, não martelo o endpoint de download.
    #    O registro fica gravado na tabela correta e a próxima execução tenta baixar novamente.
    if not permitir_download_d4sign:
        if registro_previo:
            return {
                **registro_previo,
                "ok": False,
                "status": "registro_inserido_pdf_pendente_api_d4sign_bloqueada",
                "download_d4sign_executado": False,
                "uuid_documento_d4": uuid_doc,
                "caminho_arquivo": str(caminho),
            }

        return {
            "ok": False,
            "status": "registro_existente_pdf_pendente_api_d4sign_bloqueada",
            "download_d4sign_executado": False,
            "id_fato_arquivos_contratos": int(row_existente["IDFatoArquivosContratos"]) if row_existente else None,
            "id_fato_controle_contratos": int(id_controle),
            "id_fato_kanban_card": int(id_card),
            "id_fato_contrato_d4": int(id_d4),
            "uuid_documento_d4": uuid_doc,
            "nome_arquivo": nome_final,
            "url_anexo": url_anexo_contrato_pdf(nome_final),
            "caminho_arquivo": str(caminho),
        }

    # 5) Depois tenta baixar e salvar o PDF. Se falhar, não desfaz o INSERT inicial.
    try:
        dados_pdf = executar_download_pdf_d4sign(uuid_doc)
        caminho_salvo = gravar_pdf_local_atomicamente(dados_pdf, nome_final)
        registro_final = registrar_pdf_tabela_arquivos(
            conexao,
            contrato=contrato,
            nome_arquivo=nome_final,
            tamanho_arquivo=int(caminho_salvo.stat().st_size),
        )
        return {
            **registro_final,
            "status": "baixado_salvo_registrado",
            "download_d4sign_executado": True,
            "uuid_documento_d4": uuid_doc,
            "caminho_arquivo": str(caminho_salvo),
        }
    except Exception as exc:
        logging.exception(
            "D4SIGN_PDF_LOCAL_DAG | registro gravado, mas falhou download/salvamento do PDF | IDFatoControle=%s | IDFatoContratoD4=%s | UUID=%s",
            id_controle,
            id_d4,
            uuid_doc,
        )
        if registro_previo:
            return {
                **registro_previo,
                "ok": False,
                "status": "registro_inserido_pdf_pendente_download",
                "download_d4sign_executado": False,
                "uuid_documento_d4": uuid_doc,
                "caminho_arquivo": str(caminho),
                "erro_download_pdf": str(exc),
            }

        return {
            "ok": False,
            "status": "registro_existente_pdf_pendente_download",
            "download_d4sign_executado": False,
            "id_fato_arquivos_contratos": int(row_existente["IDFatoArquivosContratos"]) if row_existente else None,
            "id_fato_controle_contratos": int(id_controle),
            "id_fato_kanban_card": int(id_card),
            "id_fato_contrato_d4": int(id_d4),
            "uuid_documento_d4": uuid_doc,
            "nome_arquivo": nome_final,
            "url_anexo": url_anexo_contrato_pdf(nome_final),
            "caminho_arquivo": str(caminho),
            "erro_download_pdf": str(exc),
        }


def buscar_contrato_d4_para_pdf_por_id(
    conexao,
    *,
    id_fato_contrato_d4: int | None = None,
    id_fato_controle_contrato: int | None = None,
) -> dict[str, Any] | None:
    """Busca o contrato D4 já gravado e resolve o IDFatoKanbanCard pelo vínculo do card quando vier nulo."""
    id_d4 = converter_int(id_fato_contrato_d4)
    id_controle = converter_int(id_fato_controle_contrato)
    if not id_d4 and not id_controle:
        return None

    linha = conexao.execute(
        text(f"""
            SELECT TOP (1)
                d.IDFatoContratoD4,
                d.IDDimStatusD4,
                d.IDEmpresa AS IDEmpresa,
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
            FROM {TABELA_CONTRATO_D4} d WITH (READPAST)
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
            WHERE ISNULL(d.BitAtivo, 1) = 1
              AND NULLIF(LTRIM(RTRIM(d.UUIDDocumentoD4)), '') IS NOT NULL
              AND (
                    (:id_fato_contrato_d4 IS NOT NULL AND d.IDFatoContratoD4 = :id_fato_contrato_d4)
                 OR (:id_fato_controle_contrato IS NOT NULL AND d.IDFatoControleContratosEuromidia = :id_fato_controle_contrato)
              )
            ORDER BY
                CASE WHEN :id_fato_contrato_d4 IS NOT NULL AND d.IDFatoContratoD4 = :id_fato_contrato_d4 THEN 0 ELSE 1 END,
                COALESCE(d.DataAtualizacao, d.DataCriacao) DESC,
                d.IDFatoContratoD4 DESC;
        """),
        {
            "id_fato_contrato_d4": int(id_d4) if id_d4 else None,
            "id_fato_controle_contrato": int(id_controle) if id_controle else None,
        },
    ).mappings().first()
    return dict(linha) if linha else None


def garantir_pdf_local_contrato_d4_gravado(
    conexao,
    contrato_gravado: dict[str, Any],
    *,
    permitir_download_d4sign: bool = True,
) -> dict[str, Any]:
    """Depois que o contrato D4 foi gravado, verifica a pasta e baixa o PDF se ele não existir."""
    contrato_pdf = buscar_contrato_d4_para_pdf_por_id(
        conexao,
        id_fato_contrato_d4=contrato_gravado.get("IDFatoContratoD4"),
        id_fato_controle_contrato=contrato_gravado.get("IDFatoControleContratosEuromidia"),
    )
    if not contrato_pdf:
        contrato_pdf = dict(contrato_gravado)

    return garantir_pdf_local_contrato_d4(
        conexao,
        contrato_pdf,
        contrato_pdf.get("NomeDocumentoD4") or contrato_gravado.get("NomeDocumentoD4"),
        permitir_download_d4sign=permitir_download_d4sign,
    )


def garantir_pdfs_locais_contratos_d4(
    hook_sql: HookSqlServer,
    *,
    permitir_download_d4sign: bool = True,
) -> dict[str, Any]:
    """Reconcilia contratos com card, documento D4 e PDF/registro de arquivo.

    Regra de negócio aplicada:
    1. parte do contrato da Euromídia ativo em FatoControleContratosEuromidia;
    2. exige vínculo com card em FatoContratoCardEuromidia;
    3. exige documento gravado em FatoContratoD4 com UUIDDocumentoD4;
    4. verifica se falta linha/informação em FatoArquivosContratosEuromidia;
    5. mesmo quando a linha já existe, valida o PDF físico na pasta;
    6. se o PDF físico não existir ou for inválido, tenta baixar da D4Sign e atualiza a tabela.

    O download não depende de mudança de status. Se existe contrato + card + UUID D4,
    esta rotina tenta garantir a tabela de arquivos e o PDF físico.
    """
    if not env_bool("D4SIGN_GARANTIR_PDF_LOCAL_DAGS_HABILITADO", "1"):
        return {"habilitado": False, "status": "desabilitado"}

    limite = obter_int_env("D4SIGN_GARANTIR_PDF_LOCAL_LIMITE_POR_EXECUCAO", 5000)
    limite = max(1, min(limite, 10000))

    # Como o SQL Server não sabe se o arquivo físico existe no volume do Airflow,
    # por padrão eu incluo todos os contratos vinculados com D4 e deixo a função Python
    # validar a pasta. Se quiser varrer só ausentes/incompletos na tabela, defina como 0.
    verificar_fisico_todos = env_bool("D4SIGN_GARANTIR_PDF_LOCAL_VERIFICAR_FISICO_TODOS", "1")
    filtro_arquivo_sql = "" if verificar_fisico_todos else """
          AND (
                arquivo.IDFatoArquivosContratos IS NULL
             OR NULLIF(LTRIM(RTRIM(ISNULL(arquivo.NomeArquivo, ''))), '') IS NULL
             OR NULLIF(LTRIM(RTRIM(ISNULL(arquivo.UrlAnexo, ''))), '') IS NULL
             OR LOWER(LTRIM(RTRIM(ISNULL(arquivo.Extensao, '')))) <> 'pdf'
             OR ISNULL(arquivo.TamanhoArquivo, 0) <= 0
          )
    """

    contratos = hook_sql.executar_select(
        f"""
        ;WITH ContratosComCard AS (
            SELECT
                c.IDFatoControleContratosEuromidia,
                c.IDDimStatusContratos AS IDDimStatusContratosControle,
                c.DataAtualizacao AS DataAtualizacaoControle,
                c.DataLancamento AS DataLancamentoControle,
                card.IDFatoContratoCardEuromidia,
                card.IDFatoControleContratosItensEuromidia,
                card.IDFatoKanbanCard
            FROM {TABELA_CONTROLE_CONTRATOS} c
            OUTER APPLY (
                SELECT TOP (1)
                    cc.IDFatoContratoCardEuromidia,
                    cc.IDFatoControleContratosItensEuromidia,
                    cc.IDFatoKanbanCard
                FROM {TABELA_CONTRATO_CARD} cc WITH (READPAST)
                WHERE cc.IDFatoControleContratosEuromidia = c.IDFatoControleContratosEuromidia
                  AND cc.IDFatoKanbanCard IS NOT NULL
                ORDER BY
                    cc.DataAtualizacao DESC,
                    cc.IDFatoContratoCardEuromidia DESC
            ) card
            WHERE ISNULL(c.BitAtivo, 1) = 1
              AND card.IDFatoKanbanCard IS NOT NULL
        ),
        ContratosD4ComCard AS (
            SELECT
                d.IDFatoContratoD4,
                d.IDDimStatusD4,
                d.IDEmpresa AS IDEmpresa,
                d.IDDimCofreD4,
                cc.IDFatoControleContratosEuromidia,
                cc.IDFatoContratoCardEuromidia,
                cc.IDFatoControleContratosItensEuromidia,
                cc.DataAtualizacaoControle,
                cc.DataLancamentoControle,
                COALESCE(d.IDFatoKanbanCard, cc.IDFatoKanbanCard) AS IDFatoKanbanCard,
                COALESCE(d.IDDimStatusContratos, cc.IDDimStatusContratosControle) AS IDDimStatusContratos,
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
                d.BitAtivo,
                arquivo.IDFatoArquivosContratos AS IDFatoArquivosContratosExistente,
                arquivo.NomeArquivo AS NomeArquivoExistente,
                arquivo.UrlAnexo AS UrlAnexoExistente,
                arquivo.Extensao AS ExtensaoExistente,
                arquivo.TamanhoArquivo AS TamanhoArquivoExistente,
                arquivo.DataAtualizado AS DataArquivoAtualizado
            FROM ContratosComCard cc
            INNER JOIN {TABELA_CONTRATO_D4} d WITH (READPAST)
                ON d.IDFatoControleContratosEuromidia = cc.IDFatoControleContratosEuromidia
               AND ISNULL(d.BitAtivo, 1) = 1
               AND NULLIF(LTRIM(RTRIM(d.UUIDDocumentoD4)), '') IS NOT NULL
            OUTER APPLY (
                SELECT TOP (1)
                    a.IDFatoArquivosContratos,
                    a.IDFatoControleContratosEuromidia,
                    a.IDFatoKanbanCard,
                    a.IDFatoContratoD4,
                    a.NomeArquivo,
                    a.UrlAnexo,
                    a.Extensao,
                    a.TamanhoArquivo,
                    a.DataAtualizado
                FROM {TABELA_ARQUIVOS_CONTRATOS} a WITH (READPAST)
                WHERE (
                        a.IDFatoContratoD4 = d.IDFatoContratoD4
                     OR (
                            a.IDFatoContratoD4 IS NULL
                        AND a.IDFatoControleContratosEuromidia = cc.IDFatoControleContratosEuromidia
                        )
                      )
                ORDER BY
                    CASE WHEN a.IDFatoContratoD4 = d.IDFatoContratoD4 THEN 0 ELSE 1 END,
                    a.DataAtualizado DESC,
                    a.IDFatoArquivosContratos DESC
            ) arquivo
            WHERE 1 = 1
            {filtro_arquivo_sql}
        )
        SELECT TOP ({limite})
            *
        FROM ContratosD4ComCard
        ORDER BY
            CASE
                WHEN IDFatoArquivosContratosExistente IS NULL THEN 0
                WHEN NULLIF(LTRIM(RTRIM(ISNULL(NomeArquivoExistente, ''))), '') IS NULL THEN 1
                WHEN NULLIF(LTRIM(RTRIM(ISNULL(UrlAnexoExistente, ''))), '') IS NULL THEN 2
                WHEN LOWER(LTRIM(RTRIM(ISNULL(ExtensaoExistente, '')))) <> 'pdf' THEN 3
                WHEN ISNULL(TamanhoArquivoExistente, 0) <= 0 THEN 4
                ELSE 9
            END ASC,
            COALESCE(DataAtualizacao, DataCriacao, DataAtualizacaoControle, DataLancamentoControle) DESC,
            IDFatoContratoD4 DESC;
        """
    )

    logging.info(
        "D4SIGN_PDF_LOCAL_DAG | reconciliação PDF | candidatos contrato+card+D4: %s | verificar_fisico_todos=%s | download_permitido=%s",
        len(contratos),
        verificar_fisico_todos,
        permitir_download_d4sign,
    )

    resultados: list[dict[str, Any]] = []
    erros: list[dict[str, Any]] = []
    engine = hook_sql.obter_engine()
    for contrato in contratos:
        try:
            with engine.begin() as conexao:
                resultado = garantir_pdf_local_contrato_d4(
                    conexao,
                    dict(contrato),
                    contrato.get("NomeDocumentoD4"),
                    permitir_download_d4sign=permitir_download_d4sign,
                )
            resultados.append(resultado)
        except Exception as exc:
            logging.exception(
                "D4SIGN_PDF_LOCAL_DAG | falha ao reconciliar PDF | IDFatoContratoD4=%s | IDFatoControle=%s | IDFatoKanbanCard=%s | UUID=%s",
                contrato.get("IDFatoContratoD4"),
                contrato.get("IDFatoControleContratosEuromidia"),
                contrato.get("IDFatoKanbanCard"),
                contrato.get("UUIDDocumentoD4"),
            )
            erros.append({
                "IDFatoContratoD4": contrato.get("IDFatoContratoD4"),
                "IDFatoControleContratosEuromidia": contrato.get("IDFatoControleContratosEuromidia"),
                "IDFatoKanbanCard": contrato.get("IDFatoKanbanCard"),
                "UUIDDocumentoD4": contrato.get("UUIDDocumentoD4"),
                "erro": str(exc),
            })

    return {
        "habilitado": True,
        "download_d4sign_permitido": bool(permitir_download_d4sign),
        "verificar_fisico_todos": bool(verificar_fisico_todos),
        "contratos_controle_card_d4_candidatos": len(contratos),
        "pdfs_ok": len([r for r in resultados if r.get("ok")]),
        "pdfs_com_pendencia": len([r for r in resultados if not r.get("ok")]),
        "pdfs_com_erro": len(erros),
        "baixados_salvos": len([r for r in resultados if r.get("status") == "baixado_salvo_registrado"]),
        "ja_existiam_validos": len([r for r in resultados if r.get("status") == "ja_existia_sem_download"]),
        "registrados_sem_download": len([r for r in resultados if r.get("status") == "arquivo_existente_registrado_sem_download"]),
        "pendentes_download": len([r for r in resultados if "pendente" in str(r.get("status") or "")]),
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

TAGS_DAG = ["Euromidia", "Contratos", "D4Sign", "SQLServer", "API"]

DOCUMENTACAO_DAG = """
# Pipeline de atualização de contratos D4Sign

Esta DAG consulta a API da D4Sign a cada 10 minutos e atualiza a tabela
`[Integracao].[Silver].[FatoContratoD4]` quando o status do documento mudar.

Por padrão, para reduzir chamadas desnecessárias à API, ela consulta apenas contratos
ativos que ainda não estão em status final: Finalizado, Arquivado ou Cancelado.

## Fluxo executado

1. Valida conexão com SQL Server usando `HookSqlServer`.
2. Valida conexão com D4Sign usando `HookD4Sign`.
3. Busca os status cadastrados em `[Integracao].[Silver].[DimStatusD4]`.
4. Busca contratos ativos em `[Integracao].[Silver].[FatoContratoD4]` com `UUIDDocumentoD4` preenchido.
5. Consulta cada documento na D4Sign pelo UUID.
6. Converte o status retornado pela API para o `IDDimStatusD4` da dimensão.
7. Atualiza a tabela fato somente quando houver mudança de status ou metadados relevantes.
8. Propaga o status para `[Integracao].[Silver].[FatoControleContratosEuromidia]`.
9. Sincroniza o texto de status em `[Integracao].[Silver].[FatoControleContratosItensEuromidia]`.

## Status esperados

- 1 = Processando
- 2 = Aguardando Signatários
- 3 = Aguardando Assinaturas
- 4 = Finalizado
- 5 = Arquivado
- 6 = Cancelado
- 7 = Editando

## Variáveis de ambiente usadas pelo hook D4Sign

- `TOKEN_D4SIGN`
- `CRYPTKEY_D4SIGN`
- `BASE_URL_D4SIGN` opcional, padrão `https://secure.d4sign.com.br/api/v1`

## Variáveis opcionais desta DAG

- `D4SIGN_LIMITE_CONTRATOS_POR_EXECUCAO`: limite de contratos consultados por execução. Padrão: 100.
- `D4SIGN_INTERVALO_SEGUNDOS_ENTRE_CONSULTAS`: pausa entre consultas na API. Padrão: 0.2.
- `D4SIGN_LIMITE_SINCRONIZACAO_LOCAL_POR_EXECUCAO`: limite da sincronização local sem API. Padrão: 1000.
- `D4SIGN_CONSULTAR_STATUS_FINAIS`: se for `1`, também consulta contratos já finalizados, arquivados ou cancelados. Padrão: `0`.
- `D4SIGN_VALIDAR_CONEXAO_A_CADA_EXECUCAO`: se for `1`, chama `listar_cofres()` antes de consultar contratos. Padrão: `0`, para evitar chamada extra na API a cada minuto.

## Observação técnica

O endpoint usado é encapsulado pelo método `listar_documento(uuid_documento)` do `HookD4Sign`.
"""


def normalizar_texto(valor: Any) -> str:
    """Normalizo texto para comparar nomes de status sem acento, caixa e espaços extras."""
    if valor is None:
        return ""

    texto = str(valor).strip().lower()
    texto = " ".join(texto.split())
    texto_sem_acento = unicodedata.normalize("NFKD", texto)
    texto_sem_acento = "".join(
        caractere for caractere in texto_sem_acento if not unicodedata.combining(caractere)
    )
    return texto_sem_acento


def converter_int(valor: Any) -> int | None:
    """Converto valores numéricos vindos da API para inteiro, quando possível."""
    if valor is None:
        return None

    if isinstance(valor, bool):
        return None

    if isinstance(valor, int):
        return valor

    texto = str(valor).strip()
    if not texto:
        return None

    try:
        return int(float(texto.replace(",", ".")))
    except (TypeError, ValueError):
        return None


def limitar_texto(valor: Any, limite: int) -> str | None:
    """Converto para texto e limito tamanho para reduzir risco de erro de truncamento."""
    if valor is None:
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    return texto[:limite]


def valores_diferentes(valor_banco: Any, valor_api: Any) -> bool:
    """Comparo banco e API sem considerar campo vazio da API como mudança."""
    if valor_api is None:
        return False

    if isinstance(valor_api, int):
        return converter_int(valor_banco) != valor_api

    texto_banco = "" if valor_banco is None else str(valor_banco).strip()
    texto_api = str(valor_api).strip()
    return texto_banco != texto_api


def obter_valor_por_caminho(dados: Any, caminho: str) -> Any:
    """Busco valor em dicionário usando caminho com ponto, exemplo: safe.uuid."""
    atual = dados

    for parte in caminho.split("."):
        if isinstance(atual, dict):
            atual = atual.get(parte)
            continue

        if isinstance(atual, list) and atual:
            atual = atual[0]
            if isinstance(atual, dict):
                atual = atual.get(parte)
                continue

        return None

    return atual


def primeiro_valor(dados: dict[str, Any], caminhos: list[str]) -> Any:
    """Retorno o primeiro valor encontrado entre vários nomes possíveis da API."""
    for caminho in caminhos:
        valor = obter_valor_por_caminho(dados, caminho)
        if valor is not None and str(valor).strip() != "":
            return valor

    return None


def desembrulhar_documento(resposta_api: Any) -> dict[str, Any]:
    """Extraio o dicionário do documento mesmo quando a API retorna envelopes diferentes."""
    if isinstance(resposta_api, list):
        for item in resposta_api:
            if isinstance(item, dict):
                return item
        return {}

    if not isinstance(resposta_api, dict):
        return {}

    chaves_documento = [
        "uuidDoc",
        "uuid_document",
        "uuid_documento",
        "nameDoc",
        "name_document",
        "status",
        "idStatus",
        "fase",
    ]

    if any(chave in resposta_api for chave in chaves_documento):
        return resposta_api

    for chave_envelope in ["document", "documento", "documents", "data", "message", "response", "result"]:
        valor = resposta_api.get(chave_envelope)

        if isinstance(valor, dict):
            documento = desembrulhar_documento(valor)
            if documento:
                return documento

        if isinstance(valor, list):
            for item in valor:
                if isinstance(item, dict):
                    documento = desembrulhar_documento(item)
                    if documento:
                        return documento

    return resposta_api


def montar_mapas_status(linhas_status: list[dict[str, Any]]) -> tuple[dict[int, str], dict[str, int]]:
    """Monto mapas de status usando a tabela DimStatusD4 e fallback padrão."""
    mapa_id_para_nome = dict(MAPA_STATUS_D4_PADRAO)

    for linha in linhas_status:
        id_status = converter_int(linha.get("IDDimStatusD4"))
        nome_status = limitar_texto(linha.get("NomeStatus"), 90)

        if id_status and nome_status:
            mapa_id_para_nome[id_status] = nome_status

    mapa_nome_para_id = {
        normalizar_texto(nome_status): id_status
        for id_status, nome_status in mapa_id_para_nome.items()
        if nome_status
    }

    return mapa_id_para_nome, mapa_nome_para_id


def resolver_status_documento(
    documento: dict[str, Any],
    mapa_id_para_nome: dict[int, str],
    mapa_nome_para_id: dict[str, int],
) -> tuple[int | None, str | None]:
    """Resolvo o IDDimStatusD4 e o nome do status a partir do retorno da D4Sign."""
    valor_status = primeiro_valor(
        documento,
        [
            "idStatus",
            "id_status",
            "statusId",
            "IDStatus",
            "IDFaseD4",
            "id_fase",
            "fase_id",
            "status",
            "fase",
        ],
    )

    id_status = converter_int(valor_status)
    if id_status in mapa_id_para_nome:
        return id_status, mapa_id_para_nome[id_status]

    valor_nome_status = primeiro_valor(
        documento,
        [
            "nameStatus",
            "statusName",
            "NomeStatus",
            "nome_status",
            "name_status",
            "NomeFaseD4",
            "nomeFase",
            "phaseName",
            "fase_nome",
            "status",
            "fase",
        ],
    )

    nome_normalizado = normalizar_texto(valor_nome_status)
    id_status_por_nome = mapa_nome_para_id.get(nome_normalizado)

    if id_status_por_nome:
        return id_status_por_nome, mapa_id_para_nome.get(id_status_por_nome)

    return None, limitar_texto(valor_nome_status, 90)


def extrair_metadados_documento(
    documento: dict[str, Any],
    id_status: int | None,
    nome_status: str | None,
) -> dict[str, Any]:
    """Monto o pacote de campos que pode ser gravado na FatoContratoD4."""
    id_fase_api = converter_int(
        primeiro_valor(
            documento,
            ["IDFaseD4", "idStatus", "id_status", "statusId", "fase", "status"],
        )
    )

    return {
        "IDDimStatusD4": id_status,
        "IDFaseD4": id_fase_api or id_status,
        "NomeFaseD4": limitar_texto(nome_status, 90),
        "UUIDCofreD4": limitar_texto(
            primeiro_valor(
                documento,
                [
                    "UUIDCofreD4",
                    "uuidSafe",
                    "uuid_safe",
                    "safe.uuid",
                    "safe.uuidSafe",
                    "cofre.uuid",
                ],
            ),
            100,
        ),
        "NomeDocumentoD4": limitar_texto(
            primeiro_valor(
                documento,
                [
                    "NomeDocumentoD4",
                    "nameDoc",
                    "name_document",
                    "document.name",
                    "nome_documento",
                    "name",
                ],
            ),
            255,
        ),
        "NomeCofreD4": limitar_texto(
            primeiro_valor(
                documento,
                [
                    "NomeCofreD4",
                    "nameSafe",
                    "name_safe",
                    "safe.name",
                    "safe.nameSafe",
                    "cofre.nome",
                ],
            ),
            255,
        ),
        "TipoArquivoD4": limitar_texto(
            primeiro_valor(
                documento,
                ["TipoArquivoD4", "type", "fileType", "file_type", "tipo", "extension"],
            ),
            50,
        ),
        "QuantidadePaginas": converter_int(
            primeiro_valor(
                documento,
                ["QuantidadePaginas", "pages", "pageCount", "page_count", "qtd_pages"],
            )
        ),
        "TamanhoArquivoD4": converter_int(
            primeiro_valor(
                documento,
                ["TamanhoArquivoD4", "size", "fileSize", "file_size", "bytes"],
            )
        ),
        "StatusComentarioD4": limitar_texto(
            primeiro_valor(
                documento,
                [
                    "StatusComentarioD4",
                    "statusComment",
                    "commentStatus",
                    "status_comment",
                    "comments.status",
                ],
            ),
            255,
        ),
        "CanceladoPorD4": limitar_texto(
            primeiro_valor(
                documento,
                [
                    "CanceladoPorD4",
                    "cancelledBy",
                    "canceledBy",
                    "cancelled_by",
                    "canceled_by",
                    "cancelado_por",
                ],
            ),
            255,
        ),
    }


def metadados_mudaram(contrato_banco: dict[str, Any], metadados_api: dict[str, Any]) -> bool:
    """Verifico se algum campo retornado pela API está diferente do banco."""
    campos_comparacao = [
        "IDDimStatusD4",
        "IDDimStatusContratos",
        "IDFaseD4",
        "NomeFaseD4",
        "UUIDCofreD4",
        "NomeDocumentoD4",
        "NomeCofreD4",
        "TipoArquivoD4",
        "QuantidadePaginas",
        "TamanhoArquivoD4",
        "StatusComentarioD4",
        "CanceladoPorD4",
    ]

    for campo in campos_comparacao:
        if valores_diferentes(contrato_banco.get(campo), metadados_api.get(campo)):
            return True

    return False



def buscar_valor_recursivo_d4(dados: Any, nomes_chaves: list[str]) -> Any:
    """Procuro valor em qualquer nível do retorno da API da D4Sign."""
    nomes = {normalizar_texto(nome) for nome in nomes_chaves}

    if isinstance(dados, dict):
        for chave, valor in dados.items():
            if normalizar_texto(chave) in nomes and valor not in (None, ""):
                return valor

            encontrado = buscar_valor_recursivo_d4(valor, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    if isinstance(dados, list):
        for item in dados:
            encontrado = buscar_valor_recursivo_d4(item, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    return None


def coletar_dicionarios_com_email_d4(dados: Any) -> list[dict[str, Any]]:
    """Coleto objetos da API que pareçam representar signatários."""
    encontrados: list[dict[str, Any]] = []

    if isinstance(dados, dict):
        email = buscar_valor_recursivo_d4(dados, ["email", "EmailSignatario"])
        if email:
            encontrados.append(dados)

        for valor in dados.values():
            encontrados.extend(coletar_dicionarios_com_email_d4(valor))

    elif isinstance(dados, list):
        for item in dados:
            encontrados.extend(coletar_dicionarios_com_email_d4(item))

    return encontrados


def valor_bool_para_bit_d4(valor: Any) -> int:
    """Converto booleanos/textos para 0 ou 1."""
    if isinstance(valor, bool):
        return 1 if valor else 0

    if isinstance(valor, int):
        return 1 if valor == 1 else 0

    texto = normalizar_texto(valor)
    if texto in {"1", "true", "sim", "yes", "y", "on", "s", "ok", "enviado", "sent", "assinado", "signed", "visualizado", "viewed"}:
        return 1

    return 0


def resolver_status_contrato_por_status_d4(id_status_d4: int | None, status_atual: Any = None) -> int | None:
    """Transformo o status atual da D4Sign no status do contrato interno.

    De/para oficial usado pela Euromídia:
    - D4 1 Processando              -> Contrato 3 Documento Gerado
    - D4 2 Aguardando Signatários   -> Contrato 5 Enviado Assinatura
    - D4 3 Aguardando Assinaturas   -> Contrato 6 Em Assinatura
    - D4 4 Finalizado               -> Contrato 8 Concluido
    - D4 5 Arquivado                -> Contrato 8 Concluido
    - D4 6 Cancelado                -> Contrato 9 Cancelado
    - D4 7 Editando                 -> mantém status interno atual
    """
    id_status = converter_int(id_status_d4)

    mapa_status_d4_para_contrato = {
        1: ID_STATUS_CONTRATO_DOCUMENTO_GERADO,
        2: ID_STATUS_CONTRATO_ENVIADO_ASSINATURA,
        3: ID_STATUS_CONTRATO_EM_ASSINATURA,
        4: ID_STATUS_CONTRATO_CONCLUIDO,
        5: ID_STATUS_CONTRATO_CONCLUIDO,
        6: ID_STATUS_CONTRATO_CANCELADO,
    }

    if id_status in mapa_status_d4_para_contrato:
        return mapa_status_d4_para_contrato[id_status]

    return converter_int(status_atual)


def extrair_signatarios_api_d4(documento: dict[str, Any], id_status_d4: int | None) -> list[dict[str, Any]]:
    """Extraio o máximo possível de signatários do retorno da API D4Sign."""
    candidatos = coletar_dicionarios_com_email_d4(documento)
    resultado: list[dict[str, Any]] = []
    emails_vistos: set[str] = set()

    for item in candidatos:
        email = limitar_texto(
            buscar_valor_recursivo_d4(item, ["email", "EmailSignatario", "email_signatario", "signer_email"]),
            255,
        )

        if not email:
            continue

        chave_email = email.strip().lower()
        if chave_email in emails_vistos:
            continue
        emails_vistos.add(chave_email)

        data_envio = limitar_texto(
            buscar_valor_recursivo_d4(item, ["sent_at", "send_at", "email_sent_at", "DataEnvioD4", "created_at", "createdAt"]),
            100,
        )
        data_primeira_visualizacao = limitar_texto(
            buscar_valor_recursivo_d4(item, ["first_viewed_at", "first_opened_at", "viewed_at", "opened_at", "DataPrimeiraVisualizacaoD4"]),
            100,
        )
        data_ultima_visualizacao = limitar_texto(
            buscar_valor_recursivo_d4(item, ["last_viewed_at", "last_opened_at", "viewed_at", "opened_at", "DataUltimaVisualizacaoD4"]),
            100,
        )
        data_assinatura = limitar_texto(
            buscar_valor_recursivo_d4(item, ["signed_at", "signedAt", "signature_date", "DataAssinaturaD4"]),
            100,
        )

        texto_status = normalizar_texto(
            buscar_valor_recursivo_d4(item, ["status", "status_assinatura", "signature_status", "message"])
        )

        bit_assinado = 1 if data_assinatura or id_status_d4 in (4, 5) or "signed" in texto_status or "assinado" in texto_status else 0
        bit_visualizou = 1 if data_primeira_visualizacao or data_ultima_visualizacao or "view" in texto_status or "visualiz" in texto_status or "opened" in texto_status else 0
        bit_email_enviado = 1 if data_envio or valor_bool_para_bit_d4(buscar_valor_recursivo_d4(item, ["email_sent", "BitEmailEnviado", "sent"])) else 0

        if bit_assinado:
            id_status_assinatura = ID_STATUS_ASSINATURA_D4_ASSINADO
        elif bit_visualizou:
            id_status_assinatura = ID_STATUS_ASSINATURA_D4_VISUALIZOU
        elif "erro" in texto_status or "error" in texto_status or "failed" in texto_status:
            id_status_assinatura = ID_STATUS_ASSINATURA_D4_EMAIL_ERRO
        else:
            id_status_assinatura = ID_STATUS_ASSINATURA_D4_PENDENTE

        geolocalizacao = buscar_valor_recursivo_d4(item, ["geolocation", "geo_location", "location", "GeolocalizacaoAssinaturaD4"])
        if isinstance(geolocalizacao, (dict, list)):
            geolocalizacao = json.dumps(geolocalizacao, ensure_ascii=False, default=str)

        resultado.append(
            {
                "KeySignerD4": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["uuid", "key_signer", "keySigner", "signer_key", "id", "KeySignerD4"]),
                    100,
                ),
                "EmailSignatario": email,
                "NomeSignatario": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["name", "nome", "full_name", "fullName", "signer_name", "NomeSignatario"]),
                    255,
                ),
                "DocumentoSignatario": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["identification_number", "document", "documento", "cpf", "cnpj", "DocumentoSignatario"]),
                    100,
                ),
                "TelefoneSignatario": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["phone", "telephone", "telefone", "celular", "mobile", "whatsapp", "TelefoneSignatario"]),
                    50,
                ),
                "BitContatoPrincipal": valor_bool_para_bit_d4(
                    buscar_valor_recursivo_d4(item, ["main", "principal", "is_main", "main_contact", "BitContatoPrincipal"])
                ),
                "IDDimStatusAssinaturaD4": id_status_assinatura,
                "BitEmailEnviado": bit_email_enviado,
                "StatusEnvioEmailD4": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["email_status", "status_email", "StatusEnvioEmailD4", "send_status"]),
                    100,
                ),
                "MensagemEnvioEmailD4": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["email_message", "message_email", "MensagemEnvioEmailD4", "send_message", "error_message"]),
                    1000,
                ),
                "BitVisualizouDocumento": bit_visualizou,
                "DataPrimeiraVisualizacaoD4": data_primeira_visualizacao,
                "DataUltimaVisualizacaoD4": data_ultima_visualizacao,
                "BitAssinado": bit_assinado,
                "DataEnvioD4": data_envio,
                "DataAssinaturaD4": data_assinatura,
                "IpAssinaturaD4": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["ip", "ip_address", "ipAddress", "signature_ip", "IpAssinaturaD4"]),
                    100,
                ),
                "GeolocalizacaoAssinaturaD4": limitar_texto(geolocalizacao, 500),
                "UserAgentAssinaturaD4": limitar_texto(
                    buscar_valor_recursivo_d4(item, ["user_agent", "userAgent", "signature_user_agent", "UserAgentAssinaturaD4"]),
                    500,
                ),
                "SegundosAtePrimeiraVisualizacao": converter_int(
                    buscar_valor_recursivo_d4(item, ["seconds_to_first_view", "SegundosAtePrimeiraVisualizacao"])
                ),
                "SegundosAteAssinatura": converter_int(
                    buscar_valor_recursivo_d4(item, ["seconds_to_signature", "SegundosAteAssinatura"])
                ),
            }
        )

    return resultado


def resolver_status_final_euromidia(
    status_atual: Any,
    status_novo: Any,
    id_status_d4: Any = None,
) -> int | None:
    """Resolvo o status interno usando a D4Sign como fonte de verdade.

    Antes havia uma trava por número: se o contrato interno estivesse 7/Ativo,
    a DAG não deixava voltar para 5/Enviado Assinatura mesmo quando a API D4Sign
    dizia que o documento ainda estava em Aguardando Signatários. Isso mascarava
    erro de tela/esteira.

    Nesta DAG, a consulta vem da API D4Sign, então ela pode corrigir status local
    adiantado. Só mantenho Cancelado como terminal por segurança operacional.
    """
    id_status_atual = converter_int(status_atual)
    id_status_novo = converter_int(status_novo)
    id_d4 = converter_int(id_status_d4)

    if id_status_novo is None:
        return id_status_atual

    # Cancelado é terminal na esteira local.
    if id_status_atual == ID_STATUS_CONTRATO_CANCELADO or id_status_novo == ID_STATUS_CONTRATO_CANCELADO or id_d4 == 6:
        return ID_STATUS_CONTRATO_CANCELADO

    # Concluido também é terminal para evitar que uma consulta antiga da API regrida a esteira.
    if id_status_atual == ID_STATUS_CONTRATO_CONCLUIDO and id_status_novo != ID_STATUS_CONTRATO_CANCELADO:
        return ID_STATUS_CONTRATO_CONCLUIDO

    # Quando a API D4Sign informa estado atual, ela corrige a tela local conforme o de/para oficial.
    # 1 Processando -> Documento Gerado
    # 2 Aguardando Signatários -> Enviado Assinatura
    # 3 Aguardando Assinaturas -> Em Assinatura
    # 4/5 Finalizado/Arquivado -> Concluido
    # 7 Editando -> mantém o mapeamento calculado/fallback.
    if id_d4 in {1, 2, 3, 4, 5, 7}:
        return id_status_novo

    if id_status_atual is None:
        return id_status_novo

    # Sem ID D4 confiável, não deixo um evento/estado antigo regredir a etapa.
    ordem_fluxo = {
        ID_STATUS_CONTRATO_DOCUMENTO_GERADO: 30,
        ID_STATUS_CONTRATO_PENDENTE_ENVIO: 40,
        ID_STATUS_CONTRATO_ENVIADO_ASSINATURA: 50,
        ID_STATUS_CONTRATO_EM_ASSINATURA: 60,
        ID_STATUS_CONTRATO_ATIVO: 70,
        ID_STATUS_CONTRATO_CONCLUIDO: 80,
        ID_STATUS_CONTRATO_CANCELADO: 90,
    }

    if ordem_fluxo.get(id_status_novo, 0) >= ordem_fluxo.get(id_status_atual, 0):
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



def inserir_historico_d4(conexao, parametros: dict[str, Any]) -> dict[str, Any]:
    """Insiro histórico somente quando o último status gravado for diferente."""
    id_controle = converter_int(parametros.get("IDFatoControleContratosEuromidia"))
    id_status_contrato = converter_int(parametros.get("IDDimStatusContratos"))
    id_status_d4 = converter_int(parametros.get("IDDimStatusD4"))

    if not id_controle or (id_status_contrato is None and id_status_d4 is None):
        return {
            "historico_inserido": False,
            "motivo": "sem_contrato_ou_sem_status",
            "id_controle": id_controle,
            "id_status_contrato": id_status_contrato,
            "id_status_d4": id_status_d4,
        }

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
            COALESCE(TRY_CONVERT(datetime2(3), :DataStatus, 126), SYSDATETIME())
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
            "DataStatus": parametros.get("DataStatus"),
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
    id_status_d4: int | None = None,
    registrar_historico: bool = True,
) -> dict[str, Any]:
    """Sincronizo a esteira inteira com o status atual do documento D4.

    Esta função é a garantia do fluxo. Ela não depende do webhook.
    Sempre que a DAG manual roda, ela pega o estado conhecido em FatoContratoD4/API
    e força a mesma verdade em:
    - FatoControleContratosEuromidia
    - FatoControleContratosItensEuromidia
    - FatoSolicitacaoContratoEuromidia
    - DimHistoricoContratosD4
    """
    id_controle = converter_int(id_fato_controle_contrato)
    id_card = converter_int(id_fato_kanban_card)
    id_status_novo = converter_int(id_status_contrato)
    id_d4 = converter_int(id_status_d4)

    if not id_controle or id_status_novo is None:
        return {
            "propagou": False,
            "motivo": "sem_id_controle_ou_sem_status",
            "id_controle": id_controle,
            "id_status_novo": id_status_novo,
            "id_status_d4": id_d4,
        }

    status_atual = buscar_status_atual_controle_euromidia(conexao, id_controle)
    status_final = resolver_status_final_euromidia(status_atual, id_status_novo, id_d4)

    if status_final is None:
        return {
            "propagou": False,
            "motivo": "status_final_nulo",
            "id_controle": id_controle,
            "status_atual": status_atual,
            "id_status_novo": id_status_novo,
            "id_status_d4": id_d4,
        }

    sql_cabecalho = text(f"""
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
    """)
    resultado_cabecalho = conexao.execute(
        sql_cabecalho,
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": status_final,
        },
    )

    sql_itens = text(f"""
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
    """)
    resultado_itens = conexao.execute(
        sql_itens,
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": status_final,
            "IDEmpresaProprietaria": ID_EMPRESA_PROPRIETARIA_EUROMIDIA,
        },
    )

    sql_solicitacao = text(f"""
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
    """)
    resultado_solicitacao = conexao.execute(
        sql_solicitacao,
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDFatoKanbanCard": id_card,
            "IDDimStatusContratos": status_final,
        },
    )

    historico = None
    if registrar_historico:
        historico = inserir_historico_d4(
            conexao,
            {
                "IDFatoControleContratosEuromidia": id_controle,
                "IDDimStatusContratos": status_final,
                "IDDimStatusD4": id_d4,
            },
        )

    return {
        "propagou": True,
        "id_controle": id_controle,
        "id_card": id_card,
        "status_atual_controle": status_atual,
        "status_evento": id_status_novo,
        "status_final": status_final,
        "id_status_d4": id_d4,
        "cabecalho_atualizado": int(resultado_cabecalho.rowcount or 0),
        "itens_atualizados": int(resultado_itens.rowcount or 0),
        "solicitacoes_atualizadas": int(resultado_solicitacao.rowcount or 0),
        "historico": historico,
    }


def sincronizar_estado_local_d4_com_esteira(hook_sql: HookSqlServer) -> dict[str, Any]:
    """Sincronizo a esteira recalculando o status interno a partir do IDDimStatusD4 gravado na FatoContratoD4.

    Esta etapa não chama a API e não depende de webhook. Ela existe para garantir
    que, ao executar a DAG manualmente, contratos como o 5875 saiam de Pendente
    Geração quando a FatoContratoD4 já sabe que o documento foi enviado.
    """
    limite = obter_int_env("D4SIGN_LIMITE_SINCRONIZACAO_LOCAL_POR_EXECUCAO", 1000)
    if limite <= 0:
        limite = 1000

    contratos = hook_sql.executar_select(
        f"""
        SELECT TOP ({limite})
            IDFatoContratoD4,
            IDFatoControleContratosEuromidia,
            IDFatoKanbanCard,
            IDDimStatusContratos,
            IDDimStatusD4,
            UUIDDocumentoD4,
            NomeDocumentoD4,
            DataAtualizacao,
            DataCriacao
        FROM {TABELA_CONTRATO_D4}
        WHERE ISNULL(BitAtivo, 1) = 1
          AND IDFatoControleContratosEuromidia IS NOT NULL
          AND IDDimStatusContratos IS NOT NULL
          AND ISNULL(IDDimStatusD4, 0) <> 1
        ORDER BY
            COALESCE(DataAtualizacao, DataCriacao) DESC,
            IDFatoContratoD4 DESC
        """
    )

    resultados: list[dict[str, Any]] = []
    engine = hook_sql.obter_engine()
    with engine.begin() as conexao:
        for contrato in contratos:
            id_status_d4 = converter_int(contrato.get("IDDimStatusD4"))
            id_status_contrato_calculado = resolver_status_contrato_por_status_d4(
                id_status_d4,
                contrato.get("IDDimStatusContratos"),
            )

            resultados.append(
                propagar_status_contrato_euromidia(
                    conexao,
                    id_fato_controle_contrato=contrato.get("IDFatoControleContratosEuromidia"),
                    id_fato_kanban_card=contrato.get("IDFatoKanbanCard"),
                    id_status_contrato=id_status_contrato_calculado,
                    id_status_d4=id_status_d4,
                    registrar_historico=True,
                )
            )

    return {
        "contratos_sincronizados_localmente": len(resultados),
        "resultados_sincronizacao_local": resultados[:100],
    }


def upsert_signatario_d4_api(
    conexao,
    *,
    contrato: dict[str, Any],
    signatario: dict[str, Any],
    payload_json: str,
) -> str:
    """Atualizo/crio signatário com os campos disponíveis na consulta da API."""
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
            DataPrimeiraVisualizacaoD4 = COALESCE(DataPrimeiraVisualizacaoD4, TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126)),
            DataUltimaVisualizacaoD4 = COALESCE(TRY_CONVERT(datetime2(7), :DataUltimaVisualizacaoD4, 126), DataUltimaVisualizacaoD4),
            BitAssinado = CASE WHEN :BitAssinado = 1 THEN 1 ELSE BitAssinado END,
            DataEnvioD4 = COALESCE(DataEnvioD4, TRY_CONVERT(datetime2(7), :DataEnvioD4, 126)),
            DataAssinaturaD4 = CASE
                WHEN :BitAssinado = 1
                    THEN COALESCE(TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126), DataAssinaturaD4, SYSDATETIME())
                ELSE DataAssinaturaD4
            END,
            IpAssinaturaD4 = COALESCE(:IpAssinaturaD4, IpAssinaturaD4),
            GeolocalizacaoAssinaturaD4 = COALESCE(:GeolocalizacaoAssinaturaD4, GeolocalizacaoAssinaturaD4),
            UserAgentAssinaturaD4 = COALESCE(:UserAgentAssinaturaD4, UserAgentAssinaturaD4),
            SegundosAtePrimeiraVisualizacao = COALESCE(:SegundosAtePrimeiraVisualizacao, SegundosAtePrimeiraVisualizacao),
            SegundosAteAssinatura = COALESCE(:SegundosAteAssinatura, SegundosAteAssinatura),
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


@dag(
    dag_id="pipeline_update_contrato_D4",
    description="Atualiza status de contratos D4Sign na FatoContratoD4 a cada 10 minutos.",
    doc_md=DOCUMENTACAO_DAG,
    tags=TAGS_DAG,
    schedule="*/10 * * * *",
    start_date=pendulum.datetime(2026, 5, 29, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "Euromidia",
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
)
def pipeline_update_contrato_D4():
    @task(task_id="validar_conexoes")
    def validar_conexoes() -> dict[str, str]:
        """Valido SQL Server e deixo o teste da D4Sign opcional para não consumir API."""
        hook_sql = HookSqlServer()

        retorno_sql = hook_sql.testar_conexao()
        logging.info(retorno_sql)

        validar_d4sign = os.getenv("D4SIGN_VALIDAR_CONEXAO_A_CADA_EXECUCAO", "0").strip() == "1"

        if validar_d4sign:
            hook_d4 = HookD4Sign()
            retorno_d4 = hook_d4.testar_conexao()
            logging.info(retorno_d4)
        else:
            retorno_d4 = "Validação D4Sign ignorada nesta execução para reduzir consumo de API."
            logging.info(retorno_d4)

        return {
            "sql_server": retorno_sql,
            "d4sign": retorno_d4,
        }

    @task(task_id="verificar_e_atualizar_status_contratos")
    def verificar_e_atualizar_status_contratos() -> dict[str, Any]:
        """Consulta a D4Sign e atualiza a FatoContratoD4 quando o status mudar."""
        hook_sql = HookSqlServer()
        hook_d4 = HookD4Sign()

        limite_contratos = int(os.getenv("D4SIGN_LIMITE_CONTRATOS_POR_EXECUCAO", "100"))
        if limite_contratos <= 0:
            limite_contratos = 100

        consultar_status_finais = os.getenv("D4SIGN_CONSULTAR_STATUS_FINAIS", "0").strip() == "1"
        filtro_status_finais = "" if consultar_status_finais else "AND ISNULL(IDDimStatusD4, 0) NOT IN (4, 5, 6)"

        intervalo_consultas = float(
            os.getenv("D4SIGN_INTERVALO_SEGUNDOS_ENTRE_CONSULTAS", "0.2")
        )
        if intervalo_consultas < 0:
            intervalo_consultas = 0

        linhas_status = hook_sql.executar_select(
            """
            SELECT
                IDDimStatusD4,
                NomeStatus
            FROM [Integracao].[Silver].[DimStatusD4]
            """
        )
        mapa_id_para_nome, mapa_nome_para_id = montar_mapas_status(linhas_status)

        contratos = hook_sql.executar_select(
            f"""
            SELECT TOP ({limite_contratos})
                IDFatoContratoD4,
                IDDimStatusD4,
                IDEmpresa,
                IDDimCofreD4,
                IDFatoControleContratosEuromidia,
                IDFatoKanbanCard,
                IDDimStatusContratos,
                IDDimModeloContratoD4,
                IDDimTipoDocumento,
                UUIDDocumentoD4,
                UUIDCofreD4,
                NomeDocumentoD4,
                NomeCofreD4,
                IDFaseD4,
                NomeFaseD4,
                TipoArquivoD4,
                QuantidadePaginas,
                TamanhoArquivoD4,
                StatusComentarioD4,
                CanceladoPorD4,
                DataCriacao,
                DataAtualizacao,
                BitAtivo
            FROM [Integracao].[Silver].[FatoContratoD4]
            WHERE
                BitAtivo = 1
                AND NULLIF(LTRIM(RTRIM(UUIDDocumentoD4)), '') IS NOT NULL
                {filtro_status_finais}
            ORDER BY
                ISNULL(DataAtualizacao, DataCriacao),
                IDFatoContratoD4
            """
        )

        logging.info("Contratos D4 encontrados para verificação: %s", len(contratos))

        atualizacoes: list[dict[str, Any]] = []
        propagacoes_para_controle: list[dict[str, Any]] = []
        resultados_propagacao: list[dict[str, Any]] = []
        signatarios_para_upsert: list[dict[str, Any]] = []
        pdfs_contratos_gravados: list[dict[str, Any]] = []
        ids_pdfs_contratos_gravados_processados: set[int] = set()
        erros_pdf_contratos_gravados: list[dict[str, Any]] = []
        erros: list[dict[str, Any]] = []
        ignorados_sem_status = 0
        sem_mudanca = 0
        api_d4sign_bloqueada = False

        for contrato in contratos:
            id_fato_contrato_d4 = contrato.get("IDFatoContratoD4")
            uuid_documento_d4 = str(contrato.get("UUIDDocumentoD4") or "").strip()

            if not uuid_documento_d4:
                continue

            try:
                resposta_api = hook_d4.listar_documento(uuid_documento_d4)
                documento = desembrulhar_documento(resposta_api)
                id_status, nome_status = resolver_status_documento(
                    documento=documento,
                    mapa_id_para_nome=mapa_id_para_nome,
                    mapa_nome_para_id=mapa_nome_para_id,
                )

                if id_status is None:
                    ignorados_sem_status += 1
                    logging.warning(
                        "Não consegui resolver status D4. IDFatoContratoD4=%s UUID=%s Resposta=%s",
                        id_fato_contrato_d4,
                        uuid_documento_d4,
                        resposta_api,
                    )
                    continue

                metadados_api = extrair_metadados_documento(
                    documento=documento,
                    id_status=id_status,
                    nome_status=nome_status,
                )
                metadados_api["IDFatoContratoD4"] = id_fato_contrato_d4
                metadados_api["IDDimStatusContratos"] = resolver_status_contrato_por_status_d4(
                    id_status,
                    contrato.get("IDDimStatusContratos"),
                )
                metadados_api["IDFatoControleContratosEuromidia"] = contrato.get("IDFatoControleContratosEuromidia")
                metadados_api["IDFatoKanbanCard"] = contrato.get("IDFatoKanbanCard")
                metadados_api["IDDimStatusD4Anterior"] = contrato.get("IDDimStatusD4")
                metadados_api["IDDimStatusContratosAnterior"] = contrato.get("IDDimStatusContratos")

                signatarios_d4 = extrair_signatarios_api_d4(documento, id_status)
                metadados_api["SignatariosD4"] = signatarios_d4

                # Não forço Ativo aqui.
                # Regra oficial:
                # D4=2 Aguardando Signatários -> Contrato=5 Enviado Assinatura
                # D4=3 Aguardando Assinaturas -> Contrato=6 Em Assinatura
                # D4=4 Finalizado            -> Contrato=8 Concluido
                # Se todos assinaram mas a API ainda mostra D4=3, mantenho 6 até a D4Sign
                # retornar o status final oficial. Isso evita contrato Ativo antes da hora.

                metadados_api["PayloadJsonD4"] = json.dumps(documento, ensure_ascii=False, default=str)
                metadados_api["ContratoBanco"] = dict(contrato)

                propagacoes_para_controle.append(metadados_api)

                if metadados_api["SignatariosD4"]:
                    signatarios_para_upsert.append(metadados_api)

                if metadados_mudaram(contrato, metadados_api):
                    atualizacoes.append(metadados_api)
                    logging.info(
                        "Contrato D4 mudou. IDFatoContratoD4=%s UUID=%s StatusBanco=%s StatusAPI=%s - %s",
                        id_fato_contrato_d4,
                        uuid_documento_d4,
                        contrato.get("IDDimStatusD4"),
                        id_status,
                        nome_status,
                    )
                else:
                    sem_mudanca += 1

                if intervalo_consultas:
                    time.sleep(intervalo_consultas)

            except Exception as erro:
                erros.append(
                    {
                        "IDFatoContratoD4": id_fato_contrato_d4,
                        "UUIDDocumentoD4": uuid_documento_d4,
                        "erro": str(erro),
                    }
                )
                logging.exception(
                    "Erro ao consultar/atualizar contrato D4. IDFatoContratoD4=%s UUID=%s",
                    id_fato_contrato_d4,
                    uuid_documento_d4,
                )

                if erro_d4sign_limite_ou_401(erro):
                    api_d4sign_bloqueada = True
                    logging.error(
                        "D4SIGN_API_LIMITE_OU_401 | interrompendo novas consultas de status D4Sign nesta execução; download de PDF continua independente e será tentado pela rotina de arquivos | IDFatoContratoD4=%s | UUID=%s",
                        id_fato_contrato_d4,
                        uuid_documento_d4,
                    )
                    break

        if atualizacoes:
            sql_update = text(
                f"""
                UPDATE {TABELA_CONTRATO_D4}
                SET
                    IDDimStatusD4 = :IDDimStatusD4,
                    IDDimStatusContratos = COALESCE(:IDDimStatusContratos, IDDimStatusContratos),
                    IDFaseD4 = COALESCE(:IDFaseD4, IDFaseD4),
                    NomeFaseD4 = COALESCE(:NomeFaseD4, NomeFaseD4),
                    UUIDCofreD4 = COALESCE(:UUIDCofreD4, UUIDCofreD4),
                    NomeDocumentoD4 = COALESCE(:NomeDocumentoD4, NomeDocumentoD4),
                    NomeCofreD4 = COALESCE(:NomeCofreD4, NomeCofreD4),
                    TipoArquivoD4 = COALESCE(:TipoArquivoD4, TipoArquivoD4),
                    QuantidadePaginas = COALESCE(:QuantidadePaginas, QuantidadePaginas),
                    TamanhoArquivoD4 = COALESCE(:TamanhoArquivoD4, TamanhoArquivoD4),
                    StatusComentarioD4 = COALESCE(:StatusComentarioD4, StatusComentarioD4),
                    CanceladoPorD4 = COALESCE(:CanceladoPorD4, CanceladoPorD4),
                    DataAtualizacao = SYSDATETIME()
                WHERE
                    IDFatoContratoD4 = :IDFatoContratoD4
                """
            )

            engine = hook_sql.obter_engine()
            with engine.begin() as conexao:
                for parametros in atualizacoes:
                    houve_mudanca_status = (
                        converter_int(parametros.get("IDDimStatusD4Anterior")) != converter_int(parametros.get("IDDimStatusD4"))
                        or converter_int(parametros.get("IDDimStatusContratosAnterior")) != converter_int(parametros.get("IDDimStatusContratos"))
                    )

                    conexao.execute(sql_update, parametros)

                    if houve_mudanca_status:
                        inserir_historico_d4(conexao, parametros)

                    try:
                        resultado_pdf_imediato = garantir_pdf_local_contrato_d4_gravado(
                            conexao,
                            parametros,
                            permitir_download_d4sign=True,
                        )
                        pdfs_contratos_gravados.append(resultado_pdf_imediato)
                        id_pdf_processado = converter_int(parametros.get("IDFatoContratoD4"))
                        if id_pdf_processado:
                            ids_pdfs_contratos_gravados_processados.add(id_pdf_processado)
                        if resultado_pdf_imediato.get("erro_download_pdf") and erro_d4sign_limite_ou_401(resultado_pdf_imediato.get("erro_download_pdf")):
                            api_d4sign_bloqueada = True
                    except Exception as exc:
                        logging.exception(
                            "D4SIGN_PDF_LOCAL_DAG | falha ao garantir PDF imediatamente após gravar contrato D4 | IDFatoContratoD4=%s | IDFatoControle=%s",
                            parametros.get("IDFatoContratoD4"),
                            parametros.get("IDFatoControleContratosEuromidia"),
                        )
                        erros_pdf_contratos_gravados.append({
                            "IDFatoContratoD4": parametros.get("IDFatoContratoD4"),
                            "IDFatoControleContratosEuromidia": parametros.get("IDFatoControleContratosEuromidia"),
                            "UUIDDocumentoD4": parametros.get("UUIDDocumentoD4"),
                            "erro": str(exc),
                        })
                        if erro_d4sign_limite_ou_401(exc):
                            api_d4sign_bloqueada = True

        # PDF não pode depender de mudança de status/metadados.
        # Se o contrato foi consultado na D4Sign nesta execução e tem UUID,
        # garanto tabela + arquivo físico mesmo quando entrou como "sem mudança".
        if propagacoes_para_controle:
            engine = hook_sql.obter_engine()
            with engine.begin() as conexao:
                for parametros in propagacoes_para_controle:
                    id_pdf_processado = converter_int(parametros.get("IDFatoContratoD4"))
                    if id_pdf_processado and id_pdf_processado in ids_pdfs_contratos_gravados_processados:
                        continue
                    try:
                        resultado_pdf_imediato = garantir_pdf_local_contrato_d4_gravado(
                            conexao,
                            parametros,
                            permitir_download_d4sign=True,
                        )
                        pdfs_contratos_gravados.append(resultado_pdf_imediato)
                        if id_pdf_processado:
                            ids_pdfs_contratos_gravados_processados.add(id_pdf_processado)
                        if resultado_pdf_imediato.get("erro_download_pdf") and erro_d4sign_limite_ou_401(resultado_pdf_imediato.get("erro_download_pdf")):
                            api_d4sign_bloqueada = True
                    except Exception as exc:
                        logging.exception(
                            "D4SIGN_PDF_LOCAL_DAG | falha ao garantir PDF para contrato D4 consultado sem mudança | IDFatoContratoD4=%s | IDFatoControle=%s",
                            parametros.get("IDFatoContratoD4"),
                            parametros.get("IDFatoControleContratosEuromidia"),
                        )
                        erros_pdf_contratos_gravados.append({
                            "IDFatoContratoD4": parametros.get("IDFatoContratoD4"),
                            "IDFatoControleContratosEuromidia": parametros.get("IDFatoControleContratosEuromidia"),
                            "UUIDDocumentoD4": parametros.get("UUIDDocumentoD4"),
                            "erro": str(exc),
                        })
                        if erro_d4sign_limite_ou_401(exc):
                            api_d4sign_bloqueada = True

        if propagacoes_para_controle:
            engine = hook_sql.obter_engine()
            with engine.begin() as conexao:
                for parametros in propagacoes_para_controle:
                    propagacao_euromidia = propagar_status_contrato_euromidia(
                        conexao,
                        id_fato_controle_contrato=parametros.get("IDFatoControleContratosEuromidia"),
                        id_fato_kanban_card=parametros.get("IDFatoKanbanCard"),
                        id_status_contrato=parametros.get("IDDimStatusContratos"),
                        id_status_d4=parametros.get("IDDimStatusD4"),
                        registrar_historico=True,
                    )
                    resultados_propagacao.append(propagacao_euromidia)

        if signatarios_para_upsert:
            engine = hook_sql.obter_engine()
            with engine.begin() as conexao:
                for pacote in signatarios_para_upsert:
                    contrato_banco = pacote.get("ContratoBanco") or {}
                    payload_json = pacote.get("PayloadJsonD4") or "{}"

                    for signatario in pacote.get("SignatariosD4") or []:
                        upsert_signatario_d4_api(
                            conexao,
                            contrato=contrato_banco,
                            signatario=signatario,
                            payload_json=payload_json,
                        )

        sincronizacao_local = sincronizar_estado_local_d4_com_esteira(hook_sql)
   
   
        pdf_local = garantir_pdfs_locais_contratos_d4(
            hook_sql,
            permitir_download_d4sign=True,
        )

        resumo = {
            "contratos_verificados": len(contratos),
            "contratos_atualizados": len(atualizacoes),
            "contratos_sem_mudanca": sem_mudanca,
            "contratos_ignorados_sem_status": ignorados_sem_status,
            "contratos_com_erro": len(erros),
            "api_d4sign_bloqueada_nesta_execucao": api_d4sign_bloqueada,
            "contratos_com_signatarios_extraidos": len(signatarios_para_upsert),
            "propagacoes_euromidia": len(resultados_propagacao),
            "resultados_propagacao_euromidia": resultados_propagacao[:50],
            "sincronizacao_local": sincronizacao_local,
            "pdfs_contratos_gravados_imediato": pdfs_contratos_gravados[:100],
            "erros_pdf_contratos_gravados_imediato": erros_pdf_contratos_gravados[:50],
            "pdf_local": pdf_local,
            "erros": erros[:20],
        }

        logging.info("Resumo pipeline_update_contrato_D4: %s", resumo)
        return resumo

    validacao = validar_conexoes()
    atualizacao = verificar_e_atualizar_status_contratos()

    validacao >> atualizacao


pipeline_update_contrato_D4()
