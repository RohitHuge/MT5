# ── Stage 1: build React SPA ────────────────────────────────────────────────
FROM node:22-alpine AS frontend-builder
WORKDIR /frontend
COPY dashboard/frontend/package*.json ./
RUN npm install --include=dev
COPY dashboard/frontend/ ./
RUN npm run build

# ── Stage 2: Python backend ──────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY dashboard/ .
# Replace source frontend with the compiled dist from stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
