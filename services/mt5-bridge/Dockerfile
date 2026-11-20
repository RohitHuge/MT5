FROM python:3.11-slim

WORKDIR /app

COPY services/mt5-bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/mt5-bridge/main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
