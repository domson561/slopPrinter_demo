# slop_printer

Sklep(y) z drukiem od 1 sztuki. Produkcję robi drukarnia Graphic House (GH): osobna aplikacja
klienta, repo `E:\DJANGO\graphic_house`, sklep łączy się z nią przez API.

Obecny kod to demo „plakat z obrazka AI” (`slop_printer_seed_3.md`, opis w README).
To baza do przebudowy, nie gotowy produkt.

## Granica sklep ↔ GH
Ustalone 2026-10-03, szczegóły w `E:\DJANGO\graphic_house\demo_docs\scope.md`, pkt 6.
- Sklep obsługuje kupującego: katalog, pliki, koszyk, płatność, etykieta, maile, status. GH tylko produkuje; sklep jest dla GH klientem hurtowym.
- Koszyk = jedno zlecenie w GH z listą pozycji. Cena zlecenia = wartość hurtowa koszyka.
- Komunikacja zawsze z inicjatywy sklepu: wysłanie zamówienia + odpytywanie o statusy. GH nie woła sklepu.
- Dane kupującego tylko na etykiecie PDF, nigdy w polach zlecenia.
- Pliki dla GH przez podpisane, wygasające linki z sumą kontrolną, nie z publicznego `/media/`.
- GH zna statusy: nowe, w realizacji, gotowe, wysłane. Odrzucenia, wady i dodruki obsługuje sklep.
- Miejsce podpięcia GH: `apps/printing/providers.py` (`PrintProvider`). Atrapa GH zastąpi `MockPrintProvider`.

## Plan
Mapa zadań: `E:\OBSIDIAN VAULTS\PROJECT_VAULTS\slopPrinter.canvas`, zmiany tylko przez skill `canvas`.
Otwarta decyzja: jeden silnik dla kilku marek czy osobny sklep per marka.

## Praca
- Testy: `.venv\Scripts\python -m pytest` (ok. 2,5 min). Podgląd na porcie 8010 (`.claude/launch.json`).
- Git i instalacje robi użytkownik.
- Odpowiedzi krótkie; przy rozkminach poziom idei, szczegóły na prośbę.
