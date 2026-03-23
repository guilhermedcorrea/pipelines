from __future__ import annotations

from typing import Final

"""
Nome do arquivo:
pipeline_health_monitor/tradutor_erros.py

O que este arquivo faz:
- traduz mensagens técnicas comuns para textos mais humanos
- tenta agrupar erros por padrões conhecidos
- devolve uma explicação curta e útil para a interface

Lógica:
- eu normalizo o texto para minúsculas e removo espaços duplicados
- depois procuro padrões por prioridade
- se encontrar um padrão, devolvo a mensagem amigável correspondente
- se não encontrar, devolvo uma mensagem genérica
"""

MENSAGEM_ERRO_DESCONHECIDO: Final[str] = (
    "Falha técnica não classificada automaticamente. "
    "É recomendável verificar o log detalhado da execução."
)

MAPEAMENTOS_ERRO: Final[list[tuple[list[str], str]]] = [
    (
        ["login timeout expired", "login timeout", "timed out", "api timeout", "timeout"],
        "Tempo de resposta excedido. A dependência demorou mais do que o limite esperado.",
    ),
    (
        ["jwt expired", "token expired", "expired token", "access token expired"],
        "Credencial ou token expirado. O pipeline provavelmente precisa renovar a autenticação.",
    ),
    (
        [
            "db connection error",
            "connection refused",
            "could not connect",
            "connection error",
            "failed to connect",
        ],
        "Falha de conexão com banco ou serviço remoto. Pode ser indisponibilidade, rede ou credencial.",
    ),
    (
        ["permission denied", "access denied", "forbidden", "not authorized", "unauthorized"],
        "Acesso negado. O pipeline não tem permissão suficiente para executar esta operação.",
    ),
    (
        ["file not found", "no such file", "arquivo não encontrado"],
        "Arquivo esperado não foi encontrado no local configurado.",
    ),
    (
        [
            "worksheet not found",
            "sheet not found",
            "aba inexistente",
            "worksheet",
        ],
        "A aba esperada do arquivo não foi encontrada ou está com nome diferente do esperado.",
    ),
    (
        ["deadlock"],
        "O banco entrou em conflito de concorrência. A operação precisará ser repetida ou reorganizada.",
    ),
    (
        ["incorrect syntax", "sql syntax", "syntax error"],
        "Há erro de sintaxe em SQL ou na instrução enviada ao banco.",
    ),
    (
        ["out of memory", "memoryerror", "memory error", "memory"],
        "Falta de memória no processo. O volume ou a forma de processamento pode estar pesados demais.",
    ),
    (
        ["name resolution", "temporary failure in name resolution", "dns"],
        "Falha de resolução de nome de host. O serviço pode estar inacessível ou com DNS inválido.",
    ),
]


def _normalizar_texto(texto: str) -> str:
    """
    Eu padronizo o texto para facilitar a comparação por padrões.
    """
    return " ".join(texto.strip().lower().split())


def traduzir_erro(erro: str | None) -> str | None:
    """
    Traduz mensagens técnicas mais comuns para uma explicação humana.

    Regra:
    - se não houver erro, devolvo None
    - se houver padrão conhecido, devolvo a tradução correspondente
    - se não houver padrão conhecido, devolvo uma mensagem genérica
    """
    if erro is None:
        return None

    erro_texto = str(erro).strip()
    if not erro_texto:
        return None

    erro_normalizado = _normalizar_texto(erro_texto)

    for chaves, mensagem in MAPEAMENTOS_ERRO:
        if any(chave in erro_normalizado for chave in chaves):
            return mensagem

    return MENSAGEM_ERRO_DESCONHECIDO