from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import requests
from airflow.sdk.bases.hook import BaseHook
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


JSON_KWARGS = {
    "ensure_ascii": False,
    "separators": (",", ":"),
}


@dataclass(frozen=True)
class ConfiguracaoOmie:
    """Representa a configuração consolidada de uma connection da Omie."""

    conn_id: str
    host: str
    app_key: str
    app_secret: str
    id_empresa_proprietaria: Optional[int]
    nome_empresa: Optional[str]
    timeout: int


class OmieHook(BaseHook):
    """
    Hook base para integração com a API da Omie.

    Responsabilidades:
    1. Ler credenciais e parâmetros da Connection do Airflow.
    2. Criar sessão HTTP com retry.
    3. Montar payload padrão da Omie.
    4. Executar chamadas POST genéricas.
    5. Expor helpers para endpoints específicos quando necessário.

    Connection esperada no Airflow:
    - conn_id: por exemplo OMIE_SP
    - conn_type: http
    - host: https://app.omie.com.br

    Extra sugerido:
    {
      "app_key": "...",
      "app_secret": "...",
      "id_empresa_proprietaria": 1,
      "nome_empresa": "Sp Indústria e Comércio Ltda",
      "timeout": 90
    }
    """

    conn_name_attr = "omie_conn_id"
    default_conn_name = "OMIE_DEFAULT"
    conn_type = "http"
    hook_name = "Omie API"

    def __init__(
        self,
        omie_conn_id: str,
        *,
        timeout_padrao: int = 90,
        total_retries: int = 6,
        backoff_factor: float = 1.1,
    ) -> None:
        """
        Inicializa o hook.

        Parâmetros:
        - omie_conn_id: nome da connection no Airflow.
        - timeout_padrao: timeout padrão em segundos.
        - total_retries: total de tentativas em erros transitórios.
        - backoff_factor: fator de espera progressiva entre tentativas.
        """
        super().__init__()
        self.omie_conn_id = omie_conn_id
        self.timeout_padrao = timeout_padrao
        self.total_retries = total_retries
        self.backoff_factor = backoff_factor

    def obter_configuracao(self) -> ConfiguracaoOmie:
        """
        Lê a Connection do Airflow e consolida os parâmetros necessários.

        Origem dos dados:
        - host: campo host da Connection
        - app_key: extra["app_key"]
        - app_secret: extra["app_secret"]
        - id_empresa_proprietaria: extra["id_empresa_proprietaria"]
        - nome_empresa: extra["nome_empresa"]
        - timeout: extra["timeout"] ou timeout padrão do hook
        """
        conexao = self.get_connection(self.omie_conn_id)
        extras = conexao.extra_dejson or {}

        host = (conexao.host or "").strip()
        if not host:
            raise ValueError(
                f"A connection '{self.omie_conn_id}' está sem host configurado."
            )

        if not host.startswith("http://") and not host.startswith("https://"):
            raise ValueError(
                f"O host da connection '{self.omie_conn_id}' precisa começar com "
                f"'http://' ou 'https://'. Valor atual: {host!r}"
            )

        app_key = extras.get("app_key")
        if not app_key:
            raise ValueError(
                f"A connection '{self.omie_conn_id}' não possui 'app_key' no campo extra."
            )

        app_secret = extras.get("app_secret")
        if not app_secret:
            raise ValueError(
                f"A connection '{self.omie_conn_id}' não possui 'app_secret' no campo extra."
            )

        id_empresa_proprietaria = extras.get("id_empresa_proprietaria")
        if id_empresa_proprietaria is not None:
            try:
                id_empresa_proprietaria = int(id_empresa_proprietaria)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"A connection '{self.omie_conn_id}' possui "
                    f"'id_empresa_proprietaria' inválido: {id_empresa_proprietaria!r}"
                ) from exc

        nome_empresa = extras.get("nome_empresa")
        if nome_empresa is not None:
            nome_empresa = str(nome_empresa).strip() or None

        timeout = extras.get("timeout", self.timeout_padrao)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"A connection '{self.omie_conn_id}' possui 'timeout' inválido: {timeout!r}"
            ) from exc

        return ConfiguracaoOmie(
            conn_id=self.omie_conn_id,
            host=host.rstrip("/"),
            app_key=str(app_key),
            app_secret=str(app_secret),
            id_empresa_proprietaria=id_empresa_proprietaria,
            nome_empresa=nome_empresa,
            timeout=timeout,
        )

    def criar_sessao(self) -> requests.Session:
        """
        Cria uma sessão HTTP com retry para erros transitórios.

        Lógica:
        - reaproveita conexão TCP
        - aplica retry em status transitórios
        - aceita POST no retry
        """
        sessao = requests.Session()

        retry = Retry(
            total=self.total_retries,
            connect=self.total_retries,
            read=self.total_retries,
            backoff_factor=self.backoff_factor,
            status_forcelist=[425, 429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            raise_on_status=False,
            respect_retry_after_header=True,
        )

        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=2,
            pool_maxsize=2,
        )

        sessao.mount("https://", adapter)
        sessao.mount("http://", adapter)

        sessao.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "airflow-omie-hook/1.0",
            }
        )

        return sessao

    def montar_payload(
        self,
        *,
        call: str,
        parametros: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Monta o payload padrão da Omie.

        Estrutura:
        {
            "call": "...",
            "app_key": "...",
            "app_secret": "...",
            "param": [...]
        }
        """
        config = self.obter_configuracao()

        return {
            "call": call,
            "app_key": config.app_key,
            "app_secret": config.app_secret,
            "param": parametros,
        }

    def _resumir_payload_para_log(self, payload: dict[str, Any]) -> str:
        """
        Gera uma versão mascarada do payload para log.
        """
        payload_resumido = {
            "call": payload.get("call"),
            "param": payload.get("param"),
            "app_key": "***",
            "app_secret": "***",
        }
        return json.dumps(payload_resumido, **JSON_KWARGS)

    def _extrair_mensagem_erro_omie(self, dados: Any) -> Optional[str]:
        """
        Tenta identificar mensagens de erro devolvidas pela Omie.
        """
        if not isinstance(dados, dict):
            return None

        chaves_erro = [
            "faultstring",
            "faultcode",
            "error",
            "descricao",
            "mensagem",
            "omie_fail",
        ]

        for chave in chaves_erro:
            if chave in dados and dados[chave]:
                valor = dados[chave]
                if isinstance(valor, (dict, list)):
                    return json.dumps(valor, ensure_ascii=False)
                return str(valor)

        return None

    def executar_call(
        self,
        *,
        endpoint_path: str,
        call: str,
        parametros: list[dict[str, Any]],
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Executa uma chamada POST genérica na Omie.

        Parâmetros:
        - endpoint_path: caminho do endpoint, por exemplo '/api/v1/financas/mf/'
        - call: nome da operação Omie
        - parametros: lista do campo 'param'
        - timeout: timeout opcional por chamada

        Exemplo:
        endpoint_path='/api/v1/financas/mf/'
        call='ListarMovimentos'
        """
        config = self.obter_configuracao()

        if not endpoint_path or not isinstance(endpoint_path, str):
            raise ValueError("O parâmetro 'endpoint_path' é obrigatório.")

        caminho = endpoint_path.strip()
        if not caminho.startswith("/"):
            caminho = f"/{caminho}"

        url = f"{config.host}{caminho}"
        timeout_final = timeout if timeout is not None else config.timeout
        payload = self.montar_payload(call=call, parametros=parametros)

        self.log.info(
            "Chamando Omie | conn_id=%s | empresa=%s | id_empresa=%s | endpoint=%s | call=%s",
            config.conn_id,
            config.nome_empresa or "<sem nome>",
            config.id_empresa_proprietaria,
            caminho,
            call,
        )

        self.log.debug(
            "Payload resumido Omie | conn_id=%s | payload=%s",
            config.conn_id,
            self._resumir_payload_para_log(payload),
        )

        with self.criar_sessao() as sessao:
            resposta = sessao.post(
                url,
                json=payload,
                timeout=timeout_final,
            )

        if resposta.status_code >= 400:
            corpo = resposta.text[:4000] if resposta.text else "<sem body>"
            raise RuntimeError(
                f"Erro HTTP ao chamar Omie. "
                f"conn_id={config.conn_id} | endpoint={caminho} | "
                f"status_code={resposta.status_code} | body={corpo}"
            )

        try:
            dados = resposta.json()
        except Exception as exc:
            corpo = resposta.text[:4000] if resposta.text else "<sem body>"
            raise RuntimeError(
                f"A Omie não retornou JSON válido. "
                f"conn_id={config.conn_id} | endpoint={caminho} | body={corpo}"
            ) from exc

        mensagem_erro = self._extrair_mensagem_erro_omie(dados)
        if mensagem_erro:
            raise RuntimeError(
                f"Erro lógico retornado pela Omie. "
                f"conn_id={config.conn_id} | endpoint={caminho} | "
                f"call={call} | erro={mensagem_erro}"
            )

        if not isinstance(dados, dict):
            raise RuntimeError(
                f"Resposta inesperada da Omie. "
                f"Era esperado dict e veio {type(dados).__name__}."
            )

        return dados

    def executar_call_financeiro(
        self,
        *,
        call: str,
        parametros: list[dict[str, Any]],
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Helper para o endpoint de financeiro da Omie.

        Endpoint:
        /api/v1/financas/mf/
        """
        return self.executar_call(
            endpoint_path="/api/v1/financas/mf/",
            call=call,
            parametros=parametros,
            timeout=timeout,
        )

    def listar_movimentos(
        self,
        *,
        tipo_lancamento: str,
        pagina: int,
        registros_por_pagina: int,
        data_alteracao_de: str,
        data_alteracao_ate: str,
        dados_cadastro: str = "S",
        exibir_departamentos: str = "S",
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Helper específico para a operação ListarMovimentos do financeiro.

        Isso é apenas conveniência.
        O hook continua genérico porque a base é o método executar_call().
        """
        parametros = [
            {
                "nPagina": pagina,
                "nRegPorPagina": registros_por_pagina,
                "cTpLancamento": tipo_lancamento,
                "lDadosCad": dados_cadastro,
                "cExibirDepartamentos": exibir_departamentos,
                "dDtAltDe": data_alteracao_de,
                "dDtAltAte": data_alteracao_ate,
            }
        ]

        return self.executar_call_financeiro(
            call="ListarMovimentos",
            parametros=parametros,
            timeout=timeout,
        )