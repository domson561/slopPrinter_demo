"""
Szkieletowy produkt „gotowy PDF”: kupujący wgrywa plik gotowy do druku, sklep przekazuje go
do GH bez obróbki. Najmniejsza implementacja `apps.products.Product`, wzór dla nowego
produktu i dowód, że rdzeń nie zależy od plakatu.

Nie jest w `SHOP_PRODUCTS`: sklep demo go nie sprzedaje, włączają go testy. PDF od kupującego
sprawdza bramka plików (`apps.assets.gate`) przy wgrywaniu.
"""
