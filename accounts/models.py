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
    email = models.EmailField(unique=True)

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['userId', 'email']
