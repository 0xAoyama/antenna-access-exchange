FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PORT=8000
EXPOSE 8000

CMD ["gunicorn", "-w", "2", "-k", "gthread", "--threads", "4", "--timeout", "60", "-b", "0.0.0.0:8000", "app:app"]
