FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY dashboard/frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY dashboard/frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY dashboard/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY smart_city_common/ ./smart_city_common/
COPY dashboard/backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist

EXPOSE 8080

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
