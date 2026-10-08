# Kontrakt API v1: sklep ↔ Graphic House

Wersja: 1.0-draft, 2026-10-08. Uzgodniony z `E:\DJANGO\graphic_house\demo_docs\scope.md`, pkt 6.
Źródło prawdy do czasu, aż GH wystawi własny `schema.yml`; wtedy schemat ma zgadzać się z tym dokumentem.

Nazwy statusów i jednostek wzięte z modelu `Order` w GH (`new`, `in_progress`, `ready`, `shipped`, jednostka `komplet`).

## 1. Zasady

- Granica jest techniczna i własnościowa: aplikacja drukarni jest GH, sklepy są podpinane z zewnątrz. Kontrakt opisuje tylko wymianę danych. Kto komu i za ile sprzedaje, to umowa poza kontraktem.
- Sklep obsługuje kupującego: katalog, pliki, koszyk, płatność, etykietę, maile, status. GH produkuje i wysyła.
- Każdy ruch inicjuje sklep: wysyła zamówienie i odpytuje o zmiany. GH nie woła sklepu i nie trzyma do niego kluczy.
- Dane kupującego są wyłącznie w PDF etykiety. Pola adresu, uwag dla kupującego, e-maila, telefonu w żądaniu są odrzucane z błędem 400.
- Koszyk = jedno zlecenie GH: nakład 1, jednostka `komplet`, wartość = wartość całego koszyka. Pozycje są listą w zleceniu.
- Pliki GH pobiera sam, z podpisanych linków. Sklep nigdy nie wysyła plików w body.
- To, który `Client` w GH jest przypięty do sklepu, jest konfiguracją po stronie GH przy wydawaniu tokenu, nie częścią kontraktu.

## 2. Uwierzytelnianie

- Nagłówek `Authorization: Token <token sklepu>`. Jeden token per sklep, w GH przechowywany jako hash, cofany w każdej chwili.
- 401 = sklep wstrzymuje wysyłkę i polling, alarmuje operatora, nie ponawia automatycznie.
- Endpointy sklepowe są poza `/api/` GH (tam są heartbeaty biura), prefiks `/shop-api/v1/`.
- GH loguje odrzucone tokeny (sklep plus IP).

## 3. Wysłanie zamówienia

`POST /shop-api/v1/orders/`

```json
{
  "shop_order_number": "SP-2026-000123",
  "title": "Zamówienie SP-2026-000123",
  "total_value": "148.50",
  "currency": "PLN",
  "items": [
    {
      "position": 1,
      "name": "Plakat A2, papier 200 g mat",
      "product_code": "poster-a2-200m",
      "quantity": 2,
      "unit_price": "61.00",
      "file": {
        "url": "https://sklep.pl/files/signed/abc...?exp=1759900000&sig=...",
        "sha256": "9f86d08...",
        "size": 4312000,
        "expires_at": "2026-10-09T09:14:00Z"
      }
    }
  ],
  "label": {
    "url": "https://sklep.pl/files/signed/lbl...?exp=...&sig=...",
    "sha256": "e3b0c44...",
    "size": 52000,
    "expires_at": "2026-10-09T09:14:00Z"
  },
  "note_for_production": "Nie zaginać."
}
```

Pola:

| Pole | Wymagane | Opis |
|---|---|---|
| `shop_order_number` | tak | Numer zamówienia w sklepie. Unikalny w parze ze sklepem. Klucz idempotencji. |
| `title` | tak | Tytuł zlecenia widoczny w GH. |
| `total_value` | tak | Wartość zlecenia dla rozliczeń GH, ustalana umową poza kontraktem. Musi równać się sumie `quantity × unit_price` pozycji. |
| `currency` | tak | W v1 tylko `PLN`. |
| `items[]` | tak, min. 1 | Pozycje koszyka. |
| `items[].position` | tak | Kolejny numer od 1, identyfikuje pozycję i jej plik. |
| `items[].name` | tak | Opis dla człowieka, trafia na kartę PDF zlecenia, żeby operator wiedział, który plik drukuje. |
| `items[].product_code` | tak | Identyfikator ze słownika produktów (pkt 10). Nieznany kod nie odrzuca zlecenia, GH oznacza je „do weryfikacji”. |
| `items[].quantity` | tak | Nakład pozycji. |
| `items[].unit_price` | tak | Cena jednostkowa pozycji dla rozliczeń GH. |
| `items[].file` | tak | Plik do druku, patrz pkt 4. |
| `label` | tak | PDF etykiety z danymi kupującego, patrz pkt 4. |
| `note_for_production` | nie | Uwagi dla produkcji. Bez danych kupującego. |

Zachowanie:

- Idempotencja: klucz to para (sklep, `shop_order_number`). Pierwsze przyjęcie zwraca 201. Ponowny POST z tym samym numerem i tą samą treścią zwraca 200 i to samo zlecenie. Ten sam numer z inną treścią zwraca 409.
- Zmiana zamówienia po wysłaniu jest zabroniona. Sklep nadaje nowy numer.
- GH tworzy zlecenie z nakładem 1 `komplet`, ceną `total_value`, numerem GH w serii wspólnej (`N/ROK`), numerem sklepu obok.
- Mapowanie pozycji na etapy produkcji robi biuro GH ręcznie (v1).

Odpowiedź (201 lub 200):

```json
{
  "gh_number": "118/2026",
  "shop_order_number": "SP-2026-000123",
  "status": "new",
  "files": [
    {"kind": "item", "position": 1, "state": "pending"},
    {"kind": "label", "state": "pending"}
  ],
  "created_at": "2026-10-08T09:14:00Z"
}
```

## 4. Pliki

- Obiekt pliku: `url` (podpisany, wygasający link), `sha256`, `size` w bajtach, `expires_at`.
- Ważność linku: co najmniej 24 h od wysłania (do potwierdzenia z GH). GH pobiera w tle z crona, sprawdza nagłówek PDF, limit rozmiaru i `sha256`. Pobiera do `.part`, przemianowuje po weryfikacji.
- Stany pliku: `pending`, `downloaded`, `failed`. Przy `failed` pole `error`: `expired`, `checksum_mismatch`, `not_pdf`, `too_large`, `unreachable`.
- Dosłanie świeżego linku: `POST /shop-api/v1/orders/{shop_order_number}/files/`

```json
{"kind": "item", "position": 1, "file": {"url": "...", "sha256": "...", "size": 4312000, "expires_at": "..."}}
```

  albo `{"kind": "label", "file": {...}}`. Dozwolone tylko dla pliku w stanie `pending` lub `failed`. Plik `downloaded` nie podlega podmianie, zmiana wymaga nowego zamówienia.
- Zlecenie nie wchodzi w realizację, dopóki wszystkie pliki nie są `downloaded`.
- GH trzyma pliki poza publicznym `/media/`, kasuje X dni po wysyłce razem z etykietą. Sklep trzyma oryginały.

## 5. Odpytywanie o zmiany

`GET /shop-api/v1/orders/changes/?since=2026-10-08T09:00:00Z`

```json
{
  "server_time": "2026-10-08T10:02:11Z",
  "orders": [
    {
      "shop_order_number": "SP-2026-000123",
      "gh_number": "118/2026",
      "status": "in_progress",
      "status_changed_at": "2026-10-08T09:40:00Z",
      "shipped_at": null,
      "files": [
        {"kind": "item", "position": 1, "state": "downloaded"},
        {"kind": "label", "state": "failed", "error": "expired"}
      ]
    }
  ]
}
```

- Zwraca zlecenia tego sklepu, w których od `since` zmienił się status lub stan pliku. Sklep zapisuje `server_time` jako następne `since`, nie własny zegar.
- Częstotliwość: co 5 minut. Przy 5xx backoff do 30 minut.
- Zlecenie rozbite ręcznie przez biuro raportuje się pod oryginalnym `shop_order_number` jako jedno, ze statusem najmniej zaawansowanej części.
- Bez numeru przesyłki, etykietę robi sklep.
- Pojedyncze zlecenie: `GET /shop-api/v1/orders/{shop_order_number}/`, ten sam kształt co element `orders[]`.

## 6. Statusy

Statusy GH: `new`, `in_progress`, `ready`, `shipped`. Nie ma anulowania ani odrzucenia.

| GH | Sklep (`PrintJob.Status`) | Dla kupującego |
|---|---|---|
| brak (POST nie wysłany lub nieudany) | `created` | w przygotowaniu |
| `new`, pliki nie wszystkie `downloaded` | `sent` | przyjęte |
| `new`, wszystkie pliki `downloaded` | `accepted` | przyjęte do druku |
| `in_progress` | `accepted` | w druku |
| `ready` | `printed` | wydrukowane, czeka na kuriera |
| `shipped` | `shipped` | nadane |

Tylko po stronie sklepu, bez udziału GH:

- `rejected`: plik nie przeszedł kontroli w sklepie przed wysyłką.
- `failed`: wada zgłoszona przez kupującego. Sklep tworzy nowe zamówienie z nowym numerem jako dodruk (`reprint_of`).
- `cancelled`: możliwe wyłącznie przed POST-em. Po wysłaniu do GH sklep nie anuluje; ewentualne wstrzymanie to telefon do biura.

Plik w stanie `failed` nie zmienia statusu w sklepie, tylko uruchamia dosłanie linku.

## 7. Błędy

| Kod | Znaczenie | Sklep |
|---|---|---|
| 400 | walidacja, `{"errors": {"items.0.file.sha256": ["..."]}}` | nie ponawia, oznacza zlecenie do ręcznej obsługi |
| 401 | zły lub cofnięty token | wstrzymuje wszystko, alarm |
| 404 | nieznany `shop_order_number` | nie ponawia |
| 409 | numer istnieje z inną treścią | nie ponawia, nowy numer |
| 429, 5xx, timeout | przejściowe | ponawia ten sam POST z backoffem |

## 8. Poza v1

Anulowanie w GH, numer przesyłki, webhooki, strukturalny opis produktu w żądaniu, częściowe wykonanie, kilka paczek na jedno zamówienie, waluty inne niż PLN.

## 9. Wersjonowanie

- Wersja w ścieżce (`/v1/`).
- Zmiana łamiąca: usunięcie pola, zmiana znaczenia statusu lub stanu pliku, nowy wymagany parametr. Wymaga `/v2/`.
- Dodanie pola opcjonalnego nie łamie. Sklep ignoruje nieznane pola w odpowiedziach.

## 10. Słownik produktów

Znaczenie wartości `product_code` jest poza kontraktem, w osobnym pliku słownika utrzymywanym wspólnie przez sklep i GH, wersjonowanym niezależnie od API. Dodanie produktu nie zmienia kontraktu. Docelowo wpis słownika może nieść specyfikację (format, papier, wykończenie) i gotowe etapy produkcji; w v1 biuro dobiera etapy ręcznie na podstawie `name` i kodu.

## 11. Zgodność ze scope pkt 6 i wymagane zmiany

W GH:

- Model `Shop` (token, kod, kolor, `Client`), na `Order` pole `shop` i numer sklepu, unikalne w parze. Zgodne ze scope.
- Model pozycji zlecenia i model plików (`OrderFile`) ze stanem pobrania, sumą kontrolną, datą skasowania. Scope je sugeruje, kontrakt ich wymaga.
- Serializer odrzuca pola adresu i uwag kupującego, żeby dane osobowe nie weszły do bazy i auditlogu.
- `created_by` opcjonalne dla zleceń ze sklepu.
- Zalecenie: dla zleceń sklepowych odbicie etapu Transport ustawia `shipped`. Bez tego status przyjdzie z opóźnieniem, aż biuro kliknie ręcznie.
- Flaga „do weryfikacji” dla nieznanego `product_code`.

W sklepie:

- `PrintJob` na poziomie `Order`, pozycje w `spec` (wcześniej jedno zlecenie na `OrderItem`). Zrobione 2026-10-08.
- Atrapa GH (`apps/gh_mock`) o kształcie z tego dokumentu zamiast `MockPrinterJob` i `MockPrintProvider`; jej statusy to statusy GH plus stany plików. Zrobione 2026-10-08.
- `accepted` znaczy „GH ma wszystkie pliki”. Zrobione 2026-10-08.
- Podpisane, wygasające linki z sumą kontrolną zamiast publicznego `/media/`. Do zrobienia (task „Pliki do druku”).

Poza kontraktem, do ustalenia między stronami: rozliczenie i faktura zbiorcza, umowa powierzenia danych z etykiet (RODO), minimalna ważność linku, zawartość słownika produktów, X dni retencji plików w GH.

## 12. Definicja zakończenia zadania (canvas)

- Wysłanie zamówienia: pkt 3 (pozycje, wartość, klucz idempotencji), pkt 4 (pliki, etykieta).
- Odpytywanie o statusy: pkt 5.
- Mapowanie statusów: pkt 6.
- Zgodność ze scope pkt 6: pkt 11.
