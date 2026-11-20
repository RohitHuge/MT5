FROM python:3.12-slim
WORKDIR /app

COPY services/strategy-ob-fvg/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared/ must be on the path — copied to /app/shared so sys.path.insert works
COPY shared/ /app/shared/

# backtesting source — required for order_blocks, fvg, ob_fvg modules.
# PYTHONPATH set below so "from mt5_liquidity.strategy..." works.
# NOTE: none of the files in this import chain import MetaTrader5.
COPY backtesting/src/ /app/backtesting_src/

COPY services/strategy-ob-fvg/main.py .

ENV PYTHONPATH=/app/backtesting_src

CMD ["python", "main.py"]
