from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import MultipleCronTriggerTimetable


"""Eu aceito as extensões comuns de imagem."""
EXTENSOES_VALIDAS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

"""Eu defino o timezone oficial da DAG."""
TIMEZONE_LOCAL = "America/Sao_Paulo"


def obter_caminho_static_em_runtime() -> Path:
    """Eu localizo a pasta static do Flask somente em runtime.

    Regras:
    1. Eu priorizo a variável de ambiente CAMINHO_STATIC_FLASK.
    2. Se ela não existir, eu tento caminhos internos comuns do container.
    3. Eu falho com mensagem explícita apenas durante a execução da task.
    """
    caminho_configurado = os.getenv("CAMINHO_STATIC_FLASK", "").strip()

    candidatos: list[Path] = []

    if caminho_configurado:
        candidatos.append(Path(caminho_configurado))

    candidatos.extend(
        [
            Path("/opt/airflow/FlaskApp/app/static"),
            Path("/opt/airflow/src/FlaskApp/app/static"),
        ]
    )

    caminhos_unicos: list[Path] = []
    vistos: set[str] = set()

    for caminho in candidatos:
        chave = str(caminho)
        if chave in vistos:
            continue
        vistos.add(chave)
        caminhos_unicos.append(caminho)

    for caminho in caminhos_unicos:
        if caminho.exists() and caminho.is_dir():
            return caminho

    caminhos_testados = "\n".join([f" - {str(caminho)}" for caminho in caminhos_unicos])

    raise RuntimeError(
        "Não foi possível localizar a pasta static do Flask dentro do container do Airflow.\n"
        "Verifique a variável CAMINHO_STATIC_FLASK e o volume do docker-compose.\n"
        f"Caminhos testados:\n{caminhos_testados}"
    )


def montar_pastas_imagens(caminho_static: Path) -> list[Path]:
    """Eu monto as possíveis pastas de imagens a partir do static encontrado."""
    return [
        caminho_static / "imagens" / "paineis",
        caminho_static / "imagens" / "painéis",
    ]


def normalizar_codface(valor: str | None) -> str:
    """Eu padronizo o CodFace para comparação segura."""
    return (valor or "").strip().upper()


def buscar_faces_paineis(sessao) -> list[dict]:
    """Eu leio as faces válidas da DimFacesPaineis."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT
            IDDimFacesPaineis,
            CodPonto,
            CodFace
        FROM [Integracao].[Silver].[DimFacesPaineis]
        WHERE CodFace IS NOT NULL
          AND LTRIM(RTRIM(CodFace)) <> ''
        ORDER BY CodFace
        """
    )

    resultado = sessao.execute(sql).mappings().all()
    return [dict(linha) for linha in resultado]


def listar_arquivos_imagem(pastas_imagens: list[Path]) -> list[Path]:
    """Eu varro as pastas e devolvo todos os arquivos de imagem encontrados."""
    arquivos: list[Path] = []
    caminhos_ja_vistos: set[str] = set()

    for pasta in pastas_imagens:
        if not pasta.exists():
            print(f"PASTA NÃO ENCONTRADA: {pasta}")
            continue

        print(f"PASTA ENCONTRADA: {pasta}")

        for arquivo in pasta.rglob("*"):
            if not arquivo.is_file():
                continue

            if arquivo.suffix.lower() not in EXTENSOES_VALIDAS:
                continue

            caminho_resolvido = str(arquivo.resolve())

            if caminho_resolvido in caminhos_ja_vistos:
                continue

            caminhos_ja_vistos.add(caminho_resolvido)
            arquivos.append(arquivo)

    return arquivos


def montar_url_relativa_static(caminho_arquivo: Path, caminho_static: Path) -> str:
    """Eu converto o caminho físico em URL relativa do Flask."""
    caminho_relativo = caminho_arquivo.resolve().relative_to(caminho_static.resolve())
    return "/static/" + str(caminho_relativo).replace("\\", "/")


def extrair_codface_base(nome_sem_extensao: str) -> str:
    """Eu extraio o CodFace base do nome do arquivo.

    Exemplos:
    1235AD -> 1235AD
    1235AD_1 -> 1235AD
    1235AD_2 -> 1235AD
    """
    nome = normalizar_codface(nome_sem_extensao)

    correspondencia = re.match(r"^(.+?)_(\d+)$", nome)
    if correspondencia:
        return correspondencia.group(1).strip()

    return nome


def extrair_indice_nome_arquivo(nome_sem_extensao: str) -> int:
    """Eu pego o índice do nome para ordenar as imagens da face.

    Regras:
    1235AD -> índice 0
    1235AD_1 -> índice 1
    1235AD_2 -> índice 2
    """
    nome = normalizar_codface(nome_sem_extensao)

    correspondencia = re.match(r"^(.+?)_(\d+)$", nome)
    if correspondencia:
        return int(correspondencia.group(2))

    return 0


def ordenar_arquivos_da_face(arquivos_face: list[Path]) -> list[Path]:
    """Eu ordeno os arquivos da face na sequência lógica."""
    return sorted(
        arquivos_face,
        key=lambda arquivo: (
            extrair_indice_nome_arquivo(arquivo.stem),
            arquivo.name.upper(),
        ),
    )


def agrupar_arquivos_por_codface(
    arquivos: list[Path],
    codfaces_validos: set[str],
) -> tuple[dict[str, list[Path]], list[Path]]:
    """Eu agrupo os arquivos por CodFace e separo os que não bateram com nenhuma face."""
    arquivos_por_codface: dict[str, list[Path]] = {}
    arquivos_sem_correspondencia: list[Path] = []

    for arquivo in arquivos:
        codface_base = extrair_codface_base(arquivo.stem)

        if codface_base not in codfaces_validos:
            arquivos_sem_correspondencia.append(arquivo)
            continue

        if codface_base not in arquivos_por_codface:
            arquivos_por_codface[codface_base] = []

        arquivos_por_codface[codface_base].append(arquivo)

    for codface in list(arquivos_por_codface.keys()):
        arquivos_por_codface[codface] = ordenar_arquivos_da_face(arquivos_por_codface[codface])

    return arquivos_por_codface, arquivos_sem_correspondencia


def buscar_registros_existentes(sessao) -> dict[tuple[str, int], dict]:
    """Eu leio a DimImagemPainel para decidir entre insert e update.

    A chave lógica do upsert será:
    (CodFace, NumeroImagem)
    """
    from sqlalchemy import text

    sql = text(
        """
        SELECT
            IDDimImagemPainel,
            IDDimFacesPaineis,
            UrlImagem,
            NumeroImagem,
            DataAtualizacao,
            BitAtivo,
            CodFace,
            CodPonto
        FROM [Integracao].[Silver].[DimImagemPainel]
        WHERE CodFace IS NOT NULL
          AND NumeroImagem IS NOT NULL
        """
    )

    resultado = sessao.execute(sql).mappings().all()

    registros: dict[tuple[str, int], dict] = {}

    for linha in resultado:
        registro = dict(linha)
        chave = (
            normalizar_codface(str(registro["CodFace"])),
            int(registro["NumeroImagem"]),
        )
        registros[chave] = registro

    return registros


def atualizar_registro_existente(
    sessao,
    id_dim_imagem_painel: int,
    id_dim_faces_paineis: int,
    url_imagem: str,
    data_atualizacao: datetime,
    codface: str,
    cod_ponto,
) -> None:
    """Eu atualizo um registro existente na DimImagemPainel."""
    from sqlalchemy import text

    sql = text(
        """
        UPDATE [Integracao].[Silver].[DimImagemPainel]
           SET IDDimFacesPaineis = :id_dim_faces_paineis,
               UrlImagem = :url_imagem,
               DataAtualizacao = :data_atualizacao,
               BitAtivo = :bit_ativo,
               CodFace = :codface,
               CodPonto = :cod_ponto
         WHERE IDDimImagemPainel = :id_dim_imagem_painel
        """
    )

    sessao.execute(
        sql,
        {
            "id_dim_faces_paineis": id_dim_faces_paineis,
            "url_imagem": url_imagem,
            "data_atualizacao": data_atualizacao,
            "bit_ativo": 1,
            "codface": codface,
            "cod_ponto": cod_ponto,
            "id_dim_imagem_painel": id_dim_imagem_painel,
        },
    )


def inserir_registro_novo(
    sessao,
    id_dim_faces_paineis: int,
    url_imagem: str,
    numero_imagem: int,
    data_atualizacao: datetime,
    codface: str,
    cod_ponto,
) -> None:
    """Eu insiro um novo registro na DimImagemPainel."""
    from sqlalchemy import text

    sql = text(
        """
        INSERT INTO [Integracao].[Silver].[DimImagemPainel]
        (
            IDDimFacesPaineis,
            UrlImagem,
            NumeroImagem,
            DataAtualizacao,
            BitAtivo,
            CodFace,
            CodPonto
        )
        VALUES
        (
            :id_dim_faces_paineis,
            :url_imagem,
            :numero_imagem,
            :data_atualizacao,
            :bit_ativo,
            :codface,
            :cod_ponto
        )
        """
    )

    sessao.execute(
        sql,
        {
            "id_dim_faces_paineis": id_dim_faces_paineis,
            "url_imagem": url_imagem,
            "numero_imagem": numero_imagem,
            "data_atualizacao": data_atualizacao,
            "bit_ativo": 1,
            "codface": codface,
            "cod_ponto": cod_ponto,
        },
    )


def executar_upsert_dim_imagem_painel() -> dict:
    """Eu leio as faces, procuro imagens na pasta e faço upsert na DimImagemPainel."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from hooks.BancodeDados.SqlServer import HookSqlServer

    hook_sql_server = HookSqlServer(conn_id="mssql_integracao")
    conexao_airflow = hook_sql_server.obter_connection_airflow()

    caminho_static = obter_caminho_static_em_runtime()
    pastas_imagens = montar_pastas_imagens(caminho_static)

    print("=" * 120)
    print("INÍCIO DO PROCESSO DE UPSERT DA [Integracao].[Silver].[DimImagemPainel]")
    print("=" * 120)
    print("CONFIGURAÇÃO DE CAMINHO:")
    print(f"CAMINHO_STATIC......................: {caminho_static}")
    print("PASTAS DE IMAGEM:")
    for pasta in pastas_imagens:
        print(f" - {pasta}")

    print("\nCONEXÃO LIDA DO AIRFLOW:")
    print(f"conn_id.............................: {hook_sql_server.conn_id}")
    print(f"host................................: {conexao_airflow.host}")
    print(f"porta...............................: {conexao_airflow.port}")
    print(f"banco (schema)......................: {conexao_airflow.schema}")
    print(f"usuario.............................: {conexao_airflow.login}")

    engine = hook_sql_server.obter_engine()
    fabrica_sessao = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sessao = fabrica_sessao()

    try:
        identificacao_banco = sessao.execute(
            text("SELECT @@SERVERNAME AS ServidorSql, DB_NAME() AS BancoAtual")
        ).mappings().one()

        print("\nIDENTIFICAÇÃO REAL DO BANCO:")
        print(f"@@SERVERNAME........................: {identificacao_banco['ServidorSql']}")
        print(f"DB_NAME()...........................: {identificacao_banco['BancoAtual']}")

        faces = buscar_faces_paineis(sessao)
        arquivos = listar_arquivos_imagem(pastas_imagens)
        registros_existentes = buscar_registros_existentes(sessao)

        print("\nRESUMO INICIAL:")
        print(f"Total de faces lidas................: {len(faces)}")
        print(f"Total de arquivos encontrados.......: {len(arquivos)}")
        print(f"Total registros existentes..........: {len(registros_existentes)}")

        if not arquivos:
            raise RuntimeError(
                "Nenhum arquivo de imagem válido foi encontrado nas pastas configuradas. "
                "Verifique o volume Docker e o conteúdo do static."
            )

        face_por_codface: dict[str, dict] = {}
        for face in faces:
            codface = normalizar_codface(str(face["CodFace"]))
            face_por_codface[codface] = face

        codfaces_validos = set(face_por_codface.keys())

        arquivos_por_codface, arquivos_sem_correspondencia = agrupar_arquivos_por_codface(
            arquivos=arquivos,
            codfaces_validos=codfaces_validos,
        )

        total_inserts = 0
        total_updates = 0
        total_updates_sem_alteracao = 0
        total_faces_sem_imagem = 0
        data_execucao = datetime.now()

        for codface in sorted(face_por_codface.keys()):
            face = face_por_codface[codface]
            imagens_da_face = arquivos_por_codface.get(codface, [])

            if not imagens_da_face:
                total_faces_sem_imagem += 1
                continue

            id_dim_faces_paineis = int(face["IDDimFacesPaineis"])
            cod_ponto = face["CodPonto"]

            for posicao, arquivo in enumerate(imagens_da_face, start=1):
                numero_imagem = posicao
                url_imagem = montar_url_relativa_static(arquivo, caminho_static)

                chave = (codface, numero_imagem)
                registro_existente = registros_existentes.get(chave)

                if registro_existente is None:
                    inserir_registro_novo(
                        sessao=sessao,
                        id_dim_faces_paineis=id_dim_faces_paineis,
                        url_imagem=url_imagem,
                        numero_imagem=numero_imagem,
                        data_atualizacao=data_execucao,
                        codface=codface,
                        cod_ponto=cod_ponto,
                    )
                    total_inserts += 1
                    print(
                        f"INSERT -> CodFace={codface} | NumeroImagem={numero_imagem} | UrlImagem={url_imagem}"
                    )
                    continue

                precisa_atualizar = False

                if int(registro_existente["IDDimFacesPaineis"]) != id_dim_faces_paineis:
                    precisa_atualizar = True

                if str(registro_existente["UrlImagem"]) != url_imagem:
                    precisa_atualizar = True

                if int(registro_existente["BitAtivo"]) != 1:
                    precisa_atualizar = True

                if normalizar_codface(str(registro_existente["CodFace"])) != codface:
                    precisa_atualizar = True

                valor_cod_ponto_existente = registro_existente["CodPonto"]
                if str(valor_cod_ponto_existente) != str(cod_ponto):
                    precisa_atualizar = True

                if precisa_atualizar:
                    atualizar_registro_existente(
                        sessao=sessao,
                        id_dim_imagem_painel=int(registro_existente["IDDimImagemPainel"]),
                        id_dim_faces_paineis=id_dim_faces_paineis,
                        url_imagem=url_imagem,
                        data_atualizacao=data_execucao,
                        codface=codface,
                        cod_ponto=cod_ponto,
                    )
                    total_updates += 1
                    print(
                        f"UPDATE -> CodFace={codface} | NumeroImagem={numero_imagem} | UrlImagem={url_imagem}"
                    )
                else:
                    total_updates_sem_alteracao += 1

        sessao.commit()

        resumo = {
            "total_faces_lidas": len(faces),
            "total_arquivos_encontrados": len(arquivos),
            "total_inserts": total_inserts,
            "total_updates": total_updates,
            "total_ja_existentes_sem_alteracao": total_updates_sem_alteracao,
            "total_faces_sem_imagem": total_faces_sem_imagem,
            "total_arquivos_sem_correspondencia": len(arquivos_sem_correspondencia),
        }

        print("\nRESUMO FINAL:")
        print(f"Total de faces lidas.........................: {resumo['total_faces_lidas']}")
        print(f"Total de arquivos encontrados................: {resumo['total_arquivos_encontrados']}")
        print(f"Total de inserts.............................: {resumo['total_inserts']}")
        print(f"Total de updates.............................: {resumo['total_updates']}")
        print(
            f"Total já existentes sem alteração............: "
            f"{resumo['total_ja_existentes_sem_alteracao']}"
        )
        print(f"Total de faces sem imagem....................: {resumo['total_faces_sem_imagem']}")
        print(
            f"Total de arquivos sem correspondência........: "
            f"{resumo['total_arquivos_sem_correspondencia']}"
        )

        if arquivos_sem_correspondencia:
            print("\nARQUIVOS SEM CORRESPONDÊNCIA COM CODFACE DA TABELA:")
            for arquivo in arquivos_sem_correspondencia[:200]:
                print(f" - {arquivo}")

            if len(arquivos_sem_correspondencia) > 200:
                print(f" ... e mais {len(arquivos_sem_correspondencia) - 200} arquivos")

        if total_inserts == 0 and total_updates == 0:
            print("\nATENÇÃO:")
            print("Nenhum insert ou update foi executado.")
            print("Possíveis causas:")
            print("1) os dados já estavam idênticos na tabela;")
            print("2) os CodFace dos arquivos não casaram com a DimFacesPaineis;")
            print("3) a connection do Airflow aponta para outro banco/servidor.")

        print("\nPROCESSO FINALIZADO COM SUCESSO.")
        print("=" * 120)

        return resumo

    except Exception as erro:
        sessao.rollback()
        print("\nERRO AO EXECUTAR O UPSERT:")
        print(str(erro))
        raise

    finally:
        sessao.close()
        engine.dispose()


with DAG(
    dag_id="etl_midia_upsert_dim_imagem_painel",
    description=(
        "ETL diário responsável por ler imagens dos painéis a partir do static do Flask "
        "montado no container do Airflow, extrair CodFace do nome do arquivo, ordenar as "
        "imagens por sequência lógica e executar upsert na tabela "
        "[Integracao].[Silver].[DimImagemPainel] usando o HookSqlServer."
    ),
    schedule=MultipleCronTriggerTimetable(
        "0 10 * * *",
        "30 18 * * *",
        timezone=TIMEZONE_LOCAL,
    ),
    start_date=pendulum.datetime(2026, 3, 31, tz=TIMEZONE_LOCAL),
    catchup=False,
    max_active_runs=1,
    tags=["ETL", "Midia", "Imagens", "Paineis"],
    doc_md="""
# ETL - Upsert de imagens dos painéis na DimImagemPainel

## Objetivo
Esta DAG sincroniza as imagens físicas dos painéis com a tabela:

`[Integracao].[Silver].[DimImagemPainel]`

## Origem dos arquivos
A DAG lê o diretório definido em `CAMINHO_STATIC_FLASK`.

Exemplo recomendado dentro do container:

`/opt/airflow/FlaskApp/app/static`

## O que ela faz
1. Localiza o static do Flask em runtime
2. Procura imagens em:
   - `imagens/paineis`
   - `imagens/painéis`
3. Busca as faces válidas em `[Integracao].[Silver].[DimFacesPaineis]`
4. Extrai o `CodFace` base do nome do arquivo
5. Gera a URL relativa `/static/...`
6. Faz insert quando não existe
7. Faz update quando já existe mas está diferente
8. Gera log detalhado de diagnóstico

## Regra de nome dos arquivos
- `1235AD.png`
- `1235AD_1.png`
- `1235AD_2.png`

## Agendamento
- 10:00
- 18:30

Timezone:
- `America/Sao_Paulo`
""",
) as dag:

    @task(
        task_id="executar_upsert_dim_imagem_painel",
        retries=1,
    )
    def tarefa_executar_upsert_dim_imagem_painel() -> dict:
        """Eu executo o processo de leitura das imagens e sincronização da DimImagemPainel."""
        return executar_upsert_dim_imagem_painel()

    tarefa_executar_upsert_dim_imagem_painel()