from pathlib import Path
from typing import Any

from ..celery_app import celery_app
from ..extensions import db


@celery_app.task(
    bind=True,
    name="checkin.processar_upload",
    acks_late=True,
    reject_on_worker_lost=True,
)
def processar_checkin_upload(
    self,
    *,
    id_empresa: int,
    id_fato_controle_contratos: int,
    cod_ponto: str,
    cod_face: str,
    data_checkin_iso: str,
    caminho_arquivo_temporario: str,
    nome_original_cliente: str | None = None,
    cnpj_digitado: str | None = None,
    observacao: str | None = None,
    id_usuario_criacao: int | None = None,
    id_empresa_destinatario: int | None = None,
    id_fato_controle_contratos_item: int | None = None,
    id_fato_contrato_destinatario_externo: int | None = None,
    id_dim_tipo_midia: int | None = None,
    nome_tipo_midia: str | None = None,
    mimetype_arquivo: str | None = None,
    extensao_arquivo: str | None = None,
) -> dict[str, Any]:
    """Eu processo o upload do checkin fora da request web."""

    caminho_temp = Path(str(caminho_arquivo_temporario or "").strip())

    self.update_state(
        state="STARTED",
        meta={
            "etapa": "processando_mockup",
            "arquivo_temporario": str(caminho_temp),
            "cod_ponto": str(cod_ponto),
            "cod_face": str(cod_face),
            "id_empresa": (
                int(id_empresa)
                if id_empresa not in (None, "", 0)
                else None
            ),
            "id_empresa_destinatario": (
                int(id_empresa_destinatario)
                if id_empresa_destinatario not in (None, "", 0)
                else None
            ),
            "id_fato_controle_contratos": (
                int(id_fato_controle_contratos)
                if id_fato_controle_contratos not in (None, "", 0)
                else None
            ),
            "id_fato_controle_contratos_item": (
                int(id_fato_controle_contratos_item)
                if id_fato_controle_contratos_item not in (None, "", 0)
                else None
            ),
            "id_fato_contrato_destinatario_externo": (
                int(id_fato_contrato_destinatario_externo)
                if id_fato_contrato_destinatario_externo not in (None, "", 0)
                else None
            ),
            "id_dim_tipo_midia": (
                int(id_dim_tipo_midia)
                if id_dim_tipo_midia not in (None, "", 0)
                else None
            ),
            "nome_tipo_midia": (nome_tipo_midia or "").strip() or None,
            "mimetype_arquivo": (mimetype_arquivo or "").strip() or None,
            "extensao_arquivo": (extensao_arquivo or "").strip() or None,
        },
    )

    try:
        from ..euromidia.controle_paineis_views import _processar_upload_checkin_por_caminho

        resultado = _processar_upload_checkin_por_caminho(
            id_empresa=int(id_empresa),
            id_fato_controle_contratos=int(id_fato_controle_contratos),
            cod_ponto=str(cod_ponto),
            cod_face=str(cod_face),
            data_checkin_iso=str(data_checkin_iso),
            caminho_arquivo_temporario=str(caminho_temp),
            nome_original_cliente=(
                str(nome_original_cliente).strip()
                if nome_original_cliente not in (None, "")
                else None
            ),
            cnpj_digitado=(
                str(cnpj_digitado).strip()
                if cnpj_digitado not in (None, "")
                else None
            ),
            observacao=(
                str(observacao).strip()
                if observacao not in (None, "")
                else None
            ),
            id_usuario_criacao=(
                int(id_usuario_criacao)
                if id_usuario_criacao not in (None, "", 0)
                else None
            ),
            id_empresa_destinatario=(
                int(id_empresa_destinatario)
                if id_empresa_destinatario not in (None, "", 0)
                else None
            ),
            id_fato_controle_contratos_item=(
                int(id_fato_controle_contratos_item)
                if id_fato_controle_contratos_item not in (None, "", 0)
                else None
            ),
            id_fato_contrato_destinatario_externo=(
                int(id_fato_contrato_destinatario_externo)
                if id_fato_contrato_destinatario_externo not in (None, "", 0)
                else None
            ),
            id_dim_tipo_midia=(
                int(id_dim_tipo_midia)
                if id_dim_tipo_midia not in (None, "", 0)
                else None
            ),
            nome_tipo_midia=(
                str(nome_tipo_midia).strip()
                if nome_tipo_midia not in (None, "")
                else None
            ),
            mimetype_arquivo=(
                str(mimetype_arquivo).strip().lower()
                if mimetype_arquivo not in (None, "")
                else None
            ),
            extensao_arquivo=(
                str(extensao_arquivo).strip().lower()
                if extensao_arquivo not in (None, "")
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