FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# run as a normal user; the app writes recordings, reports and cached audio to ./data
RUN useradd -m app
WORKDIR /home/app/saavdhaan
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
RUN mkdir -p data && chown app:app data
USER app

# Render passes the port in $PORT; 8000 elsewhere
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
