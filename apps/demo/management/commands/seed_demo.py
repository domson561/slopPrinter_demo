"""
seed_demo: konto operatora demo/demo, katalog, obrazki testowe i ok. 15 zamówień
rozłożonych po wszystkich stanach z pełną historią; jedno z reklamacją, jedno z dodrukiem,
dwa w kolejce moderacji. Zamówienia przechodzą przez te same serwisy, co w aplikacji.
"""

import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from huey.contrib.djhuey import HUEY

from apps.assets.models import Asset
from apps.catalog.models import DeliveryMethod
from apps.demo.images_gen import IMAGES_DIR, ensure_demo_images
from apps.orders import services as order_services
from apps.orders.models import Actor, Order
from apps.orders.payments import MockPaymentProvider
from apps.poster.models import Format, PaperOption
from apps.poster.services import save_poster_draft, upload_poster_asset
from apps.printing import services as print_services
from apps.printing.models import PollCursor, PrintJob

CUSTOMERS = [
    ("anna.kowalska@example.com", "Anna Kowalska", "ul. Długa 12/3", "00-238", "Warszawa"),
    ("piotr.nowak@example.com", "Piotr Nowak", "ul. Piwna 4", "31-041", "Kraków"),
    ("marta.wisniewska@example.com", "Marta Wiśniewska", "ul. Świdnicka 20/5", "50-068", "Wrocław"),
    ("tomasz.zielinski@example.com", "Tomasz Zieliński", "ul. Półwiejska 8", "61-888", "Poznań"),
    ("kasia.lewandowska@example.com", "Katarzyna Lewandowska", "ul. Długa 45", "80-827", "Gdańsk"),
    ("michal.wojcik@example.com", "Michał Wójcik", "ul. Piotrkowska 100/12", "90-004", "Łódź"),
]

# (obrazek, format, docelowy stan, dni temu, opcje)
SCENARIOS = [
    ("typical_1448.png", "A2", "awaiting_approval", 0, {}),
    ("large_3000.jpg", "B2", "awaiting_approval", 0, {"paper": "satin"}),
    ("logo_test.png", "A3", "on_hold", 1, {}),
    ("typical_1448.png", "A3", "sent_to_printer", 1, {"quantity": 2}),
    ("large_3000.jpg", "50x70", "sent_to_printer", 2, {"white_margin": True}),
    ("panorama_2048x768.jpg", "A2", "printed", 2, {}),
    ("typical_1448.png", "A2", "shipped", 3, {"paper": "satin"}),
    ("large_3000.jpg", "A2", "shipped", 4, {}),
    ("typical_1448.png", "A3", "delivered", 8, {}),
    ("large_3000.jpg", "A3", "delivered", 10, {"quantity": 3}),
    ("panorama_2048x768.jpg", "A3", "complaint", 12, {}),
    ("large_3000.jpg", "B2", "reprint", 5, {}),
    ("logo_test.png", "A2", "cancelled", 6, {}),
    ("small_512.png", "A3", "awaiting_payment", 0, {}),
    ("typical_1448.png", "50x70", "draft", 0, {}),
    ("large_3000.jpg", "A3", "processing_failed", 1, {}),
]


def _gh_mock():
    """Seed udaje biuro GH, więc wymaga atrapy; ta jest instalowana tylko pod GH_MOCK_ENABLED."""
    if not settings.GH_MOCK_ENABLED:
        raise CommandError("seed_demo wymaga atrapy GH: ustaw GH_MOCK_ENABLED=1.")
    from apps.gh_mock import services
    from apps.gh_mock.models import GhOrder

    return services, GhOrder


class Command(BaseCommand):
    help = "Ładuje dane demo: operator demo/demo, katalog, obrazki, ~15 zamówień we wszystkich stanach."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Najpierw usuń istniejące zamówienia, pliki i zlecenia.",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        call_command("loaddata", "catalog.json", "poster.json", verbosity=0)
        self._operator()
        _gh_mock()[0].ensure_demo_shop()
        ensure_demo_images()

        # Deterministycznie i szybko: zadania synchronicznie, mock upscale bez opóźnień.
        HUEY.immediate = True
        settings.MOCK_UPSCALE_DELAY_SECONDS = (0, 0)
        random.seed(42)

        for i, (image, fmt_code, target, days_ago, opts) in enumerate(SCENARIOS):
            customer = CUSTOMERS[i % len(CUSTOMERS)]
            try:
                order = self._make_order(image, fmt_code, target, customer, opts)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"[{i}] {image} {fmt_code} -> {target}: BŁĄD {exc}")
                raise
            self._backdate(order, days_ago)
            self.stdout.write(f"{order.number}: {image} {fmt_code} -> {order.status}")

        self.stdout.write(
            self.style.SUCCESS("Gotowe. Operator: demo / demo. Panel: /ops/, atrapa GH: /gh-mock/")
        )

    # ------------------------------------------------------------------

    def _flush(self):
        _gh_mock()[1].objects.all().delete()
        PollCursor.objects.all().delete()
        PrintJob.objects.all().delete()
        Order.objects.all().delete()
        for asset in Asset.objects.all():
            for v in asset.versions.all():
                v.file.delete(save=False)
            asset.original_file.delete(save=False)
            asset.delete()
        self.stdout.write("Wyczyszczono zamówienia, pliki i zlecenia.")

    def _operator(self):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username="demo",
            defaults={"is_staff": True, "is_superuser": True, "email": "demo@example.com"},
        )
        if created:
            user.set_password("demo")
            user.save()
        self.operator = user

    def _upload(self, image_name):
        data = (IMAGES_DIR / image_name).read_bytes()
        mime = "image/png" if image_name.endswith(".png") else "image/jpeg"
        return upload_poster_asset(SimpleUploadedFile(image_name, data, content_type=mime))

    def _make_order(self, image, fmt_code, target, customer, opts):
        asset = self._upload(image)
        fmt = Format.objects.get(code=fmt_code)
        paper = PaperOption.objects.get(code=opts.get("paper", "matte"))
        q = asset.analysis.quality(fmt_code)
        order = save_poster_draft(
            asset,
            fmt,
            paper,
            white_margin=opts.get("white_margin", False),
            quantity=opts.get("quantity", 1),
            crop=None,
            enhancer_enabled=opts.get("enhancer", q["enhancer_recommended"]),
        )
        if target == "draft":
            return order

        email, name, line1, postal, city = customer
        order_services.start_checkout(
            order,
            email=email,
            full_name=name,
            phone="+48 600 000 000",
            address={
                "line1": line1,
                "line2": "",
                "postal_code": postal,
                "city": city,
                "country": "PL",
            },
            delivery_method=DeliveryMethod.objects.get(
                code=random.choice(["courier", "parcel_locker"])
            ),
            consents={"terms": True, "no_withdrawal": True, "image_rights": True},
        )
        if target == "awaiting_payment":
            return order

        payment = order.payments.first()
        if target == "processing_failed":
            # Symulacja błędu pipeline: usuwamy oryginał przed przetworzeniem.
            asset.original_file.delete(save=True)
        MockPaymentProvider().process_callback(payment.external_id, "success")
        order.refresh_from_db()
        if target in ("awaiting_approval", "on_hold", "processing_failed"):
            return order

        if target == "cancelled":
            order_services.reject_order(
                order, self.operator, "Obrazek zawiera cudze logo, nie możemy tego wydrukować."
            )
            return order

        order_services.approve_order(order, Actor.OPERATOR, self.operator, "plik sprawdzony")
        order.refresh_from_db()
        job = PrintJob.objects.filter(order=order).first()
        self._gh(job, "in_progress")
        if target == "sent_to_printer":
            return order

        self._gh(job, "ready")
        if target == "printed":
            return order

        if target == "reprint":
            # Wadę zgłasza kupujący, dodruk zleca operator sklepu; GH o wadach nie wie.
            order.refresh_from_db()
            order_services.request_reprint(
                order, Actor.OPERATOR, "Smuga tuszu na 1/3 wysokości", self.operator
            )
            new_job = PrintJob.objects.filter(order=order, reprint_of=job).first()
            self._gh(new_job, "in_progress")
            return order

        self._gh(job, "shipped")
        if target == "shipped":
            return order

        order.refresh_from_db()
        order_services.mark_delivered(order, Actor.SYSTEM, reason="kurier potwierdził doręczenie")
        if target == "delivered":
            return order

        if target == "complaint":
            order.refresh_from_db()
            order_services.file_complaint(
                order, "Plakat przyszedł z zagiętym rogiem, widać załamanie na wydruku."
            )
            return order

        return order

    def _gh(self, job, target_status):
        """Biuro GH: pobiera pliki i przesuwa zlecenie do `target_status`; sklep odpytuje."""
        gh_services, GhOrder = _gh_mock()
        gh_order = GhOrder.objects.get(shop_order_number=job.shop_order_number)
        gh_services.download_pending_files()
        while gh_order.status != target_status and gh_order.next_status:
            gh_services.set_status(gh_order, gh_order.next_status)
        print_services.poll_changes()

    def _backdate(self, order, days_ago):
        if not days_ago:
            return
        base = timezone.now() - timedelta(days=days_ago, hours=random.randint(0, 6))
        with transaction.atomic():
            Order.objects.filter(pk=order.pk).update(created_at=base)
            events = list(order.status_events.order_by("created_at", "pk"))
            span = max(min(days_ago * 24 - 2, 72), 1)
            for i, e in enumerate(events):
                when = base + timedelta(hours=span * (i + 1) / (len(events) + 1))
                type(e).objects.filter(pk=e.pk).update(created_at=when)
