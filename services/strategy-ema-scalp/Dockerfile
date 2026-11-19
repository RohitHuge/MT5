FROM python:3.12-slim
WORKDIR /app

COPY services/strategy-ema-scalp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared/ must be on the path — copy to /app/shared so the sys.path.insert works
COPY shared/ /app/shared/
COPY services/strategy-ema-scalp/main.py .

CMD ["python", "main.py"]
