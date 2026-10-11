# Granica rdzeń ↔ sklep

Stan na 2026-10-10, gałąź `v1/rdzen`. Uzupełnia `kontrakt-gh-v1.md` (granica sklep ↔ GH).

## Zasada

Jeden rdzeń, wiele sklepów (ustalone 2026-10-08). Sklep to osobna instalacja: własna domena,
baza, `.env` i token w GH. Składa się z rdzenia (wspólny kod), własnego produktu wpiętego przez
interfejs `apps.products.Product` i własnych szablonów. Rdzeń nie wie, że istnieją inne sklepy,
i nie wie, co sprzedaje ten.

Test prostoty: **nowy sklep nie zmienia żadnego pliku rdzenia.** Jeśli produkt wymaga łatki
w `orders`, `printing`, `ops` albo `assets`, to brakuje czegoś w interfejsie, nie w produkcie.

## Rdzeń (wspólny, taki sam w każdym sklepie)

| Obszar | Kod | Co robi |
|---|---|---|
| Interfejs produktu | `apps/products` | `Product`, `GhLine`, `ProductError`, rejestr z `SHOP_PRODUCTS`. Jedyne miejsce, przez które rdzeń widzi produkt. |
| Zamówienia | `apps/orders` | `Order`, `OrderItem` (kod produktu + opcje JSON + plik), maszyna stanów, płatność, przesyłka i etykieta PDF za interfejsem przewoźnika (`shipping.py`), reklamacje ze zdjęciami, maile o statusie, `process_order`. |
| Druk | `apps/printing` | `PrintJob` (koszyk = jedno zlecenie), `GhApiProvider`, budowa body wg kontraktu, submit z retry, polling `changes`, mapowanie statusów GH. |
| Pliki | `apps/assets` | `Asset`, `AssetVersion`, bramka `gate.py` (typ po zawartości, limity, sanityzacja obrazów i PDF), prywatny storage oryginałów, `save_version`. |
| Panel operatora | `apps/ops` | O1-O5: lista, kolejka, zamówienie, akcje, ustawienia. Fragmenty per produkt wpina przez `operator_template` i `settings_template`. |
| Koszyk i proces zakupu po konfiguracji | `apps/web` koszyk, S4-S8 | koszyk w sesji (`web/cart.py`, jeden szkic z listą pozycji), checkout, płatność, potwierdzenie, status, reklamacja, strony statyczne. Logika z `orders.services`, szablony sklepu. |
| Ustawienia sklepu | `catalog.ShopSettings` (część), `catalog.DeliveryMethod` | auto-zatwierdzanie, czas realizacji, retencja, waluta, metody dostawy. |
| Atrapa GH | `apps/gh_mock` | tylko development, pod `GH_MOCK_ENABLED`. |
| Konfiguracja | `config/settings` | `SHOP_PRODUCTS`, `GH_API_*`, `PAYMENT_PROVIDER`, `CARRIER_PROVIDER`, `PRIVATE_MEDIA_ROOT`, `SITE_URL`, `ORDER_NUMBER_PREFIX`, huey, e-mail. |

## Sklep (każdy robi po swojemu)

| Obszar | Co dostarcza sklep |
|---|---|
| Produkt | Klasa `Product`: kod, opcje i ich walidacja, cena, dozwolone typy plików, produkcja PDF-ów do druku, powody weryfikacji, opis pozycji dla GH, podsumowanie i szczegóły, fragmenty panelu i formularze ustawień (np. cennik). Własne modele konfiguracji (dla plakatu: formaty, papiery, progi DPI). |
| Proces zakupu do konfiguracji | S1-S3 w aplikacji produktu (`apps/poster/views.py`): upload, konfigurator, podgląd pozycji. Formularz opcji, kadrowanie, makieta, cena na żywo, JS. Po podglądzie odsyła do koszyka rdzenia. |
| Wygląd | `templates/web`, `static/css/tokens.css`, `base.css`, treści S8 (FAQ, regulamin, prywatność). Szablony maili w `templates/emails` może nadpisać. |
| Konfiguracja wdrożenia | domena i `SITE_URL`, token GH, prefiks numeru, metody dostawy, cennik, `SHOP_PRODUCTS`. |

## Reguły na granicy

1. `orders`, `printing`, `ops` importują produkt tylko przez `apps.products`; pilnuje test w `apps/products/tests`.
2. Pozycja to `product` (kod) + `options` (JSON). Rdzeń przechowuje opcje, nigdy ich nie interpretuje. Zmiana opcji produktu nie wymaga migracji rdzenia.
3. Każdy plik z zewnątrz (plik do produktu, zdjęcie do reklamacji) przechodzi przez `assets.gate`. Produkt może listę typów zawęzić (`accepted_mime`), nigdy poszerzyć. Bramka ocenia bezpieczeństwo, produkt przydatność do druku.
4. Pliki do druku to `AssetVersion` rodzaju `print_pdf` przypięte do pozycji; robi je produkt w `build_print_files`, w tle. Kontrakt v1 przyjmuje dokładnie jeden plik na pozycję, więcej daje czytelny błąd przy wysyłce (do v1.1/v2).
5. Opis pozycji dla GH to `GhLine(product_code, name)` z produktu. Oba pola ze słownika `apps/products/slownik.json` (`apps.products.dictionary`), nie składane w kodzie. Kod spoza słownika GH oznacza „do weryfikacji”.
6. Cena: produkt daje cenę detaliczną za sztukę z opcji. Cena hurtowa dojdzie z taskiem „Katalog” i będzie wartością zlecenia w GH.
7. Panel operatora jest rdzeniem. Produkt dostarcza tylko fragment szablonu i dane do niego (`operator_context`, `settings_forms`), nie własne widoki.
8. Rdzeń nie zna GH inaczej niż przez `GhApiProvider`; sklep nie woła GH bezpośrednio ani nie buduje body zlecenia.
9. Numer przesyłki i etykietę robi rdzeń przez `orders.shipping` (przewoźnik wymienny jak płatności), zanim zlecenie pójdzie do GH. Dane kupującego są tylko w PDF etykiety. Produkt nie wie o wysyłce.

## Stan obecny

Przeniesienie zrobione 2026-10-10: `apps/assets` i `apps/catalog` nie znają formatu, papieru,
DPI ani kadru. Pilnuje tego test `apps/products/tests/test_products.py` (`test_core_has_no_poster_references`),
który sprawdza `products`, `assets`, `catalog`, `orders`, `printing`, `ops` i szablony rdzenia.

Co gdzie wylądowało:
- `apps/poster/models.py`: `Format`, `PaperOption`, `PosterSettings` (progi DPI, mnożnik enhancera); migracja `poster.0001` kopiuje dane z `catalog`, `catalog.0003` usuwa je z rdzenia.
- `apps/poster/quality.py`, `pipeline.py`, `pricing.py`: ocena jakości, upscale/kadr/PDF/makieta, cennik.
- `apps/poster/views.py` + `templates/poster/s1`-`s3`: wgranie, konfigurator, podgląd, obrazy. Trasy nadal pod nazwami `web:*` w `apps/web/urls.py`.
- `apps/poster/services.py`: `upload_poster_asset` (bramka rdzenia + werdykt per format), `save_poster_draft`.
- W rdzeniu zostały: `AssetAnalysis.quality_per_format` jako ogólne pole JSON, które wypełnia produkt, i analiza treści (`MockContentSafety`, flagi) w `apps/assets`. Czy moderacja treści ma zostać rdzeniem, rozstrzygnie pierwszy produkt, który jej nie potrzebuje.

## Lista kontrolna nowego sklepu

1. Zainstalować rdzeń (te same aplikacje, te same migracje).
2. Napisać klasę `Product` i wpisać ją do `SHOP_PRODUCTS`; wzór najmniejszy: `apps/ready_pdf`, pełny: `apps/poster`.
3. Dostarczyć widoki S1-S3 i szablony `templates/web`, tokeny CSS.
4. Ustawić `.env`: `SITE_URL`, `GH_API_BASE_URL`, `GH_API_TOKEN`, `ORDER_NUMBER_PREFIX`, `PRIVATE_MEDIA_ROOT`, `CARRIER_PROVIDER` (domyślnie atrapa kuriera).
5. Zasiać metody dostawy i ustawienia sklepu.
6. Przejść testy zgodności na atrapie GH (task „seed nowego sklepu”, jeszcze nie ma).
