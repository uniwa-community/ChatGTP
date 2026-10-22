FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py system_prompt.txt channels.txt ./

USER nobody

CMD ["python", "bot.py"]
