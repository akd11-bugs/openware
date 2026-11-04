FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-serving.txt .
RUN pip install --no-cache-dir -r requirements-serving.txt
COPY agentshield agentshield
RUN useradd --system app && mkdir /audit && chown app /audit
USER app
EXPOSE 8000
# One worker only: feature and result state is in memory.
CMD ["uvicorn", "agentshield.serving.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
