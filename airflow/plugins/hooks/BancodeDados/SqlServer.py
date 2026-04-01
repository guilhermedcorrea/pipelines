import urllib.parse
from typing import Any

from airflow.sdk.bases.hook import BaseHook
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Result


class HookSqlServer:
   

    def __init__(
        self,
        conn_id: str = "mssql_integracao",
        driver_odbc: str = "ODBC Driver 18 for SQL Server",
        trust_server_certificate: str = "yes",
        timeout_conexao: int = 30,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_recycle: int = 1800,
    ) -> None:
        """Inicializo o hook com parâmetros padrão da conexão."""
        self.conn_id = conn_id
        self.driver_odbc = driver_odbc
        self.trust_server_certificate = trust_server_certificate
        self.timeout_conexao = timeout_conexao
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.pool_recycle = pool_recycle

    def obter_connection_airflow(self):
        """Busco a Connection cadastrada no Airflow pelo conn_id."""
        return BaseHook.get_connection(self.conn_id)

    def montar_string_odbc(self) -> str:
        """Monto a string ODBC a partir da Connection do Airflow."""
        conexao = self.obter_connection_airflow()

        servidor = conexao.host
        banco = conexao.schema
        usuario = conexao.login
        senha = conexao.password
        porta = conexao.port or 1433

        string_odbc = (
            f"DRIVER={{{self.driver_odbc}}};"
            f"SERVER={servidor},{porta};"
            f"DATABASE={banco};"
            f"UID={usuario};"
            f"PWD={senha};"
            f"TrustServerCertificate={self.trust_server_certificate};"
            f"Connection Timeout={self.timeout_conexao};"
        )

        return string_odbc

    def obter_url_sqlalchemy(self) -> str:
        """Converto a string ODBC em URL compatível com SQLAlchemy."""
        string_odbc = self.montar_string_odbc()
        params = urllib.parse.quote_plus(string_odbc)
        return f"mssql+pyodbc:///?odbc_connect={params}"

    def obter_engine(self) -> Engine:
        """Crio e retorno a engine SQLAlchemy."""
        url = self.obter_url_sqlalchemy()

        engine = create_engine(
            url,
            fast_executemany=True,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_pre_ping=True,
            pool_recycle=self.pool_recycle,
            future=True,
        )

        return engine

    def obter_conexao_dbapi(self):
        """Abro uma conexão DBAPI a partir da engine."""
        engine = self.obter_engine()
        return engine.raw_connection()

    def testar_conexao(self) -> str:
        """Testo a conexão com uma query simples e retorno mensagem de sucesso."""
        engine = self.obter_engine()

        with engine.connect() as conexao:
            resultado = conexao.execute(text("SELECT 1 AS teste"))
            valor = resultado.scalar()

        if valor == 1:
            return "Conexão com SQL Server realizada com sucesso."

        raise RuntimeError("Falha ao validar a conexão com SQL Server.")

    def executar_select(
        self,
        sql: str,
        parametros: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Executo uma consulta SELECT e retorno lista de dicionários."""
        engine = self.obter_engine()

        with engine.connect() as conexao:
            resultado: Result = conexao.execute(text(sql), parametros or {})
            linhas = resultado.mappings().all()

        return [dict(linha) for linha in linhas]

    def executar_comando(
        self,
        sql: str,
        parametros: dict[str, Any] | None = None,
    ) -> None:
        """Executo comando SQL sem retorno, como INSERT, UPDATE, DELETE ou EXEC."""
        engine = self.obter_engine()

        with engine.begin() as conexao:
            conexao.execute(text(sql), parametros or {})