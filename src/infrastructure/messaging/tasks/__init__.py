from src.infrastructure.messaging.tasks.backup_task import backup_database  # noqa: F401
from src.infrastructure.messaging.tasks.onboarding_email_tasks import (  # noqa: F401
    send_first_order_email_task,
    send_first_product_email_task,
    send_welcome_email_task,
)
from src.infrastructure.messaging.tasks.notification_tasks import (  # noqa: F401
    send_delivery_confirmation_email_task,
    send_order_confirmation_email_task,
    send_shipping_notification_email_task,
    send_whatsapp_delivery_confirmation_task,
    send_whatsapp_order_confirmation_task,
    send_whatsapp_shipping_update_task,
)
from src.infrastructure.messaging.tasks.slack_tasks import (  # noqa: F401
    process_slack_alert_queue,
    send_fraud_alert_task,
    send_payment_alert_task,
    send_slack_alert_task,
)
