FROM python:3.12-slim

WORKDIR /srv

# Dependências primeiro (cache de camada)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY app ./app

# Banco SQLite vive em /data (volume nomeado no compose)
ENV MCP_DB_PATH=/data/agendamentos.db
RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
