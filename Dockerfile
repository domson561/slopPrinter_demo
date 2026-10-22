FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Czcionka z polskimi znakami do etykiet PDF (apps/orders/shipping.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pliki statyczne w obrazie, wydaje je whitenoise (demo.py). Zmienne z panelu Railway nie są
# widoczne przy buildzie, więc te wymagane przez demo.py dostają wartości tylko na czas tej komendy.
RUN DJANGO_SECRET_KEY=collectstatic DJANGO_ALLOWED_HOSTS=localhost SITE_URL=https://localhost \
    python manage.py collectstatic --noinput --settings=config.settings.demo

EXPOSE 8000
# Port z PORT (Railway), lokalnie 8000. Ustawienia z DJANGO_SETTINGS_MODULE (domyślnie wsgi bierze
# production, demo: config.settings.demo). Timeout z zapasem: w demo zadania huey idą w żądaniu.
# docker-compose nadpisuje tę komendę własną (runserver).
CMD ["sh", "-c", "exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --access-logfile -"]
