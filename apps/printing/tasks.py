"""Polling GH co 5 minut (kontrakt pkt 5). Bez workera: przycisk „Odpytaj GH” w panelu operatora."""

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from .services import poll_changes


@db_periodic_task(crontab(minute="*/5"))
def poll_gh_changes():
    return poll_changes()
