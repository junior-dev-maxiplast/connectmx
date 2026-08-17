import os
import secrets
import string

from django.db import models
from django.urls import reverse
from django.utils.text import slugify

# Create your models here.

class TaskGroup(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self):
        return self.name


class SystemConfig(models.Model):
    system_version = models.CharField(max_length=40, blank=True, null=True)
    service_agent_url = models.CharField(max_length=255, blank=True, null=True)
    service_agent_token = models.CharField(max_length=255, blank=True, null=True)
    service_agent_timeout_sec = models.PositiveIntegerField(default=8)
    openai_enabled = models.BooleanField(default=False)
    openai_api_key_encrypted = models.TextField(blank=True, null=True)
    openai_base_url = models.CharField(max_length=255, default="https://api.openai.com/v1")
    openai_model = models.CharField(max_length=80, default="gpt-5.6-sol")
    openai_reasoning_effort = models.CharField(max_length=20, default="medium")
    openai_timeout_sec = models.PositiveIntegerField(default=120)
    openai_max_output_tokens = models.PositiveIntegerField(default=5000)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.system_version or "Sem versao"


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


class UserQueueCustomColumn(models.Model):
    FIELD_TEXT = "text"
    FIELD_SELECT = "select"
    FIELD_NUMBER = "number"
    FIELD_DATE = "date"
    FIELD_CHECKBOX = "checkbox"
    FIELD_TYPE_CHOICES = [
        (FIELD_TEXT, "Texto"),
        (FIELD_SELECT, "Lista"),
        (FIELD_NUMBER, "Numero"),
        (FIELD_DATE, "Data"),
        (FIELD_CHECKBOX, "Checkbox"),
    ]

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="queue_custom_columns")
    name = models.CharField(max_length=60)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default=FIELD_TEXT)
    color = models.CharField(max_length=7, default="#61688c")
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("user", "name")

    def __str__(self):
        return f"{self.user.username} - {self.name}"


class UserQueueCustomColumnOption(models.Model):
    column = models.ForeignKey(UserQueueCustomColumn, on_delete=models.CASCADE, related_name="options")
    value = models.CharField(max_length=40)
    label = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default="#61688c")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("column", "value")

    def __str__(self):
        return f"{self.column.name} - {self.label}"


class UserQueueFieldOption(models.Model):
    FIELD_PRIORITY = "priority_level"
    FIELD_EFFORT = "estimated_effort_level"
    FIELD_CHOICES = [
        (FIELD_PRIORITY, "Prioridade"),
        (FIELD_EFFORT, "Nivel estimado"),
    ]

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="queue_field_options")
    field_key = models.CharField(max_length=40, choices=FIELD_CHOICES)
    value = models.CharField(max_length=40)
    label = models.CharField(max_length=60)
    color = models.CharField(max_length=7, default="#61688c")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("user", "field_key", "value")

    def __str__(self):
        return f"{self.user.username} - {self.field_key} - {self.label}"


class UserQueueSavedView(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="queue_saved_views")
    name = models.CharField(max_length=80)
    filters_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        unique_together = ("user", "name")

    def __str__(self):
        return f"{self.user.username} - {self.name}"


class userQueue(models.Model):
    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    FIELD_PRIORITY = UserQueueFieldOption.FIELD_PRIORITY
    DEFAULT_PRIORITY_OPTIONS = [
        (PRIORITY_LOW, "Baixa", "#2d8f66"),
        (PRIORITY_MEDIUM, "Media", "#ad8d2e"),
        (PRIORITY_HIGH, "Alta", "#b14f59"),
    ]

    ESTIMATE_SMALL = "small"
    ESTIMATE_MEDIUM = "medium"
    ESTIMATE_LARGE = "large"
    FIELD_EFFORT = UserQueueFieldOption.FIELD_EFFORT
    DEFAULT_EFFORT_OPTIONS = [
        (ESTIMATE_SMALL, "Pequeno", "#2f9d9c"),
        (ESTIMATE_MEDIUM, "Medio", "#4b668f"),
        (ESTIMATE_LARGE, "Grande", "#7d5fb0"),
    ]

    user_code = models.CharField(max_length=10)
    n_register = models.AutoField(primary_key=True)
    a_ticket = models.CharField(max_length=30, blank=True, null=True)
    f_conclusion_rate = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    n_status_code = models.IntegerField(blank=True, null=True)
    a_description = models.CharField(max_length=250, blank=True, null=True)
    a_demand_detail = models.TextField(blank=True, null=True)
    a_notes = models.TextField(blank=True, null=True)
    priority_level = models.CharField(max_length=40, default=PRIORITY_MEDIUM)
    estimated_effort_level = models.CharField(max_length=40, default=ESTIMATE_MEDIUM)
    n_type_group = models.IntegerField(blank=True, null=True)
    n_type_code = models.IntegerField(blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.SET_NULL, null=True, blank=True)
    task_type = models.ForeignKey(TaskType, on_delete=models.SET_NULL, null=True, blank=True)
    linked_project = models.ForeignKey("Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_items")
    linked_roadmap_item = models.ForeignKey(
        "ProjectRoadmapItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_items"
    )
    extra_collaborators = models.ManyToManyField(
        "accounts.User",
        blank=True,
        related_name="extra_queue_collaborations",
    )
    kanban_column = models.ForeignKey(
        UserQueueKanbanColumn, on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_items"
    )
    kanban_sort_order = models.IntegerField(default=0)
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
    
    @classmethod
    def default_field_options(cls, field_key):
        if field_key == cls.FIELD_PRIORITY:
            return cls.DEFAULT_PRIORITY_OPTIONS
        if field_key == cls.FIELD_EFFORT:
            return cls.DEFAULT_EFFORT_OPTIONS
        return []

    @classmethod
    def default_field_option_map(cls, field_key):
        return {
            value: {"value": value, "label": label, "color": color}
            for value, label, color in cls.default_field_options(field_key)
        }

    def get_priority_level_display(self):
        return (
            self.default_field_option_map(self.FIELD_PRIORITY).get(self.priority_level or "", {}).get("label")
            or self.priority_level
            or "-"
        )

    def get_estimated_effort_level_display(self):
        return (
            self.default_field_option_map(self.FIELD_EFFORT).get(self.estimated_effort_level or "", {}).get("label")
            or self.estimated_effort_level
            or "-"
        )

    def __str__(self):
        return self.a_description


class UserQueueCustomValue(models.Model):
    queue_item = models.ForeignKey(userQueue, on_delete=models.CASCADE, related_name="custom_values")
    column = models.ForeignKey(UserQueueCustomColumn, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=250, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("queue_item", "column")

    def __str__(self):
        return f"{self.queue_item_id} - {self.column_id}"

class concludedTasks(models.Model):

    user_code = models.CharField(max_length=10)
    n_register = models.AutoField(primary_key=True)
    a_ticket = models.CharField(max_length=30, blank=True, null=True)
    f_conclusion_rate = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    n_status_code = models.IntegerField(blank=True, null=True)
    a_description = models.CharField(max_length=250, blank=True, null=True)
    priority_level = models.CharField(max_length=40, default=userQueue.PRIORITY_MEDIUM)
    estimated_effort_level = models.CharField(max_length=40, default=userQueue.ESTIMATE_MEDIUM)
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
    extra_collaborators = models.ManyToManyField(
        "accounts.User",
        blank=True,
        related_name="extra_concluded_queue_collaborations",
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
    
    def get_priority_level_display(self):
        return (
            userQueue.default_field_option_map(userQueue.FIELD_PRIORITY).get(self.priority_level or "", {}).get("label")
            or self.priority_level
            or "-"
        )

    def get_estimated_effort_level_display(self):
        return (
            userQueue.default_field_option_map(userQueue.FIELD_EFFORT).get(self.estimated_effort_level or "", {}).get("label")
            or self.estimated_effort_level
            or "-"
        )

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


class DemandTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=240, blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="demand_templates")
    task_type = models.ForeignKey(TaskType, on_delete=models.SET_NULL, null=True, blank=True, related_name="demand_templates")
    linked_project = models.ForeignKey("Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="demand_templates")
    predicted_start_offset_hours = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    predicted_end_offset_hours = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DemandTemplateDetail(models.Model):
    template = models.ForeignKey(DemandTemplate, on_delete=models.CASCADE, related_name="details")
    description = models.CharField(max_length=240)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.description


class PortalDemandCustomField(models.Model):
    FIELD_TEXT = "text"
    FIELD_TEXTAREA = "textarea"
    FIELD_SELECT = "select"
    FIELD_NUMBER = "number"
    FIELD_DATE = "date"
    FIELD_CHECKBOX = "checkbox"
    FIELD_TYPE_CHOICES = [
        (FIELD_TEXT, "Texto"),
        (FIELD_TEXTAREA, "Texto longo"),
        (FIELD_SELECT, "Lista"),
        (FIELD_NUMBER, "Numero"),
        (FIELD_DATE, "Data"),
        (FIELD_CHECKBOX, "Checkbox"),
    ]

    label = models.CharField(max_length=80, unique=True)
    field_key = models.SlugField(max_length=80, unique=True, blank=True)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default=FIELD_TEXT)
    placeholder = models.CharField(max_length=160, blank=True, null=True)
    help_text = models.CharField(max_length=240, blank=True, null=True)
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    task_groups = models.ManyToManyField(TaskGroup, blank=True, related_name="portal_custom_fields")
    task_types = models.ManyToManyField(TaskType, blank=True, related_name="portal_custom_fields")
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_demand_custom_fields",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def save(self, *args, **kwargs):
        base_key = slugify(self.field_key or self.label or "") or "campo-portal"
        candidate = base_key[:80]
        suffix = 2
        while PortalDemandCustomField.objects.exclude(pk=self.pk).filter(field_key=candidate).exists():
            candidate = f"{base_key[:72]}-{suffix}"
            suffix += 1
        self.field_key = candidate[:80]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.label


class PortalDemandCustomFieldOption(models.Model):
    field = models.ForeignKey(PortalDemandCustomField, on_delete=models.CASCADE, related_name="options")
    value = models.CharField(max_length=40)
    label = models.CharField(max_length=80)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("field", "value")

    def __str__(self):
        return f"{self.field.label} - {self.label}"


class PortalDemandSlaPolicy(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=240, blank=True, null=True)
    task_group = models.ForeignKey(
        TaskGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_sla_policies",
    )
    task_type = models.ForeignKey(
        TaskType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_sla_policies",
    )
    priority_level = models.CharField(max_length=40, blank=True, null=True)
    first_response_minutes = models.PositiveIntegerField(default=60)
    resolution_minutes = models.PositiveIntegerField(default=480)
    default_attendant = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_default_sla_policies",
    )
    auto_assign_on_create = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class PortalCannedResponse(models.Model):
    title = models.CharField(max_length=120)
    message = models.TextField()
    task_group = models.ForeignKey(
        TaskGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_canned_responses",
    )
    task_type = models.ForeignKey(
        TaskType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_canned_responses",
    )
    suggest_internal = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_canned_responses",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "title", "id"]

    def __str__(self):
        return self.title


class PortalRequesterSector(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class PortalRequesterCollaborator(models.Model):
    sector = models.ForeignKey(PortalRequesterSector, on_delete=models.PROTECT, related_name="collaborators")
    full_name = models.CharField(max_length=160)
    registration_code = models.CharField(max_length=40, unique=True)
    email = models.EmailField(unique=True)
    role_title = models.CharField(max_length=120, blank=True, null=True)
    phone = models.CharField(max_length=40, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "id"]

    def __str__(self):
        return f"{self.full_name} ({self.sector.name})"


class PortalRequesterAccount(models.Model):
    collaborator = models.OneToOneField(
        PortalRequesterCollaborator,
        on_delete=models.CASCADE,
        related_name="portal_account",
    )
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="portal_requester_account",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_portal_requester_accounts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["collaborator__full_name", "id"]

    def __str__(self):
        return f"{self.collaborator.full_name} -> {self.user.username}"


class PortalDemand(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ASSUMED = "assumed"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendente"),
        (STATUS_ASSUMED, "Em atendimento"),
        (STATUS_COMPLETED, "Concluída"),
        (STATUS_CANCELLED, "Cancelada"),
    ]
    FEEDBACK_CHOICES = [
        (1, "Muito insatisfeito"),
        (2, "Insatisfeito"),
        (3, "Neutro"),
        (4, "Satisfeito"),
        (5, "Muito satisfeito"),
    ]

    requester = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="portal_demands")
    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_portal_demands",
    )
    linked_queue_item = models.ForeignKey(
        userQueue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_sources",
    )
    access_code = models.CharField(max_length=9, unique=True, blank=True, null=True, db_index=True)
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.SET_NULL, null=True, blank=True)
    task_type = models.ForeignKey(TaskType, on_delete=models.SET_NULL, null=True, blank=True)
    priority_level = models.CharField(max_length=40, default=userQueue.PRIORITY_MEDIUM)
    sla_policy = models.ForeignKey(
        PortalDemandSlaPolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="demands",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    first_response_due_at = models.DateTimeField(blank=True, null=True)
    first_response_at = models.DateTimeField(blank=True, null=True)
    resolution_due_at = models.DateTimeField(blank=True, null=True)
    assumed_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    feedback_rating = models.PositiveSmallIntegerField(blank=True, null=True, choices=FEEDBACK_CHOICES)
    feedback_comment = models.TextField(blank=True, null=True)
    feedback_submitted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @classmethod
    def generate_access_code(cls):
        while True:
            candidate = f"{secrets.randbelow(1000):03d}-" + "".join(
                secrets.choice(string.ascii_uppercase) for _ in range(5)
            )
            if not cls.objects.filter(access_code=candidate).exists():
                return candidate

    def ensure_access_code(self, commit=True):
        if self.access_code:
            return self.access_code

        self.access_code = self.generate_access_code()
        if commit and self.pk:
            self.__class__.objects.filter(pk=self.pk).update(access_code=self.access_code)
        return self.access_code

    def save(self, *args, **kwargs):
        self.ensure_access_code(commit=False)
        super().save(*args, **kwargs)

    @property
    def protocol(self):
        if not self.pk:
            return "PORTAL-NOVO"
        return self.ensure_access_code(commit=True)

    def get_absolute_url(self):
        return reverse("portalDemandCodeDetailPage", args=[self.protocol])

    def get_priority_level_display(self):
        return (
            userQueue.default_field_option_map(userQueue.FIELD_PRIORITY).get(self.priority_level or "", {}).get("label")
            or self.priority_level
            or "-"
        )

    def get_priority_level_color(self):
        return (
            userQueue.default_field_option_map(userQueue.FIELD_PRIORITY).get(self.priority_level or "", {}).get("color")
            or "#61688c"
        )

    @property
    def has_feedback(self):
        return bool(self.feedback_rating)

    def get_feedback_rating_label(self):
        return dict(self.FEEDBACK_CHOICES).get(self.feedback_rating or 0, "-")

    def __str__(self):
        return f"{self.protocol} - {self.title}"


class PortalDemandCustomValue(models.Model):
    demand = models.ForeignKey(PortalDemand, on_delete=models.CASCADE, related_name="custom_values")
    field = models.ForeignKey(PortalDemandCustomField, on_delete=models.CASCADE, related_name="values")
    value = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["field__sort_order", "field__id", "id"]
        unique_together = ("demand", "field")

    def __str__(self):
        return f"{self.demand.protocol} - {self.field.label}"


class PortalDemandMessage(models.Model):
    ROLE_REQUESTER = "requester"
    ROLE_ATTENDANT = "attendant"
    ROLE_SYSTEM = "system"
    ROLE_CHOICES = [
        (ROLE_REQUESTER, "Solicitante"),
        (ROLE_ATTENDANT, "Atendente"),
        (ROLE_SYSTEM, "Sistema"),
    ]

    demand = models.ForeignKey(PortalDemand, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_demand_messages",
    )
    author_name = models.CharField(max_length=160, blank=True, null=True)
    author_role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_REQUESTER)
    canned_response = models.ForeignKey(
        "PortalCannedResponse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    is_internal = models.BooleanField(default=False)
    message = models.TextField(blank=True, null=True)
    work_started_at = models.DateTimeField(blank=True, null=True)
    work_ended_at = models.DateTimeField(blank=True, null=True)
    worked_minutes = models.PositiveIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if self.work_started_at and self.work_ended_at:
            delta_seconds = max(int((self.work_ended_at - self.work_started_at).total_seconds()), 0)
            self.worked_minutes = delta_seconds // 60
        else:
            self.worked_minutes = None
        super().save(*args, **kwargs)

    @property
    def display_author_name(self):
        if self.author_name:
            return self.author_name
        if self.author_id:
            raw_name = getattr(self.author, "nameUser", "") or getattr(self.author, "username", "")
            if raw_name:
                return raw_name
        if self.author_role == self.ROLE_SYSTEM:
            return "Sistema"
        return "Usuário"

    @property
    def has_worklog(self):
        return bool(self.work_started_at and self.work_ended_at and self.worked_minutes is not None)

    @property
    def worked_time_display(self):
        total_minutes = int(self.worked_minutes or 0)
        hours, minutes = divmod(total_minutes, 60)
        if hours and minutes:
            return f"{hours}h {minutes:02d}min"
        if hours:
            return f"{hours}h"
        return f"{minutes}min"

    def __str__(self):
        return f"{self.demand.protocol} - {self.display_author_name}"


class PortalDemandLog(models.Model):
    EVENT_ASSUMED = "assumed"
    EVENT_TRANSFERRED = "transferred"
    EVENT_WORKLOG = "worklog"
    EVENT_COMPLETED = "completed"
    EVENT_CANCELLED = "cancelled"
    EVENT_AI_ROUTED = "ai_routed"
    EVENT_CHOICES = [
        (EVENT_ASSUMED, "Assunção"),
        (EVENT_TRANSFERRED, "Transferência"),
        (EVENT_WORKLOG, "Apontamento"),
        (EVENT_COMPLETED, "Conclusão"),
        (EVENT_CANCELLED, "Cancelamento"),
        (EVENT_AI_ROUTED, "Roteamento IA"),
    ]

    demand = models.ForeignKey(PortalDemand, on_delete=models.CASCADE, related_name="logs")
    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_demand_logs",
    )
    actor_name = models.CharField(max_length=160, blank=True, null=True)
    event_type = models.CharField(max_length=20, choices=EVENT_CHOICES)
    summary = models.CharField(max_length=255)
    details = models.TextField(blank=True, null=True)
    from_attendant = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_transfer_logs_from",
    )
    to_attendant = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_transfer_logs_to",
    )
    related_message = models.ForeignKey(
        "PortalDemandMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def display_actor_name(self):
        if self.actor_name:
            return self.actor_name
        if self.actor_id:
            raw_name = getattr(self.actor, "nameUser", "") or getattr(self.actor, "username", "")
            if raw_name:
                return raw_name
        return "Sistema"

    def __str__(self):
        return f"{self.demand.protocol} - {self.get_event_type_display()}"


class PortalDemandAttachment(models.Model):
    demand = models.ForeignKey(PortalDemand, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(
        PortalDemandMessage,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_demand_attachments",
    )
    uploaded_by_name = models.CharField(max_length=160, blank=True, null=True)
    original_name = models.CharField(max_length=255, blank=True, null=True)
    file = models.FileField(upload_to="portal_demands/%Y/%m/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    @property
    def display_name(self):
        return self.original_name or os.path.basename(self.file.name or "") or "arquivo"

    def __str__(self):
        return f"{self.demand.protocol} - {self.display_name}"


class Project(models.Model):
    STATUS_CHOICES = [
        ("planned", "Planejado"),
        ("active", "Em andamento"),
        ("paused", "Pausado"),
        ("done", "Concluido"),
    ]

    name = models.CharField(max_length=140, unique=True)
    description = models.TextField(blank=True, null=True)
    developer = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    participants = models.ManyToManyField("accounts.User", blank=True, related_name="project_participations")
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
    responsible = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roadmap_responsibilities",
    )
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


class ProjectRoadmapSubtask(models.Model):
    roadmap_item = models.ForeignKey(ProjectRoadmapItem, on_delete=models.CASCADE, related_name="subtasks")
    description = models.CharField(max_length=240)
    is_done = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.roadmap_item.title} - {self.description}"


class ProjectMilestone(models.Model):
    MILESTONE_PLANNING = "planning"
    MILESTONE_ANALYSIS = "analysis"
    MILESTONE_DEVELOPMENT = "development"
    MILESTONE_VALIDATION = "validation"
    MILESTONE_HOMOLOGATION = "homologation"
    MILESTONE_DEPLOYMENT = "deployment"
    MILESTONE_DELIVERY = "delivery"
    MILESTONE_CHOICES = [
        (MILESTONE_PLANNING, "Planejamento"),
        (MILESTONE_ANALYSIS, "Analise"),
        (MILESTONE_DEVELOPMENT, "Desenvolvimento"),
        (MILESTONE_VALIDATION, "Validacao interna"),
        (MILESTONE_HOMOLOGATION, "Homologacao"),
        (MILESTONE_DEPLOYMENT, "Implantacao"),
        (MILESTONE_DELIVERY, "Entrega"),
    ]
    MILESTONE_DEFAULTS = {
        MILESTONE_PLANNING: {"title": "Planejamento", "color": "#7A8DBB"},
        MILESTONE_ANALYSIS: {"title": "Analise", "color": "#5CD6A3"},
        MILESTONE_DEVELOPMENT: {"title": "Desenvolvimento", "color": "#4A7DFF"},
        MILESTONE_VALIDATION: {"title": "Validacao interna", "color": "#45C0BE"},
        MILESTONE_HOMOLOGATION: {"title": "Homologacao", "color": "#F5B55E"},
        MILESTONE_DEPLOYMENT: {"title": "Implantacao", "color": "#FF8B5D"},
        MILESTONE_DELIVERY: {"title": "Entrega", "color": "#B392FF"},
    }

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    anchor_item = models.ForeignKey(
        ProjectRoadmapItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attached_milestones",
    )
    milestone_key = models.CharField(max_length=40, choices=MILESTONE_CHOICES, default=MILESTONE_ANALYSIS)
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True, null=True)
    target_date = models.DateField(blank=True, null=True)
    color = models.CharField(max_length=7, default="#5CD6A3")
    is_done = models.BooleanField(default=False)
    completed_at = models.DateTimeField(blank=True, null=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "target_date", "id"]

    @classmethod
    def template_defaults(cls, milestone_key):
        return cls.MILESTONE_DEFAULTS.get(milestone_key or "", cls.MILESTONE_DEFAULTS[cls.MILESTONE_ANALYSIS]).copy()

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


class ContractRecord(models.Model):
    reference_month = models.CharField(max_length=20, blank=True, null=True)
    company = models.CharField(max_length=80, blank=True, null=True)
    cnpj = models.CharField(max_length=30, blank=True, null=True)
    supplier = models.CharField(max_length=180, blank=True, null=True)
    invoice_number = models.CharField(max_length=60, blank=True, null=True)
    issue_date = models.DateField(blank=True, null=True)
    due_date = models.DateField(blank=True, null=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    item = models.CharField(max_length=80, blank=True, null=True)
    request_code = models.CharField(max_length=80, blank=True, null=True)
    contract_code = models.CharField(max_length=80, blank=True, null=True)
    transaction_type = models.CharField(max_length=120, blank=True, null=True)
    cost_center = models.CharField(max_length=160, blank=True, null=True)
    observation = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.company or '-'} - {self.supplier or '-'}"


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


class MaintenanceType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default="#343955")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class MaintenanceSituation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class MaintenanceIndicator(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_incident = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class MaintenanceSystemGroup(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class MaintenanceSystem(models.Model):
    group = models.ForeignKey(MaintenanceSystemGroup, on_delete=models.CASCADE, related_name="systems")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["group__name", "name", "id"]
        unique_together = ("group", "name")

    def __str__(self):
        return f"{self.group.name} - {self.name}"


class MaintenanceEvent(models.Model):
    title = models.CharField(max_length=180)
    short_description = models.CharField(max_length=280, blank=True, null=True)
    full_description = models.TextField(blank=True, null=True)
    maintenance_type = models.ForeignKey(
        MaintenanceType, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    situation = models.ForeignKey(
        MaintenanceSituation, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    indicator = models.ForeignKey(
        MaintenanceIndicator, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    system_group = models.ForeignKey(
        MaintenanceSystemGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    affected_systems = models.ManyToManyField(MaintenanceSystem, blank=True, related_name="events")
    scheduled_start = models.DateTimeField()
    expected_return = models.DateTimeField(blank=True, null=True)
    real_return = models.DateTimeField(blank=True, null=True)
    is_outage = models.BooleanField(default=False)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_start", "-id"]

    def __str__(self):
        return self.title


class MyAgendaReminder(models.Model):
    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Baixa"),
        (PRIORITY_MEDIUM, "Média"),
        (PRIORITY_HIGH, "Alta"),
    ]

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="agenda_reminders")
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, blank=True, null=True)
    reminder_date = models.DateField()
    reminder_time = models.TimeField(blank=True, null=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    is_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["reminder_date", "reminder_time", "id"]

    def __str__(self):
        return f"{self.user.username} - {self.title}"


class MaxiTetrisHighScore(models.Model):
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="maxi_tetris_highscore",
    )
    best_score = models.PositiveIntegerField(default=0)
    best_lines = models.PositiveIntegerField(default=0)
    best_level = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-best_score", "-best_lines", "-best_level", "id"]

    def __str__(self):
        display_name = self.user.nameUser or self.user.username
        return f"{display_name} - {self.best_score}"


class PomodoroSession(models.Model):
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="pomodoro_sessions",
    )
    focus_minutes = models.PositiveIntegerField(default=25)
    break_minutes = models.PositiveIntegerField(default=5)
    accomplishment = models.CharField(max_length=240, blank=True, default="")
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_at", "-id"]

    def __str__(self):
        display_name = self.user.nameUser or self.user.username
        return f"{display_name} - {self.focus_minutes}min - {self.completed_at:%d/%m %H:%M}"


class DataModelLaunch(models.Model):
    name = models.CharField(max_length=140, unique=True)
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.name


class DataModelTable(models.Model):
    launch = models.ForeignKey(DataModelLaunch, on_delete=models.CASCADE, related_name="tables")
    name = models.CharField(max_length=140)
    x = models.IntegerField(default=40)
    y = models.IntegerField(default=40)
    color = models.CharField(max_length=7, default="#343955")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        unique_together = ("launch", "name")

    def __str__(self):
        return f"{self.launch.name} - {self.name}"


class DataModelField(models.Model):
    TYPE_CHOICES = [
        ("int", "INT"),
        ("bigint", "BIGINT"),
        ("varchar", "VARCHAR"),
        ("text", "TEXT"),
        ("date", "DATE"),
        ("datetime", "DATETIME"),
        ("bool", "BOOLEAN"),
        ("decimal", "DECIMAL"),
        ("float", "FLOAT"),
        ("json", "JSON"),
    ]

    table = models.ForeignKey(DataModelTable, on_delete=models.CASCADE, related_name="fields")
    name = models.CharField(max_length=140)
    data_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="varchar")
    size = models.CharField(max_length=30, blank=True, null=True)
    is_primary = models.BooleanField(default=False)
    is_nullable = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("table", "name")

    def __str__(self):
        return f"{self.table.name}.{self.name}"


class DataModelRelation(models.Model):
    REL_CHOICES = [
        ("1:1", "1:1"),
        ("1:N", "1:N"),
        ("N:1", "N:1"),
        ("N:N", "N:N"),
    ]

    launch = models.ForeignKey(DataModelLaunch, on_delete=models.CASCADE, related_name="relations")
    source_field = models.ForeignKey(DataModelField, on_delete=models.CASCADE, related_name="source_relations")
    target_field = models.ForeignKey(DataModelField, on_delete=models.CASCADE, related_name="target_relations")
    relation_type = models.CharField(max_length=3, choices=REL_CHOICES, default="1:N")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.source_field} -> {self.target_field}"


class SystemNotification(models.Model):
    LEVEL_INFO = "info"
    LEVEL_WARNING = "warning"
    LEVEL_ERROR = "error"
    LEVEL_CHOICES = [
        (LEVEL_INFO, "Info"),
        (LEVEL_WARNING, "Aviso"),
        (LEVEL_ERROR, "Erro"),
    ]

    source_key = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=180)
    message = models.TextField()
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=LEVEL_WARNING)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return self.title


class CustomerInsightSnapshot(models.Model):
    STATUS_PREPARED = "prepared"
    STATUS_PROCESSING = "processing"
    STATUS_SENT = "sent"
    STATUS_COMPLETED = "completed"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_PREPARED, "Preparado"),
        (STATUS_PROCESSING, "Processando na IA"),
        (STATUS_SENT, "Enviado para IA"),
        (STATUS_COMPLETED, "Concluído"),
        (STATUS_ERROR, "Erro"),
    ]

    customer_code = models.BigIntegerField(db_index=True)
    customer_name = models.CharField(max_length=240)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PREPARED)
    source_period_start = models.DateField(blank=True, null=True)
    source_period_end = models.DateField(blank=True, null=True)
    source_fingerprint = models.CharField(max_length=64, db_index=True)
    source_row_count = models.PositiveIntegerField(default=0)
    metrics = models.JSONField(default=dict)
    insight_cards = models.JSONField(default=list)
    ai_payload = models.JSONField(default=dict)
    ai_response = models.JSONField(default=dict, blank=True)
    ai_provider = models.CharField(max_length=40, blank=True, null=True)
    ai_model = models.CharField(max_length=80, blank=True, null=True)
    ai_response_id = models.CharField(max_length=120, blank=True, null=True)
    ai_input_tokens = models.PositiveIntegerField(default=0)
    ai_output_tokens = models.PositiveIntegerField(default=0)
    ai_total_tokens = models.PositiveIntegerField(default=0)
    ai_error = models.TextField(blank=True, null=True)
    ai_requested_at = models.DateTimeField(blank=True, null=True)
    ai_completed_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="customer_insight_snapshots",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["customer_code", "source_fingerprint"],
                name="unique_customer_insight_source",
            )
        ]

    def __str__(self):
        return f"{self.customer_code} - {self.customer_name} - {self.source_period_end or '-'}"


class Dashboard(models.Model):
    """Catálogo dos painéis do ConnectMX Dashes.

    Um painel novo é um registro aqui: a sidebar do Dashes e a tela de
    permissões do Cadastro interno se montam a partir desta tabela, sem
    precisar de migration nem de alteração de template.
    """

    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=200, blank=True, null=True)
    url_name = models.CharField(
        max_length=120,
        help_text="Nome da rota Django que abre o painel (ex.: dashesCustomerDnaPage).",
    )
    icon_path = models.TextField(
        blank=True,
        null=True,
        help_text="Conteúdo do <svg> exibido na sidebar do Dashes.",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name", "id"]

    def __str__(self):
        return self.name


class DashboardAccess(models.Model):
    """Quem pode abrir qual painel. Sem registro aqui, não há acesso."""

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="dashboard_accesses",
    )
    dashboard = models.ForeignKey(
        Dashboard,
        on_delete=models.CASCADE,
        related_name="accesses",
    )
    granted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="granted_dashboard_accesses",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["dashboard__display_order", "dashboard__name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "dashboard"], name="unique_user_dashboard_access"),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.dashboard.slug}"
