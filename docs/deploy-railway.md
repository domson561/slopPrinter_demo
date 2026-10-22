# Demo na Railway

Jeden serwis z `Dockerfile` (gunicorn, whitenoise, atrapa GH w procesie) + Railway Postgres + jeden
wolumen na media. Konfiguracja tylko w panelu (config as code w `railway.json` Railway wycofuje).

## 1. Usługi

1. Nowy projekt → **Deploy from GitHub repo** (ten repozytorium, gałąź z demo). Railway sam wykryje `Dockerfile`.
2. W projekcie **+ New → Database → PostgreSQL**. Nazwa usługi zostaje `Postgres` (od niej zależą odwołania niżej).
3. Serwis aplikacji → **Settings → Networking → Generate Domain** (adres `*.up.railway.app`).
4. Serwis aplikacji → **+ New → Volume**, mount path: `/data`.

## 2. Zmienne (serwis aplikacji → Variables → Raw Editor)

`DJANGO_SECRET_KEY` wygeneruj lokalnie:
`.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(50))"`

```env
DJANGO_SETTINGS_MODULE=config.settings.demo
DJANGO_SECRET_KEY=<wklej wygenerowany>
DJANGO_ALLOWED_HOSTS=${{RAILWAY_PUBLIC_DOMAIN}}
SITE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
SITE_NAME=drukuj-to.me
ORDER_NUMBER_PREFIX=PA

POSTGRES_HOST=${{Postgres.PGHOST}}
POSTGRES_PORT=${{Postgres.PGPORT}}
POSTGRES_DB=${{Postgres.PGDATABASE}}
POSTGRES_USER=${{Postgres.PGUSER}}
POSTGRES_PASSWORD=${{Postgres.PGPASSWORD}}

MEDIA_ROOT=/data/media
PRIVATE_MEDIA_ROOT=/data/private_media

HUEY_IMMEDIATE=1
WEB_CONCURRENCY=2
```

Bez `REDIS_URL` zadania huey idą w żądaniu (bez osobnego workera). `GH_API_*` nie są potrzebne:
`demo.py` zawsze używa atrapy GH z tokenem demo.

## 3. Deploy (serwis aplikacji → Settings → Deploy)

- **Pre-deploy command:** `python manage.py migrate --noinput`
  (pre-deploy nie ma wolumenu, ale ma sieć prywatną, więc migracje działają, a seed nie).
- **Healthcheck path:** `/healthz/`
- **Start command:** puste (bierze `CMD` z `Dockerfile`).

## 4. Dane demo, jednorazowo

Seed zapisuje obrazki na wolumen, więc musi iść w działającym kontenerze, nie w pre-deploy.
`seed_demo` nie jest idempotentny: każde uruchomienie dokłada ok. 15 zamówień (`--flush` czyści stare).

```bash
railway ssh
python manage.py seed_demo
python manage.py changepassword demo
```

Zmiana hasła jest obowiązkowa: `demo` to superuser (`/admin/`, `/ops/`, `/gh-mock/`), a domyślne
hasło `demo` jest w README.

## Co klient zobaczy

- Sklep: `https://<domena>/`
- Panel operatora: `/ops/` (login `demo`, nowe hasło)
- Atrapa drukarni: `/gh-mock/` (zmiana statusów zleceń ręcznie)
- Maile: tylko w logach serwisu (backend konsolowy), płatność i przewoźnik to atrapy.
