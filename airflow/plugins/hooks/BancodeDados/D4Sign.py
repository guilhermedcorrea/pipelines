from __future__ import annotations

import os
from typing import Any

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class HookD4Sign:
    def __init__(
        self,
        token_env: str = "TOKEN_D4SIGN",
        cryptkey_env: str = "CRYPTKEY_D4SIGN",
        base_url_env: str = "BASE_URL_D4SIGN",
        base_url_padrao: str = "https://secure.d4sign.com.br/api/v1",
        timeout_requisicao: int = 60,
        total_tentativas: int = 3,
        fator_espera_retry: float = 0.5,
    ) -> None:
        """Inicializo o hook da D4Sign usando variáveis de ambiente."""
        self.token_env = token_env
        self.cryptkey_env = cryptkey_env
        self.base_url_env = base_url_env
        self.base_url_padrao = base_url_padrao
        self.timeout_requisicao = timeout_requisicao
        self.total_tentativas = total_tentativas
        self.fator_espera_retry = fator_espera_retry
        self.sessao = self._criar_sessao_http()

    def _criar_sessao_http(self) -> Session:
        """Crio uma sessão HTTP com retry para falhas temporárias da API."""
        sessao = requests.Session()

        retry = Retry(
            total=self.total_tentativas,
            connect=self.total_tentativas,
            read=self.total_tentativas,
            status=self.total_tentativas,
            backoff_factor=self.fator_espera_retry,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST", "PUT", "PATCH", "DELETE"]),
            raise_on_status=False,
        )

        adapter = HTTPAdapter(max_retries=retry)
        sessao.mount("https://", adapter)
        sessao.mount("http://", adapter)

        return sessao

    def obter_token(self) -> str:
        """Busco o token da D4Sign na variável de ambiente TOKEN_D4SIGN."""
        token = os.getenv(self.token_env, "").strip()

        if not token:
            raise RuntimeError(
                f"Variável de ambiente {self.token_env} não encontrada ou vazia."
            )

        return token

    def obter_cryptkey(self) -> str | None:
        """Busco a cryptKey da D4Sign; se estiver vazia, não envio na URL."""
        cryptkey = os.getenv(self.cryptkey_env, "").strip()
        return cryptkey or None

    def obter_base_url(self) -> str:
        """Busco a URL base da D4Sign; se não existir no ambiente, uso produção."""
        base_url = os.getenv(self.base_url_env, self.base_url_padrao).strip()
        return base_url.rstrip("/")

    def obter_parametros_autenticacao(self) -> dict[str, str]:
        """Monto os parâmetros tokenAPI e cryptKey exigidos pela D4Sign."""
        parametros = {"tokenAPI": self.obter_token()}

        cryptkey = self.obter_cryptkey()
        if cryptkey:
            parametros["cryptKey"] = cryptkey

        return parametros

    def montar_url(self, endpoint: str) -> str:
        """Monto a URL final aceitando endpoint relativo ou URL completa."""
        endpoint_limpo = endpoint.strip()

        if endpoint_limpo.startswith("http://") or endpoint_limpo.startswith("https://"):
            return endpoint_limpo

        return f"{self.obter_base_url()}/{endpoint_limpo.lstrip('/')}"

    def _converter_resposta(self, resposta: Response) -> Any:
        """Converto a resposta em JSON quando possível; caso contrário, retorno texto."""
        if resposta.status_code == 204:
            return {}

        if not resposta.text:
            return {}

        try:
            return resposta.json()
        except ValueError:
            return resposta.text

    def _validar_resposta(self, resposta: Response) -> Any:
        """Valido HTTP status e levanto erro claro quando a D4Sign recusar a chamada."""
        conteudo = self._converter_resposta(resposta)

        if 200 <= resposta.status_code <= 299:
            return conteudo

        raise RuntimeError(
            "Erro ao chamar API D4Sign. "
            f"Status HTTP: {resposta.status_code}. "
            f"URL: {resposta.url}. "
            f"Resposta: {conteudo}"
        )

    def executar_requisicao(
        self,
        metodo: str,
        endpoint: str,
        parametros: dict[str, Any] | None = None,
        corpo_json: dict[str, Any] | list[Any] | None = None,
        dados: dict[str, Any] | None = None,
        arquivos: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Executo uma chamada HTTP genérica contra a API da D4Sign."""
        url = self.montar_url(endpoint)

        parametros_finais: dict[str, Any] = {}
        parametros_finais.update(parametros or {})
        parametros_finais.update(self.obter_parametros_autenticacao())

        headers_finais = {
            "Accept": "application/json",
        }
        headers_finais.update(headers or {})

        if corpo_json is not None and arquivos is None:
            headers_finais.setdefault("Content-Type", "application/json")

        resposta = self.sessao.request(
            method=metodo.upper(),
            url=url,
            params=parametros_finais,
            json=corpo_json,
            data=dados,
            files=arquivos,
            headers=headers_finais,
            timeout=self.timeout_requisicao,
        )

        return self._validar_resposta(resposta)

    def get(
        self,
        endpoint: str,
        parametros: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Executo GET na API da D4Sign."""
        return self.executar_requisicao(
            metodo="GET",
            endpoint=endpoint,
            parametros=parametros,
            headers=headers,
        )

    def post(
        self,
        endpoint: str,
        corpo_json: dict[str, Any] | list[Any] | None = None,
        parametros: dict[str, Any] | None = None,
        dados: dict[str, Any] | None = None,
        arquivos: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Executo POST na API da D4Sign."""
        return self.executar_requisicao(
            metodo="POST",
            endpoint=endpoint,
            parametros=parametros,
            corpo_json=corpo_json,
            dados=dados,
            arquivos=arquivos,
            headers=headers,
        )

    def put(
        self,
        endpoint: str,
        corpo_json: dict[str, Any] | list[Any] | None = None,
        parametros: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Executo PUT na API da D4Sign."""
        return self.executar_requisicao(
            metodo="PUT",
            endpoint=endpoint,
            parametros=parametros,
            corpo_json=corpo_json,
            headers=headers,
        )

    def delete(
        self,
        endpoint: str,
        parametros: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Executo DELETE na API da D4Sign."""
        return self.executar_requisicao(
            metodo="DELETE",
            endpoint=endpoint,
            parametros=parametros,
            headers=headers,
        )

    def testar_conexao(self) -> str:
        """Testo a autenticação listando os cofres da conta."""
        resposta = self.listar_cofres()

        if resposta is None:
            raise RuntimeError("A D4Sign respondeu vazio ao listar cofres.")

        return "Conexão com D4Sign realizada com sucesso."

    def listar_cofres(self) -> Any:
        """Listo todos os cofres disponíveis na conta D4Sign."""
        return self.get("/safes")

    def listar_documentos(self, pagina: int | None = None) -> Any:
        """Listo documentos da conta, com paginação opcional pelo parâmetro PG."""
        parametros = {"PG": pagina} if pagina is not None else None
        return self.get("/documents", parametros=parametros)

    def listar_documento(self, uuid_documento: str) -> Any:
        """Busco os detalhes de um documento específico pelo UUID."""
        return self.get(f"/documents/{uuid_documento}")

    def listar_templates(self) -> Any:
        """Listo os templates cadastrados na conta D4Sign."""
        return self.post("/templates", corpo_json={})

    def criar_documento_por_template_word(
        self,
        uuid_safe: str,
        nome_documento: str,
        id_template: str,
        variaveis_template: dict[str, Any],
        uuid_folder: str | None = None,
    ) -> Any:
        """Crio um documento em um cofre usando um template Word e variáveis."""
        corpo: dict[str, Any] = {
            "name_document": nome_documento,
            "templates": {
                id_template: variaveis_template,
            },
        }

        if uuid_folder:
            corpo["uuid_folder"] = uuid_folder

        return self.post(
            endpoint=f"/documents/{uuid_safe}/makedocumentbytemplateword",
            corpo_json=corpo,
        )

    def criar_documento_por_template_word_payload_livre(
        self,
        uuid_safe: str,
        payload: dict[str, Any],
    ) -> Any:
        """Crio documento por template Word enviando o payload exatamente como recebido."""
        return self.post(
            endpoint=f"/documents/{uuid_safe}/makedocumentbytemplateword",
            corpo_json=payload,
        )

    def baixar_documento(
        self,
        uuid_documento: str,
        tipo: str = "pdf",
        linguagem: str = "pt",
        retornar_base64: bool = False,
    ) -> Any:
        """Solicito o download de um documento na D4Sign."""
        corpo = {
            "type": tipo,
            "language": linguagem,
            "encoding": retornar_base64,
        }

        return self.post(
            endpoint=f"/documents/{uuid_documento}/download",
            corpo_json=corpo,
        )
