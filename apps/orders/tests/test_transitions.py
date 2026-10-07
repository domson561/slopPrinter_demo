import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from apps.orders import services
from apps.orders.models import Actor, OrderStatus as S
from apps.orders.services import NOTIFY_STATUSES, TRANSITIONS, TransitionError

ALL = list(S.values)


@pytest.mark.django_db
@pytest.mark.parametrize("from_status", ALL)
@pytest.mark.parametrize("to_status", ALL)
def test_transition_table_is_enforced(bare_order, from_status, to_status):
    order = bare_order(status=from_status)
    allowed = to_status in TRANSITIONS[from_status]
    if allowed:
        event = services.transition(order, to_status, Actor.SYSTEM, "test")
        order.refresh_from_db()
        assert order.status == to_status
        assert event.from_status == from_status and event.to_status == to_status
        assert not event.forced
    else:
        with pytest.raises(TransitionError):
            services.transition(order, to_status, Actor.SYSTEM, "test")
        order.refresh_from_db()
        assert order.status == from_status
        assert order.status_events.count() == 0


@pytest.mark.django_db
def test_table_matches_seed_diagram():
    assert TRANSITIONS[S.DRAFT] == {S.AWAITING_PAYMENT}
    assert TRANSITIONS[S.AWAITING_PAYMENT] == {S.PAID, S.CANCELLED}
    assert TRANSITIONS[S.ON_HOLD] == {S.PROCESSING, S.AWAITING_APPROVAL, S.CANCELLED}
    assert TRANSITIONS[S.PRINTED] == {S.SHIPPED, S.REPRINT}
    assert TRANSITIONS[S.DELIVERED] == {S.REPRINT}
    assert TRANSITIONS[S.CANCELLED] == set()


@pytest.mark.django_db
def test_force_transition_requires_reason_and_is_logged(bare_order):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    order = bare_order(status=S.SHIPPED)
    with pytest.raises(TransitionError):
        services.force_transition(order, S.ON_HOLD, user, "")
    event = services.force_transition(order, S.ON_HOLD, user, "paczka wróciła")
    order.refresh_from_db()
    assert order.status == S.ON_HOLD
    assert event.forced and event.actor == Actor.OPERATOR and event.actor_user == user
    assert order.hold_reason == "paczka wróciła"


@pytest.mark.django_db
def test_hold_reason_cleared_when_leaving_on_hold(bare_order):
    order = bare_order(status=S.PROCESSING)
    services.transition(order, S.ON_HOLD, Actor.SYSTEM, "błąd")
    assert order.hold_reason == "błąd"
    services.transition(order, S.AWAITING_APPROVAL, Actor.OPERATOR, "ok")
    assert order.hold_reason == ""


@pytest.mark.django_db
def test_customer_visible_transitions_send_email(bare_order):
    order = bare_order(status=S.AWAITING_PAYMENT)
    services.transition(order, S.PAID, Actor.SYSTEM)
    assert len(mail.outbox) == 1
    assert order.number in mail.outbox[0].subject
    assert order.get_absolute_status_url() in mail.outbox[0].body
    services.transition(order, S.PROCESSING, Actor.SYSTEM)  # nie w NOTIFY_STATUSES
    assert len(mail.outbox) == 1
    assert S.PROCESSING not in NOTIFY_STATUSES


@pytest.mark.django_db
def test_every_status_has_customer_label():
    for status in S.values:
        title, description = services.customer_label(status)
        assert title and description
