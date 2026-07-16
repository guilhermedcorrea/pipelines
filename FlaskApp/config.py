import os
import urllib.parse
import pyodbc
from pathlib import Path
from dotenv import load_dotenv


pyodbc.pooling = False


CAMINHO_DOTENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(CAMINHO_DOTENV)



EXTENSOES_PERMITIDAS_CHECKING = {"jpg", "jpeg", "png"}
TAMANHO_MAXIMO_UPLOAD_MB = 300
LARGURA_MAXIMA_IMAGEM = 5000
ALTURA_MAXIMA_IMAGEM = 5000



KANBAN_DEADLOCK_TASK_NAME = "app.kanban.tarefa_retry_movimento_card"
KANBAN_DEADLOCK_QUEUE_NAME = "kanban_retry_rapido"
KANBAN_DEADLOCK_COUNTDOWN = 2
KANBAN_DEADLOCK_EXPIRES = 30





def obter_variavel_obrigatoria(nome_variavel: str) -> str:
    valor = os.getenv(nome_variavel)
    if not valor:
        raise RuntimeError(f"Variável obrigatória ausente no .env: {nome_variavel}")
    return valor


SERVER = obter_variavel_obrigatoria("SERVER")
DATABASE = obter_variavel_obrigatoria("DATABASE")
UID = obter_variavel_obrigatoria("UID")
PWD = obter_variavel_obrigatoria("PWD")
SHEMPO_DATABASE = (os.getenv("SHEMPO_DATABASE", "Shempo") or "Shempo").strip()

SECRET_KEY = obter_variavel_obrigatoria("SECRET_KEY")
RECAPTCHA_PUBLIC_KEY = obter_variavel_obrigatoria("RECAPTCHA_PUBLIC_KEY")
RECAPTCHA_PRIVATE_KEY = obter_variavel_obrigatoria("RECAPTCHA_PRIVATE_KEY")
RATELIMIT_STORAGE_URI = obter_variavel_obrigatoria("RATELIMIT_STORAGE_URI")
CACHE_REDIS_URL = obter_variavel_obrigatoria("CACHE_REDIS_URL")
MENSAGERIA_SOCKET_TOKEN = os.getenv("MENSAGERIA_SOCKET_TOKEN", "")


def criar_uri_sqlserver(nome_banco: str) -> str:
    parametros_odbc = urllib.parse.quote_plus(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={SERVER},1433;"
        f"DATABASE={nome_banco};"
        f"UID={UID};"
        f"PWD={PWD};"
        "TrustServerCertificate=yes;"
        "Encrypt=no;"
        "Connection Timeout=90;"
        "LoginTimeout=30;"
    )
    return f"mssql+pyodbc:///?odbc_connect={parametros_odbc}"


SQLALCHEMY_DATABASE_URI = criar_uri_sqlserver(DATABASE)
SQLALCHEMY_SHEMPO_DATABASE_URI = criar_uri_sqlserver(SHEMPO_DATABASE)

# O banco principal continua sendo o definido por DATABASE. Somente os models
# legados do módulo Shempo usam este bind separado.
SQLALCHEMY_BINDS = {
    "shempo": {
        "url": SQLALCHEMY_SHEMPO_DATABASE_URI,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 30,
        "fast_executemany": True,
        "connect_args": {
            "timeout": 60,
        },
    }
}


CHECKING_BASE_URL_PUBLICA = "http://189.45.251.100:5000"
PASTA_RELATORIOS_OCUPACAO = os.getenv(
    "PASTA_RELATORIOS_OCUPACAO",
    "/home/guilherme_correa/PythonJobs/pipelines/FlaskApp/relatorios/ocupacao",
)
