from django.utils import timezone

from apps.notifications.models import EmailJob
from apps.notifications.services.email import EmailService


def process_email_job(job_id):
    job = EmailJob.objects.get(id=job_id)

    if job.status != "PENDING":
        return

    job.status = "PROCESSING"
    job.attempts += 1
    job.save(update_fields=["status", "attempts", "updated_at"])

    try:
        if job.template == "customer_email_change":
            email_data = EmailService.build_email_change_email(
                token=job.payload["token"]
            )
        else:
            email_data = EmailService.build_verification_email(
                token=job.payload["token"]
            )

        # Send email here using your configured email provider.
        # Example:
        #
        # send_mail(
        #     subject=email_data["subject"],
        #     message=email_data["body"],
        #     from_email=settings.DEFAULT_FROM_EMAIL,
        #     recipient_list=[job.recipient_email],
        # )

        job.status = "SENT"
        job.sent_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "sent_at",
                "updated_at",
            ]
        )

    except Exception as exc:
        job.status = "FAILED"
        job.failed_at = timezone.now()
        job.error_message = str(exc)

        job.save(
            update_fields=[
                "status",
                "failed_at",
                "error_message",
                "updated_at",
            ]
        )

        raise
