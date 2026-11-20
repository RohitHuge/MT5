FROM python:3.12-slim
WORKDIR /app
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY dashboard/ .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
