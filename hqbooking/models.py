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


class Truck(models.Model):
    identifier = models.CharField(max_length=80, unique=True)
    model_template = models.ForeignKey(
        "TruckModelTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trucks",
    )
    tire_count = models.PositiveIntegerField(default=6)
    layout_model = models.CharField(max_length=30, default="BASCULANTE_10")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["identifier", "id"]

    def __str__(self):
        return self.identifier


class TruckTireChange(models.Model):
    truck = models.ForeignKey(Truck, on_delete=models.CASCADE, related_name="tire_changes")
    tire_number = models.PositiveIntegerField()
    tire_code = models.CharField(max_length=12, blank=True, null=True)
    changed_on = models.DateField(blank=True, null=True)
    odometer_km = models.PositiveIntegerField(blank=True, null=True)
    note = models.CharField(max_length=180, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("truck", "tire_number")
        ordering = ["tire_number", "id"]

    def __str__(self):
        return f"{self.truck.identifier} - Pneu {self.tire_number}"


class TruckTireChangeHistory(models.Model):
    truck = models.ForeignKey(Truck, on_delete=models.CASCADE, related_name="tire_history")
    tire_number = models.PositiveIntegerField()
    tire_code = models.CharField(max_length=12, blank=True, null=True)
    changed_on = models.DateField(blank=True, null=True)
    odometer_km = models.PositiveIntegerField(blank=True, null=True)
    note = models.CharField(max_length=180, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.truck.identifier} - {self.tire_code or self.tire_number}"


class TruckModelTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    axle_count = models.PositiveIntegerField(default=1)
    wheel_count = models.PositiveIntegerField(default=2)
    structure_json = models.TextField(default="[]")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name
