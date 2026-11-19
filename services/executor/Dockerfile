FROM python:3.12-slim
WORKDIR /app
COPY services/executor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ ./shared/
COPY services/executor/ .
CMD ["python", "main.py"]
