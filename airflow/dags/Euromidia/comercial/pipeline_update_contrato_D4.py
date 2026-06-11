import json
import logging
import os
import time
import unicodedata
from datetime import timedelta
from typing import Any

import pendulum
from sqlalchemy import text

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

ID_EMPRESA_PROPRIETARIA_EUROMIDIA = 3

ID_STATUS_CONTRATO_DOCUMENTO_GERADO = 3
ID_STATUS_CONTRATO_PENDENTE_ENVIO = 4
ID_STATUS_CONTRATO_ENVIADO_ASSINATURA = 5
ID_STATUS_CONTRATO_EM_ASSINATURA = 6
ID_STATUS_CONTRATO_ATIVO = 7
ID_STATUS_CONTRATO_CANCELADO = 9


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

Esta DAG consulta a API da D4Sign a cada 5 minutos e atualiza a tabela
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
    """Transformo o status atual da D4Sign no status do contrato interno."""
    id_status = converter_int(id_status_d4)

    if id_status == 4 or id_status == 5:
        return ID_STATUS_CONTRATO_ATIVO

    if id_status == 6:
        return ID_STATUS_CONTRATO_CANCELADO

    if id_status == 3:
        return ID_STATUS_CONTRATO_EM_ASSINATURA

    if id_status == 2:
        return ID_STATUS_CONTRATO_ENVIADO_ASSINATURA

    if id_status == 1:
        # D4 status 1 = Processando. Não forço "Pendente Envio" enquanto o documento ainda está processando.
        return ID_STATUS_CONTRATO_DOCUMENTO_GERADO

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

    # Quando a API D4Sign informa estado atual, ela corrige a tela local.
    # 1 Processando -> Documento Gerado
    # 2 Aguardando Signatários -> Enviado Assinatura
    # 3 Aguardando Assinaturas -> Em Assinatura
    # 4/5 Finalizado/Arquivado -> Ativo
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
    """Sincronizo a esteira usando o que já está gravado na FatoContratoD4.

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
            ISNULL(DataAtualizacao, DataCriacao) DESC,
            IDFatoContratoD4 DESC
        """
    )

    resultados: list[dict[str, Any]] = []
    engine = hook_sql.obter_engine()
    with engine.begin() as conexao:
        for contrato in contratos:
            resultados.append(
                propagar_status_contrato_euromidia(
                    conexao,
                    id_fato_controle_contrato=contrato.get("IDFatoControleContratosEuromidia"),
                    id_fato_kanban_card=contrato.get("IDFatoKanbanCard"),
                    id_status_contrato=contrato.get("IDDimStatusContratos"),
                    id_status_d4=contrato.get("IDDimStatusD4"),
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
    description="Atualiza status de contratos D4Sign na FatoContratoD4 a cada 5 minutos.",
    doc_md=DOCUMENTACAO_DAG,
    tags=TAGS_DAG,
    schedule="*/5 * * * *",
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
        erros: list[dict[str, Any]] = []
        ignorados_sem_status = 0
        sem_mudanca = 0

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
                metadados_api["SignatariosD4"] = extrair_signatarios_api_d4(documento, id_status)
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

        resumo = {
            "contratos_verificados": len(contratos),
            "contratos_atualizados": len(atualizacoes),
            "contratos_sem_mudanca": sem_mudanca,
            "contratos_ignorados_sem_status": ignorados_sem_status,
            "contratos_com_erro": len(erros),
            "contratos_com_signatarios_extraidos": len(signatarios_para_upsert),
            "propagacoes_euromidia": len(resultados_propagacao),
            "resultados_propagacao_euromidia": resultados_propagacao[:50],
            "sincronizacao_local": sincronizacao_local,
            "erros": erros[:20],
        }

        logging.info("Resumo pipeline_update_contrato_D4: %s", resumo)
        return resumo

    validacao = validar_conexoes()
    atualizacao = verificar_e_atualizar_status_contratos()

    validacao >> atualizacao


pipeline_update_contrato_D4()
