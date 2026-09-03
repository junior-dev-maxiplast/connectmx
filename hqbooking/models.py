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


class SimulationRomaneioEntry(models.Model):
    SYNC_PENDING = "pending"
    SYNC_SUCCESS = "success"
    SYNC_ERROR = "error"
    # A embalagem já estava contabilizada na USU_TCONROM. É recusa definitiva,
    # não é falha: guardamos a tentativa para ficar registrado quem tentou
    # recontar o mesmo pallet, e por isso ela tem status próprio em vez de cair
    # em `error` junto com problema de rede ou de driver.
    SYNC_DUPLICATE = "duplicate"
    SYNC_STATUS_CHOICES = [
        (SYNC_PENDING, "Pendente"),
        (SYNC_SUCCESS, "Enviado"),
        (SYNC_ERROR, "Erro"),
        (SYNC_DUPLICATE, "Já contabilizado"),
    ]

    company_code = models.CharField(max_length=20)
    branch_code = models.CharField(max_length=20)
    sequence_record = models.CharField(max_length=40)
    user_code = models.CharField(max_length=40)
    generated_date = models.DateField()
    generated_time = models.TimeField()
    volume_quantity = models.PositiveIntegerField()
    romaneio_weight = models.DecimalField(max_digits=14, decimal_places=3)
    # USU_NUMEMB: o código do pallet impresso na etiqueta. É a chave da contagem
    # — cada embalagem entra uma única vez, e é por este campo que a segunda
    # leitura é recusada. É NUMBER(9) no Oracle (só dígitos); guardado aqui
    # como texto já normalizado por `_parse_romaneio_numeric_code` (sem zero
    # à esquerda) para casar com o valor que o Oracle vai armazenar.
    package_code = models.CharField(max_length=40, blank=True, default="", db_index=True)
    # USU_CODEND: endereçamento que veio na etiqueta. NUMBER(6) no Oracle,
    # mesma normalização do package_code.
    address_code = models.CharField(max_length=40, blank=True, default="")
    barcode_payload = models.TextField(blank=True, null=True)
    # Identificador gerado pelo ConnectMX Mobile para cada leitura da fila local.
    # Existe porque o app reenvia sozinho quando a rede volta: se a resposta do
    # INSERT se perder no caminho, o retry chegaria como um romaneio novo e
    # dobraria a contagem de pallets. Com ele o servidor reconhece a repetição e
    # devolve o registro que já gravou. Vazio nos lançamentos feitos pela web.
    client_reference = models.CharField(max_length=64, blank=True, default="", db_index=True)
    sync_status = models.CharField(max_length=10, choices=SYNC_STATUS_CHOICES, default=SYNC_PENDING)
    sync_message = models.CharField(max_length=255, blank=True, null=True)
    synced_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # Trava de verdade contra a corrida: duas leituras da mesma
            # embalagem partindo ao mesmo tempo passariam pela checagem em
            # `_find_duplicate_romaneio_entry` antes de qualquer uma commitar.
            # O índice único garante que só a primeira vira "success" mesmo
            # nesse caso; a segunda esbarra no IntegrityError e é convertida em
            # SYNC_DUPLICATE por quem chamou. Vazio fica de fora: lançamentos
            # antigos sem `package_code` não podem colidir entre si.
            models.UniqueConstraint(
                fields=["package_code"],
                condition=models.Q(sync_status="success") & ~models.Q(package_code=""),
                name="uq_romaneio_package_success",
            ),
        ]

    def __str__(self):
        return f"{self.company_code}/{self.branch_code} - {self.sequence_record}"


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


class Tire(models.Model):
    STATUS_STOCK = "stock"
    STATUS_INSTALLED = "installed"
    STATUS_RETREADING = "retreading"
    STATUS_DISCARDED = "discarded"
    STATUS_CHOICES = [
        (STATUS_STOCK, "Estoque"),
        (STATUS_INSTALLED, "Instalado"),
        (STATUS_RETREADING, "Em recapagem"),
        (STATUS_DISCARDED, "Descartado"),
    ]

    brand = models.CharField(max_length=80)
    tire_model = models.CharField(max_length=80, blank=True, null=True)
    serial_number = models.CharField(max_length=40, unique=True)
    # DOT e sulco descrevem o pneu fisico: o primeiro identifica o lote de
    # fabricacao, o segundo e a medida que decide quando ele sai de operacao.
    dot_code = models.CharField(max_length=20, blank=True, null=True)
    size_spec = models.CharField(max_length=40, blank=True, null=True)
    groove_depth_mm = models.DecimalField(max_digits=5, decimal_places=1, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_STOCK, db_index=True)
    recap_count = models.PositiveSmallIntegerField(default=0)
    purchase_value = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    last_retread_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    total_retread_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    current_truck = models.ForeignKey(
        Truck,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tires",
    )
    current_tire_number = models.PositiveIntegerField(blank=True, null=True)
    current_slot_label = models.CharField(max_length=60, blank=True, null=True)
    registered_on = models.DateField(blank=True, null=True)
    discarded_on = models.DateField(blank=True, null=True)
    notes = models.CharField(max_length=220, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["serial_number", "id"]

    def __str__(self):
        return f"{self.serial_number} - {self.brand}"


class TruckTireChange(models.Model):
    truck = models.ForeignKey(Truck, on_delete=models.CASCADE, related_name="tire_changes")
    tire_number = models.PositiveIntegerField()
    tire = models.ForeignKey(
        Tire,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="truck_assignments",
    )
    tire_code = models.CharField(max_length=40, blank=True, null=True)
    tire_brand = models.CharField(max_length=80, blank=True, null=True)
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
    tire = models.ForeignKey(
        Tire,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="truck_history_entries",
    )
    tire_code = models.CharField(max_length=40, blank=True, null=True)
    tire_brand = models.CharField(max_length=80, blank=True, null=True)
    changed_on = models.DateField(blank=True, null=True)
    odometer_km = models.PositiveIntegerField(blank=True, null=True)
    previous_tire_code = models.CharField(max_length=40, blank=True, null=True)
    previous_tire_brand = models.CharField(max_length=80, blank=True, null=True)
    previous_changed_on = models.DateField(blank=True, null=True)
    previous_odometer_km = models.PositiveIntegerField(blank=True, null=True)
    run_days = models.PositiveIntegerField(blank=True, null=True)
    run_km = models.PositiveIntegerField(blank=True, null=True)
    action_type = models.CharField(max_length=20, blank=True, null=True)
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


class TireMovement(models.Model):
    TYPE_REGISTER = "register"
    TYPE_INSTALL = "install"
    TYPE_REPOSITION = "reposition"
    TYPE_TO_STOCK = "to_stock"
    TYPE_TO_RETREAD = "to_retread"
    TYPE_FROM_RETREAD = "from_retread"
    TYPE_RETREAD = "retread"
    TYPE_DISCARD = "discard"
    TYPE_CHOICES = [
        (TYPE_REGISTER, "Cadastro"),
        (TYPE_INSTALL, "Instalacao"),
        (TYPE_REPOSITION, "Reposicionamento"),
        (TYPE_TO_STOCK, "Movido para estoque"),
        (TYPE_TO_RETREAD, "Enviado para recapagem"),
        (TYPE_FROM_RETREAD, "Retorno da recapagem"),
        (TYPE_RETREAD, "Recape"),
        (TYPE_DISCARD, "Descarte"),
    ]

    tire = models.ForeignKey(Tire, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_REGISTER)
    truck = models.ForeignKey(
        Truck,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tire_movements",
    )
    tire_number = models.PositiveIntegerField(blank=True, null=True)
    position_label = models.CharField(max_length=60, blank=True, null=True)
    movement_date = models.DateField(blank=True, null=True)
    odometer_km = models.PositiveIntegerField(blank=True, null=True)
    movement_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    note = models.CharField(max_length=220, blank=True, null=True)
    # Comprovacao visual do movimento (hoje usada no descarte, para registrar o
    # estado do pneu que justificou a baixa).
    photo = models.ImageField(upload_to="tires/movements/%Y/%m/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.tire.serial_number} - {self.movement_type}"
