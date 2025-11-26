FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .

ENV PORT=5000

CMD exec gunicorn --bind 0.0.0.0:$PORT app:app --workers 1
