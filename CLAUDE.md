# slop_printer

Sklep(y) z drukiem od 1 sztuki. Produkcję robi drukarnia Graphic House (GH): osobna aplikacja
klienta, repo `E:\DJANGO\graphic_house`, sklep łączy się z nią przez API.

Obecny kod to demo „plakat z obrazka AI” (`docs/slop_printer_seed_3.md`, opis w README).
To baza do przebudowy, nie gotowy produkt.

## Granica sklep ↔ GH
Ustalone 2026-10-03, szczegóły w `E:\DJANGO\graphic_house\demo_docs\scope.md`, pkt 6.
- Sklep obsługuje kupującego: katalog, pliki, koszyk, płatność, etykieta, maile, status. GH tylko produkuje; sklep jest dla GH klientem hurtowym.
- Koszyk = jedno zlecenie w GH z listą pozycji. Cena zlecenia = wartość hurtowa koszyka.
- Komunikacja zawsze z inicjatywy sklepu: wysłanie zamówienia + odpytywanie o statusy. GH nie woła sklepu.
- Dane kupującego tylko na etykiecie PDF, nigdy w polach zlecenia.
- Pliki dla GH przez podpisane, wygasające linki z sumą kontrolną, nie z publicznego `/media/`.
- GH zna statusy: nowe, w realizacji, gotowe, wysłane. Odrzucenia, wady i dodruki obsługuje sklep.
- Kontrakt: `docs/kontrakt-gh-v1.md`. Klient GH: `apps/printing/providers.py` (`GhApiProvider`, transport
  in-process do atrapy albo HTTP). Atrapa GH: `apps/gh_mock` (API pod `/shop-api/v1/`, pulpit `/gh-mock/`), instalowana
  tylko w `development.py` pod `GH_MOCK_ENABLED`; produkcja wymaga `GH_API_BASE_URL` (https) i `GH_API_TOKEN`.

## Plan
Mapa zadań: `E:\OBSIDIAN VAULTS\PROJECT_VAULTS\slopPrinter.canvas`, zmiany tylko przez skill `canvas`.
Granica rdzeń ↔ sklep (co wspólne, co per sklep, co jeszcze do przeniesienia): `docs/rdzen-vs-sklep.md`.
Ustalone 2026-10-08: jeden rdzeń, wiele sklepów (sklep = rdzeń + własny produkt + własne szablony, osobna instalacja).
Otwarte: która marka i produkt startują pierwsze; blokuje dopiero grupę 3.

## Praca
- Testy: `.venv\Scripts\python -m pytest` (ok. 2,5 min). Podgląd na porcie 8010 (`.claude/launch.json`).
- Po każdej zmianie w kodzie Python Claude uruchamia `.venv\Scripts\python -m ruff check .` i `ruff format .`; konfiguracja w `pyproject.toml`.
- Zależności: `requirements.txt` (produkcja) i `requirements-dev.txt` (pytest, ruff), wersje przypięte.
- Git i instalacje robi użytkownik.
- Odpowiedzi krótkie; przy rozkminach poziom idei, szczegóły na prośbę.
