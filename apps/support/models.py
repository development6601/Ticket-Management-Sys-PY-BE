from django.db import models
from django.conf import settings

# Create your models here.
from apps.accounts.models import BaseModel

class SupportTeam(BaseModel):
    name = models.CharField(
        max_length=150,
        unique=True
    )
    description = models.TextField(
        blank=True,
    )

    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name = "team_manager",
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(
        default = True,
    )

    def __str__(self):
        return self.name

class TeamMember(BaseModel):
    team = models.ForeignKey(
        SupportTeam,
        on_delete=models.PROTECT, 
        related_name = "members",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name = "support_team_member",
    )

    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True,)

    class Meta : 
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user"],
                name = "unique_team_member",
            ),
        ]

    def __str__(self):
        return f"{self.team.name} - {self.user.email}"