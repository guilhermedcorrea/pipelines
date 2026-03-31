from __future__ import annotations

import os
from typing import Any

import requests
from airflow.exceptions import AirflowException
from airflow.hooks.base import BaseHook


class AuvoHook(BaseHook):
    """
    Eu centralizo a autenticação e as chamadas HTTP da API do Auvo.

    Fontes de credencial aceitas, nesta ordem:
    1) Variáveis de ambiente:
       - KEY_AUVO
       - TOKEN_AUVO
       - AUVO_BASE_URL (opcional)
    2) Connection do Airflow:
       - host     -> base_url
       - login    -> api_key
       - password -> api_token
       - extra    -> api_key / api_token / base_url
    """

    conn_name_attr = "auvo_conn_id"
    default_conn_name = "auvo_default"
    conn_type = "auvo"
    hook_name = "Auvo"

    def __init__(
        self,
        auvo_conn_id: str = default_conn_name,
        timeout: int = 30,
    ) -> None:
        """
        Eu apenas guardo as configurações.
        Não faço login aqui para evitar trabalho durante o parse da DAG.
        """
        super().__init__()
        self.auvo_conn_id = auvo_conn_id
        self.timeout = timeout

        self._sessao_http: requests.Session | None = None
        self._token_acesso: str | None = None
        self._conexao_airflow_cache = None
        self._base_url_cache: str | None = None

    @staticmethod
    def _limpar_texto(valor: Any) -> str:
        """
        Eu converto qualquer valor para texto limpo.
        """
        if valor is None:
            return ""
        return str(valor).strip()

    @staticmethod
    def _normalizar_base_url(base_url: str) -> str:
        """
        Eu garanto que a URL base fique consistente.
        """
        url = (base_url or "").strip()

        if not url:
            url = "https://api.auvo.com.br/v2"

        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        return url.rstrip("/")

    def _obter_conexao_airflow(self):
        """
        Eu tento ler a conexão do Airflow apenas quando necessário.
        """
        if self._conexao_airflow_cache is not None:
            return self._conexao_airflow_cache

        try:
            self._conexao_airflow_cache = self.get_connection(self.auvo_conn_id)
            self.log.info(
                "Conexão do Airflow '%s' encontrada para o Auvo.",
                self.auvo_conn_id,
            )
        except Exception as erro:
            self._conexao_airflow_cache = None
            self.log.info(
                "Conexão do Airflow '%s' não encontrada. "
                "Vou tentar usar variáveis de ambiente. Detalhe: %s",
                self.auvo_conn_id,
                erro,
            )

        return self._conexao_airflow_cache

    def _obter_configuracao(self) -> dict[str, str]:
        """
        Eu resolvo base_url, api_key e api_token a partir do ambiente
        ou da Connection do Airflow.
        """
        conexao = self._obter_conexao_airflow()
        extras = conexao.extra_dejson if conexao else {}

        api_key_env = self._limpar_texto(os.getenv("KEY_AUVO"))
        api_token_env = self._limpar_texto(os.getenv("TOKEN_AUVO"))
        base_url_env = self._limpar_texto(os.getenv("AUVO_BASE_URL"))

        api_key_conn = ""
        api_token_conn = ""
        base_url_conn = ""

        if conexao:
            api_key_conn = (
                self._limpar_texto(extras.get("api_key"))
                or self._limpar_texto(extras.get("apiKey"))
                or self._limpar_texto(conexao.login)
            )

            api_token_conn = (
                self._limpar_texto(extras.get("api_token"))
                or self._limpar_texto(extras.get("apiToken"))
                or self._limpar_texto(conexao.password)
            )

            base_url_conn = (
                self._limpar_texto(extras.get("base_url"))
                or self._limpar_texto(extras.get("baseUrl"))
                or self._limpar_texto(conexao.host)
            )

        api_key = api_key_env or api_key_conn
        api_token = api_token_env or api_token_conn
        base_url = self._normalizar_base_url(base_url_env or base_url_conn)

        if not api_key:
            raise AirflowException(
                "KEY_AUVO não foi encontrada. "
                "Defina KEY_AUVO no .env/ambiente ou use login/extra.api_key "
                f"na Connection '{self.auvo_conn_id}'."
            )

        if not api_token:
            raise AirflowException(
                "TOKEN_AUVO não foi encontrada. "
                "Defina TOKEN_AUVO no .env/ambiente ou use password/extra.api_token "
                f"na Connection '{self.auvo_conn_id}'."
            )

        self._base_url_cache = base_url

        return {
            "base_url": base_url,
            "api_key": api_key,
            "api_token": api_token,
        }

    @property
    def base_url(self) -> str:
        """
        Eu devolvo a URL base já resolvida.
        """
        if self._base_url_cache:
            return self._base_url_cache

        configuracao = self._obter_configuracao()
        self._base_url_cache = configuracao["base_url"]
        return self._base_url_cache

    def obter_token_acesso(self, forcar_renovacao: bool = False) -> str:
        """
        Eu faço login no Auvo e guardo o bearer token.
        """
        if self._token_acesso and not forcar_renovacao:
            return self._token_acesso

        configuracao = self._obter_configuracao()
        url_login = f"{configuracao['base_url']}/login"

        cabecalhos = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "apiKey": configuracao["api_key"],
            "apiToken": configuracao["api_token"],
        }

        self.log.info("Vou autenticar no Auvo em %s", url_login)

        resposta = requests.post(
            url_login,
            headers=cabecalhos,
            json=payload,
            timeout=self.timeout,
        )

        if resposta.status_code != 200:
            raise AirflowException(
                f"Falha no login do Auvo. "
                f"HTTP={resposta.status_code} | Resposta={resposta.text[:1000]}"
            )

        try:
            dados = resposta.json() if resposta.text else {}
        except Exception as erro:
            raise AirflowException(
                f"Login do Auvo retornou conteúdo inválido. Erro: {erro}"
            ) from erro

        token = (
            dados.get("accessToken")
            or (dados.get("result") or {}).get("accessToken")
            or dados.get("token")
            or (dados.get("result") or {}).get("token")
        )

        if not token:
            raise AirflowException(
                "O login no Auvo retornou 200, mas não trouxe accessToken/token. "
                f"Chaves recebidas: {list(dados.keys())}"
            )

        self._token_acesso = str(token).strip()

        if self._sessao_http is not None:
            self._sessao_http.headers.update(
                {"Authorization": f"Bearer {self._token_acesso}"}
            )

        self.log.info("Token do Auvo obtido com sucesso.")
        return self._token_acesso

    def get_conn(self) -> requests.Session:
        """
        Eu devolvo uma sessão HTTP autenticada.
        """
        if self._sessao_http is not None:
            return self._sessao_http

        token = self.obter_token_acesso()

        sessao = requests.Session()
        sessao.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            }
        )

        self._sessao_http = sessao
        return self._sessao_http

    def _fazer_requisicao(
        self,
        metodo: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | list[Any] | None = None,
        timeout: int | None = None,
        aceitar_404: bool = False,
        renovar_token_em_401: bool = True,
    ) -> Any:
        """
        Eu executo uma chamada HTTP genérica para a API do Auvo.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        sessao = self.get_conn()

        self.log.info(
            "Chamando Auvo | metodo=%s | url=%s | params=%s",
            metodo.upper(),
            url,
            params,
        )

        resposta = sessao.request(
            method=metodo.upper(),
            url=url,
            params=params,
            json=json_payload,
            timeout=timeout or self.timeout,
        )

        if resposta.status_code == 401 and renovar_token_em_401:
            self.log.warning(
                "Recebi 401 no Auvo. Vou renovar o token e tentar novamente."
            )
            self.obter_token_acesso(forcar_renovacao=True)

            resposta = sessao.request(
                method=metodo.upper(),
                url=url,
                params=params,
                json=json_payload,
                timeout=timeout or self.timeout,
            )

        if resposta.status_code == 404 and aceitar_404:
            self.log.info("Endpoint retornou 404 e isso foi aceito. url=%s", url)
            return None

        if resposta.status_code >= 400:
            raise AirflowException(
                f"Erro ao chamar Auvo. "
                f"HTTP={resposta.status_code} | URL={url} | "
                f"Resposta={resposta.text[:1000]}"
            )

        if not resposta.text:
            return {}

        try:
            return resposta.json()
        except Exception:
            return resposta.text

    def buscar_ticket_por_id(self, id_ticket: int) -> dict[str, Any]:
        """
        Eu busco um ticket específico no Auvo.
        """
        if id_ticket is None:
            raise AirflowException("id_ticket não pode ser vazio.")

        resposta = self._fazer_requisicao(
            "GET",
            f"/tickets/{int(id_ticket)}",
            aceitar_404=False,
        )

        if not isinstance(resposta, dict):
            raise AirflowException(
                f"Resposta inesperada ao buscar ticket {id_ticket}: {type(resposta)}"
            )

        return resposta

    def listar_tickets(
        self,
        parametros: dict[str, Any] | None = None,
    ) -> Any:
        """
        Eu listo tickets usando o endpoint padrão /tickets.
        """
        return self._fazer_requisicao(
            "GET",
            "/tickets",
            params=parametros or {},
        )

    def buscar_tarefa_por_id(self, id_tarefa: int) -> dict[str, Any]:
        """
        Eu busco uma tarefa específica no Auvo.
        """
        if id_tarefa is None:
            raise AirflowException("id_tarefa não pode ser vazio.")

        resposta = self._fazer_requisicao(
            "GET",
            f"/tasks/{int(id_tarefa)}",
            aceitar_404=False,
        )

        if not isinstance(resposta, dict):
            raise AirflowException(
                f"Resposta inesperada ao buscar tarefa {id_tarefa}: {type(resposta)}"
            )

        return resposta

    def post(self, endpoint: str, payload: dict[str, Any] | list[Any]) -> Any:
        """
        Eu envio POST genérico para endpoints do Auvo.
        """
        return self._fazer_requisicao(
            "POST",
            endpoint,
            json_payload=payload,
        )

    def put(self, endpoint: str, payload: dict[str, Any] | list[Any]) -> Any:
        """
        Eu envio PUT genérico para endpoints do Auvo.
        """
        return self._fazer_requisicao(
            "PUT",
            endpoint,
            json_payload=payload,
        )

    def patch(self, endpoint: str, payload: dict[str, Any] | list[Any]) -> Any:
        """
        Eu envio PATCH genérico para endpoints do Auvo.
        """
        return self._fazer_requisicao(
            "PATCH",
            endpoint,
            json_payload=payload,
        )

    def delete(self, endpoint: str) -> Any:
        """
        Eu envio DELETE genérico para endpoints do Auvo.
        """
        return self._fazer_requisicao(
            "DELETE",
            endpoint,
        )

    def test_connection(self) -> tuple[bool, str]:
        """
        Eu testo a autenticação do hook.
        """
        try:
            token = self.obter_token_acesso(forcar_renovacao=True)

            if not token:
                return False, "Login executado, mas o token veio vazio."

            return True, "Login no Auvo executado com sucesso."
        except Exception as erro:
            return False, f"Falha ao autenticar no Auvo: {erro}"

    @staticmethod
    def get_ui_field_behaviour() -> dict[str, Any]:
        """
        Eu personalizo a tela de Connection do Airflow.
        """
        return {
            "hidden_fields": ["schema", "port"],
            "relabeling": {
                "host": "Base URL",
                "login": "API Key",
                "password": "API Token",
            },
            "placeholders": {
                "host": "https://api.auvo.com.br/v2",
                "login": "Informe a API Key do Auvo",
                "password": "Informe o API Token do Auvo",
            },
        }