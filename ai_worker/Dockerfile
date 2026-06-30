FROM python:3.12-slim

WORKDIR /app

COPY ai_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY smart_city_common/ ./smart_city_common/
COPY ai_worker/worker.py ./

CMD ["python", "-u", "worker.py"]