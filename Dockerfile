FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=false \
    DATABASE_PATH=/app/data/db.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data && python manage.py collectstatic --noinput -v0

EXPOSE 8000

# Apply migrations on start, then serve with gunicorn.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn voice_eval.wsgi:application --bind 0.0.0.0:8000 --workers 2"]
