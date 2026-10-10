# Słownik produktów (`product_code`)

Kontrakt v1, pkt 10. Plik: `apps/products/slownik.json`. Wersja 1.0 z 2026-10-10.

## Zasady

- Jeden plik dla wszystkich sklepów i GH. Sklep ma go w repo, GH trzyma kopię. Zmiana pliku
  podnosi jego `version`, nie wersję API.
- Kod jest kluczem: małe litery, cyfry, myślniki, stały w czasie. Zmiana specyfikacji = nowy kod,
  stary zostaje (zlecenia historyczne).
- Sklep bierze `product_code` i `name` pozycji ze słownika (`apps.products.dictionary.get`),
  nie składa ich w kodzie. Kod spoza słownika GH przyjmuje, ale oznacza „do weryfikacji”.
- Wpis opisuje, co GH ma wyprodukować, nie co kupujący widzi w sklepie. Opcje, które są już
  w pliku (np. biały margines plakatu), nie wchodzą do kodu.

## Wpis

| Pole | Znaczenie |
|---|---|
| klucz | `product_code`, np. `poster-a2-matte` |
| `name` | nazwa dla produkcji, trafia do `items[].name` w zleceniu |
| `format` | wymiar netto, np. `A2, 420 x 594 mm` |
| `paper` | papier |
| `finish` | wykończenie, spady, uszlachetnienia |
| `files` | lista oczekiwanych plików: `role`, `pages`, `description` |

Kontrakt v1 przyjmuje jeden plik na pozycję, więc `files` ma dziś jeden element. Lista jest po to,
żeby wpis produktu wieloplikowego nie wymagał zmiany formatu słownika.

## Szablon: produkt wieloplikowy (książka)

```json
"book-a5-softcover": {
  "name": "Książka A5, oprawa miękka klejona",
  "format": "A5, 148 x 210 mm",
  "paper": "blok offset 90 g, okładka kreda 300 g mat",
  "finish": "oprawa klejona, folia mat na okładce, spady 3 mm",
  "files": [
    {"role": "cover", "pages": 1, "description": "rozkładówka okładki z grzbietem, spady"},
    {"role": "block", "pages": null, "description": "blok, strony pojedyncze, liczba stron parzysta"}
  ]
}
```

Taki wpis czeka na kontrakt v1.1/v2 (kilka plików na pozycję, grubość grzbietu z liczby stron).

## Do uzgodnienia z GH

- Miejsce pliku po stronie GH i sposób synchronizacji (kopia w repo GH albo pobieranie ze sklepu).
- Czy GH chce w słowniku gotowe etapy produkcji (wtedy wpis dostaje pole `stages`).
