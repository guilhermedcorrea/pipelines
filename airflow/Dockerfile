FROM apache/airflow:3.1.8-python3.12

USER root

ARG DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    . /etc/os-release; \
    if [ "${VERSION_CODENAME}" != "bookworm" ]; then \
        echo "ERRO: a imagem base do Airflow esta em Debian ${VERSION_ID} (${VERSION_CODENAME}), mas este Dockerfile usa o repositorio Microsoft Debian 12 / bookworm."; \
        echo "Use uma imagem base bookworm ou ajuste o repositorio Microsoft para a mesma versao da base."; \
        exit 1; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        apt-transport-https \
        ca-certificates \
        unixodbc \
        unixodbc-dev \
        build-essential \
        rclone; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg; \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list; \
    apt-get update; \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

COPY --chown=airflow:root requirements.txt /requirements.txt

USER airflow

ENV AIRFLOW_VERSION=3.1.8
ENV PYTHON_VERSION=3.12
ENV CONSTRAINT_URL=https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir "apache-airflow-providers-fab" --constraint "${CONSTRAINT_URL}" \
    && python -m pip install --no-cache-dir -r /requirements.txt --constraint "${CONSTRAINT_URL}"
