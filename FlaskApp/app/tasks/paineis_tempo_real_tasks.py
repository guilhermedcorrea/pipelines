from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from app.celery_app import celery_app
from app.extensions import cache, db, socketio


NAMESPACE_PAINEIS = "/paineis"


def _normalizar_texto(valor: Any) -> str:
    return str(valor or "").strip()


def _normalizar_tipo(valor: Any) -> str:
    return _normalizar_texto(valor).upper()


def _normalizar_codface(valor: Any) -> str:
    return _normalizar_texto(valor).upper()


def _parse_data_iso(valor: Any) -> date | None:
    try:
        texto = _normalizar_texto(valor)
        if not texto:
            return None
        if len(texto) >= 10:
            texto = texto[:10]
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except Exception:
        return None


def _int_seguro(valor: Any, padrao: int = 0) -> int:
    try:
        if valor is None:
            return padrao
        texto = str(valor).strip()
        if not texto:
            return padrao
        return int(float(texto.replace(",", ".")))
    except Exception:
        return padrao


def _float_seguro(valor: Any, padrao: float = 0.0) -> float:
    try:
        if valor is None:
            return padrao
        texto = str(valor).strip()
        if not texto:
            return padrao
        return float(texto.replace(",", "."))
    except Exception:
        return padrao


def _formatar_pct(valor: float) -> str:
    texto = f"{float(valor or 0.0):.1f}".replace(".", ",")
    if texto.endswith(",0"):
        texto = texto[:-2]
    return texto


def _montar_cache_key(dt_ini: str, dt_fim: str, itens: list[dict[str, Any]]) -> str:
    base = {
        "dt_ini": dt_ini,
        "dt_fim": dt_fim,
        "itens": [
            {
                "codponto": _int_seguro(item.get("codponto")),
                "codface": _normalizar_codface(item.get("codface")),
                "tipo_prod": _normalizar_tipo(item.get("tipo_prod")),
            }
            for item in itens
        ],
    }
    bruto = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "paineis:ocupacao:lista:" + hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def _normalizar_itens_visiveis(itens: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    vistos: set[tuple[int, str, str]] = set()

    for item in itens or []:
        codponto = _int_seguro(item.get("codponto"))
        codface = _normalizar_codface(item.get("codface"))
        tipo_prod = _normalizar_tipo(item.get("tipo_prod"))

        if codponto <= 0 or not codface:
            continue

        chave = (codponto, codface, tipo_prod)
        if chave in vistos:
            continue
        vistos.add(chave)

        saida.append(
            {
                "codponto": codponto,
                "codface": codface,
                "tipo_prod": tipo_prod,
            }
        )

    return saida[:120]


def _buscar_ocupacao_lista_paineis(
    *,
    dt_ini: date,
    dt_fim: date,
    itens: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Eu recalculo apenas as faces visíveis da página usando slot-dia.

    Regra preservada:
    Total disponível = quantidade de dias do período × QuantidadeFaces.
    Total ocupado = soma dos slots ocupados por dia, limitado pela capacidade diária.
    Percentual = Total ocupado ÷ Total disponível × 100.
    """
    itens_norm = _normalizar_itens_visiveis(itens)
    if not itens_norm:
        return []

    if dt_fim < dt_ini:
        dt_ini, dt_fim = dt_fim, dt_ini

    parametros: dict[str, Any] = {
        "dt_ini": dt_ini.isoformat(),
        "dt_fim": dt_fim.isoformat(),
    }

    valores_sql = []
    for indice, item in enumerate(itens_norm):
        parametros[f"codponto_{indice}"] = int(item["codponto"])
        parametros[f"codface_{indice}"] = item["codface"]
        parametros[f"tipo_prod_{indice}"] = item["tipo_prod"]
        valores_sql.append(f"(:codponto_{indice}, :codface_{indice}, :tipo_prod_{indice})")

    sql = text(f"""
        DECLARE @DtIni date = TRY_CONVERT(date, :dt_ini);
        DECLARE @DtFim date = TRY_CONVERT(date, :dt_fim);

        IF @DtIni IS NULL SET @DtIni = CAST(GETDATE() AS date);
        IF @DtFim IS NULL SET @DtFim = @DtIni;
        IF @DtFim < @DtIni
        BEGIN
            DECLARE @Tmp date = @DtIni;
            SET @DtIni = @DtFim;
            SET @DtFim = @Tmp;
        END;

        ;WITH Chaves AS (
            SELECT
                CodPonto = TRY_CONVERT(int, v.CodPonto),
                CodFace = UPPER(LTRIM(RTRIM(CONVERT(varchar(80), v.CodFace)))),
                TipoProd = UPPER(LTRIM(RTRIM(CONVERT(varchar(120), v.TipoProd))))
            FROM (VALUES {", ".join(valores_sql)}) AS v(CodPonto, CodFace, TipoProd)
            WHERE TRY_CONVERT(int, v.CodPonto) IS NOT NULL
              AND NULLIF(LTRIM(RTRIM(CONVERT(varchar(80), v.CodFace))), '') IS NOT NULL
        ),
        PainelOrdenado AS (
            SELECT
                p.CodPonto,
                TipoPainel = UPPER(LTRIM(RTRIM(ISNULL(p.Tipo, '')))),
                QuantidadeFaces = NULLIF(TRY_CONVERT(int, p.QuantidadeFaces), 0),
                rn = ROW_NUMBER() OVER (
                    PARTITION BY p.CodPonto, UPPER(LTRIM(RTRIM(ISNULL(p.Tipo, ''))))
                    ORDER BY p.DataAtualizacao DESC, p.IDDimPaineisEuromidia DESC
                )
            FROM [Integracao].[Silver].[DimPaineisEuromidia] AS p
            INNER JOIN (
                SELECT DISTINCT CodPonto, TipoProd
                FROM Chaves
            ) AS c
                ON c.CodPonto = p.CodPonto
               AND c.TipoProd = UPPER(LTRIM(RTRIM(ISNULL(p.Tipo, ''))))
        ),
        Capacidade AS (
            SELECT
                c.CodPonto,
                c.CodFace,
                c.TipoProd,
                Denominador =
                    CASE
                        WHEN c.TipoProd = 'PAINEL DIGITAL'
                            THEN COALESCE(MAX(CASE WHEN po.rn = 1 THEN po.QuantidadeFaces END), 16)
                        ELSE 1
                    END
            FROM Chaves AS c
            LEFT JOIN PainelOrdenado AS po
                ON po.CodPonto = c.CodPonto
               AND po.TipoPainel = c.TipoProd
            GROUP BY
                c.CodPonto,
                c.CodFace,
                c.TipoProd
        ),
        Dias AS (
            SELECT Dia = CAST(cal.[data] AS date)
            FROM [Integracao].[Silver].[DimCalendario] AS cal
            WHERE cal.[data] >= @DtIni
              AND cal.[data] <= @DtFim
        ),
        ItensPeriodo AS (
            SELECT
                cap.CodPonto,
                cap.CodFace,
                cap.TipoProd,
                d.Dia,
                Slots =
                    CASE
                        WHEN cap.TipoProd = 'PAINEL DIGITAL'
                             AND TRY_CONVERT(int, i.Cota) = 1080 THEN CONVERT(decimal(19,4), 2.0)
                        WHEN cap.TipoProd = 'PAINEL DIGITAL'
                             AND TRY_CONVERT(int, i.Cota) = 540 THEN CONVERT(decimal(19,4), 1.0)
                        WHEN cap.TipoProd = 'PAINEL DIGITAL'
                             AND TRY_CONVERT(int, i.Cota) = 1 THEN CONVERT(decimal(19,4), 2.0)
                        WHEN cap.TipoProd = 'PAINEL DIGITAL'
                             AND TRY_CONVERT(int, i.Cota) = 2 THEN CONVERT(decimal(19,4), 1.0)
                        ELSE CONVERT(decimal(19,4), 1.0)
                    END
            FROM Capacidade AS cap
            INNER JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS i
                ON i.CodPonto = cap.CodPonto
               AND UPPER(LTRIM(RTRIM(ISNULL(i.CodFace, '')))) = cap.CodFace
               AND i.DataInicioPrevisto IS NOT NULL
               AND i.DataTerminoPrevisto IS NOT NULL
               AND TRY_CONVERT(date, i.DataInicioPrevisto) <= @DtFim
               AND TRY_CONVERT(date, i.DataTerminoPrevisto) >= @DtIni
               AND UPPER(LTRIM(RTRIM(ISNULL(i.AtivoCancelamento, 'A')))) = 'A'
            INNER JOIN Dias AS d
                ON d.Dia >= CASE
                                WHEN TRY_CONVERT(date, i.DataInicioPrevisto) > @DtIni
                                    THEN TRY_CONVERT(date, i.DataInicioPrevisto)
                                ELSE @DtIni
                            END
               AND d.Dia <= CASE
                                WHEN TRY_CONVERT(date, i.DataTerminoPrevisto) < @DtFim
                                    THEN TRY_CONVERT(date, i.DataTerminoPrevisto)
                                ELSE @DtFim
                            END
        ),
        UsoPorDia AS (
            SELECT
                CodPonto,
                CodFace,
                TipoProd,
                Dia,
                SlotsDia = SUM(Slots)
            FROM ItensPeriodo
            GROUP BY CodPonto, CodFace, TipoProd, Dia
        ),
        Agregado AS (
            SELECT
                cap.CodPonto,
                cap.CodFace,
                cap.TipoProd,
                cap.Denominador,
                TotalDiasPeriodo = DATEDIFF(DAY, @DtIni, @DtFim) + 1,
                TotalEspacosOcupados =
                    COALESCE(SUM(
                        CASE
                            WHEN upd.SlotsDia IS NULL THEN CONVERT(decimal(19,4), 0.0)
                            WHEN upd.SlotsDia < 0 THEN CONVERT(decimal(19,4), 0.0)
                            WHEN upd.SlotsDia > cap.Denominador THEN CONVERT(decimal(19,4), cap.Denominador)
                            ELSE upd.SlotsDia
                        END
                    ), CONVERT(decimal(19,4), 0.0)),
                MaxSimultaneoSemTeto = COALESCE(MAX(upd.SlotsDia), CONVERT(decimal(19,4), 0.0))
            FROM Capacidade AS cap
            LEFT JOIN UsoPorDia AS upd
                ON upd.CodPonto = cap.CodPonto
               AND upd.CodFace = cap.CodFace
               AND upd.TipoProd = cap.TipoProd
            GROUP BY
                cap.CodPonto,
                cap.CodFace,
                cap.TipoProd,
                cap.Denominador
        )
        SELECT
            CodPonto,
            CodFace,
            TipoProd,
            Denominador = CASE WHEN Denominador <= 0 THEN 1 ELSE Denominador END,
            TotalDiasPeriodo = CASE WHEN TotalDiasPeriodo <= 0 THEN 1 ELSE TotalDiasPeriodo END,
            TotalEspacosDisponiveis =
                (CASE WHEN Denominador <= 0 THEN 1 ELSE Denominador END)
                * (CASE WHEN TotalDiasPeriodo <= 0 THEN 1 ELSE TotalDiasPeriodo END),
            TotalEspacosOcupados = ROUND(TotalEspacosOcupados, 0),
            MaxSimultaneoSemTeto,
            Conflitos = CASE WHEN MaxSimultaneoSemTeto > Denominador THEN 1 ELSE 0 END
        FROM Agregado
        ORDER BY CodPonto, CodFace, TipoProd;
    """)

    rows = db.session.execute(sql, parametros).mappings().all()
    saida: list[dict[str, Any]] = []

    for row in rows:
        codponto = _int_seguro(row.get("CodPonto"))
        codface = _normalizar_codface(row.get("CodFace"))
        tipo_prod = _normalizar_tipo(row.get("TipoProd"))
        denominador = max(1, _int_seguro(row.get("Denominador"), 1))
        total_dias = max(1, _int_seguro(row.get("TotalDiasPeriodo"), 1))
        total_disponivel = max(1, _int_seguro(row.get("TotalEspacosDisponiveis"), denominador * total_dias))
        total_ocupado = _int_seguro(row.get("TotalEspacosOcupados"), 0)

        if total_ocupado < 0:
            total_ocupado = 0
        if total_ocupado > total_disponivel:
            total_ocupado = total_disponivel

        total_desocupado = max(0, total_disponivel - total_ocupado)
        pct = round((float(total_ocupado) / float(total_disponivel)) * 100.0, 1) if total_disponivel > 0 else 0.0
        pct = max(0.0, min(100.0, pct))

        try:
            ocupadas_media = int(round(float(total_ocupado) / float(total_dias), 0))
        except Exception:
            ocupadas_media = 0
        ocupadas_media = max(0, min(denominador, ocupadas_media))

        conflitos = 1 if _float_seguro(row.get("MaxSimultaneoSemTeto"), 0.0) > float(denominador) else 0
        status = "com_conflito" if conflitos > 0 else ("ocupado" if pct > 0 else "livre")

        saida.append(
            {
                "codponto": codponto,
                "codface": codface,
                "tipo_prod": tipo_prod,
                "denominador": denominador,
                "num_faces": denominador,
                "ocupadas": ocupadas_media,
                "pct": pct,
                "pct_texto": _formatar_pct(pct),
                "conflitos": int(conflitos),
                "status": status,
                "total_dias_periodo": total_dias,
                "total_espacos_disponiveis": total_disponivel,
                "total_espacos_ocupados": total_ocupado,
                "total_espacos_desocupados": total_desocupado,
                "uso_slots_dias": float(total_ocupado),
                "max_simultaneo_sem_teto": _float_seguro(row.get("MaxSimultaneoSemTeto"), 0.0),
                "texto_barra": f"Conflito ({conflitos}/{denominador})" if conflitos > 0 else f"{_formatar_pct(pct)}%",
                "texto_legenda": (
                    f"▲ Conflito ({conflitos}/{denominador})"
                    if conflitos > 0
                    else (f"● Ocupado ({ocupadas_media}/{denominador})" if ocupadas_media > 0 else f"● Livre (0/{denominador})")
                ),
            }
        )

    return saida


@celery_app.task(name="paineis_ocupacao.atualizar_lista", bind=True, ignore_result=True)
def atualizar_ocupacao_lista_paineis_socket(
    self,
    *,
    room: str,
    dt_ini: str,
    dt_fim: str,
    itens: list[dict[str, Any]],
    cache_key: str | None = None,
) -> dict[str, Any]:
    """Eu recalculo ocupação em background e envio o resultado pelo Socket.IO."""
    inicio = _parse_data_iso(dt_ini)
    fim = _parse_data_iso(dt_fim)
    itens_norm = _normalizar_itens_visiveis(itens)

    if inicio is None or fim is None or not itens_norm:
        payload = {
            "ok": False,
            "erro": "Período ou itens visíveis inválidos para recalcular ocupação.",
            "itens": [],
        }
        socketio.emit("paineis:ocupacao:erro", payload, namespace=NAMESPACE_PAINEIS, to=room)
        return payload

    if cache_key is None:
        cache_key = _montar_cache_key(inicio.isoformat(), fim.isoformat(), itens_norm)

    try:
        itens_ocupacao = _buscar_ocupacao_lista_paineis(
            dt_ini=inicio,
            dt_fim=fim,
            itens=itens_norm,
        )

        payload = {
            "ok": True,
            "dt_ini": inicio.isoformat(),
            "dt_fim": fim.isoformat(),
            "itens": itens_ocupacao,
            "total_itens": len(itens_ocupacao),
            "cache_key": cache_key,
            "origem": "celery_sqlserver",
            "data_atualizacao": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            cache.set(cache_key, payload, timeout=90)
        except Exception:
            pass

        socketio.emit("paineis:ocupacao:lote", payload, namespace=NAMESPACE_PAINEIS, to=room)
        return payload

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass

        payload = {
            "ok": False,
            "erro": str(exc),
            "itens": [],
            "cache_key": cache_key,
            "origem": "celery_sqlserver",
            "data_atualizacao": datetime.now().isoformat(timespec="seconds"),
        }
        socketio.emit("paineis:ocupacao:erro", payload, namespace=NAMESPACE_PAINEIS, to=room)
        return payload

    finally:
        try:
            db.session.close()
        except Exception:
            pass
