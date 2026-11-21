FROM python:3.12-slim
WORKDIR /app

COPY services/strategy-oco/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY services/strategy-oco/main.py .

CMD ["python", "main.py"]
