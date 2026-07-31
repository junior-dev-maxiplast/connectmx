from django import forms
from django.db.models import Q

from accounts.models import User

from .forms import (
    MultipleFileField,
    MultipleFileInput,
    PortalDemandFeedbackForm,
    PortalDemandForm as BasePortalDemandForm,
    PortalDemandTransferForm,
    TaskTypeSelect,
)
from .models import (
    PortalCannedResponse,
    PortalDemandCustomField,
    PortalRequesterAccount,
    PortalRequesterCollaborator,
    PortalRequesterSector,
    PortalDemandSlaPolicy,
    TaskGroup,
    TaskType,
    userQueue,
)


class PortalDemandForm(BasePortalDemandForm):
    pass


class PortalDemandReplyForm(forms.Form):
    canned_response = forms.ModelChoiceField(
        queryset=PortalCannedResponse.objects.none(),
        required=False,
        label="Resposta pronta",
        empty_label="Selecione uma resposta pronta",
        widget=forms.Select(attrs={"class": "queue-select"}),
    )
    message = forms.CharField(
        required=False,
        label="Nova mensagem",
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "Adicione uma atualização, resposta ou solicitação complementar.",
            }
        ),
    )
    attachments = MultipleFileField(
        required=False,
        label="Anexos",
        widget=MultipleFileInput(attrs={"multiple": True}),
    )
    is_internal = forms.BooleanField(
        required=False,
        label="Nota interna (somente atendentes)",
        widget=forms.CheckboxInput(attrs={"class": "modern-checkbox-input"}),
    )
    work_started_at = forms.DateTimeField(
        required=False,
        label="Início do trabalho",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    work_ended_at = forms.DateTimeField(
        required=False,
        label="Fim do trabalho",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )

    def __init__(self, *args, demand=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.demand = demand
        self.user = user
        self.can_manage = bool(
            user
            and getattr(user, "is_authenticated", False)
            and (getattr(user, "is_system_admin", False) or getattr(user, "is_superuser", False))
        )

        queryset = PortalCannedResponse.objects.filter(is_active=True).select_related("task_group", "task_type")
        demand_group_id = getattr(demand, "task_group_id", None)
        demand_type_id = getattr(demand, "task_type_id", None)
        if demand_type_id:
            queryset = queryset.filter(Q(task_type_id=demand_type_id) | Q(task_type__isnull=True))
        if demand_group_id:
            queryset = queryset.filter(Q(task_group_id=demand_group_id) | Q(task_group__isnull=True))
        queryset = queryset.order_by("sort_order", "title", "id")
        self.fields["canned_response"].queryset = queryset if self.can_manage else PortalCannedResponse.objects.none()

    def clean(self):
        cleaned = super().clean()
        message = (cleaned.get("message") or "").strip()
        canned_response = cleaned.get("canned_response")
        work_started_at = cleaned.get("work_started_at")
        work_ended_at = cleaned.get("work_ended_at")
        attachments = []
        if hasattr(self, "files") and self.files is not None:
            attachments = [f for f in self.files.getlist("attachments") if getattr(f, "name", "")]

        if canned_response and not message:
            message = (canned_response.message or "").strip()

        if cleaned.get("is_internal") and not self.can_manage:
            self.add_error("is_internal", "Somente atendentes podem registrar notas internas.")
        if bool(work_started_at) != bool(work_ended_at):
            raise forms.ValidationError("Informe início e fim do trabalho para registrar o tempo apontado.")
        if work_started_at and work_ended_at and work_ended_at < work_started_at:
            self.add_error("work_ended_at", "O fim do trabalho deve ser posterior ao início.")
        if not message and not attachments and not (work_started_at and work_ended_at):
            raise forms.ValidationError("Informe uma mensagem, anexe um arquivo ou registre o tempo trabalhado.")

        cleaned["message"] = message
        return cleaned


class PortalRequesterSectorForm(forms.ModelForm):
    class Meta:
        model = PortalRequesterSector
        fields = ["name", "description", "is_active"]
        labels = {
            "name": "Nome do setor",
            "description": "Descrição",
            "is_active": "Setor ativo",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ex.: Comercial, Logística, RH"}),
            "description": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Explique rapidamente qual área irá abrir demandas para o TI.",
                }
            ),
        }

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()


class PortalRequesterCollaboratorForm(forms.ModelForm):
    class Meta:
        model = PortalRequesterCollaborator
        fields = ["sector", "full_name", "registration_code", "email", "role_title", "phone", "notes", "is_active"]
        labels = {
            "sector": "Setor",
            "full_name": "Nome completo",
            "registration_code": "Matrícula",
            "email": "E-mail",
            "role_title": "Cargo",
            "phone": "Telefone",
            "notes": "Observações",
            "is_active": "Colaborador ativo",
        }
        widgets = {
            "sector": forms.Select(attrs={"class": "queue-select"}),
            "full_name": forms.TextInput(attrs={"placeholder": "Ex.: João da Silva"}),
            "registration_code": forms.TextInput(attrs={"placeholder": "Ex.: 10025"}),
            "email": forms.EmailInput(attrs={"placeholder": "nome@empresa.com.br"}),
            "role_title": forms.TextInput(attrs={"placeholder": "Ex.: Coordenador, Analista, Assistente"}),
            "phone": forms.TextInput(attrs={"placeholder": "Ex.: (47) 99999-9999"}),
            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Informações adicionais úteis sobre esse solicitante.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sector"].queryset = PortalRequesterSector.objects.order_by("name")

    def clean_full_name(self):
        return (self.cleaned_data.get("full_name") or "").strip()

    def clean_registration_code(self):
        return (self.cleaned_data.get("registration_code") or "").strip()

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_role_title(self):
        return (self.cleaned_data.get("role_title") or "").strip()

    def clean_phone(self):
        return (self.cleaned_data.get("phone") or "").strip()

    def clean_notes(self):
        return (self.cleaned_data.get("notes") or "").strip()


class PortalRequesterAccountForm(forms.Form):
    collaborator = forms.ModelChoiceField(
        queryset=PortalRequesterCollaborator.objects.none(),
        label="Colaborador",
        widget=forms.Select(attrs={"class": "queue-select"}),
    )
    username = forms.CharField(
        max_length=150,
        label="Usuário de acesso",
        widget=forms.TextInput(attrs={"placeholder": "Ex.: joao.silva"}),
    )
    password = forms.CharField(
        required=False,
        label="Senha",
        widget=forms.PasswordInput(attrs={"placeholder": "Informe uma senha inicial"}),
    )
    is_active = forms.BooleanField(required=False, label="Acesso ativo")

    def __init__(self, *args, account=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.account = account
        available = PortalRequesterCollaborator.objects.select_related("sector").order_by("full_name")
        if account:
            available = available.filter(Q(portal_account__isnull=True) | Q(pk=account.collaborator_id))
            self.initial.setdefault("collaborator", account.collaborator_id)
            self.initial.setdefault("username", account.user.username)
            self.initial.setdefault("is_active", account.is_active)
        else:
            available = available.filter(portal_account__isnull=True)
            self.initial.setdefault("is_active", True)
        self.fields["collaborator"].queryset = available

    def clean_username(self):
        return (self.cleaned_data.get("username") or "").strip()

    def clean(self):
        cleaned = super().clean()
        collaborator = cleaned.get("collaborator")
        username = cleaned.get("username")
        password = (cleaned.get("password") or "").strip()

        if not self.account and not password:
            self.add_error("password", "Informe a senha inicial para criar o acesso.")
        existing_account = None
        if collaborator:
            existing_account = PortalRequesterAccount.objects.filter(collaborator=collaborator).first()
        if existing_account and (not self.account or existing_account.id != self.account.id):
            self.add_error("collaborator", "Este colaborador já possui um usuário de acesso vinculado.")
        if collaborator and not getattr(collaborator.sector, "is_active", False):
            self.add_error("collaborator", "O setor selecionado está inativo. Reative-o antes de criar o acesso.")
        if collaborator and not getattr(collaborator, "is_active", False):
            self.add_error("collaborator", "O colaborador selecionado está inativo.")
        if not username:
            self.add_error("username", "Informe o usuário de acesso.")
        cleaned["password"] = password
        return cleaned


class PortalDemandCustomFieldCreateForm(forms.Form):
    label = forms.CharField(max_length=80, label="Nome do campo")
    field_type = forms.ChoiceField(
        choices=PortalDemandCustomField.FIELD_TYPE_CHOICES,
        label="Tipo do campo",
        widget=forms.Select(attrs={"class": "queue-select"}),
    )
    placeholder = forms.CharField(max_length=160, required=False, label="Placeholder")
    help_text = forms.CharField(max_length=240, required=False, label="Texto de apoio")
    task_groups = forms.ModelMultipleChoiceField(
        queryset=TaskGroup.objects.none(),
        required=False,
        label="Exibir para grupos",
        widget=forms.SelectMultiple(attrs={"class": "queue-select", "size": 5}),
    )
    task_types = forms.ModelMultipleChoiceField(
        queryset=TaskType.objects.none(),
        required=False,
        label="Exibir para tipos",
        widget=forms.SelectMultiple(attrs={"class": "queue-select", "size": 5}),
    )
    is_required = forms.BooleanField(required=False, label="Campo obrigatório")
    initial_option_label = forms.CharField(max_length=80, required=False, label="Primeira opção da lista")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task_groups"].queryset = TaskGroup.objects.order_by("name")
        self.fields["task_types"].queryset = TaskType.objects.select_related("group").order_by("group__name", "name")

    def clean_label(self):
        return (self.cleaned_data.get("label") or "").strip()

    def clean_placeholder(self):
        return (self.cleaned_data.get("placeholder") or "").strip()

    def clean_help_text(self):
        return (self.cleaned_data.get("help_text") or "").strip()

    def clean_initial_option_label(self):
        return (self.cleaned_data.get("initial_option_label") or "").strip()

    def clean(self):
        cleaned = super().clean()
        selected_groups = list(cleaned.get("task_groups") or [])
        selected_types = list(cleaned.get("task_types") or [])
        if cleaned.get("field_type") == PortalDemandCustomField.FIELD_SELECT and not cleaned.get("initial_option_label"):
            self.add_error("initial_option_label", "Informe ao menos a primeira opção para campos do tipo lista.")
        if selected_groups and selected_types:
            group_ids = {group.id for group in selected_groups}
            invalid_types = [task_type.name for task_type in selected_types if task_type.group_id not in group_ids]
            if invalid_types:
                self.add_error("task_types", "Existem tipos fora dos grupos selecionados: " + ", ".join(invalid_types) + ".")
        return cleaned


class PortalDemandCustomFieldOptionForm(forms.Form):
    label = forms.CharField(max_length=80, label="Nova opção")

    def clean_label(self):
        return (self.cleaned_data.get("label") or "").strip()


class PortalDemandSlaPolicyForm(forms.ModelForm):
    PRIORITY_CHOICES = [("", "Todas as prioridades")] + [
        (value, label) for value, label, _color in userQueue.default_field_options(userQueue.FIELD_PRIORITY)
    ]

    priority_level = forms.ChoiceField(
        choices=PRIORITY_CHOICES,
        required=False,
        label="Prioridade",
        widget=forms.Select(attrs={"class": "queue-select"}),
    )

    class Meta:
        model = PortalDemandSlaPolicy
        fields = [
            "name",
            "description",
            "task_group",
            "task_type",
            "priority_level",
            "first_response_minutes",
            "resolution_minutes",
            "default_attendant",
            "auto_assign_on_create",
            "is_active",
        ]
        labels = {
            "name": "Nome da política",
            "description": "Descrição",
            "task_group": "Grupo",
            "task_type": "Tipo",
            "first_response_minutes": "Primeira resposta (min)",
            "resolution_minutes": "Resolução (min)",
            "default_attendant": "Atendente padrão",
            "auto_assign_on_create": "Assumir automaticamente na abertura",
            "is_active": "Política ativa",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ex.: Infra alta prioridade"}),
            "description": forms.TextInput(attrs={"placeholder": "Regra usada para medir e direcionar a demanda."}),
            "task_group": forms.Select(attrs={"class": "queue-select"}),
            "task_type": TaskTypeSelect(attrs={"class": "queue-select"}),
            "first_response_minutes": forms.NumberInput(attrs={"min": 1, "step": 1}),
            "resolution_minutes": forms.NumberInput(attrs={"min": 1, "step": 1}),
            "default_attendant": forms.Select(attrs={"class": "queue-select"}),
            "auto_assign_on_create": forms.CheckboxInput(attrs={"class": "modern-checkbox-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "modern-checkbox-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task_group"].queryset = TaskGroup.objects.order_by("name")
        self.fields["task_type"].queryset = TaskType.objects.select_related("group").order_by("group__name", "name")
        self.fields["default_attendant"].queryset = (
            User.objects.filter(is_active=True)
            .filter(Q(is_system_admin=True) | Q(is_superuser=True))
            .order_by("nameUser", "username", "id")
        )

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

    def clean(self):
        cleaned = super().clean()
        task_group = cleaned.get("task_group")
        task_type = cleaned.get("task_type")
        if task_type and task_group and task_type.group_id != task_group.id:
            self.add_error("task_type", "O tipo selecionado não pertence ao grupo informado.")
        elif task_type and not task_group:
            cleaned["task_group"] = task_type.group
        return cleaned


class PortalCannedResponseForm(forms.ModelForm):
    class Meta:
        model = PortalCannedResponse
        fields = ["title", "message", "task_group", "task_type", "suggest_internal", "is_active"]
        labels = {
            "title": "Título",
            "message": "Mensagem",
            "task_group": "Grupo",
            "task_type": "Tipo",
            "suggest_internal": "Sugerir como nota interna",
            "is_active": "Resposta ativa",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Ex.: Solicitação de evidências"}),
            "message": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Escreva a resposta pronta que poderá ser aplicada no atendimento.",
                }
            ),
            "task_group": forms.Select(attrs={"class": "queue-select"}),
            "task_type": TaskTypeSelect(attrs={"class": "queue-select"}),
            "suggest_internal": forms.CheckboxInput(attrs={"class": "modern-checkbox-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "modern-checkbox-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task_group"].queryset = TaskGroup.objects.order_by("name")
        self.fields["task_type"].queryset = TaskType.objects.select_related("group").order_by("group__name", "name")

    def clean_title(self):
        return (self.cleaned_data.get("title") or "").strip()

    def clean_message(self):
        return (self.cleaned_data.get("message") or "").strip()

    def clean(self):
        cleaned = super().clean()
        task_group = cleaned.get("task_group")
        task_type = cleaned.get("task_type")
        if task_type and task_group and task_type.group_id != task_group.id:
            self.add_error("task_type", "O tipo selecionado não pertence ao grupo informado.")
        elif task_type and not task_group:
            cleaned["task_group"] = task_type.group
        return cleaned


__all__ = [
    "PortalDemandForm",
    "PortalDemandReplyForm",
    "PortalRequesterSectorForm",
    "PortalRequesterCollaboratorForm",
    "PortalRequesterAccountForm",
    "PortalDemandTransferForm",
    "PortalDemandFeedbackForm",
    "PortalDemandCustomFieldCreateForm",
    "PortalDemandCustomFieldOptionForm",
    "PortalDemandSlaPolicyForm",
    "PortalCannedResponseForm",
]
