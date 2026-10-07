# Architektura: sklepy + Graphic House na jednym VPS

Stan: propozycja z 2026-09-27, przed decyzją klienta. Założenie: jeden kod (repo graphic_house),
jedna baza, dwa procesy www (panel i sklepy), jeden worker.

## 1. Wdrożenie na VPS

```mermaid
flowchart TB
    subgraph internet["Internet"]
        K["Klient sklepu<br/>(przeglądarka)"]
        B["Biuro / produkcja<br/>(przeglądarka, skaner QR)"]
    end

    subgraph vps["VPS (jedna maszyna)"]
        N["nginx<br/>TLS, limity uploadu, rate limit"]

        subgraph procs["Procesy z tego samego kodu"]
            GW["gunicorn: panel GH<br/>settings.production<br/>domena: panel.graphichouse.pl"]
            GS["gunicorn: sklepy<br/>settings.shop<br/>domeny: drukuj.to, kalendarze.*, horeca.*"]
            W["worker (huey)<br/>konwersja do PDF, maile,<br/>preflight, upscale"]
        end

        DB[("PostgreSQL<br/>jedna baza")]
        M[/"media/ na dysku<br/>oryginały, PDF do druku<br/>(w backupie DR)"/]
    end

    subgraph ext["Usługi zewnętrzne (później)"]
        PAY["Bramka płatności<br/>P24 / Stripe"]
        MAIL["SMTP"]
        KUR["Kurier / Paczkomat<br/>(etykiety)"]
    end

    K -->|https| N
    B -->|https| N
    N -->|drukuj.to, kalendarze, horeca| GS
    N -->|panel.*| GW
    GS --> DB
    GW --> DB
    W --> DB
    GS --> M
    GW --> M
    W --> M
    GS -.->|kolejka zadań| W
    GS <-->|redirect + callback| PAY
    W --> MAIL
    GW -.-> KUR
```

## 2. Podział odpowiedzialności w kodzie

```mermaid
flowchart LR
    subgraph shop["Aplikacje sklepowe (apps/shop_*)"]
        CAT["Katalog produktów<br/>i cennik<br/>(3 katalogi, 3 skórki)"]
        CFG["Konfigurator<br/>opcje, kadr, cena na żywo"]
        UP["Upload + preflight<br/>PDF/JPG/PNG/Word/Canva -> PDF"]
        PAYM["Płatności<br/>(drukuj.to)"]
        RFQ["Zapytanie o wycenę<br/>(kalendarze, HoReCa)"]
        STAT["Strona statusu<br/>+ e-maile do klienta"]
    end

    subgraph border["Granica (jedyne dwa punkty styku)"]
        IN["intake.create_order_from_shop()<br/>opis pracy + plik + cena + dostawa"]
        OUT["hook on_status_change()<br/>ready / shipped + nr listu"]
    end

    subgraph gh["Graphic House (apps/orders, clients, accounts)"]
        ORD["Order<br/>NR/ROK, status, etapy"]
        FILE["OrderFile (nowe)<br/>PDF do druku, sha256, źródło"]
        QUO["Quote (nowe)<br/>zapytanie -> wycena -> akceptacja"]
        PROD["Produkcja<br/>etapy, karta PDF, QR, skaner"]
        CLI["Client<br/>+ klient zbiorczy 'Sklep'"]
    end

    CAT --> CFG --> UP --> PAYM --> IN
    UP --> RFQ --> QUO
    QUO -->|akceptacja oferty| IN
    IN --> ORD
    IN --> FILE
    IN --> CLI
    ORD --> PROD
    PROD --> OUT
    OUT --> STAT
```

## 3. Ścieżka zamówienia (drukuj.to, cena natychmiast)

```mermaid
sequenceDiagram
    autonumber
    actor K as Klient
    participant S as Sklep (proces sklepy)
    participant W as Worker
    participant P as Bramka płatności
    participant G as GH (baza / panel)
    actor B as Biuro / produkcja

    K->>S: wgrywa plik, konfiguruje, widzi cenę
    S->>W: konwersja do PDF, preflight
    W-->>S: PDF do druku + ocena jakości
    K->>S: dane dostawy, zgody
    S->>P: utwórz płatność
    P-->>S: callback "opłacone" (idempotentny)
    S->>G: intake.create_order_from_shop()<br/>Order(new) + OrderFile + adres
    S-->>K: potwierdzenie + link statusu
    B->>G: widzi zlecenie na dashboardzie,<br/>przypisuje etapy, drukuje kartę
    B->>G: odhacza etapy, status ready
    G-->>S: hook: ready
    S-->>K: e-mail "gotowe"
    B->>G: wysyłka, nr listu, status shipped
    G-->>S: hook: shipped + nr listu
    S-->>K: e-mail "nadane" + śledzenie
```

## 4. Ścieżka z wyceną (kalendarze, HoReCa)

```mermaid
sequenceDiagram
    autonumber
    actor K as Klient B2B
    participant S as Sklep
    participant G as GH
    actor B as Biuro

    K->>S: konfiguruje produkt, nakład, plik, "Wyślij zapytanie"
    S->>G: Quote(new) + plik
    B->>G: wycenia, termin, uwagi -> Quote(quoted)
    G-->>S: hook: oferta gotowa
    S-->>K: e-mail z ofertą i linkiem
    K->>S: akceptuje ofertę
    S->>G: intake.create_order_from_shop(quote) -> Order(new)
    Note over G,B: dalej jak w ścieżce 3
```

## Co jest nowe względem dzisiejszego GH

- `OrderFile`, `Quote`, pole na numer zamówienia sklepu i numer listu, status płatności "online".
- Klient zbiorczy "Sklep" albo `Client` bez NIP.
- Drugi plik ustawień i drugi proces gunicorn dla domen sklepów.
- Worker (dziś GH nie ma zadań w tle).
- Hook na zmianie statusu (dziś status zmienia się tylko w panelu i nigdzie nie wychodzi).
