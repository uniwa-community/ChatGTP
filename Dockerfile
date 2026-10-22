FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py system_prompt.txt ./

# .env is dockerignored; mount it at runtime: -v ./secrets/.env:/app/.env:ro

USER nobody

CMD ["python", "bot.py"]
