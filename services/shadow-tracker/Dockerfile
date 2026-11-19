FROM python:3.12-slim
WORKDIR /app

# Install dependencies first (cached layer)
COPY services/shadow-tracker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared contracts then service code
COPY shared/ ./shared/
COPY services/shadow-tracker/main.py .

# /app is the working directory so "from shared.models import ..." resolves
ENV PYTHONPATH=/app

CMD ["python", "main.py"]
