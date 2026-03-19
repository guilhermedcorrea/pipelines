import os
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv


CAMINHO_DOTENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(CAMINHO_DOTENV)


def obter_variavel_obrigatoria(nome_variavel: str) -> str:
    valor = os.getenv(nome_variavel)
    if not valor:
        raise RuntimeError(f"Variável obrigatória ausente no .env: {nome_variavel}")
    return valor


SERVER = obter_variavel_obrigatoria("SERVER")
DATABASE = obter_variavel_obrigatoria("DATABASE")
UID = obter_variavel_obrigatoria("UID")
PWD = obter_variavel_obrigatoria("PWD")

SECRET_KEY = obter_variavel_obrigatoria("SECRET_KEY")
RECAPTCHA_PUBLIC_KEY = obter_variavel_obrigatoria("RECAPTCHA_PUBLIC_KEY")
RECAPTCHA_PRIVATE_KEY = obter_variavel_obrigatoria("RECAPTCHA_PRIVATE_KEY")
RATELIMIT_STORAGE_URI = obter_variavel_obrigatoria("RATELIMIT_STORAGE_URI")
CACHE_REDIS_URL = obter_variavel_obrigatoria("CACHE_REDIS_URL")


parametros_odbc = urllib.parse.quote_plus(
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={SERVER},1433;"
    f"DATABASE={DATABASE};"
    f"UID={UID};"
    f"PWD={PWD};"
    "TrustServerCertificate=yes;"
    "Encrypt=no;"
    "Connection Timeout=90;"
    "LoginTimeout=30;"
)

SQLALCHEMY_DATABASE_URI = f"mssql+pyodbc:///?odbc_connect={parametros_odbc}"