FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md corpora.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "app.main"]
CMD ["run-bot", "--consent-file", "/app/data/consent.txt"]
