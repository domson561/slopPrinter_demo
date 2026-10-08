# NAZWA_ROBOCZA - demo serwisu druku obrazków AI

Demo zbudowane wg `docs/slop_printer_seed_3.md`. Klient wgrywa obrazek, system ocenia jakość
i przygotowuje plik, klient wybiera format, płaci (symulacja), operator zatwierdza,
symulowana drukarnia drukuje i nadaje. Wszystkie integracje zewnętrzne są mockami
za wymiennymi interfejsami i mają etykietę "symulacja" w UI.

## Szybki start (lokalnie, SQLite, bez workera)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; na Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

- Klient: http://localhost:8000/
- Panel operatora: http://localhost:8000/ops/ (login `demo` / `demo`)
- Mock drukarni: http://localhost:8000/mock-printer/
- Admin (awaryjnie): http://localhost:8000/admin/

Domyślnie `HUEY_IMMEDIATE=1`: pipeline pliku (mock upscale z opóźnieniem 3-8 s) wykonuje się
synchronicznie w requeście płatności. E-maile lądują w konsoli serwera.

## Docker (Postgres + Redis + worker huey)

```bash
docker compose up --build
```

`web` robi `migrate`, `seed_demo` i startuje serwer na :8000. `worker` przetwarza zadania w tle.

## Testy

```bash
pytest
```

Pokrycie wg §12 seeda: tabela przejść stanów, DPI i werdykty, pipeline z mockami,
idempotencja `submit` i callbacku płatności, webhook z błędnym podpisem, pełna ścieżka klienta.

## Struktura

| Aplikacja       | Zawartość |
|-----------------|-----------|
| `apps/catalog`  | formaty, papiery, dostawa, `ShopSettings` (progi DPI, auto-zatwierdzanie, auto-symulacja), cennik |
| `apps/assets`   | upload i walidacja, `AnalysisService` (Pillow), `MockContentSafety`, `MockUpscale`, `CropService`, `PrepressService` (PDF ze spadami, reportlab), `MockupService`, `run_pipeline`, komenda `cleanup_assets` |
| `apps/orders`   | `Order`/`OrderItem`/`OrderStatusEvent`/`Payment`/`Shipment`/`Complaint`, maszyna stanów (`services.transition`, `force_transition`), `MockPaymentProvider`, powiadomienia, zadanie `process_order_task` |
| `apps/printing` | `PrintJob`, `MockPrintProvider` (+ puste `HotFolder`/`Email`/`Http`), spec JSON, submit z retry, webhook HMAC `POST /api/printer/events/`, pulpit mock drukarni `/mock-printer/` |
| `apps/ops`      | panel operatora O1-O5 (`is_staff`) |
| `apps/web`      | widoki klienta S1-S8, kadrowanie i cena na żywo (vanilla JS) |
| `apps/demo`     | `seed_demo`, `make_demo_images` (obrazki rysowane Pillow) |

Logika biznesowa jest w `services.py` każdej aplikacji; widoki są cienkie. Wygląd: `static/css/tokens.css`
(zmienne) + `static/css/base.css` (wireframe). Bez bibliotek UI.

## Konfiguracja

Ustawienia w `config/settings/`: `base.py` (wspólne), `development.py` (domyślne dla `manage.py`,
testów i Dockera) i `production.py` (domyślne dla `wsgi`/`asgi`). Produkcja nie startuje bez
`DJANGO_SECRET_KEY` i `DJANGO_ALLOWED_HOSTS`, a `DEBUG` jest w niej zawsze wyłączony. Inne środowisko
wybierasz zmienną `DJANGO_SETTINGS_MODULE`.

Zmienne środowiskowe: patrz `.env.example`. Najważniejsze: `SITE_NAME`, `SITE_URL` (linki w e-mailach
i `spec.file.url`), `HUEY_IMMEDIATE`, `POSTGRES_HOST`, `REDIS_URL`, `PRINTER_WEBHOOK_SECRET`.

## Scenariusz prezentacji (ok. 5 minut)

1. S1: wgraj `apps/demo/images/typical_1448.png`. S2: A3 `ok`, A2 `wymaga poprawy`, 50x70 i B2 `wymaga poprawy`
   (przy 4x upscale "za słaby" pojawia się dopiero dla `small_512.png`, patrz uwaga niżej).
2. Wybierz A2, enhancer włączy się sam, przesuń kadr i przybliż suwakiem, cena i ocena zmieniają się na żywo.
3. S3: mockup na ścianie, wycinek przed/po.
4. S4 -> S5: "Zapłać (sukces)". S6: numer i link do statusu.
5. `/ops/queue/`: zamówienie czeka; szczegóły: plik przed/po z DPI, PDF do pobrania. Zatwierdź.
6. `/mock-printer/`: zlecenie z JSON i plikiem. Klikaj: przyjmij -> wydrukowane -> nadane.
7. S7 (link statusu) zmienia się po każdym kliknięciu; e-mail w logu serwera.
8. Ścieżka wyjątku: wgraj `logo_test.png`, opłać -> `on_hold` -> odrzuć z powodem -> zwrot (symulacja).
9. `/ops/`: lista z filtrami, licznik "wymaga uwagi", dodruk na zamówieniu w stanie `printed`/`delivered`.

## Uwagi do seeda

- Progi z §7 (ostry >= 200, ok 120-200, wymaga poprawy 30-120, za słaby < 30 DPI) nie pozwalają, żeby
  jeden plik był jednocześnie `ok` na A3 i `za słaby` na B2 (stosunek szerokości 1.68, stosunek progów 4).
  Dlatego obrazek "typowy" ma 1448 x 2048 px (`typical_1448.png`) i daje A3 `ok`, A2/B2 `wymaga poprawy`;
  `small_512.png` (362 x 512) daje `za słaby` na A2 i większych.
- Zamiast HTMX użyty jest minimalny vanilla JS (brak zależności z sieci). Wymiana na HTMX nie dotyka `services.py`.
- Auto-symulacja drukarni działa bez workera: włączona na pulpicie D1 odpytuje `/mock-printer/tick/` co N sekund,
  gdy strona jest otwarta. Z workerem dodatkowo zadanie okresowe co minutę.
- Zdarzenia z mock drukarni idą przez HTTP do własnego webhooka (`requests.post`), więc `runserver` musi być
  wielowątkowy (domyślnie jest). W `seed_demo` webhook wołany jest bezpośrednio (brak serwera).
