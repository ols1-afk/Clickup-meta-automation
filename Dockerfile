FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PORT=8000
CMD gunicorn -w 1 --threads 4 --timeout 120 -b 0.0.0.0:${PORT} app.server:app
