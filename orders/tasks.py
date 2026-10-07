from huey.contrib.djhuey import db_task


@db_task()
def process_order_task(order_id):
    from .models import Order
    from .services import process_order

    order = Order.objects.get(pk=order_id)
    process_order(order)
