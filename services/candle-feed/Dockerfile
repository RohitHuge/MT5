FROM python:3.12-slim
WORKDIR /app
COPY services/candle-feed/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ ./shared/
COPY services/candle-feed/ .
CMD ["python", "main.py"]
