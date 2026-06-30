FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglib2.0-0 libxcb1 libxcb-render0 libxcb-shape0 libxcb-xfixes0 \
    && rm -rf /var/lib/apt/lists/*

COPY perception_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY smart_city_common/ ./smart_city_common/
COPY perception_worker/worker.py .

CMD ["python", "worker.py"]
