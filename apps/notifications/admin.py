from django.contrib import admin
from apps.notifications.models import EmailJob


@admin.register(EmailJob)
class EmailJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient_email",
        "template",
        "subject",
        "status",
        "token_preview",
        "created_at",
    )
    list_filter = ("status", "template", "created_at")
    search_fields = ("recipient_email", "subject")
    readonly_fields = ("id", "created_at", "updated_at")

    @admin.display(description="Token (Payload)")
    def token_preview(self, obj):
        if obj.payload and isinstance(obj.payload, dict):
            return obj.payload.get("token") or "-"
        return "-"
