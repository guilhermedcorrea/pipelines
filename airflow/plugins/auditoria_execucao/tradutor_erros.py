from __future__ import annotations


def traduzir_erro(mensagem_erro: str | None) -> dict[str, str | None]:
    """Traduz erros técnicos recorrentes para mensagens operacionais amigáveis."""
    texto = (mensagem_erro or "").lower().strip()

    if not texto:
        return {
            "erro_traduzido": None,
            "causa_provavel": None,
            "acao_sugerida": None,
        }

    if "timeout" in texto:
        return {
            "erro_traduzido": "A operação excedeu o tempo limite.",
            "causa_provavel": "Consulta lenta, rede instável, bloqueio no banco ou serviço externo demorando para responder.",
            "acao_sugerida": "Verifique tempo de resposta, índices, volume processado e disponibilidade do sistema consultado.",
        }

    if "login failed" in texto or "authentication" in texto or "unauthorized" in texto:
        return {
            "erro_traduzido": "Falha de autenticação.",
            "causa_provavel": "Credencial inválida, expirada ou mal configurada.",
            "acao_sugerida": "Revise a connection do Airflow, token, usuário e senha.",
        }

    if "file not found" in texto or "no such file" in texto or "cannot find the file" in texto:
        return {
            "erro_traduzido": "Arquivo de entrada não foi encontrado.",
            "causa_provavel": "Caminho inválido, volume não montado no container ou etapa anterior não gerou o arquivo.",
            "acao_sugerida": "Valide o caminho físico, bind mount e a etapa anterior da DAG.",
        }

    if "permission denied" in texto or "access is denied" in texto:
        return {
            "erro_traduzido": "Acesso negado ao recurso necessário.",
            "causa_provavel": "Permissão insuficiente em pasta, arquivo, banco ou rede.",
            "acao_sugerida": "Revise permissões do container, usuário do Airflow e regras de acesso.",
        }

    if "column" in texto and ("invalid" in texto or "not found" in texto):
        return {
            "erro_traduzido": "Estrutura de dados divergente do esperado.",
            "causa_provavel": "Coluna ausente, renomeada ou consulta usando schema desatualizado.",
            "acao_sugerida": "Compare o schema esperado pela task com a tabela ou arquivo atual.",
        }

    if "json" in texto and ("decode" in texto or "invalid" in texto):
        return {
            "erro_traduzido": "Resposta JSON inválida.",
            "causa_provavel": "API retornou conteúdo inesperado ou payload corrompido.",
            "acao_sugerida": "Abra o log bruto e valide a resposta recebida da API.",
        }

    if "08s01" in texto or "communication link failure" in texto:
        return {
            "erro_traduzido": "A conexão com o banco foi interrompida durante a execução.",
            "causa_provavel": "Instabilidade de rede, reinício do serviço ou timeout de comunicação.",
            "acao_sugerida": "Verifique conectividade, firewall, timeout e saúde do SQL Server.",
        }

    return {
        "erro_traduzido": "Falha não classificada automaticamente.",
        "causa_provavel": "O erro ainda não possui regra de tradução cadastrada.",
        "acao_sugerida": "Abra o log técnico e, se recorrente, adicione nova regra no tradutor de erros.",
    }