from .models import DeliveryMethod


def active_deliveries():
    return DeliveryMethod.objects.filter(is_active=True)
