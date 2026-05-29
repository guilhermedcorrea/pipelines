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

- `D4SIGN_LIMITE_CONTRATOS_POR_EXECUCAO`: limite de contratos consultados por execução. Padrão: 10.
- `D4SIGN_INTERVALO_SEGUNDOS_ENTRE_CONSULTAS`: pausa entre consultas na API. Padrão: 0.2.
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

        limite_contratos = int(os.getenv("D4SIGN_LIMITE_CONTRATOS_POR_EXECUCAO", "10"))
        if limite_contratos <= 0:
            limite_contratos = 10

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
                """
                UPDATE [Integracao].[Silver].[FatoContratoD4]
                SET
                    IDDimStatusD4 = :IDDimStatusD4,
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
                    conexao.execute(sql_update, parametros)

        resumo = {
            "contratos_verificados": len(contratos),
            "contratos_atualizados": len(atualizacoes),
            "contratos_sem_mudanca": sem_mudanca,
            "contratos_ignorados_sem_status": ignorados_sem_status,
            "contratos_com_erro": len(erros),
            "erros": erros[:20],
        }

        logging.info("Resumo pipeline_update_contrato_D4: %s", resumo)
        return resumo

    validacao = validar_conexoes()
    atualizacao = verificar_e_atualizar_status_contratos()

    validacao >> atualizacao


pipeline_update_contrato_D4()
