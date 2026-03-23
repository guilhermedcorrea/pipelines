from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ScoresDimensoes:
    agendamento: int
    execucao: int
    performance: int
    dependencias: int
    dados: int
    confiabilidade: int

    @property
    def score_total(self) -> int:
        """
        Calcula a média simples das dimensões.
        """
        valores = [
            self.agendamento,
            self.execucao,
            self.performance,
            self.dependencias,
            self.dados,
            self.confiabilidade,
        ]
        return round(sum(valores) / len(valores))


def _para_int(valor: Any, padrao: int = 0) -> int:
    """
    Converte valor para inteiro de forma segura.
    """
    try:
        if valor is None:
            return padrao

        if isinstance(valor, bool):
            return int(valor)

        if isinstance(valor, str):
            valor_limpo = valor.strip()
            if not valor_limpo:
                return padrao
            return int(float(valor_limpo.replace(",", ".")))

        return int(valor)
    except (TypeError, ValueError):
        return padrao


def _para_float(valor: Any, padrao: float = 0.0) -> float:
    """
    Converte valor para float de forma segura.
    """
    try:
        if valor is None:
            return padrao

        if isinstance(valor, bool):
            return float(valor)

        if isinstance(valor, str):
            valor_limpo = valor.strip()
            if not valor_limpo:
                return padrao
            return float(valor_limpo.replace(",", "."))

        return float(valor)
    except (TypeError, ValueError):
        return padrao


def limitar_entre_zero_e_cem(valor: float | int) -> int:
    """
    Garante que o score fique entre 0 e 100.
    """
    return max(0, min(100, round(valor)))


def classificar_status_por_score(score: int, pausado: bool = False) -> str:
    """
    Traduz score em status amigável do monitor.
    """
    if pausado:
        return "paused"

    if score >= 90:
        return "healthy"

    if score >= 75:
        return "degraded"

    return "critical"


def calcular_score_agendamento(
    atraso_minutos: int | None,
    execucoes_perdidas: int = 0,
    pausado: bool = False,
) -> int:
    """
    Regras simples de score para agendamento.

    Lógica:
    - se está pausado, não faz sentido tratar como problema operacional
    - sem atraso: score alto
    - atraso moderado: reduz
    - atraso grande ou execuções perdidas: reduz bastante
    """
    if pausado:
        return 100

    score = 100
    atraso_minutos_tratado = max(0, _para_int(atraso_minutos, 0))
    execucoes_perdidas_tratado = max(0, _para_int(execucoes_perdidas, 0))

    if atraso_minutos_tratado > 5:
        score -= 5

    if atraso_minutos_tratado > 15:
        score -= 10

    if atraso_minutos_tratado > 30:
        score -= 20

    if atraso_minutos_tratado > 60:
        score -= 30

    if execucoes_perdidas_tratado > 0:
        score -= execucoes_perdidas_tratado * 15

    return limitar_entre_zero_e_cem(score)


def calcular_score_execucao(
    taxa_sucesso_percentual: float,
    quantidade_falhas_recentes: int = 0,
    quantidade_retries_recentes: int = 0,
) -> int:
    """
    Regras de score para execução.

    Lógica:
    - taxa de sucesso começa como base principal
    - falhas recentes penalizam
    - retries excessivos também penalizam
    """
    score = limitar_entre_zero_e_cem(_para_float(taxa_sucesso_percentual, 100.0))
    falhas = max(0, _para_int(quantidade_falhas_recentes, 0))
    retries = max(0, _para_int(quantidade_retries_recentes, 0))

    score -= falhas * 6
    score -= retries * 2

    return limitar_entre_zero_e_cem(score)


def calcular_score_performance(
    duracao_atual_min: int | None,
    duracao_media_min: int | None,
) -> int:
    """
    Regras de score para performance.

    Lógica:
    - se não há dados suficientes, devolve score neutro-alto
    - se duração atual está próxima da média, score alto
    - se está muito acima, score cai
    """
    duracao_atual = _para_float(duracao_atual_min, 0.0)
    duracao_media = _para_float(duracao_media_min, 0.0)

    if duracao_atual <= 0 or duracao_media <= 0:
        return 85

    proporcao = duracao_atual / duracao_media

    if proporcao <= 1.0:
        return 100

    if proporcao <= 1.2:
        return 92

    if proporcao <= 1.5:
        return 80

    if proporcao <= 2.0:
        return 65

    if proporcao <= 3.0:
        return 45

    return 25


def calcular_score_dependencias(
    quantidade_dependencias_down: int = 0,
    quantidade_dependencias_warning: int = 0,
    quantidade_dependencias_unstable: int = 0,
) -> int:
    """
    Regras de score para dependências.

    Peso:
    - down pesa muito
    - unstable pesa médio
    - warning pesa menos
    """
    score = 100
    down = max(0, _para_int(quantidade_dependencias_down, 0))
    warning = max(0, _para_int(quantidade_dependencias_warning, 0))
    unstable = max(0, _para_int(quantidade_dependencias_unstable, 0))

    score -= down * 35
    score -= unstable * 15
    score -= warning * 8

    return limitar_entre_zero_e_cem(score)


def calcular_score_dados(
    data_quality: str | None,
    percentual_rejeicao: float | None = None,
    volume_zerado_inesperado: bool = False,
) -> int:
    """
    Regras de score para qualidade de dados.
    """
    score = 100

    if data_quality:
        valor_normalizado = str(data_quality).strip().lower()

        if valor_normalizado == "ok":
            score = 100
        elif valor_normalizado == "warning":
            score = 70
        elif valor_normalizado == "critical":
            score = 30

    percentual_rejeicao_tratado = _para_float(percentual_rejeicao, 0.0)

    if percentual_rejeicao_tratado > 1:
        score -= 5
    if percentual_rejeicao_tratado > 5:
        score -= 15
    if percentual_rejeicao_tratado > 10:
        score -= 30

    if bool(volume_zerado_inesperado):
        score -= 50

    return limitar_entre_zero_e_cem(score)


def calcular_score_confiabilidade(
    quantidade_falhas_recentes: int = 0,
    quantidade_incidentes_abertos: int = 0,
    quantidade_intervencoes_manuais: int = 0,
) -> int:
    """
    Regras de score para confiabilidade operacional.
    """
    score = 100
    falhas = max(0, _para_int(quantidade_falhas_recentes, 0))
    incidentes = max(0, _para_int(quantidade_incidentes_abertos, 0))
    intervencoes = max(0, _para_int(quantidade_intervencoes_manuais, 0))

    score -= falhas * 8
    score -= incidentes * 15
    score -= intervencoes * 7

    return limitar_entre_zero_e_cem(score)


def calcular_scores_pipeline(registro: dict[str, Any]) -> dict[str, Any]:
    """
    Calcula todos os scores de um pipeline a partir de um registro simples.

    Espera um dicionário com chaves opcionais como:
    - atraso_minutos
    - execucoes_perdidas
    - taxa_sucesso_percentual
    - quantidade_falhas_recentes
    - quantidade_retries_recentes
    - duracao_atual_min
    - duracao_media_min
    - quantidade_dependencias_down
    - quantidade_dependencias_warning
    - quantidade_dependencias_unstable
    - data_quality
    - percentual_rejeicao
    - volume_zerado_inesperado
    - quantidade_incidentes_abertos
    - quantidade_intervencoes_manuais
    - pausado
    """
    pausado = bool(registro.get("pausado", False))

    scores = ScoresDimensoes(
        agendamento=calcular_score_agendamento(
            atraso_minutos=_para_int(registro.get("atraso_minutos"), 0),
            execucoes_perdidas=_para_int(registro.get("execucoes_perdidas"), 0),
            pausado=pausado,
        ),
        execucao=calcular_score_execucao(
            taxa_sucesso_percentual=_para_float(
                registro.get("taxa_sucesso_percentual"),
                100.0,
            ),
            quantidade_falhas_recentes=_para_int(
                registro.get("quantidade_falhas_recentes"),
                0,
            ),
            quantidade_retries_recentes=_para_int(
                registro.get("quantidade_retries_recentes"),
                0,
            ),
        ),
        performance=calcular_score_performance(
            duracao_atual_min=_para_int(registro.get("duracao_atual_min"), 0),
            duracao_media_min=_para_int(registro.get("duracao_media_min"), 0),
        ),
        dependencias=calcular_score_dependencias(
            quantidade_dependencias_down=_para_int(
                registro.get("quantidade_dependencias_down"),
                0,
            ),
            quantidade_dependencias_warning=_para_int(
                registro.get("quantidade_dependencias_warning"),
                0,
            ),
            quantidade_dependencias_unstable=_para_int(
                registro.get("quantidade_dependencias_unstable"),
                0,
            ),
        ),
        dados=calcular_score_dados(
            data_quality=registro.get("data_quality"),
            percentual_rejeicao=_para_float(
                registro.get("percentual_rejeicao"),
                0.0,
            ),
            volume_zerado_inesperado=bool(
                registro.get("volume_zerado_inesperado", False)
            ),
        ),
        confiabilidade=calcular_score_confiabilidade(
            quantidade_falhas_recentes=_para_int(
                registro.get("quantidade_falhas_recentes"),
                0,
            ),
            quantidade_incidentes_abertos=_para_int(
                registro.get("quantidade_incidentes_abertos"),
                0,
            ),
            quantidade_intervencoes_manuais=_para_int(
                registro.get("quantidade_intervencoes_manuais"),
                0,
            ),
        ),
    )

    score_total = scores.score_total
    status = classificar_status_por_score(score_total, pausado=pausado)

    return {
        "agendamento": scores.agendamento,
        "execucao": scores.execucao,
        "performance": scores.performance,
        "dependencias": scores.dependencias,
        "dados": scores.dados,
        "confiabilidade": scores.confiabilidade,
        "health_score": score_total,
        "status": status,
    }