from django.db import models


class HeadquartersEnvironment(models.Model):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=200, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class HeadquartersDateBlock(models.Model):
    blocked_date = models.DateField(unique=True)
    reason = models.CharField(max_length=200, blank=True, null=True)
    blocked_by = models.CharField(max_length=20, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["blocked_date", "id"]

    def __str__(self):
        return f"{self.blocked_date} ({self.reason or 'Indisponivel'})"


class HeadquartersReservation(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendente"),
        (STATUS_APPROVED, "Aprovada"),
        (STATUS_REJECTED, "Recusada"),
    ]

    reserved_date = models.DateField()
    employee_id = models.CharField(max_length=20)
    start_time = models.TimeField(blank=True, null=True)
    end_time = models.TimeField(blank=True, null=True)
    reason = models.CharField(max_length=180, blank=True, null=True)
    environments = models.ManyToManyField(HeadquartersEnvironment, blank=True, related_name="reservations")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["reserved_date", "id"]

    def __str__(self):
        return f"{self.reserved_date} - {self.employee_id}"


class LunchReservation(models.Model):
    employee_id = models.CharField(max_length=20)
    reserved_date = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reserved_date", "-created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["employee_id", "reserved_date"], name="uq_lunch_employee_date"),
        ]

    def __str__(self):
        return f"{self.employee_id} - {self.reserved_date}"
