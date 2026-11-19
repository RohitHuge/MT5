FROM python:3.12-slim
WORKDIR /app

COPY services/strategy-liq-sweep/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared/ contracts
COPY shared/ /app/shared/

# Backtesting strategy source — added to PYTHONPATH so relative imports work.
# The strategy layer has no MetaTrader5 imports; only mt5_adapter.py does,
# and nothing in the strategy pipeline imports it.
COPY backtesting/src/ /app/backtesting_src/
ENV PYTHONPATH=/app/backtesting_src

COPY services/strategy-liq-sweep/main.py .

CMD ["python", "main.py"]
