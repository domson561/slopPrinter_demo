import pytest
from django.core.management import call_command

from orders.models import Complaint, Order, OrderStatus as S
from printing.models import MockPrinterJob, PrintJob


@pytest.mark.django_db
def test_seed_demo_covers_all_states(tmp_path, monkeypatch):
    from demo import images_gen

    monkeypatch.setattr(images_gen, "IMAGES_DIR", tmp_path / "images")
    from demo.management.commands import seed_demo

    monkeypatch.setattr(seed_demo, "IMAGES_DIR", tmp_path / "images")

    call_command("seed_demo", verbosity=0)

    statuses = set(Order.objects.values_list("status", flat=True))
    expected = {S.DRAFT, S.AWAITING_PAYMENT, S.AWAITING_APPROVAL, S.ON_HOLD, S.SENT_TO_PRINTER, S.PRINTED, S.SHIPPED, S.DELIVERED, S.CANCELLED}
    assert expected <= statuses
    assert Order.objects.count() >= 15
    assert Order.objects.filter(status__in=[S.AWAITING_APPROVAL, S.ON_HOLD]).count() >= 3
    assert Complaint.objects.filter(status=Complaint.Status.OPEN).count() == 1
    assert PrintJob.objects.filter(reprint_of__isnull=False).count() == 1
    assert MockPrinterJob.objects.count() >= 7
    assert Order.objects.filter(hold_reason__icontains="Błąd przetwarzania").exists()

    # Ponowne uruchomienie z --flush działa i nie duplikuje operatora.
    call_command("seed_demo", "--flush", verbosity=0)
    from django.contrib.auth import get_user_model

    assert get_user_model().objects.filter(username="demo").count() == 1
