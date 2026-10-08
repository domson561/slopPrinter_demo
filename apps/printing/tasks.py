"""
Auto-symulacja drukarni: zlecenia przechodzą accepted -> printed -> shipped co N sekund.
Domyślnie wyłączona (na prezentacji klika się ręcznie). Dwa mechanizmy:
- pulpit D1 odpytuje /mock-printer/tick/ co N sekund, gdy przełącznik jest włączony (bez workera),
- w trybie z workerem huey dodatkowo zadanie okresowe co minutę.
"""

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.catalog.models import ShopSettings

from .models import MockPrinterJob

_NEXT = {
    MockPrinterJob.Status.RECEIVED: "accepted",
    MockPrinterJob.Status.ACCEPTED: "printed",
    MockPrinterJob.Status.PRINTED: "shipped",
}


def advance_all_mock_jobs(request=None) -> int:
    from .views import send_printer_event

    advanced = 0
    for mock_job in MockPrinterJob.objects.filter(status__in=list(_NEXT.keys())).order_by(
        "created_at"
    ):
        send_printer_event(
            mock_job, _NEXT[mock_job.status], reason="auto-symulacja", request=request
        )
        advanced += 1
    return advanced


@db_periodic_task(crontab(minute="*"))
def auto_simulate_printer():
    if not ShopSettings.get().printer_auto_simulate:
        return 0
    return advance_all_mock_jobs()
