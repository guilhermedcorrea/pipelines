from pathlib import Path
from typing import Any

from ..celery_app import celery_app
from ..extensions import db


@celery_app.task(
    bind=True,
    name="checking.processar_upload",
    acks_late=True,
    reject_on_worker_lost=True,
)
def processar_checking_upload(
    self,
    *,
    id_empresa: int,
    id_fato_controle_contratos: int,
    cod_ponto: str,
    cod_face: str,
    data_checking_iso: str,
    caminho_arquivo_temporario: str,
    nome_original_cliente: str | None = None,
    cnpj_digitado: str | None = None,
    observacao: str | None = None,
    id_usuario_criacao: int | None = None,
) -> dict[str, Any]:
    """Eu processo o upload do checking fora da request web."""

    caminho_temp = Path(str(caminho_arquivo_temporario or "").strip())

    self.update_state(
        state="STARTED",
        meta={
            "etapa": "processando_mockup",
            "arquivo_temporario": str(caminho_temp),
            "cod_ponto": str(cod_ponto),
            "cod_face": str(cod_face),
        },
    )

    try:
        from ..euromidia.controle_paineis_views import _processar_upload_checking_por_caminho

        resultado = _processar_upload_checking_por_caminho(
            id_empresa=int(id_empresa),
            id_fato_controle_contratos=int(id_fato_controle_contratos),
            cod_ponto=str(cod_ponto),
            cod_face=str(cod_face),
            data_checking_iso=str(data_checking_iso),
            caminho_arquivo_temporario=str(caminho_temp),
            nome_original_cliente=nome_original_cliente,
            cnpj_digitado=cnpj_digitado,
            observacao=observacao,
            id_usuario_criacao=(
                int(id_usuario_criacao)
                if id_usuario_criacao not in (None, "", 0)
                else None
            ),
        )

        return resultado

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass

        try:
            if str(caminho_temp).strip() and caminho_temp.exists() and caminho_temp.is_file():
                caminho_temp.unlink()
        except Exception:
            pass

        raise exc

    finally:
        try:
            db.session.remove()
        except Exception:
            pass