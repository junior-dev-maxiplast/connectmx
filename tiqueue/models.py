from django.db import models

# Create your models here.

class TaskGroup(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self):
        return self.name


class TaskType(models.Model):
    group = models.ForeignKey(TaskGroup, on_delete=models.CASCADE, related_name="types")
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default="#5CD6A3")

    class Meta:
        unique_together = ("group", "name")

    def __str__(self):
        return f"{self.group.name} - {self.name}"


class UserQueueKanbanColumn(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="queue_kanban_columns")
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default="#343955")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("user", "name")

    def __str__(self):
        return f"{self.user.username} - {self.name}"


class userQueue(models.Model):

    user_code = models.CharField(max_length=10)
    n_register = models.AutoField(primary_key=True)
    a_ticket = models.CharField(max_length=30, blank=True, null=True)
    f_conclusion_rate = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    n_status_code = models.IntegerField(blank=True, null=True)
    a_description = models.CharField(max_length=250, blank=True, null=True)
    n_type_group = models.IntegerField(blank=True, null=True)
    n_type_code = models.IntegerField(blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.SET_NULL, null=True, blank=True)
    task_type = models.ForeignKey(TaskType, on_delete=models.SET_NULL, null=True, blank=True)
    linked_project = models.ForeignKey("Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_items")
    linked_roadmap_item = models.ForeignKey(
        "ProjectRoadmapItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_items"
    )
    kanban_column = models.ForeignKey(
        UserQueueKanbanColumn, on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_items"
    )
    d_predicted_date_start = models.DateField(blank=True, null=True)
    d_predicted_date_end = models.DateField(blank=True, null=True)
    t_predicted_time_start = models.TimeField(blank=True, null=True)
    t_predicted_time_end = models.TimeField(blank=True, null=True)
    f_total_predicted_time = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    d_real_date_start = models.DateField(blank=True, null=True)
    d_real_date_end = models.DateField(blank=True, null=True)
    d_real_time_start = models.TimeField(blank=True, null=True)
    t_real_time_end = models.TimeField(blank=True, null=True)
    f_total_real_time = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    f_predicted_real_diference = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    n_queue_position = models.IntegerField(blank=True, null=True)
    is_current = models.BooleanField(default=False)
    
    def __str__(self):
        return self.a_description

class concludedTasks(models.Model):

    user_code = models.CharField(max_length=10)
    n_register = models.AutoField(primary_key=True)
    a_ticket = models.CharField(max_length=30, blank=True, null=True)
    f_conclusion_rate = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    n_status_code = models.IntegerField(blank=True, null=True)
    a_description = models.CharField(max_length=250, blank=True, null=True)
    n_type_group = models.IntegerField(blank=True, null=True)
    n_type_code = models.IntegerField(blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.SET_NULL, null=True, blank=True)
    task_type = models.ForeignKey(TaskType, on_delete=models.SET_NULL, null=True, blank=True)
    linked_project = models.ForeignKey(
        "Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="concluded_queue_items"
    )
    linked_roadmap_item = models.ForeignKey(
        "ProjectRoadmapItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="concluded_queue_items"
    )
    d_predicted_date_start = models.DateField(blank=True, null=True)
    d_predicted_date_end = models.DateField(blank=True, null=True)
    t_predicted_time_start = models.TimeField(blank=True, null=True)
    t_predicted_time_end = models.TimeField(blank=True, null=True)
    f_total_predicted_time = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    d_real_date_start = models.DateField(blank=True, null=True)
    d_real_date_end = models.DateField(blank=True, null=True)
    d_real_time_start = models.TimeField(blank=True, null=True)
    t_real_time_end = models.TimeField(blank=True, null=True)
    f_total_real_time = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    f_predicted_real_diference = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    n_queue_position = models.IntegerField(blank=True, null=True)
    d_conclusion_date = models.DateField(auto_now=True)
    d_conclusion_time = models.TimeField(auto_now=True)
    
    def __str__(self):
        return self.a_description


class QueueTaskDetail(models.Model):
    queue_item = models.ForeignKey(userQueue, on_delete=models.CASCADE, related_name="details")
    description = models.CharField(max_length=240)
    is_done = models.BooleanField(default=False)
    duration_hours = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.description


class Project(models.Model):
    STATUS_CHOICES = [
        ("planned", "Planejado"),
        ("active", "Em andamento"),
        ("paused", "Pausado"),
        ("done", "Concluido"),
    ]

    name = models.CharField(max_length=140, unique=True)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="active")
    color = models.CharField(max_length=7, default="#00bf63")
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ProjectRoadmapItem(models.Model):
    STATUS_CHOICES = [
        ("planned", "Planejado"),
        ("doing", "Em execucao"),
        ("blocked", "Bloqueado"),
        ("done", "Concluido"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="roadmap_items")
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="planned")
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.project.name} - {self.title}"


class ProjectKanbanColumn(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="kanban_columns")
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default="#343955")
    sort_order = models.IntegerField(default=0)

    class Meta:
        unique_together = ("project", "name")
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.project.name} - {self.name}"


class ProjectKanbanCard(models.Model):
    PRIORITY_CHOICES = [
        (1, "Baixa"),
        (2, "Media"),
        (3, "Alta"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="kanban_cards")
    column = models.ForeignKey(ProjectKanbanColumn, on_delete=models.CASCADE, related_name="cards")
    roadmap_item = models.OneToOneField(
        ProjectRoadmapItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kanban_card",
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True, null=True)
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=2)
    due_date = models.DateField(blank=True, null=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.project.name} - {self.title}"


class ChecklistTemplate(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ChecklistSection(models.Model):
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField(max_length=160)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.template.name} - {self.title}"


class ChecklistChoiceGroup(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class ChecklistChoiceOption(models.Model):
    group = models.ForeignKey(ChecklistChoiceGroup, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=120)

    class Meta:
        unique_together = ("group", "label")

    def __str__(self):
        return f"{self.group.name} - {self.label}"


class ChecklistField(models.Model):
    FIELD_TYPES = [
        ("text", "Texto curto"),
        ("textarea", "Texto longo"),
        ("number", "Numero"),
        ("date", "Data"),
        ("time", "Hora"),
        ("boolean", "Sim/Nao"),
        ("single_choice", "Escolha unica"),
        ("multi_choice", "Multipla escolha"),
    ]

    section = models.ForeignKey(ChecklistSection, on_delete=models.CASCADE, related_name="fields")
    label = models.CharField(max_length=200)
    help_text = models.CharField(max_length=240, blank=True, null=True)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default="text")
    required = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    choice_group = models.ForeignKey(
        ChecklistChoiceGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="fields"
    )

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.label


class ChecklistEntry(models.Model):
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.PROTECT, related_name="entries")
    title = models.CharField(max_length=160, blank=True, null=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or f"Checklist {self.id}"


class ChecklistAnswer(models.Model):
    entry = models.ForeignKey(ChecklistEntry, on_delete=models.CASCADE, related_name="answers")
    field = models.ForeignKey(ChecklistField, on_delete=models.CASCADE, related_name="answers")
    value_text = models.TextField(blank=True, null=True)
    value_number = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    value_date = models.DateField(blank=True, null=True)
    value_time = models.TimeField(blank=True, null=True)
    value_bool = models.BooleanField(blank=True, null=True)
    selected_options = models.ManyToManyField(ChecklistChoiceOption, blank=True)

    class Meta:
        unique_together = ("entry", "field")

    def __str__(self):
        return f"{self.entry} - {self.field.label}"


class SeniorSystemUpdate(models.Model):
    erp_version = models.CharField(max_length=40)
    hcm_version = models.CharField(max_length=40)
    sde_version = models.CharField(max_length=40)
    folder_name = models.CharField(max_length=180, blank=True, null=True)

    release_date = models.DateField(blank=True, null=True)
    download_date = models.DateField(blank=True, null=True)
    planned_apply_date = models.DateField(blank=True, null=True)
    real_apply_date = models.DateField(blank=True, null=True)

    in_production = models.BooleanField(default=False)
    in_test_base = models.BooleanField(default=False)
    in_simulation_base = models.BooleanField(default=False)
    sent_to_drive = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-release_date", "-created_at", "-id"]

    def __str__(self):
        return f"ERP {self.erp_version} | HCM {self.hcm_version} | SDE {self.sde_version}"


class WifiVoucherGroup(models.Model):
    name = models.CharField(max_length=80, blank=True, null=True)
    duration_hours = models.PositiveIntegerField()
    expected_quantity = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["duration_hours", "id"]

    def __str__(self):
        return self.display_label

    @property
    def display_label(self):
        if self.name:
            return self.name
        suffix = "hora" if self.duration_hours == 1 else "horas"
        return f"Vouchers: {self.duration_hours} {suffix}"


class WifiVoucher(models.Model):
    STATUS_AVAILABLE = "available"
    STATUS_PENDING = "pending"
    STATUS_USED = "used"
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Disponivel"),
        (STATUS_PENDING, "Pendente"),
        (STATUS_USED, "Usado"),
    ]

    group = models.ForeignKey(WifiVoucherGroup, on_delete=models.CASCADE, related_name="vouchers")
    voucher_code = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    inserted_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["group__duration_hours", "status", "voucher_code", "id"]

    def __str__(self):
        return f"{self.voucher_code} ({self.get_status_display()})"


class HubToolCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]

    def __str__(self):
        return self.name


class HubTool(models.Model):
    category = models.ForeignKey(HubToolCategory, on_delete=models.CASCADE, related_name="tools")
    name = models.CharField(max_length=120)
    link = models.URLField(max_length=500)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        unique_together = ("category", "name")

    def __str__(self):
        return f"{self.category.name} - {self.name}"


class HubUserTool(models.Model):
    category = models.ForeignKey(
        "HubUserToolCategory",
        on_delete=models.CASCADE,
        related_name="tools",
        null=True,
        blank=True,
    )
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="hub_tools")
    category_name = models.CharField(max_length=80, default="Meu HUB")
    name = models.CharField(max_length=120)
    link = models.URLField(max_length=500)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category_name", "sort_order", "name", "id"]
        unique_together = ("user", "category_name", "name")

    def __str__(self):
        cat = self.category.name if self.category_id else self.category_name
        return f"{self.user.username} - {cat} - {self.name}"


class HubUserToolCategory(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="hub_tool_categories")
    name = models.CharField(max_length=80)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        unique_together = ("user", "name")

    def __str__(self):
        return f"{self.user.username} - {self.name}"


class KnowledgeCategory(models.Model):
    name = models.CharField(max_length=90, unique=True)
    description = models.TextField(blank=True, null=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]

    def __str__(self):
        return self.name


class KnowledgeEntry(models.Model):
    category = models.ForeignKey(KnowledgeCategory, on_delete=models.PROTECT, related_name="entries")
    title = models.CharField(max_length=180)
    trigger = models.TextField(help_text="Situacao/problema que gerou a anotacao.")
    description = models.TextField(help_text="Descricao detalhada do problema.")
    impact = models.TextField(blank=True, null=True)
    workaround = models.TextField(blank=True, null=True)
    root_cause = models.TextField(blank=True, null=True)
    resolution = models.TextField(blank=True, null=True)
    tags = models.CharField(max_length=220, blank=True, null=True)
    is_resolved = models.BooleanField(default=False)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    inserted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-inserted_at", "-id"]

    def __str__(self):
        return f"{self.category.name} - {self.title}"


class KnowledgeEntryAttachment(models.Model):
    entry = models.ForeignKey(KnowledgeEntry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="knowledge_base/")
    original_name = models.CharField(max_length=255, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_name or self.file.name
