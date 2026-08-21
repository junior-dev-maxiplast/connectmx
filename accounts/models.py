from django.db import models
from django.contrib.auth.models import AbstractUser
# Create your models here.

class User(AbstractUser):

    userId = models.CharField(max_length=20, unique=True)
    nameUser = models.CharField(max_length=150, null=True)
    id_sm = models.CharField(max_length=50, blank=True, null=True)
    id_erp = models.CharField(max_length=50, blank=True, null=True)
    is_representative = models.BooleanField(default=False)
    representative_code = models.CharField(max_length=30, blank=True, null=True, db_index=True)
    is_system_admin = models.BooleanField(default=False)
    # Contas exclusivas do Dashes entram com isto desmarcado: autenticam
    # normalmente, mas o middleware não as deixa abrir o ConnectMX interno.
    can_access_internal = models.BooleanField(default=True)
    last_access_at = models.DateTimeField(blank=True, null=True)
    last_data_change_at = models.DateTimeField(blank=True, null=True)
    # Teto diario de analises de IA no Dashes. Em branco = sem limite, que é o
    # comportamento que existia antes deste campo; 0 bloqueia todas.
    dashes_ai_daily_limit = models.PositiveIntegerField(blank=True, null=True)
    email = models.EmailField(unique=True)

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['userId', 'email']


class DashesAiUsage(models.Model):
    """Quantas analises de IA o usuario pediu em cada dia.

    Uma linha por usuario/dia: o contador precisa ser persistente para o limite
    valer entre sessoes e maquinas, e guardar por dia deixa o historico de
    consumo disponivel sem custo extra.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="dashes_ai_usages",
    )
    usage_date = models.DateField(db_index=True)
    request_count = models.PositiveIntegerField(default=0)
    last_request_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-usage_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "usage_date"],
                name="unique_dashes_ai_usage_per_day",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.usage_date} - {self.request_count}"
