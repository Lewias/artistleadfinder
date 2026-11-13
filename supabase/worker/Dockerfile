# Telegram bot of the assistant: a small Python process that talks to Telegram and to the
# database. Build context: this folder's Dockerfile, requirements.txt and telegram_bot/.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY telegram_bot ./telegram_bot
# The build context may come from a root-only folder: the bot's user must read its code.
RUN chmod -R a+rX /app && useradd --system --no-create-home bot
USER bot
CMD ["python", "-m", "telegram_bot"]
