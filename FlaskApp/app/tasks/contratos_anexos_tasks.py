from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(
    name="contratos_anexos.tarefa_processar_upload_anexos_contrato",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def tarefa_processar_upload_anexos_contrato(
    self,
    *,
    arquivos: list[dict],
    id_solicitacao: int | None = None,
    id_fato_controle_contratos: int | None = None,
    id_fato_contrato_d4: int | None = None,
    id_fato_kanban_card: int | None = None,
    tipo_solicitacao: str | None = None,
) -> dict:
    """
    Processa anexos de contratos fora da requisição HTTP.

    Fluxo:
    1. Flask recebe o upload e salva cada arquivo em pasta temporária compartilhada.
    2. Esta task move o arquivo para a pasta final do contrato.
    3. Esta task insere o registro em Integracao.Silver.FatoAnexosContratosEuromidia.
    """
    from app.admin.admin_views import _processar_upload_anexos_contrato_admin

    return _processar_upload_anexos_contrato_admin(
        arquivos=arquivos or [],
        id_solicitacao=id_solicitacao,
        id_fato_controle_contratos=id_fato_controle_contratos,
        id_fato_contrato_d4=id_fato_contrato_d4,
        id_fato_kanban_card=id_fato_kanban_card,
        tipo_solicitacao=tipo_solicitacao,
    )
