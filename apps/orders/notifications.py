"""Powiadomienia e-mail do klienta. W demo backend konsolowy (log)."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

log = logging.getLogger("slop.notifications")


def _send(order, subject, template, context):
    if not order.email:
        log.info("Brak e-maila dla %s, pomijam powiadomienie", order.number)
        return
    body = render_to_string(
        template,
        {
            "order": order,
            "site_name": settings.SITE_NAME,
            "status_url": order.get_absolute_status_url(),
            **context,
        },
    )
    send_mail(
        f"[{settings.SITE_NAME}] {subject}",
        body,
        settings.DEFAULT_FROM_EMAIL,
        [order.email],
        fail_silently=True,
    )


def send_status_email(order, event):
    from .services import customer_label

    title, description = customer_label(event.to_status)
    shipment = order.latest_shipment if event.to_status == "shipped" else None
    _send(
        order,
        f"Zamówienie {order.number}: {title}",
        "emails/status_change.txt",
        {"title": title, "description": description, "event": event, "shipment": shipment},
    )


def send_complaint_received_email(order, complaint):
    _send(
        order,
        f"Zamówienie {order.number}: przyjęliśmy reklamację",
        "emails/complaint_received.txt",
        {"complaint": complaint},
    )


def send_complaint_rejected_email(order, complaint):
    _send(
        order,
        f"Zamówienie {order.number}: odpowiedź na reklamację",
        "emails/complaint_rejected.txt",
        {"complaint": complaint},
    )
