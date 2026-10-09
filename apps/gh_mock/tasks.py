"""
Auto-symulacja biura GH (domyślnie wyłączona, na prezentacji klika się ręcznie):
pulpit odpytuje /gh-mock/tick/ co N sekund, gdy przełącznik jest włączony; z workerem
huey dodatkowo zadanie okresowe co minutę.
"""

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from .models import GhMockSettings
from .services import advance_all


@db_periodic_task(crontab(minute="*"))
def auto_simulate_gh():
    if not GhMockSettings.get().auto_simulate:
        return 0
    return advance_all()
