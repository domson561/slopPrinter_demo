# drukuj-to.me - sklep z drukiem od 1 sztuki, produkcja w Graphic House

Sklep obsługuje kupującego: katalog, plik, koszyk, płatność, etykietę, maile, status. Produkcję
robi drukarnia Graphic House (GH), do której sklep wysyła zlecenia i którą odpytuje o statusy przez
API z `docs/kontrakt-gh-v1.md`. W repo jest atrapa GH (`apps/gh_mock`) o kształcie tego kontraktu,
więc całość działa bez prawdziwej drukarni. Pierwszy produkt to plakat z obrazka AI, zbudowany
jako demo wg `docs/slop_printer_seed_3.md` (ocena jakości, kadr, PDF ze spadami); płatność i kurier
są na razie symulowane i mają etykietę "symulacja" w UI.

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
- Atrapa Graphic House (biuro drukarni): http://localhost:8000/gh-mock/
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
idempotencja `submit` i callbacku płatności, pełna ścieżka klienta. Atrapa GH jako serwer kontraktu
(`apps/gh_mock/tests`) i sklep jako jego klient (`apps/printing/tests`).

Testy zgodności z kontraktem v1, wspólne dla każdego sklepu na rdzeniu (koszyk z próbek
produktów z `SHOP_PRODUCTS`, `Product.contract_sample`):

```bash
pytest apps/printing/tests/contract
```

## Struktura

| Aplikacja       | Zawartość |
|-----------------|-----------|
| `apps/catalog`  | metody dostawy i `ShopSettings` rdzenia (auto-zatwierdzanie, czas realizacji, retencja, waluta) |
| `apps/assets`   | bramka plików od kupujących (`gate.py`: typ po zawartości, limity, obraz zapisany na nowo, PDF bez treści aktywnej), oryginały w prywatnym katalogu (`storage.py`), `Asset`/`AssetVersion`, podgląd, `AnalysisService` z `MockContentSafety` (flagi treści), komenda `cleanup_assets` |
| `apps/products` | interfejs produktu (`Product`: kod, opcje, cena, pliki od kupującego, pliki do druku, opis dla GH), rejestr z `SHOP_PRODUCTS` i słownik `product_code` wspólny z GH, zarazem tabela stawek GH za druk (`slownik.json`, `dictionary.py`); do GH idzie stawka, nie cena detaliczna; rdzeń zna produkty tylko stąd |
| `apps/poster`   | plakat z demo jako implementacja `Product` (`PosterProduct`): formaty, papiery, `PosterSettings` (progi DPI), cennik, ocena jakości (`quality.py`), `pipeline.py` (`MockUpscale`, `CropService`, `PrepressService` z reportlab, `MockupService`, `run_pipeline`), widoki S1-S3 i obrazy, szablony `templates/poster`, sekcje panelu O3/O5 |
| `apps/ready_pdf` | szkieletowy produkt „gotowy PDF” (bez obróbki): wzór najmniejszego `Product`, poza `SHOP_PRODUCTS`, włączany w testach |
| `apps/orders`   | `Order`/`OrderItem` (kod produktu + opcje JSON)/`OrderStatusEvent`/`Payment`/`Shipment`/`Complaint`, maszyna stanów (`services.transition`, `force_transition`), `MockPaymentProvider`, przesyłka i etykieta PDF za interfejsem przewoźnika (`shipping.py`, `MockCarrierProvider`, `CARRIER_PROVIDER`), powiadomienia, zadanie `process_order_task` |
| `apps/printing` | `PrintJob` (jedno zlecenie na koszyk), `GhApiProvider` (klient kontraktu v1, transport in-process do atrapy albo HTTP), body POST-a, podpisane wygasające linki do plików (`links.py`, `/files/signed/<token>.pdf`), submit z retry, polling `changes` co 5 min i mapowanie statusów GH |
| `apps/gh_mock`  | atrapa GH: API z kontraktu pod `/shop-api/v1/` (token, idempotencja, pliki, zmiany), pulpit biura `/gh-mock/`, `GhMockSettings` (auto-symulacja) |
| `apps/ops`      | panel operatora O1-O5 (`is_staff`) |
| `apps/web`      | widoki klienta rdzenia: koszyk (sesja trzyma jeden szkic, pozycje z wielu plików), S4-S8 (dane, potwierdzenie, status, reklamacja, strony) i trasy `web:*`; S1-S3 wpina produkt |
| `apps/demo`     | `seed_demo`, `make_demo_images` (obrazki rysowane Pillow) |

Logika biznesowa jest w `services.py` każdej aplikacji; widoki są cienkie. Wygląd: `static/css/tokens.css`
(zmienne) + `static/css/base.css` (wireframe). Bez bibliotek UI.

## Konfiguracja

Ustawienia w `config/settings/`: `base.py` (wspólne), `development.py` (domyślne dla `manage.py`,
testów i Dockera) i `production.py` (domyślne dla `wsgi`/`asgi`). Produkcja nie startuje bez
`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `SITE_URL` (https), `GH_API_BASE_URL` (https) i `GH_API_TOKEN`, `DEBUG` jest w niej zawsze
wyłączony, a atrapa GH nigdy nie jest zainstalowana. Inne środowisko
wybierasz zmienną `DJANGO_SETTINGS_MODULE`.

Zmienne środowiskowe: patrz `.env.example`. Najważniejsze: `SITE_NAME`, `SITE_URL` (linki w e-mailach
i linki do plików dla GH), `PRIVATE_MEDIA_ROOT` (pliki od kupujących poza `/media/`, wydawane tylko
przez widok; domyślnie `private_media/`), `HUEY_IMMEDIATE`, `POSTGRES_HOST`, `REDIS_URL`, `GH_API_BASE_URL`
(ścieżka = atrapa w tym procesie, URL http(s) = prawdziwe GH), `GH_API_TOKEN`, `GH_MOCK_ENABLED`
(atrapa GH pod `/shop-api/v1/` i `/gh-mock/`, w dev domyślnie włączona).

## Scenariusz prezentacji (ok. 5 minut)

1. S1: wgraj `apps/demo/images/typical_1448.png`. S2: A3 `ok`, A2 `wymaga poprawy`, 50x70 i B2 `wymaga poprawy`
   (przy 4x upscale "za słaby" pojawia się dopiero dla `small_512.png`, patrz uwaga niżej).
2. Wybierz A2, enhancer włączy się sam, przesuń kadr i przybliż suwakiem, cena i ocena zmieniają się na żywo.
3. S3: mockup na ścianie, wycinek przed/po.
4. S4 -> S5: "Zapłać (sukces)". S6: numer i link do statusu.
5. `/ops/queue/`: zamówienie czeka; szczegóły: plik przed/po z DPI, PDF do pobrania. Zatwierdź: sklep
   zakłada przesyłkę u atrapy kuriera (numer `MOCK…`, etykieta PDF w sekcji „Wysyłka”) i wysyła zlecenie.
6. `/gh-mock/`: zlecenie ze sklepu z pozycjami, plikami i etykietą. Biuro: pobierz pliki -> w realizacji -> gotowe -> wysłane.
   W `/ops/` „Odpytaj GH” (normalnie robi to zadanie co 5 minut; GH nigdy nie woła sklepu).
7. S7 (link statusu) zmienia się po każdym odpytaniu; e-mail w logu serwera.
8. Ścieżka wyjątku: wgraj `logo_test.png`, opłać -> `on_hold` -> odrzuć z powodem -> zwrot (symulacja).
9. `/ops/`: lista z filtrami, licznik "wymaga uwagi", dodruk na zamówieniu w stanie `printed`/`delivered`.

## Uwagi do seeda

- Progi z §7 (ostry >= 200, ok 120-200, wymaga poprawy 30-120, za słaby < 30 DPI) nie pozwalają, żeby
  jeden plik był jednocześnie `ok` na A3 i `za słaby` na B2 (stosunek szerokości 1.68, stosunek progów 4).
  Dlatego obrazek "typowy" ma 1448 x 2048 px (`typical_1448.png`) i daje A3 `ok`, A2/B2 `wymaga poprawy`;
  `small_512.png` (362 x 512) daje `za słaby` na A2 i większych.
- Zamiast HTMX użyty jest minimalny vanilla JS (brak zależności z sieci). Wymiana na HTMX nie dotyka `services.py`.
- Auto-symulacja biura GH działa bez workera: włączona na `/gh-mock/` odpytuje `/gh-mock/tick/` co N sekund,
  gdy strona jest otwarta. Z workerem dodatkowo zadanie okresowe co minutę.
- Sklep woła atrapę przez pełny cykl HTTP Django w tym samym procesie (`InProcessTransport`), bez sieci.
  Prawdziwe GH: `GH_API_BASE_URL=https://…` przełącza na `requests`. Atrapa czyta pliki z własnego `/media/`
  z dysku; prawdziwe GH pobiera je po HTTP z podpisanych linków (osobny task).
